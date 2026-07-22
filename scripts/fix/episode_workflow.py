#!/usr/bin/env python3
"""Single-episode subtitle proofread workflow (v5.0).

Focused on Fixer-related steps only — scan, noun review, and cleanup are
handled by run_all.py's batch layers.

  audio — VAD + Whisper transcription
  text  — translate reference subs → compare with Whisper output

Usage:
  python episode_workflow.py EP064                    # Auto-detect mode
  python episode_workflow.py EP064 --mode audio        # Force audio mode
  python episode_workflow.py EP064 --mode text          # Force text mode
  python episode_workflow.py EP064 --dry-run            # Preview only
  python episode_workflow.py EP064 --step audio         # VAD + Whisper only
  python episode_workflow.py EP064 --step translate     # Translate reference SRT
  python episode_workflow.py EP064 --step compare       # Compare Whisper vs ref
  python episode_workflow.py EP064 --step apply         # Apply fixes
  python episode_workflow.py EP064 --step diff          # Show changes
  python episode_workflow.py EP064 --step ai-review     # AI confidence review
  python episode_workflow.py EP064 --no-backup          # Skip git backup
  python episode_workflow.py EP064 --project-dir <DIR>  # Explicit project root
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime

# ── Path setup for importing from scripts dir ──
import lib._path  # noqa: F401

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.dirname(_SCRIPT_DIR)  # scripts/ — needed for subprocess PYTHONPATH

from lib.whisper_utils import to_seconds, setup_windows_utf8, parse_srt
from lib.project_utils import load_json, norm_ep, find_srt, can_use_whisper
setup_windows_utf8()


# ═══════════════════════════════════════════════════════════════
# Steps

# ═══════════════════════════════════════════════════════════════
# Step: translate — 百度翻译参考字幕→日语 (text mode only)
# ═══════════════════════════════════════════════════════════════

def step_translate(project_dir, episode, scan_result, dry_run=False, target_lang='ja'):
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
        ref_path,
        '--output', out_path,
        '--to', target_lang,
    ]

    if dry_run:
        print(f'[translate] DRY RUN — would execute:')
        print(f'  {" ".join(str(p) for p in cmd_parts)}')
        return out_path

    print(f'[translate] Translating: {os.path.basename(ref_path)} → ja')
    try:
        env = os.environ.copy()
        if _SCRIPT_DIR not in env.get('PYTHONPATH', ''):
            env['PYTHONPATH'] = _SCRIPTS_DIR + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
        result = subprocess.run([str(p) for p in cmd_parts], cwd=project_dir, env=env,
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

def step_compare(project_dir, episode, scan_result, translated_path, dry_run=False, target_lang='ja'):
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

    fixer = Fixer(episode, project_dir, target_lang=target_lang)
    report = fixer.fix_by_reference(translated_path)
    print(f'[compare] {report.applied} fixed, {report.failed} suspicious')

    return report


# ═══════════════════════════════════════════════════════════════
# Step: review — noun consistency check
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# Step: audio — VAD + Whisper for audio-only mode (no reference subs)
# ═══════════════════════════════════════════════════════════════

def step_audio(project_dir, episode, scan_result, dry_run=False, video_dir=None, target_lang='ja'):
    """Audio mode: dispatch garbled cues to VAD + Whisper.

    Delegates to Fixer.fix_by_whisper() which handles the full pipeline:
    VAD clean → build clusters → Tier 1/2 Whisper → apply to SRT → update report.
    """
    if not scan_result or not scan_result.get('issues'):
        print('[audio] No garbled cues to process.')
        from fix.fix_orchestrator import FixReport
        return FixReport(source='audio')

    issues = scan_result['issues']
    print(f'[audio] {len(issues)} garbled cue(s) → VAD + Whisper')

    if dry_run:
        print(f'\n[audio] DRY RUN — would call Fixer.fix_by_whisper()')
        from fix.fix_orchestrator import FixReport
        return FixReport(source='audio', applied=len(issues))

    from fix.fix_orchestrator import Fixer

    fixer = Fixer(episode, project_dir, target_lang=target_lang, video_dir=video_dir)
    if fixer.is_clean():
        print('[audio] Already clean — nothing to fix.')
        from fix.fix_orchestrator import FixReport
        return FixReport(source='audio')

    print(f'\n[audio] Running Whisper via Fixer...')
    report = fixer.fix_by_whisper(separate_vocals=True)
    print(f'[audio] Done: {report.applied} fixed, {report.ai_review} AI review, '
          f'{report.failed} unfixable')

    # Return fixes from report for backward compatibility with step_apply
    return report


# ═══════════════════════════════════════════════════════════════
# Step: apply — apply fixes to the SRT file + AI confidence review
# ═══════════════════════════════════════════════════════════════

def step_apply(project_dir, episode, fixes_or_report, scan_result, no_backup=False):
    """Log AI confidence review JSON for low-confidence Whisper items.

    SRT writing and report updates are handled internally by Fixer.fix_by_whisper().
    This step only generates the per-episode AI review JSON for --step ai-review.

    Args:
        fixes_or_report: FixReport from step_audio()
    """
    # FixReport from Fixer (only path — legacy list path removed 2026-07-21)
    report = fixes_or_report
    if hasattr(report, 'applied') and report.applied == 0 and report.failed == 0:
        print('[apply] No fixes to report.')
        return [], []

    print(f'[apply] {report.applied} fixed, {report.ai_review} AI review, '
          f'{report.failed} unfixable (already applied to SRT & report)')

    # AI review JSON no longer generated — tracked in report Layer 2.5 only
    return [], []


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

    print(f'[diff] {episode} changes:')
    print()

    fixed_count = 0
    still_count = 0

    # Build timecode→text map from current SRT
    current_map = {c['start']: c['text'] for c in parse_srt(srt_path, mark_garbled=False)}

    deleted_count = 0
    for item in sorted(issues, key=lambda x: x.get('start', x.get('timecode', ''))):
        ts = item.get('start', item.get('timecode', ''))
        orig = item.get('original_text', item.get('text', ''))
        ts_key = ts.replace(',', '.')
        current = current_map.get(ts_key)

        if current is None:
            # Cue not found in current SRT → VAD-deleted
            print(f'  [DELETED] {ts} | {orig[:80]}')
            deleted_count += 1
        elif current != orig:
            print(f'  [FIXED]  {ts}')
            print(f'    was: {orig[:80]}')
            print(f'    now: {current[:80]}')
            fixed_count += 1
        else:
            print(f'  [STILL]  {ts} | {orig[:80]}')
            still_count += 1

    print()
    print(f'  Fixed: {fixed_count}  |  Still broken: {still_count}  '
          f'|  Deleted: {deleted_count}  |  Total: {len(issues)}')


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
    parser.add_argument('--step', choices=['audio', 'translate', 'compare', 'apply', 'ai-review', 'diff'],
                        help='Run a specific step only (default: all)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview only, no file changes')
    parser.add_argument('--no-backup', action='store_true',
                        help='Skip git backup before modifying SRT')
    parser.add_argument('--project-dir', default=None,
                        help='Project root directory (default: CWD)')
    parser.add_argument('--video-dir', default=None,
                        help='Video directory (default: auto-detect from project/video, project/videos)')

    args = parser.parse_args()

    episode = norm_ep(args.episode)
    project_dir = args.project_dir or os.getcwd()

    # Detect available resources
    from lib.project_utils import detect_resources, resources_summary
    resources = detect_resources(project_dir, video_dir=args.video_dir)

    # Single episode mode
    print('=' * 55)
    print(f'  {episode} — Proofread Workflow')
    print(f'  {resources_summary(resources)}')
    print('=' * 55)
    _run_pipeline(project_dir, episode, resources, args)


def _run_pipeline(project_dir, episode, resources, args):
    """Execute the proofread pipeline for one episode.

    Resource-driven: uses Whisper if video+Whisper available, injects
    reference text if reference subtitles available.

    Graceful degradation: missing resources → skip that step, continue
    with what's available.
    """
    # ── Dynamic step list based on available resources ──
    target_lang = getattr(args, 'target_lang', 'ja')
    if args.step is None:
        steps = []
        if can_use_whisper(resources, skip_whisper=getattr(args, 'skip_whisper', False)):
            steps.append('audio')
            if target_lang != 'ja':
                steps.append('translate')
        steps.extend(['apply', 'diff'])
    else:
        steps = [args.step]

    # Load findings for this episode (used as gate by step functions)
    findings = load_json(os.path.join(project_dir, 'temp', 'scans', 'findings.json'))
    per_ep = (findings or {}).get('per_episode_issues', {})
    issues = per_ep.get(episode, [])
    scan_result = {'issues': issues} if issues else None

    from fix.fix_orchestrator import FixReport
    fixes = FixReport(source='workflow')
    applied = []
    ai_review_items = []
    translated_path = None
    diffs = []

    for step in steps:
        if step == 'audio':
            fixes = step_audio(project_dir, episode, scan_result,
                               dry_run=args.dry_run, video_dir=args.video_dir,
                               target_lang=target_lang)

        elif step == 'translate':
            translated_path = step_translate(project_dir, episode, scan_result,
                                             dry_run=args.dry_run,
                                             target_lang=target_lang)

        elif step == 'compare':
            applied = step_compare(project_dir, episode, scan_result,
                                   translated_path, dry_run=args.dry_run,
                                   target_lang=target_lang)

        elif step == 'apply':
            if args.dry_run:
                n = fixes.applied + fixes.failed
                print(f'[apply] DRY RUN — {n} fixes would be applied')
                print(f'[apply] Would backup, then write to SRT')
            else:
                applied, ai_review_items = step_apply(project_dir, episode, fixes,
                                                       scan_result, args.no_backup)

        elif step == 'ai-review':
            _print_ai_review_prompt(project_dir, episode, ai_review_items)

        elif step == 'diff':
            step_diff(project_dir, episode, scan_result, applied)

    # Final summary (checklists + clips now generated in step_deliver)
    if not args.dry_run and 'apply' in steps:
        n_fixed = fixes.applied
        n_ai = fixes.ai_review
        n_failed = fixes.failed

        print()
        print('=' * 55)
        print(f'  Done: {n_fixed} auto-keep, {n_ai} AI review, '
              f'{n_failed} → L6 human')
        print(f'  Reports + clips → generated in Layer 6 step_deliver')
        print('=' * 55)


if __name__ == '__main__':
    main()
