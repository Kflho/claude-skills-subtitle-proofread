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

from lib.project_utils import load_json, detect_mode, detect_format


# ── Helpers ──

def _run(cmd_parts, cwd, timeout=600, desc=''):
    """Run a subprocess command, print status, return True on success."""
    label = f'[{desc}]' if desc else ''
    cmd_display = ' '.join(str(p) for p in cmd_parts)
    print(f'{label} {cmd_display[:100]}...', file=sys.stderr)
    try:
        result = subprocess.run([str(p) for p in cmd_parts], cwd=cwd,
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


# ── Pipeline steps ──

def step_scan(project_dir, lang):
    """Layer 1: unified_scanner + build_glossary."""
    target = os.path.join(project_dir, 'AI审查后')
    findings = os.path.join(project_dir, 'temp', 'scans', 'findings.json')
    issues = os.path.join(project_dir, 'temp', 'scans', 'issues')
    ai_nouns = os.path.join(project_dir, 'temp', 'scans', 'ai_nouns.json')
    os.makedirs(os.path.dirname(findings), exist_ok=True)

    scanner = os.path.join(_SCRIPT_DIR, 'scan', 'unified_scanner.py')
    cmd = [
        'python', scanner,
        '--target-dir', target,
        '--output-findings', findings,
        '--output-issues', issues,
        '--build-glossary',
        '--project-lang', lang,
    ]
    if os.path.exists(ai_nouns):
        cmd.extend(['--ai-nouns', ai_nouns])
    return _run(cmd, project_dir, desc='scan')


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


def step_fix_episodes(project_dir, lang, mode, video_dir=None,
                      skip_whisper=False, episodes=None, limit=0, start_from=None,
                      skip_if_clean=True):
    """Layer 2: Unified error-fix via Fixer (reference → Whisper → human).

    Each episode goes through the cascading priority:
    1. Reference translation comparison (if 参考字幕/ exists)
    2. Whisper audio transcription
    3. Human review checklist generation (for unfixable items)

    With --skip-if-clean (default), episodes with no garbled cues are skipped
    without invoking Whisper or ffmpeg.
    """
    findings = load_json(os.path.join(project_dir, 'temp', 'scans', 'findings.json'))

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
        return []

    print(f'[fix] {len(selected)} episodes to process', file=sys.stderr)
    if len(selected) > 10:
        print(f'[fix] First: {selected[0]}, Last: {selected[-1]}', file=sys.stderr)

    # --skip-if-clean: fast pre-check (Fixer.is_clean() is cheap — no audio/ffmpeg)
    if skip_if_clean:
        from fix.fix_orchestrator import Fixer
        clean_eps = []
        for ep in selected:
            try:
                if Fixer(ep, project_dir).is_clean():
                    clean_eps.append(ep)
            except Exception:
                pass
        if clean_eps:
            print(f'[fix] --skip-if-clean: {len(clean_eps)} already clean → skipping',
                  file=sys.stderr)
            selected = [ep for ep in selected if ep not in clean_eps]

    if not selected:
        print('[fix] All episodes already clean — nothing to do.', file=sys.stderr)
        return []

    from fix.episode_workflow import _run_pipeline

    class _Args:
        step = None
        dry_run = False

    for i, ep in enumerate(selected):
        if skip_whisper and mode == 'audio':
            continue  # audio mode with --skip-whisper: nothing to do
        args = _Args()
        if video_dir:
            args.video_dir = video_dir
        else:
            args.video_dir = None
        args.no_backup = False
        print(f'[fix] {ep} ({i+1}/{len(selected)})', file=sys.stderr)
        try:
            _run_pipeline(project_dir, ep, mode, args)
        except Exception as e:
            print(f'[fix] {ep} FAILED: {e}', file=sys.stderr)

    return selected


def step_nouns(project_dir, lang):
    """Layer 3: noun_checker — proper nouns + OP/ED consistency."""
    target = os.path.join(project_dir, 'AI审查后')
    glossary = os.path.join(project_dir, 'reports', 'proper-nouns.md')
    checker = os.path.join(_SCRIPT_DIR, 'nouns', 'noun_checker.py')

    results = {}

    # OP/ED cross-episode consistency
    print('\n[oped] OP/ED consistency check...', file=sys.stderr)
    _run(['python', checker, target, '--lang', lang, '--oped',
          '-o', os.path.join(project_dir, 'temp', 'scans', 'oped_fixes.json')],
         project_dir, desc='oped')

    # Noun table check (only if glossary exists)
    if os.path.exists(glossary):
        results.update(_step_noun_classify(project_dir, lang, checker, target, glossary))
    else:
        print('\n[nouns] No proper-nouns.md — skipping noun table check.', file=sys.stderr)
        print('[nouns] Run unified_scanner --build-glossary first to generate.', file=sys.stderr)

    return results


def _step_noun_classify(project_dir, lang, checker, target, glossary):
    """Run noun_checker + auto_classify for unknown proper noun candidates."""
    print('\n[nouns] Noun table check...', file=sys.stderr)
    _run(['python', checker, target, '--lang', lang,
          '--noun-table', glossary,
          '-o', os.path.join(project_dir, 'temp', 'scans', 'noun_check.json')],
         project_dir, desc='nouns')

    noun_json = load_json(os.path.join(project_dir, 'temp', 'scans', 'noun_check.json'))
    if not noun_json:
        return {}

    unknowns = [r for r in noun_json.get('results', [])
                if r.get('status') in ('unknown', 'mismatch')]
    if not unknowns:
        return {'total_unknown': 0}

    # Deduplicate and rank by frequency
    from collections import Counter
    cands = Counter(r['candidate'] for r in unknowns)
    candidates_for_classify = [
        {'candidate': c, 'count': n,
         'context': next((r.get('context', '') for r in unknowns
                          if r['candidate'] == c), '')}
        for c, n in cands.most_common(50)
    ]

    # Save candidates JSON for auto_classify subprocess
    cand_path = os.path.join(project_dir, 'temp', 'scans', 'noun_candidates.json')
    os.makedirs(os.path.dirname(cand_path), exist_ok=True)
    with open(cand_path, 'w', encoding='utf-8') as f:
        json.dump(candidates_for_classify, f, ensure_ascii=False, indent=2)

    # Also run via subprocess for logging (non-essential — failure ignored)
    classified_path = os.path.join(project_dir, 'temp', 'scans', 'noun_classified.json')
    _run([
        'python', os.path.join(_SCRIPT_DIR, 'nouns', 'auto_classify.py'),
        '--candidates', cand_path,
        '--lang', lang,
        '--output', classified_path,
    ], project_dir, desc='auto_classify')

    return _apply_classified_results(project_dir, candidates_for_classify, unknowns, cands, lang)


def _apply_classified_results(project_dir, candidates, unknowns, cands, lang):
    """Run auto_classify inline and distribute results to fixes/glossary/AI review."""
    results = {'total_unknown': len(unknowns)}

    try:
        from nouns.auto_classify import classify_batch
        classified = classify_batch(candidates, lang=lang)
    except ImportError:
        # Fallback: if auto_classify not available, all go to AI
        results['ai_review_count'] = len(unknowns)
        results['ai_candidates'] = [{'candidate': c, 'count': n}
                                    for c, n in cands.most_common(20)]
        ai_path = os.path.join(project_dir, 'temp', 'scans', 'ai_review_candidates.json')
        with open(ai_path, 'w', encoding='utf-8') as f:
            json.dump(results['ai_candidates'], f, ensure_ascii=False, indent=2)
        results['ai_review_file'] = ai_path
        return results

    # Accepted → add to fixes
    if classified['accepted']:
        accepted_fixes = [
            {'action': 'replace_global',
             'original': c['candidate'], 'replacement': c['candidate'],
             'note': f'auto_classify: {c["reason"]}'}
            for c in classified['accepted']
        ]
        fixes_path = os.path.join(project_dir, 'temp', 'scans', 'noun_accepted_fixes.json')
        with open(fixes_path, 'w', encoding='utf-8') as f:
            json.dump(accepted_fixes, f, ensure_ascii=False, indent=2)
        _append_to_glossary(project_dir, classified['accepted'])

    # Rejected → log only
    if classified['rejected']:
        results['auto_rejected'] = len(classified['rejected'])

    # Needs AI → save for AI review
    if classified['needs_ai']:
        results['ai_review_count'] = len(classified['needs_ai'])
        results['ai_candidates'] = [
            {'candidate': c['candidate'], 'count': c.get('count', 1),
             'reason': c.get('reason', '')}
            for c in classified['needs_ai']
        ]
        ai_path = os.path.join(project_dir, 'temp', 'scans', 'ai_review_candidates.json')
        with open(ai_path, 'w', encoding='utf-8') as f:
            json.dump(results['ai_candidates'], f, ensure_ascii=False, indent=2)
        results['ai_review_file'] = ai_path

    return results


def step_apply_all(project_dir, lang):
    """Layer 4: apply_fixes — collect all fixes, apply at once."""
    target = os.path.join(project_dir, 'AI审查后')
    apply_script = os.path.join(_SCRIPT_DIR, 'apply', 'apply_fixes.py')

    # Collect fixes from all sources
    all_fixes = []
    for src in ['oped_fixes.json', 'noun_check.json']:
        path = os.path.join(project_dir, 'temp', 'scans', src)
        data = load_json(path)
        if data:
            fixes = data.get('fixes', [])
            if fixes:
                all_fixes.extend(fixes)
                print(f'[apply] {len(fixes)} fixes from {src}', file=sys.stderr)

    # Also check ai_review_fixes.json if it exists
    ai_fixes = load_json(os.path.join(project_dir, 'temp', 'scans', 'ai_review_fixes.json'))
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
        '--target-dir', target,
        '--fixes', fixes_path,
        '--lang', lang,
    ], project_dir, desc='apply')


