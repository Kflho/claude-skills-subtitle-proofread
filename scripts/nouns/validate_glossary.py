#!/usr/bin/env python3
"""Validate a ja→zh glossary against aligned Japanese CC + Chinese ASS.

This is evidence collection, not translation: a term pair is kept only if
the Japanese term occurs in the CC, the Chinese term occurs in the Chinese
subtitle, and they co-occur in at least one time-aligned cue pair.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lib._path  # noqa: F401,E402
from lib.whisper_utils import parse_subtitles  # noqa: E402

EP_RE = re.compile(r'^(?:EP)?0*(\d{1,3})', re.IGNORECASE)
CC_EP_RE = re.compile(r'S\d+E(\d{1,3})', re.IGNORECASE)


def _episode_number(name: str) -> int | None:
    base = os.path.basename(name)
    match = CC_EP_RE.search(base) or EP_RE.search(base)
    return int(match.group(1)) if match else None


def _best_overlap(ja_cue: dict, zh_cues: list[dict]) -> dict | None:
    best, best_overlap = None, 0.0
    for zh in zh_cues:
        overlap = (min(ja_cue.get('end_s', 0), zh.get('end_s', 0))
                   - max(ja_cue.get('start_s', 0), zh.get('start_s', 0)))
        if overlap > best_overlap:
            best, best_overlap = zh, overlap
    return best if best_overlap > 0.2 else None


def validate(ja_dir: str, zh_dir: str, candidates: dict,
             *, min_pairs: int = 1) -> dict:
    ja_files = {}
    for name in os.listdir(ja_dir):
        if name.lower().endswith('.srt') and 'S01E' in name.upper():
            num = _episode_number(name)
            if num:
                ja_files[num] = os.path.join(ja_dir, name)
    zh_files = {}
    for name in os.listdir(zh_dir):
        if name.lower().endswith('.sc.ass'):
            num = _episode_number(name)
            if num:
                zh_files[num] = os.path.join(zh_dir, name)

    stats = {ja: {'ja': 0, 'zh': 0, 'pairs': 0, 'episodes': set()}
             for ja in candidates}
    total_pairs = 0
    for num in sorted(set(ja_files) & set(zh_files)):
        ja_cues = parse_subtitles(ja_files[num], mark_garbled=False)
        zh_cues = parse_subtitles(zh_files[num], mark_garbled=False)
        zh_all = ' '.join(c.get('text', '') for c in zh_cues)
        for ja, zh in candidates.items():
            if ja in ' '.join(c.get('text', '') for c in ja_cues):
                stats[ja]['ja'] += 1
            if zh in zh_all:
                stats[ja]['zh'] += 1
        for ja_cue in ja_cues:
            zh_cue = _best_overlap(ja_cue, zh_cues)
            if not zh_cue:
                continue
            total_pairs += 1
            for ja, zh in candidates.items():
                if ja in ja_cue.get('text', '') and zh in zh_cue.get('text', ''):
                    stats[ja]['pairs'] += 1
                    stats[ja]['episodes'].add(num)

    mappings, dropped = {}, {}
    for ja, zh in candidates.items():
        s = stats[ja]
        if s['pairs'] >= min_pairs and s['zh'] > 0:
            mappings[ja] = zh
        else:
            dropped[ja] = {'pairs': s['pairs'], 'zh': s['zh'],
                           'episodes': sorted(s['episodes'])}
    return {
        'mappings': mappings,
        'stats': {k: {**v, 'episodes': sorted(v['episodes'])}
                  for k, v in stats.items()},
        'dropped': dropped,
        'episodes_paired': len(set(ja_files) & set(zh_files)),
        'cue_pairs': total_pairs,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ja-dir', required=True)
    parser.add_argument('--zh-dir', required=True)
    parser.add_argument('--candidates', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--min-pairs', type=int, default=1)
    args = parser.parse_args(argv)
    candidates = json.load(open(args.candidates, encoding='utf-8'))
    result = validate(args.ja_dir, args.zh_dir, candidates,
                      min_pairs=args.min_pairs)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                              encoding='utf-8')
    print(f"kept {len(result['mappings'])}/{len(candidates)}, "
          f"dropped {len(result['dropped'])}, "
          f"episodes={result['episodes_paired']}, "
          f"cue_pairs={result['cue_pairs']}")


if __name__ == '__main__':
    main()
