#!/usr/bin/env python3
"""Scan tracked files for machine paths and credentials.

The repo is the development source and must not carry this machine's paths or
keys (those live in the harness copy's ``local.json``).  This check is cheap
enough for a test/pre-commit hook and catches the two classes we actually hit:

- absolute user paths (``C:\\Users\\...``, ``/home/<user>/``);
- credential-looking strings (``sk-...``, ``ghp_...``, AWS keys, private keys).

It is not a replacement for gitleaks/detect-secrets on **history**; run those
before publishing if history may contain secrets.

Usage:
    python scripts/harness/privacy_check.py
    python scripts/harness/privacy_check.py --root . --json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

KEY_PATTERNS = (
    (re.compile(r'sk-[A-Za-z0-9]{20,}'), 'api-key'),
    (re.compile(r'ghp_[A-Za-z0-9]{20,}'), 'github-token'),
    (re.compile(r'AKIA[0-9A-Z]{16}'), 'aws-key'),
    (re.compile(r'xox[baprs]-[A-Za-z0-9-]{10,}'), 'slack-token'),
    (re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----'), 'private-key'),
)
PATH_PATTERNS = (
    (re.compile(r'(?<![A-Za-z])[A-Za-z]:\\Users\\[^\\\s]+'), 'windows-home'),
    (re.compile(r'/home/[A-Za-z0-9._-]+/'), 'posix-home'),
    (re.compile(r'(?<![A-Za-z])[A-Za-z]:\\data\\'), 'absolute-data-path'),
)
SKIP_SUFFIX = {'.png', '.jpg', '.jpeg', '.gif', '.zip', '.bundle', '.pyc',
               '.safetensors', '.bin', '.wav', '.mp3', '.mp4'}
SKIP_NAMES = {'local.json'}


def _tracked(root: Path) -> list[str]:
    try:
        out = subprocess.run(['git', '-C', str(root), 'ls-files', '-z'],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    return [p for p in out.stdout.split('\0') if p]


def scan(root: Path) -> list[dict]:
    findings = []
    for rel in _tracked(root):
        path = root / rel
        if not path.is_file() or path.suffix.lower() in SKIP_SUFFIX \
                or path.name in SKIP_NAMES:
            continue
        try:
            text = path.read_text(encoding='utf-8-sig', errors='ignore')
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for pattern, kind in KEY_PATTERNS + PATH_PATTERNS:
                if pattern.search(line):
                    findings.append({'file': rel, 'line': lineno, 'kind': kind,
                                     'text': line.strip()[:100]})
    return findings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    findings = scan(Path(args.root))
    if args.json:
        print(json.dumps({'findings': findings}, ensure_ascii=False, indent=2))
    else:
        for item in findings:
            print(f"{item['file']}:{item['line']} [{item['kind']}] {item['text']}")
        print(f'{len(findings)} finding(s)')
    return 0 if not findings else 1


if __name__ == '__main__':
    sys.exit(main())
