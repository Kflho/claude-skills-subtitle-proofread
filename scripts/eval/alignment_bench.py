#!/usr/bin/env python3
"""Structural benchmark for the P2 cue mapper (no audio, no models).

Why this exists: the alignment mapper can be wrong in ways that have
nothing to do with ASR quality — duplicate lines, unsorted dialogue,
overlapping cues, long merged segments.  Those mechanics can be measured
on any real subtitle file by *deriving* synthetic ASR from the cues
themselves, where the expected mapping is known exactly.

Scenarios (all derived from the same cue list):

    token    one 1–2-part token per cue, ±50 ms jitter
    segment  groups of 3 cues fused into one ASR segment (Whisper-style)
    lossy    10 % cues dropped, 10 % corrupted, one hallucinated segment
    drift    token scenario with every ASR time shifted by +0.8 s

Examples:
    python scripts\\eval\\alignment_bench.py --srt ep.srt
    python scripts\\eval\\alignment_bench.py --srt ep.ass --scenario token drift
    python scripts\\eval\\alignment_bench.py --srt ep.srt --limit 100 --json out.json

The numbers are mapper mechanics, not ASR accuracy; real CER/WER still
needs `scripts/eval/asr_eval.py` on audio.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from lib.alignment import strip_tags, map_cues, summarize  # noqa: E402
from lib.asr.base import ASRResult, Token  # noqa: E402
from lib.subtitle_io import read_subtitles  # noqa: E402

SCENARIOS = {
    'token': {'jitter': 0.05},
    'segment': {'jitter': 0.0, 'merge': 3},
    'lossy': {'jitter': 0.05, 'drop': 0.1, 'corrupt_ratio': 0.1, 'seed': 11},
    'drift': {'jitter': 0.05, 'drift': 0.8},
}

_DIALOGUE_RE = re.compile(r'[\u3040-\u30ff\u4e00-\u9fff\u0400-\u04ff]')


def load_cues(path: str) -> list[dict]:
    cues = read_subtitles(path, mark_garbled=False)
    out = []
    for cue in cues:
        text = strip_tags(cue.get('text', '')).strip()
        if not _DIALOGUE_RE.search(text):
            continue                     # signs / vector art: not speech
        if cue.get('end_s', 0) <= cue.get('start_s', 0):
            continue
        out.append(cue)
    return out


def split_text(text: str) -> list[str]:
    text = text.strip()
    if len(text) < 4:
        return [text]
    cut = len(text) // 2
    return [text[:cut], text[cut:]]


def corrupt(text: str, rng: random.Random) -> str:
    chars = list(text)
    for _ in range(max(1, len(chars) // 5)):
        chars[rng.randrange(len(chars))] = rng.choice('あいうえおかきくけこ')
    return ''.join(chars)


def build_synthetic(cues, *, jitter=0.05, drift=0.0, drop=0.0,
                    corrupt_ratio=0.0, merge=1, seed=7):
    """Return (tokens, expected) with expected[cue_index] = {unit indices}."""
    rng = random.Random(seed)
    ordered = sorted(range(len(cues)), key=lambda i: (cues[i]['start_s'], i))

    per_cue = []
    for index in ordered:
        cue = cues[index]
        start = cue['start_s'] + drift
        end = cue['end_s'] + drift
        if drop and rng.random() < drop:
            continue
        text = strip_tags(cue['text']).strip()
        if corrupt_ratio and rng.random() < corrupt_ratio:
            text = corrupt(text, rng)
        pieces = []
        parts = split_text(text)
        span = end - start
        for i, part in enumerate(parts):
            part_start = start + span * i / len(parts)
            part_end = start + span * (i + 1) / len(parts)
            if jitter:
                part_start += rng.uniform(-jitter, jitter)
                part_end += rng.uniform(-jitter, jitter)
            if part_end <= part_start:
                part_end = part_start + 0.01
            pieces.append((part, part_start, part_end))
        per_cue.append((index, pieces))

    records = []                    # (cue_index, part_index, text, start, end)
    if merge <= 1:
        for index, pieces in per_cue:
            for part_index, (text, start, end) in enumerate(pieces):
                records.append((index, part_index, text, start, end))
    else:
        for group_start in range(0, len(per_cue), merge):
            group = per_cue[group_start:group_start + merge]
            flat = [p for _, pieces in group for p in pieces]
            records.append((group[0][0], 0,
                            ''.join(p[0] for p in flat),
                            min(p[1] for p in flat),
                            max(p[2] for p in flat)))

    records.sort(key=lambda r: (r[3], r[0], r[1]))
    expected: dict[int, set[int]] = {}
    for unit, (cue_index, _part, _text, _start, _end) in enumerate(records):
        expected.setdefault(cue_index, set()).add(unit)

    tokens = [(r[2], r[3], r[4]) for r in records]
    if corrupt_ratio:
        tokens.append(('ああああ', 600.0, 601.0))   # hallucinated segment
        tokens.sort(key=lambda t: t[1])
    return tokens, expected


def run_scenario(name: str, cues, expected, tokens) -> dict:
    result = ASRResult(
        text=''.join(t[0] for t in tokens),
        tokens=[Token(t, s, e) for t, s, e in tokens],
        engine='synthetic', audio_duration=1000.0)
    alignments = map_cues(cues, result)
    stats = summarize(alignments)

    checked = recovered = 0
    for al in alignments:
        units = expected.get(al.cue_index)
        if not units:
            continue
        checked += 1
        if al.asr_span and any(u in units for u in range(*al.asr_span)):
            recovered += 1

    ordered = sorted(alignments, key=lambda a: (a.cue_start, a.cue_index))
    violations = 0
    for a, b in zip(ordered, ordered[1:]):
        if (a.char_span and b.char_span
                and b.cue_start >= a.cue_end - 1e-3
                and a.char_span[1] > b.char_span[0]):
            violations += 1

    start = stats['boundary']['start']
    end = stats['boundary']['end']
    return {
        'scenario': name,
        'cues': len(cues),
        'units': len(tokens),
        'status': stats['status'],
        'actions': stats['actions'],
        'clean_ratio': stats['clean_ratio'],
        'p50_start_ms': start['p50_ms'],
        'p50_end_ms': end['p50_ms'],
        'p90_start_ms': start['p90_ms'],
        'p90_end_ms': end['p90_ms'],
        'hit100_start': start['hit_100'],
        'hit100_end': end['hit_100'],
        'recovery': round(recovered / checked, 3) if checked else 0.0,
        'monotonic_violations': violations,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='P2 cue-mapper structural benchmark (synthetic ASR)')
    parser.add_argument('--srt', required=True,
                        help='real subtitle file (SRT or ASS)')
    parser.add_argument('--scenario', nargs='+', default=list(SCENARIOS),
                        choices=list(SCENARIOS))
    parser.add_argument('--limit', type=int, default=0,
                        help='use only the first N dialogue cues')
    parser.add_argument('--json', default='', help='write results as JSON')
    args = parser.parse_args(argv)

    path = Path(args.srt)
    if not path.exists():
        print(f'[bench] subtitle not found: {path}', file=sys.stderr)
        return 2
    cues = load_cues(str(path))
    if args.limit:
        cues = cues[:args.limit]
    if not cues:
        print('[bench] no dialogue cues found', file=sys.stderr)
        return 2

    print(f'# alignment bench: {path.name} — {len(cues)} dialogue cues\n')
    print('| scenario | match | P50 起/止 (ms) | P90 起/止 (ms) | '
          '±100ms 起/止 | recovery | mono |')
    print('|---|---:|---:|---:|---:|---:|---:|')
    results = []
    for name in args.scenario:
        tokens, expected = build_synthetic(cues, **SCENARIOS[name])
        row = run_scenario(name, cues, expected, tokens)
        results.append(row)
        match = row['status'].get('match', 0)
        print(f"| {name} | {match}/{row['cues']} | "
              f"{row['p50_start_ms']} / {row['p50_end_ms']} | "
              f"{row['p90_start_ms']} / {row['p90_end_ms']} | "
              f"{row['hit100_start']:.1%} / {row['hit100_end']:.1%} | "
              f"{row['recovery']:.1%} | {row['monotonic_violations']} |")

    print('\n> synthetic ASR derived from the cues; this measures mapper '
          'mechanics, not ASR accuracy.')
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(
            json.dumps({'srt': str(path), 'cues': len(cues),
                        'results': results}, ensure_ascii=False, indent=2),
            encoding='utf-8')
        print(f'\n[bench] wrote {args.json}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
