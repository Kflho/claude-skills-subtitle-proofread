#!/usr/bin/env python3
"""Retime existing subtitles against ASR of a *different* language.

Problem this solves: the subtitle text is a human translation (e.g. English
fansub), the audio is Japanese, and the timing is unusable — text matching
gives 0 % similarity and speech-activity matching is ambiguous.  The bridge is
an LLM that reads both ordered transcripts and pairs lines by meaning; this
module executes and validates that decision.

Pipeline:
  1. ``build_anchors`` — ask the LLM for a monotone cue -> ASR-line mapping.
  2. ``filter_anchors`` — drop outliers that disagree with the local median.
  3. ``Warp`` — piecewise-linear delta(t) through the anchors (median smoothed).
  4. ``apply_warp`` — shift only the timecodes, then enforce monotonicity,
     minimum duration and the cue-count/text invariants.

Text is never modified and the output always goes to a new file.

Usage:
    # 1) run the LLM alignment and write a retimed copy
    python scripts/asr/retime_crosslingual.py \
        --source-srt "EN/EP001.srt" --asr-srt "temp/asr/P01.srt" \
        --target-srt "zh/EP001.srt" --output "zh_retimed/EP001.srt" \
        --report "temp/retime/EP001.json"

    # 2) dry run (no file written)
    python scripts/asr/retime_crosslingual.py ... --dry-run

    # 3) reuse an existing anchors JSON (no LLM call)
    python scripts/asr/retime_crosslingual.py ... --anchors temp/retime/EP001.anchors.json

Exit codes: 0 ok, 2 bad input, 3 LLM failure, 4 validation refusal.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from lib.subtitle_io import format_tc, read_subtitles, write_subtitles  # noqa: E402

MIN_COVERAGE = 0.35


def read_subtitles_robust(path):
    """Read a subtitle, surviving broken UTF-8 (whisper.cpp can split glyphs).

    ``read_subtitles`` falls back to cp1251 when a UTF-8 file has invalid
    bytes, which silently turns Japanese text into mojibake and destroys the
    anchors.  Here the file is decoded as UTF-8 with U+FFFD replacement instead
    and a warning is printed, so the rest of the lines stay usable.
    """
    import os
    import tempfile

    target = Path(path)
    raw = target.read_bytes()
    try:
        raw.decode('utf-8-sig')
        return read_subtitles(str(target))
    except UnicodeDecodeError:
        text = raw.decode('utf-8', 'replace').lstrip('\ufeff')
        broken = text.count('\ufffd')
        print(f'warn: {target.name} has invalid UTF-8; {broken} byte(s) replaced',
              file=sys.stderr)
        handle, temp_name = tempfile.mkstemp(suffix='.srt')
        os.close(handle)
        try:
            # newline='' keeps the original CRLF instead of doubling the CR.
            with open(temp_name, 'w', encoding='utf-8', newline='') as fh:
                fh.write(text)
            return read_subtitles(temp_name)
        finally:
            os.unlink(temp_name)

SYSTEM = (
    'You align two transcripts of the same episode: (A) subtitles that are a '
    'human translation, (B) noisy ASR in the audio language. Times are '
    'unreliable and differ between the two; text is the only signal. Pair only '
    'lines that say the same thing. Reply with JSON only.'
)

PROMPT = '''Below are two ordered lists from the same episode.

A = subtitle lines (cue index, start time, text)
B = ASR lines (asr index, start time, text) — may contain recognition errors, may miss lines, may invent lines

For every A line, give the B index that says the SAME thing. Rules:
- Use meaning, not literal wording; the languages differ.
- If several B lines make up one A line, give the first one.
- If no B line matches, use null. Do not guess to fill gaps.
- Keep the mapping monotone: as A index increases, B index must not decrease.
- ASR may garble names/places; judge the overall meaning.

Reply with a JSON array of %d entries, each {"a": <A index>, "b": <B index or null>}.
No prose outside the JSON.

A:
%s

B:
%s
'''


# --------------------------------------------------------------------------
# anchors
# --------------------------------------------------------------------------
def format_lines(items, ids, time_key='start_s') -> str:
    return '\n'.join(
        f"{i}: [{items[i][time_key]:.1f}] "
        f"{items[i]['text'].replace(chr(10), ' ').strip()}"
        for i in ids)


def build_prompt(cues, rows, cue_ids, asr_ids) -> str:
    return PROMPT % (len(cue_ids), format_lines(cues, cue_ids),
                     format_lines(rows, asr_ids))


def pairs_to_anchors(pairs, cues, rows):
    """``{cue_id: asr_id|None}`` -> anchor dicts with start deltas."""
    anchors = []
    for cue_id, asr_id in sorted(pairs.items()):
        if asr_id is None:
            continue
        if not (0 <= cue_id < len(cues)) or not (0 <= asr_id < len(rows)):
            continue
        cue_start = cues[cue_id]['start_s']
        anchors.append({
            'cue': cue_id, 'asr': asr_id,
            'cue_start': round(cue_start, 3),
            'asr_start': round(rows[asr_id]['start_s'], 3),
            'asr_end': round(rows[asr_id]['end_s'], 3),
            'asr_len': round(rows[asr_id]['end_s'] - rows[asr_id]['start_s'], 2),
            'delta': round(rows[asr_id]['start_s'] - cue_start, 3),
        })
    return anchors


def parse_pairs(raw: str, cue_ids, asr_ids) -> dict:
    """Parse the model reply into a validated, order-respecting mapping."""
    from lib.llm import extract_json_array
    items = extract_json_array(raw)
    if items is None:
        raise ValueError('no JSON array in reply')
    allowed_cues = set(cue_ids)
    allowed_asr = set(asr_ids)
    pairs = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        a, b = item.get('a'), item.get('b')
        try:
            a = int(a)
        except (TypeError, ValueError):
            continue
        if a not in allowed_cues:
            continue
        if b is None:
            pairs[a] = None
            continue
        try:
            b = int(b)
        except (TypeError, ValueError):
            pairs[a] = None
            continue
        pairs[a] = b if b in allowed_asr else None
    return pairs


def drop_nonmonotone(anchors):
    """Keep the longest ASR-index-increasing chain (by anchor order)."""
    if not anchors:
        return []
    n = len(anchors)
    best = [1] * n
    prev = [-1] * n
    for i in range(n):
        for j in range(i):
            if anchors[j]['asr'] < anchors[i]['asr'] and best[j] + 1 > best[i]:
                best[i] = best[j] + 1
                prev[i] = j
    end = max(range(n), key=lambda i: best[i])
    chain = []
    while end >= 0:
        chain.append(anchors[end])
        end = prev[end]
    return chain[::-1]


def filter_anchors(anchors, window=5, tol=8.0):
    """Drop anchors whose delta disagrees with the local median delta."""
    if len(anchors) < 3:
        return list(anchors)
    deltas = [a['delta'] for a in anchors]
    kept = []
    for i, anchor in enumerate(anchors):
        lo, hi = max(0, i - window), min(len(anchors), i + window + 1)
        local = sorted(deltas[lo:hi])
        if abs(anchor['delta'] - local[len(local) // 2]) <= tol:
            kept.append(anchor)
    return kept


def prepare_anchors(anchors, window=5, tol=8.0, baseline='auto', lead=1.0):
    """Full anchor hygiene: monotone chain + local-median outlier rejection.

    ``baseline='auto'`` (default) treats a small episode-wide delta as the
    fansub's intentional early lead (typically -0.2..-0.5 s): it is subtracted
    so only structural offsets move.  A larger global delta (``|median| >
    lead``) is a real offset and is kept.  ``'median'`` always subtracts,
    ``'zero'`` never does.
    """
    clean = filter_anchors(drop_nonmonotone(anchors), window=window, tol=tol)
    if not clean:
        return clean
    shift = statistics.median(a['delta'] for a in clean)
    if baseline == 'zero':
        shift = 0.0
    elif baseline == 'auto' and abs(shift) > lead:
        shift = 0.0
    return [{**a, 'delta': round(a['delta'] - shift, 3),
             'baseline': round(shift, 3)} for a in clean]


# --------------------------------------------------------------------------
# warp
# --------------------------------------------------------------------------
class Warp:
    """Piecewise-linear delta(t); constant before the first / after the last point."""

    def __init__(self, anchors, smooth=2):
        points = sorted((a['cue_start'], a['delta']) for a in anchors)
        self.times = [p[0] for p in points] or [0.0]
        self.deltas = [p[1] for p in points] or [0.0]
        if smooth and len(self.deltas) >= 3:
            smoothed = []
            for i in range(len(self.deltas)):
                lo, hi = max(0, i - smooth), min(len(self.deltas), i + smooth + 1)
                seg = sorted(self.deltas[lo:hi])
                smoothed.append(seg[len(seg) // 2])
            self.deltas = smoothed

    def __call__(self, t: float) -> float:
        times, deltas = self.times, self.deltas
        if t <= times[0]:
            return deltas[0]
        if t >= times[-1]:
            return deltas[-1]
        lo, hi = 0, len(times) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if times[mid] <= t:
                lo = mid
            else:
                hi = mid
        span = times[hi] - times[lo]
        if span <= 0:
            return deltas[lo]
        ratio = (t - times[lo]) / span
        return deltas[lo] + ratio * (deltas[hi] - deltas[lo])


def apply_warp(cues, warp, min_dur=0.4, min_gap=0.04):
    """Shift cue times through ``warp``; keep text and cue count untouched.

    Both the float fields (``start_s``/``end_s``) and the SRT timecode strings
    (``start``/``end``) are updated: ``write_subtitles`` writes the strings.
    """
    out = []
    for cue in cues:
        start = max(0.0, cue['start_s'] + warp(cue['start_s']))
        end = cue['end_s'] + warp(cue['end_s'])
        if end - start < min_dur:
            end = start + min_dur
        out.append({**cue, 'start_s': round(start, 3), 'end_s': round(end, 3),
                    'start': format_tc(round(start, 3)),
                    'end': format_tc(round(end, 3))})
    for i in range(1, len(out)):
        floor = out[i - 1]['end_s'] + min_gap
        if out[i]['start_s'] < floor:
            shift = floor - out[i]['start_s']
            out[i]['start_s'] = round(floor, 3)
            out[i]['end_s'] = round(max(out[i]['end_s'] + shift,
                                        out[i]['start_s'] + min_dur), 3)
            out[i]['start'] = format_tc(out[i]['start_s'])
            out[i]['end'] = format_tc(out[i]['end_s'])
    return out


# --------------------------------------------------------------------------
# diagnostics
# --------------------------------------------------------------------------
def summarize(errors):
    if not errors:
        return {'n': 0}
    errors = sorted(errors)
    idx = min(len(errors) - 1, max(0, int(len(errors) * 0.9) - 1))
    return {'n': len(errors), 'p50': round(errors[len(errors) // 2], 3),
            'p90': round(errors[idx], 3),
            'mean': round(sum(errors) / len(errors), 3),
            'max': round(errors[-1], 3)}


def holdout_error(anchors):
    """Fit on even-indexed anchors, predict odd-indexed ones."""
    even = [a for k, a in enumerate(anchors) if k % 2 == 0]
    odd = [a for k, a in enumerate(anchors) if k % 2 == 1]
    if len(even) < 3 or not odd:
        return {'n': 0}
    model = Warp(even)
    return summarize([abs(a['asr_start'] - (a['cue_start'] + model(a['cue_start'])))
                      for a in odd])


def local_medians(anchors, bucket=180.0, min_count=3):
    buckets = {}
    for anchor in anchors:
        buckets.setdefault(int(anchor['cue_start'] // bucket), []).append(anchor['delta'])
    out = []
    for key in sorted(buckets):
        values = sorted(buckets[key])
        if len(values) < min_count:
            continue
        out.append({'t': round(key * bucket, 1), 'n': len(values),
                    'median': round(values[len(values) // 2], 2)})
    return out


# --------------------------------------------------------------------------
# LLM alignment driver
# --------------------------------------------------------------------------
def align_with_llm(cues, rows, window=40, pad=90.0, chat=None, verbose=True):
    """Align in windows; ``chat(messages)`` returns the raw reply text."""
    if chat is None:
        from lib.llm import call_chat

        def chat(messages):
            return call_chat(messages, temperature=0, max_tokens=4000,
                             timeout=300, retries=2)

    anchors = []
    for start in range(0, len(cues), window):
        cue_ids = list(range(start, min(start + window, len(cues))))
        lo = cues[cue_ids[0]]['start_s'] - pad
        hi = cues[cue_ids[-1]]['end_s'] + pad
        asr_ids = [j for j, row in enumerate(rows)
                   if row['end_s'] >= lo and row['start_s'] <= hi]
        if not asr_ids:
            continue
        reply = chat([{'role': 'system', 'content': SYSTEM},
                      {'role': 'user', 'content': build_prompt(cues, rows, cue_ids, asr_ids)}])
        if not reply:
            raise RuntimeError(f'LLM returned nothing for cues {cue_ids[0]}-{cue_ids[-1]}')
        pairs = parse_pairs(reply, cue_ids, asr_ids)
        anchors += pairs_to_anchors(pairs, cues, rows)
        if verbose:
            print(f'  [align] cues {cue_ids[0] + 1}-{cue_ids[-1] + 1}: '
                  f'{len(anchors)} anchors', file=sys.stderr)
    return anchors


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--source-srt', required=True,
                        help='subtitle in the translation language (text is the anchor)')
    parser.add_argument('--asr-srt', required=True,
                        help='ASR of the audio, with usable timestamps')
    parser.add_argument('--target-srt', default='',
                        help='the subtitle to retime (default: --source-srt)')
    parser.add_argument('--output', default='', help='new file; never overwrites')
    parser.add_argument('--report', default='')
    parser.add_argument('--anchors', default='', help='reuse anchors JSON (no LLM)')
    parser.add_argument('--window', type=int, default=40)
    parser.add_argument('--pad', type=float, default=90.0)
    parser.add_argument('--smooth', type=int, default=2)
    parser.add_argument('--baseline', choices=('auto', 'median', 'zero'),
                        default='auto',
                        help='small global delta = fansub lead (auto: subtract if <=1s)')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)

    source = Path(args.source_srt)
    asr_path = Path(args.asr_srt)
    target = Path(args.target_srt) if args.target_srt else source
    for path in (source, asr_path, target):
        if not path.is_file():
            print(f'error: missing {path}', file=sys.stderr)
            return 2

    cues = read_subtitles(str(source))
    rows = read_subtitles_robust(asr_path)
    targets = read_subtitles(str(target))
    if len(targets) != len(cues):
        print(f'error: target has {len(targets)} cues but source has {len(cues)}; '
              'only 1:1 files are supported', file=sys.stderr)
        return 4
    if not cues or not rows:
        print('error: empty subtitle or ASR', file=sys.stderr)
        return 2

    try:
        if args.anchors:
            anchors = json.loads(Path(args.anchors).read_text(encoding='utf-8'))
        else:
            anchors = align_with_llm(cues, rows, window=args.window, pad=args.pad)
    except Exception as exc:  # noqa: BLE001
        print(f'error: alignment failed: {exc}', file=sys.stderr)
        return 3

    clean = prepare_anchors(anchors, baseline=args.baseline)
    coverage = len(clean) / max(1, len(cues))
    warp = Warp(clean, smooth=args.smooth)
    result = apply_warp(targets, warp)

    deltas = [a['delta'] for a in clean]
    moved = [abs(a['start_s'] - b['start_s']) for a, b in zip(targets, result)]
    report = {
        'source': str(source), 'asr': str(asr_path), 'target': str(target),
        'cues': len(cues), 'asr_rows': len(rows),
        'anchors_raw': len(anchors), 'anchors_used': len(clean),
        'coverage': round(coverage, 3),
        'baseline': clean[0].get('baseline', 0.0) if clean else 0.0,
        'delta_median': round(statistics.median(deltas), 2) if deltas else None,
        'delta_range': [round(min(deltas), 2), round(max(deltas), 2)] if deltas else [],
        'holdout_error': holdout_error(clean),
        'local_medians': local_medians(clean),
        'moved_cues': sum(1 for d in moved if d > 0.05),
        'moved_p50': round(sorted(moved)[len(moved) // 2], 2) if moved else 0,
        'moved_max': round(max(moved), 2) if moved else 0,
    }
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(text + '\n', encoding='utf-8', newline='\n')
    print(text)

    if coverage < MIN_COVERAGE:
        print(f'error: anchor coverage {coverage:.2f} < {MIN_COVERAGE}; '
              'refusing to rewrite times', file=sys.stderr)
        return 4
    if args.dry_run:
        return 0
    if not args.output:
        print('error: --output is required unless --dry-run', file=sys.stderr)
        return 2
    out_path = Path(args.output)
    if out_path.exists():
        print(f'error: refusing to overwrite {out_path}', file=sys.stderr)
        return 2
    before = [cue['text'] for cue in targets]
    after = [cue['text'] for cue in result]
    if before != after:
        print('error: text changed during retime; aborting', file=sys.stderr)
        return 4
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_subtitles(str(out_path), result)
    print(f'wrote {out_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
