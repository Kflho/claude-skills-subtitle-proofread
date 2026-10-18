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
        if len(segs) == 1 and text:
            # One utterance: keep the ASR text verbatim, including internal
            # commas/spaces the aligner never times.
            segs[0].text = text
        elif segs and text and text[-1] in _SENTENCE_MARKS:
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

        chunk_sec = float(payload.get('chunk_sec', self.args.chunk_sec) or 0.0)
        _log(f'transcribe {Path(audio).name} lang={requested_language} '
             f'ctx={len(context)}ch timestamps={want_ts} chunk={chunk_sec}s')

        if chunk_sec > 0:
            text, tokens, segments, duration, n_chunks = self._transcribe_chunked(
                audio, requested_language=requested_language,
                context=context, want_ts=want_ts, chunk_sec=chunk_sec)
        else:
            results = self.model.transcribe(
                audio=audio,
                context=context,
                language=language_name(requested_language),
                return_time_stamps=want_ts,
            )
            if not results:
                raise RuntimeError('Qwen3-ASR returned no result')
            asr = results[0]
            text = asr.text or ''
            tokens = _align_items_to_tokens(getattr(asr, 'time_stamps', None))
            if tokens:
                _fill_zero_durations(tokens)
            duration = _audio_duration(audio)
            segments = _segments_from_asr_text(text, tokens, duration)
            n_chunks = 1

        result = ASRResult(
            text=text,
            language=language_name(requested_language),
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
                'chunk_sec': chunk_sec,
                'chunks': n_chunks,
            },
        )
        return result.to_dict()

    def _transcribe_chunked(self, audio: str, *, requested_language: str,
                            context: str, want_ts: bool, chunk_sec: float):
        """Chunk long audio ourselves, then offset/merge per-chunk results.

        qwen_asr's own long-audio path splits at 180 s; on this material the
        ForcedAligner collapses most tokens to the chunk end beyond ~60 s
        (measured: 48% zero-duration tokens, 5-minute speech gaps).  Doing
        the split here keeps every alignment request short and gives us the
        offsets explicitly.
        """
        from qwen_asr.inference.utils import (
            SAMPLE_RATE, load_audio_any, split_audio_into_chunks,
        )

        wav, sr = load_audio_any(audio)
        parts = split_audio_into_chunks(wav=wav, sr=sr, max_chunk_sec=chunk_sec)
        joiner = '' if language_name(requested_language) == 'Japanese' else ' '

        texts: list[str] = []
        tokens: list[Token] = []
        segments: list[Segment] = []

        for chunk_wav, offset in parts:
            results = self.model.transcribe(
                audio=(chunk_wav, sr),
                context=context,
                language=language_name(requested_language),
                return_time_stamps=want_ts,
            )
            if not results:
                continue
            part = results[0]
            part_text = part.text or ''
            texts.append(part_text)

            part_tokens = _align_items_to_tokens(
                getattr(part, 'time_stamps', None))
            for token in part_tokens:
                token.start = float(token.start) + offset
                token.end = float(token.end) + offset
            if part_tokens:
                _fill_zero_durations(part_tokens)
            tokens.extend(part_tokens)

            part_dur = len(chunk_wav) / float(sr or SAMPLE_RATE)
            part_segments = _segments_from_asr_text(
                part_text, part_tokens, offset + part_dur)
            if not part_tokens:
                for seg in part_segments:
                    seg.start = offset
                    seg.end = offset + part_dur
            segments.extend(part_segments)

        duration = len(wav) / float(sr or SAMPLE_RATE)
        if tokens:
            _fill_zero_durations(tokens)   # cross-chunk boundary runs
        _log(f'chunked: {len(parts)} chunks, {len(tokens)} tokens')
        return joiner.join(texts), tokens, segments, duration, len(parts)

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


def _fill_zero_durations(tokens: list[Token], *, max_gap: float = 3.0,
                         max_per_token: float = 0.6) -> int:
    """Give zero-length aligner tokens a share of the surrounding span.

    Qwen3-FA returns start == end for tokens it cannot place (27 % of tokens
    on our 1966 material).  Left alone they contribute no time, which makes
    the character timeline look far less covered than the audio actually is.
    Redistribute such a run between the previous end and the next start, but
    only when that gap is plausibly intra-utterance; a long music/silence
    gap is left untouched rather than smeared with text.

    Returns the number of tokens that received a span.
    """
    filled = 0
    i = 0
    total = len(tokens)
    while i < total:
        if tokens[i].end - tokens[i].start > 1e-6:
            i += 1
            continue
        j = i
        while j < total and tokens[j].end - tokens[j].start <= 1e-6:
            j += 1
        prev_end = tokens[i - 1].end if i > 0 else tokens[i].start
        next_start = tokens[j].start if j < total else prev_end
        run = j - i
        span = next_start - prev_end
        limit = max(max_gap, max_per_token * run)
        if run and span > 0 and span <= limit:
            step = span / run
            for k, token in enumerate(tokens[i:j]):
                token.start = prev_end + step * k
                token.end = prev_end + step * (k + 1)
            filled += run
        i = j
    return filled


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
    parser.add_argument(
        '--chunk-sec', type=float, default=60.0,
        help='split long audio into <=N-second low-energy chunks before ASR; '
             '0 disables (qwen_asr then uses its own 180s chunks, whose '
             'timestamps collapse on our material)')
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
        if not isinstance(request, dict):
            _emit({'ok': False, 'id': None,
                   'error': 'bad json: request must be a JSON object'})
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
