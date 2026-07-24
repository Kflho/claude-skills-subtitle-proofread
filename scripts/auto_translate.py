#!/usr/bin/env python3
"""Auto-translate orchestration: translate → review → fix.

Checkpoint-driven — re-run the same command to advance through stages.

Usage:
  # Full pipeline (new translation)
  python auto_translate.py \\
    --source-dir 日文ai修复版/ \\
    --target-dir 中文AI翻译验证/ \\
    --mappings temp/noun_mappings.json

  # Review-only (already translated, no source available)
  python auto_translate.py \\
    --target-dir 中文AI翻译验证/ \\
    --mappings temp/noun_mappings.json

Stages:
  translate → run translate_srt.py (if no target files)
  review → find_suspect_nouns.py --mode translation → candidates.json
  done → candidates empty or no target files changed
"""

import argparse
import json
import os
import subprocess
import sys

import lib._path  # noqa: F401

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_FIND_SUSPECT = os.path.join(_SCRIPT_DIR, 'nouns', 'find_suspect_nouns.py')
_TRANSLATE_SRT = os.path.join(_SCRIPT_DIR, 'translate_srt.py')

CANDIDATES_FILE = 'candidates.json'


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def _run(cmd, project_dir, desc=''):
    """Run a subprocess with PYTHONPATH set so scripts can find lib/."""
    label = f'[{desc}]' if desc else ''
    cmd_display = ' '.join(str(p) for p in cmd)
    print(f'{label} {cmd_display[:120]}...', file=sys.stderr)

    env = os.environ.copy()
    existing = env.get('PYTHONPATH', '')
    env['PYTHONPATH'] = _SCRIPT_DIR + (os.pathsep + existing if existing else '')

    subprocess.run(cmd, check=True, cwd=project_dir, env=env)


def _get_temp_dir(project_dir):
    return os.path.join(project_dir, 'temp', 'scans')


def _latest_srt_mtime(target_dir):
    """Return modification time of the newest SRT/ASS file in target_dir."""
    if not os.path.isdir(target_dir):
        return 0
    return max(
        (os.path.getmtime(os.path.join(target_dir, f))
         for f in os.listdir(target_dir)
         if f.lower().endswith(('.srt', '.ass'))),
        default=0,
    )


def _has_srt_files(target_dir):
    """Check if target_dir contains any SRT/ASS files."""
    if not os.path.isdir(target_dir):
        return False
    return any(f.lower().endswith(('.srt', '.ass')) for f in os.listdir(target_dir))


# ═══════════════════════════════════════════════════════════════
# Stage determination
# ═══════════════════════════════════════════════════════════════

