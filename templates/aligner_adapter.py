#!/usr/bin/env python3
"""Aligner adapter template — copy to scripts/lib/asr/adapters/<name>.py.

Rules
-----
1. Do NOT change the core for a new aligner.  Registry discovers this
   module by NAME/KIND/INFO/build.
2. KIND must be "aligner".
3. Implement the Aligner contract:
       name: str
       available() -> bool
       align(audio_path, text, *, language='ja') -> AlignResult
4. Return shared AlignResult/Token types; extras go in meta.
5. Unit tests must not download/load the model.
"""

from __future__ import annotations

from lib.asr.base import ASRConfig, AlignResult, BackendInfo, Token

NAME = 'my_aligner'
KIND = 'aligner'
INFO = BackendInfo(
    name=NAME,
    kind=KIND,
    version='0.1.0',
    capabilities=('token_timestamps',),
    auto_priority=5,
    description='One-line description for registry listings.',
)


class MyAligner:
    name = NAME

    def __init__(self, config: ASRConfig):
        self.config = config

    def available(self) -> bool:
        """Cheap check only; do not load the model here."""
        return True

    def align(self, audio_path: str, text: str, *,
              language: str = 'ja') -> AlignResult:
        # Replace with the real engine call.
        return AlignResult(
            text=text,
            language=language,
            tokens=[Token(text=text, start=0.0, end=0.0)],
            meta={'engine': NAME},
        )


def build(config: ASRConfig, **kwargs) -> MyAligner:
    return MyAligner(config)
