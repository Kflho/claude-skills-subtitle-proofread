#!/usr/bin/env python3
"""Whisper 精准转录 — 从前一个好字幕结束到后一个好字幕开始，整段喂给 whisper.cpp。"""

import sys, os, re, subprocess, json, argparse, tempfile, io, time

# 脚本目录加入 path，以便导入同目录工具模块
_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

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
# SRT 解析 + 上下文集群
# ═══════════════════════════════════════════════════════════════════

def parse_all_cues(srt_path):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, script_dir)
    from srt_utils import read_srt_file, parse_srt_cue

    lines = read_srt_file(srt_path)
    cues, idx = [], 0
    while idx < len(lines):
        cue, idx = parse_srt_cue(lines, idx)
        if cue is None: idx += 1; continue
        c = {'start': cue['start'], 'end': cue['end'],
             'text': cue['text'].strip(), 'line': cue.get('_start_line')}
        for key in ('start', 'end'):
            p = c[key].replace(',', '.').split(':')
            c[key + '_s'] = int(p[0])*3600 + int(p[1])*60 + float(p[2])
        t = c['text']
        is_g = bool(t and not t.startswith('[') and re.search(r'[a-zA-Z]{2,}', t))
        c['is_garbled'] = is_g
        cues.append(c)

    # OP/ED 过滤（在所有 cue 解析完后做，才能拿到真实的 max_end_s）
    max_end_s = max((c['end_s'] for c in cues), default=1500)
    for c in cues:
        if c['is_garbled'] and (c['start_s'] < 95 or c['start_s'] > max_end_s - 120):
            c['is_garbled'] = False

    return cues


def build_clusters(cues):
    """1) 按 MAX_CLUSTER_GAP 聚类乱码 cue；2) 扩展到前后好字幕边界。"""
    garbled_cues = [c for c in cues if c['is_garbled']]
    if not garbled_cues: return []

    # 步骤 1: 聚类相邻乱码
    groups, current = [], [garbled_cues[0]]
    for g in garbled_cues[1:]:
        if g['start_s'] - current[-1]['start_s'] <= MAX_CLUSTER_GAP:
            current.append(g)
        else:
            groups.append(current); current = [g]
    groups.append(current)

    # 步骤 2: 扩展到前后好字幕边界
    clusters = []
    for g_group in groups:
        first, last = g_group[0], g_group[-1]
        # 找到前一个好 cue 的 end
        left, left_idx = None, 0
        for k, c in enumerate(cues):
            if c['start'] == first['start']: left_idx = k; break
        for k in range(left_idx - 1, -1, -1):
            if not cues[k]['is_garbled'] and cues[k]['text']:
                left = cues[k]; break
        ss = left['end_s'] if left else max(0, first['start_s'] - 5)
        # 找到后一个好 cue 的 start
        right, right_idx = None, 0
        for k, c in enumerate(cues):
            if c['start'] == last['start']: right_idx = k; break
        for k in range(right_idx + 1, len(cues)):
            if not cues[k]['is_garbled'] and cues[k]['text']:
                right = cues[k]; break
        es = right['start_s'] if right else last['end_s'] + 5
        ss = min(ss, first['start_s'])
        es = max(es, last['end_s'])

        clusters.append({
            'ss': ss, 'es': es, 'dur': es - ss,
            'garbled': g_group,
            'left_text': left['text'][:60] if left else '(无)',
            'right_text': right['text'][:60] if right else '(无)'
        })
    return clusters


# ═══════════════════════════════════════════════════════════════════
# whisper.cpp CLI
# ═══════════════════════════════════════════════════════════════════

