#!/usr/bin/env python3
"""Where the media lives: library roots, series folders, subtitle dirs.

Machine paths belong in ``local.json`` (``paths.*``) next to ``models_dir``,
never in SKILL.md and never in a project's CLAUDE.md.  Which series a project is
and where its folders sit comes from the global registry
(:mod:`lib.projects_registry`, ``projects_registry.json`` beside ``local.json``)
— project folders themselves keep no config.

Resolution order for every value, same as everywhere else in this skill::

    CLI argument  >  projects_registry.json  >  local.json  >  derived default

Library layout (``local.json`` → ``paths.media``)::

    {
      "root": "D:\\\\videos",              # anything under here is the library
      "categories": {"animation": "Animation"},
      "kinds": {"tv": "TV", "ova": "OVA", "movie": "Movie"}
    }

which yields ``<root>/<category>/<KIND>/<series>``.  Explicit per-kind paths
win over the derivation, so a machine can keep TV somewhere else::

    "kinds": {"tv": "D:\\\\videos\\\\Animation\\\\TV", ...}

Call ``python scripts/lib/media_paths.py --check`` to print what this machine
resolves to.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from lib import projects_registry
from lib.local_config import local_value

MEDIA_TYPES = ('tv', 'ova', 'movie')
DEFAULT_CATEGORIES = {'animation': 'Animation'}
DEFAULT_KINDS = {'tv': 'TV', 'ova': 'OVA', 'movie': 'Movie'}


# ── local.json: the machine library ──────────────────────────────────────


def _media_block() -> dict:
    block = local_value('paths', 'media', default=None)
    if isinstance(block, dict):
        return block
    return {}


def library_root() -> str:
    block = _media_block()
    root = str(block.get('root') or '').strip()
    if root:
        return os.path.expanduser(root)
    # Legacy single-purpose key, then the oldest hint of all.
    roots = local_value('video_roots', default=None)
    if isinstance(roots, list) and roots:
        return os.path.expanduser(str(roots[0]))
    return ''


def _categories() -> dict:
    block = _media_block()
    cats = block.get('categories')
    merged = dict(DEFAULT_CATEGORIES)
    if isinstance(cats, dict):
        merged.update({str(k): str(v) for k, v in cats.items()})
    return merged


def _kinds() -> dict:
    block = _media_block()
    kinds = block.get('kinds')
    merged = dict(DEFAULT_KINDS)
    if isinstance(kinds, dict):
        merged.update({str(k): str(v) for k, v in kinds.items()})
    return merged


def kind_dir(media_type: str = 'tv', category: str = 'animation') -> str:
    """``<root>/<category>/<KIND>`` — the folder series of this kind live in.

    Each segment is matched against what is actually on disk, case-insensitively,
    so a library that spells it ``TV`` here and ``tv`` there needs no config
    change.  An empty category means "kinds sit directly under the root".
    """
    media_type = normalize_media_type(media_type)
    kinds = _kinds()
    value = str(kinds.get(media_type) or '').strip()
    if os.path.isabs(os.path.expanduser(value)):
        return os.path.expanduser(value)
    root = library_root()
    if not root:
        return ''
    cat = _categories().get(category, category)
    base = _resolve_child(root, cat) if cat else root
    return _resolve_child(base, value or media_type.upper()) if base else ''


def _resolve_child(base: str, name: str) -> str:
    """Join *name* onto *base*, preferring the on-disk spelling of that folder."""
    if not base:
        return ''
    if not name:
        return base
    if not os.path.isdir(base):
        return os.path.join(base, name)
    try:
        for entry in os.listdir(base):
            if entry.lower() == name.lower() and os.path.isdir(os.path.join(base, entry)):
                return os.path.join(base, entry)
    except OSError:
        pass
    return os.path.join(base, name)


def series_dir(series: str, media_type: str = 'tv', category: str = 'animation') -> str:
    base = kind_dir(media_type, category)
    if not base or not series:
        return ''
    return os.path.join(base, series.strip())


def series_info(project: str | Path | None = None) -> tuple[str, str, str]:
    """``(series, media_type, category)`` for a project, defaulting to tv/animation."""
    series = str(projects_registry.value(project, 'series') or '').strip()
    media_type = str(projects_registry.value(project, 'media_type') or 'tv').strip()
    category = str(projects_registry.value(project, 'category') or 'animation').strip()
    return series, media_type, category


def _stage_dir(project: str | Path | None, key: str, default_parts: tuple[str, ...]) -> str:
    """Registry ``subtitles.<key>`` (resolved against the project) or the default."""
    value = str(projects_registry.value(project, 'subtitles', key) or '').strip()
    if value:
        return projects_registry.abs_path(project, value)
    from lib import project_paths

    return os.path.join(project_paths.project_root(str(project) if project else None),
                        *default_parts)


def reference_subs_dir(project: str | Path | None = None) -> str:
    """Where reference/human subtitles for this project live."""
    return _stage_dir(project, 'reference_dir',
                      ('bin', 'reference_docs', '参考字幕'))


def input_subs_dir(project: str | Path | None = None) -> str:
    """Source-language subtitles that the pipeline reads."""
    from lib import project_paths

    value = str(projects_registry.value(project, 'subtitles', 'input_dir') or '').strip()
    if value:
        return projects_registry.abs_path(project, value)
    return os.path.join(project_paths.data_root(str(project) if project else None), 'ja')


def output_subs_dir(project: str | Path | None = None) -> str:
    """Working copies of the translated subtitles."""
    from lib import project_paths

    value = str(projects_registry.value(project, 'subtitles', 'output_dir') or '').strip()
    if value:
        return projects_registry.abs_path(project, value)
    return os.path.join(project_paths.data_root(str(project) if project else None), 'zh')


def project_video_dir(project: str | Path | None = None) -> str:
    """The series folder this project works on (may not exist yet).

    Order: registry ``video_dir`` (an explicit override for series whose folder
    breaks the library rule) → ``<root>/<category>/<KIND>/<series>``.
    """
    explicit = str(projects_registry.value(project, 'video_dir') or '').strip()
    if explicit:
        return projects_registry.abs_path(project, explicit)
    series, media_type, category = series_info(project)
    if not series:
        return ''
    return series_dir(series, media_type or 'tv', category or 'animation')


# ── discovery: every machine lays this out differently ──────────────────
#
# Do not bake in one machine's spelling.  A library is any folder whose
# children look like kind folders, and the kind folders are whatever that
# machine actually named them.

KIND_ALIASES = {
    'tv': 'tv', 'tv番组': 'tv', 'television': 'tv', 'series': 'tv', '剧集': 'tv',
    'ova': 'ova', 'oad': 'ova', 'oad/ova': 'ova',
    'movie': 'movie', 'movies': 'movie', 'film': 'movie', 'films': 'movie',
    '剧场版': 'movie', '映画': 'movie',
}
LIBRARY_NAMES = {'animation', 'anime', 'animes', '卡通', '动画', '动漫', '番剧',
                 'bangumi', 'acg'}
SKIP_DIRS = {'windows', 'program files', 'program files (x86)', 'programdata',
             'appdata', 'users', '$recycle.bin', 'system volume information',
             'node_modules', '.git', '.venv', 'site-packages', 'temp', 'tmp'}


def _kind_of(name: str) -> str:
    return KIND_ALIASES.get(name.strip().lower(), '')


def _kind_map(path: str) -> dict:
    """``{'tv': 'D:/x/TV', 'ova': 'D:/x/ova', ...}`` for the kinds present."""
    found = {}
    try:
        for entry in os.listdir(path):
            full = os.path.join(path, entry)
            if not os.path.isdir(full):
                continue
            kind = _kind_of(entry)
            if kind and kind not in found:
                found[kind] = full
    except OSError:
        return {}
    return found


def discover_candidates(roots: list[str] | None = None, max_depth: int = 2) -> list[dict]:
    """Find plausible media libraries, without assuming any one spelling.

    A candidate is either a folder whose children include at least two kind
    folders (``TV`` + ``ova`` + ``movie``), or a folder named ``animation`` /
    ``アニメ`` / ``动漫`` that holds at least one kind folder.  Depth is small on
    purpose: this runs interactively, and deep walks on a full disk are slow.
    """
    import string

    if roots is None:
        roots = [str(r) for r in (local_value('video_roots', default=None) or [])]
        if not roots:
            roots = [f'{letter}:\\' for letter in string.ascii_uppercase
                     if os.path.isdir(f'{letter}:\\')]
    out = []
    seen = set()
    for root in roots:
        root = os.path.abspath(os.path.expanduser(root))
        if not os.path.isdir(root):
            continue
        base_depth = root.rstrip(os.sep).count(os.sep)
        for current, dirs, _files in os.walk(root):
            depth = current.rstrip(os.sep).count(os.sep) - base_depth
            if depth >= max_depth:
                dirs[:] = []
            dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS]
            kinds = _kind_map(current)
            named = os.path.basename(current).lower() in LIBRARY_NAMES
            if len(kinds) >= 2 or (named and kinds):
                if current not in seen:
                    seen.add(current)
                    out.append({'path': current, 'kinds': kinds})
    return out


# ── media type: TV / OVA / MOVIE ────────────────────────────────────────


def normalize_media_type(value: str) -> str:
    text = (value or '').strip().lower()
    aliases = {'television': 'tv', 'series': 'tv', 'movies': 'movie', 'film': 'movie'}
    text = aliases.get(text, text)
    if text and text not in MEDIA_TYPES:
        raise ValueError(f'unknown media type {value!r}; expected one of {MEDIA_TYPES}')
    return text or 'tv'


def suggest_media_type(episode_count: int, durations: list[float]) -> tuple[str, str]:
    """Guess TV / OVA / MOVIE from part count and per-part minutes.

    Returns ``(type, reason)``.  The guess is a *proposal*: it is deliberately
    conservative and the caller is expected to show it to the user, because the
    folder decides where the files land.
    """
    count = int(episode_count or 0)
    minutes = [d / 60.0 for d in durations if d]
    avg = sum(minutes) / len(minutes) if minutes else 0.0
    if count <= 1:
        if avg >= 50:
            return 'movie', f'单文件 {avg:.0f} 分钟 → 剧场版'
        return 'movie', f'单文件 {avg:.0f} 分钟 → 按剧场版/单本处理'
    if avg >= 45:
        return 'ova', f'{count} 集、单集 {avg:.0f} 分钟 → OVA/长篇'
    if count <= 6 and avg >= 25:
        return 'ova', f'{count} 集、单集 {avg:.0f} 分钟 → 短篇合集，多半是 OVA'
    return 'tv', f'{count} 集、单集 {avg:.0f} 分钟 → 电视动画'


# ── CLI ─────────────────────────────────────────────────────────────────


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='dump resolved paths')
    parser.add_argument('--discover', action='store_true',
                        help='scan drives for libraries and print candidates')
    parser.add_argument('--roots', default='', help=';-separated roots to scan')
    parser.add_argument('--depth', type=int, default=2)
    parser.add_argument('--media-type', default='tv', choices=MEDIA_TYPES)
    parser.add_argument('--series', default='')
    parser.add_argument('--project', default='')
    parser.add_argument('--episodes', type=int, default=0)
    parser.add_argument('--minutes', default='', help='comma-separated per-part minutes')
    args = parser.parse_args(argv)

    if args.discover:
        roots = [r for r in args.roots.split(';') if r.strip()] or None
        candidates = discover_candidates(roots, args.depth)
        print(json.dumps({'configured_root': library_root() or None,
                          'candidates': candidates}, ensure_ascii=False, indent=2))
        return 0

    if args.episodes or args.minutes:
        durations = [float(m) * 60 for m in args.minutes.split(',') if m.strip()]
        kind, reason = suggest_media_type(args.episodes or len(durations), durations)
        print(json.dumps({'media_type': kind, 'reason': reason}, ensure_ascii=False))

    project_arg = args.project or None
    payload = {
        'library_root': library_root() or None,
        'kind_dirs': {t: kind_dir(t) for t in MEDIA_TYPES},
        'series': series_dir(args.series, args.media_type) if args.series else None,
        'registry': str(projects_registry.registry_path()),
        'project': projects_registry.project_dir(project_arg),
        'project_registered': bool(projects_registry.entry(project_arg)),
        'series_config': series_info(project_arg),
        'video_dir': project_video_dir(project_arg) or None,
        'reference_subs': reference_subs_dir(project_arg),
        'input_subs': input_subs_dir(project_arg),
        'output_subs': output_subs_dir(project_arg),
    }
    if args.check or not (args.episodes or args.minutes):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.exit(main())
