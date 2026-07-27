#!/usr/bin/env python3
"""Whisper 切段 + 翻译 → 中文参考字幕。

用于修复全量翻译中因 Whisper 乱码导致 LLM 翻不出来的个别 cue。
给出集号和需要重翻的时间轴，输出：Whisper 干净日文 + 中文翻译。

Usage:
  # 单段时间轴
  python whisper_spot_fix.py EP001 --start 24:35 --end 24:44

  # 多段（逗号分隔）
  python whisper_spot_fix.py EP001 --spots "24:35-24:44,12:10-12:18"

  # 指定视频目录和项目根
  python whisper_spot_fix.py EP001 --start 24:35 --end 24:44 \\
    --video-dir "D:/Video/..." --project-dir "."

  # 仅输出 Whisper 日文（不翻译）
  python whisper_spot_fix.py EP001 --start 24:35 --end 24:44 --no-translate
"""

import argparse
import json
import os
import re
import sys
import tempfile
import time

import lib._path  # noqa: F401
from lib.whisper_utils import extract_audio_wav, run_whisper
from lib.config import WHISPER_CLI, WHISPER_MODEL, LLM_API_KEY, LLM_MODEL, LLM_BASE_URL
from lib.project_utils import find_video


def parse_timestamp(s):
    """Parse 'MM:SS' or 'MM:SS.ms' or 'HH:MM:SS' or 'HH:MM:SS.ms' to seconds."""
    parts = s.strip().split(':')
    if len(parts) == 2:
        m, s = parts
        h = 0
    elif len(parts) == 3:
        h, m, s = parts
    else:
        raise ValueError(f'Invalid timestamp: {s}')
    return int(h) * 3600 + int(m) * 60 + float(s)


def translate_text(text, api_key, model, base_url):
    """Translate a single Japanese text to Chinese via LLM API."""
    import urllib.request

    messages = [
        {'role': 'system', 'content': '你是日→中字幕翻译器。只输出中文译文，不要解释。'},
        {'role': 'user', 'content': f'翻译成中文：\n{text}'},
    ]

    req = urllib.request.Request(
        f'{base_url}/chat/completions',
        data=json.dumps({
            'model': model,
            'messages': messages,
            'temperature': 0.3,
            'max_tokens': 500,
        }).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
        },
    )

    try:
        resp = urllib.request.urlopen(req, timeout=60)
        body = json.loads(resp.read().decode('utf-8'))
        return body['choices'][0]['message']['content'].strip()
    except Exception as e:
        print(f'  [translate] API error: {e}', file=sys.stderr)
        return None


def main():
    parser = argparse.ArgumentParser(
        description='Whisper spot-fix: extract segment → Whisper → translate')
    parser.add_argument('episode', help='Episode ID, e.g. EP001')
    parser.add_argument('--start', help='Start timestamp (MM:SS or HH:MM:SS)')
    parser.add_argument('--end', help='End timestamp (MM:SS or HH:MM:SS)')
    parser.add_argument('--spots', help='Multiple spots: "24:35-24:44,12:10-12:18"')
    parser.add_argument('--padding', type=float, default=3.0,
                        help='Padding seconds around spot (default: 3)')
    parser.add_argument('--video-dir',
                        default=r'D:\Video\Animation\TV\[Anonymoose] 鉄腕アトム (DVD, 10bit)',
                        help='Video directory')
    parser.add_argument('--project-dir', default=os.getcwd(),
                        help='Project root (default: CWD)')
    parser.add_argument('--no-translate', action='store_true',
                        help='Skip translation, output Japanese only')
    parser.add_argument('--model', default=LLM_MODEL or 'deepseek-v4-pro',
                        help='LLM model for translation')
    parser.add_argument('--base-url', default=LLM_BASE_URL or 'https://api.deepseek.com/v1',
                        help='API base URL')
    args = parser.parse_args()

    # Parse spots
    spots = []
    if args.spots:
        for part in args.spots.split(','):
            part = part.strip()
            if '-' in part:
                a, b = part.split('-', 1)
                spots.append((parse_timestamp(a), parse_timestamp(b)))
    elif args.start and args.end:
        spots.append((parse_timestamp(args.start), parse_timestamp(args.end)))
    else:
        print('ERROR: need --start/--end or --spots', file=sys.stderr)
        sys.exit(1)

    # Find video
    video_path = find_video(args.project_dir, args.episode, args.video_dir)
    if not video_path:
        print(f'ERROR: video not found for {args.episode}', file=sys.stderr)
        sys.exit(1)
    print(f'Video: {os.path.basename(video_path)}', file=sys.stderr)

    # API key (only needed for translation)
    api_key = LLM_API_KEY
    if not args.no_translate and not api_key:
        print('WARNING: LLM_API_KEY not set — output Japanese only', file=sys.stderr)

    for idx, (start_s, end_s) in enumerate(spots, 1):
        print(f'\n═══ Spot {idx}: {start_s:.1f}s → {end_s:.1f}s ═══', file=sys.stderr)

        # Extract audio segment with padding
        ss = max(0, start_s - args.padding)
        duration = (end_s - start_s) + 2 * args.padding

        tmpdir = tempfile.mkdtemp()
        audio_path = os.path.join(tmpdir, 'segment.wav')
        try:
            extract_audio_wav(video_path, audio_path, ss=ss, duration=duration)
        except Exception as e:
            print(f'  ERROR: audio extraction failed: {e}', file=sys.stderr)
            continue

        # Whisper
        segs = run_whisper(audio_path, WHISPER_CLI, WHISPER_MODEL, language='ja')
        if not segs:
            print('  Whisper returned 0 segments', file=sys.stderr)
            continue

        # Filter to relevant segments (within the spot range, adjusted to original timeline)
        relevant = []
        for s in segs:
            abs_start = ss + s['start_s']
            abs_end = ss + s['end_s']
            # Only include segments that overlap with the spot
            if abs_end >= start_s and abs_start <= end_s:
                relevant.append({
                    'start': abs_start,
                    'end': abs_end,
                    'text': s['text'].strip(),
                })

        if not relevant:
            print('  No segments overlap with the spot range', file=sys.stderr)
            continue

        # Print results
        print()
        for r in relevant:
            m_s, s_s = int(r['start'] // 60), r['start'] % 60
            m_e, s_e = int(r['end'] // 60), r['end'] % 60
            ja_text = r['text']

            print(f'[{m_s:02d}:{s_s:05.2f} → {m_e:02d}:{s_e:05.2f}]')
            print(f'  JA: {ja_text}')

            if not args.no_translate and api_key:
                zh = translate_text(ja_text, api_key, args.model, args.base_url)
                if zh:
                    print(f'  ZH: {zh}')
                else:
                    print(f'  ZH: [翻译失败]')
                time.sleep(0.5)
            print()

        # Cleanup
        try:
            os.unlink(audio_path)
            os.rmdir(tmpdir)
        except OSError:
            pass


if __name__ == '__main__':
    main()
