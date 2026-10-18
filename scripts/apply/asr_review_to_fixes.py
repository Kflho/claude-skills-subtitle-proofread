#!/usr/bin/env python3
"""Convert filled ASR review worklists into ``ai_review_fixes.json``.

The agent fills ``decision`` in ``temp/scans/<EP>.asr_review.json``:
keep / fix_time / replace_text / delete / split.  This script turns those
decisions into the fix-list format consumed by ``apply_fixes.py`` (through
``run_all``'s step_apply_all).

Split is not auto-applied (apply_fixes has no split action); it is reported.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys


def _decision_fix(item: dict, default_srt: str = '') -> tuple[dict | None, str]:
    decision = str(item.get('decision') or '').strip().lower()
    if not decision or decision == 'keep':
        return None, ''
    start = str(item.get('start') or '').strip()
    file = str(item.get('srt') or default_srt or '').strip()
    if not start:
        return None, 'missing start time'
    if not file:
        return None, 'missing srt path (fill worklist "srt" or pass --srt)'

    if decision == 'fix_time':
        new_start = item.get('new_start')
        new_end = item.get('new_end')
        if new_start in (None, '') and new_end in (None, ''):
            return None, 'fix_time needs new_start/new_end'
        fix = {'action': 'set_time', 'file': file, 'start': start}
        if new_start not in (None, ''):
            fix['new_start'] = new_start
        if new_end not in (None, ''):
            fix['new_end'] = new_end
        return fix, ''

    if decision == 'replace_text':
        replacement = item.get('replacement')
        if not replacement:
            replacement = (item.get('suggested') or {}).get('new_text')
        if not str(replacement or '').strip():
            return None, 'replace_text needs replacement'
        return {'action': 'replace_text', 'file': file, 'start': start,
                'replacement': str(replacement).strip()}, ''

    if decision == 'delete':
        return {'action': 'delete_line', 'file': file, 'start': start}, ''

    if decision == 'split':
        return None, 'split is not auto-applied (apply_fixes has no split action)'

    return None, f'unknown decision: {decision}'


def convert_worklist(data: dict, default_srt: str = '') -> tuple[list[dict], list[str]]:
    fixes, errors = [], []
    srt = str(data.get('srt') or default_srt or '')
    for item in data.get('items', []):
        fix, error = _decision_fix(item, default_srt=srt)
        if fix:
            fixes.append(fix)
        elif error:
            errors.append(f"cue {item.get('cue_index', '?')}: {error}")
    return fixes, errors


def convert_files(paths: list[str], default_srt: str = '') -> tuple[list[dict], list[str]]:
    fixes, errors = [], []
    for path in paths:
        try:
            with open(path, 'r', encoding='utf-8') as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f'{path}: unreadable ({exc})')
            continue
        f, e = convert_worklist(data, default_srt=default_srt)
        fixes.extend(f)
        errors.extend(e)
    # Dedupe by action/file/start; keep the first decision.
    seen, unique = set(), []
    for fix in fixes:
        key = (fix['action'], fix['file'], fix['start'])
        if key in seen:
            continue
        seen.add(key)
        unique.append(fix)
    return unique, errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scans-dir', default=os.path.join('temp', 'scans'))
    parser.add_argument('--out', default='',
                        help='output JSON (default: <scans-dir>/ai_review_fixes.json)')
    parser.add_argument('--srt', default='',
                        help='fallback SRT path when worklist has no "srt"')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)

    paths = sorted(glob.glob(os.path.join(args.scans_dir, '*.asr_review.json')))
    if not paths:
        print(f'[review] no worklists in {args.scans_dir}', file=sys.stderr)
        return 0
    fixes, errors = convert_files(paths, default_srt=args.srt)
    out = args.out or os.path.join(args.scans_dir, 'ai_review_fixes.json')
    print(f'[review] {len(fixes)} fixes, {len(errors)} skipped', file=sys.stderr)
    for err in errors:
        print(f'  [skip] {err}', file=sys.stderr)
    if args.dry_run:
        print(json.dumps(fixes, ensure_ascii=False, indent=2))
        return 0
    with open(out, 'w', encoding='utf-8') as handle:
        json.dump(fixes, handle, ensure_ascii=False, indent=2)
    print(f'[review] wrote {out}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
