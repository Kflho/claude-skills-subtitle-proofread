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
from datetime import datetime

# ── Path setup for importing from scripts dir ──
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_SCRIPT_DIR)  # scripts/
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from lib.srt_utils import read_srt_file, write_srt_file, parse_srt_cue, build_srt_cue_lines
from lib.whisper_utils import to_seconds, setup_windows_utf8
setup_windows_utf8()


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


def find_video(project_dir, episode, video_dir=None):
    """Find video file for an episode. Returns path or None.

    Searches: explicit video_dir > project/video > project/videos.
    """
    ep_num = episode[2:]  # '064' from 'EP064'
    exts = ('.mkv', '.mp4', '.avi', '.mov')

    # Candidate directories (explicit first, then project-local)
    candidates = []
    if video_dir:
        candidates.append(video_dir)
    candidates.extend([
        os.path.join(project_dir, 'video'),
        os.path.join(project_dir, 'videos'),
    ])

    for vdir in candidates:
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
    script_path = os.path.join(_ROOT_DIR, 'ass', 'ass_repair.py')

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
    """Compare current SRT with translated reference, apply fixes for mismatches.

    Delegates to Fixer.fix_by_reference() which handles comparison +
    SRT write + report update in one step.
    """
    if not translated_path or not os.path.exists(translated_path):
        print('[compare] No translated reference available.')
        return []

    if dry_run:
        print(f'[compare] DRY RUN — would call Fixer.fix_by_reference()')
        return []

    from fix.fix_orchestrator import Fixer

    fixer = Fixer(episode, project_dir)
    report = fixer.fix_by_reference(translated_path)
    print(f'[compare] {report.applied} fixed, {report.failed} suspicious')

    return report


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
        'python', os.path.join(_ROOT_DIR, 'nouns', 'noun_checker.py'),
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

def step_audio(project_dir, episode, scan_result, dry_run=False, video_dir=None):
    """Audio mode: dispatch garbled cues to VAD + Whisper.

    Delegates to Fixer.fix_by_whisper() which handles the full pipeline:
    VAD clean → build clusters → Tier 1/2 Whisper → apply to SRT → update report.
    """
    if not scan_result or not scan_result.get('issues'):
        print('[audio] No garbled cues to process.')
        return []

    issues = scan_result['issues']
    print(f'[audio] {len(issues)} garbled cue(s) → VAD + Whisper')

    if dry_run:
        print(f'\n[audio] DRY RUN — would call Fixer.fix_by_whisper()')
        return issues

    from fix.fix_orchestrator import Fixer

    fixer = Fixer(episode, project_dir, video_dir=video_dir)
    if fixer.is_clean():
        print('[audio] Already clean — nothing to fix.')
        return []

    print(f'\n[audio] Running Whisper via Fixer...')
    report = fixer.fix_by_whisper(separate_vocals=True)
    print(f'[audio] Done: {report.applied} fixed, {report.ai_review} AI review, '
          f'{report.failed} unfixable')

    # Return fixes from report for backward compatibility with step_apply
    return report


# ═══════════════════════════════════════════════════════════════
# Step: apply — apply fixes to the SRT file + AI confidence review
# ═══════════════════════════════════════════════════════════════

# Thresholds for flagging low-confidence Whisper output for AI review
AI_REVIEW_AVG_LOGPROB_THRESHOLD = -1.0     # avg_logprob below this → uncertain
AI_REVIEW_COMPRESSION_THRESHOLD = 2.0       # compression_ratio above this → hallucination risk
AI_REVIEW_NO_SPEECH_THRESHOLD = 0.4         # no_speech_prob above this → might not be speech


