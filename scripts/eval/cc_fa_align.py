#!/usr/bin/env python3
"""Direct CC-text ↔ Qwen3-ForcedAligner alignment with FireRedVAD masking.

This is the P0 model-usage experiment, not another mapper: the trusted
subtitle text is aligned to speech-masked audio in ~60s groups.  The output
is an ASRResult-shaped JSON so the existing benchmark can score it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lib._path  # noqa: F401,E402
from lib.asr.base import ASRResult, Token  # noqa: E402
from lib.asr.qwen3 import Qwen3WorkerClient  # noqa: E402
from lib.whisper_utils import parse_subtitles  # noqa: E402


def group_cues(cues: list[dict], *, max_sec: float = 60.0,
               pad: float = 0.2) -> list[dict]:
    """Group consecutive cues into windows of at most ~max_sec."""
    groups, cur = [], []

    def window(items):
        return (items[0]['start_s'] - pad, items[-1]['end_s'] + pad)

    for cue in cues:
        if not cur:
            cur = [cue]
            continue
        start, end = window(cur + [cue])
        if end - start > max_sec:
            groups.append(cur)
            cur = [cue]
        else:
            cur.append(cue)
    if cur:
        groups.append(cur)
    return groups


def read_wav(path: str) -> tuple[np.ndarray, int]:
    with wave.open(path, 'rb') as wav:
        if wav.getsampwidth() != 2 or wav.getnchannels() != 1:
            raise ValueError('expected 16-bit mono wav')
        sr = wav.getframerate()
        data = np.frombuffer(wav.readframes(wav.getnframes()),
                             dtype=np.int16).copy()
    return data, sr


def mask_speech(data: np.ndarray, sr: int, speech: list[tuple[float, float]],
                *, pad: float = 0.1) -> np.ndarray:
    keep = np.zeros(len(data), dtype=bool)
    for start, end in speech:
        a = max(0, int((start - pad) * sr))
        b = min(len(data), int((end + pad) * sr))
        if b > a:
            keep[a:b] = True
    out = data.copy()
    out[~keep] = 0
    return out


def write_wav(path: str, data: np.ndarray, sr: int) -> None:
    with wave.open(path, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sr)
        wav.writeframes(data.astype(np.int16).tobytes())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audio', required=True, help='main-window 16k mono wav')
    parser.add_argument('--srt', required=True, help='Japanese CC')
    parser.add_argument('--hints', default='', help='FireRedVAD audio_hints.json')
    parser.add_argument('--out', required=True, help='ASRResult-shaped JSON')
    parser.add_argument('--start', type=float, default=95.0,
                        help='main-window start in the CC timeline (default 95)')
    parser.add_argument('--window', type=float, default=60.0)
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--python', default='.venv-qwen/Scripts/python.exe')
    parser.add_argument('--worker', default='scripts/asr/qwen3_worker.py')
    parser.add_argument('--asr', default='models/Qwen3-ASR-1.7B')
    parser.add_argument('--aligner', default='models/Qwen3-ForcedAligner-0.6B')
    parser.add_argument('--work-dir', default='temp/cc_fa_groups')
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
    groups = group_cues(cues, max_sec=args.window)
    print(f'groups={len(groups)} cues={len(cues)} speech={len(speech)} '
          f'duration={duration:.1f}s', file=sys.stderr)

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
    text = ''.join(str(c.get('text', '')).replace('\n', '') for c in cues)
    try:
        client.start()
        client.load()
        for gi, group in enumerate(groups, 1):
            a = max(0.0, group[0]['start_s'] - args.start - 0.2)
            b = min(duration, group[-1]['end_s'] - args.start + 0.2)
            if b <= a:
                continue
            chunk = data[int(a * sr):int(b * sr)]
            chunk_speech = [(max(0.0, s - a), min(b - a, e - a))
                            for s, e in speech]
            chunk = mask_speech(chunk, sr, chunk_speech)
            path = os.path.join(args.work_dir, f'g{gi:03d}.wav')
            write_wav(path, chunk, sr)
            group_text = ''.join(str(c.get('text', '')).replace('\n', '')
                                 for c in group)
            result = client.align(path, group_text, language='Japanese')
            for t in result.tokens:
                tokens.append(Token(
                    text=t.text,
                    start=round(a + t.start, 3),
                    end=round(a + t.end, 3),
                ))
            print(f'  group {gi}/{len(groups)} cues={len(group)} '
                  f'chars={len(group_text)} tokens={len(result.tokens)}',
                  file=sys.stderr)
    finally:
        client.close()

    payload = ASRResult(
        text=text,
        language='Japanese',
        tokens=tokens,
        engine='qwen3-fa-cc',
        model=os.path.basename(args.aligner),
        audio_duration=duration,
        meta={'source': 'cc_fa_direct', 'groups': len(groups),
              'speech_masked': bool(speech)},
    )
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps(payload.to_dict(), ensure_ascii=False, indent=2),
        encoding='utf-8')
    print(f'wrote {args.out}: {len(tokens)} tokens', file=sys.stderr)


if __name__ == '__main__':
    main()
