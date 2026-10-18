#!/usr/bin/env python3
"""Adapter registry — the stable core discovers model-specific plugins.

Adding a model should NOT touch the router, CLI, evaluator, or pipeline.
Drop a module into ``lib/asr/adapters/`` with:

    NAME = "my_model"
    KIND = "asr"                     # or "aligner"
    INFO = BackendInfo(...)
    def build(config, **kwargs):     # config: ASRConfig
        return MyModelBackend(...)

The registry imports every non-underscore module in that package and reads
those attributes.  Capability checks, fallback, CLI ``--engine`` validation,
and evaluation all go through this registry.
"""

from __future__ import annotations

import importlib
import pkgutil
import threading
from dataclasses import dataclass
from typing import Any, Callable

from lib.asr.base import ASRConfig, BackendInfo


@dataclass(frozen=True)
class PluginEntry:
    name: str
    kind: str
    info: BackendInfo
    factory: Callable[..., Any]


_LOCK = threading.RLock()
_ENTRIES: dict[str, PluginEntry] = {}
_DISCOVERED = False


def register(name: str, factory: Callable[..., Any], *,
             kind: str = 'asr', info: BackendInfo | None = None,
             replace: bool = False) -> None:
    """Register one adapter factory (idempotent unless replace=True)."""
    if not name:
        raise ValueError('adapter name is empty')
    if not callable(factory):
        raise TypeError(f'adapter {name!r} factory is not callable')
    with _LOCK:
        if name in _ENTRIES and not replace:
            return
        _ENTRIES[name] = PluginEntry(
            name=name,
            kind=kind,
            info=info or BackendInfo(name=name, kind=kind),
            factory=factory,
        )


def discover(force: bool = False) -> None:
    """Import all adapter modules and register their NAME/build/INFO."""
    global _DISCOVERED
    with _LOCK:
        if _DISCOVERED and not force:
            return
        package = importlib.import_module('lib.asr.adapters')
        for modinfo in pkgutil.iter_modules(package.__path__):
            if modinfo.name.startswith('_'):
                continue
            module = importlib.import_module(f'lib.asr.adapters.{modinfo.name}')
            factory = getattr(module, 'build', None)
            if not callable(factory):
                continue
            name = getattr(module, 'NAME', modinfo.name)
            kind = getattr(module, 'KIND', 'asr')
            info = getattr(module, 'INFO', None)
            if not isinstance(info, BackendInfo):
                info = BackendInfo(name=name, kind=kind)
            register(name, factory, kind=kind, info=info)
        _DISCOVERED = True


def get(name: str) -> PluginEntry:
    discover()
    entry = _ENTRIES.get(name)
    if entry is None:
        known = ', '.join(sorted(_ENTRIES)) or '(none)'
        raise KeyError(f'unknown adapter {name!r}; known: {known}')
    return entry


def entries(kind: str | None = None) -> list[PluginEntry]:
    discover()
    values = list(_ENTRIES.values())
    if kind:
        values = [e for e in values if e.kind == kind]
    return sorted(values, key=lambda e: (-e.info.auto_priority, e.name))


def names(kind: str | None = None) -> list[str]:
    return [e.name for e in entries(kind)]


def build(name: str, *, config: ASRConfig | None = None, **kwargs):
    """Build one adapter.  Extra kwargs are forwarded to its factory."""
    entry = get(name)
    config = config or ASRConfig()
    return entry.factory(config, **kwargs)


def available(kind: str = 'asr', *, config: ASRConfig | None = None) -> list[str]:
    """Names whose adapter reports available(); never raises for one adapter."""
    config = config or ASRConfig()
    out = []
    for entry in entries(kind):
        try:
            backend = entry.factory(config)
            if backend.available():
                out.append(entry.name)
        except Exception:
            continue
    return out


def auto_candidates(kind: str = 'asr', *, config: ASRConfig | None = None) -> list[str]:
    """Available adapters ordered by INFO.auto_priority (highest first)."""
    config = config or ASRConfig()
    ranked = entries(kind)
    out = []
    for entry in ranked:
        try:
            backend = entry.factory(config)
            if backend.available():
                out.append(entry.name)
        except Exception:
            continue
    return out


def reset_for_tests() -> None:
    """Clear the registry (tests only)."""
    global _DISCOVERED
    with _LOCK:
        _ENTRIES.clear()
        _DISCOVERED = False