def step_apply(project_dir, episode, fixes_or_report, scan_result, no_backup=False):
    """Log AI confidence review JSON for low-confidence Whisper items.

    Note: SRT writing and report updates are now handled internally by
    Fixer.fix_by_whisper(). This step only generates the per-episode
    AI review JSON for --step ai-review.

    Args:
        fixes_or_report: FixReport from step_audio(), or legacy fixes list
    """
    # Handle legacy fixes list (backward compat)
    if isinstance(fixes_or_report, list):
        fixes = fixes_or_report
        if not fixes:
            print('[apply] No fixes to report.')
            return [], []
        fixed = [f for f in fixes if f.get('confidence') in ('high', 'retry')]
        unfixed = [f for f in fixes if f.get('confidence') == 'none']
    else:
        # FixReport from Fixer
        report = fixes_or_report
        if report.applied == 0 and report.failed == 0:
            print('[apply] No fixes to report.')
            return [], []
        # AI review items are in report.details
        ai_review_items = report.details if isinstance(report.details, list) else []
        print(f'[apply] {report.applied} fixed, {report.ai_review} AI review, '
              f'{report.failed} unfixable (already applied to SRT & report)')
        # Generate AI review JSON if needed
        if ai_review_items:
            _write_ai_review_json(project_dir, episode, ai_review_items)
        return [], ai_review_items

    srt_name, srt_path = find_srt(project_dir, episode)

    print(f'[apply] {len(fixed)} fixed, {len(unfixed)} unmatched '
          f'(already written to SRT by Whisper)', file=sys.stderr)

    # ── Filter low-confidence items from "fixed" for AI review ──
    ai_review_items = []
    for f in fixed:
        alp = f.get('avg_logprob')
        cr = f.get('compression_ratio')
        nsp = f.get('no_speech_prob', -1.0)
        reasons = []
        if alp is not None and alp < AI_REVIEW_AVG_LOGPROB_THRESHOLD:
            reasons.append(f'avg_logprob={alp:.2f}')
        if cr is not None and cr > AI_REVIEW_COMPRESSION_THRESHOLD:
            reasons.append(f'compression_ratio={cr:.1f}')
        if nsp >= 0 and nsp > AI_REVIEW_NO_SPEECH_THRESHOLD:
            reasons.append(f'no_speech_prob={nsp:.2f}')
        if reasons:
            ai_review_items.append({**f, 'flag_reasons': reasons})

    if ai_review_items:
        print(f'[apply] ⚠ {len(ai_review_items)}/{len(fixed)} fixed items have low '
              f'Whisper confidence → AI review needed', file=sys.stderr)

    # ── Read context for AI review items ──
    cues = None
    if ai_review_items and srt_path:
        try:
            from lib.srt_utils import read_srt_file, parse_srt_cue
            cues = read_srt_file(srt_path)
            if isinstance(cues, list) and cues and isinstance(cues[0], str):
                cues = parse_srt_cue(cues)
        except ImportError:
            pass

    # ── Generate AI review JSON ──
    _write_ai_review_json(project_dir, episode, ai_review_items, cues)

    # ── Log to report (legacy path — Fixer doesn't handle this for old callers) ──
    try:
        from utils.update_report import upsert_entries
        report_path = os.path.join(project_dir, 'reports', '问题解决报告.md')

        high_conf = [f for f in fixed if f not in ai_review_items]
        if high_conf:
            upsert_entries(report_path, step='2', entries=[
                {'ep': episode, 'time': f.get('start', ''),
                 'original': f.get('original', '')[:80],
                 'corrected': f.get('replacement', '')[:80],
                 'status': '✅'}
                for f in high_conf
            ])

        if ai_review_items:
            upsert_entries(report_path, step='2.5', entries=[
                {'ep': episode, 'time': f.get('start', ''),
                 'original': f.get('original', '')[:80],
                 'corrected': f.get('replacement', '')[:80],
                 'status': '⬜'}
                for f in ai_review_items
            ])

        if unfixed:
            upsert_entries(report_path, step='6', entries=[
                {'ep': episode, 'time': f.get('start', ''),
                 'original': f.get('original', '')[:80],
                 'corrected': '', 'status': '⬜'}
                for f in unfixed
            ])
    except Exception as e:
        print(f'[apply] Report update error: {e}', file=sys.stderr)

    return fixed, ai_review_items


def _write_ai_review_json(project_dir, episode, ai_review_items, cues=None):
    """Write per-episode AI review JSON file."""
    if not ai_review_items:
        return

    review_data = {
        'episode': episode,
        'generated': datetime.now().isoformat(),
        'total': len(ai_review_items),
        'items': [],
    }
    for f in ai_review_items:
        item = {
            'start': f.get('start', ''),
            'end': f.get('end', ''),
            'original': f.get('original', '')[:120],
            'replacement': f.get('replacement', '')[:120],
            'pipeline_confidence': f.get('confidence', '?'),
            'model_tier': f.get('model', '?'),
            'avg_logprob': f.get('avg_logprob'),
            'no_speech_prob': f.get('no_speech_prob'),
            'compression_ratio': f.get('compression_ratio'),
            'flag_reasons': f.get('flag_reasons', []),
        }
        # Add context
        if cues:
            try:
                start_s = to_seconds(f.get('start', '00:00:00.000'))
                prev_text, next_text = '', ''
                for c in cues:
                    cs = c.get('start_s', 0) if isinstance(c, dict) else 0
                    ct = c.get('text', '') if isinstance(c, dict) else ''
                    if cs < start_s - 1:
                        prev_text = ct
                    elif cs > start_s + 1 and not next_text:
                        next_text = ct
                item['context_before'] = prev_text[:100] if prev_text else None
                item['context_after'] = next_text[:100] if next_text else None
            except Exception:
                pass
        review_data['items'].append(item)

    review_dir = os.path.join(project_dir, 'temp', 'scans')
    os.makedirs(review_dir, exist_ok=True)
    review_path = os.path.join(review_dir, f'{episode}_ai_review.json')
    with open(review_path, 'w', encoding='utf-8') as f:
        json.dump(review_data, f, ensure_ascii=False, indent=2)
    print(f'[apply] AI review data → {review_path}', file=sys.stderr)


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
# Step: ai-review — print AI review prompt for Claude
# ═══════════════════════════════════════════════════════════════

