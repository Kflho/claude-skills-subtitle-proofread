#!/usr/bin/env python3
"""Per-project config (``project.json``) for subtitle work.

Two config tiers:

- **global/machine**: ``local.json`` next to SKILL.md (paths to models, ffmpeg,
  API endpoints, tool installs) — rarely changes, never committed;
- **per-project**: ``project.json`` at the project root (download dir, target
  language/style, staging paths) — changes with every project.

Precedence for a project value: CLI argument > project.json > local.json
(global fallback) > default.  ``project.json`` is looked up from ``--project``
(or ``SUBTITLE_PROJECT``) upwards from the current directory.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

PROJECT_FILE = 'project.json'


def find_project(start: str | Path | None = None) -> Path | None:
    """Return the nearest project.json walking up from *start* (or cwd)."""
    if start:
        base = Path(start).expanduser().resolve()
    else:
        env = os.environ.get('SUBTITLE_PROJECT', '').strip()
        base = Path(env).expanduser().resolve() if env else Path.cwd().resolve()
    if base.is_file():
        return base if base.name == PROJECT_FILE else None
    for parent in (base, *base.parents):
        candidate = parent / PROJECT_FILE
        if candidate.is_file():
            return candidate
    return None


def load(path: Path | None) -> dict:
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def get(path: Path | None, *keys, default=None):
    node = load(path)
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def set_value(path: Path, *keys, value) -> None:
    data = load(path)
    node = data
    for key in keys[:-1]:
        child = node.get(key)
        if not isinstance(child, dict):
            child = {}
            node[key] = child
        node = child
    node[keys[-1]] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n',
                    encoding='utf-8', newline='\n')


def resolve_path(*keys, project: Path | None = None, default: str = '') -> str:
    value = get(project, *keys)
    if isinstance(value, str) and value.strip():
        return value
    return default
