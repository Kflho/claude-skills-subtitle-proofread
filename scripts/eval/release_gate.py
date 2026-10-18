#!/usr/bin/env python3
"""Release gate: run the CC-standard benchmark and print a PASS/FAIL verdict.

Why: ``episode_benchmark.py`` measures, the protocol decides.  This script is
the decision step — one command that

1. runs the benchmark on every given manifest (cached ASR, read-only),
2. checks the P2-14 targets on the test split
   (start P50 ≤ 100 ms, reliable start P90 ≤ 300 ms),
3. checks for regressions against a frozen baseline,
4. exits 0 only when everything passes.

The Japanese CC is the boundary test standard (user decision, 2026-10-18).
Targets and regression bounds are policy, not tuned values: they live here
so a change can be judged without reading reports by hand.

Usage:
    python scripts/eval/release_gate.py \\
        --manifests eval/manifest.digimon.json eval/manifest.digimon.whisper.json \\
        --baseline eval/gate.baseline.json \\
        --out-dir temp/reports

    # Freeze the current numbers as the new baseline (deliberate action):
    ... --update-baseline
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from eval import episode_benchmark  # noqa: E402

SCHEMA_VERSION = 1

# P2-14 acceptance (tech-route-2026 §8.3) on the test split.
TARGETS = {
    'p50_start_ms': 100.0,
    'p90_reliable_ms': 300.0,
}

# Allowed movement before it counts as a regression.  The CER/P50 bounds are
# the protocol's "decimal noise" limits (<1% CER, <20 ms P50); the P90 bound
# is the same order as the P50 one.  Coverage is the one where *lower* is
# worse, hence the negative allowance.
REGRESSION = {
    'p50_start_ms': 20.0,
    'p90_reliable_ms': 50.0,
    'micro_cer': 0.01,
    'char_coverage': -0.02,
}

METRICS = ('p50_start_ms', 'p90_reliable_ms', 'micro_cer',
           'char_coverage', 'matched_cue_ratio')
LABELS = {
    'p50_start_ms': 'P50 start (ms)',
    'p90_reliable_ms': 'reliable P90 (ms)',
    'micro_cer': 'micro CER',
    'char_coverage': 'char coverage',
    'matched_cue_ratio': 'matched cue ratio',
}


# ═══════════════════════════════════════════════════════════════
# Collect
# ═══════════════════════════════════════════════════════════════

def manifest_key(path: str | Path) -> str:
    """Stable baseline key for a manifest file."""
    return Path(path).stem


def collect(manifest: str | Path, out_dir: str | Path) -> dict:
    """Run the benchmark for one manifest and return its JSON payload."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    key = manifest_key(manifest)
    json_out = out_dir / f'gate_{key}.json'
    md_out = out_dir / f'gate_{key}.md'
    code = episode_benchmark.main([
        '--manifest', str(manifest),
        '--json-out', str(json_out),
        '--output', str(md_out),
    ])
    if code not in (0, 1):
        raise RuntimeError(f'benchmark failed for {manifest} (code {code})')
    payload = json.loads(json_out.read_text(encoding='utf-8'))
    if any('error' in row for row in payload.get('episodes', [])):
        raise RuntimeError(f'benchmark reported episode errors for {manifest}')
    return payload


def extract_metrics(payload: dict) -> dict:
    """Per-split gate metrics from a benchmark payload."""
    out = {}
    for split, agg in (payload.get('splits') or {}).items():
        matched = agg.get('match')
        cues = agg.get('cues')
        out[split] = {
            'p50_start_ms': agg.get('p50_start_ms'),
            'p90_reliable_ms': agg.get('p90_reliable_ms'),
            'micro_cer': agg.get('micro_cer'),
            'char_coverage': agg.get('char_coverage'),
            'matched_cue_ratio': (round(matched / cues, 3)
                                  if matched is not None and cues else None),
        }
    return out


# ═══════════════════════════════════════════════════════════════
# Decide
# ═══════════════════════════════════════════════════════════════

