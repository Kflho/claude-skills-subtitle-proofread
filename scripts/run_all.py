#!/usr/bin/env python3
"""Single-command full pipeline — scan → fix → review → unify → apply → clean.

Usage:
  python run_all.py --lang ja                          # Full pipeline, all episodes
  python run_all.py --lang ja --limit 5                 # First 5 episodes only
  python run_all.py --lang ja --episodes EP001-EP010    # Specific range
  python run_all.py --lang ja --episodes EP001,EP005,EP010  # Specific episodes
  python run_all.py --lang ja --skip-whisper            # No audio processing
  python run_all.py --lang ja --resume                  # Resume after AI review
  python run_all.py --lang ja --start-from EP050        # Start from EP050
"""

import argparse
import json
import os
import re
import subprocess
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = _SCRIPT_DIR
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

# ── Helpers ──

def _run(cmd_parts, cwd, timeout=600, desc=''):
    """Run a subprocess command, print status, return True on success."""
    cmd = ' '.join(str(p) for p in cmd_parts)
    label = f'[{desc}]' if desc else ''
    print(f'{label} {cmd[:100]}...', file=sys.stderr)
    try:
        result = subprocess.run(cmd, cwd=cwd, shell=True,
                                capture_output=False, timeout=timeout)
        ok = result.returncode == 0
        print(f'{label} {"OK" if ok else "FAILED (code " + str(result.returncode) + ")"}',
              file=sys.stderr)
        return ok
    except subprocess.TimeoutExpired:
        print(f'{label} TIMEOUT', file=sys.stderr)
        return False
    except Exception as e:
        print(f'{label} ERROR: {e}', file=sys.stderr)
        return False


def _load_json(path):
    if not os.path.exists(path):
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _detect_format(project_dir):
    """Detect subtitle format (SRT/ASS)."""
    target = os.path.join(project_dir, 'AI审查后')
    if not os.path.isdir(target):
        return 'srt'
    ass = sum(1 for f in os.listdir(target) if f.endswith('.ass'))
    srt = sum(1 for f in os.listdir(target) if f.endswith('.srt'))
    return 'ass' if ass >= srt else 'srt'


def _detect_mode(project_dir):
    """Detect text vs audio mode."""
    ref = os.path.join(project_dir, '参考字幕')
    if os.path.isdir(ref) and os.listdir(ref):
        return 'text'
    return 'audio'


# ── Pipeline steps ──

def step_scan(project_dir, lang):
    """Layer 1: unified_scanner + build_glossary."""
    target = os.path.join(project_dir, 'AI审查后')
    findings = os.path.join(project_dir, 'temp', 'scans', 'findings.json')
    issues = os.path.join(project_dir, 'temp', 'scans', 'issues')
    os.makedirs(os.path.dirname(findings), exist_ok=True)

    scanner = os.path.join(_SCRIPT_DIR, '01_scan', 'unified_scanner.py')
    return _run([
        'python', scanner,
        '--target-dir', f'"{target}"',
        '--output-findings', f'"{findings}"',
        '--output-issues', f'"{issues}"',
        '--build-glossary',
        '--project-lang', lang,
    ], project_dir, desc='scan')


def _parse_episodes(arg, findings=None):
    """Parse --episodes argument into a list of episode IDs.

    Supports: 'EP001-EP010', 'EP001,EP005,EP010', '1-10', '1,5,10', None (all)
    """
    if not arg:
        if findings:
            return sorted(findings.get('per_episode_issues', {}).keys())
        return []

    episodes = []
    for part in arg.split(','):
        part = part.strip()
        if '-' in part:
            # Range: EP001-EP010 or 1-10
            a, b = part.split('-', 1)
            a = int(re.sub(r'\D', '', a))
            b = int(re.sub(r'\D', '', b))
            episodes.extend(f'EP{i:03d}' for i in range(a, b + 1))
        else:
            # Single: EP001 or 1
            num = int(re.sub(r'\D', '', part))
            episodes.append(f'EP{num:03d}')
    return sorted(set(episodes))