def step_ass_repair(project_dir):
    """Layer 5: ASS format repair (ASS only)."""
    fmt = detect_format(project_dir)
    if fmt['primary'] != 'ass':
        print('[ass] SRT project — skipping.', file=sys.stderr)
        return True

    target = os.path.join(project_dir, 'AI审查后')
    repair = os.path.join(_SCRIPT_DIR, 'ass', 'ass_repair.py')
    return _run(['python', repair, '--target-dir', target, '--check', 'all'],
                project_dir, desc='ass')


def _append_to_glossary(project_dir, accepted_candidates):
    """Append auto-accepted proper nouns to the glossary."""
    glossary_path = os.path.join(project_dir, 'reports', 'proper-nouns.md')
    if not accepted_candidates:
        return

    # Read existing entries
    existing_names = set()
    if os.path.exists(glossary_path):
        with open(glossary_path, 'r', encoding='utf-8') as f:
            for line in f:
                # Match table rows: | アトム | 866 | ... |
                m = re.match(r'\|\s*([^\s|]+)\s*\|', line)
                if m:
                    existing_names.add(m.group(1).strip())

    new_entries = []
    for c in accepted_candidates:
        name = c.get('candidate', '')
        if name and name not in existing_names:
            count = c.get('count', 1)
            new_entries.append(f'| {name} | {count} | — |')
            existing_names.add(name)

    if new_entries:
        with open(glossary_path, 'a', encoding='utf-8') as f:
            for entry in new_entries:
                f.write(entry + '\n')
        print(f'[glossary] {len(new_entries)} new proper nouns appended',
              file=sys.stderr)


