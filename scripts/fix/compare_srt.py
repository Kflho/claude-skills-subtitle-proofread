#!/usr/bin/env python3
"""SRT comparison tool — align Whisper output with translated reference subtitles.

Matches cues by timecode overlap, computes text similarity, and flags mismatches
for human review.

Usage:
  python compare_srt.py whisper_output.srt translated_reference.srt
  python compare_srt.py whisper_output.srt translated_reference.srt --output diff.json
  python compare_srt.py whisper_output.srt translated_reference.srt --threshold 0.5
"""

import argparse
import json
import os
import sys
from difflib import SequenceMatcher

import lib._path  # noqa: F401

# ═══════════════════════════════════════════════════════════════
# SRT parsing
# ═══════════════════════════════════════════════════════════════

# parse_srt: use lib.subtitle_io.read_subtitles or lib.whisper_utils.parse_srt directly


# ═══════════════════════════════════════════════════════════════
# Comparison
# ═══════════════════════════════════════════════════════════════

def similarity(a, b):
    """Text similarity ratio (0-1)."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def time_overlap(a, b):
    """Compute overlap ratio between two time ranges."""
    overlap_start = max(a['start_s'], b['start_s'])
    overlap_end = min(a['end_s'], b['end_s'])

    if overlap_start >= overlap_end:
        return 0.0

    overlap_dur = overlap_end - overlap_start
    a_dur = a['end_s'] - a['start_s']
    return overlap_dur / max(a_dur, 0.001)


def compare(whisper_cues, ref_cues, threshold=0.4):
    """Align and compare two SRT cue lists.

    Returns list of {
        'start': str,
        'whisper': str,
        'reference': str,
        'similarity': float,
        'verdict': 'match' | 'suspicious' | 'mismatch',
    }
    """
    results = []

    # Build time-indexed lookup for reference cues
    ref_by_time = sorted(ref_cues, key=lambda c: c['start_s'])

    for wc in whisper_cues:
        # Find reference cues with time overlap
        candidates = []
        for rc in ref_by_time:
            overlap = time_overlap(wc, rc)
            if overlap > 0:
                candidates.append((overlap, rc))
            elif rc['start_s'] > wc['end_s'] + 5:
                break  # past relevant range

        if not candidates:
            # No overlapping reference cue — Whisper-only
            results.append({
                'start': wc['start'],
                'whisper': wc['text'],
                'reference': '(no match)',
                'similarity': 0.0,
                'verdict': 'mismatch',
            })
            continue

        # Best match by overlap
        best = max(candidates, key=lambda x: x[0])
        rc = best[1]

        sim = similarity(wc['text'], rc['text'])

        # Verdict
        if sim >= threshold + 0.2:
            verdict = 'match'
        elif sim >= threshold:
            verdict = 'suspicious'
        else:
            verdict = 'mismatch'

        results.append({
            'start': wc['start'],
            'whisper': wc['text'],
            'reference': rc['text'],
            'similarity': round(sim, 3),
            'verdict': verdict,
        })

    return results


# ═══════════════════════════════════════════════════════════════
# Output
# ═══════════════════════════════════════════════════════════════

def print_report(diffs):
    """Print human-readable comparison report."""
    match = sum(1 for d in diffs if d['verdict'] == 'match')
    suspicious = sum(1 for d in diffs if d['verdict'] == 'suspicious')
    mismatch = sum(1 for d in diffs if d['verdict'] == 'mismatch')

    print(f'\n=== Comparison Report ===')
    print(f'  Total: {len(diffs)} cues')
    print(f'  Match:      {match}')
    print(f'  Suspicious: {suspicious}')
    print(f'  Mismatch:   {mismatch}')
    print()

    if suspicious + mismatch > 0:
        print('--- Needs Review ---')
        for d in diffs:
            if d['verdict'] in ('suspicious', 'mismatch'):
                tag = '[?]' if d['verdict'] == 'suspicious' else '[X]'
                print(f'  {tag} {d["start"]}')
                print(f'     Whisper:   {d["whisper"][:80]}')
                print(f'     Reference: {d["reference"][:80]}')
                print(f'     Similarity: {d["similarity"]}')
                print()


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description='Compare Whisper output with translated reference subtitles'
    )
    parser.add_argument('whisper_srt', help='Whisper output SRT')
    parser.add_argument('reference_srt', help='Translated reference SRT')
    parser.add_argument('--output', '-o', help='Output JSON path for differences')
    parser.add_argument('--threshold', '-t', type=float, default=0.4,
                        help='Similarity threshold for suspicious (default: 0.4)')
    args = parser.parse_args()

    for path in [args.whisper_srt, args.reference_srt]:
        if not os.path.exists(path):
            print(f'ERROR: File not found: {path}', file=sys.stderr)
            sys.exit(1)

    print(f'Whisper:   {args.whisper_srt}', file=sys.stderr)
    print(f'Reference: {args.reference_srt}', file=sys.stderr)

    from lib.whisper_utils import parse_srt as _parse
    whisper_cues = _parse(args.whisper_srt)
    ref_cues = _parse(args.reference_srt)
    print(f'  Cues: {len(whisper_cues)} (Whisper) vs {len(ref_cues)} (Ref)', file=sys.stderr)

    diffs = compare(whisper_cues, ref_cues, threshold=args.threshold)
    print_report(diffs)

    if args.output:
        os.makedirs(os.path.dirname(args.output) if os.path.dirname(args.output) else '.', exist_ok=True)
        output_data = {
            'whisper_file': args.whisper_srt,
            'reference_file': args.reference_srt,
            'threshold': args.threshold,
            'total': len(diffs),
            'match': sum(1 for d in diffs if d['verdict'] == 'match'),
            'suspicious': sum(1 for d in diffs if d['verdict'] == 'suspicious'),
            'mismatch': sum(1 for d in diffs if d['verdict'] == 'mismatch'),
            'differences': diffs,
        }
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        print(f'\n→ {args.output}', file=sys.stderr)


if __name__ == '__main__':
    main()