def _print_ai_review_prompt(project_dir, episode, ai_review_items):
    """Print a structured prompt for Claude to review low-confidence Whisper fixes.

    Called via: python episode_workflow.py EPxxx --step ai-review
    """
    review_path = os.path.join(project_dir, 'temp', 'scans', f'{episode}_ai_review.json')
    review_data = load_json(review_path)

    if not review_data or not review_data.get('items'):
        print('[ai-review] No items flagged for AI review.')
        return

    items = review_data['items']
    print(f'\n{"="*55}')
    print(f'  AI 置信度审查 — {episode} ({len(items)} items)')
    print(f'{"="*55}\n')

    print('以下是 Whisper 返回了结果但模型自身置信度偏低的条目。')
    print('请逐条审查，判断 Whisper 的输出是否可信。\n')

    for i, item in enumerate(items):
        print(f'─── [{i+1}/{len(items)}] {item["start"]} ───')
        print(f'原文:      {item["original"]}')
        print(f'Whisper:   {item["replacement"]}')
        print(f'置信度:    {item["pipeline_confidence"]} (tier={item["model_tier"]})')
        flags = item.get('flag_reasons', [])
        if flags:
            print(f'⚠ 标记原因: {", ".join(flags)}')
        if item.get('context_before'):
            print(f'上文:      {item["context_before"]}')
        if item.get('context_after'):
            print(f'下文:      {item["context_after"]}')
        print()

    print(f'{"="*55}')
    print('审查规则（逐条判断）：')
    print()
    print('  1. Whisper 输出通顺、上下文合理 → ✅ 保留（加入 L2 ✅ 报告）')
    print('  2. Whisper 输出有问题但可修正 → ✏️  给出修正文本')
    print('  3. Whisper 输出完全不对且无法修正 → ⬜ 升级到 L6 人工审查')
    print()
    print('输出格式（每行一条，| 分隔）：')
    print('  状态 | 时间码 | 修正文本')
    print('  ✅    | 00:02:00.490 | （保留原文则留空）')
    print('  ✏️    | 00:02:05.120 | 正しいテキスト')
    print('  ⬜    | 00:02:10.300 |')
    print()
    print('审查完成后运行 apply_fixes.py 应用修正。')
    print(f'数据文件: {review_path}')
    print(f'{"="*55}\n')


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
    parser.add_argument('--step', choices=['scan', 'audio', 'review', 'translate', 'compare', 'apply', 'ai-review', 'diff', 'clean', 'repair-ass'],
                        help='Run a specific step only (default: all)')
    parser.add_argument('--repair-ass', action='store_true',
                        help='Run ASS format repair scripts (names/styles/drawing/comment/oped detect)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview only, no file changes')
    parser.add_argument('--no-backup', action='store_true',
                        help='Skip git backup before modifying SRT')
    parser.add_argument('--project-dir', default=None,
                        help='Project root directory (default: CWD)')
    parser.add_argument('--video-dir', default=None,
                        help='Video directory (default: auto-detect from project/video, project/videos)')
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
    ai_review_items = []
    translated_path = None
    diffs = []

    for step in steps:
        if step == 'scan':
            scan_result = step_scan(project_dir, episode)

        elif step == 'audio':
            fixes = step_audio(project_dir, episode, scan_result,
                               dry_run=args.dry_run, video_dir=args.video_dir)

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
                n = fixes.applied + fixes.failed if hasattr(fixes, 'applied') else len(fixes)
                print(f'[apply] DRY RUN — {n} fixes would be applied')
                print(f'[apply] Would backup, then write to SRT')
            else:
                applied, ai_review_items = step_apply(project_dir, episode, fixes,
                                                       scan_result, args.no_backup)

        elif step == 'ai-review':
            _print_ai_review_prompt(project_dir, episode, ai_review_items)

        elif step == 'diff':
            step_diff(project_dir, episode, scan_result, applied)

        elif step == 'clean':
            _run_clean(project_dir)

        elif step == 'repair-ass':
            step_repair_ass(project_dir, dry_run=args.dry_run)

    # Final summary
    if not args.dry_run and 'apply' in steps:
        total_issues = len(scan_result.get('issues', [])) if scan_result else 0
        n_fixed = len(applied) if isinstance(applied, list) else 0
        n_ai = len(ai_review_items) if isinstance(ai_review_items, list) else 0
        print()
        print('=' * 55)
        print(f'  Done: {n_fixed} fixed, {n_ai} AI review, '
              f'{total_issues - n_fixed} pending')
        if n_ai:
            print(f'  Next: python episode_workflow.py {episode} --step ai-review')
        print('=' * 55)


if __name__ == '__main__':
    main()