def step_clean(project_dir):
    """Clean up empty cues."""
    target = os.path.join(project_dir, 'AI审查后')
    cleaner = os.path.join(_SCRIPT_DIR, 'utils', 'clean_empty_cues.py')
    return _run(['python', cleaner, '--target-dir', target],
                project_dir, desc='clean')


def step_deliver(project_dir, lang, processed_episodes=None, is_full_run=True,
                 video_dir=None):
    """Generate unified human review checklist from Layer 6 ⬜ entries.

    One file (reports/manual-review/checklist.md) covers ALL episodes.
    Entries grouped by episode with ## EP### headers.
    """
    review_dir = os.path.join(project_dir, 'reports', 'manual-review')
    os.makedirs(review_dir, exist_ok=True)
    unified_path = os.path.join(review_dir, 'checklist.md')
    report_path = os.path.join(project_dir, 'reports', '问题解决报告.md')

    from utils.update_report import read_report
    report_data = read_report(report_path)
    layer6 = report_data.get('6', [])
    pending = [e for e in layer6 if e.get('status') == '⬜']

    if not pending:
        print('[deliver] No pending Layer 6 entries — all clean.', file=sys.stderr)
        return True

    # Group by episode
    by_ep = {}
    for e in pending:
        ep = e.get('ep', '?')
        by_ep.setdefault(ep, []).append(e)

    # Build unified checklist markdown
    from datetime import datetime
    today = datetime.now().strftime('%Y-%m-%d')
    lines = [
        f'# 人工审查清单',
        f'> 导出: {today}',
        f'> 共 {len(by_ep)} 集 {len(pending)} 条待审查',
        f'> version: 3  (unified)',
        f'>',
        f'> **填写方法**：看视频 + 读上下文 → 在「修正:」后写正确台词。',
        f'> 写「删除」移除该 cue。填完运行 --apply-checklist。',
        f'>',
        f'---',
        f'',
    ]

    for ep in sorted(by_ep.keys()):
        entries = by_ep[ep]
        lines.append(f'## {ep} ({len(entries)}条)')
        lines.append('')
        for entry in entries:
            timecode = entry.get('time', '?')
            original = entry.get('original', '')
            corrected = entry.get('corrected', '')
            safe_tc = timecode.replace(':', '-').replace(',', '-').replace('.', '-')
            clip_name = f'{ep}_{safe_tc}.mp4'
            clip_path = os.path.join(review_dir, clip_name)
            clip_str = clip_name if os.path.exists(clip_path) else '(需先生成)'
            lines.append(
                f'{ep} | {timecode}\n'
                f'残留: {original}\n'
                f'片段: {clip_str}\n'
                f'修正: {corrected}\n'
                f'\n---\n'
            )

    with open(unified_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    print(f'\n[deliver] Unified checklist: {unified_path}', file=sys.stderr)
    print(f'[deliver] {len(pending)} pending items across {len(by_ep)} episodes',
          file=sys.stderr)
    print(f'[deliver] After filling corrections, run:',
          file=sys.stderr)
    print(f'  python scripts/run_all.py --lang {lang} --apply-checklist',
          file=sys.stderr)
    return True


def step_apply_checklist(project_dir, lang, video_dir=None):
    """Apply filled unified human review checklist → SRT + report.

    Parses reports/manual-review/checklist.md (v3 unified format),
    applies corrections per-episode via Fixer.apply() with VAD alignment.
    """
    review_dir = os.path.join(project_dir, 'reports', 'manual-review')
    unified_path = os.path.join(review_dir, 'checklist.md')

    if not os.path.exists(unified_path):
        print('[apply-checklist] Unified checklist not found.', file=sys.stderr)
        return False

    from fix.fix_orchestrator import Fixer
    corrections_by_ep = _parse_unified_checklist(unified_path)
    if not corrections_by_ep:
        print('[apply-checklist] No corrections found in checklist.', file=sys.stderr)
        return False

    total_applied = 0
    for ep in sorted(corrections_by_ep.keys()):
        ep_corrections = corrections_by_ep[ep]
        tmp_path = os.path.join(review_dir, f'{ep}_checklist.md')
        _write_ep_checklist(tmp_path, ep, ep_corrections)
        fixer = Fixer(ep, project_dir, video_dir=video_dir)
        applied = fixer.apply(tmp_path)
        total_applied += applied
        print(f'[apply-checklist] {ep}: {applied} corrections applied', file=sys.stderr)

    print(f'\n[apply-checklist] Total: {total_applied} corrections across '
          f'{len(corrections_by_ep)} episodes', file=sys.stderr)
    return total_applied > 0


def _parse_unified_checklist(path):
    """Parse unified checklist (version 3) into {ep: [corrections]}.

    Supports both version 3 (unified) and version 2 (per-ep) for
    backward compatibility.
    """
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Detect version
    is_v3 = 'version: 3' in content

    if is_v3:
        corrections_by_ep = {}
        # Split by episode headers: ## EP###
        sections = re.split(r'\n(?=## EP\d{3} )', content)
        for section in sections:
            ep_match = re.match(r'## (EP\d{3})', section)
            if not ep_match:
                continue
            ep = ep_match.group(1)
            corrections = _parse_v3_section(section)
            if corrections:
                corrections_by_ep[ep] = corrections
        return corrections_by_ep
    else:
        # Legacy v2: parse for single episode, infer EP from filename or content
        corrections = _parse_v2_entries(content)
        if not corrections:
            return {}
        # Try to find EP in content
        ep_match = re.search(r'(EP\d{3})', content)
        ep = ep_match.group(1) if ep_match else '???'
        return {ep: corrections}


def _parse_v3_section(section):
    """Parse one episode section of a v3 unified checklist."""
    corrections = []
    blocks = re.split(r'\n---\n', section)
    for block in blocks:
        tc_match = re.search(r'(EP\d{3})\s*\|\s*(\d{2}:\d{2}:\d{2}[.,]\d{3})', block)
        if not tc_match:
            continue
        timecode = tc_match.group(2).replace(',', '.')
        if re.search(r'^\s*✅', block, re.MULTILINE):
            continue  # Already done
        corr_match = re.search(r'修正:\s*\n?(.+?)(?=\n---|\n\Z|\Z)', block, re.DOTALL)
        if not corr_match:
            continue
        text = corr_match.group(1).strip()
        corrections.append({'time': timecode, 'text': text})
    return corrections


def _parse_v2_entries(content):
    """Parse legacy v2 checklist entries."""
    corrections = []
    blocks = re.split(r'\n---\n', content)
    for block in blocks:
        tc_match = re.search(
            r'(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*~\s*(\d{2}:\d{2}:\d{2}[.,]\d{3})?', block)
        if not tc_match:
            continue
        timecode = tc_match.group(1).replace(',', '.')
        if re.search(r'^\s*✅', block, re.MULTILINE):
            continue
        corr_match = re.search(r'修正:\s*\n?(.+?)(?=\n---|\n\Z|\Z)', block, re.DOTALL)
        if not corr_match:
            continue
        text = corr_match.group(1).strip()
        corrections.append({'time': timecode, 'text': text})
    return corrections


def _write_ep_checklist(path, ep, corrections):
    """Write a temporary per-ep checklist for Fixer.apply() compatibility."""
    lines = [
        f'# 人工审查清单 — {ep}',
        f'> version: 2',
        f'> 共 {len(corrections)} 条',
        f'',
        f'---',
        f'',
    ]
    for c in corrections:
        lines.append(
            f'{ep} | {c["time"]} ~ ? | ?.mp4\n'
            f'来源: unified checklist\n'
            f'残留: (see unified)\n'
            f'修正: {c["text"]}\n'
            f'\n---\n'
        )
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))