def run_whisper(audio_path, whisper_cli, model_path, threads, processors,
                beam_size, best_of, language='ja', no_gpu=False):
    cmd = [whisper_cli, '-m', model_path, '-f', audio_path, '-l', language,
           '-t', str(threads), '-p', str(processors),
           '-bs', str(beam_size), '-bo', str(best_of),
           '-oj', '-of', audio_path + '.whisper', '--print-progress',
           '-nth', '0.6',        # 无声阈值（默认0.6），过低会引入噪声，过高跳过弱语音
           '-mc', '0',           # 禁用跨片段上下文，避免拼接音频中的语义污染
           '-sns']               # 抑制非语音 token
    if no_gpu: cmd.append('--no-gpu')

    proc = subprocess.run(cmd, capture_output=True, text=True)
    for line in (proc.stderr or '').strip().split('\n'):
        if line.strip():
            print(f'  [whisper] {line.strip()}', file=sys.stderr)

    json_path = audio_path + '.whisper.json'
    if not os.path.exists(json_path):
        print(f'⚠ whisper-cli 未生成 JSON: {json_path}', file=sys.stderr)
        return []

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError):
        print(f'⚠ whisper JSON 解码失败（音频质量过差）', file=sys.stderr)
        if os.path.exists(json_path):
            os.remove(json_path)
        return []
    os.remove(json_path)

    segs = []
    for seg in data.get('transcription', []):
        ts_from = seg.get('timestamps', {}).get('from', '00:00:00,000').replace(',', '.')
        ts_to = seg.get('timestamps', {}).get('to', '00:00:08,000').replace(',', '.')
        parts_f = ts_from.split(':')
        parts_t = ts_to.split(':')
        t_from = int(parts_f[0])*3600 + int(parts_f[1])*60 + float(parts_f[2])
        t_to = int(parts_t[0])*3600 + int(parts_t[1])*60 + float(parts_t[2])
        text = seg.get('text', '').strip()
        if text:
            segs.append({'merged_s': t_from, 'merged_e': t_to, 'text': text})
    return segs


# ═══════════════════════════════════════════════════════════════════
# 匹配：whisper 输出 → 覆盖的原始乱码 cue
# ═══════════════════════════════════════════════════════════════════

def match_whisper_to_cues(whisper_segs, cluster, offset):
    """whisper segment → 覆盖的原始乱码 cue 列表。
    使用 whisper 实际时间戳做区间重叠检测，未匹配的 cue 做二次宽窗口匹配。
    """
    fixes, covered = [], set()
    cl = cluster['garbled']
    ss = cluster['ss']

    # ── 首轮匹配：区间重叠检测 ──
    for wh in whisper_segs:
        wh_abs_s = ss + (wh['merged_s'] - offset)
        wh_abs_e = ss + (wh.get('merged_e', wh['merged_s'] + 8) - offset)
        # 确保有效区间
        if wh_abs_e <= wh_abs_s:
            wh_abs_e = wh_abs_s + 8
        matched = []
        for g in cl:
            # 区间重叠检测：g 与 wh 有时间重叠即匹配
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
                'lines': [m.get('line') for m in matched if m.get('line')]
            })

    # ── 二次匹配：宽窗口（±3s 扩展）──
    for wh in whisper_segs:
        wh_abs_s = ss + (wh['merged_s'] - offset) - 3  # 前扩展 3s
        wh_abs_e = ss + (wh.get('merged_e', wh['merged_s'] + 8) - offset) + 3  # 后扩展 3s
        if wh_abs_e <= wh_abs_s:
            wh_abs_e = wh_abs_s + 14
        for g in cl:
            if g['start'] in covered:
                continue
            if g['start_s'] <= wh_abs_e + 1 and wh_abs_s - 1 <= g['end_s']:
                # 二次匹配降级为 retry 置信度
                fixes.append({
                    'start': g['start'], 'end': g['end'],
                    'original': g['text'],
                    'replacement': wh['text'],
                    'confidence': 'retry', 'model': 'primary',
                    'covered_count': 1,
                    'covered_starts': [g['start']],
                    'lines': [g.get('line')] if g.get('line') else []
                })
                covered.add(g['start'])

    # 未覆盖的
    for g in cl:
        if g['start'] not in covered:
            fixes.append({
                'start': g['start'], 'end': g['end'],
                'original': g['text'], 'replacement': None,
                'confidence': 'none', 'model': 'primary',
                'covered_count': 0, 'covered_starts': [],
                'lines': [g.get('line')] if g.get('line') else []
            })

    return fixes, covered


