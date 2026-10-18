#!/usr/bin/env python3
"""Detect speech / singing / music events and write AudioHints JSON.

Runs FireRedVAD in the isolated qwen venv (main env has no torch).
The input must be 16 kHz mono PCM WAV; use ffmpeg first, e.g.:

    ffmpeg -ss 95 -to 1240 -i episode.mkv -vn -ar 16000 -ac 1 \
        -c:a pcm_s16le episode.wav

Usage:
    python scripts/audio/detect_events.py --wav episode.wav \
        --out temp/scans/EP001.audio_hints.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# This script lives one level below scripts/; bootstrap the package path.
_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import lib._path  # noqa: F401

from lib.audio.fireredvad import FireRedVadProvider


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wav', required=True)
    parser.add_argument('--out', default='', help='JSON path; stdout if empty')
    parser.add_argument('--mode', choices=('vad', 'aed'), default='aed')
    parser.add_argument('--python', default='',
                        help='isolated venv python (default: .venv-qwen)')
    parser.add_argument('--models', default='',
                        help='FireRedVAD model dir (default: models/FireRedVAD)')
    parser.add_argument('--gpu', action='store_true')
    parser.add_argument('--timeout', type=float, default=1800.0)
    args = parser.parse_args(argv)

    provider = FireRedVadProvider(
        python=args.python or None,
        models_dir=args.models or None,
        use_gpu=args.gpu,
        timeout=args.timeout,
    )
    try:
        hints = provider.detect(args.wav, mode=args.mode)
    except Exception as exc:
        print(f'[audio] detection failed: {exc}', file=sys.stderr)
        return 1

    text = json.dumps(hints.to_dict(), ensure_ascii=False, indent=2)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + '\n', encoding='utf-8')
        print(f'[audio] {args.out}', file=sys.stderr)
    else:
        print(text)

    summary = hints.summary()
    print(f'[audio] duration {summary["duration"]:.1f}s: '
          + ', '.join(f'{k} {v["seconds"]:.1f}s/{v["segments"]}seg '
                      f'({v["ratio"]:.1%})'
                      for k, v in summary.items() if k != 'duration'),
          file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
