#!/usr/bin/env python3
"""Sync the runtime skill from this repo into the harness skills directory.

Default mode copies files (portable).  On the local dev machine ``--link``
creates a Windows junction to this repo, so edits are live and ``local.json``
stays in the gitignored repo root.

    python scripts/harness/sync_skill.py --dry-run
    python scripts/harness/sync_skill.py --link
    python scripts/harness/sync_skill.py
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FILES = ('SKILL.md', 'README.md')
DIRS = ('references', 'scripts', 'templates')
IGNORE = shutil.ignore_patterns('__pycache__', '*.pyc', '.venv*', 'models',
                                'temp', 'tests', 'eval', 'local.json')


def _skill_name() -> str:
    try:
        text = (REPO_ROOT / 'SKILL.md').read_text(encoding='utf-8-sig')
        for line in text.splitlines():
            if line.startswith('name:'):
                return line.split(':', 1)[1].strip()
    except OSError:
        pass
    return REPO_ROOT.name


def _is_link(path: Path) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, 'st_file_attributes', 0)
                & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))


def _git_commit() -> str:
    try:
        out = subprocess.run(['git', '-C', str(REPO_ROOT), 'rev-parse',
                              '--short', 'HEAD'], capture_output=True, text=True,
                             timeout=5)
        return out.stdout.strip() if out.returncode == 0 else 'unknown'
    except (OSError, subprocess.SubprocessError):
        return 'unknown'


def plan(target: Path) -> list[Path]:
    items = [REPO_ROOT / name for name in FILES if (REPO_ROOT / name).is_file()]
    items += [REPO_ROOT / name for name in DIRS if (REPO_ROOT / name).is_dir()]
    return items


def link(target: Path, dry_run: bool = False) -> int:
    if target.resolve() == REPO_ROOT.resolve():
        print('refusing: target is the development repo itself', file=sys.stderr)
        return 2
    if target.exists() and _is_link(target) and target.resolve() == REPO_ROOT.resolve():
        print(f'already linked: {target}')
        return 0
    if dry_run:
        print(f'  would link {target} -> {REPO_ROOT}')
        return 0
    if target.exists() or _is_link(target):
        if _is_link(target):
            target.unlink()
        else:
            shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ['cmd', '/c', 'mklink', '/J', str(target), str(REPO_ROOT)],
        capture_output=True, text=True, encoding='utf-8', errors='replace')
    if result.returncode != 0:
        print(f'failed to create junction: {result.stderr}', file=sys.stderr)
        return 2
    print(f'linked {target} -> {REPO_ROOT} (edits are live)')
    return 0


def sync(target: Path, dry_run: bool = False) -> int:
    if _is_link(target):
        print(f'refusing: {target} is a junction/symlink; remove it first',
              file=sys.stderr)
        return 2
    if target.resolve() == REPO_ROOT.resolve():
        print('refusing: target is the development repo itself', file=sys.stderr)
        return 2

    items = plan(target)
    files = sum(1 for item in items
                for _ in (item.rglob('*') if item.is_dir() else [item])
                if _.is_file() and '__pycache__' not in str(_))
    if dry_run:
        for item in items:
            print(f'  would sync {item.relative_to(REPO_ROOT)}')
        print(f'  {files} file(s) -> {target}')
        return 0

    target.mkdir(parents=True, exist_ok=True)
    for item in items:
        dest = target / item.name
        if item.is_dir():
            if dest.exists() and _is_link(dest):
                dest.unlink()
            elif dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(item, dest, ignore=IGNORE)
        else:
            shutil.copy2(item, dest)

    meta = {
        'source': str(REPO_ROOT),
        'commit': _git_commit(),
        'synced_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'files': files,
    }
    (target / 'SYNCED.json').write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'synced {files} file(s) -> {target}')
    if not (target / 'local.json').is_file():
        print('note: local.json missing; run scripts/harness/init_local.py '
              f'--output "{target / "local.json"}"', file=sys.stderr)
    return 0


def main(argv=None):
    default_target = (Path.home() / '.agents' / 'skills' / _skill_name())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', default=str(default_target))
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--link', action='store_true',
                        help='create a local junction instead of copying')
    args = parser.parse_args(argv)
    target = Path(args.target)
    return link(target, args.dry_run) if args.link else sync(target, args.dry_run)


if __name__ == '__main__':
    sys.exit(main())
