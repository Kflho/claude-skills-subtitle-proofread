#!/usr/bin/env python3
"""Whisper 精准转录 (Tier 1) — 集群乱码 cue，从好字幕边界切音频，喂给 whisper.cpp。"""

import sys, os, re, subprocess, json, argparse, tempfile, time

_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

from whisper_utils import (
    setup_windows_utf8, extract_ep_number, to_seconds,
    parse_srt, run_whisper, extract_audio_wav,
)
setup_windows_utf8()

GAP_SEC = 5.0
MAX_CLUSTER_GAP = 60.0

_CPU_COUNT = os.cpu_count() or 4
_TARGET = max(1, int(_CPU_COUNT * 0.8))
DEFAULT_PROCESSORS = 2
DEFAULT_THREADS    = max(1, _TARGET // DEFAULT_PROCESSORS)
DEFAULT_BEAM       = 5
DEFAULT_BEST_OF    = 8

_timers = {}

def tick(label):
    _timers[label] = time.time()

def tock(label):
    return time.time() - _timers.get(label, time.time())


# ═══════════════════════════════════════════════════════════════════
# 上下文集群（unique to Tier 1）
# ═══════════════════════════════════════════════════════════════════

def build_clusters(cues, max_gap=MAX_CLUSTER_GAP):
    """1) 按 max_gap 秒聚类乱码 cue；2) 扩展到前后好字幕边界。"""
    garbled_cues = [c for c in cues if c.get('is_garbled')]
    if not garbled_cues:
        return []

    groups, current = [], [garbled_cues[0]]
    for g in garbled_cues[1:]:
        if g['start_s'] - current[-1]['start_s'] <= max_gap:
            current.append(g)
        else:
            groups.append(current); current = [g]
    groups.append(current)

    clusters = []
    for g_group in groups:
        first, last = g_group[0], g_group[-1]
        left_idx = next((k for k, c in enumerate(cues) if c['start'] == first['start']), 0)
        right_idx = next((k for k, c in enumerate(cues) if c['start'] == last['start']), 0)

        left = next((cues[k] for k in range(left_idx - 1, -1, -1)
                     if not cues[k].get('is_garbled') and cues[k]['text']), None)
        right = next((cues[k] for k in range(right_idx + 1, len(cues))
                      if not cues[k].get('is_garbled') and cues[k]['text']), None)

        ss = left['end_s'] if left else max(0, first['start_s'] - 5)
        es = right['start_s'] if right else last['end_s'] + 5
        ss, es = min(ss, first['start_s']), max(es, last['end_s'])

        clusters.append({
            'ss': ss, 'es': es, 'dur': es - ss,
            'garbled': g_group,
            'left_text': left['text'][:60] if left else '(无)',
            'right_text': right['text'][:60] if right else '(无)',
        })
    return clusters


# ═══════════════════════════════════════════════════════════════════
# 匹配（unique to Tier 1）
# ═══════════════════════════════════════════════════════════════════

def match_whisper_to_cues(whisper_segs, cluster, offset):
    """whisper segment → 乱码 cue。区间重叠检测 + 二次宽窗口匹配。"""
    fixes, covered = [], set()
    cl = cluster['garbled']
    ss = cluster['ss']

    # 首轮：区间重叠
    for wh in whisper_segs:
        wh_abs_s = ss + (wh['start_s'] - offset)
        wh_abs_e = ss + (wh.get('end_s', wh['start_s'] + 8) - offset)
        if wh_abs_e <= wh_abs_s:
            wh_abs_e = wh_abs_s + 8
        matched = []
        for g in cl:
            if g['start_s'] <= wh_abs_e + 1 and wh_abs_s - 1 <= g['end_s']:
                matched.append(g)
                covered.add(g['start'])
        if matched:
            fixes.append({
                'start': matched[0]['start'], 'end': matched[-1]['end'],
                'original': ' | '.join(m['text'][:30] for m in matched),
                'replacement': wh['text'],
                'confidence': 'high', 'model': 'primary',
                'covered_count': len(matched),
                'covered_starts': [m['start'] for m in matched],
                'lines': [m.get('line') for m in matched if m.get('line')],
            })

    # 二次：±3s 宽窗口
    for wh in whisper_segs:
        wh_abs_s = ss + (wh['start_s'] - offset) - 3
        wh_abs_e = ss + (wh.get('end_s', wh['start_s'] + 8) - offset) + 3
        if wh_abs_e <= wh_abs_s:
            wh_abs_e = wh_abs_s + 14
        for g in cl:
            if g['start'] in covered:
                continue
            if g['start_s'] <= wh_abs_e + 1 and wh_abs_s - 1 <= g['end_s']:
                fixes.append({
                    'start': g['start'], 'end': g['end'],
                    'original': g['text'], 'replacement': wh['text'],
                    'confidence': 'retry', 'model': 'primary',
                    'covered_count': 1, 'covered_starts': [g['start']],
                    'lines': [g.get('line')] if g.get('line') else [],
                })
                covered.add(g['start'])

    # 未覆盖
    for g in cl:
        if g['start'] not in covered:
            fixes.append({
                'start': g['start'], 'end': g['end'],
                'original': g['text'], 'replacement': None,
                'confidence': 'none', 'model': 'primary',
                'covered_count': 0, 'covered_starts': [],
                'lines': [g.get('line')] if g.get('line') else [],
            })

    return fixes, covered


# ═══════════════════════════════════════════════════════════════════
# 报告同步
# ═══════════════════════════════════════════════════════════════════

def update_unified_report(reports_dir, srt_path, fixes, clusters, all_covered, merged_unmatched=None):
    """更新统一报告步骤15（Whisper修复）+ 步骤16（人工审查），标记旧报告废弃。"""
    import datetime
    from update_report import upsert_entries

    os.makedirs(reports_dir, exist_ok=True)
    report_path = os.path.join(reports_dir, '问题解决报告.md')
    ep = extract_ep_number(srt_path)
    fixed_items = [f for f in fixes if f['confidence'] in ('high', 'retry')]

    # 步骤15: Whisper 已修复
    step15 = [{
        'ep': ep, 'time': f['start'],
        'original': f['original'].replace('\n', ' ')[:80],
        'corrected': f['replacement'].replace('\n', ' ')[:80] if f.get('replacement') else '',
        'status': '✅',
    } for f in fixed_items]
    if step15:
        upsert_entries(report_path, step=15, entries=step15)

    # 步骤16: 待人工审查
    step16 = [{
        'ep': ep, 'time': mu['start'],
        'original': ' / '.join(mu['texts'])[:120],
        'corrected': '', 'status': '⬜',
    } for mu in (merged_unmatched or [])]
    if step16:
        upsert_entries(report_path, step=16, entries=step16)

    # 标记旧报告废弃
    old = os.path.join(reports_dir, 'whisper-pending.md')
    if os.path.exists(old):
        with open(old, 'r', encoding='utf-8') as f:
            content = f.read()
        if not content.startswith('> ⚠️ 已废弃'):
            with open(old, 'w', encoding='utf-8') as f:
                f.write('> ⚠️ 已废弃 — 请查看 [问题解决报告.md](./问题解决报告.md)\n\n' + content)


# ═══════════════════════════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description='Whisper 精准转录')
    parser.add_argument('video'); parser.add_argument('srt')
    parser.add_argument('--whisper-cli', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--language', '-l', default='ja')
    parser.add_argument('--threads', '-t', type=int, default=DEFAULT_THREADS)
    parser.add_argument('--processors', '-p', type=int, default=DEFAULT_PROCESSORS)
    parser.add_argument('--beam-size', '-bs', type=int, default=DEFAULT_BEAM)
    parser.add_argument('--best-of', '-bo', type=int, default=DEFAULT_BEST_OF)
    parser.add_argument('--no-gpu', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--retry-model')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--update-report', metavar='REPORTS_DIR')
    parser.add_argument('--cluster-gap', type=float, default=MAX_CLUSTER_GAP,
                        help=f'乱码 cue 聚类最大间隔秒数 (default: {MAX_CLUSTER_GAP})')
    parser.add_argument('--gap-sec', type=float, default=GAP_SEC,
                        help=f'聚类片段间的静音间隔秒数 (default: {GAP_SEC})')
    parser.add_argument('--op-boundary', type=float, default=95,
                        help='OP 豁免边界 — 开头 N 秒不标记乱码 (default: 95)')
    parser.add_argument('--ed-boundary', type=float, default=120,
                        help='ED 豁免边界 — 结尾 N 秒不标记乱码 (default: 120)')
    parser.add_argument('--separate-vocals', action='store_true',
                        help='用 demucs 分离人声后再转录（去 BGM，减少幻觉）')
    args = parser.parse_args()

    # 扫描
    tick('scan')
    cues = parse_srt(args.srt, op_boundary=args.op_boundary, ed_boundary=args.ed_boundary)
    clusters = build_clusters(cues, max_gap=args.cluster_gap)
    total_garbled = sum(len(c['garbled']) for c in clusters)
    if not args.json:
        print(f'[1/5] 扫描: {total_garbled}处乱码 → {len(clusters)}群 ({tock("scan"):.1f}s)')

    if args.dry_run:
        if args.json:
            items = [{'cluster': i+1, 'start': s['start'], 'end': s['end'], 'text': s['text']}
                     for i, cl in enumerate(clusters) for s in cl['garbled']]
            print(json.dumps({'episode': os.path.basename(args.srt), 'segments': total_garbled,
                              'clusters': len(clusters), 'items': items}, ensure_ascii=False, indent=2))
        else:
            for i, cl in enumerate(clusters):
                print(f'\n  群{i+1}: {cl["garbled"][0]["start"]} ~ {cl["garbled"][-1]["start"]} '
                      f'({len(cl["garbled"])}处, {cl["dur"]:.0f}s)')
                print(f'    ← {cl["left_text"]}')
                for s in cl['garbled'][:3]:
                    print(f'    [{s["start"]}] {s["text"][:80]}')
                if len(cl['garbled']) > 3: print(f'    ... 还有 {len(cl["garbled"])-3} 处')
                print(f'    → {cl["right_text"]}')
        return

    if not clusters:
        if args.json:
            print(json.dumps({'status': 'ok', 'fixed': 0, 'unmatched': 0}))
        else:
            print('无需修复！')
        return

    # 提取全片音频
    tick('extract')
    tmpdir = tempfile.mkdtemp()
    full_audio = os.path.join(tmpdir, 'full.wav')
    extract_audio_wav(args.video, full_audio)
    if not args.json:
        print(f'[2/5] 提取全片音频: {tock("extract"):.1f}s')

    # 可选：人声分离
    if getattr(args, 'separate_vocals', False):
        from whisper_utils import separate_vocals
        full_audio = separate_vocals(full_audio, output_dir=tmpdir)

    # 切片段 + 拼接
    tick('merge')
    clips, total_dur = [], 0
    silence = os.path.join(tmpdir, 'silence.wav')
    subprocess.run(['ffmpeg', '-y', '-f', 'lavfi', '-i', f'anullsrc=r=16000:d={args.gap_sec}',
                    '-c:a', 'pcm_s16le', silence], capture_output=True, check=True)

    for i, cl in enumerate(clusters):
        ss, es, dur = cl['ss'], cl['es'], cl['es'] - cl['ss']
        cp = os.path.join(tmpdir, f'c{i:03d}.wav')
        subprocess.run(['ffmpeg', '-y', '-ss', str(ss), '-t', str(dur),
                        '-i', full_audio, '-c', 'copy', cp], capture_output=True, check=True)
        clips.append((i, ss, es, dur, cp))
        total_dur += dur + args.gap_sec

    concat_txt = os.path.join(tmpdir, 'concat.txt')
    with open(concat_txt, 'w') as f:
        for _, _, _, _, cp in clips:
            f.write(f"file '{cp}'\nfile '{silence}'\n")

    merged = os.path.join(tmpdir, 'merged.wav')
    subprocess.run(['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', concat_txt,
                    '-c', 'copy', merged], capture_output=True, check=True)
    if not args.json:
        print(f'[3/5] 拼接 {len(clips)}段: {tock("merge"):.1f}s (共{total_dur:.0f}s音频)')

    # 首轮转录
    tick('whisper')
    if not args.json:
        print(f'[4+5/5] 转录 {total_dur:.0f}s (t={args.threads} p={args.processors} bs={args.beam_size})')
    all_whisper = run_whisper(merged, args.whisper_cli, args.model, args.language,
                              args.threads, args.processors, args.beam_size, args.best_of)

    all_fixes, all_covered = [], set()
    offset = 0
    for i, (ci, ss, es, dur, _) in enumerate(clips):
        wh_segs = [w for w in all_whisper if offset <= w['start_s'] < offset + dur]
        fixes, covered = match_whisper_to_cues(wh_segs, clusters[ci], offset)
        all_fixes.extend(fixes)
        all_covered |= covered
        offset += dur + args.gap_sec

    if not args.json:
        print(f'[4+5/5] 首轮完成: {len(all_whisper)}段, {tock("whisper"):.1f}s')

    # 重试
    unmatched = [f for f in all_fixes if f['confidence'] == 'none']
    if args.retry_model and unmatched:
        tick('retry')
        if not args.json:
            print(f'[retry] {len(unmatched)}段未匹配 → 备选模型')

        retry_clips = []
        for i, f in enumerate(unmatched):
            seg_s = to_seconds(f['start'])
            es2 = to_seconds(f['end']) + 2
            ss2 = max(0, seg_s - 2); dur2 = es2 - ss2
            cp2 = os.path.join(tmpdir, f'retry_{i:03d}.wav')
            subprocess.run(['ffmpeg', '-y', '-ss', str(ss2), '-t', str(dur2),
                            '-i', full_audio, '-c', 'copy', cp2], capture_output=True, check=True)
            retry_clips.append((i, ss2, es2, dur2, cp2, f))

        retry_merged = os.path.join(tmpdir, 'retry_merged.wav')
        with open(os.path.join(tmpdir, 'retry_concat.txt'), 'w') as fh:
            for _, _, _, _, cp, _ in retry_clips:
                fh.write(f"file '{cp}'\nfile '{silence}'\n")
        subprocess.run(['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i',
                        os.path.join(tmpdir, 'retry_concat.txt'), '-c', 'copy', retry_merged],
                       capture_output=True, check=True)

        retry_whisper = run_whisper(retry_merged, args.whisper_cli, args.retry_model,
                                    args.language, args.threads, 1, args.beam_size, args.best_of)

        retry_offset = 0; retry_fixed = 0
        for i, ss2, es2, dur2, _, f in retry_clips:
            results = [w for w in retry_whisper if retry_offset <= w['start_s'] < retry_offset + dur2]
            best = min(results, key=lambda r: abs(r['start_s'] - retry_offset - 1), default=None)
            if best and abs(best['start_s'] - retry_offset - 1) < 8:
                for fix in all_fixes:
                    if fix['start'] == f['start'] and fix['confidence'] == 'none':
                        fix['replacement'] = best['text']
                        fix['confidence'] = 'retry'; fix['model'] = 'retry'
                        fix['covered_count'] = 1
                        all_covered.add(f['start'])
                        retry_fixed += 1; break
            retry_offset += dur2 + args.gap_sec
        if not args.json:
            print(f'[retry] 修复 {retry_fixed}/{len(unmatched)} ({tock("retry"):.1f}s)')

    # 幻觉检测
    from collections import Counter
    repl_counts = Counter(f['replacement'] for f in all_fixes
                          if f['confidence'] != 'none' and f['replacement'])
    hallucinations = {k for k, v in repl_counts.items() if v >= 3}
    if hallucinations and not args.json:
        print(f'[post] 检测到 {len(hallucinations)} 组幻觉，降级为未匹配', file=sys.stderr)
    for f in all_fixes:
        if f['confidence'] != 'none' and f['replacement'] in hallucinations:
            f['confidence'] = 'none'; f['model'] = 'hallucination'
            all_covered.discard(f['start'])

    # 汇总未匹配集群
    merged_unmatched = []
    for i, cl in enumerate(clusters):
        unmatched_in = [g for g in cl['garbled'] if g['start'] not in all_covered]
        if unmatched_in:
            merged_unmatched.append({
                'cluster': i, 'start': unmatched_in[0]['start'], 'end': unmatched_in[-1]['end'],
                'count': len(unmatched_in),
                'texts': [g['text'][:40] for g in unmatched_in], 'clip_idx': i,
            })

    total_groups = len(clusters)
    unmatched_groups = len({mu['cluster'] for mu in merged_unmatched})
    fixed_groups = total_groups - unmatched_groups

    if args.json:
        print(json.dumps({
            'status': 'ok', 'total_groups': total_groups,
            'fixed_groups': fixed_groups, 'unmatched_groups': unmatched_groups,
            'fixes': all_fixes,
            'merged_unmatched': [{'start': mu['start'], 'end': mu['end'],
                                  'count': mu['count'], 'texts': mu['texts']} for mu in merged_unmatched],
        }, ensure_ascii=False, indent=2))
    else:
        print(f'\n{"="*60}')
        print(f'结果: {fixed_groups}群修复 / {unmatched_groups}群未匹配')
        print(f'{"="*60}')

    # 报告 + 视频切片
    if args.update_report and args.json:
        update_unified_report(args.update_report, args.srt, all_fixes, clusters, all_covered, merged_unmatched)

        if merged_unmatched:
            review_dir = os.path.join(args.update_report, 'manual-review')
            os.makedirs(review_dir, exist_ok=True)
            ep = extract_ep_number(args.srt)

            for old in os.listdir(review_dir):
                if old.startswith(ep + '_'):
                    os.remove(os.path.join(review_dir, old))

            checklist_path = os.path.join(review_dir, 'review-checklist.md')
            existing = ''
            if os.path.exists(checklist_path):
                with open(checklist_path, 'r', encoding='utf-8') as cf:
                    existing = cf.read()
            existing = re.sub(rf'\n?---\n{ep} \|.*?\n修正:\n\n', '', existing, flags=re.DOTALL).rstrip()

            new_block = ''
            for mu in merged_unmatched:
                safe_start = mu['start'].replace(',', '-').replace('.', '-').replace(':', '-')
                safe_end = mu['end'].replace(',', '-').replace('.', '-').replace(':', '-')
                clip_name = f'{ep}_{safe_start}_to_{safe_end}.mp4'
                dur = clusters[mu['cluster']]['es'] - clusters[mu['cluster']]['ss']
                subprocess.run(['ffmpeg', '-y', '-ss', str(clusters[mu['cluster']]['ss']),
                                '-t', str(dur), '-i', args.video, '-c', 'copy', os.path.join(review_dir, clip_name)],
                               capture_output=True, check=True)
                texts = ' / '.join(mu['texts'])
                new_block += f'---\n{ep} | {mu["start"]} ~ {mu["end"]} | {clip_name}\n残留: {texts}\n修正:\n\n'

            ep_num = int(ep.replace('EP', ''))
            if not existing or not existing.startswith('#'):
                import datetime as _dt
                existing = f'# 人工审查清单\n> 导出: {_dt.date.today().isoformat()}\n>\n> **填写方法**：「修正:」下一行开始，每行写一句台词，直接换行\n>\n'

            all_blocks = []
            for m in re.finditer(r'\n?---\n(EP\d+) \|.*?\n修正:\n\n', existing, re.DOTALL):
                all_blocks.append((int(re.match(r'EP(\d+)', m.group(1)).group(1)), m.group(0)))
            existing = re.sub(r'(\n?---\nEP\d+ \|.*?\n修正:\n\n)', '', existing, flags=re.DOTALL)
            all_blocks.append((ep_num, new_block))
            all_blocks.sort(key=lambda x: x[0])

            with open(checklist_path, 'w', encoding='utf-8') as cf:
                cf.write(existing.rstrip() + '\n')
                total_items = 0
                for _, block in all_blocks:
                    cf.write(block)
                    total_items += block.count('修正:')
                cf.write('\n---\n')
                cf.write(f'> 共 {total_items} 条。在「修正:」下方每行写一句台词（可多行），脚本用 whisper VAD 自动分配时间轴。\n')
                cf.write(f'> 填写完毕后: python scripts/apply_review_fixes.py review-checklist.md --srt-dir <字幕目录>\n')
            print(f'[report] manual-review/ ← {len(merged_unmatched)} 个视频切片 + review-checklist.md(累计{total_items}条)', file=sys.stderr)

        # 删除候选
        delete_candidates = []
        for f in all_fixes:
            if f['confidence'] != 'none': continue
            text = f['original']
            if not re.search(r'[぀-ヿ一-鿿]', text) and len(text.split()) <= 2:
                delete_candidates.append(f)
        if delete_candidates:
            delete_path = os.path.join(args.update_report, 'whisper_delete_candidates.json')
            delete_fixes = [{
                'action': 'delete_line', 'file': os.path.basename(args.srt),
                'line': f['lines'][0], 'note': f'噪声误识别: {f["original"][:30]}',
            } for f in delete_candidates if f.get('lines')]
            if delete_fixes:
                with open(delete_path, 'w', encoding='utf-8') as fh:
                    json.dump(delete_fixes, fh, ensure_ascii=False, indent=2)
                print(f'[report] {delete_path} ← {len(delete_fixes)}条删除候选', file=sys.stderr)

    import shutil; shutil.rmtree(tmpdir)
    if not args.json:
        print('\n完成！')


if __name__ == '__main__':
    main()
