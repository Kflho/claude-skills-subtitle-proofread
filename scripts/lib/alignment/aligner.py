#!/usr/bin/env python3
"""Aligner service over the plugin registry (engine-agnostic).

The P2 plan (§7.1) sketched ``lib/alignment/qwen3_fa.py``.  P1 already
shipped the same thing as a registry plugin (``lib/asr/adapters/
qwen3_aligner.py``, ``KIND='aligner'``), so this module is a thin service
instead of a second adapter: it never imports a model, and a new aligner
is still "drop a file into ``lib/asr/adapters/``".

``ALIGNER_ENGINE=none`` is a first-class value: it disables alignment so
the old pipeline keeps working while P2 is evaluated.

Usage:
    from lib.alignment.aligner import AlignerService
    with AlignerService() as svc:
        result = svc.align('cue_0012.wav', 'お前、何してんだよ！')
"""

from __future__ import annotations

from lib.alignment.base import AlignmentError
from lib.asr import registry
from lib.asr.base import ASRConfig, AlignResult
from lib.config import ALIGNER_ENGINE, ALIGNER_MODEL, ASR_DEVICE, ASR_DTYPE, QWEN_PYTHON


def _default_config() -> ASRConfig:
    return ASRConfig(
        aligner_model=ALIGNER_MODEL,
        python=QWEN_PYTHON or '',
        device=ASR_DEVICE,
        dtype=ASR_DTYPE,
    )


class AlignerService:
    """Select an aligner adapter by name and expose ``align()``."""

    def __init__(self, engine: str | None = None, *,
                 config: ASRConfig | None = None,
                 backend_kwargs: dict | None = None):
        self.requested_engine = (engine or ALIGNER_ENGINE or 'none').lower()
        self.config = config or _default_config()
        self.backend_kwargs = dict(backend_kwargs or {})
        self.last_engine = ''
        self._backend = None

    # ── selection ───────────────────────────────────────────

    def resolve_engine(self) -> str:
        wanted = self.requested_engine
        if wanted in ('auto', ''):
            candidates = registry.auto_candidates('aligner', config=self.config)
            return candidates[0] if candidates else 'none'
        if wanted == 'none':
            return 'none'
        known = registry.names('aligner')
        if wanted not in known:
            raise AlignmentError(
                f'unknown aligner engine: {wanted!r}; '
                f'known: {", ".join(known) or "(none)"}')
        return wanted

    @property
    def enabled(self) -> bool:
        return self.resolve_engine() != 'none'

    def backend(self):
        if self._backend is None:
            engine = self.resolve_engine()
            if engine == 'none':
                raise AlignmentError('aligner engine is "none"')
            self._backend = registry.build(
                engine, config=self.config,
                **self.backend_kwargs.get(engine, {}))
        return self._backend

    def available(self) -> bool:
        engine = self.resolve_engine()
        if engine == 'none':
            return False
        try:
            return bool(self.backend().available())
        except Exception:
            return False

    # ── public API ──────────────────────────────────────────

    def align(self, audio_path: str, text: str, *,
              language: str = 'ja') -> AlignResult:
        if not (text or '').strip():
            raise AlignmentError('cannot align empty text')
        backend = self.backend()
        result = backend.align(audio_path, text, language=language)
        if not isinstance(result, AlignResult):
            raise AlignmentError(
                f'aligner {backend.name!r} returned {type(result).__name__}, '
                'expected AlignResult')
        self.last_engine = backend.name
        return result

    def close(self) -> None:
        if self._backend is not None:
            closer = getattr(self._backend, 'close', None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    pass
            self._backend = None

    def __enter__(self) -> 'AlignerService':
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def build_aligner(engine: str | None = None, **kwargs) -> AlignerService:
    """Factory kept as a seam for tests/callers."""
    return AlignerService(engine, **kwargs)
