"""Repo privacy: no machine paths or credentials in tracked files."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from harness.privacy_check import scan  # noqa: E402


def test_repository_is_clean():
    findings = scan(ROOT)
    assert findings == [], findings[:5]


def test_scanner_detects_key_and_home_path(tmp_path):
    subprocess.run(['git', 'init', '-q'], cwd=tmp_path, check=True)
    (tmp_path / 'leak.md').write_text(
        'key: sk-' + 'a' * 32 + '\n'
        'path: C:\\Users\\someone\\project\n',
        encoding='utf-8')
    subprocess.run(['git', '-C', str(tmp_path), 'add', '.'], check=True)

    kinds = {item['kind'] for item in scan(tmp_path)}
    assert {'api-key', 'windows-home'} <= kinds
