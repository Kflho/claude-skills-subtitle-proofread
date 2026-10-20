#!/usr/bin/env python3
"""Language-agnostic retiming for subtitles whose text is already correct.

NOTE (2026-10-21): keep as the "no ASR available" fallback only.  On
dialogue-dense material the speech-activity score is nearly flat, so the
estimated global shift is not trustworthy; prefer ``asr/retime_crosslingual.py``
when an ASR of the audio exists.  Notes: references/design/crosslingual-retime.md

Why this exists: translated subtitles cannot be matched to source-language ASR
by text (English subs vs Japanese audio gives 0 clean cues).  This tool only
uses **speech segments and cue order**:

1. estimate a global offset by matching cue starts to speech onsets;
2. optionally snap cue boundaries to nearby speech starts/ends;
3. export to a NEW file; originals are never modified.

Usage:
    python scripts/asr/retime_sequence.py \
      --srt "translated/zh/EP034.srt" --video "P34.mp4" \
      --output-dir "retimed" --dry-run

    # no audio needed if speech segments / ASR JSON exist
    python scripts/asr/retime_sequence.py \
      --srt ep.srt --asr-json ep.asr.json --output-dir retimed

Text is never replaced.  The report lists estimated offset, before/after
speech coverage and changed cue count.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from lib.subtitle_io import read_subtitles, write_subtitles  # noqa: E402


def _speech_from_asr(path: Path) -> list[tuple[float, float]]:
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if isinstance(data, dict):
        segments = data.get('segments') or data.get('transcription') or []
    else:
        segments = data
    out = []
    for item in segments:
        start = item.get('start', item.get('start_s'))
        end = item.get('end', item.get('end_s'))
        if start is None or end is None:
            offsets = item.get('offsets') or {}
            start = offsets.get('from', 0) / 1000
            end = offsets.get('to', 0) / 1000
        if end > start:
            out.append((float(start), float(end)))
    return sorted(out)


def _speech_from_video(path: Path, noise: str = '-30dB',
                       min_silence: float = 0.3) -> list[tuple[float, float]]:
    command = ['ffmpeg', '-hide_banner', '-nostats', '-i', str(path),
               '-af', f'silencedetect=noise={noise}:d={min_silence}',
               '-f', 'null', '-']
    result = subprocess.run(command, capture_output=True, text=True,
                            encoding='utf-8', errors='replace')
    silences = []
    start = None
    for line in result.stderr.splitlines():
        match = re.search(r'silence_start: ([\d.]+)', line)
        if match:
            start = float(match.group(1))
        match = re.search(r'silence_end: ([\d.]+)', line)
        if match and start is not None:
            silences.append((start, float(match.group(1))))
            start = None
    duration = 0.0
    match = re.search(r'Duration: (\d+):(\d+):([\d.]+)', result.stderr)
    if match:
        duration = (int(match.group(1)) * 3600 + int(match.group(2)) * 60
                    + float(match.group(3)))
    segments = []
    cursor = 0.0
    for begin, end in silences:
        if begin > cursor:
            segments.append((cursor, begin))
        cursor = end
    if duration > cursor:
        segments.append((cursor, duration))
    return segments


def _cues_from_srt(path: Path) -> list[dict]:
    return read_subtitles(str(path))


def _nearest(value: float, points: list[float]) -> float:
    if not points:
        return 0.0
    return min(abs(value - point) for point in points)


def _score(cues: list[dict], speech: list[tuple[float, float]],
           offset: float = 0.0) -> tuple[float, float]:
    """Return (fraction of cues overlapping speech, mean coverage)."""
    if not cues:
        return 0.0, 0.0
    hit = 0
    total = 0.0
    for cue in cues:
        start = cue['start_s'] + offset
        end = cue['end_s'] + offset
        if end <= start:
            continue
        best = 0.0
        for seg_start, seg_end in speech:
            left = max(start, seg_start)
            right = min(end, seg_end)
            if right > left:
                best = max(best, (right - left) / (end - start))
        total += best
        if best >= 0.4:
            hit += 1
    return hit / len(cues), total / len(cues)


def _utterances(speech: list[tuple[float, float]],
                gap: float = 0.5) -> list[tuple[float, float]]:
    """Merge ASR fragments separated by short pauses into utterances."""
    if not speech:
        return []
    merged = [list(speech[0])]
    for start, end in speech[1:]:
        if start - merged[-1][1] <= gap:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def _boundary_error(cues: list[dict], speech: list[tuple[float, float]],
                    offset: float) -> float:
    utterances = _utterances(speech)
    onsets = [start for start, _ in utterances]
    ends = [end for _, end in utterances]
    distances = []
    for cue in cues:
        if cue['start_s'] < 20 or cue['end_s'] - cue['start_s'] > 8:
            continue
        start = cue['start_s'] + offset
        end = cue['end_s'] + offset
        if onsets:
            distances.append(min(abs(start - point) for point in onsets))
        if ends:
            distances.append(min(abs(end - point) for point in ends))
    if not distances:
        return 0.0
    distances.sort()
    return distances[len(distances) // 2]


def estimate_offset(cues: list[dict], speech: list[tuple[float, float]],
                    max_shift: float = 30.0,
                    step: float = 0.1,
                    min_improvement: float = 5.0) -> dict:
    """Boundary-distance offset with a confidence guard.

    ASR utterance edges are not exact cue edges, so a small "best" shift is
    noise.  Only report a shift when it beats offset 0 by ``min_improvement``.
    """
    utterances = _utterances(speech)
    if not utterances:
        return {'offset': 0.0, 'confidence': 0.0, 'error_at_0': 0.0,
                'coverage': 0.0, 'utterances': 0}
    base_error = _boundary_error(cues, speech, 0.0)
    best_offset, best_error = 0.0, base_error
    limit = int(round(max_shift / step))
    for index in range(-limit, limit + 1):
        offset = round(index * step, 3)
        error = _boundary_error(cues, speech, offset)
        if error < best_error:
            best_offset, best_error = offset, error
    confidence = base_error - best_error
    offset = best_offset if confidence >= min_improvement else 0.0
    coverage, _ = _score(cues, speech, offset)
    return {'offset': offset, 'confidence': round(confidence, 3),
            'error_at_0': round(base_error, 3),
            'error_at_best': round(best_error, 3),
            'coverage': round(coverage, 3),
            'utterances': len(utterances)}


def snap_cues(cues: list[dict], speech: list[tuple[float, float]],
              offset: float, max_snap: float = 0.8) -> list[dict]:
    onsets = [start for start, _ in speech]
    ends = [end for _, end in speech]
    out = []
    for cue in cues:
        start = max(0.0, cue['start_s'] + offset)
        end = max(start + 0.3, cue['end_s'] + offset)
        if onsets:
            nearest_start = min(onsets, key=lambda point: abs(point - start))
            if abs(nearest_start - start) <= max_snap:
                start = nearest_start
        if ends:
            nearest_end = min(ends, key=lambda point: abs(point - end))
            if abs(nearest_end - end) <= max_snap and nearest_end > start:
                end = nearest_end
        out.append({**cue, 'start_s': round(start, 3), 'end_s': round(end, 3)})
    for index in range(1, len(out)):
        if out[index]['start_s'] < out[index - 1]['end_s']:
            out[index]['start_s'] = out[index - 1]['end_s']
        if out[index]['end_s'] <= out[index]['start_s']:
            out[index]['end_s'] = out[index]['start_s'] + 0.3
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--srt', required=True)
    parser.add_argument('--video', default='')
    parser.add_argument('--audio', default='')
    parser.add_argument('--asr-json', default='')
    parser.add_argument('--output-dir', default='retimed')
    parser.add_argument('--suffix', default='.retimed.srt')
    parser.add_argument('--max-shift', type=float, default=30.0)
    parser.add_argument('--snap', action='store_true',
                        help='snap boundaries to nearby speech edges')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--report', default='')
    args = parser.parse_args(argv)

    source = Path(args.srt)
    if not source.is_file():
        print(f'error: missing {source}', file=sys.stderr)
        return 2
    cues = _cues_from_srt(source)
    if args.asr_json:
        speech = _speech_from_asr(Path(args.asr_json))
    elif args.video or args.audio:
        speech = _speech_from_video(Path(args.video or args.audio))
    else:
        print('error: need --asr-json, --video or --audio', file=sys.stderr)
        return 2
    if not speech:
        print('error: no speech segments', file=sys.stderr)
        return 2

    before = _score(cues, speech)
    estimate = estimate_offset(cues, speech, args.max_shift)
    shifted = [{**cue, 'start_s': max(0.0, cue['start_s'] + estimate['offset']),
                'end_s': max(0.3, cue['end_s'] + estimate['offset'])}
               for cue in cues]
    if args.snap:
        shifted = snap_cues(cues, speech, estimate['offset'])
    after = _score(shifted, speech)
    changed = sum(1 for old, new in zip(cues, shifted)
                  if abs(old['start_s'] - new['start_s']) > 0.001
                  or abs(old['end_s'] - new['end_s']) > 0.001)
    report = {
        'source': str(source),
        'cues': len(cues),
        'speech_segments': len(speech),
        'before': {'hit': round(before[0], 3),
                   'coverage': round(before[1], 3)},
        'after': {'hit': round(after[0], 3),
                  'coverage': round(after[1], 3)},
        'estimate': estimate,
        'changed_cues': changed,
        'snap': bool(args.snap),
        'output': None,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8', newline='\n')
    if args.dry_run:
        return 0
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / (source.stem + args.suffix)
    if target.exists():
        print(f'error: refusing to overwrite {target}', file=sys.stderr)
        return 2
    write_subtitles(str(target), shifted)
    report['output'] = str(target)
    if args.report:
        Path(args.report).write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8', newline='\n')
    print(f'exported {target}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