def determine_stage(project_dir, target_dir, source_dir):
    """Return the next stage: 'translate', 'review', 'review_pending', or 'done'."""
    temp_dir = _get_temp_dir(project_dir)
    candidates_path = os.path.join(temp_dir, CANDIDATES_FILE)

    # ── Translate needed? ──
    if source_dir and os.path.isdir(source_dir) and not _has_srt_files(target_dir):
        return 'translate'

    # ── Review needed? ──
    if _has_srt_files(target_dir):
        if not os.path.exists(candidates_path):
            return 'review'

        target_mtime = _latest_srt_mtime(target_dir)
        candidates_mtime = os.path.getmtime(candidates_path)

        if target_mtime > candidates_mtime:
            # SRT files changed since last review → re-review
            return 'review'

        # Candidates is current — check if clean
        try:
            with open(candidates_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if data.get('candidates'):
                return 'review_pending'
            return 'done'
        except (json.JSONDecodeError, IOError):
            return 'review'

    return 'done'


# ═══════════════════════════════════════════════════════════════
# Stage: Translate
# ═══════════════════════════════════════════════════════════════

def stage_translate(project_dir, source_dir, target_dir, mappings_path, dry_run=False):
    """Run translate phase: Japanese → Chinese via LLM API."""
    api_key = os.environ.get('LLM_API_KEY', '') or os.environ.get('POLISH_API_KEY', '')
    if not api_key and not dry_run:
        print('ERROR: LLM_API_KEY not set.', file=sys.stderr)
        print('  export LLM_API_KEY="sk-..."', file=sys.stderr)
        sys.exit(1)

    cmd = [
        sys.executable, _TRANSLATE_SRT,
        '--input-dir', source_dir,
        '--output-dir', target_dir,
        '--mappings', mappings_path,
    ]
    if dry_run:
        cmd.append('--dry-run')

    print(f'[translate] {source_dir} → {target_dir}', file=sys.stderr)
    _run(cmd, project_dir, desc='translate')


# ═══════════════════════════════════════════════════════════════
# Stage: Review
# ═══════════════════════════════════════════════════════════════

def stage_review(project_dir, target_dir, source_dir, mappings_path, limit=None):
    """Run review phase.

    Calls find_suspect_nouns.py --mode translation (upgraded unified scanner)
    which outputs a unified candidates.json for AI review.
    """
    temp_dir = _get_temp_dir(project_dir)
    os.makedirs(temp_dir, exist_ok=True)

    candidates_path = os.path.join(temp_dir, CANDIDATES_FILE)

    # Build command — source-dir enables cross-reference, otherwise degrade
    cmd = [
        sys.executable, _FIND_SUSPECT,
        '--input-dir', target_dir,
        '--lang', 'zh',
        '--mode', 'translation',
        '--output', candidates_path,
        '--output-format', 'candidates',
        '--max-singletons', '0',  # unlimited
    ]
    if source_dir and os.path.isdir(source_dir):
        cmd.extend(['--source-dir', source_dir])
    if os.path.exists(mappings_path):
        cmd.extend(['--mappings', mappings_path])
    if limit:
        cmd.extend(['--limit', str(limit)])

    print(f'[review] Scanning: {target_dir}', file=sys.stderr)
    _run(cmd, project_dir, desc='review')

    # Report
    try:
        with open(candidates_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        print('[review] Failed to read candidates output.', file=sys.stderr)
        return

    candidates = data.get('candidates', [])
    stats = data.get('stats', {})
    n_total = stats.get('total', len(candidates))
    n_inc = stats.get('inconsistency', sum(1 for c in candidates if c.get('type') == 'inconsistency'))
    n_unk = stats.get('unknown_suspect', n_total - n_inc)

    print(f'[review] → {candidates_path}', file=sys.stderr)
    print(f'  Total: {n_total} ({n_inc} inconsistencies, {n_unk} unknown)',
          file=sys.stderr)

    if candidates:
        print(f'\n👉 Next: Read {candidates_path}, fix SRT files + update mappings, '
              f'then re-run.',
              file=sys.stderr)
    else:
        print('\n✅ All clear — no proper noun issues found.', file=sys.stderr)


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description='Auto-translate: translate → review → fix'
    )
    parser.add_argument('--source-dir',
                        help='Japanese source SRT directory (optional, enables cross-reference)')
    parser.add_argument('--target-dir', required=True,
                        help='Chinese target SRT directory')
    parser.add_argument('--mappings', default='temp/noun_mappings.json',
                        help='Path to noun_mappings.json (default: temp/noun_mappings.json)')
    parser.add_argument('--limit', type=int, default=0,
                        help='Max files per stage (for testing)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview only, no API calls')
    args = parser.parse_args()

    project_dir = os.getcwd()

    def _resolve(p):
        if p and not os.path.isabs(p):
            return os.path.join(project_dir, p)
        return p

    mappings_path = _resolve(args.mappings)
    source_dir = _resolve(args.source_dir)
    target_dir = _resolve(args.target_dir)
    limit = args.limit or None

    # Validate source_dir
    if source_dir and not os.path.isdir(source_dir):
        print(f'[WARN] Source dir not found: {source_dir}', file=sys.stderr)
        print('[WARN] Cross-reference disabled. Chinese-side scanning only.', file=sys.stderr)
        source_dir = None

    # Determine and execute stage
    stage = determine_stage(project_dir, target_dir, source_dir)
    print(f'[auto_translate] Stage: {stage}', file=sys.stderr)

    if stage == 'translate':
        if not source_dir:
            print('[translate] No source-dir — skipping.', file=sys.stderr)
            print('👉 Re-run to proceed to review.', file=sys.stderr)
        else:
            stage_translate(project_dir, source_dir, target_dir,
                           mappings_path, args.dry_run)
            # After translation, proceed directly to review
            print('\n[auto_translate] Translation done → review.', file=sys.stderr)
            stage_review(project_dir, target_dir, source_dir, mappings_path, limit)

    elif stage == 'review':
        stage_review(project_dir, target_dir, source_dir, mappings_path, limit)

    elif stage == 'review_pending':
        temp_dir = _get_temp_dir(project_dir)
        candidates_path = os.path.join(temp_dir, CANDIDATES_FILE)
        with open(candidates_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        n = len(data.get('candidates', []))
        print(f'[review] {n} candidate(s) still pending AI review.', file=sys.stderr)
        print(f'👉 Read {candidates_path}, fix the issues, then re-run.',
              file=sys.stderr)

    elif stage == 'done':
        print('✅ All done — proper noun check complete.', file=sys.stderr)


if __name__ == '__main__':
    main()
