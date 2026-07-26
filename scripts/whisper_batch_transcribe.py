#!/usr/bin/env python3
"""Whisper 批量转录 — 从视频直接生成 SRT，不依赖已有字幕。

适用场景：没有任何字幕文件，或已有字幕质量太差不值得修复，需要
从视频音频从头生成干净字幕。

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
from lib.project_utils import find_video
from lib.config import WHISPER_CLI, WHISPER_MODEL

setup_windows_utf8()


def main():
    parser = argparse.ArgumentParser(description='Whisper batch transcribe video → SRT')
    parser.add_argument('--video-dir', required=True, help='Video directory')
    parser.add_argument('--output-dir', required=True, help='Output SRT directory')
    parser.add_argument('--lang', default='ja', help='Language code (default: ja)')
    parser.add_argument('--limit', type=int, default=0, help='Limit N episodes (0=all)')
    parser.add_argument('--project-dir', default=None, help='Project root (default: CWD)')
    parser.add_argument('--start-from', type=int, default=1, help='Start from EP N')
    args = parser.parse_args()

    project_dir = args.project_dir or os.getcwd()
    video_dir = args.video_dir

    if not WHISPER_CLI or not WHISPER_MODEL:
        print('ERROR: WHISPER_CLI and WHISPER_MODEL env vars required.', file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)

    end_ep = 194 if args.limit == 0 else args.start_from + args.limit
    end_ep = min(end_ep, 194)

    total_cues = 0
    for ep_num in range(args.start_from, end_ep):
        episode = f'EP{ep_num:03d}'

        # Find video
        video_path = find_video(project_dir, episode, video_dir)
        if not video_path:
            print(f'{episode}: video not found, skip')
            continue

        print(f'{episode}: {os.path.basename(video_path)}')

        # Extract full audio
        tmpdir = tempfile.mkdtemp()
        audio_path = os.path.join(tmpdir, 'full.wav')
        try:
            extract_audio_wav(video_path, audio_path)
        except Exception as e:
            print(f'{episode}: audio extraction failed: {e}')
            continue

        # Whisper transcribe
        segs = run_whisper(audio_path, WHISPER_CLI, WHISPER_MODEL,
                           language=args.lang)
        if not segs:
            print(f'{episode}: Whisper returned 0 segments')
            continue

        # Filter low-confidence hallucinations
        kept, discarded = filter_low_confidence(segs)

        # Build SRT cues
        cues = []
        for i, seg in enumerate(kept, 1):
            text = seg['text'].strip()
            if not text:
                continue
            cues.append({
                'index': i,
                'start': format_tc(seg['start_s']),
                'end': format_tc(seg['end_s']),
                'text': text,
            })

        # Renumber
        for i, c in enumerate(cues, 1):
            c['index'] = i

        # Write SRT
        srt_path = os.path.join(args.output_dir, f'{episode}.srt')
        write_srt(srt_path, cues)

        # Cleanup
        try:
            os.unlink(audio_path)
            os.rmdir(tmpdir)
        except OSError:
            pass

        total_cues += len(cues)
        print(f'  → {len(cues)} cues ({len(discarded)} discarded)')

    print(f'\nDone: {end_ep - args.start_from} episodes, {total_cues} cues total')


if __name__ == '__main__':
    main()