def evaluate(current: dict, baseline: dict | None, *,
             targets: dict | None = None,
             bounds: dict | None = None,
             target_splits: tuple[str, ...] = ('test',)) -> list[dict]:
    """Return one row per (engine, split, metric) with a verdict.

    Targets apply to ``target_splits`` (the test split by default: dev is a
    direction, not a gate).  Regression checks apply to the test split when a
    baseline exists.
    """
    targets = targets or TARGETS
    bounds = bounds or REGRESSION
    rows: list[dict] = []
    for engine, splits in current.items():
        base_splits = (baseline or {}).get(engine, {})
        for split, metrics in splits.items():
            base = base_splits.get(split, {})
            for metric in METRICS:
                value = metrics.get(metric)
                if value is None:
                    continue
                verdict = 'ok'
                reason = ''
                if split in target_splits and metric in targets:
                    target = targets[metric]
                    if value > target:
                        verdict = 'FAIL'
                        reason = f'target <= {target:g}'
                allowed = bounds.get(metric)
                base_value = base.get(metric)
                if (not reason and split == 'test' and allowed is not None
                        and base_value is not None):
                    delta = value - base_value
                    worse = (delta < allowed if allowed < 0
                             else delta > allowed)
                    if worse:
                        verdict = 'FAIL'
                        reason = (f'regressed vs baseline {base_value:g} '
                                  f'(delta {delta:+.3f})')
                rows.append({
                    'engine': engine,
                    'split': split,
                    'metric': metric,
                    'label': LABELS.get(metric, metric),
                    'value': value,
                    'baseline': base_value,
                    'verdict': verdict,
                    'reason': reason,
                })
    return rows


def failed(rows: list[dict]) -> list[dict]:
    return [row for row in rows if row['verdict'] == 'FAIL']


def render(rows: list[dict], *, baseline_used: bool) -> str:
    lines = [
        'release gate — Japanese CC boundary standard',
        '',
        '| engine | split | metric | current | baseline | verdict |',
        '|---|---|---|---:|---:|---|',
    ]
    for row in rows:
        base = '—' if row['baseline'] is None else f"{row['baseline']:g}"
        note = row['verdict']
        if row['reason']:
            note += f" ({row['reason']})"
        lines.append(
            f"| {row['engine']} | {row['split']} | {row['label']} | "
            f"{row['value']:g} | {base} | {note} |")
    lines += [
        '',
        f"- targets (test split): P50 ≤ {TARGETS['p50_start_ms']:g} ms, "
        f"reliable P90 ≤ {TARGETS['p90_reliable_ms']:g} ms",
        f'- baseline: {"used" if baseline_used else "not found (targets only)"}',
    ]
    return '\n'.join(lines) + '\n'


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Run the CC-standard benchmark and gate releases')
    parser.add_argument('--manifests', nargs='+', required=True)
    parser.add_argument('--baseline', default='eval/gate.baseline.json')
    parser.add_argument('--out-dir', default='temp/reports')
    parser.add_argument('--update-baseline', action='store_true',
                        help='write the current numbers as the new baseline')
    parser.add_argument('--allow-dev', action='store_true',
                        help='include dev split target checks (normally off)')
    args = parser.parse_args(argv)

    current: dict[str, dict] = {}
    for manifest in args.manifests:
        payload = collect(manifest, args.out_dir)
        current[manifest_key(manifest)] = extract_metrics(payload)

    baseline_path = Path(args.baseline)
    if args.update_baseline:
        baseline = {
            'schema_version': SCHEMA_VERSION,
            'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'standard': ('Japanese CC boundary standard (user decision, '
                         '2026-10-18); raw, no offset correction'),
            'metrics': current,
        }
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(
            json.dumps(baseline, ensure_ascii=False, indent=2),
            encoding='utf-8')
        print(f'[gate] baseline written: {baseline_path}', file=sys.stderr)

    baseline_used = False
    baseline_metrics = None
    if baseline_path.exists():
        loaded = json.loads(baseline_path.read_text(encoding='utf-8'))
        if isinstance(loaded, dict) and 'metrics' in loaded:
            baseline_metrics = loaded['metrics'] or {}
            baseline_used = True
        else:
            print(f'[gate] WARN ignoring baseline without "metrics": '
                  f'{baseline_path}', file=sys.stderr)

    targets = dict(TARGETS)
    target_splits = ('test', 'dev') if args.allow_dev else ('test',)
    rows = evaluate(current, baseline_metrics, targets=targets,
                    target_splits=target_splits)
    text = render(rows, baseline_used=baseline_used)
    print(text)

    failures = [row for row in failed(rows)
                if row['split'] in target_splits]
    if failures:
        print(f'[gate] FAIL: {len(failures)} check(s) failed',
              file=sys.stderr)
        return 1
    print('[gate] PASS', file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
