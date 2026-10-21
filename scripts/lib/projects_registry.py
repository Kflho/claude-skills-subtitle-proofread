#!/usr/bin/env python3
"""Global project registry: one file that knows every subtitle project.

House rule (this replaced per-project ``project.json``): a project folder holds
**content** — subtitles, reports, docs — and nothing about how this machine is
configured.  Which series a project is, where its media sits, its style, its
subtitle stage dirs, its download dir: all of that lives in one machine-local
file next to ``local.json``::

    <skill>/projects_registry.json        (gitignored)

    {
      "schema_version": 1,
      "projects": {
        "赤胴铃之助": {
          "series": "赤胴铃之助",
          "media_type": "tv",
          "category": "animation",
          "video_dir": "",                     # empty = derive from paths.media
          "style": "江户时代剑戟动画……",
          "source_lang": "ja",
          "target_lang": "zh-CN",
          "subtitles": {
            "input_dir": "data/ja",            # relative to the project root
            "output_dir": "data/zh",
            "reference_dir": "bin/reference_docs/参考字幕"
          },
          "bili23": {"source_url": "", "download_dir": ""},
          "notes": []
        }
      }
    }

Keys are project folder names (the folder under ``projects_root``); an entry may
carry ``"path"`` when a project lives somewhere else.  Relative values are
resolved against the project root by :func:`abs_path`, so the registry reads the
same whether you pass a project dir or run from inside it.

Resolution order stays ``CLI > registry > local.json > derived default``;
:mod:`lib.projects_index` prints the resolved table.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from lib.local_config import config_path as local_config_path

REGISTRY_FILENAME = 'projects_registry.json'
SCHEMA_VERSION = 1


def registry_path() -> Path:
    """``projects_registry.json`` beside ``local.json`` (machine-local)."""
    return local_config_path().parent / REGISTRY_FILENAME


def _empty() -> dict:
    return {'schema_version': SCHEMA_VERSION, 'projects': {}}


def load(path: Path | None = None, refresh: bool = False) -> dict:
    global _cache
    if path is None and _cache is not None and not refresh:
        return _cache
    target = path or registry_path()
    data = _empty()
    try:
        if target.is_file():
            raw = json.loads(target.read_text(encoding='utf-8-sig'))
            if isinstance(raw, dict):
                data.update(raw)
    except (OSError, json.JSONDecodeError):
        data = _empty()
    if not isinstance(data.get('projects'), dict):
        data['projects'] = {}
    data.setdefault('schema_version', SCHEMA_VERSION)
    if path is None:
        _cache = data
    return data


def save(data: dict, path: Path | None = None) -> Path:
    global _cache
    target = path or registry_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n',
                      encoding='utf-8', newline='\n')
    if path is None:
        _cache = data
    return target


_cache: dict | None = None


# ── looking a project up ────────────────────────────────────────────────


def project_dir(project: str | Path | None = None) -> str:
    """Absolute project root: *project* arg > ``SUBTITLE_PROJECT`` > cwd."""
    if project:
        return os.path.abspath(os.path.expanduser(str(project)))
    env = os.environ.get('SUBTITLE_PROJECT', '').strip()
    return os.path.abspath(os.path.expanduser(env)) if env else os.getcwd()


def project_key(project: str | Path | None = None) -> str:
    return os.path.basename(project_dir(project).rstrip('\\/'))


def entry(project: str | Path | None = None, data: dict | None = None) -> dict:
    """Registry entry for *project* (``{}`` when it was never registered)."""
    data = data if data is not None else load()
    projects = data.get('projects') or {}
    key = project_key(project)
    found = projects.get(key)
    if isinstance(found, dict):
        return found
    # A project may live outside projects_root and be registered by full path.
    root = project_dir(project)
    for value in projects.values():
        if isinstance(value, dict) and str(value.get('path') or '').strip():
            if os.path.normcase(os.path.abspath(str(value['path']))) == os.path.normcase(root):
                return value
    return {}


def value(project: str | Path | None = None, *keys, default=None):
    node = entry(project)
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def set_value(project: str | Path | None, *keys, value=None) -> Path:
    """Write ``keys`` under this project's entry, creating it as needed."""
    data = load()
    key = project_key(project)
    projects = data.setdefault('projects', {})
    node = projects.setdefault(key, {})
    if not isinstance(node, dict):
        node = projects[key] = {}
    for part in keys[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = node[part] = {}
        node = child
    node[keys[-1]] = value
    return save(data)


def register(project: str | Path | None, **fields) -> Path:
    """Merge flat ``fields`` into the entry (ignoring empty values)."""
    data = load()
    node = data.setdefault('projects', {}).setdefault(project_key(project), {})
    if not isinstance(node, dict):
        node = data['projects'][project_key(project)] = {}
    for name, val in fields.items():
        if val not in (None, '', [], {}):
            node[name] = val
    return save(data)


def abs_path(project: str | Path | None, value: str) -> str:
    """Resolve a registry path value against the project root when relative."""
    text = os.path.expanduser(str(value or '').strip())
    if not text:
        return ''
    if os.path.isabs(text):
        return text
    return os.path.normpath(os.path.join(project_dir(project), text))


# ── CLI: inspect / edit the registry by hand ───────────────────────────


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description='inspect the global project registry')
    ap.add_argument('--path', action='store_true', help='print the registry path')
    ap.add_argument('--project', default='', help='project dir to show')
    ap.add_argument('--json', action='store_true', help='machine-readable output')
    args = ap.parse_args(argv)

    if args.path:
        print(registry_path())
        return 0
    if args.project:
        payload = entry(args.project)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    data = load()
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    print(f'registry: {registry_path()}')
    for key, item in sorted((data.get('projects') or {}).items()):
        series = str((item or {}).get('series') or key)
        kind = str((item or {}).get('media_type') or 'tv')
        print(f'{key:<16}{kind:<6}{series}')
    return 0


if __name__ == '__main__':
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.exit(main())
