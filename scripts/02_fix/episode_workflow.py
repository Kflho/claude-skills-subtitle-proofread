#!/usr/bin/env python3
"""Single-episode subtitle proofread workflow orchestrator (v4.0).

Both modes share the same first half (VAD + Whisper from audio).
Only the verification step differs:

  audio — no reference subs → done after Whisper
  text  — has reference subs → translate reference → compare → review

Project-aware script selection:
  SRT only → skip all ASS steps
  ASS format → auto-enable --repair-ass
  Chinese target → auto-suggest trad_to_simp, garbled_detect, format_detect
  Japanese target → skip semantic-layer detection (source=target)

Usage:
  python episode_workflow.py EP064                    # Auto-detect mode
  python episode_workflow.py EP064 --mode audio        # Force audio mode
  python episode_workflow.py EP064 --mode text          # Force text mode
  python episode_workflow.py EP064 --dry-run            # Preview only
  python episode_workflow.py EP064 --step scan          # Show garbled cues
  python episode_workflow.py EP064 --step audio         # VAD + Whisper only
  python episode_workflow.py EP064 --step translate     # Translate reference SRT
  python episode_workflow.py EP064 --step compare       # Compare Whisper vs ref
  python episode_workflow.py EP064 --step diff          # Show changes
  python episode_workflow.py EP064 --repair-ass         # Run ASS repair scripts
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
_ROOT_DIR = os.path.dirname(_SCRIPT_DIR)  # scripts/
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from lib.srt_utils import read_srt_file, write_srt_file, parse_srt_cue, build_srt_cue_lines


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


def detect_format(project_dir):
    """Detect subtitle format(s) in the project.

    Returns:
        dict: {
            'has_ass': bool,     # 目录含 .ass 文件
            'has_srt': bool,     # 目录含 .srt 文件
            'primary': 'ass' | 'srt' | 'mixed',
            'ass_count': int,
            'srt_count': int,
        }
    """
    target_dir = os.path.join(project_dir, 'AI审查后')
    if not os.path.isdir(target_dir):
        return {'has_ass': False, 'has_srt': False, 'primary': None, 'ass_count': 0, 'srt_count': 0}

    ass_count = 0
    srt_count = 0
    for fname in os.listdir(target_dir):
        lname = fname.lower()
        if lname.endswith('.ass'):
            ass_count += 1
        elif lname.endswith('.srt'):
            srt_count += 1

    if ass_count > srt_count:
        primary = 'ass'
    elif srt_count > ass_count:
        primary = 'srt'
    else:
        primary = 'mixed' if ass_count > 0 else None

    return {
        'has_ass': ass_count > 0,
        'has_srt': srt_count > 0,
        'primary': primary,
        'ass_count': ass_count,
        'srt_count': srt_count,
    }


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
# Helpers
# ═══════════════════════════════════════════════════════════════

def _run_clean(project_dir):
    """Run clean_empty_cues on the target directory."""
    target = os.path.join(project_dir, 'AI审查后')
    cmd = ' '.join([
        'python', os.path.join(_ROOT_DIR, 'utils', 'clean_empty_cues.py'),
        f'--target-dir', f'"{target}"',
    ])
    print(f'[clean] Removing empty cues...')
    try:
        subprocess.run(cmd, cwd=project_dir, shell=True, timeout=120)
    except Exception as e:
        print(f'[clean] Error: {e}')


# ═══════════════════════════════════════════════════════════════
# Step: repair-ass — run ASS format repair scripts
# ═══════════════════════════════════════════════════════════════

def step_repair_ass(project_dir, dry_run=False):
    """Run ASS format repair on the target directory.

    Calls ass_repair.py --check all which covers:
    names, styles, drawing, comment, oped (if --oped-config provided).
    """
    fmt = detect_format(project_dir)
    if not fmt['has_ass']:
        print('[repair-ass] No .ass files found — skipping ASS repair.')
        print('[repair-ass] Use unified_scanner.py for SRT projects.')
        return

    target = os.path.join(project_dir, 'AI审查后')
    script_path = os.path.join(_ROOT_DIR, '05_ass', 'ass_repair.py')

    if not os.path.exists(script_path):
        print('[repair-ass] ERROR: ass_repair.py not found.')
        return

    cmd_parts = [
        'python', script_path,
        '--target-dir', f'"{target}"',
    ]

    # Add oped config if present
    oped_config = os.path.join(project_dir, 'oped_config.json')
    if os.path.exists(oped_config):
        cmd_parts.extend(['--oped-config', f'"{oped_config}"'])

    cmd = ' '.join(cmd_parts)

    print(f'[repair-ass] Running ass_repair.py --check all...')
    if dry_run:
        print(f'  DRY RUN: {cmd}')
    else:
        try:
            subprocess.run(cmd, cwd=project_dir, shell=True, timeout=300)
        except subprocess.TimeoutExpired:
            print('  TIMEOUT: ass_repair.py')
        except Exception as e:
            print(f'  ERROR: ass_repair.py: {e}')

    print('[repair-ass] Done. Review JSON outputs, then use apply_fixes.py.')


# ═══════════════════════════════════════════════════════════════
# Step: scan — extract issues for one episode
# ═══════════════════════════════════════════════════════════════

def step_scan(project_dir, episode):
    """Extract and display garbled cues for a single episode (v4.0)."""
    findings_path = os.path.join(project_dir, 'temp', 'scans', 'findings.json')
    findings = load_json(findings_path)

    if not findings:
        print(f'No {findings_path} found. Run unified_scanner.py first:')
        print(f'  python scripts/unified_scanner.py --target-dir AI审查后/ \\')
        print(f'    --output-findings temp/scans/findings.json \\')
        print(f'    --output-issues temp/scans/issues/')
        return None

    # Gather issues from per_episode_issues
    per_ep = findings.get('per_episode_issues', {})
    issues = per_ep.get(episode, [])

    if not issues:
        print(f'{episode}: no garbled cues found.')
        return {'issues': [], 'srt_name': None}

    print(f'[scan] {episode}: {len(issues)} garbled cue(s) → VAD + Whisper')

    # Show each issue
    print()
    for item in sorted(issues, key=lambda x: x.get('start', '')):
        ts = item.get('start', '')
        text = item.get('original_text', '')
        print(f'  {ts} | {text[:100]}')

    srt_name, _ = find_srt(project_dir, episode)
    return {'issues': issues, 'srt_name': srt_name}


# ═══════════════════════════════════════════════════════════════
# Step: translate — 百度翻译参考字幕→日语 (text mode only)
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# Step: translate — 百度翻译参考字幕→日语 (text mode only)
# ═══════════════════════════════════════════════════════════════

def step_translate(project_dir, episode, scan_result, dry_run=False):
    """Translate reference subtitles to Japanese for comparison (text mode)."""
    if not scan_result or not scan_result.get('issues'):
        print('[translate] No issues — skipping translation.')
        return None

    ref_dir = os.path.join(project_dir, '参考字幕')
    if not os.path.isdir(ref_dir):
        print('[translate] No 参考字幕/ directory.')
        return None

    # Find reference SRT
    ep_num = episode[2:]
    ref_path = None
    for fname in os.listdir(ref_dir):
        if fname.endswith('.srt') and ep_num in fname:
            ref_path = os.path.join(ref_dir, fname)
            break

    if not ref_path:
        print(f'[translate] No reference SRT found for {episode}.')
        return None

    # Output path
    out_dir = os.path.join(project_dir, 'temp', 'translations')
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f'{episode}_translated.srt')

    cmd_parts = [
        'python', os.path.join(_SCRIPT_DIR, 'translate_srt.py'),
        f'"{ref_path}"',
        f'--output', f'"{out_path}"',
        '--to', 'ja',
    ]
    cmd = ' '.join(cmd_parts)

    if dry_run:
        print(f'[translate] DRY RUN — would execute:')
        print(f'  {cmd}')
        return out_path

    print(f'[translate] Translating: {os.path.basename(ref_path)} → ja')
    try:
        result = subprocess.run(cmd, cwd=project_dir, shell=True,
                                capture_output=False, timeout=1800)
        if result.returncode != 0:
            print(f'[translate] Translation failed (exit {result.returncode})')
            return None
    except Exception as e:
        print(f'[translate] Error: {e}')
        return None

    print(f'[translate] → {out_path}')
    return out_path


# ═══════════════════════════════════════════════════════════════
# Step: compare — 对照 Whisper 输出与翻译后参考字幕 (text mode only)
# ═══════════════════════════════════════════════════════════════

def step_compare(project_dir, episode, scan_result, translated_path, dry_run=False):
    """Compare Whisper output with translated reference subtitles."""
    if not translated_path or not os.path.exists(translated_path):
        print('[compare] No translated reference available.')
        return []

    srt_name, srt_path = find_srt(project_dir, episode)
    if not srt_path:
        print('[compare] SRT not found.')
        return []

    out_dir = os.path.join(project_dir, 'temp', 'compares')
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f'{episode}_diff.json')

    cmd_parts = [
        'python', os.path.join(_SCRIPT_DIR, 'compare_srt.py'),
        f'"{srt_path}"', f'"{translated_path}"',
        f'--output', f'"{out_path}"',
    ]
    cmd = ' '.join(cmd_parts)

    if dry_run:
        print(f'[compare] DRY RUN — would execute:')
        print(f'  {cmd}')
        return []

    print(f'[compare] Comparing: {srt_name} vs translated reference')
    try:
        result = subprocess.run(cmd, cwd=project_dir, shell=True,
                                capture_output=False, timeout=600)
        if result.returncode != 0:
            print(f'[compare] Comparison failed (exit {result.returncode})')
            return []
    except Exception as e:
        print(f'[compare] Error: {e}')
        return []

    # Load differences
    diffs = load_json(out_path) if os.path.exists(out_path) else []
    if isinstance(diffs, dict):
        diffs = diffs.get('differences', [])

    match_count = sum(1 for d in diffs if d.get('verdict') == 'match')
    mismatch_count = sum(1 for d in diffs if d.get('verdict') != 'match')
    print(f'[compare] {len(diffs)} cues: {match_count} match, {mismatch_count} need review')
    return diffs


# ═══════════════════════════════════════════════════════════════
# Step: review — noun consistency check
# ═══════════════════════════════════════════════════════════════

def step_review(project_dir, episode, dry_run=False):
    """Check proper noun consistency against proper-nouns.md."""
    srt_name, srt_path = find_srt(project_dir, episode)
    if not srt_path:
        print('[review] SRT not found.')
        return None

    noun_table = os.path.join(project_dir, 'reports', 'proper-nouns.md')
    if not os.path.exists(noun_table):
        print('[review] No proper-nouns.md. Skip.')
        return None

    out_dir = os.path.join(project_dir, 'temp', 'reviews')
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f'{episode}_nouns.json')

    cmd = ' '.join([
        'python', os.path.join(_ROOT_DIR, '03_nouns', 'noun_checker.py'),
        f'"{srt_path}"',
        f'--noun-table', f'"{noun_table}"',
        f'--output', f'"{out_path}"',
    ])

    if dry_run:
        print(f'[review] DRY RUN — {cmd}')
        return None

    print(f'[review] Noun check: {srt_name}')
    try:
        subprocess.run(cmd, cwd=project_dir, shell=True, timeout=300)
    except Exception as e:
        print(f'[review] Error: {e}')
        return None

    report = load_json(out_path)
    if report:
        stats = report.get('stats', {})
        m = stats.get('mismatch', 0)
        u = stats.get('unknown', 0) + stats.get('unknown_katakana', 0)
        if m: print(f'[review] ⚠ {m} mismatches!')
        if u: print(f'[review] ℹ {u} unknown nouns')
        if not m and not u: print('[review] ✓ All clear.')
    return report


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

    output_json = os.path.join(project_dir, 'temp', 'scans', f'{episode}_fixes.json')
    cmd_parts = [
        'python', os.path.join(_SCRIPT_DIR, 'whisper_pipeline.py'),
        f'"{video_path}"', f'"{srt_path}"',
        f'--whisper-cli', f'"{whisper_cli}"',
        f'--model', f'"{model}"',
        f'--retry-model', f'"{retry_model}"',
        f'--output', f'"{output_json}"',
        '--separate-vocals',
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

    # Read fixes from whisper_pipeline output JSON
    output_json = os.path.join(project_dir, 'temp', 'scans', f'{episode}_fixes.json')
    fixes = []
    if os.path.exists(output_json):
        data = load_json(output_json)
        if data:
            fixes = data.get('fixes', [])
            vad_deleted = data.get('deleted_by_vad', 0)
            whisper_fixed = data.get('fixed', 0)
            print(f'[audio] VAD deleted: {vad_deleted}, Whisper fixed: {whisper_fixed}, '
                  f'Unmatched: {data.get("unmatched", 0)}', file=sys.stderr)
    return fixes


# ═══════════════════════════════════════════════════════════════
# Step: apply — apply fixes to the SRT file
# ═══════════════════════════════════════════════════════════════

def step_apply(project_dir, episode, fixes, scan_result, no_backup=False):
    """Log fixes to report. Whisper pipeline already wrote SRT — we just report."""
    srt_name, srt_path = find_srt(project_dir, episode)

    if not fixes:
        print('[apply] No fixes to report.')
        return []

    # Count by confidence
    fixed = [f for f in fixes if f.get('confidence') in ('high', 'retry')]
    unfixed = [f for f in fixes if f.get('confidence') == 'none']

    print(f'[apply] {len(fixed)} fixed, {len(unfixed)} unmatched '
          f'(already written to SRT by Whisper)', file=sys.stderr)

    # Log to report
    try:
        from utils.update_report import upsert_entries
        report_path = os.path.join(project_dir, 'reports', '问题解决报告.md')

        # Step 15: applied Whisper fixes
        step15_entries = []
        for f in fixed:
            step15_entries.append({
                'ep': episode,
                'time': f.get('start', ''),
                'original': f.get('original', '')[:80],
                'corrected': f.get('replacement', '')[:80],
                'status': '✅',
            })
        if step15_entries:
            upsert_entries(report_path, step=15, entries=step15_entries)

        # Step 16: unfixable → human review
        step16_entries = []
        for f in unfixed:
            step16_entries.append({
                'ep': episode,
                'time': f.get('start', ''),
                'original': f.get('original', '')[:80],
                'corrected': '',
                'status': '⬜',
            })
        if step16_entries:
            upsert_entries(report_path, step=16, entries=step16_entries)
            print(f'[apply] Report: +{len(step15_entries)} step15, '
                  f'+{len(step16_entries)} step16 (needs human review)',
                  file=sys.stderr)

    except Exception as e:
        print(f'[apply] Report update error: {e}', file=sys.stderr)

    return fixed


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

    # Build timecode→text map from current SRT
    current_map = {}
    cue_pat = re.compile(
        r'(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*\d{2}:\d{2}:\d{2}[.,]\d{3}\s*\n(.+?)(?:\n\n|\n?\Z)',
        re.MULTILINE | re.DOTALL
    )
    for m in cue_pat.finditer('\n'.join(lines)):
        tc = m.group(1).replace(',', '.')
        txt = m.group(2).strip().replace('\n', ' ')
        current_map[tc] = txt

    for item in sorted(issues, key=lambda x: x.get('start', x.get('timecode', ''))):
        ts = item.get('start', item.get('timecode', ''))
        orig = item.get('original_text', item.get('text', ''))
        current = current_map.get(ts.replace(',', '.'), orig)

        if current and current != orig:
            print(f'  [FIXED]  {ts}')
            print(f'    was: {orig[:80]}')
            print(f'    now: {current[:80]}')
            fixed_count += 1
        elif not current or current == orig:
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
    parser.add_argument('--step', choices=['scan', 'audio', 'review', 'translate', 'compare', 'apply', 'diff', 'clean', 'repair-ass'],
                        help='Run a specific step only (default: all)')
    parser.add_argument('--repair-ass', action='store_true',
                        help='Run ASS format repair scripts (names/styles/drawing/comment/oped detect)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview only, no file changes')
    parser.add_argument('--no-backup', action='store_true',
                        help='Skip git backup before modifying SRT')
    parser.add_argument('--project-dir', default=None,
                        help='Project root directory (default: CWD)')
    parser.add_argument('--all', action='store_true',
                        help='Process all episodes with issues (batch mode)')
    parser.add_argument('--limit', type=int, default=0,
                        help='Max episodes to process in --all mode (0=unlimited)')

    args = parser.parse_args()

    episode = norm_ep(args.episode)
    project_dir = args.project_dir or os.getcwd()

    # Detect mode
    if args.mode == 'auto':
        mode = detect_mode(project_dir)
    else:
        mode = args.mode

    # --all batch mode: process all episodes with issues
    if args.all:
        findings = load_json(os.path.join(project_dir, 'temp', 'scans', 'findings.json'))
        if not findings:
            print('No findings.json found. Run unified_scanner first.')
            return
        episodes = sorted(findings.get('per_episode_issues', {}).keys())
        if args.limit > 0:
            episodes = episodes[:args.limit]
        print(f'Batch mode: {len(episodes)} episodes')
        for i, ep in enumerate(episodes):
            print(f'\n{"="*55}')
            print(f'  [{i+1}/{len(episodes)}] {ep}')
            print(f'{"="*55}')
            _run_pipeline(project_dir, ep, mode, args)
        # Final cleanup
        print(f'\n{"="*55}')
        print(f'  Cleanup — 清理空行')
        _run_clean(project_dir)
        return

    # Single episode mode
    print('=' * 55)
    print(f'  {episode} — Proofread Workflow [{mode.upper()} mode]')
    print('=' * 55)
    _run_pipeline(project_dir, episode, mode, args)


def _run_pipeline(project_dir, episode, mode, args):
    """Execute the proofread pipeline for one episode."""
    # Auto-detect format for logging
    fmt = detect_format(project_dir)
    if fmt['primary'] == 'ass':
        print(f'  Format: ASS ({fmt["ass_count"]} files) — ASS repair available via --repair-ass')
    elif fmt['primary'] == 'srt':
        print(f'  Format: SRT ({fmt["srt_count"]} files) — ASS scripts will be skipped')

    # Default pipeline: detect → fix (text first, cheaper) → whisper (fallback) → apply → verify → clean
    if args.step is None:
        if mode == 'text':
            steps = ['scan', 'translate', 'compare', 'review', 'apply', 'diff', 'clean']
        else:  # audio
            steps = ['scan', 'audio', 'review', 'apply', 'diff', 'clean']
        # Append ASS repair if requested
        if args.repair_ass and fmt['has_ass']:
            steps.insert(-1, 'repair-ass')  # before 'clean'
    else:
        steps = [args.step]

    scan_result = None
    fixes = []
    applied = []
    translated_path = None
    diffs = []

    for step in steps:
        if step == 'scan':
            scan_result = step_scan(project_dir, episode)

        elif step == 'audio':
            fixes = step_audio(project_dir, episode, scan_result, dry_run=args.dry_run)

        elif step == 'review':
            step_review(project_dir, episode, dry_run=args.dry_run)

        elif step == 'translate':
            if mode == 'audio':
                print('[translate] Skipped — audio mode has no reference subtitles.')
                continue
            translated_path = step_translate(project_dir, episode, scan_result,
                                             dry_run=args.dry_run)

        elif step == 'compare':
            if mode == 'audio':
                print('[compare] Skipped — audio mode has no reference subtitles.')
                continue
            diffs = step_compare(project_dir, episode, scan_result, translated_path,
                                dry_run=args.dry_run)

        elif step == 'apply':
            if args.dry_run:
                print(f'[apply] DRY RUN — {len(fixes)} fixes would be applied')
                print(f'[apply] Would backup, then write to SRT')
            else:
                applied = step_apply(project_dir, episode, fixes, scan_result, args.no_backup)

        elif step == 'diff':
            step_diff(project_dir, episode, scan_result, applied)

        elif step == 'clean':
            _run_clean(project_dir)

        elif step == 'repair-ass':
            step_repair_ass(project_dir, dry_run=args.dry_run)

    # Final summary
    if not args.dry_run and 'apply' in steps and applied:
        remaining = len(scan_result.get('issues', [])) - len(applied) if scan_result else 0
        print()
        print('=' * 55)
        print(f'  Done: {len(applied)} fixed, {remaining} pending')
        print('=' * 55)


if __name__ == '__main__':
    main()
