#!/usr/bin/env python3
"""ASR adapter template — copy to scripts/lib/asr/adapters/<name>.py.

Rules
-----
1. Do NOT change lib/asr/base.py, registry.py, router.py, or the CLI when
   adding a model.  The registry auto-discovers every non-underscore module
   in scripts/lib/asr/adapters/.
2. Expose NAME, KIND, INFO, and build(config, **kwargs).
3. build() must be cheap (no model load); load inside the backend on first
   transcribe(), or in a worker process.
4. Implement the ASRBackend contract:
       name: str
       available() -> bool
       transcribe(audio_path, *, language='ja', context='',
                  timestamps=False) -> ASRResult
5. Return the shared ASRResult / Token / Segment types.  Do not invent a
   new JSON shape; if you need extras, put them in ASRResult.meta.
6. Add tests that call lib.asr.base.validate_backend() and a fake/mock
   transcribe path.  No real model download in unit tests.

After adding this file, run:
    python scripts/asr/qwen3_transcribe.py --list-engines
    python scripts/asr/qwen3_transcribe.py --engine <name> --audio sample.wav
"""

from __future__ import annotations

from lib.asr.base import (
    ASRConfig, ASRResult, BackendInfo, Segment, Token,
)

# ── Registry metadata ──────────────────────────────────────────────
NAME = 'my_model'
KIND = 'asr'  # 'asr' | 'aligner'

INFO = BackendInfo(
    name=NAME,
    kind=KIND,
    version='0.1.0',
    capabilities=('timestamps', 'context'),  # or ('segments',)
    auto_priority=5,       # higher = more likely picked by --engine auto
    requires_env=(),       # e.g. ('MY_MODEL_API_KEY',)
    description='One-line description for --list-engines.',
)


class MyModelBackend:
    """Implement the shared ASRBackend contract."""

    name = NAME

    def __init__(self, config: ASRConfig):
        self.config = config
        self._loaded = False

    def available(self) -> bool:
        """Cheap check only.  Do not download/load the model here."""
        return True

    def _ensure_loaded(self):
        if self._loaded:
            return
        # import heavy deps here, not at module import time
        # from my_model import load_model
        # self._model = load_model(self.config.model)
        self._loaded = True

    def transcribe(self, audio_path: str, *, language: str = 'ja',
                   context: str = '', timestamps: bool = False) -> ASRResult:
        self._ensure_loaded()
        # Replace with the real engine call.
        text = ''
        return ASRResult(
            text=text,
            language=language,
            segments=[Segment(text=text, start=0.0, end=0.0)],
            tokens=[],
            engine=NAME,
            model=self.config.model,
            audio_duration=0.0,
            meta={'timestamps': bool(timestamps)},
        )


def build(config: ASRConfig, **kwargs) -> MyModelBackend:
    """Registry factory — keep this signature stable."""
    return MyModelBackend(config)
