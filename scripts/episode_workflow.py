#!/usr/bin/env python3
"""Single-episode proofread workflow orchestrator.

Two modes:
  text  — reference subtitles exist → dictionary fixes + AI review
  audio — no reference, audio only → VAD + Whisper (no guessing)

Usage:
  python episode_workflow.py EP064                    # Auto-detect mode
  python episode_workflow.py EP064 --mode audio        # Force audio mode
  python episode_workflow.py EP064 --mode text          # Force text mode
  python episode_workflow.py EP064 --dry-run            # Preview only
  python episode_workflow.py EP064 --step scan          # Show issues
  python episode_workflow.py EP064 --step audio         # VAD + Whisper only
  python episode_workflow.py EP064 --step diff          # Show changes
  python episode_workflow.py EP064 --no-backup          # Skip git backup
  python episode_workflow.py EP064 --project-dir <DIR>  # Explicit project root
"""

import argparse
import json
import os
import re
import subprocess
import sys

# UTF-8 safety on Windows
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# ── Path setup for importing from scripts dir ──
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from srt_utils import read_srt_file, write_srt_file, parse_srt_cue, build_srt_cue_lines


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def load_json(path):
    """Load a JSON file, return None if missing."""
    if not os.path.exists(path):
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def git_backup(project_dir, message):
    """Auto git add + commit. Returns commit hash or None."""
    try:
        subprocess.run(['git', 'add', '-A'], cwd=project_dir,
                       capture_output=True, timeout=10)
        result = subprocess.run(
            ['git', 'commit', '-m', message],
            cwd=project_dir, capture_output=True, text=True, timeout=10,
            encoding='utf-8', errors='replace'
        )
        if result.returncode == 0:
            # Extract short hash
            m = re.search(r'\[[\w-]+ ([a-f0-9]+)\]', result.stdout)
            return m.group(1)[:7] if m else 'ok'
        return 'ok'  # nothing to commit
    except Exception as e:
        return f'error: {e}'


def norm_ep(episode):
    """Normalize episode identifier: '64' → 'EP064', 'EP064' → 'EP064'."""
    episode = str(episode).strip().upper()
    if episode.startswith('EP'):
        return f'EP{int(episode[2:]):03d}'
    return f'EP{int(episode):03d}'


def detect_mode(project_dir):
    """Auto-detect workflow mode based on project resources.

    Returns:
        'text'  — 参考字幕/ 目录有文件（有准确参考字幕可对照校对）
        'audio' — 无参考字幕，只能靠 VAD + Whisper（不猜）

    注意：原始字幕/ 是备份目录，不是参考字幕。参考字幕是外部准确字幕
    （如官方翻译、人工校对版等），存放在 参考字幕/ 目录。
    """
    ref_dir = os.path.join(project_dir, '参考字幕')
    if os.path.isdir(ref_dir) and os.listdir(ref_dir):
        return 'text'
    return 'audio'


def find_video(project_dir, episode):
    """Find video file for an episode. Returns path or None.

    Searches common video directories and filename patterns.
    """
    ep_num = episode[2:]  # '064' from 'EP064'

    # Candidate directories
    video_dirs = [
        os.path.join(project_dir, 'video'),
        os.path.join(project_dir, 'videos'),
        r'E:\Animation\TV\[Anonymoose] 鉄腕アトム (DVD, 10bit)',
    ]

    # Common extensions
    exts = ('.mkv', '.mp4', '.avi', '.mov')

    for vdir in video_dirs:
        if not os.path.isdir(vdir):
            continue
        for fname in os.listdir(vdir):
            if ep_num in fname and fname.lower().endswith(exts):
                return os.path.join(vdir, fname)

    return None


def find_srt(project_dir, episode):
    """Find the SRT file for an episode. Returns (filename, full_path) or (None, None)."""
    target_dir = os.path.join(project_dir, 'AI审查后')
    ep_num = episode[2:]  # '064' from 'EP064'
    for fname in os.listdir(target_dir):
        if fname.endswith('.srt') and ep_num in fname:
            return fname, os.path.join(target_dir, fname)
    return None, None


