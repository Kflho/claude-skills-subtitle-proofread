#!/usr/bin/env python3
"""Qwen3-ForcedAligner adapter — plugin form of the token-time aligner.

P2's cue mapper consumes the shared AlignResult contract; it never imports
Qwen3 directly.  A future aligner only needs another file in this package.
"""

from __future__ import annotations

from lib.asr.base import (
    ASRConfig, AlignResult, BackendInfo,
)
from lib.asr.adapters.qwen3 import Qwen3WorkerClient
from lib.config import ALIGNER_MODEL, ASR_DEVICE, ASR_MODEL, QWEN_PYTHON


NAME = 'qwen3_fa'
KIND = 'aligner'
INFO = BackendInfo(
    name=NAME,
    kind=KIND,
    version='0.0.6',
    capabilities=('token_timestamps', 'japanese'),
    auto_priority=10,
    description='Qwen3-ForcedAligner-0.6B via the Qwen3 worker.',
)


class Qwen3ForcedAlignerAdapter:
    """Aligner contract over a Qwen3 worker client."""

    name = NAME

    def __init__(self, client: Qwen3WorkerClient):
        self.client = client

    def available(self) -> bool:
        return self.client.available()

    def align(self, audio_path: str, text: str, *,
              language: str = 'ja') -> AlignResult:
        if self.client._proc is None or self.client._proc.poll() is not None:
            self.client.start()
        return self.client.align(audio_path, text, language=language)

    def close(self) -> None:
        self.client.close()


def build(config: ASRConfig, **kwargs) -> Qwen3ForcedAlignerAdapter:
    client = kwargs.pop('client', None)
    if client is None:
        client = Qwen3WorkerClient(
            python_exe=kwargs.pop('python_exe', None) or config.python
            or QWEN_PYTHON or None,
            asr_model=kwargs.pop('asr_model', None) or ASR_MODEL,
            aligner_model=(kwargs.pop('aligner_model', None)
                           or config.aligner_model or ALIGNER_MODEL),
            device=kwargs.pop('device', None) or config.device or ASR_DEVICE,
            dtype=kwargs.pop('dtype', None) or config.dtype,
            **kwargs,
        )
    return Qwen3ForcedAlignerAdapter(client)