# ═══════════════════════════════════════════════════════════════════
# 报告同步
# ═══════════════════════════════════════════════════════════════════

def update_unified_report(reports_dir, srt_path, fixes, clusters, all_covered, merged_unmatched=None):
    """更新统一问题解决报告：步骤15（Whisper修复）+ 步骤16（人工审查）。
    同时标记旧的 whisper-pending.md 为废弃。
    """
    import datetime
    from update_report import upsert_entries

    os.makedirs(reports_dir, exist_ok=True)
    report_path = os.path.join(reports_dir, '问题解决报告.md')
    ep = os.path.basename(srt_path).split('-')[1].strip().split()[0] if '-' in os.path.basename(srt_path) else '???'
    ep_tag = f'EP{ep}'
    today = datetime.date.today().isoformat()

    total_groups = len(clusters)
    unmatched_groups = len(merged_unmatched) if merged_unmatched else 0
    fixed_items = [f for f in fixes if f['confidence'] in ('high', 'retry')]

    # ── 步骤15: Whisper 自动修复（仅已修复条目）──
    step15_entries = []
    for f in fixed_items:
        step15_entries.append({
            'ep': ep_tag,
            'time': f['start'],
            'original': f['original'].replace('\n', ' ')[:80],
            'corrected': f['replacement'].replace('\n', ' ')[:80] if f.get('replacement') else '',
            'status': '✅',
        })

    if step15_entries:
        upsert_entries(report_path, step=15, entries=step15_entries)

    # ── 步骤16: 人工审查修正（Whisper 未能自动修复的条目）──
    step16_entries = []
    if merged_unmatched:
        for mu in merged_unmatched:
            texts = ' / '.join(mu['texts'])
            step16_entries.append({
                'ep': ep_tag,
                'time': mu['start'],
                'original': texts[:120],
                'corrected': '',
                'status': '⬜',
            })

    if step16_entries:
        upsert_entries(report_path, step=16, entries=step16_entries)

    # ── 标记旧报告为废弃 ──
    old_report = os.path.join(reports_dir, 'whisper-pending.md')
    if os.path.exists(old_report):
        with open(old_report, 'r', encoding='utf-8') as f:
            old_content = f.read()
        if not old_content.startswith('> ⚠️ 已废弃'):
            with open(old_report, 'w', encoding='utf-8') as f:
                f.write('> ⚠️ 已废弃 — 请查看 [问题解决报告.md](./问题解决报告.md)\n\n')
                f.write(old_content)


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
    args = parser.parse_args()

    model_path = args.model if os.path.isabs(args.model) or '/' in args.model else ''
    retry_model_path = ''
    if args.retry_model:
        retry_model_path = args.retry_model if os.path.isabs(args.retry_model) or '/' in args.retry_model else ''

    # ── 阶段 1: 扫描 ──
    tick('scan')
    cues = parse_all_cues(args.srt)
    clusters = build_clusters(cues)
    total_garbled = sum(len(c['garbled']) for c in clusters)
    if not args.json:
        print(f'[1/5] 扫描: {total_garbled}处乱码 → {len(clusters)}群 ({tock("scan"):.1f}s)')

    if args.dry_run:
        if args.json:
            items = []
            for i, cl in enumerate(clusters):
                for s in cl['garbled']:
                    items.append({'cluster': i+1, 'start': s['start'], 'end': s['end'], 'text': s['text']})
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

    # ── 阶段 2: 提取全片音频 ──
    tick('extract')
    tmpdir = tempfile.mkdtemp()
    full_audio = os.path.join(tmpdir, 'full.wav')
    subprocess.run(['ffmpeg', '-y', '-i', args.video, '-vn', '-ac', '1', '-ar', '16000',
                    '-c:a', 'pcm_s16le', full_audio], capture_output=True, check=True)
    if not args.json:
        print(f'[2/5] 提取全片音频: {tock("extract"):.1f}s')

    # ── 阶段 3: 按集群切片段（从好字幕边界到好字幕边界）──
    tick('merge')
    clips, total_dur = [], 0
    silence = os.path.join(tmpdir, 'silence.wav')
    subprocess.run(['ffmpeg', '-y', '-f', 'lavfi', '-i', f'anullsrc=r=16000:d={GAP_SEC}',
                    '-c:a', 'pcm_s16le', silence], capture_output=True, check=True)

    for i, cl in enumerate(clusters):
        ss, es = cl['ss'], cl['es']
        dur = es - ss
        cp = os.path.join(tmpdir, f'c{i:03d}.wav')
        subprocess.run(['ffmpeg', '-y', '-ss', str(ss), '-t', str(dur),
                        '-i', full_audio, '-c', 'copy', cp], capture_output=True, check=True)
        clips.append((i, ss, es, dur, cp))
        total_dur += dur + GAP_SEC

    concat_txt = os.path.join(tmpdir, 'concat.txt')
    with open(concat_txt, 'w') as f:
        for _, _, _, _, cp in clips:
            f.write(f"file '{cp}'\nfile '{silence}'\n")

    merged = os.path.join(tmpdir, 'merged.wav')
    subprocess.run(['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', concat_txt,
                    '-c', 'copy', merged], capture_output=True, check=True)
    if not args.json:
        print(f'[3/5] 拼接 {len(clips)}段: {tock("merge"):.1f}s (共{total_dur:.0f}s音频)')

    # ── 阶段 4+5: 首轮转录 ──
    tick('whisper')
    if not args.json:
        print(f'[4+5/5] 转录 {total_dur:.0f}s (t={args.threads} p={args.processors} bs={args.beam_size})')
    all_whisper = run_whisper(merged, args.whisper_cli, model_path,
                              args.threads, args.processors,
                              args.beam_size, args.best_of, args.language, args.no_gpu)

    all_fixes, all_covered = [], set()
    offset = 0
    for i, (ci, ss, es, dur, _) in enumerate(clips):
        wh_segs = [w for w in all_whisper if offset <= w['merged_s'] < offset + dur]
        fixes, covered = match_whisper_to_cues(wh_segs, clusters[ci], offset)
        all_fixes.extend(fixes)
        all_covered |= covered
        offset += dur + GAP_SEC

    elapsed = tock('whisper')
    if not args.json:
        print(f'[4+5/5] 首轮完成: {len(all_whisper)}段, {elapsed:.1f}s')

    # ── 重试 ──
    unmatched = [f for f in all_fixes if f['confidence'] == 'none']
    if args.retry_model and unmatched:
        tick('retry')
        if not args.json:
            print(f'[retry] {len(unmatched)}段未匹配 → 备选模型')

        retry_clips = []
        for i, f in enumerate(unmatched):
            seg_s = 0
            for key in ('start',):
                p = f[key].replace(',', '.').split(':')
                seg_s = int(p[0])*3600 + int(p[1])*60 + float(p[2])
            ss2 = max(0, seg_s - 2)
            p2 = f['end'].replace(',', '.').split(':')
            es2 = int(p2[0])*3600 + int(p2[1])*60 + float(p2[2]) + 2
            dur2 = es2 - ss2
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

        retry_whisper = run_whisper(retry_merged, args.whisper_cli, retry_model_path,
                                     args.threads, 1, args.beam_size, args.best_of,
                                     args.language, args.no_gpu)

        retry_offset = 0
        retry_fixed = 0
        for i, ss2, es2, dur2, _, f in retry_clips:
            results = [w for w in retry_whisper if retry_offset <= w['merged_s'] < retry_offset + dur2]
            best = min(results, key=lambda r: abs(r['merged_s'] - retry_offset - 1), default=None)
            if best and abs(best['merged_s'] - retry_offset - 1) < 8:
                for fix in all_fixes:
                    if fix['start'] == f['start'] and fix['confidence'] == 'none':
                        fix['replacement'] = best['text']
                        fix['confidence'] = 'retry'
                        fix['model'] = 'retry'
                        fix['covered_count'] = 1
                        all_covered.add(f['start'])
                        retry_fixed += 1
                        break
            retry_offset += dur2 + GAP_SEC

        if not args.json:
            print(f'[retry] 修复 {retry_fixed}/{len(unmatched)} ({tock("retry"):.1f}s)')

    # ── 后处理：检测幻觉 ──
    from collections import Counter
    repl_counts = Counter(
        f['replacement'] for f in all_fixes
        if f['confidence'] != 'none' and f['replacement']
    )
    hallucinations = {k for k, v in repl_counts.items() if v >= 3}
    if hallucinations and not args.json:
        print(f'[post] 检测到 {len(hallucinations)} 组幻觉，已降级为未匹配', file=sys.stderr)
    for f in all_fixes:
        if f['confidence'] != 'none' and f['replacement'] in hallucinations:
            f['confidence'] = 'none'
            f['model'] = 'hallucination'
            if f['start'] in all_covered:
                all_covered.discard(f['start'])

    # ── 后处理：一集群 = 一审查条目 ──
    merged_unmatched = []
    for i, cl in enumerate(clusters):
        unmatched_in_cl = [g for g in cl['garbled'] if g['start'] not in all_covered]
        if not unmatched_in_cl:
            continue
        texts = [g['text'][:40] for g in unmatched_in_cl]
        merged_unmatched.append({
            'cluster': i,
            'start': unmatched_in_cl[0]['start'],
            'end': unmatched_in_cl[-1]['end'],
            'count': len(unmatched_in_cl),
            'texts': texts,
            'clip_idx': i
        })

    # ── 输出（集群含任一未修复 cue 即为未修复群）──
    total_groups = len(clusters)
    # 找哪些集群有未匹配 cue
    clusters_with_unmatched = set()
    for mu in merged_unmatched:
        clusters_with_unmatched.add(mu['cluster'])
    unmatched_groups = len(clusters_with_unmatched)
    fixed_groups = total_groups - unmatched_groups

    if args.json:
        print(json.dumps({
            'status': 'ok',
            'total_groups': total_groups,
            'fixed_groups': fixed_groups,
            'unmatched_groups': unmatched_groups,
            'fixes': all_fixes,
            'merged_unmatched': [{
                'start': mu['start'], 'end': mu['end'],
                'count': mu['count'], 'texts': mu['texts']
            } for mu in merged_unmatched]
        }, ensure_ascii=False, indent=2))
    else:
        print(f'\n{"="*60}')
        print(f'结果: {fixed_count}修复 / {unmatched_count}未匹配')
        print(f'{"="*60}')
        offset = 0
        for i, (ci, ss, es, dur, _) in enumerate(clips):
            cl = clusters[ci]
            print(f'\n--- 群{i+1}: {cl["garbled"][0]["start"]} ~ {cl["garbled"][-1]["start"]} ---')
            print(f'    切: {ss:.0f}s ~ {es:.0f}s (← {cl["left_text"][:30]} ... {cl["right_text"][:30]} →)')
            for g in cl['garbled']:
                status = '✅' if g['start'] in all_covered else '❌'
                match = [f for f in all_fixes if f['start'] == g['start'] and f['confidence'] != 'none']
                repl = match[0]['replacement'][:60] if match else '(未匹配)'
                print(f'  {status} [{g["start"]}] {g["text"][:50]}')
                if match: print(f'      → {repl}')
            offset += dur + GAP_SEC

    # ── 报告 + 音频导出 ──
    if args.update_report and args.json:
        update_unified_report(args.update_report, args.srt, all_fixes, clusters, all_covered, merged_unmatched)

        # 导出未匹配段视频切片 + 审查清单
        if merged_unmatched:
            review_dir = os.path.join(args.update_report, 'manual-review')
            os.makedirs(review_dir, exist_ok=True)
            ep = os.path.basename(args.srt).split('-')[1].strip().split()[0] if '-' in os.path.basename(args.srt) else '???'
            # 清理该集旧切片
            for old in os.listdir(review_dir):
                if old.startswith(f'EP{ep}_'):
                    os.remove(os.path.join(review_dir, old))
            checklist_path = os.path.join(review_dir, 'review-checklist.md')
            exported = 0
            import datetime as _dt

            # 读取已有清单，按 EP 号替换/新增本集条目
            existing = ''
            if os.path.exists(checklist_path):
                with open(checklist_path, 'r', encoding='utf-8') as cf:
                    existing = cf.read()
            # 删除本集旧条目（含分隔符）
            existing = re.sub(rf'\n?---\nEP0?{ep} \|.*?\n修正:\n\n', '', existing, flags=re.DOTALL)
            existing = existing.rstrip()

            # 生成本集新条目
            new_block = ''
            for mu in merged_unmatched:
                safe_start = mu['start'].replace(',', '-').replace('.', '-').replace(':', '-')
                safe_end = mu['end'].replace(',', '-').replace('.', '-').replace(':', '-')
                clip_name = f'EP{ep}_{safe_start}_to_{safe_end}.mp4'
                dst = os.path.join(review_dir, clip_name)
                dur = clusters[mu['cluster']]['es'] - clusters[mu['cluster']]['ss']
                subprocess.run(['ffmpeg', '-y', '-ss', str(clusters[mu['cluster']]['ss']),
                                '-t', str(dur), '-i', args.video, '-c', 'copy', dst],
                               capture_output=True, check=True)
                exported += 1
                texts = ' / '.join(mu['texts'])
                new_block += f'---\nEP{ep} | {mu["start"]} ~ {mu["end"]} | {clip_name}\n'
                new_block += f'残留: {texts}\n修正:\n\n'

            # 插入到正确位置（按集号排序）
            ep_num = int(ep)
            if not existing or not existing.startswith('#'):
                existing = f'# 人工审查清单\n> 导出: {_dt.date.today().isoformat()}\n>\n> **填写方法**：「修正:」下一行开始，每行写一句台词，直接换行\n>\n'

            # 找到插入点：在所有条目中按集号排序
            all_blocks = []
            # 解析已有条目
            for m in re.finditer(r'\n?---\n(EP\d+) \|.*?\n修正:\n\n', existing, re.DOTALL):
                all_blocks.append((int(re.match(r'EP(\d+)', m.group(1)).group(1)), m.group(0)))
            # 删除旧条目占位
            existing = re.sub(r'(\n?---\nEP\d+ \|.*?\n修正:\n\n)', '', existing, flags=re.DOTALL)
            # 加入新条目
            all_blocks.append((ep_num, new_block))
            all_blocks.sort(key=lambda x: x[0])

            # 统计 + 重写
            with open(checklist_path, 'w', encoding='utf-8') as cf:
                cf.write(existing.rstrip() + '\n')
                total_items = 0
                for _, block in all_blocks:
                    cf.write(block)
                    total_items += block.count('修正:')
                cf.write('\n---\n')
                cf.write(f'> 共 {total_items} 条。在「修正:」下方每行写一句台词（可多行），脚本用 whisper VAD 自动分配时间轴。\n')
                cf.write(f'> 填写完毕后: python scripts/apply_review_fixes.py review-checklist.md --srt-dir <字幕目录>\n')
            print(f'[report] manual-review/ ← {exported} 个视频切片 + review-checklist.md(累计{total_items}条)', file=sys.stderr)

        delete_candidates = []
        for f in all_fixes:
            if f['confidence'] != 'none': continue
            text = f['original']
            has_jp = bool(re.search(r'[぀-ヿ一-鿿]', text))
            if not has_jp and len(text.split()) <= 2:
                delete_candidates.append(f)

        if delete_candidates:
            delete_path = os.path.join(args.update_report, 'whisper_delete_candidates.json')
            delete_fixes = []
            for f in delete_candidates:
                if f.get('lines'):
                    delete_fixes.append({
                        'action': 'delete_line',
                        'file': os.path.basename(args.srt),
                        'line': f['lines'][0],
                        'note': f'噪声误识别: {f["original"][:30]}'
                    })
            with open(delete_path, 'w', encoding='utf-8') as fh:
                json.dump(delete_fixes, fh, ensure_ascii=False, indent=2)
            print(f'[report] {delete_path} ← {len(delete_fixes)}条删除候选', file=sys.stderr)

    import shutil; shutil.rmtree(tmpdir)
    if not args.json:
        print('\n完成！')


if __name__ == '__main__':
    main()