def find_original_srt(project_dir, episode):
    """Find the original backup SRT. Returns full_path or None."""
    orig_dir = os.path.join(project_dir, '原始字幕')
    if not os.path.isdir(orig_dir):
        return None
    ep_num = episode[2:]
    for fname in os.listdir(orig_dir):
        if fname.endswith('.srt') and ep_num in fname:
            return os.path.join(orig_dir, fname)
    return None


# ═══════════════════════════════════════════════════════════════
# Step: scan — extract issues for one episode
# ═══════════════════════════════════════════════════════════════

def step_scan(project_dir, episode):
    """Extract and display issues for a single episode."""
    findings = load_json(os.path.join(project_dir, 'findings.json'))
    proj_dict = load_json(os.path.join(project_dir, 'project_dict.json'))

    if not findings:
        print('No findings.json found. Run unified_scanner.py first.')
        return None

    # Gather issues from per_episode_issues
    per_ep = findings.get('per_episode_issues', {})
    issues = per_ep.get(episode, [])

    # Also check findings_by_type (dedup by start time + text)
    findings_by_type = findings.get('findings', {})
    srt_name, _ = find_srt(project_dir, episode)
    seen_keys = {(i.get('start', ''), i.get('original_text', i.get('text', '')))
                 for i in issues}
    if srt_name:
        for ftype, items in findings_by_type.items():
            for item in items:
                if item.get('file') == srt_name:
                    key = (item.get('timecode', item.get('start', '')),
                           item.get('text', ''))
                    if key not in seen_keys:
                        seen_keys.add(key)
                        issues.append(item)

    if not issues:
        print(f'{episode}: no issues found.')
        return {'issues': [], 'findings_by_type': {}, 'project_dict': proj_dict}

    # Count types
    type_counts = {}
    for item in issues:
        t = item.get('type', 'unknown')
        type_counts[t] = type_counts.get(t, 0) + 1

    print(f'[scan] {episode}: {len(issues)} issues')
    for t, n in sorted(type_counts.items()):
        print(f'  {t}: {n}')

    # Check project_dict coverage
    if proj_dict:
        auto = proj_dict.get('auto', {})
        hit_words = set()
        for item in issues:
            text = item.get('original_text', item.get('text', ''))
            for word in re.findall(r'[a-zA-Z]{2,}', text.lower()):
                if word in auto:
                    hit_words.add(word)
        if hit_words:
            print(f'  Dict coverage: {len(hit_words)} words ({", ".join(sorted(hit_words))})')

    # Show each issue
    print()
    for item in sorted(issues, key=lambda x: x.get('start', x.get('timecode', ''))):
        ts = item.get('start', item.get('timecode', ''))
        text = item.get('original_text', item.get('text', ''))
        print(f'  {ts} | {text[:100]}')

    # Build findings_by_type filtered to this episode's file
    fbt = {}
    if srt_name:
        for ftype, items in findings_by_type.items():
            ep_items = [i for i in items if i.get('file') == srt_name]
            if ep_items:
                fbt[ftype] = ep_items

    return {'issues': issues, 'findings_by_type': fbt, 'project_dict': proj_dict,
            'srt_name': srt_name}


# ═══════════════════════════════════════════════════════════════
# Step: fix — generate fixes using romaji_fixer logic
# ═══════════════════════════════════════════════════════════════

def step_fix(scan_result, project_dir):
    """Generate romaji fixes for the episode's issues."""
    if not scan_result or not scan_result.get('findings_by_type'):
        print('[fix] No findings to fix.')
        return []

    try:
        from romaji_fixer import generate_dict_fixes
    except ImportError:
        print('[fix] ERROR: Cannot import romaji_fixer. Check scripts path.')
        return []

    fbt = scan_result['findings_by_type']
    proj_dict = scan_result.get('project_dict')

    fixes = generate_dict_fixes(fbt, project_dict=proj_dict)

    # Identify which words were hit and which were missed
    all_words = set()
    hit_words = set()
    for item in (fbt.get('mixed_romaji', []) + fbt.get('pure_romaji', [])):
        text = item.get('original_text', item.get('text', ''))
        for word in re.findall(r'[a-zA-Z]{2,}', text.lower()):
            all_words.add(word)

    for fix in fixes:
        note = fix.get('note', '')
        for word in all_words:
            if word.lower() in note.lower():
                hit_words.add(word)

    missed = all_words - hit_words
    # Filter to meaningful misses (not English OK words)
    missed_meaningful = {w for w in missed if len(w) > 1 and not w.startswith('ok')}

    total_issues = len(scan_result.get('issues', []))
    fixable = len(hit_words)
    unfixable = total_issues - len(fixes)

    print(f'[fix] {len(fixes)} fix(es) generated for {total_issues} issues')
    if hit_words:
        print(f'  [HIT]  {", ".join(sorted(hit_words))}')
    if missed_meaningful:
        print(f'  [MISS] {", ".join(sorted(missed_meaningful)[:15])}')
    if unfixable > 0:
        print(f'  -> {unfixable} issue(s) require Whisper Tier 1/2')

    return fixes