def _filter_by_start(episodes, start_from):
    """Only keep episodes >= start_from."""
    if not start_from:
        return episodes
    start_ep = f'EP{int(re.sub(r"\D", "", start_from)):03d}'
    return [ep for ep in episodes if ep >= start_ep]


def step_fix_episodes(project_dir, lang, mode, skip_whisper=False,
                      episodes=None, limit=0, start_from=None):
    """Layer 2: episode_workflow for selected episodes."""
    findings = _load_json(os.path.join(project_dir, 'temp', 'scans', 'findings.json'))

    # Determine episode list
    if episodes:
        selected = episodes
    elif findings:
        selected = sorted(findings.get('per_episode_issues', {}).keys())
    else:
        # No findings — try to find all SRT files in target
        target = os.path.join(project_dir, 'AI审查后')
        selected = sorted([
            f'EP{re.search(r"(\d+)", f).group(1):0>3}'
            for f in os.listdir(target) if f.endswith('.srt') and re.search(r'(\d+)', f)
        ]) if os.path.isdir(target) else []

    if start_from:
        selected = _filter_by_start(selected, start_from)
    if limit > 0:
        selected = selected[:limit]

    if not selected:
        print('[fix] No episodes to process.', file=sys.stderr)
        return True

    print(f'[fix] {len(selected)} episodes to process', file=sys.stderr)
    if len(selected) > 10:
        print(f'[fix] First: {selected[0]}, Last: {selected[-1]}', file=sys.stderr)

    ep_workflow = os.path.join(_SCRIPT_DIR, '02_fix', 'episode_workflow.py')
    for i, ep in enumerate(selected):
        cmd = ['python', ep_workflow, ep, '--mode', mode, '--no-backup',
               f'--project-dir', f'"{project_dir}"']
        if skip_whisper:
            cmd.append('--skip-whisper')
        _run(cmd, project_dir, desc=f'ep {i+1}/{len(selected)} {ep}')

    return True


def step_nouns(project_dir, lang):
    """Layer 3: noun_checker — proper nouns + OP/ED consistency."""
    target = os.path.join(project_dir, 'AI审查后')
    glossary = os.path.join(project_dir, 'reports', 'proper-nouns.md')
    checker = os.path.join(_SCRIPT_DIR, '03_nouns', 'noun_checker.py')

    results = {}

    # OP/ED cross-episode consistency
    print('\n[oped] OP/ED consistency check...', file=sys.stderr)
    _run(['python', checker, f'"{target}"', '--lang', lang, '--oped',
          '-o', os.path.join(project_dir, 'temp', 'scans', 'oped_fixes.json')],
         project_dir, desc='oped')

    # Noun table check (only if glossary exists)
    if os.path.exists(glossary):
        print('\n[nouns] Noun table check...', file=sys.stderr)
        _run(['python', checker, f'"{target}"', '--lang', lang,
              '--noun-table', f'"{glossary}"',
              '-o', os.path.join(project_dir, 'temp', 'scans', 'noun_check.json')],
             project_dir, desc='nouns')

        # Check for AI review candidates
        noun_json = _load_json(os.path.join(project_dir, 'temp', 'scans', 'noun_check.json'))
        if noun_json:
            unknowns = [r for r in noun_json.get('results', [])
                       if r.get('status') in ('unknown', 'mismatch')]
            results['ai_review_count'] = len(unknowns)
            if unknowns:
                # Deduplicate and rank by frequency
                from collections import Counter
                cands = Counter(r['candidate'] for r in unknowns)
                results['ai_candidates'] = [
                    {'candidate': c, 'count': n}
                    for c, n in cands.most_common(20)
                ]
                # Save for AI review
                ai_path = os.path.join(project_dir, 'temp', 'scans', 'ai_review_candidates.json')
                with open(ai_path, 'w', encoding='utf-8') as f:
                    json.dump(results['ai_candidates'], f, ensure_ascii=False, indent=2)
                results['ai_review_file'] = ai_path
    else:
        print('\n[nouns] No proper-nouns.md — skipping noun table check.', file=sys.stderr)
        print('[nouns] Run unified_scanner --build-glossary first to generate.', file=sys.stderr)

    return results


