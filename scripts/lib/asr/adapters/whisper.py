#!/usr/bin/env python3
"""Whisper fallback adapter — exposes the legacy pipeline as an ASRBackend.

The old Whisper path remains the default until P2 cue repair lands.  This
adapter lets the router and evaluation harness compare Qwen3 vs Whisper
using the same ASRResult contract.

Usage:
    from lib.asr.whisper import WhisperASRBackend
    backend = WhisperASRBackend(model_path='.../ggml-kotoba-q5_0.bin')
    result = backend.transcribe('ep.wav', language='ja')
"""

from __future__ import annotations

import sys

from lib.asr.base import (
    ASRConfig, ASRError, ASRResult, BackendInfo, Segment, language_name,
)
from lib.config import WHISPER_BACKEND, WHISPER_CLI, WHISPER_MODEL
from lib.whisper_backends import detect_available_backends, transcribe as whisper_transcribe


NAME = 'whisper'
KIND = 'asr'
INFO = BackendInfo(
    name=NAME,
    kind=KIND,
    version='legacy',
    capabilities=('segments',),
    auto_priority=0,
    description='Legacy Whisper fallback (segment-level timestamps; no tokens).',
)


def _lang_code(language: str) -> str:
    """Map 'Japanese' / 'ja' to the two-letter code Whisper expects."""
    value = (language or 'ja').strip().lower()
    mapping = {
        'japanese': 'ja', 'ja': 'ja', 'jp': 'ja',
        'chinese': 'zh', 'zh': 'zh',
        'english': 'en', 'en': 'en',
        'korean': 'ko', 'ko': 'ko',
    }
    return mapping.get(value, value[:2] or 'ja')


class WhisperASRBackend:
    """ASRBackend implementation over lib.whisper_backends.transcribe()."""

    name = 'whisper'

    def __init__(self, *, model_path: str | None = None,
                 backend: str | None = None,
                 whisper_cli: str | None = None,
                 response_language: str | None = None):
        self.model_path = model_path or WHISPER_MODEL
        self.backend = backend or WHISPER_BACKEND or None
        self.whisper_cli = whisper_cli or WHISPER_CLI or None
        self.response_language = response_language or 'Japanese'

    def available(self) -> bool:
        if not self.model_path:
            return False
        try:
            backends = detect_available_backends()
        except Exception as exc:  # pragma: no cover - defensive
            print(f'[whisper] backend detection failed: {exc}', file=sys.stderr)
            return False
        if not backends:
            return False
        return self.backend is None or self.backend in backends

    def transcribe(self, audio_path: str, *, language: str = 'ja',
                   context: str = '', timestamps: bool = False) -> ASRResult:
        """Transcribe via Whisper.

        ``timestamps`` is accepted for interface parity but Whisper only
        returns segment-level times, so tokens stay empty.  ``context`` is
        ignored (Whisper.cpp has no equivalent prompt in this backend).
        """
        if not self.model_path:
            raise ASRError('WHISPER_MODEL is empty; cannot run Whisper fallback')

        segments_raw = whisper_transcribe(
            audio_path,
            self.model_path,
            language=_lang_code(language),
            backend=self.backend,
            whisper_cli=self.whisper_cli,
        )
        if not segments_raw:
            raise ASRError(
                f'Whisper produced no segments for {audio_path} '
                f'(backend={self.backend or "auto"}, model={self.model_path})'
            )

        segments: list[Segment] = []
        for raw in segments_raw:
            text = str(raw.get('text', '')).strip()
            if not text:
                continue
            start = float(raw.get('start_s', 0.0))
            end = float(raw.get('end_s', start))
            segments.append(Segment(text=text, start=start, end=end))

        if not segments:
            raise ASRError(f'Whisper returned only empty segments for {audio_path}')

        joiner = '' if _lang_code(language) == 'ja' else ' '
        text = joiner.join(s.text for s in segments)
        duration = max((s.end for s in segments), default=0.0)

        return ASRResult(
            text=text,
            language=language_name(self.response_language or language),
            segments=segments,
            tokens=[],
            engine=self.name,
            model=self.model_path,
            audio_duration=duration,
            meta={
                'backend': self.backend or 'auto',
                'timestamps': False,
                'note': 'Whisper segment timestamps only; run Qwen3-FA for token times.',
            },
        )


def build(config: ASRConfig, **kwargs) -> WhisperASRBackend:
    """Registry factory: the only Whisper-specific glue the core calls."""
    return WhisperASRBackend(
        model_path=(kwargs.get('model_path')
                    or config.option('whisper_model')
                    or config.model or WHISPER_MODEL),
        backend=(kwargs.get('backend')
                 or config.option('whisper_backend')
                 or WHISPER_BACKEND or None),
        whisper_cli=(kwargs.get('whisper_cli')
                     or config.option('whisper_cli')
                     or WHISPER_CLI or None),
    )
