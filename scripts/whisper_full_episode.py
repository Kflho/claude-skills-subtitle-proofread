#!/usr/bin/env python3
"""Whisper 整集重转录 — 对碎片多的集进行全片转录 + SRT 对齐 (Tier 2)。

用法:
  python whisper_full_episode.py video.mkv sub.srt \
    --whisper-cli D:/.../whisper-cli.exe \
    --model D:/.../kotoba-q5_0.bin \
    --update-report reports/ --json
"""

import sys, os, re, json, argparse, tempfile

_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

from whisper_utils import (
    setup_windows_utf8, extract_ep_number, to_seconds,
    parse_srt, apply_fixes_to_srt, run_whisper,
    extract_audio_wav, is_valid_japanese,
)
setup_windows_utf8()


def align_and_fix(cues, whisper_segs):
    """对齐 whisper 输出到 SRT cues，生成修复列表。"""
    fixes = []
    stats = {'fixed': 0, 'unmatched': 0, 'kept': 0}

    for c in cues:
        if not c.get('is_garbled'):
            stats['kept'] += 1
            continue

        best_overlap = 0
        best_seg = None
        for wh in whisper_segs:
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
                'start': c['start'], 'end': c['end'],
                'original': c['text'][:80],
                'replacement': best_seg['text'][:80],
                'confidence': 'high' if best_overlap >= 0.5 else 'retry',
                'model': 'full_episode',
            })
            stats['fixed'] += 1
        else:
            fixes.append({
                'start': c['start'], 'end': c['end'],
                'original': c['text'],
                'replacement': None,
                'confidence': 'none',
                'model': 'full_episode',
            })
            stats['unmatched'] += 1

    return fixes, stats


def update_report(reports_dir, srt_path, fixes):
    """更新统一报告步骤15。"""
    from update_report import upsert_entries

    report_path = os.path.join(reports_dir, '问题解决报告.md')
    ep = extract_ep_number(srt_path)

    entries = []
    for f in fixes:
        if f['confidence'] in ('high', 'retry'):
            entries.append({
                'ep': ep, 'time': f['start'],
                'original': f['original'][:80],
                'corrected': f.get('replacement', '')[:80],
                'status': '✅',
            })
    if entries:
        upsert_entries(report_path, step=15, entries=entries)
        print(f'[report] 步骤15: +{len(entries)}条', file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description='Whisper 整集重转录 + SRT 对齐')
    parser.add_argument('video'); parser.add_argument('srt')
    parser.add_argument('--whisper-cli', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--language', '-l', default='ja')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--update-report', metavar='REPORTS_DIR')
    parser.add_argument('--separate-vocals', action='store_true',
                        help='用 demucs 分离人声后再转录（去 BGM，减少幻觉）')
    args = parser.parse_args()

    # 扫描
    print('[1/3] 扫描 SRT ...')
    cues = parse_srt(args.srt)
    garbled = [c for c in cues if c.get('is_garbled')]
    print(f'  {len(cues)} cues, {len(garbled)} 乱码')
    if not garbled:
        print('无需修复！'); return

    # 提取音频 + 转录
    print('[2/3] 提取音频 + Whisper 整集转录 ...')
    tmpdir = tempfile.mkdtemp()
    full_audio = os.path.join(tmpdir, 'full.wav')
    extract_audio_wav(args.video, full_audio)

    # 可选：人声分离
    if args.separate_vocals:
        from whisper_utils import separate_vocals
        full_audio = separate_vocals(full_audio, output_dir=tmpdir)
    whisper_segs = run_whisper(full_audio, args.whisper_cli, args.model, args.language)
    print(f'  {len(whisper_segs)} segments')

    if not whisper_segs:
        print('⚠ Whisper 无输出'); import shutil; shutil.rmtree(tmpdir); return

    # 对齐 + 修复
    print('[3/3] 对齐 & 修复 ...')
    fixes, stats = align_and_fix(cues, whisper_segs)

    if args.json or args.dry_run:
        report = {
            'episode': os.path.basename(args.srt),
            'total_cues': len(cues), 'garbled_cues': len(garbled),
            'stats': stats, 'fixes': fixes,
        }
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))

    if args.dry_run:
        print(f'\n[DRY-RUN] 可修复: {stats["fixed"]}, 未匹配: {stats["unmatched"]}')
        for f in [fx for fx in fixes if fx['confidence'] != 'none'][:10]:
            print(f'  [{f["start"]}] {f["original"][:40]} → {f["replacement"][:40]}')
    else:
        fixed = apply_fixes_to_srt(args.srt, fixes)
        print(f'  已写入: {fixed} cues 修复')

    if args.update_report:
        update_report(args.update_report, args.srt, fixes)

    import shutil; shutil.rmtree(tmpdir)
    print(f'\n完成: {stats["fixed"]}✅ / {stats["unmatched"]}⬜')


if __name__ == '__main__':
    main()
