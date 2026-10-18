#!/usr/bin/env python3
"""FireRedVAD/AED worker — runs inside the isolated qwen venv.

The main skill process shells out here because FireRedVAD needs torch.
Emits the shared AudioHints JSON contract (scripts/lib/audio/base.py):

    {"contract_version": 1, "source": "fireredvad", "duration": 1145.0,
     "events": [{"kind": "speech", "start": 0.44, "end": 1.82}, ...],
     "meta": {"mode": "aed"}}

Usage (from the repo root):
    .venv-qwen\\Scripts\\python.exe scripts/audio/firered_worker.py \
        --wav episode.wav --models models/FireRedVAD --mode aed --out hints.json
"""

from __future__ import annotations

import argparse
import json
import sys


def _events(timestamps, kind):
    out = []
    for span in timestamps or []:
        try:
            start, end = float(span[0]), float(span[1])
        except (TypeError, ValueError, IndexError):
            continue
        if end > start:
            out.append({'kind': kind, 'start': round(start, 3),
                        'end': round(end, 3)})
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wav', required=True)
    parser.add_argument('--models', required=True,
                        help='models/FireRedVAD (contains VAD/ and AED/)')
    parser.add_argument('--mode', choices=('vad', 'aed'), default='aed')
    parser.add_argument('--out', default='', help='JSON output path; stdout if empty')
    parser.add_argument('--gpu', action='store_true')
    args = parser.parse_args(argv)

    payload = {
        'contract_version': 1,
        'source': 'fireredvad',
        'duration': 0.0,
        'events': [],
        'meta': {'mode': args.mode, 'models': args.models},
    }
    try:
        if args.mode == 'vad':
            from fireredvad import FireRedVad, FireRedVadConfig
            model = FireRedVad.from_pretrained(
                args.models + '/VAD', FireRedVadConfig(use_gpu=args.gpu))
            result, _ = model.detect(args.wav)
            payload['duration'] = float(result.get('dur', 0.0) or 0.0)
            payload['events'] = _events(result.get('timestamps'), 'speech')
        else:
            from fireredvad import FireRedAed, FireRedAedConfig
            model = FireRedAed.from_pretrained(
                args.models + '/AED', FireRedAedConfig(use_gpu=args.gpu))
            result, _ = model.detect(args.wav)
            payload['duration'] = float(result.get('dur', 0.0) or 0.0)
            for kind in ('speech', 'singing', 'music'):
                payload['events'].extend(_events(
                    (result.get('event2timestamps') or {}).get(kind), kind))
    except Exception as exc:  # model/import/audio errors → JSON, not traceback
        payload = {'error': f'{type(exc).__name__}: {exc}'}

    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, 'w', encoding='utf-8') as handle:
            handle.write(text)
    else:
        print(text)
    return 0 if 'error' not in payload else 1


if __name__ == '__main__':
    sys.exit(main())
