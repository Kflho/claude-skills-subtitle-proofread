#!/usr/bin/env python3
"""Sync the runtime skill from this repo into the harness skills directory.

Development happens in the repo; the harness must load a **real copy**, never a
junction to the dev tree (that was the old, confusing setup).  Machine paths
and keys do not belong to the repo: after syncing, run ``init_local.py`` inside
the harness copy to write its ``local.json``.

    python scripts/harness/sync_skill.py --dry-run
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
    args = parser.parse_args(argv)
    return sync(Path(args.target), args.dry_run)


if __name__ == '__main__':
    sys.exit(main())