def step_apply_all(project_dir, lang):
    """Layer 4: apply_fixes — collect all fixes, apply at once."""
    target = os.path.join(project_dir, 'AI审查后')
    apply_script = os.path.join(_SCRIPT_DIR, '04_apply', 'apply_fixes.py')

    # Collect fixes from all sources
    all_fixes = []
    for src in ['oped_fixes.json', 'noun_check.json']:
        path = os.path.join(project_dir, 'temp', 'scans', src)
        data = _load_json(path)
        if data:
            fixes = data.get('fixes', [])
            if fixes:
                all_fixes.extend(fixes)
                print(f'[apply] {len(fixes)} fixes from {src}', file=sys.stderr)

    # Also check ai_review_fixes.json if it exists
    ai_fixes = _load_json(os.path.join(project_dir, 'temp', 'scans', 'ai_review_fixes.json'))
    if ai_fixes:
        all_fixes.extend(ai_fixes)
        print(f'[apply] {len(ai_fixes)} fixes from AI review', file=sys.stderr)

    if not all_fixes:
        print('[apply] No fixes to apply.', file=sys.stderr)
        return True

    # Write combined fixes
    fixes_path = os.path.join(project_dir, 'temp', 'scans', 'all_fixes.json')
    os.makedirs(os.path.dirname(fixes_path), exist_ok=True)
    with open(fixes_path, 'w', encoding='utf-8') as f:
        json.dump(all_fixes, f, ensure_ascii=False, indent=2)

    # Apply
    return _run([
        'python', apply_script,
        '--target-dir', f'"{target}"',
        '--fixes', f'"{fixes_path}"',
        '--lang', lang,
    ], project_dir, desc='apply')


def step_ass_repair(project_dir):
    """Layer 5: ASS format repair (ASS only)."""
    fmt = _detect_format(project_dir)
    if fmt != 'ass':
        print('[ass] SRT project — skipping.', file=sys.stderr)
        return True

    target = os.path.join(project_dir, 'AI审查后')
    repair = os.path.join(_SCRIPT_DIR, '05_ass', 'ass_repair.py')
    return _run(['python', repair, '--target-dir', f'"{target}"', '--check', 'all'],
                project_dir, desc='ass')


def step_clean(project_dir):
    """Clean up empty cues."""
    target = os.path.join(project_dir, 'AI审查后')
    cleaner = os.path.join(_SCRIPT_DIR, 'utils', 'clean_empty_cues.py')
    return _run(['python', cleaner, '--target-dir', f'"{target}"'],
                project_dir, desc='clean')


def step_deliver(project_dir, lang):
    """Layer 6: extract review clips for human delivery.

    Collects unfixable items from:
      - Whisper fixes (confidence=none) in temp/scans/*_fixes.json
      - Report layer 6 entries in reports/问题解决报告.md
      - Noun checker unresolved items in temp/scans/noun_check.json
    """
    report_path = os.path.join(project_dir, 'reports', '问题解决报告.md')
    fixes_dir = os.path.join(project_dir, 'temp', 'scans')
    noun_check = os.path.join(project_dir, 'temp', 'scans', 'noun_check.json')
    srt_dir = os.path.join(project_dir, 'AI审查后')
    output_dir = os.path.join(project_dir, 'reports', 'manual-review')
    extractor = os.path.join(_SCRIPT_DIR, 'utils', 'extract_review_clips.py')

    # Auto-detect video directory
    video_dir = _detect_video_dir(project_dir)

    cmd = [
        'python', extractor,
        '--srt-dir', f'"{srt_dir}"',
        '--output', f'"{output_dir}"',
    ]

    # Add report if it exists and has Layer 6 content
    if os.path.exists(report_path):
        cmd.extend(['--report', f'"{report_path}"', '--step', '6'])

    # Add noun check if it exists
    if os.path.exists(noun_check):
        cmd.extend(['--noun-check', f'"{noun_check}"'])

    # Collect fixes JSONs — pass all at once (nargs='*' needs single --fixes occurrence)
    if os.path.isdir(fixes_dir):
        fix_files = [os.path.join(fixes_dir, f) for f in os.listdir(fixes_dir)
                     if f.endswith('_fixes.json')]
        if fix_files:
            cmd.append('--fixes')
            for ff in fix_files:
                cmd.append(f'"{ff}"')

    if video_dir:
        cmd.extend(['--video-dir', f'"{video_dir}"'])
    else:
        print('[deliver] ⚠ No video directory found — clips cannot be extracted.',
              file=sys.stderr)
        print('[deliver] Pass --video-dir or set up video path detection.',
              file=sys.stderr)
        return False

    return _run(cmd, project_dir, timeout=1800, desc='deliver')


