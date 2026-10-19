#!/usr/bin/env python3
"""Build a full-series benchmark manifest from videos + Japanese CC.

The existing manifest is deliberately small (EP001–003).  When the full
series is available, this script maps every video to its CC by episode
number and creates a dev/test split.  Keep the split fixed before looking
at held-out results.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

VIDEO_EP_RE = re.compile(r'^(\d{1,3})\s*[、.．]')
CC_EP_RE = re.compile(r'S01E(\d{1,3})', re.IGNORECASE)

# Known main-story windows for the three episodes already benchmarked.
KNOWN_WINDOWS = {
    1: [95, 1240],
    2: [95, 1233],
    3: [95, 1240],
}


def episode_number(name: str, pattern: re.Pattern) -> int | None:
    match = pattern.search(name)
    return int(match.group(1)) if match else None


def build(video_dir: str, cc_dir: str, *, dev_count: int = 10,
          default_window: tuple[float, float] = (95.0, 1240.0)) -> dict:
    videos = {}
    for name in os.listdir(video_dir):
        if name.lower().endswith('.mkv'):
            num = episode_number(name, VIDEO_EP_RE)
            if num:
                videos[num] = os.path.join(video_dir, name)
    ccs = {}
    for name in os.listdir(cc_dir):
        if name.lower().endswith('.srt') and 'S01E' in name.upper():
            num = episode_number(name, CC_EP_RE)
            if num:
                ccs[num] = os.path.join(cc_dir, name)

    common = sorted(set(videos) & set(ccs))
    episodes = []
    for num in common:
        window = KNOWN_WINDOWS.get(num, list(default_window))
        episodes.append({
            'id': f'EP{num:03d}',
            'split': 'dev' if num <= dev_count else 'test',
            'category': 'unclassified',
            'video': videos[num],
            'reference_srt': ccs[num],
            'main_window': window,
            'asr_json': f'temp/asr/digimon_ep{num:03d}_main.qwen3.json',
            'context': '',
            'timestamps': True,
        })
    return {
        'project': 'digimon-adventure',
        'language': 'Japanese',
        'protocol': 'eval/protocol.md',
        'episodes': episodes,
        '_note': (f'Generated from {len(common)} matched episodes; '
                  f'dev=EP001–EP{dev_count:03d}, test=EP{dev_count + 1:03d}+. '
                  'Fix the split before reporting held-out results.'),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--video-dir', required=True)
    parser.add_argument('--cc-dir', required=True)
    parser.add_argument('--out', default='eval/manifest.digimon.full.json')
    parser.add_argument('--dev-count', type=int, default=10)
    args = parser.parse_args(argv)
    data = build(args.video_dir, args.cc_dir, dev_count=args.dev_count)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(data, ensure_ascii=False, indent=2),
                              encoding='utf-8')
    print(f"wrote {args.out}: {len(data['episodes'])} episodes "
          f"(dev={sum(e['split'] == 'dev' for e in data['episodes'])}, "
          f"test={sum(e['split'] == 'test' for e in data['episodes'])})")
    missing = [e['id'] for e in data['episodes']
               if not os.path.exists(e['video'])]
    if missing:
        print(f'warning: missing videos: {missing}')


if __name__ == '__main__':
    main()
