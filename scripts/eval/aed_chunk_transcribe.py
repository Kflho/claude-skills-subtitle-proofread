#!/usr/bin/env python3
"""One-off: plan AED-boundary chunks and transcribe an episode with Qwen3.

Not production wiring yet; this is the P0 experiment for docs/research-2026-10-tech.md.
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))

from lib.asr.qwen3 import Qwen3WorkerClient  # noqa: E402


def plan_cuts(hints: dict, target: float = 60.0,
              search: float = 15.0, min_gap: float = 0.8) -> list[float]:
    speech = sorted((float(e['start']), float(e['end']))
                    for e in hints.get('events', []) if e.get('kind') == 'speech')
    duration = float(hints['duration'])
    gaps = [(a[1], b[0]) for a, b in zip(speech, speech[1:])
            if b[0] - a[1] >= min_gap]
    cuts, start = [], 0.0
    while start + target < duration - 1.0:
        goal = start + target
        candidates = [((a + b) / 2.0, a, b) for a, b in gaps
                      if start + target - search <= (a + b) / 2.0 <= start + target + search]
        if candidates:
            cut = min(candidates, key=lambda x: abs(x[0] - goal))[0]
        else:
            cut = goal
        cuts.append(round(cut, 3))
        start = cut
    return cuts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--audio', required=True)
    parser.add_argument('--hints', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--python', default='.venv-qwen/Scripts/python.exe')
    parser.add_argument('--worker', default='scripts/asr/qwen3_worker.py')
    parser.add_argument('--asr', default='models/Qwen3-ASR-1.7B')
    parser.add_argument('--aligner', default='models/Qwen3-ForcedAligner-0.6B')
    parser.add_argument('--target', type=float, default=60.0)
    args = parser.parse_args()

    hints = json.load(open(args.hints, encoding='utf-8'))
    cuts = plan_cuts(hints, target=args.target)
    print(f'cuts={len(cuts)} avg={sum(cuts) / max(1, len(cuts)):.1f}s', file=sys.stderr)

    client = Qwen3WorkerClient(
        python_exe=os.path.abspath(args.python),
        worker_script=os.path.abspath(args.worker),
        project_root=os.getcwd(),
        asr_model=os.path.abspath(args.asr),
        aligner_model=os.path.abspath(args.aligner),
        request_timeout=1800,
    )
    try:
        client.start()
        client.load()
        result = client.transcribe(
            os.path.abspath(args.audio), language='ja',
            timestamps=True, chunk_boundaries=cuts)
    finally:
        client.close()

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding='utf-8')
    print(f'wrote {args.out}: {len(result.tokens)} tokens, '
          f'{result.meta.get("chunks")} chunks', file=sys.stderr)


if __name__ == '__main__':
    main()
