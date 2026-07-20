#!/usr/bin/env python3
"""Whisper 整集重转录 — 对碎片多的集进行全片转录 + SRT 对齐。

适用场景: Tier 2 深度修复（碎片 ≥15 条的集）
原理: 整集音频给 Whisper 获得完整上下文，然后按时间对齐到现有 SRT，
      只替换乱码 cue，保留已正确翻译的 cue 不动。

用法:
  python whisper_full_episode.py video.mkv sub.srt \
    --whisper-cli D:/.../whisper-cli.exe \
    --model D:/.../kotoba-q5_0.bin \
    --update-report reports/ \
    --json
"""

import sys, os, re, subprocess, json, argparse, tempfile, io, time

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)


def _to_seconds(tc):
    """时间码 → 秒数。"""
    tc = tc.replace(',', '.').replace('-', ':')
    parts = tc.split(':')
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])


def _parse_srt(path):
    """解析 SRT 文件，返回 [{'start','end','start_s','end_s','text','line'}, ...]。
    复用 whisper_transcribe 中的 srt_utils。
    """
    from srt_utils import read_srt_file, parse_srt_cue

    lines = read_srt_file(path)
    cues, idx = [], 0
    while idx < len(lines):
        cue, idx = parse_srt_cue(lines, idx)
        if cue is None:
            idx += 1
            continue
        c = {
            'start': cue['start'],
            'end': cue['end'],
            'text': cue['text'].strip(),
            'line': cue.get('_start_line'),
            'start_s': _to_seconds(cue['start']),
            'end_s': _to_seconds(cue['end']),
        }
        # 标记乱码（含连续拉丁字母的 cue）
        c['is_garbled'] = bool(
            c['text']
            and not c['text'].startswith('[')
            and re.search(r'[a-zA-Z]{2,}', c['text'])
        )
        cues.append(c)

    # OP/ED 豁免
    max_end_s = max((c['end_s'] for c in cues), default=1500)
    for c in cues:
        if c['is_garbled'] and (c['start_s'] < 95 or c['start_s'] > max_end_s - 120):
            c['is_garbled'] = False

    return cues


def run_whisper_full(audio_path, whisper_cli, model_path, language='ja'):
    """整集转录，返回 [{start_s, end_s, text}, ...] 带完整时间戳。"""
    cmd = [
        whisper_cli, '-m', model_path, '-f', audio_path, '-l', language,
        '-t', '8', '-p', '2', '-bs', '5', '-bo', '8',
        '-oj', '-of', audio_path + '.whisper', '--print-progress',
        '-nth', '0.6', '-mc', '0', '-sns',
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    for line in (proc.stderr or '').strip().split('\n'):
        if line.strip():
            print(f'  [whisper] {line.strip()}', file=sys.stderr)

    json_path = audio_path + '.whisper.json'
    if not os.path.exists(json_path):
        print('⚠ whisper-cli 未生成 JSON', file=sys.stderr)
        return []

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError):
        print('⚠ whisper JSON 解码失败', file=sys.stderr)
        if os.path.exists(json_path):
            os.remove(json_path)
        return []
    os.remove(json_path)

    segs = []
    for seg in data.get('transcription', []):
        ts_from = seg.get('timestamps', {}).get('from', '00:00:00,000').replace(',', '.')
        ts_to = seg.get('timestamps', {}).get('to', '00:00:08,000').replace(',', '.')
        text = seg.get('text', '').strip()
        if text:
            segs.append({
                'start_s': _to_seconds(ts_from),
                'end_s': _to_seconds(ts_to),
                'text': text,
            })
    return segs


def is_valid_japanese(text):
    """判断文本是否为有效日语（含假名/汉字，不是纯罗马字幻觉）。"""
    has_kana = bool(re.search(r'[぀-ヿ]', text))
    has_kanji = bool(re.search(r'[一-鿿]', text))
    is_pure_romaji = bool(re.fullmatch(r'[a-zA-Z\s\d.,!?\'\"\-]+', text))
    # 有效日语: 含假名或汉字，且不是纯罗马字
    return (has_kana or has_kanji) and not is_pure_romaji


def align_and_fix(cues, whisper_segs):
    """对齐 whisper 输出到 SRT cues，生成修复列表。

    算法:
      对每个乱码 cue，找时间重叠 ≥50% 的 whisper segment。
      如果 whisper 文本是有效日语 → 替换。
      如果 whisper 文本无效 → 保留未匹配。
      对有效 cue 不做任何修改。
    """
    fixes = []
    stats = {'fixed': 0, 'unmatched': 0, 'kept': 0}

    for c in cues:
        if not c['is_garbled']:
            stats['kept'] += 1
            continue

        # 找时间重叠的 whisper segments
        best_overlap = 0
        best_seg = None
        for wh in whisper_segs:
            # 区间重叠计算
            overlap_start = max(c['start_s'], wh['start_s'])
            overlap_end = min(c['end_s'], wh['end_s'])
            overlap = overlap_end - overlap_start
            if overlap > 0:
                cue_dur = c['end_s'] - c['start_s']
                if cue_dur > 0:
                    ratio = overlap / cue_dur
                    if ratio > best_overlap:
                        best_overlap = ratio
                        best_seg = wh

        if best_seg and best_overlap >= 0.3 and is_valid_japanese(best_seg['text']):
            fixes.append({
                'start': c['start'],
                'end': c['end'],
                'original': c['text'][:80],
                'replacement': best_seg['text'][:80],
                'confidence': 'high' if best_overlap >= 0.5 else 'retry',
                'model': 'full_episode',
                'covered_count': 1,
                'covered_starts': [c['start']],
                'lines': [c.get('line')] if c.get('line') else [],
            })
            stats['fixed'] += 1
        else:
            fixes.append({
                'start': c['start'],
                'end': c['end'],
                'original': c['text'],
                'replacement': None,
                'confidence': 'none',
                'model': 'full_episode',
                'covered_count': 0,
                'covered_starts': [],
                'lines': [c.get('line')] if c.get('line') else [],
            })
            stats['unmatched'] += 1

    return fixes, stats


