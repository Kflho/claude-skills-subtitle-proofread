"""Synced docs must not leak machine paths or point outside the skill copy."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = [ROOT / 'SKILL.md', ROOT / 'README.md']
DOCS += sorted((ROOT / 'references').rglob('*.md'))

DRIVE = re.compile(r'(?<![A-Za-z])[A-Za-z]:[\\/]')
LINK = re.compile(r'\[[^\]]*\]\(([^)]+)\)')


def _text(path):
    return path.read_text(encoding='utf-8-sig')


def test_synced_docs_have_no_machine_paths():
    offenders = []
    for path in DOCS:
        for lineno, line in enumerate(_text(path).splitlines(), 1):
            if DRIVE.search(line):
                offenders.append(f'{path.name}:{lineno}: {line.strip()[:80]}')
    assert not offenders, offenders


def test_synced_docs_do_not_embed_dev_tree_paths():
    offenders = []
    for path in DOCS:
        if 'sk_01_dev/' in _text(path):
            offenders.append(path.name)
    assert not offenders, offenders


def test_relative_markdown_links_resolve():
    missing = []
    for path in DOCS:
        for target in LINK.findall(_text(path)):
            if target.startswith(('http://', 'https://', '#', 'mailto:')):
                continue
            target = target.split('#', 1)[0]
            if not target.endswith('.md'):
                continue
            resolved = (path.parent / target).resolve()
            if not resolved.is_file():
                missing.append(f'{path.name} -> {target}')
    assert not missing, missing


def test_skill_explains_sync_and_local_config():
    text = _text(ROOT / 'SKILL.md')
    assert 'local.json' in text
    assert 'sync_skill.py' in text
    assert 'init_local.py' in text