def _detect_video_dir(project_dir, explicit=None):
    """Resolve video directory: explicit arg > project-local > None."""
    if explicit and os.path.isdir(explicit):
        return explicit
    for d in [os.path.join(project_dir, 'video'), os.path.join(project_dir, 'videos')]:
        if os.path.isdir(d):
            return d
    return None


# ── AI Review flagging ──

def print_ai_review_notice(noun_results, project_dir, lang):
    """Print AI review instructions if candidates found after auto_classify."""
    count = noun_results.get('ai_review_count', 0)
    total = noun_results.get('total_unknown', 0)
    auto_ok = noun_results.get('auto_accepted', 0)
    auto_rej = noun_results.get('auto_rejected', 0)

    if count == 0:
        if total > 0:
            print(f'\n[AI review] {total} candidates → auto_classify handled all '
                  f'({auto_ok} accepted, {auto_rej} rejected). '
                  f'Nothing needs AI review.', file=sys.stderr)
        else:
            print('\n[AI review] All proper nouns matched — nothing to review.',
                  file=sys.stderr)
        return

    candidates = noun_results.get('ai_candidates', [])
    ai_file = noun_results.get('ai_review_file', '')

    print(f'\n{"="*60}', file=sys.stderr)
    print(f'  AI REVIEW NEEDED: {count} proper noun candidates '
          f'(out of {total} total, {auto_ok} auto-accepted, {auto_rej} auto-rejected)',
          file=sys.stderr)
    print(f'{"="*60}', file=sys.stderr)
    print(f'\n  Candidates saved to: {ai_file}', file=sys.stderr)
    print(f'\n  Candidates:', file=sys.stderr)
    for c in candidates:
        print(f'    {c["candidate"]} ({c["count"]}x) — {c.get("reason", "")}',
              file=sys.stderr)

    print(f'\n  AI任务（只审候选项，不读名词表全文）：', file=sys.stderr)
    print(f'  1. 判断每个候选项是否为专有名词', file=sys.stderr)
    print(f'  2. 是 → 给出规范形式', file=sys.stderr)
    print(f'  3. 否 → 标记排除', file=sys.stderr)
    print(f'  4. 输出到: temp/scans/ai_review_fixes.json', file=sys.stderr)
    print(f'     Format: [{{"action":"replace_global","original":"...","replacement":"..."}},...]',
          file=sys.stderr)
    print(f'  5. Re-run: python run_all.py --lang {lang} --resume', file=sys.stderr)
    print(f'', file=sys.stderr)