# ═══════════════════════════════════════════════════════════════
# Step: audio — VAD + Whisper for audio-only mode (no reference subs)
# ═══════════════════════════════════════════════════════════════

def step_audio(project_dir, episode, scan_result, dry_run=False):
    """Audio mode: dispatch garbled cues to VAD + Whisper Tier 1.

    Does NOT guess or use dictionaries — audio is the only source of truth.
    """
    if not scan_result or not scan_result.get('issues'):
        print('[audio] No garbled cues to process.')
        return []

    issues = scan_result['issues']
    print(f'[audio] {len(issues)} garbled cue(s) → VAD + Whisper')

    # Find video
    video_path = find_video(project_dir, episode)
    if not video_path:
        print('[audio] WARNING: No video found. Cannot run Whisper.')
        print('[audio] Garbled cues (manual review needed):')
        for item in sorted(issues, key=lambda x: x.get('start', x.get('timecode', ''))):
            ts = item.get('start', item.get('timecode', ''))
            text = item.get('original_text', item.get('text', ''))
            print(f'  {ts} | {text[:80]}')
        return []

    srt_name, srt_path = find_srt(project_dir, episode)
    if not srt_path:
        print('[audio] ERROR: SRT not found.')
        return []

    print(f'[audio] Video: {os.path.basename(video_path)}')
    print(f'[audio] SRT:   {srt_name}')

    # Build Whisper Tier 1 command
    whisper_cli = r'D:/software/video/whisper-cublas-12.4.0-bin-x64/whisper-cli.exe'
    model = r'D:/software/video/whisper-cublas-12.4.0-bin-x64/models/ggml-kotoba-whisper-v2.0-q5_0.bin'
    retry_model = r'D:/software/video/whisper-cublas-12.4.0-bin-x64/models/ggml-large-v3-q5_0.bin'
    reports_dir = os.path.join(project_dir, 'reports')

    cmd_parts = [
        'python', os.path.join(_SCRIPT_DIR, 'whisper_transcribe.py'),
        f'"{video_path}"', f'"{srt_path}"',
        f'--whisper-cli', f'"{whisper_cli}"',
        f'--model', f'"{model}"',
        f'--retry-model', f'"{retry_model}"',
        f'--update-report', f'"{reports_dir}"',
        '--json', '--separate-vocals',
    ]
    cmd = ' '.join(cmd_parts)

    if dry_run:
        print(f'\n[audio] DRY RUN — would execute:')
        print(f'  {cmd}')
        return issues

    # Execute Whisper Tier 1
    print(f'\n[audio] Running Whisper Tier 1...')
    try:
        result = subprocess.run(cmd, cwd=project_dir, shell=True,
                                capture_output=False, timeout=3600)
        if result.returncode != 0:
            print(f'[audio] Whisper exited with code {result.returncode}')
            return issues
    except subprocess.TimeoutExpired:
        print('[audio] Whisper timed out (>1h)')
        return issues
    except Exception as e:
        print(f'[audio] Whisper error: {e}')
        return issues

    print('[audio] Whisper Tier 1 complete.')
    return issues


# ═══════════════════════════════════════════════════════════════
# Step: apply — apply fixes to the SRT file
# ═══════════════════════════════════════════════════════════════

