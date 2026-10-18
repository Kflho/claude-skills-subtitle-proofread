"""Backward-compatible import shim for the Whisper adapter.

New code should import from lib.asr.adapters.whisper or use the registry.
"""

from lib.asr.adapters.whisper import (  # noqa: F401
    INFO, KIND, NAME, WhisperASRBackend, _lang_code, build,
)

__all__ = ['INFO', 'KIND', 'NAME', 'WhisperASRBackend', '_lang_code', 'build']
