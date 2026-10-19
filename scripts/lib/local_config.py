#!/usr/bin/env python3
"""Machine-local configuration for the harness copy of this skill.

The repository stays free of machine paths and keys.  When the skill is synced
into the harness (``~/.agents/skills/subtitle-proofread``), ``init_local.py``
writes ``local.json`` there with this machine's model paths, venv, ffmpeg and
LLM settings.  Resolution order for every value:

    environment variable  >  local.json  >  built-in default

``SKILL_LOCAL_CONFIG`` can point at a different local.json (tests, dev repo).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_SKILL_ROOT = Path(__file__).resolve().parents[2]


def config_path() -> Path:
    override = os.environ.get('SKILL_LOCAL_CONFIG', '').strip()
    if override:
        return Path(override)
    return _SKILL_ROOT / 'local.json'


_cache: dict | None = None


def load_local(refresh: bool = False) -> dict:
    global _cache
    if _cache is not None and not refresh:
        return _cache
    data: dict = {}
    path = config_path()
    try:
        if path.is_file():
            raw = json.loads(path.read_text(encoding='utf-8-sig'))
            if isinstance(raw, dict):
                data = raw
    except (OSError, json.JSONDecodeError):
        data = {}
    _cache = data
    return _cache


def local_value(*keys, default=None):
    """Read a nested key path from local.json (``local_value('paths','models_dir')``)."""
    node = load_local()
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def pick(env_name: str, *local_keys, default=''):
    """env var > local.json nested value > default."""
    env = os.environ.get(env_name)
    if env is not None and str(env).strip() != '':
        return env
    value = local_value(*local_keys, default=None)
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    return value
