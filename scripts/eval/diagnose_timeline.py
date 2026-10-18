#!/usr/bin/env python3
"""Diagnose an ASR JSON's token/segment calendar (offline, no audio).

This is the reproducible half of the EP01 token-timeline work: it tells you
*where* the timestamps are broken before you blame the cue text.

It reports, per timestamp source:

* zero-duration units and their collapsed runs (time, text, neighbours);
* overlong units (a token stretched over silence/music);
* a quality verdict (``degraded``) and the score used for fallback;
* optionally, cue-alignment boundary stats against a reference SRT.

Examples:
    python scripts\\eval\\diagnose_timeline.py \\
        --asr-json temp/asr/digimon_ep001_main.qwen3.json

    # The reference must share the ASR's zero point; --shift removes the
    # main-story cut (95 s) from an absolute-time SRT.
    python scripts\\eval\\diagnose_timeline.py \\
        --asr-json temp/asr/digimon_ep001_main.qwen3.json \\
        --srt temp/references/digimon/EP001.main.srt --shift -95 \\
        --json temp/reports/digimon_ep001_main_timeline.json

    # Compare the opt-in overlong repair as well.
    ... --repair-overlong

The numbers are diagnostics, not ASR/CER accuracy.  See
docs/tech-route-progress.md §"EP01 token 时间轴诊断".
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from lib.alignment.diagnostics import boundary_stats  # noqa: E402
from lib.alignment.quality import (  # noqa: E402
    TimelinePolicy, median_char_rate, visible_len,
)
from lib.alignment.cue_mapper import map_report  # noqa: E402
from lib.alignment.timeline import build_char_timeline  # noqa: E402
from lib.asr.base import ASRResult  # noqa: E402
from lib.subtitle_io import read_subtitles  # noqa: E402

ZERO_EPS = 1e-6


def _load(path: str) -> ASRResult:
    payload = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    return ASRResult.from_dict(payload)


def _triples(units) -> list[tuple[str, float, float]]:
    out = []
    for unit in units or []:
        out.append((str(getattr(unit, 'text', '') or ''),
                    float(getattr(unit, 'start', 0.0) or 0.0),
                    float(getattr(unit, 'end', 0.0) or 0.0)))
    return out


def zero_runs(units, *, min_run: int = 1,
              limit: int = 200) -> list[dict]:
    runs = []
    i = 0
    total = len(units)
    while i < total:
        if units[i][2] - units[i][1] > ZERO_EPS:
            i += 1
            continue
        j = i
        while j < total and units[j][2] - units[j][1] <= ZERO_EPS:
            j += 1
        prev_end = units[i - 1][2] if i else 0.0
        next_start = units[j][1] if j < total else prev_end
        runs.append({
            'index': i,
            'units': j - i,
            'start': round(units[i][1], 3),
            'prev_end': round(prev_end, 3),
            'next_start': round(next_start, 3),
            'gap_seconds': round(next_start - prev_end, 3),
            'text': ''.join(u[0] for u in units[i:j])[:60],
        })
        i = j
    if min_run > 1:
        runs = [r for r in runs if r['units'] >= min_run]
    return runs[:limit]


def overlong_units(units, *, policy: TimelinePolicy,
                   limit: int = 30) -> list[dict]:
    rate = median_char_rate(units)
    out = []
    for index, (text, start, end) in enumerate(units):
        n = visible_len(text)
        dur = end - start
        if n <= 0:
            continue
        expected = rate * n
        if dur > max(policy.overlong_floor, policy.overlong_factor * expected):
            out.append({
                'index': index,
                'text': text[:60],
                'start': round(start, 3),
                'end': round(end, 3),
                'seconds': round(dur, 3),
                'expected': round(expected, 3),
                'ratio': round(dur / expected, 2) if expected else 0.0,
            })
    out.sort(key=lambda r: -r['seconds'])
    return out[:limit]


def _case(cues, result, policy: TimelinePolicy) -> dict:
    report = map_report(cues, result, policy=policy)
    boundary = boundary_stats(report.alignments)
    summary = report.summary()
    return {
        'source': report.timeline_source,
        'fallback': report.timeline_fallback,
        'quality': report.timeline_quality,
        'cues': len(cues),
        'status': summary['status'],
        'clean_ratio': summary['clean_ratio'],
        'boundary': boundary,
        'warnings': report.warnings,
    }


def _print_quality(label: str, quality: dict) -> None:
    if not quality:
        print(f'{label}: no timestamp quality (empty source)')
        return
    verdict = 'degraded' if quality.get('degraded') else 'ok'
    print(f"{label}: {quality.get('units', 0)} units / "
          f"{quality.get('chars', 0)} chars / "
          f"{quality.get('duration', 0):.1f}s -> {verdict} "
          f"(score {quality.get('score', 0):.2f})")
    print(f"  zero={quality.get('zero_ratio', 0):.1%} "
          f"run={quality.get('longest_zero_run', 0)} "
          f"overlong={quality.get('overlong_ratio', 0):.1%} "
          f"median={quality.get('median_unit_seconds', 0):.2f}s "
          f"rate={quality.get('median_char_rate', 0):.3f}s/char")
    for reason in quality.get('reasons', []):
        print(f'  - {reason}')


def _print_cases(cases: dict) -> None:
    print('\n## cue alignment cases (reference SRT)')
    print('| case | source | match | P50 起/止 ms | P90 起/止 ms | '
          '±100ms | clean |')
    print('|---|---|---:|---:|---:|---:|---:|')
    for name, case in cases.items():
        start = case['boundary']['start']
        end = case['boundary']['end']
        print(f"| {name} | {case['source']} | "
              f"{case['status'].get('match', 0)}/{case['cues']} | "
              f"{start['p50_ms']} / {end['p50_ms']} | "
              f"{start['p90_ms']} / {end['p90_ms']} | "
              f"{start['hit_100']:.1%} / {end['hit_100']:.1%} | "
              f"{case['clean_ratio']:.1%} |")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Diagnose ASR token/segment timestamps (offline)')
    parser.add_argument('--asr-json', required=True,
                        help='cached ASRResult JSON')
    parser.add_argument('--srt', default='',
                        help='optional reference SRT/ASS for boundary stats')
    parser.add_argument('--shift', type=float, default=0.0,
                        help='add this to reference cue times (e.g. -95 for '
                             'a main-story cut)')
    parser.add_argument('--json', default='', help='write the full report')
    parser.add_argument('--repair-overlong', action='store_true',
                        help='also score an opt-in overlong-span repair')
    parser.add_argument('--min-run', type=int, default=1,
                        help='only print collapsed runs with >= N units')
    parser.add_argument('--max-runs', type=int, default=40)
    args = parser.parse_args(argv)

    asr_path = Path(args.asr_json)
    if not asr_path.exists():
        print(f'[timeline] ASR JSON not found: {asr_path}', file=sys.stderr)
        return 2
    try:
        result = _load(str(asr_path))
    except (ValueError, json.JSONDecodeError) as exc:
        print(f'[timeline] cannot read ASR JSON: {exc}', file=sys.stderr)
        return 3

    token_units = _triples(result.tokens)
    segment_units = _triples(result.segments)
    policy = TimelinePolicy()
    token_tl = build_char_timeline(result, policy=TimelinePolicy(
        force_source='tokens'))
    segment_tl = (build_char_timeline(result, policy=TimelinePolicy(
        force_source='segments')) if segment_units else None)

    report = {
        'asr': str(asr_path),
        'audio_duration': float(getattr(result, 'audio_duration', 0.0) or 0.0),
        'tokens': {
            'quality': (token_tl.quality.to_dict()
                        if token_tl.quality else {}),
            'zero_runs': zero_runs(token_units, min_run=1, limit=10_000),
            'overlong': overlong_units(token_units, policy=policy),
        },
        'segments': {
            'quality': (segment_tl.quality.to_dict()
                        if segment_tl and segment_tl.quality else {}),
            'zero_runs': zero_runs(segment_units, min_run=1, limit=10_000),
            'overlong': overlong_units(segment_units, policy=policy),
        } if segment_units else {},
        'cases': {},
    }

    print(f'# timeline diagnosis: {asr_path.name}')
    print(f"audio={report['audio_duration']:.1f}s "
          f"tokens={len(token_units)} segments={len(segment_units)}")
    if token_tl.quality:
        _print_quality('tokens', token_tl.quality.to_dict())
    if segment_tl and segment_tl.quality:
        _print_quality('segments', segment_tl.quality.to_dict())
    for warning in token_tl.warnings:
        print(f'  ! {warning}')
    for warning in (segment_tl.warnings if segment_tl else []):
        if warning not in token_tl.warnings:
            print(f'  ! {warning}')

    runs = report['tokens']['zero_runs']
    shown = [r for r in runs if r['units'] >= args.min_run][:args.max_runs]
    print(f'\n## collapsed token runs: {len(runs)} '
          f'({sum(r["units"] for r in runs)} zero units)')
    for run in shown:
        print(f"  {run['start']:8.2f}s n={run['units']:2d} "
              f"gap={run['gap_seconds']:7.2f}s {run['text']!r}")
    if len(runs) > len(shown):
        print(f'  ... {len(runs) - len(shown)} more (see --max-runs/--json)')

    overlong = report['tokens']['overlong']
    if overlong:
        print(f'\n## overlong token spans: {len(overlong)}')
        for unit in overlong[:20]:
            print(f"  {unit['start']:8.2f}-{unit['end']:8.2f}s "
                  f"{unit['seconds']:6.2f}s ({unit['ratio']:.1f}x) "
                  f"{unit['text']!r}")

    if args.srt:
        srt_path = Path(args.srt)
        if not srt_path.exists():
            print(f'[timeline] reference not found: {srt_path}',
                  file=sys.stderr)
            return 2
        cues = read_subtitles(str(srt_path), mark_garbled=False)
        if args.shift:
            cues = [dict(c, start_s=c['start_s'] + args.shift,
                         end_s=c['end_s'] + args.shift) for c in cues]
        if not cues:
            print('[timeline] no cues parsed from reference', file=sys.stderr)
            return 2
        cases = {}
        cases['tokens'] = _case(cues, result, TimelinePolicy(
            force_source='tokens'))
        if segment_units:
            cases['segments'] = _case(cues, result, TimelinePolicy(
                force_source='segments'))
        if args.repair_overlong:
            cases['tokens_repaired'] = _case(cues, result, TimelinePolicy(
                force_source='tokens', repair_overlong=True))
        report['cases'] = cases
        _print_cases(cases)

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                       encoding='utf-8')
        print(f'\n[timeline] report: {out}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