def step_apply(project_dir, episode, fixes, scan_result, no_backup=False):
    """Apply fixes to the episode's SRT file. Auto-backs up first."""
    srt_name, srt_path = find_srt(project_dir, episode)
    if not srt_path:
        print(f'[apply] ERROR: SRT not found for {episode}')
        return []

    if not fixes:
        print('[apply] No fixes to apply.')
        return []

    # Backup
    if not no_backup:
        commit_msg = f'备份：{episode} proofread ({len(fixes)} fixes)'
        hash_val = git_backup(project_dir, commit_msg)
        print(f'[apply] Git backup: {hash_val}')
    else:
        print('[apply] --no-backup: skipping git commit')

    # Load SRT
    lines = read_srt_file(srt_path)
    if lines is None:
        print(f'[apply] ERROR: Cannot read {srt_path}')
        return []

    # Build cue list with line tracking
    cues = []
    idx = 0
    while idx < len(lines):
        start_idx = idx
        cue, idx = parse_srt_cue(lines, idx)
        if cue is None:
            idx += 1
            continue
        cue['_start_line'] = start_idx
        cue['_end_line'] = idx - 1
        cues.append(cue)

    # Apply fixes (descending line order to prevent line shift)
    applied = []
    fixes_sorted = sorted(fixes, key=lambda f: f.get('line', 0), reverse=True)

    for fix in fixes_sorted:
        line_num = fix.get('line', 0)
        action = fix.get('action', 'replace_text')
        replacement = fix.get('replacement', '')

        if action == 'replace_text':
            # Find the cue containing this line
            line_idx = line_num - 1  # 0-based
            for cue in cues:
                if cue['_start_line'] <= line_idx <= cue['_end_line']:
                    old_text = cue.get('text', '')
                    # Only apply if the line still matches (prevent double-fix)
                    actual_line = lines[line_idx].strip() if line_idx < len(lines) else ''
                    if old_text != replacement:
                        cue['text'] = replacement
                        applied.append({
                            'start': cue.get('start', ''),
                            'original': old_text,
                            'corrected': replacement,
                            'note': fix.get('note', ''),
                        })
                    break

    # Rebuild and write
    new_lines = []
    for i, cue in enumerate(cues):
        new_lines.extend(build_srt_cue_lines(cue))

    write_srt_file(srt_path, new_lines)

    print(f'[apply] {len(applied)}/{len(fixes)} fixes applied to {srt_name}')

    # Log to report
    try:
        from update_report import upsert_entries
        report_path = os.path.join(project_dir, 'reports', '问题解决报告.md')
        entries = []
        for a in applied:
            entries.append({
                'ep': episode,
                'time': a['start'],
                'original': a['original'][:80],
                'corrected': a['corrected'][:80],
                'status': '✅',
            })
        # Mark applied fixes as step 15 (Whisper乱码修复)
        if entries:
            upsert_entries(report_path, step=15, entries=entries)
            print(f'[apply] Report: +{len(entries)} entries at step 15')

        # Mark remaining issues as step 16 (人工审查修正)
        remaining = []
        issues = scan_result.get('issues', []) if scan_result else []
        applied_starts = {a['start'] for a in applied}
        for item in issues:
            ts = item.get('start', item.get('timecode', ''))
            if ts not in applied_starts:
                remaining.append({
                    'ep': episode,
                    'time': ts,
                    'original': item.get('original_text', item.get('text', ''))[:80],
                    'corrected': '⬜',
                    'status': '⬜',
                })
        if remaining:
            upsert_entries(report_path, step=16, entries=remaining)
            print(f'[apply] Report: +{len(remaining)} entries at step 16')
    except Exception as e:
        print(f'[apply] Report update skipped: {e}')

    return applied


# ═══════════════════════════════════════════════════════════════
# Step: diff — show before/after comparison
# ═══════════════════════════════════════════════════════════════

