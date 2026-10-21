#!/usr/bin/env python3
"""Index every subtitle project on this machine and where its media lives.

The rule this enforces: a project **folder** holds content, not config.  Which
series it is, its type, style and any folder override come from the global
registry (``projects_registry.json`` beside ``local.json``, see
:mod:`lib.projects_registry`); the media folder itself comes from ``local.json``
→ ``paths.media``.  A row's ``video_source`` says which of those answered, and
``video_drift`` flags the folders that break the library rule.

Writes ``projects_index.json`` next to ``local.json`` (gitignored, machine
local) and prints a table.

    python scripts/lib/projects_index.py --root <projects-root>
    python scripts/lib/projects_index.py --check      # re-resolve, no write
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from lib import media_paths, projects_registry
from lib.local_config import config_path as local_config_path
from lib.local_config import local_value

MARKERS = ('CLAUDE.md',)


def index_path() -> Path:
    return local_config_path().parent / 'projects_index.json'


def projects_root(explicit: str = '') -> str:
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    configured = str(local_value('projects_root', default='') or '').strip()
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    return ''


def _count_media(root: str) -> dict:
    if not root or not os.path.isdir(root):
        return {'exists': False, 'files': 0, 'size_gb': 0.0}
    files = 0
    size = 0
    for current, _dirs, names in os.walk(root):
        for name in names:
            try:
                size += os.path.getsize(os.path.join(current, name))
                files += 1
            except OSError:
                pass
    return {'exists': True, 'files': files, 'size_gb': round(size / 1024 ** 3, 2)}


VIDEO_EXT = ('.mp4', '.mkv', '.avi', '.ts', '.m2ts', '.webm', '.mov')


def _media_count(path: str) -> int:
    if not os.path.isdir(path):
        return 0
    total = 0
    for _current, _dirs, names in os.walk(path):
        total += sum(1 for n in names if n.lower().endswith(VIDEO_EXT))
    return total


PATH_RE = re.compile(r'[A-Za-z]:\\[^`|\r\n]+')


def _paths_in_claude_md(project_dir: Path) -> list[str]:
    """Absolute paths an old project documented in its CLAUDE.md.

    Folder names here contain spaces, brackets and CJK, so split on the table
    cell / backtick delimiters instead of whitespace, then trim the trailing
    separator the docs usually leave on a directory path.
    """
    doc = project_dir / 'CLAUDE.md'
    if not doc.is_file():
        return []
    try:
        text = doc.read_text(encoding='utf-8', errors='ignore')
    except OSError:
        return []
    out = []
    for match in PATH_RE.finditer(text):
        candidate = match.group(0).strip()
        while candidate and not os.path.isdir(candidate):
            trimmed = candidate.rstrip(' \\/`|\t')
            if trimmed == candidate:
                break
            candidate = trimmed
        if candidate and os.path.isdir(candidate) and candidate not in out:
            out.append(candidate)
    return out


def _tokens(name: str) -> set[str]:
    keep = ''.join(ch if ch.isalnum() else ' ' for ch in name)
    return {tok for tok in keep.lower().split() if tok}


def _fuzzy_video_dir(series: str, media_type: str) -> tuple[str, int]:
    """Best folder under the kind dir whose name shares tokens with *series*."""
    base = media_paths.kind_dir(media_type)
    if not base or not os.path.isdir(base):
        return '', 0
    wanted = _tokens(series)
    best, best_score = '', 0
    for entry in os.listdir(base):
        full = os.path.join(base, entry)
        if not os.path.isdir(full):
            continue
        score = len(wanted & _tokens(entry))
        files = _media_count(full)
        if score and files and (score, files) > (best_score, 0):
            best, best_score = full, score
    return best, best_score


def describe(project_dir: Path) -> dict:
    """One table row: identity from the registry, folders resolved globally.

    The project folder is only scanned for media as a last resort — everything
    declarative (series, type, style, overrides, stage dirs) comes from
    ``projects_registry.json`` so no config lives inside the project.
    """
    entry = projects_registry.entry(str(project_dir))
    video_block = entry.get('video') if isinstance(entry.get('video'), dict) else {}
    subs_block = entry.get('subtitles') if isinstance(entry.get('subtitles'), dict) else {}
    bili_block = entry.get('bili23') if isinstance(entry.get('bili23'), dict) else {}

    series = str(entry.get('series') or video_block.get('series') or project_dir.name).strip()
    media_type = str(entry.get('media_type') or video_block.get('media_type') or 'tv').strip()
    category = str(entry.get('category') or video_block.get('category') or 'animation').strip()
    explicit_video = str(entry.get('video_dir') or video_block.get('dir') or '').strip()
    download_dir = str(bili_block.get('download_dir') or '').strip()
    derived = media_paths.series_dir(series, media_type, category) if series else ''

    video_dir, source = '', ''
    for value, label in ((explicit_video, 'registry video_dir'),
                         (download_dir, 'registry bili23.download_dir')):
        value = projects_registry.abs_path(project_dir, value) if value else ''
        if value and _media_count(value):
            video_dir, source = value, label
            break
    if not video_dir:
        derived_exists = bool(derived) and os.path.isdir(derived)
        documented = [p for p in _paths_in_claude_md(project_dir) if _media_count(p)]
        if documented:
            video_dir = max(documented, key=_media_count)
            source = 'CLAUDE.md'
        elif _media_count(str(project_dir)):
            video_dir, source = str(project_dir), '项目根目录'
        elif derived_exists and _media_count(derived):
            video_dir, source = derived, 'local.json paths.media'
        else:
            found, _score = _fuzzy_video_dir(series, media_type)
            if found:
                video_dir, source = found, 'local.json paths.media (同名匹配)'
    if not video_dir:
        video_dir, source = '', '视频未下载'

    drift = ''
    if video_dir and derived and os.path.isdir(video_dir) and os.path.normcase(
            os.path.abspath(video_dir)) != os.path.normcase(os.path.abspath(derived)):
        pinned = source.startswith('registry')
        drift = (f'目录与库规则不同（按规则应为 {os.path.basename(derived)}）'
                 + ('，已由 registry 固化' if pinned else '；可在 projects_registry.json 该项目的 video_dir 固化'))

    def stage(key: str, default_parts: tuple[str, ...]) -> str:
        value = str(subs_block.get(key) or '').strip()
        if value:
            return projects_registry.abs_path(project_dir, value)
        return str(os.path.join(project_dir, *default_parts))

    return {
        'name': project_dir.name,
        'path': str(project_dir),
        'registered': bool(entry),
        'series': series,
        'media_type': media_type,
        'category': category,
        'style': str(entry.get('style') or '').strip(),
        'source_lang': str(entry.get('source_lang') or '').strip(),
        'target_lang': str(entry.get('target_lang') or '').strip(),
        'bili23_source_url': str(entry.get('bili23_source_url')
                                 or bili_block.get('source_url') or '').strip(),
        'video_dir': video_dir or None,
        'video_source': source,
        'video_drift': drift,
        'media': _count_media(video_dir),
        'reference_subs': stage('reference_dir', ('bin', 'reference_docs', '参考字幕')),
        'input_subs': stage('input_dir', ('data', 'ja')),
        'output_subs': stage('output_dir', ('data', 'zh')),
        'deliverables': str(os.path.join(project_dir, 'bin', 'exports')),
    }


def _registry_only(entries: list[dict], data: dict) -> list[dict]:
    """Registered projects whose folder is not under the projects root."""
    found = {entry['name'] for entry in entries}
    out = []
    for key, item in sorted((data.get('projects') or {}).items()):
        if key in found or not isinstance(item, dict):
            continue
        out.append({
            'name': key,
            'registered': True,
            'path': str(item.get('path') or ''),
            'series': str(item.get('series') or key),
            'media_type': str(item.get('media_type') or 'tv'),
            'media': _count_media(projects_registry.abs_path(key, str(item.get('video_dir') or ''))),
        })
    return out


def collect(root: str) -> dict:
    base = Path(root)
    entries = []
    if not base.is_dir():
        return {'root': root, 'entries': [], 'error': 'projects root not found'}
    for child in sorted(base.iterdir()):
        if not child.is_dir() or child.name.startswith('.'):
            continue
        if not any((child / marker).exists() for marker in MARKERS):
            continue
        entries.append(describe(child))
    data = projects_registry.load()
    unregistered = [entry['name'] for entry in entries if not entry['registered']]
    return {
        'root': str(base),
        'media_root': media_paths.library_root() or None,
        'registry': str(projects_registry.registry_path()),
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'entries': entries,
        'unregistered': unregistered,
        'registry_only': _registry_only(entries, data),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', default='', help='projects root (default: local.json)')
    ap.add_argument('--check', action='store_true', help='do not write the index')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args(argv)

    root = projects_root(args.root)
    if not root:
        print('no projects root: pass --root or set projects_root in local.json',
              file=sys.stderr)
        return 2
    payload = collect(root)
    if not args.check:
        target = index_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n',
                          encoding='utf-8')
        print(f'wrote {target}', file=sys.stderr)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print(f"projects root: {payload['root']}")
    print(f"media root   : {payload['media_root']}")
    print(f"registry     : {payload['registry']}")
    print(f"{'project':<16}{'type':<6}{'files':>6}{'GB':>7}  video_dir")
    for entry in payload['entries']:
        media = entry['media']
        mark = '' if entry['registered'] else '  (未登记)'
        print(f"{entry['name']:<16}{entry['media_type']:<6}"
              f"{media['files']:>6}{media['size_gb']:>7}  {entry['video_dir']}{mark}")
        if entry['video_drift']:
            print(f"{'':<16}  ! {entry['video_drift']}")
    for entry in payload.get('registry_only') or []:
        print(f"{entry['name']:<16}{entry['media_type']:<6}"
              f"{entry['media']['files']:>6}{entry['media']['size_gb']:>7}  "
              f"{entry['path'] or '(待建目录)'}  (仅登记)")
    return 0


if __name__ == '__main__':
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.exit(main())
