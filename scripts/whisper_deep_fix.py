#!/usr/bin/env python3
"""Whisper 深度碎片修复 (Tier 3) — silencedetect 拆分未修复碎片，逐个重转录。

用法:
  python whisper_deep_fix.py \
    --report reports/问题解决报告.md \
    --srt-dir AI审查后/ \
    --video-dir reports/manual-review/ \
    --whisper-cli ... --model .../large-v3-q5_0.bin
"""

import sys, os, re, subprocess, argparse, tempfile

_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

from whisper_utils import (
    setup_windows_utf8, extract_ep_number, to_seconds,
    parse_srt, write_srt, run_whisper,
    extract_audio_wav, get_audio_duration, is_valid_japanese,
)
setup_windows_utf8()

SILENCE_MIN = 1.5
PADDING = 0.3


def detect_silence_breaks(audio_path, dur):
    """ffmpeg silencedetect 找静音断点。返回 [break_time, ...]（秒）。"""
    proc = subprocess.run(
        ['ffmpeg', '-i', audio_path,
         '-af', f'silencedetect=n=-30dB:d={SILENCE_MIN}',
         '-f', 'null', '-'],
        capture_output=True, text=True)
    breaks = []
    for line in (proc.stderr or '').split('\n'):
        m = re.search(r'silence_end:\s*([\d.]+)', line)
        if m:
            t = float(m.group(1))
            if PADDING < t < dur - PADDING:
                breaks.append(t)
    return sorted(set(round(b, 1) for b in breaks))


def split_fragment(dur, breaks):
    """根据断点拆分子片段。返回 [(ss, es), ...] 秒数对。"""
    if not breaks:
        return [(0, dur)]
    segments, prev = [], 0
    for b in breaks:
        if b - prev >= 1.0:
            segments.append((max(0, prev - PADDING), min(dur, b + PADDING)))
        prev = b
    if dur - prev >= 1.0:
        segments.append((max(0, prev - PADDING), dur))
    return segments if segments else [(0, dur)]


def find_srt(srt_dir, ep_tag):
    """根据集号找到 SRT 文件路径。"""
    ep_num = ep_tag.replace('EP', '')
    for fname in os.listdir(srt_dir):
        if fname.endswith('.srt') and (ep_tag in fname or ep_num in fname):
            return os.path.join(srt_dir, fname)
    return None


def find_clip(video_dir, ep, time_start):
    """根据集号+起始时间找到对应的 mp4 切片。"""
    safe = time_start.replace(',', '-').replace('.', '-').replace(':', '-')
    for fname in os.listdir(video_dir):
        if fname.startswith(ep) and safe[:12] in fname and fname.endswith('.mp4'):
            return os.path.join(video_dir, fname)
    return None


def main():
    parser = argparse.ArgumentParser(description='Whisper 深度碎片修复')
    parser.add_argument('--report', required=True)
    parser.add_argument('--srt-dir', required=True)
    parser.add_argument('--video-dir', required=True)
    parser.add_argument('--whisper-cli', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--episode', '-e')
    args = parser.parse_args()

    from update_report import read_report, update_entry_status

    data = read_report(args.report)
    step16 = [e for e in data.get(16, []) if e.get('status') == '⬜']
    if not step16:
        print('步骤16无待处理条目'); return

    # 按集分组
    by_ep = {}
    for entry in step16:
        ep = entry['ep']
        if args.episode and ep != args.episode:
            continue
        by_ep.setdefault(ep, []).append(entry)

    print(f'待深度处理: {sum(len(v) for v in by_ep.values())} 条 ({len(by_ep)} 集)')
    total_fixed = total_unmatched = 0

    for ep, entries in sorted(by_ep.items()):
        srt_path = find_srt(args.srt_dir, ep)
        if not srt_path:
            print(f'  ⚠ {ep}: 未找到 SRT'); continue

        for entry in entries:
            time_start = entry['time']
            clip = find_clip(args.video_dir, ep, time_start)
            if not clip:
                total_unmatched += 1; continue

            tmpdir = tempfile.mkdtemp()
            tmp_audio = os.path.join(tmpdir, 'frag.wav')
            try:
                extract_audio_wav(clip, tmp_audio)
            except subprocess.CalledProcessError:
                import shutil; shutil.rmtree(tmpdir)
                total_unmatched += 1; continue

            dur = get_audio_duration(tmp_audio)
            if dur < 1.0:
                import shutil; shutil.rmtree(tmpdir)
                total_unmatched += 1; continue

            breaks = detect_silence_breaks(tmp_audio, dur)
            segments = split_fragment(dur, breaks)

            if args.json and not args.dry_run:
                print(f'  {ep} [{time_start}] dur={dur:.0f}s → {len(segments)}段')

            best_text = None
            for ss, es in segments:
                seg_audio = os.path.join(tmpdir, f'seg_{ss:.1f}.wav')
                try:
                    extract_audio_wav(tmp_audio, seg_audio, ss=ss, duration=es - ss)
                except subprocess.CalledProcessError:
                    continue

                results = run_whisper(seg_audio, args.whisper_cli, args.model,
                                     threads=4, processors=1, beam_size=5, best_of=4, nth=0.3)
                for seg in results:
                    if is_valid_japanese(seg['text']):
                        if not best_text or len(seg['text']) > len(best_text):
                            best_text = seg['text']

            if best_text:
                if not args.dry_run:
                    cues = parse_srt(srt_path, mark_garbled=False)
                    start_s = to_seconds(time_start)
                    for cue in cues:
                        if abs(to_seconds(cue['start']) - start_s) < 0.5:
                            cue['text'] = best_text
                            break
                    write_srt(srt_path, cues)
                    update_entry_status(args.report, step=16, ep=ep,
                                      time=time_start, corrected=best_text, status='✅')
                total_fixed += 1
                if args.json:
                    print(f'    → "{best_text[:60]}"')
            else:
                total_unmatched += 1

            import shutil; shutil.rmtree(tmpdir)

    print(f'\n深度修复完成: {total_fixed}✅ / {total_unmatched}⬜')
    if args.dry_run:
        print('[DRY-RUN] 未实际写入')


if __name__ == '__main__':
    main()