def step_diff(project_dir, episode, scan_result, applied_fixes):
    """Show before/after comparison of changes."""
    if not scan_result:
        print('[diff] No scan data to compare.')
        return

    srt_name, srt_path = find_srt(project_dir, episode)
    if not srt_path:
        print('[diff] SRT not found.')
        return

    issues = scan_result.get('issues', [])
    if not issues:
        print('[diff] No issues.')
        return

    # Build a set of fixed start times
    fixed_starts = {a['start'] for a in applied_fixes} if applied_fixes else set()

    # Read current SRT text for each issue timecode
    lines = read_srt_file(srt_path)
    print(f'[diff] {episode} changes:')
    print()

    fixed_count = 0
    still_count = 0

    for item in sorted(issues, key=lambda x: x.get('start', x.get('timecode', ''))):
        ts = item.get('start', item.get('timecode', ''))
        orig = item.get('original_text', item.get('text', ''))

        # Find current text at this timecode
        current = orig  # default: unchanged
        if lines:
            for i, line in enumerate(lines):
                if ts in line:
                    # Text is typically 2 lines after the timecode
                    for offset in [1, 2]:
                        if i + offset < len(lines):
                            txt = lines[i + offset].strip()
                            if txt and '-->' not in txt:
                                current = txt
                                break
                    break

        if orig != current:
            print(f'  [FIXED]  {ts}')
            print(f'    was: {orig[:80]}')
            print(f'    now: {current[:80]}')
            fixed_count += 1
        else:
            print(f'  [STILL]  {ts} | {orig[:80]}')
            still_count += 1

    print()
    print(f'  Fixed: {fixed_count}  |  Still broken: {still_count}  |  Total: {len(issues)}')


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description='Single-episode subtitle proofread workflow',
        usage='python episode_workflow.py EP064 [--mode text|audio|auto] [--step ...] [--dry-run]'
    )
    parser.add_argument('episode', help='Episode number (e.g., EP064 or 64)')
    parser.add_argument('--mode', choices=['text', 'audio', 'auto'], default='auto',
                        help='Workflow mode: text=reference subs, audio=VAD+Whisper, '
                             'auto=detect from project (default)')
    parser.add_argument('--step', choices=['scan', 'fix', 'audio', 'apply', 'diff'],
                        help='Run a specific step only (default: all)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview only, no file changes')
    parser.add_argument('--no-backup', action='store_true',
                        help='Skip git backup before modifying SRT')
    parser.add_argument('--project-dir', default=None,
                        help='Project root directory (default: CWD)')

    args = parser.parse_args()

    episode = norm_ep(args.episode)
    project_dir = args.project_dir or os.getcwd()

    # Detect mode
    if args.mode == 'auto':
        mode = detect_mode(project_dir)
    else:
        mode = args.mode

    print('=' * 55)
    print(f'  {episode} — Proofread Workflow [{mode.upper()} mode]')
    print('=' * 55)

    # Default pipeline per mode
    if args.step is None:
        if mode == 'text':
            steps = ['scan', 'fix', 'apply', 'diff']
        else:  # audio
            steps = ['scan', 'audio', 'apply', 'diff']
    else:
        steps = [args.step]

    scan_result = None
    fixes = []
    applied = []

    for step in steps:
        if step == 'scan':
            scan_result = step_scan(project_dir, episode)

        elif step == 'fix':
            if mode == 'audio':
                print('[fix] Skipped — audio mode does not use dictionary fixes.')
                print('[fix] Use --step audio for VAD+Whisper pipeline.')
                continue
            fixes = step_fix(scan_result, project_dir)
            if args.dry_run:
                for f in fixes:
                    print(f'  [DRY] {f.get("note", "")}: {f.get("replacement", "")[:60]}')

        elif step == 'audio':
            if mode == 'text':
                print('[audio] Skipped — text mode uses dictionary fixes (--step fix).')
                continue
            fixes = step_audio(project_dir, episode, scan_result, dry_run=args.dry_run)

        elif step == 'apply':
            if args.dry_run:
                print(f'[apply] DRY RUN — {len(fixes)} fixes would be applied')
                print(f'[apply] Would backup, then write to SRT')
            else:
                applied = step_apply(project_dir, episode, fixes, scan_result, args.no_backup)

        elif step == 'diff':
            step_diff(project_dir, episode, scan_result, applied)

    # Final summary
    if not args.dry_run and 'apply' in steps and applied:
        remaining = len(scan_result.get('issues', [])) - len(applied) if scan_result else 0
        print()
        print('=' * 55)
        print(f'  Done: {len(applied)} fixed, {remaining} pending')
        print('=' * 55)


if __name__ == '__main__':
    main()
