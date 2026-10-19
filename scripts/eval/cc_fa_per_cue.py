#!/usr/bin/env python3
"""Per-cue CC→FA alignment: a short, speech-masked window per subtitle cue.

The group experiment improved coverage/CER but still had group-level
collapse.  This variant gives the aligner one cue and a ±2s window, so a
bad group cannot drag other cues with it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lib._path  # noqa: F401,E402
from lib.asr.base import ASRResult, Token  # noqa: E402
from lib.asr.qwen3 import Qwen3WorkerClient  # noqa: E402
from lib.whisper_utils import parse_subtitles  # noqa: E402
from eval.cc_fa_align import read_wav, mask_speech, write_wav  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audio', required=True)
    parser.add_argument('--srt', required=True)
    parser.add_argument('--hints', default='')
    parser.add_argument('--out', required=True)
    parser.add_argument('--start', type=float, default=95.0)
    parser.add_argument('--pad', type=float, default=2.0)
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--python', default='.venv-qwen/Scripts/python.exe')
    parser.add_argument('--worker', default='scripts/asr/qwen3_worker.py')
    parser.add_argument('--asr', default='models/Qwen3-ASR-1.7B')
    parser.add_argument('--aligner', default='models/Qwen3-ForcedAligner-0.6B')
    parser.add_argument('--work-dir', default='temp/cc_fa_per_cue')
    args = parser.parse_args(argv)

    data, sr = read_wav(args.audio)
    duration = len(data) / sr
    cues = parse_subtitles(args.srt, mark_garbled=False)
    if args.limit:
        cues = cues[:args.limit]
    speech = []
    if args.hints:
        hints = json.load(open(args.hints, encoding='utf-8'))
        speech = [(float(e['start']), float(e['end']))
                  for e in hints.get('events', [])
                  if e.get('kind') == 'speech']
    os.makedirs(args.work_dir, exist_ok=True)

    client = Qwen3WorkerClient(
        python_exe=os.path.abspath(args.python),
        worker_script=os.path.abspath(args.worker),
        project_root=os.getcwd(),
        asr_model=os.path.abspath(args.asr),
        aligner_model=os.path.abspath(args.aligner),
        request_timeout=900,
    )
    tokens: list[Token] = []
    try:
        client.start()
        client.load()
        for i, cue in enumerate(cues, 1):
            text = str(cue.get('text', '')).replace('\n', '')
            if not text:
                continue
            a = max(0.0, cue['start_s'] - args.start - args.pad)
            b = min(duration, cue['end_s'] - args.start + args.pad)
            if b - a < 0.2:
                continue
            chunk = data[int(a * sr):int(b * sr)]
            chunk_speech = [(max(0.0, s - a), min(b - a, e - a))
                            for s, e in speech]
            chunk = mask_speech(chunk, sr, chunk_speech)
            path = os.path.join(args.work_dir, f'c{i:04d}.wav')
            write_wav(path, chunk, sr)
            result = client.align(path, text, language='Japanese')
            for t in result.tokens:
                tokens.append(Token(text=t.text,
                                    start=round(a + t.start, 3),
                                    end=round(a + t.end, 3)))
            if i % 20 == 0 or i == len(cues):
                print(f'  cue {i}/{len(cues)} tokens={len(tokens)}',
                      file=sys.stderr)
    finally:
        client.close()

    payload = ASRResult(
        text=''.join(str(c.get('text', '')).replace('\n', '') for c in cues),
        language='Japanese',
        tokens=tokens,
        engine='qwen3-fa-per-cue',
        model=os.path.basename(args.aligner),
        audio_duration=duration,
        meta={'source': 'cc_fa_per_cue', 'cues': len(cues),
              'speech_masked': bool(speech)},
    )
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps(payload.to_dict(), ensure_ascii=False, indent=2),
        encoding='utf-8')
    print(f'wrote {args.out}: {len(tokens)} tokens', file=sys.stderr)


if __name__ == '__main__':
    main()
