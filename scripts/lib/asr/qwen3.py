"""Backward-compatible import shim for the Qwen3 adapter.

New code should import from lib.asr.adapters.qwen3 or use the registry.
"""

from lib.asr.adapters.qwen3 import (  # noqa: F401
    INFO, KIND, NAME, Qwen3ASRBackend, Qwen3WorkerClient,
    default_worker_script, build,
)

__all__ = ['INFO', 'KIND', 'NAME', 'Qwen3ASRBackend', 'Qwen3WorkerClient',
           'default_worker_script', 'build']
