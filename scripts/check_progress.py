#!/usr/bin/env python3
"""Fast progress checker for Astro Boy (1963) subtitle project.

Reads only report header + JSON summaries — no full file parsing.
Single command, ASCII-safe output, works on Windows GBK locale.

Usage:
  python check_progress.py [--project-dir <path>]

If --project-dir is omitted, uses the current working directory.
"""

import json
import os
import re
import subprocess
import sys

# Ensure UTF-8 output on Windows
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass


def find_project_dir():
    """Find project root from args or CWD."""
    for i, arg in enumerate(sys.argv):
        if arg == '--project-dir' and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return os.getcwd()


def read_report_header(report_path):
    """Read only the header line to get resolved/pending/deleted counts.
    Also counts by status-column markers to validate the header metadata.

    IMPORTANT: Each pending entry has ⬜ in BOTH the 'corrected' and 'status'
    columns (e.g. | me | ⬜ | ⬜ |), so counting all ⬜ characters in the file
    gives 2x the actual pending count. We count only the LAST column (status)."""
    if not os.path.exists(report_path):
        return None

    with open(report_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Parse header metadata line
    result = None
    for line in content.split('\n'):
        line = line.strip()
        if line.startswith('> 总览:') or line.startswith('> Total:'):
            nums = re.findall(r'(\d+)', line)
            if len(nums) >= 3:
                result = {
                    'fixed': int(nums[0]),
                    'pending': int(nums[1]),
                    'deleted': int(nums[2]),
                }
            break

    # Count status-column markers only (the last cell in each table row).
    # Pattern: line ends with "| ✅ |" or "| ⬜ |" or "| 🗑️ |"
    actual_fixed = len(re.findall(r'\| ✅ \|$', content, re.MULTILINE))
    actual_pending = len(re.findall(r'\| ⬜ \|$', content, re.MULTILINE))
    actual_deleted = len(re.findall(r'\| \U0001F5D1️ \|$', content, re.MULTILINE))

    if result:
        result['actual_fixed'] = actual_fixed
        result['actual_pending'] = actual_pending
        result['actual_deleted'] = actual_deleted
        result['header_stale'] = (
            result['fixed'] != actual_fixed or
            result['pending'] != actual_pending or
            result['deleted'] != actual_deleted
        )

    return result


def read_findings_summary(findings_path):
    """Read only the 'summary' key from findings.json."""
    if not os.path.exists(findings_path):
        return None

    with open(findings_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    return data.get('summary', {})


def read_fixes_count(fixes_path):
    """Count entries in fixes.json without loading full content."""
    if not os.path.exists(fixes_path):
        return 0

    with open(fixes_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    return len(data) if isinstance(data, list) else 0


def get_git_info(project_dir):
    """Get last commit and status from git.
    Uses utf-8 encoding explicitly to avoid GBK decode errors on CJK commit messages.
    """
    git_kwargs = {
        'cwd': project_dir,
        'capture_output': True,
        'encoding': 'utf-8',
        'errors': 'replace',
        'timeout': 10,
    }
    try:
        result = subprocess.run(['git', 'log', '--oneline', '-1'], **git_kwargs)
        last_commit = result.stdout.strip() if result.returncode == 0 else '(git error)'
    except Exception:
        last_commit = '(git unavailable)'

    try:
        result = subprocess.run(['git', 'status', '--short'], **git_kwargs)
        status_lines = [l for l in result.stdout.strip().split('\n') if l] if result.returncode == 0 else []
    except Exception:
        status_lines = []

    return last_commit, status_lines


def format_kv(key, value, indent=2):
    """Format a key-value line with right-aligned value."""
    prefix = ' ' * indent
    return f'{prefix}{key:<25} {value}'


def main():
    project_dir = find_project_dir()
    report_path = os.path.join(project_dir, 'reports', '问题解决报告.md')
    findings_path = os.path.join(project_dir, 'findings.json')
    fixes_path = os.path.join(project_dir, 'fixes.json')

    print('=' * 60)
    print('  Astro Boy (1963) — Subtitle Progress')
    print('=' * 60)

    # Git info
    last_commit, status_lines = get_git_info(project_dir)
    print(f'\n  Last commit: {last_commit}')

    # Report
    print(f'\n  --- Report ---')
    report = read_report_header(report_path)
    if report:
        print(format_kv('Resolved (fixed):', report['fixed']))
        print(format_kv('Pending  (todo):', report['pending']))
        print(format_kv('Deleted:', report['deleted']))
        print(format_kv('Total (header):',
              report['fixed'] + report['pending'] + report['deleted']))
        if report.get('header_stale'):
            # Show actual file counts when header is stale
            print(f'\n  *** Header is STALE — actual file counts: ***')
            print(format_kv('Actual resolved:', report['actual_fixed']))
            print(format_kv('Actual pending:', report['actual_pending']))
            print(format_kv('Actual total:',
                  report['actual_fixed'] + report['actual_pending'] + report['actual_deleted']))
    else:
        print('  (no report found)')

    # Findings
    print(f'\n  --- Findings ---')
    summary = read_findings_summary(findings_path)
    if summary:
        print(format_kv('Files scanned:', summary.get('files_scanned', '?')))
        print(format_kv('Total cues:', summary.get('total_cues', '?')))
        print(format_kv('Total findings:', summary.get('total_findings', '?')))
        by_type = summary.get('by_type', {})
        for t, n in by_type.items():
            print(format_kv(f'  {t}:', n))
        print(format_kv('Episodes w/ issues:', summary.get('episodes_with_issues', '?')))
    else:
        print('  (no findings.json found)')

    # Fixes
    print(f'\n  --- Fixes ---')
    fixes_count = read_fixes_count(fixes_path)
    print(format_kv('Fixes generated:', fixes_count))

    # Git status
    print(f'\n  --- Git Status ---')
    if status_lines:
        for line in status_lines[:15]:
            print(f'    {line}')
        if len(status_lines) > 15:
            print(f'    ... and {len(status_lines) - 15} more')
    else:
        print('  (clean)')

    print('=' * 60)


if __name__ == '__main__':
    main()
