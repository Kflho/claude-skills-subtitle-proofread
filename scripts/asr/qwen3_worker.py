#!/usr/bin/env python3
"""Qwen3-ASR long-lived worker (runs in an isolated venv).

Protocol: newline-delimited JSON on stdin/stdout.  One process keeps the
models loaded across many episodes; the main skill never imports torch.

Request:
    {"cmd": "ping", "id": "1"}
    {"cmd": "transcribe", "id": "EP001", "audio": "ep.wav",
     "language": "Japanese", "context": "...", "timestamps": true}
    {"cmd": "align", "id": "cue-1", "audio": "scene.wav",
     "text": "お前、何してんだよ！", "language": "Japanese"}
    {"cmd": "shutdown"}

Response:
    {"ok": true, "id": "EP001", "result": {...ASRResult...}}
    {"ok": false, "id": "EP001", "error": "...", "traceback": "..."}

Usage:
    .venv-qwen\\Scripts\\python.exe scripts\\asr\\qwen3_worker.py \
        --asr Qwen/Qwen3-ASR-1.7B \
        --aligner Qwen/Qwen3-ForcedAligner-0.6B \
        --device cuda:0 --dtype bfloat16
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import traceback
import unicodedata
import wave
from pathlib import Path


# Make `lib.asr.base` importable when launched as scripts/asr/qwen3_worker.py
_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from lib.asr.base import (  # noqa: E402
    ASRResult, Segment, Token, build_segments, language_name,
)


def _log(message: str) -> None:
    print(f'[qwen3-worker] {message}', file=sys.stderr, flush=True)


def _audio_duration(path: str) -> float:
    """Duration in seconds for a WAV file; 0.0 if unknown."""
    try:
        with wave.open(path, 'rb') as wf:
            rate = wf.getframerate()
            return wf.getnframes() / float(rate) if rate else 0.0
    except Exception:
        pass
    try:
        import soundfile as sf
        info = sf.info(path)
        return float(info.frames) / float(info.samplerate)
    except Exception:
        return 0.0


def _context_hash(context: str) -> str:
    return hashlib.sha1((context or '').encode('utf-8')).hexdigest()[:12]


_SENTENCE_MARKS = '。！？!?'


def _split_sentence_chunks(text: str) -> list[str]:
    """Split on sentence punctuation, keeping the punctuation."""
    chunks: list[str] = []
    buf = ''
    for ch in text or '':
        buf += ch
        if ch in _SENTENCE_MARKS:
            if buf.strip():
                chunks.append(buf.strip())
            buf = ''
    if buf.strip():
        chunks.append(buf.strip())
    return chunks


def _visible_len(text: str) -> int:
    """Characters that carry speech (ignore punctuation/spaces/symbols)."""
    total = 0
    for ch in text or '':
        cat = unicodedata.category(ch)
        if cat.startswith(('P', 'Z', 'S')):
            continue
        total += 1
    return total


def _segments_from_asr_text(text: str, tokens: list[Token],
                            duration: float = 0.0) -> list[Segment]:
    """Build segments whose text keeps ASR punctuation.

    Qwen3-ASR often emits punctuation that the aligner does not time.  If we
    naively join token texts, the SRT loses those marks.  Split the ASR text
    into sentence chunks and distribute the timed tokens across them.
    """
    text = (text or '').strip()
    if not tokens:
        return ([Segment(text=text, start=0.0, end=duration or 0.0)]
                if text else [])

    chunks = _split_sentence_chunks(text)
    if len(chunks) <= 1:
        segs = build_segments(tokens)
        if segs and text and text[-1] in _SENTENCE_MARKS:
            segs[-1].text += text[-1]
        if not segs:
            segs = [Segment(text=text, start=tokens[0].start,
                            end=tokens[-1].end, tokens=list(tokens))]
        return segs

    weights = [max(_visible_len(t.text), 1) for t in tokens]
    idx = 0
    segs: list[Segment] = []
    for chunk in chunks:
        target = _visible_len(chunk)
        start = idx
        acc = 0
        while idx < len(tokens) and (acc < target or idx == start):
            acc += weights[idx]
            idx += 1
        picked = tokens[start:idx]
        if not picked:
            if segs:
                segs[-1].text += chunk
                continue
            picked = [tokens[0]]
        segs.append(Segment(
            text=chunk,
            start=picked[0].start,
            end=picked[-1].end,
            tokens=list(picked),
        ))

    if idx < len(tokens) and segs:
        segs[-1].end = max(segs[-1].end, tokens[-1].end)
        segs[-1].tokens.extend(tokens[idx:])
    return segs


def _dtype(name: str):
    import torch
    if name == 'float16':
        return torch.float16
    if name == 'float32':
        return torch.float32
    return torch.bfloat16


class QwenRuntime:
    """Lazy loader so `ping` is instant and model load errors are reportable."""

    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.model = None
        self.loaded = False
        self.load_error: str | None = None

    def ensure_loaded(self):
        if self.loaded:
            return
        if self.load_error:
            raise RuntimeError(self.load_error)
        try:
            import torch
            from qwen_asr import Qwen3ASRModel
        except Exception as exc:
            self.load_error = f'qwen-asr import failed: {exc}'
            raise RuntimeError(self.load_error) from exc

        if str(self.args.device).startswith('cuda') and not torch.cuda.is_available():
            self.load_error = 'CUDA requested but torch.cuda.is_available() is False'
            raise RuntimeError(self.load_error)

        dtype_name = self.args.dtype
        if str(self.args.device) == 'cpu' and dtype_name == 'bfloat16':
            dtype_name = 'float32'
            _log('CPU + bfloat16 is slow; switching to float32')
        dtype = _dtype(dtype_name)

        kwargs = dict(
            dtype=dtype,
            device_map=self.args.device,
        )
        if self.args.attn_implementation:
            kwargs['attn_implementation'] = self.args.attn_implementation

        _log(f'loading ASR {self.args.asr} on {self.args.device} ({dtype_name})')
        if self.args.aligner:
            kwargs['forced_aligner'] = self.args.aligner
            kwargs['forced_aligner_kwargs'] = dict(
                dtype=dtype, device_map=self.args.device)
        self.model = Qwen3ASRModel.from_pretrained(
            self.args.asr,
            max_inference_batch_size=1,
            max_new_tokens=self.args.max_new_tokens,
            **kwargs,
        )
        self.loaded = True
        _log('models loaded')

    @property
    def aligner(self):
        return getattr(self.model, 'forced_aligner', None) if self.model else None

    def ping(self) -> dict:
        info = {
            'asr_model': self.args.asr,
            'aligner_model': self.args.aligner or None,
            'device': self.args.device,
            'dtype': self.args.dtype,
            'loaded': self.loaded,
        }
        if not self.loaded:
            try:
                import torch
                info['cuda_available'] = bool(torch.cuda.is_available())
                info['torch'] = torch.__version__
            except Exception:
                info['cuda_available'] = False
        return info

    def transcribe(self, payload: dict) -> dict:
        self.ensure_loaded()
        audio = payload.get('audio')
        if not audio:
            raise ValueError('transcribe: missing "audio"')
        if not os.path.exists(audio):
            raise FileNotFoundError(audio)

        requested_language = payload.get('language') or 'Japanese'
        context = payload.get('context') or ''
        want_ts = bool(payload.get('timestamps', False))
        has_aligner = self.aligner is not None
        if want_ts and not has_aligner:
            _log('timestamps requested but no aligner loaded; returning text only')
            want_ts = False

        _log(f'transcribe {Path(audio).name} lang={requested_language} '
             f'ctx={len(context)}ch timestamps={want_ts}')
        results = self.model.transcribe(
            audio=audio,
            context=context,
            language=language_name(requested_language),
            return_time_stamps=want_ts,
        )
        if not results:
            raise RuntimeError('Qwen3-ASR returned no result')

        asr = results[0]
        tokens = _align_items_to_tokens(getattr(asr, 'time_stamps', None))
        duration = _audio_duration(audio)
        segments = _segments_from_asr_text(asr.text or '', tokens, duration)

        result = ASRResult(
            text=asr.text or '',
            language=getattr(asr, 'language', '') or language_name(requested_language),
            segments=segments,
            tokens=tokens,
            engine='qwen3',
            model=self.args.asr,
            audio_duration=duration,
            context_hash=_context_hash(context),
            meta={
                'device': self.args.device,
                'dtype': self.args.dtype,
                'timestamps': bool(tokens),
                'aligner': self.args.aligner or None,
            },
        )
        return result.to_dict()

    def align(self, payload: dict) -> dict:
        self.ensure_loaded()
        if self.aligner is None:
            raise RuntimeError('align: worker started without --aligner')
        audio = payload.get('audio')
        text = payload.get('text') or ''
        if not audio or not os.path.exists(audio):
            raise FileNotFoundError(audio or '(missing audio)')
        if not text.strip():
            raise ValueError('align: empty text')
        language = payload.get('language') or 'Japanese'

        _log(f'align {Path(audio).name} {len(text)}ch lang={language}')
        results = self.aligner.align(
            audio=audio, text=text, language=language_name(language))
        if not results:
            raise RuntimeError('Qwen3-ForcedAligner returned no result')
        tokens = _align_items_to_tokens(results[0])
        return {
            'text': text,
            'language': language_name(language),
            'tokens': [t.to_dict() for t in tokens],
            'meta': {'device': self.args.device, 'dtype': self.args.dtype,
                     'aligner': self.args.aligner},
        }


def _align_items_to_tokens(align_result) -> list[Token]:
    """Convert Qwen ForcedAlignResult/items into our Token list."""
    if align_result is None:
        return []
    items = getattr(align_result, 'items', align_result)
    if items is None:
        return []
    tokens: list[Token] = []
    for item in items:
        text = getattr(item, 'text', None)
        start = getattr(item, 'start_time', None)
        end = getattr(item, 'end_time', None)
        if text is None and isinstance(item, dict):
            text = item.get('text')
            start = item.get('start', item.get('start_time'))
            end = item.get('end', item.get('end_time'))
        if text is None or start is None or end is None:
            continue
        if not str(text).strip():
            continue
        tokens.append(Token(text=str(text), start=float(start), end=float(end)))
    return tokens


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _handle(runtime: QwenRuntime, request: dict) -> dict:
    cmd = request.get('cmd', '')
    if cmd == 'ping':
        return runtime.ping()
    if cmd == 'load':
        runtime.ensure_loaded()
        return runtime.ping()
    if cmd == 'transcribe':
        return runtime.transcribe(request)
    if cmd == 'align':
        return runtime.align(request)
    raise ValueError(f'unknown cmd: {cmd!r}')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Qwen3-ASR JSON-lines worker')
    parser.add_argument('--asr', default='Qwen/Qwen3-ASR-1.7B')
    parser.add_argument('--aligner', default='Qwen/Qwen3-ForcedAligner-0.6B')
    parser.add_argument('--device', default='cuda:0')
    parser.add_argument('--dtype', default='bfloat16',
                        choices=['bfloat16', 'float16', 'float32'])
    parser.add_argument('--attn-implementation', default='')
    parser.add_argument('--max-new-tokens', type=int, default=1024)
    parser.add_argument('--once', action='store_true',
                        help='Handle one request then exit (debug/tests)')
    args = parser.parse_args(argv)

    if hasattr(sys.stdin, 'reconfigure'):
        sys.stdin.reconfigure(encoding='utf-8')
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')

    runtime = QwenRuntime(args)
    _log(f'ready (asr={args.asr}, aligner={args.aligner or "none"}, '
         f'device={args.device}, dtype={args.dtype})')

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            _emit({'ok': False, 'id': None, 'error': f'bad json: {exc}'})
            if args.once:
                break
            continue

        req_id = request.get('id')
        cmd = request.get('cmd', '')
        if cmd == 'shutdown':
            _emit({'ok': True, 'id': req_id, 'result': {'bye': True}})
            break
        try:
            result = _handle(runtime, request)
            _emit({'ok': True, 'id': req_id, 'result': result})
        except Exception as exc:
            tb = traceback.format_exc()
            _log(f'{cmd} failed: {exc}')
            _emit({'ok': False, 'id': req_id, 'error': str(exc),
                   'traceback': tb[-4000:]})
        if args.once:
            break
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
