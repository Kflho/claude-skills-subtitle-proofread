#!/usr/bin/env python3
"""ASR engine router — stable core over pluggable adapters.

The router never imports a model directly.  It asks lib.asr.registry for
the adapter named by ``engine`` and calls the shared ASRBackend contract.
Adding an engine = adding lib/asr/adapters/<name>.py; no changes here.

New code should pass an ASRConfig.  The qwen_*/whisper_* keyword arguments
are compatibility shims for older callers; do not add a new one per model.

Usage:
    from lib.asr.router import ASRRouter
    router = ASRRouter(engine='qwen3', fallback='whisper')
    result = router.transcribe('ep.wav', language='ja', timestamps=True)
"""

from __future__ import annotations

import sys

from lib.asr.base import ASRConfig, ASRError, ASRResult
from lib.asr import registry
from lib.config import (
    ASR_DEVICE, ASR_DTYPE, ASR_ENGINE, ASR_FALLBACK, ASR_MAX_NEW_TOKENS,
    ASR_MODEL, ASR_REQUEST_TIMEOUT, ALIGNER_MODEL, QWEN_PYTHON,
    WHISPER_BACKEND, WHISPER_CLI, WHISPER_MODEL,
)


def _make_config(*, qwen_python=None, qwen_model=None, aligner_model=None,
                 whisper_model=None, whisper_backend=None, whisper_cli=None,
                 device=None, dtype=None, request_timeout=None) -> ASRConfig:
    options = {
        'whisper_model': whisper_model or WHISPER_MODEL,
        'whisper_backend': whisper_backend or WHISPER_BACKEND or '',
        'whisper_cli': whisper_cli or WHISPER_CLI or '',
    }
    return ASRConfig(
        model=qwen_model or ASR_MODEL,
        aligner_model=aligner_model or ALIGNER_MODEL,
        python=qwen_python or QWEN_PYTHON or '',
        device=device or ASR_DEVICE,
        dtype=dtype or ASR_DTYPE,
        request_timeout=request_timeout or ASR_REQUEST_TIMEOUT,
        max_new_tokens=ASR_MAX_NEW_TOKENS,
        options=options,
    )


def build_backend(engine: str, *, config: ASRConfig | None = None, **kwargs):
    """Build one adapter through the registry (kept as a seam for tests)."""
    return registry.build(engine, config=config or ASRConfig(), **kwargs)


class ASRRouter:
    """Select an adapter and fall back on failure."""

    def __init__(self, engine: str | None = None,
                 fallback: str | None = None,
                 *, qwen_python: str | None = None,
                 qwen_model: str | None = None,
                 aligner_model: str | None = None,
                 whisper_model: str | None = None,
                 whisper_backend: str | None = None,
                 whisper_cli: str | None = None,
                 device: str | None = None,
                 dtype: str | None = None,
                 request_timeout: float | None = None,
                 config: ASRConfig | None = None,
                 backend_kwargs: dict[str, dict] | None = None):
        self.requested_engine = (engine or ASR_ENGINE or 'whisper').lower()
        self.fallback = (fallback if fallback is not None
                         else ASR_FALLBACK or '').lower()
        self.config = config or _make_config(
            qwen_python=qwen_python, qwen_model=qwen_model,
            aligner_model=aligner_model, whisper_model=whisper_model,
            whisper_backend=whisper_backend, whisper_cli=whisper_cli,
            device=device, dtype=dtype, request_timeout=request_timeout,
        )
        self.backend_kwargs = dict(backend_kwargs or {})
        self.last_engine = ''
        self._backends: dict[str, object] = {}

    # ── selection ────────────────────────────────────────────

    def resolve_engine(self) -> str:
        wanted = self.requested_engine
        if wanted in ('auto', ''):
            candidates = registry.auto_candidates('asr', config=self.config)
            if candidates:
                return candidates[0]
            known = registry.names('asr')
            # Preserve the legacy default when nothing advertises itself.
            return 'whisper' if 'whisper' in known else (known[0] if known else 'none')
        if wanted == 'none':
            return 'none'
        if wanted not in registry.names('asr'):
            raise ValueError(
                f'unknown ASR engine: {wanted!r}; '
                f'known: {", ".join(registry.names("asr")) or "(none)"}')
        return wanted

    def _get(self, engine: str):
        if engine in self._backends:
            return self._backends[engine]
        extra = self.backend_kwargs.get(engine, {})
        backend = build_backend(engine, config=self.config, **extra)
        self._backends[engine] = backend
        return backend

    # ── public API ──────────────────────────────────────────

    def warmup(self) -> None:
        """Load the selected model once (excluded from batch timing)."""
        primary = self.resolve_engine()
        if primary == 'none':
            return
        backend = self._get(primary)
        warm = getattr(backend, 'warmup', None)
        if callable(warm):
            warm()

    def transcribe(self, audio_path: str, *, language: str = 'ja',
                   context: str = '', timestamps: bool = True) -> ASRResult:
        primary = self.resolve_engine()
        if primary == 'none':
            raise ASRError('ASR engine is "none"')

        try:
            result = self._get(primary).transcribe(
                audio_path, language=language, context=context,
                timestamps=timestamps)
            self.last_engine = primary
            return result
        except Exception as exc:
            if not self.fallback or self.fallback in ('none', primary):
                raise
            print(f'[asr] {primary} failed ({exc}); falling back to '
                  f'{self.fallback}', file=sys.stderr)
            result = self._get(self.fallback).transcribe(
                audio_path, language=language, context=context,
                timestamps=timestamps)
            result.meta = dict(result.meta or {})
            result.meta['fallback_from'] = primary
            result.meta['fallback_reason'] = str(exc)[:500]
            self.last_engine = self.fallback
            return result

    def close(self) -> None:
        for backend in self._backends.values():
            try:
                backend.close()  # type: ignore[attr-defined]
            except Exception:
                pass
        self._backends.clear()

    def __enter__(self) -> 'ASRRouter':
        return self

    def __exit__(self, *exc) -> None:
        self.close()