# ── Progress ──

def _print_progress(project_dir, label='Progress'):
    """Print concise progress summary from findings.json and report."""
    findings = load_json(os.path.join(project_dir, 'temp', 'scans', 'findings.json'))
    report_path = os.path.join(project_dir, 'reports', '问题解决报告.md')

    print(f'\n{"─"*40}', file=sys.stderr)
    print(f'  {label}', file=sys.stderr)
    print(f'{"─"*40}', file=sys.stderr)

    if findings:
        s = findings.get('summary', {})
        per_ep = findings.get('per_episode_issues', {})
        eps_with = [ep for ep, v in per_ep.items() if v]
        print(f'  Episodes with issues: {len(eps_with)}/{len(per_ep)}', file=sys.stderr)
        print(f'  Garbled cues:        {s.get("garbled_count", "?")}', file=sys.stderr)
        if s.get('repeat_count'):
            print(f'  Repeat patterns:     {s["repeat_count"]}', file=sys.stderr)
    else:
        print(f'  (no findings.json)', file=sys.stderr)

    if os.path.exists(report_path):
        import re as _re
        with open(report_path, 'r', encoding='utf-8') as f:
            content = f.read()
        nums = _re.findall(r'(\d+)', content.split('\n')[0]) if content else []
        if len(nums) >= 3:
            print(f'  Report: {nums[0]} fixed, {nums[1]} pending, {nums[2]} deleted',
                  file=sys.stderr)
    else:
        print(f'  (no report yet)', file=sys.stderr)


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
    parser.add_argument('--video-dir', default=None,
                        help='Video directory (default: auto-detect from project/video, project/videos)')
    parser.add_argument('--skip-whisper', action='store_true', help='Skip Whisper pipeline')
    parser.add_argument('--resume', action='store_true',
                        help='Resume after AI review (skip scan, re-run nouns+apply)')
    parser.add_argument('--apply-ai-review', action='store_true',
                        help='Apply AI review fixes only (fast — no scan, no audio)')
    parser.add_argument('--apply-checklist', action='store_true',
                        help='Apply filled human-review checklists (--step deliver only)')
    parser.add_argument('--no-skip-if-clean', action='store_true',
                        help='Process all episodes even if SRT has no garbled cues')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    project_dir = args.target_dir or os.getcwd()
    mode = detect_mode(project_dir)
    fmt = detect_format(project_dir)

    # Parse episode selection
    episodes = _parse_episodes(args.episodes) if args.episodes else None

    # Resolve video directory (explicit arg > auto-detect)
    video_dir = _detect_video_dir(project_dir, explicit=args.video_dir)

    # Detect partial vs full run (affects Layer 6 delivery scope)
    is_full_run = (args.limit == 0 and not args.episodes and not args.start_from
                   and not args.apply_ai_review)

    print(f'{"="*55}', file=sys.stderr)
    print(f'  Subtitle Proofread — {mode.upper()} mode, {(fmt["primary"] or "NONE").upper()} format, '
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
        return

    # ── Fast path: AI review apply only ──
    if args.apply_ai_review:
        print(f'\n{"─"*40}', file=sys.stderr)
        print('  Apply AI review fixes (fast)', file=sys.stderr)
        print(f'{"─"*40}', file=sys.stderr)
        step_apply_all(project_dir, args.lang)
        step_clean(project_dir)
        return

    # ── Fast path: checklist apply only ──
    if args.apply_checklist:
        print(f'\n{"─"*40}', file=sys.stderr)
        print('  Apply human review checklist (fast)', file=sys.stderr)
        print(f'{"─"*40}', file=sys.stderr)
        step_apply_checklist(project_dir, args.lang, video_dir=video_dir)
        return

    # ── Layer 1: Scan ──
    if not args.resume:
        print(f'\n{"─"*40}', file=sys.stderr)
        print('  Layer 1/6: Character scan', file=sys.stderr)
        print(f'{"─"*40}', file=sys.stderr)
        step_scan(project_dir, args.lang)

    _print_progress(project_dir, 'Status: after scan')

    # ── Layer 2: Fix episodes ──
    print(f'\n{"─"*40}', file=sys.stderr)
    print('  Layer 2/6: Error fix (reference → Whisper → human)', file=sys.stderr)
    print(f'{"─"*40}', file=sys.stderr)
    processed = step_fix_episodes(project_dir, args.lang, mode, video_dir=video_dir,
                                  skip_whisper=args.skip_whisper,
                                  episodes=episodes, limit=args.limit,
                                  start_from=args.start_from,
                                  skip_if_clean=not args.no_skip_if_clean)

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

    # ── Human review checklist generation ──
    print(f'\n{"─"*40}', file=sys.stderr)
    print('  Review: Generate human review checklist', file=sys.stderr)
    print(f'{"─"*40}', file=sys.stderr)
    step_deliver(project_dir, args.lang, processed_episodes=processed,
                 is_full_run=is_full_run, video_dir=video_dir)

    # ── Clean ──
    step_clean(project_dir)

    _print_progress(project_dir, 'Status: final')

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
