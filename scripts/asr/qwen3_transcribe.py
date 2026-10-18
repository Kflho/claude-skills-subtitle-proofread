#!/usr/bin/env python3
"""Transcribe one audio/video file with Qwen3-ASR (P1 standalone CLI).

This is the first runnable slice of the 2026 tech route.  It does NOT
modify SRT files and does NOT wire into Fixer yet; it writes ASR JSON
(and optional SRT) so we can A/B against the current Whisper pipeline.

Examples:
    # Inside the isolated venv
    .venv-qwen\\Scripts\\python.exe scripts\\asr\\qwen3_transcribe.py \
        --check

    .venv-qwen\\Scripts\\python.exe scripts\\asr\\qwen3_transcribe.py \
        --audio ep.wav --language Japanese --timestamps \
        --context-file glossary.txt --output temp/asr/EP001.json

    # From the main env, point at the isolated interpreter
    python scripts\\asr\\qwen3_transcribe.py --audio ep.wav \
        --python .venv-qwen\\Scripts\\python.exe --srt temp/EP001.srt

    # Compare Whisper on the same file
    python scripts\\asr\\qwen3_transcribe.py --audio ep.wav \
        --engine whisper --output temp/asr/EP001.whisper.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

# Make `lib` importable when run as scripts/asr/qwen3_transcribe.py
_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from lib.asr.base import ASRConfig, Segment  # noqa: E402
from lib.asr.router import ASRRouter  # noqa: E402
from lib.config import (  # noqa: E402
    ALIGNER_MODEL, ASR_DEVICE, ASR_DTYPE, ASR_MODEL, QWEN_PYTHON,
    WHISPER_MODEL,
)


def _read_context(path: str | None) -> str:
    if not path:
        return ''
    # Windows PowerShell's `Set-Content -Encoding utf8` writes a BOM.
    return Path(path).read_text(encoding='utf-8-sig').strip()


def _srt_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f'{h:02d}:{m:02d}:{s:02d},{ms:03d}'


def write_srt(segments: list[Segment], path: str) -> None:
    """Minimal SRT writer (transcript-only; timeline untouched)."""
    if not segments:
        raise ValueError('no segments to write')
    lines = []
    idx = 0
    for seg in segments:
        text = (seg.text or '').strip()
        if not text:
            continue
        idx += 1
        lines.append(str(idx))
        lines.append(f'{_srt_time(seg.start)} --> {_srt_time(seg.end)}')
        lines.append(text)
        lines.append('')
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text('\n'.join(lines), encoding='utf-8')


def _extract_audio(video: str, work_dir: str) -> str:
    from lib.whisper_utils import extract_audio_wav
    out = Path(work_dir) / (Path(video).stem + '.wav')
    out.parent.mkdir(parents=True, exist_ok=True)
    extract_audio_wav(video, str(out))
    return str(out)


def _check(args) -> int:
    """Report whether the current interpreter can run Qwen3 (no model load)."""
    ok = True
    print(f'python: {sys.executable}')
    print(f'worker: {(Path(__file__).parent / "qwen3_worker.py")}')
    print(f'engine: {args.engine}')
    print(f'model: {args.model or ASR_MODEL}')
    print(f'aligner_model: {args.aligner or "(none)"}')
    try:
        import torch
        print(f'torch: {torch.__version__} cuda={torch.cuda.is_available()}')
        if torch.cuda.is_available():
            print(f'gpu: {torch.cuda.get_device_name(0)}')
    except Exception as exc:
        ok = False
        print(f'torch: MISSING ({exc})')
    try:
        import qwen_asr
        version = getattr(qwen_asr, '__version__', '?')
        print(f'qwen_asr: {version}')
    except Exception as exc:
        ok = False
        print(f'qwen_asr: MISSING ({exc})')
        print('  install: pip install -U qwen-asr==0.0.6')
    print('OK' if ok else 'NOT READY')
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Qwen3-ASR single-file transcription (P1)')
    parser.add_argument('--audio', help='16k mono WAV (preferred)')
    parser.add_argument('--video', help='video file; audio is extracted via ffmpeg')
    parser.add_argument('--language', default='Japanese')
    parser.add_argument('--context', default='', help='glossary/context string')
    parser.add_argument('--context-file', default='', help='UTF-8 glossary file')
    parser.add_argument('--timestamps', dest='timestamps', action='store_true',
                        default=True)
    parser.add_argument('--no-timestamps', dest='timestamps',
                        action='store_false')
    parser.add_argument('--engine', default='qwen3',
                        help='registered ASR engine (see --list-engines)')
    parser.add_argument('--model', '--asr', dest='model', default=None,
                        help='engine model id/path (default: engine-specific)')
    parser.add_argument('--aligner', default=ALIGNER_MODEL)
    parser.add_argument('--python', default=QWEN_PYTHON or '',
                        help='isolated venv python for worker engines')
    parser.add_argument('--device', default=ASR_DEVICE)
    parser.add_argument('--dtype', default=ASR_DTYPE,
                        choices=['bfloat16', 'float16', 'float32'])
    parser.add_argument('--output', default='', help='ASR JSON output path')
    parser.add_argument('--srt', default='', help='optional SRT output path')
    parser.add_argument('--work-dir', default='temp/qwen3_asr')
    parser.add_argument('--check', action='store_true',
                        help='check interpreter/model deps and exit')
    parser.add_argument('--list-engines', action='store_true',
                        help='list registered ASR adapters and exit')
    args = parser.parse_args(argv)

    if args.list_engines:
        from lib.asr import registry
        for entry in registry.entries():
            caps = ','.join(entry.info.capabilities) or '-'
            print(f'{entry.name}\t{entry.kind}\t{entry.info.auto_priority}\t'
                  f'{caps}\t{entry.info.description}')
        return 0

    if args.check:
        return _check(args)

    if not args.audio and not args.video:
        parser.error('one of --audio / --video is required (or --check)')
    if args.audio and args.video:
        parser.error('use only one of --audio / --video')

    audio_path = args.audio
    if args.video:
        audio_path = _extract_audio(args.video, args.work_dir)
        print(f'[qwen3] extracted audio: {audio_path}', file=sys.stderr)
    if not os.path.exists(audio_path):
        print(f'[qwen3] audio not found: {audio_path}', file=sys.stderr)
        return 2

    context = args.context or _read_context(args.context_file)
    print(f'[qwen3] engine={args.engine} lang={args.language} '
          f'timestamps={args.timestamps} context={len(context)}ch',
          file=sys.stderr)

    engine = args.engine
    model = args.model or (WHISPER_MODEL if engine == 'whisper' else ASR_MODEL)
    whisper_model = model if engine == 'whisper' else WHISPER_MODEL
    config = ASRConfig(
        model=model,
        aligner_model=args.aligner,
        python=args.python or '',
        device=args.device,
        dtype=args.dtype,
        options={'whisper_model': whisper_model},
    )

    router = ASRRouter(engine=engine, fallback='none', config=config)
    try:
        result = router.transcribe(
            audio_path, language=args.language, context=context,
            timestamps=args.timestamps)
    except Exception as exc:
        print(f'[qwen3] transcription failed: {exc}', file=sys.stderr)
        return 3
    finally:
        router.close()

    payload = result.to_dict()
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding='utf-8')
        print(f'[qwen3] wrote {args.output}', file=sys.stderr)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    if args.srt:
        segments = result.segments
        if not segments and result.tokens:
            from lib.asr.base import build_segments
            segments = build_segments(result.tokens)
        if not segments and result.text:
            segments = [Segment(text=result.text, start=0.0,
                                end=result.audio_duration or 0.0)]
        write_srt(segments, args.srt)
        print(f'[qwen3] wrote {args.srt}', file=sys.stderr)

    print(f'[qwen3] done: {len(result.text)} chars, '
          f'{len(result.tokens)} tokens, {len(result.segments)} segments, '
          f'{result.audio_duration:.1f}s', file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