def apply_fixes_to_srt(srt_path, fixes):
    """将修复写入 SRT 文件。只修改有 replacement 的 cue。"""
    from srt_utils import read_srt_file, parse_srt_cue, build_srt_cue_lines

    lines = read_srt_file(srt_path)
    parsed_cues, idx = [], 0
    while idx < len(lines):
        cue, idx = parse_srt_cue(lines, idx)
        if cue is None:
            idx += 1
            continue
        parsed_cues.append(cue)

    # 按 start 建立索引
    fixed_count = 0
    for cue in parsed_cues:
        for f in fixes:
            if f['start'] == cue['start'] and f.get('replacement'):
                cue['text'] = f['replacement']
                fixed_count += 1
                break

    # 重建 SRT
    with open(srt_path, 'w', encoding='utf-8-sig') as f:
        for i, cue in enumerate(parsed_cues, 1):
            f.write(f'{i}\n')
            f.write(f'{cue["start"]} --> {cue["end"]}\n')
            f.write(f'{cue["text"]}\n\n')

    return fixed_count


def update_report(reports_dir, srt_path, fixes):
    """更新统一报告步骤15。"""
    import datetime
    from update_report import upsert_entries

    report_path = os.path.join(reports_dir, '问题解决报告.md')
    ep = os.path.basename(srt_path).split('-')[1].strip().split()[0] if '-' in os.path.basename(srt_path) else '???'
    ep_tag = f'EP{ep}'

    entries = []
    for f in fixes:
        if f['confidence'] in ('high', 'retry'):
            entries.append({
                'ep': ep_tag,
                'time': f['start'],
                'original': f['original'][:80],
                'corrected': f.get('replacement', '')[:80],
                'status': '✅',
            })

    if entries:
        upsert_entries(report_path, step=15, entries=entries)
        print(f'[report] 步骤15: +{len(entries)}条', file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description='Whisper 整集重转录 + SRT 对齐')
    parser.add_argument('video')
    parser.add_argument('srt')
    parser.add_argument('--whisper-cli', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--language', '-l', default='ja')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--update-report', metavar='REPORTS_DIR')
    args = parser.parse_args()

    # ── 阶段 1: 扫描 ──
    print(f'[1/4] 扫描 SRT ...')
    cues = _parse_srt(args.srt)
    garbled = [c for c in cues if c['is_garbled']]
    print(f'  {len(cues)} cues, {len(garbled)} 乱码')

    if not garbled:
        print('无需修复！')
        return

    # ── 阶段 2: 提取全片音频 ──
    print(f'[2/4] 提取全片音频 (WAV 16kHz mono) ...')
    tmpdir = tempfile.mkdtemp()
    full_audio = os.path.join(tmpdir, 'full.wav')
    subprocess.run([
        'ffmpeg', '-y', '-i', args.video, '-vn',
        '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le',
        full_audio,
    ], capture_output=True, check=True)

    # ── 阶段 3: Whisper 整集转录 ──
    print(f'[3/4] Whisper 整集转录 ...')
    whisper_segs = run_whisper_full(full_audio, args.whisper_cli, args.model, args.language)
    print(f'  {len(whisper_segs)} segments')

    if not whisper_segs:
        print('⚠ Whisper 无输出')
        import shutil; shutil.rmtree(tmpdir)
        return

    # ── 阶段 4: 对齐 + 修复 ──
    print(f'[4/4] 对齐 & 修复 ...')
    fixes, stats = align_and_fix(cues, whisper_segs)

    if args.json or args.dry_run:
        report = {
            'episode': os.path.basename(args.srt),
            'total_cues': len(cues),
            'garbled_cues': len(garbled),
            'stats': stats,
            'fixes': fixes,
        }
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))

    if args.dry_run:
        print(f'\n[DRY-RUN] 可修复: {stats["fixed"]}, 未匹配: {stats["unmatched"]}, 保留: {stats["kept"]}')
        for f in [fx for fx in fixes if fx['confidence'] != 'none'][:10]:
            print(f'  [{f["start"]}] {f["original"][:40]} → {f["replacement"][:40]}')
    else:
        fixed = apply_fixes_to_srt(args.srt, fixes)
        print(f'  已写入: {fixed} cues 修复')

    # ── 报告同步 ──
    if args.update_report:
        update_report(args.update_report, args.srt, fixes)

    import shutil; shutil.rmtree(tmpdir)
    print(f'\n完成: {stats["fixed"]}✅ / {stats["unmatched"]}⬜ (共{stats["kept"] + stats["fixed"] + stats["unmatched"]} cues)')


if __name__ == '__main__':
    main()
