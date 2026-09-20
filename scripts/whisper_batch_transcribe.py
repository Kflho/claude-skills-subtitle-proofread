#!/usr/bin/env python3
"""Whisper 批量转录 — 从视频直接生成 SRT，不依赖已有字幕。

适用场景：没有任何字幕文件，或已有字幕质量太差不值得修复，需要
从视频音频从头生成干净字幕。

集号识别交给 lib.video_scan：直接枚举视频文件再推导 token，因此
EP### 之外还有 SP## / OVA## / 特别篇 等命名都能跑（不再硬编码 1-194）。

Usage:
  python whisper_batch_transcribe.py \
    --video-dir "D:/Video/..." \
    --output-dir "日语参考字幕" \
    --lang ja

  # 限制前3集测试
  python whisper_batch_transcribe.py \
    --video-dir "D:/Video/..." \
    --output-dir "日语参考字幕" \
    --lang ja --limit 3

  # 只转录指定集（支持 EP001-EP010 / SP01,SP02 / 1-3）
  python whisper_batch_transcribe.py \
    --video-dir "D:/Video/..." \
    --output-dir "temp/ja_raw" \
    --lang ja --episodes SP01,SP02
"""

import argparse
import os
import sys
import tempfile

import lib._path  # noqa: F401

from lib.whisper_utils import (
    setup_windows_utf8, extract_audio_wav, run_whisper,
    filter_low_confidence, format_tc, write_srt,
)
from lib.video_scan import select_videos
from lib.config import WHISPER_CLI, WHISPER_MODEL, VIDEO_CANDIDATES

setup_windows_utf8()


def timestamps_sane(segs, min_count=10):
    """检测 whisper.cpp 并行分块的时间戳损坏。

    `whisper_full_parallel`（-p N>1）在某些 build 上不对第 2..N 块施加时间偏移，
    表现为分块边界之后的时间戳集体塌成 0（end <= start），或整体非单调。
    命中时调用方应改用 -p 1 重跑。
    """
    if len(segs) < min_count:
        return True
    degenerate = sum(1 for s in segs if s['end_s'] <= s['start_s'])
    if degenerate > len(segs) * 0.10:
        return False
    nonmono = sum(1 for a, b in zip(segs, segs[1:])
                  if b['start_s'] < a['start_s'] - 0.001)
    return nonmono <= len(segs) * 0.05


def main():
    parser = argparse.ArgumentParser(description='Whisper batch transcribe video → SRT')
    parser.add_argument('--video-dir', required=True, help='Video directory')
    parser.add_argument('--output-dir', required=True, help='Output SRT directory')
    parser.add_argument('--lang', default='ja', help='Language code (default: ja)')
    parser.add_argument('--limit', type=int, default=0, help='Limit N episodes (0=all)')
    parser.add_argument('--project-dir', default=None, help='Project root (default: CWD)')
    parser.add_argument('--start-from', default=None,
                        help='Start from this episode token (e.g. EP050 or SP01)')
    parser.add_argument('--episodes', '-e', default=None,
                        help='Episodes to transcribe: EP001-EP010, SP01,SP02, 1-3')
    parser.add_argument('--processors', type=int, default=2,
                        help='whisper.cpp -p (default: 2; auto-retries at 1 '
                             'if timestamps come back corrupted)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Only list the videos that would be transcribed')
    args = parser.parse_args()

    project_dir = args.project_dir or os.getcwd()

    if not WHISPER_CLI or not WHISPER_MODEL:
        print('ERROR: WHISPER_CLI and WHISPER_MODEL env vars required.', file=sys.stderr)
        sys.exit(1)

    dirs = [args.video_dir] if args.video_dir else []
    if not args.video_dir:
        dirs = [os.path.join(project_dir, d) for d in VIDEO_CANDIDATES]
    dirs = [d for d in dirs if os.path.isdir(d)]

    entries = select_videos(dirs, episodes=args.episodes,
                            start_from=args.start_from, limit=args.limit)
    if not entries:
        print(f'ERROR: no video files selected from {dirs}'
              + (f' (--episodes {args.episodes})' if args.episodes else ''),
              file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)
    print(f'{len(entries)} video(s) selected: '
          f'{", ".join(t for t, _ in entries)}', file=sys.stderr)

    if args.dry_run:
        for episode, video_path in entries:
            print(f'  {episode:8s} {os.path.basename(video_path)}')
        print(f'\n[DRY RUN] nothing transcribed; would write to {args.output_dir}')
        return

    total_cues = 0
    for episode, video_path in entries:
        print(f'{episode}: {os.path.basename(video_path)}')

        tmpdir = tempfile.mkdtemp()
        audio_path = os.path.join(tmpdir, 'full.wav')
        try:
            extract_audio_wav(video_path, audio_path, prefer_lang=args.lang)
        except Exception as e:
            print(f'  {episode}: audio extraction failed: {e}')
            continue

        processors = args.processors
        try:
            segs = run_whisper(audio_path, WHISPER_CLI, WHISPER_MODEL,
                               language=args.lang, processors=processors)
            if segs and not timestamps_sane(segs) and processors > 1:
                print(f'  ⚠ 时间戳异常（whisper.cpp 并行分块偏移丢失），'
                      f'改用 -p 1 重跑', file=sys.stderr)
                processors = 1
                segs = run_whisper(audio_path, WHISPER_CLI, WHISPER_MODEL,
                                   language=args.lang, processors=1)
        except Exception as e:
            print(f'  {episode}: Whisper failed: {e}')
            continue

        if not segs:
            print(f'  {episode}: Whisper returned 0 segments')
            continue

        # Filter low-confidence hallucinations
        kept, discarded = filter_low_confidence(segs)

        cues = []
        for seg in kept:
            text = seg['text'].strip()
            if not text:
                continue
            cues.append({
                'index': len(cues) + 1,
                'start': format_tc(seg['start_s']),
                'end': format_tc(seg['end_s']),
                'text': text,
            })

        srt_path = os.path.join(args.output_dir, f'{episode}.srt')
        # 重跑会盖掉上一轮的转录，而下游（翻译、复用、OP/ED 定本）全都建在它上面。
        # 备份一次，别让「重跑转录」变成不可逆操作（temp/ 不受 git 管）。
        if os.path.exists(srt_path):
            from lib.project_utils import backup_file
            backup_file(srt_path)
        write_srt(srt_path, cues)

        # Cleanup
        try:
            os.unlink(audio_path)
            os.rmdir(tmpdir)
        except OSError:
            pass

        total_cues += len(cues)
        print(f'  → {len(cues)} cues ({len(discarded)} discarded, -p {processors})')

    print(f'\nDone: {len(entries)} episodes, {total_cues} cues total')


if __name__ == '__main__':
    main()