def _detect_video_dir(project_dir):
    """Auto-detect video directory from common locations."""
    candidates = [
        os.path.join(project_dir, 'video'),
        os.path.join(project_dir, 'videos'),
        r'E:\Animation\TV\[Anonymoose] 鉄腕アトム (DVD, 10bit)',
    ]
    for d in candidates:
        if os.path.isdir(d):
            return d
    return None


# ── AI Review flagging ──

def print_ai_review_notice(noun_results, project_dir, lang):
    """Print AI review instructions if candidates found."""
    count = noun_results.get('ai_review_count', 0)
    if count == 0:
        print('\n[AI review] All proper nouns matched — nothing to review.', file=sys.stderr)
        return

    candidates = noun_results.get('ai_candidates', [])
    ai_file = noun_results.get('ai_review_file', '')

    print(f'\n{"="*60}', file=sys.stderr)
    print(f'  AI REVIEW NEEDED: {count} unknown proper noun candidates', file=sys.stderr)
    print(f'{"="*60}', file=sys.stderr)
    print(f'\n  Candidates saved to: {ai_file}', file=sys.stderr)
    print(f'\n  Top {len(candidates)} candidates:', file=sys.stderr)
    for c in candidates:
        print(f'    {c["candidate"]} ({c["count"]}x)', file=sys.stderr)

    print(f'\n  To complete AI review:', file=sys.stderr)
    print(f'  1. Claude reads {ai_file}', file=sys.stderr)
    print(f'  2. For each candidate, determine:', file=sys.stderr)
    print(f'     - Is it a real proper noun? If not → add to exclude list', file=sys.stderr)
    print(f'     - What is the canonical form?', file=sys.stderr)
    print(f'  3. Output fixes to: temp/scans/ai_review_fixes.json', file=sys.stderr)
    print(f'     Format: [{{"action":"replace_global","original":"...","replacement":"..."}},...]', file=sys.stderr)
    print(f'  4. New proper nouns → append to reports/proper-nouns.md', file=sys.stderr)
    print(f'  5. Re-run: python run_all.py --lang {lang} --resume', file=sys.stderr)
    print(f'', file=sys.stderr)


# ── Main ──

