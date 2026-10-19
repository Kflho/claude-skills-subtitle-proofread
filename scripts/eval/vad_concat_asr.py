#!/usr/bin/env python3
"""VAD speech-only concatenation → Qwen3-ASR → map timestamps back.

Zero-masking non-speech produced long silence runs that made Qwen3-ASR
generate endlessly.  This variant removes non-speech entirely, transcribes
the concatenated speech, then maps token times back through the segment map.
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


def merge_speech(events: list[dict], *, gap: float = 0.2,
                 pad: float = 0.05, duration: float = 0.0) -> list[tuple]:
    spans = sorted((max(0.0, float(e['start'])), float(e['end']))
                   for e in events if e.get('kind') == 'speech')
    merged = []
    for start, end in spans:
        if merged and start - merged[-1][1] <= gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    out = []
    for start, end in merged:
        start = max(0.0, start - pad)
        end = min(duration, end + pad) if duration else end + pad
        if end > start:
            out.append((start, end))
    return out


def build_concat(data: np.ndarray, sr: int, spans: list[tuple]) -> tuple:
    pieces, mapping, cursor = [], [], 0
    for start, end in spans:
        piece = data[int(start * sr):int(end * sr)]
        if len(piece) == 0:
            continue
        pieces.append(piece)
        length = len(piece) / sr
        mapping.append((cursor, cursor + length, start, end))
        cursor += length
    if not pieces:
        return np.zeros(0, dtype=np.int16), [], 0.0
    return np.concatenate(pieces), mapping, cursor


def map_time(t: float, mapping: list[tuple]) -> float:
    for c0, c1, o0, o1 in mapping:
        if c0 <= t <= c1:
            return min(o1, o0 + max(0.0, t - c0))
    if not mapping:
        return t
    if t < mapping[0][0]:
        return mapping[0][2]
    return mapping[-1][3]


def read_wav(path: str) -> tuple[np.ndarray, int]:
    with wave.open(path, 'rb') as wav:
        sr = wav.getframerate()
        data = np.frombuffer(wav.readframes(wav.getnframes()),
                             dtype=np.int16).copy()
    return data, sr


def write_wav(path: str, data: np.ndarray, sr: int) -> None:
    with wave.open(path, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sr)
        wav.writeframes(data.astype(np.int16).tobytes())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audio', required=True)
    parser.add_argument('--hints', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--concat-out', default='temp/asr_vad/EP001_speech_only.wav')
    parser.add_argument('--python', default='.venv-qwen/Scripts/python.exe')
    parser.add_argument('--worker', default='scripts/asr/qwen3_worker.py')
    parser.add_argument('--asr', default='models/Qwen3-ASR-1.7B')
    parser.add_argument('--aligner', default='models/Qwen3-ForcedAligner-0.6B')
    parser.add_argument('--chunk-sec', type=float, default=60.0)
    args = parser.parse_args(argv)

    data, sr = read_wav(args.audio)
    duration = len(data) / sr
    hints = json.load(open(args.hints, encoding='utf-8'))
    spans = merge_speech(hints.get('events', []), duration=duration)
    concat, mapping, concat_duration = build_concat(data, sr, spans)
    Path(args.concat_out).parent.mkdir(parents=True, exist_ok=True)
    write_wav(args.concat_out, concat, sr)
    print(f'spans={len(spans)} concat={concat_duration:.1f}s '
          f'orig={duration:.1f}s -> {args.concat_out}', file=sys.stderr)

    client = Qwen3WorkerClient(
        python_exe=os.path.abspath(args.python),
        worker_script=os.path.abspath(args.worker),
        project_root=os.getcwd(),
        asr_model=os.path.abspath(args.asr),
        aligner_model=os.path.abspath(args.aligner),
        request_timeout=900,
    )
    try:
        client.start()
        client.load()
        result = client.transcribe(
            os.path.abspath(args.concat_out), language='ja',
            timestamps=True, chunk_sec=args.chunk_sec)
    finally:
        client.close()

    tokens = []
    for t in result.tokens:
        start = map_time(t.start, mapping)
        end = map_time(t.end, mapping)
        tokens.append(Token(text=t.text, start=round(start, 3),
                            end=round(max(start, end), 3)))
    payload = ASRResult(
        text=result.text,
        language='Japanese',
        tokens=tokens,
        engine='qwen3-vad-concat',
        model=os.path.basename(args.asr),
        audio_duration=duration,
        meta={'source': 'vad_speech_only', 'spans': len(spans),
              'concat_duration': round(concat_duration, 3)},
    )
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps(payload.to_dict(), ensure_ascii=False, indent=2),
        encoding='utf-8')
    print(f'wrote {args.out}: {len(tokens)} tokens', file=sys.stderr)


if __name__ == '__main__':
    main()
