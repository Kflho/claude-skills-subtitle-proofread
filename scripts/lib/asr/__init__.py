"""ASR adapters and engine-neutral contracts (2026 tech route).

Stable core:
    base.py       — Token/Segment/ASRResult/ASRConfig/BackendInfo/Protocols
    registry.py   — discovers lib/asr/adapters/* and builds them
    router.py     — engine selection + fallback
Adapters:
    lib/asr/adapters/<name>.py — add a model here; no core changes.
"""

from lib.asr.base import (
    AlignResult, Aligner, ASRBackend, ASRConfig, ASRError, ASRResult,
    BackendInfo, CONTRACT_VERSION, Segment, Token, build_segments,
    language_name, validate_aligner, validate_backend,
)

__all__ = [
    'AlignResult', 'Aligner', 'ASRBackend', 'ASRConfig', 'ASRError',
    'ASRResult', 'BackendInfo', 'CONTRACT_VERSION', 'Segment', 'Token',
    'build_segments', 'language_name', 'validate_aligner',
    'validate_backend',
]