def main():
    parser = argparse.ArgumentParser(
        description='Single-command full subtitle proofread pipeline',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_all.py --lang ja                          # Full pipeline
  python run_all.py --lang zh --target-dir ./subs/     # Custom target
  python run_all.py --lang ja --resume                 # Resume after AI review
  python run_all.py --lang ja --skip-whisper           # No audio processing
        """
    )
    parser.add_argument('--lang', default='ja', choices=['ja', 'zh'])
    parser.add_argument('--target-dir', default=None, help='Project root (default: CWD)')
    parser.add_argument('--episodes', '-e', default=None,
                        help='Episodes to process: EP001-EP010, EP001,EP005, 1-10, 1,5')
    parser.add_argument('--limit', '-n', type=int, default=0,
                        help='Max episodes to process (0=all)')
    parser.add_argument('--start-from', default=None,
                        help='Start from this episode (e.g., EP050 or 50)')
    parser.add_argument('--skip-whisper', action='store_true', help='Skip Whisper pipeline')
    parser.add_argument('--resume', action='store_true',
                        help='Resume after AI review (skip scan, re-run nouns+apply)')
    parser.add_argument('--apply-ai-review', action='store_true',
                        help='Apply AI review fixes only (fast — no scan, no audio)')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    project_dir = args.target_dir or os.getcwd()
    mode = _detect_mode(project_dir)
    fmt = _detect_format(project_dir)

    # Parse episode selection
    episodes = _parse_episodes(args.episodes) if args.episodes else None

    print(f'{"="*55}', file=sys.stderr)
    print(f'  Subtitle Proofread — {mode.upper()} mode, {fmt.upper()} format, '
          f'--lang {args.lang}', file=sys.stderr)
    print(f'  Project: {project_dir}', file=sys.stderr)
    if episodes:
        ep_range = f'{episodes[0]}~{episodes[-1]}' if len(episodes) > 1 else episodes[0]
        print(f'  Episodes: {len(episodes)} selected ({ep_range})', file=sys.stderr)
    elif args.limit:
        print(f'  Episodes: first {args.limit} with issues', file=sys.stderr)
    else:
        print(f'  Episodes: all with issues', file=sys.stderr)
    print(f'{"="*55}', file=sys.stderr)

    if args.dry_run:
        print('\n[DRY RUN] — no files will be modified\n', file=sys.stderr)

    # ── Fast path: AI review apply only ──
    if args.apply_ai_review:
        print(f'\n{"─"*40}', file=sys.stderr)
        print('  Apply AI review fixes (fast)', file=sys.stderr)
        print(f'{"─"*40}', file=sys.stderr)
        step_apply_all(project_dir, args.lang)
        step_clean(project_dir)
        return

    # ── Layer 1: Scan ──
    if not args.resume:
        print(f'\n{"─"*40}', file=sys.stderr)
        print('  Layer 1/6: Character scan', file=sys.stderr)
        print(f'{"─"*40}', file=sys.stderr)
        step_scan(project_dir, args.lang)

    # ── Layer 2: Fix episodes ──
    print(f'\n{"─"*40}', file=sys.stderr)
    print('  Layer 2/6: Semantic fix (translate/Whisper)', file=sys.stderr)
    print(f'{"─"*40}', file=sys.stderr)
    step_fix_episodes(project_dir, args.lang, mode, skip_whisper=args.skip_whisper,
                      episodes=episodes, limit=args.limit, start_from=args.start_from)

    # ── Layer 3: Proper nouns ──
    print(f'\n{"─"*40}', file=sys.stderr)
    print('  Layer 3/6: Proper noun unification', file=sys.stderr)
    print(f'{"─"*40}', file=sys.stderr)
    noun_results = step_nouns(project_dir, args.lang)

    # ── AI Review notice ──
    print_ai_review_notice(noun_results, project_dir, args.lang)

    # ── Layer 4: Apply fixes ──
    print(f'\n{"─"*40}', file=sys.stderr)
    print('  Layer 4/6: Apply fixes', file=sys.stderr)
    print(f'{"─"*40}', file=sys.stderr)
    step_apply_all(project_dir, args.lang)

    # ── Layer 5: ASS repair ──
    print(f'\n{"─"*40}', file=sys.stderr)
    print('  Layer 5/6: Format repair', file=sys.stderr)
    print(f'{"─"*40}', file=sys.stderr)
    step_ass_repair(project_dir)

    # ── Layer 6: Human review delivery ──
    print(f'\n{"─"*40}', file=sys.stderr)
    print('  Layer 6/6: Human review delivery', file=sys.stderr)
    print(f'{"─"*40}', file=sys.stderr)
    step_deliver(project_dir, args.lang)

    # ── Clean ──
    step_clean(project_dir)

    # ── Done ──
    if noun_results.get('ai_review_count', 0) > 0:
        print(f'\n{"="*55}', file=sys.stderr)
        print(f'  Pipeline complete — AI review still needed!', file=sys.stderr)
        print(f'  See AI REVIEW notice above for instructions.', file=sys.stderr)
        print(f'{"="*55}', file=sys.stderr)
    else:
        print(f'\n{"="*55}', file=sys.stderr)
        print(f'  Pipeline complete — all layers passed.', file=sys.stderr)
        print(f'{"="*55}', file=sys.stderr)


if __name__ == '__main__':
    main()
