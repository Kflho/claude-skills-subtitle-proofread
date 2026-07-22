#!/usr/bin/env python3
"""Single-command full pipeline — scan → fix → unify → deliver.

3-phase pipeline:
  Phase 1: Scan — detect garbled text, build glossary
  Phase 2: Fix  — Whisper ASR → triage (keep / AI-complete / cut noise)
  Phase 3: Unify — proper nouns + apply + deliver human checklist

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

def step_scan(project_dir, lang, force_rescan=False):
    """Layer 1: unified_scanner + build_glossary.

    If findings.json already exists and SRT files haven't changed since
    last scan, skip re-scanning (idempotent). Use --force-rescan to override.
    """
    target = os.path.join(project_dir, 'AI审查后')
    findings = os.path.join(project_dir, 'temp', 'scans', 'findings.json')
    issues = os.path.join(project_dir, 'temp', 'scans', 'issues')
    ai_nouns = os.path.join(project_dir, 'temp', 'scans', 'ai_nouns.json')
    os.makedirs(os.path.dirname(findings), exist_ok=True)

    # ── Cache check: skip rescan if SRTs haven't changed ──
    if not force_rescan and os.path.exists(findings):
        try:
            findings_mtime = os.path.getmtime(findings)
            srt_changed = False
            if os.path.isdir(target):
                for fname in os.listdir(target):
                    if fname.endswith('.srt'):
                        srt_path = os.path.join(target, fname)
                        if os.path.getmtime(srt_path) > findings_mtime:
                            srt_changed = True
                            break
            if not srt_changed:
                print('[scan] findings.json is fresh — skipping rescan '
                      '(use --force-rescan to override)', file=sys.stderr)
                return True
        except Exception:
            pass  # Fall through to normal scan

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
    """Run noun_checker + auto_classify for unknown proper noun candidates.

    noun_checker writes one *_nouns.json per SRT when there are multiple files.
    We use a dedicated subdirectory and aggregate the per-file results afterward.
    """
    print('\n[nouns] Noun table check...', file=sys.stderr)

    # Use a subdirectory for per-file outputs — noun_checker writes one JSON
    # per SRT when len(srt_files) > 1, so a single-file -o target is ignored.
    # MUST create the directory first: noun_checker only respects -o as a dir
    # target when os.path.isdir() returns True (it falls back to dirname otherwise).
    nouns_out_dir = os.path.join(project_dir, 'temp', 'scans', 'nouns')
    os.makedirs(nouns_out_dir, exist_ok=True)
    _run(['python', checker, target, '--lang', lang,
          '--noun-table', glossary,
          '-o', nouns_out_dir],
         project_dir, desc='nouns')

    # Aggregate all per-file *_nouns.json into a single unknown list
    all_unknowns = []
    if os.path.isdir(nouns_out_dir):
        for fname in sorted(os.listdir(nouns_out_dir)):
            if not fname.endswith('_nouns.json'):
                continue
            report = load_json(os.path.join(nouns_out_dir, fname))
            if not report:
                continue
            for r in report.get('results', []):
                if r.get('status') in ('unknown', 'mismatch'):
                    # Tag with source episode for context
                    r['episode'] = os.path.splitext(fname)[0].rsplit('_', 1)[0]
                    all_unknowns.append(r)

    # Save aggregated result for backward compatibility (step_apply_all reads this)
    agg_path = os.path.join(project_dir, 'temp', 'scans', 'noun_check.json')
    agg_data = {
        'total_unknown': len(all_unknowns),
        'results': all_unknowns,
        'fixes': [],  # placeholder — actual fixes come from auto_classify
    }
    with open(agg_path, 'w', encoding='utf-8') as f:
        json.dump(agg_data, f, ensure_ascii=False, indent=2)

    unknowns = all_unknowns
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
        # Wrap in dict with 'fixes' key so step_apply_all can read it
        with open(fixes_path, 'w', encoding='utf-8') as f:
            json.dump({'fixes': accepted_fixes}, f, ensure_ascii=False, indent=2)
        results['auto_accepted'] = len(classified['accepted'])
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


def _dedup_fixes(fixes):
    """Deduplicate fixes by (action, file, original/line) key.

    First occurrence wins (preserves source priority: oped > noun_check > ai_review).
    """
    seen = set()
    result = []
    for fix in fixes:
        action = fix.get('action', '')
        if action in ('replace_global', 'replace_global_regex'):
            key = (action, fix.get('original', fix.get('pattern', '')))
        else:
            key = (action, fix.get('file', ''),
                   fix.get('original', fix.get('line', '')))
        if key not in seen:
            seen.add(key)
            result.append(fix)

    if len(result) < len(fixes):
        print(f'[apply] Dedup: {len(fixes)} → {len(result)} fixes '
              f'(removed {len(fixes) - len(result)} duplicates)', file=sys.stderr)

    return result


def step_apply_all(project_dir, lang):
    """Layer 4: apply_fixes — collect all fixes, apply at once."""
    target = os.path.join(project_dir, 'AI审查后')
    apply_script = os.path.join(_SCRIPT_DIR, 'apply', 'apply_fixes.py')

    # Collect fixes from all sources
    all_fixes = []
    for src in ['oped_fixes.json', 'noun_check.json', 'noun_accepted_fixes.json']:
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

    # Deduplicate across sources
    all_fixes = _dedup_fixes(all_fixes)

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
    """Append auto-classified proper nouns to the glossary — with validation.

    Only appends entries that pass basic proper-noun checks (not sound effects,
    not common words, not fragments).  This prevents the auto_classify →
    glossary feedback loop from accumulating garbage.
    """
    glossary_path = os.path.join(project_dir, 'reports', 'proper-nouns.md')
    if not accepted_candidates:
        return

    # ── Validation helpers (same logic as auto_clean_glossary.py) ──
    import re as _re

    _SOUND_RE = _re.compile(
        r'^([ア-ヾ]{2})\1+ン?$|'
        r'^[ア-ヾ]{1,3}[ッっ]$|'
        r'^[ア-ヾ]{1,3}ー[ッツ]$|'
        r'^[ア-ヾ]{2,3}ーン$|'
        r'^[ア-ヾ]{2,3}ョン$'
    )
    _EXCLAMATION = frozenset({
        'ハッハー', 'ハハハ', 'アハハ', 'イェーイ', 'ワーイ',
        'エーッ', 'アーッ', 'キャー', 'ウワー', 'ヤッター',
        'ハーイ', 'ニャー', 'ニャーニャー', 'チャー',
    })
    _COMMON_WORDS = frozenset({
        'パパ', 'ママ', 'パパママ', 'バイキン', 'アイスクリー',
        'バイバイ', 'バイバーイ', 'パパー', 'エネルギ', 'ネルギー',
        'プロダクショ', 'リボリュー',
    })
    _FRAGMENT_RE = _re.compile(r'^ー[ア-ヾ]')

    def _is_valid_proper_noun(name):
        """Quick validation: reject obvious non-proper-nouns."""
        if _SOUND_RE.match(name):
            return False
        if name in _EXCLAMATION:
            return False
        if name in _COMMON_WORDS:
            return False
        if _FRAGMENT_RE.match(name):
            return False
        if len(name) < 2:
            return False
        return True

    # ── Read existing entries ──
    existing_names = set()
    if os.path.exists(glossary_path):
        with open(glossary_path, 'r', encoding='utf-8') as f:
            for line in f:
                m = re.match(r'\|\s*([^\s|]+)\s*\|', line)
                if m:
                    existing_names.add(m.group(1).strip())

    # ── Filter and validate new entries ──
    new_entries = []
    for c in accepted_candidates:
        name = c.get('candidate', '')
        if not name or name in existing_names:
            continue
        if not _is_valid_proper_noun(name):
            print(f'[glossary] SKIP invalid: {name}', file=sys.stderr)
            continue
        count = c.get('count', 1)
        new_entries.append((name, count))
        existing_names.add(name)

    if not new_entries:
        print('[glossary] All candidates rejected by validation — nothing appended.',
              file=sys.stderr)
        return

    # ── Append to correct section ──
    # Find the 其他片假名术语 section (or 角色名 if honorific)
    with open(glossary_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Default: append before ## 使用方法
    insert_marker = '## 使用方法'
    insert_pos = content.find(insert_marker)
    if insert_pos == -1:
        # Fallback: append at end of file
        insert_pos = len(content)
        content += '\n'

    insert_lines = []
    for name, count in new_entries:
        insert_lines.append(f'| {name} | {count} | — |')

    insert_block = '\n'.join(insert_lines) + '\n'
    new_content = content[:insert_pos] + insert_block + content[insert_pos:]

    with open(glossary_path, 'w', encoding='utf-8') as f:
        f.write(new_content)

    print(f'[glossary] {len(new_entries)} validated proper nouns appended',
          file=sys.stderr)


def step_clean(project_dir):
    """Clean up empty cues."""
    target = os.path.join(project_dir, 'AI审查后')
    cleaner = os.path.join(_SCRIPT_DIR, 'utils', 'clean_empty_cues.py')
    return _run(['python', cleaner, '--target-dir', target],
                project_dir, desc='clean')


def step_ai_review(project_dir, lang):
    """Report AI-review checklists generated by fix_by_whisper().

    fix_by_whisper() now calls review_ai_from_fragments() directly,
    writing ai_review.md per episode. This step scans for those files
    and prints instructions.
    """
    review_dir = os.path.join(project_dir, 'reports', 'manual-review')

    if not os.path.isdir(review_dir):
        print('[ai-review] No manual-review/ directory.', file=sys.stderr)
        return True

    total_entries = 0
    ep_count = 0

    for name in sorted(os.listdir(review_dir)):
        ep_dir = os.path.join(review_dir, name)
        if not os.path.isdir(ep_dir) or not re.match(r'EP\d{3}$', name):
            continue
        ai_md = os.path.join(ep_dir, 'ai_review.md')
        if not os.path.exists(ai_md):
            continue
        ep_count += 1
        # Count entries (each block separated by ---, minus header)
        with open(ai_md, 'r', encoding='utf-8') as f:
            content = f.read()
        count = len(re.findall(r'\n---\n', content))
        total_entries += count
        print(f'\n[ai-review] {name}: {count} pending → {ai_md}',
              file=sys.stderr)

    if total_entries > 0:
        print(f'\n[ai-review] {total_entries} entries across {ep_count} episodes',
              file=sys.stderr)
        print(f'[ai-review] Claude: read ai_review.md files, fill 修正: fields,',
              file=sys.stderr)
        print(f'  then run: python run_all.py --lang {lang} --apply-ai-review',
              file=sys.stderr)
    else:
        print('[ai-review] No pending AI review entries.', file=sys.stderr)

    return True


def step_deliver(project_dir, lang, processed_episodes=None, is_full_run=True,
                 video_dir=None):
    """Generate human review checklists + video clips.

    Reads *_pending_human.json temp files (written by fix_by_reference()
    or _apply_ai_checklists() escalate step) and generates per-episode
    checklist.md + .mp4 clips.
    """
    review_dir = os.path.join(project_dir, 'reports', 'manual-review')
    scan_dir = os.path.join(project_dir, 'temp', 'scans')

    from fix.fix_orchestrator import Fixer

    # Collect pending human-review items from temp JSON files
    import glob as _glob
    all_items = []
    for pf in sorted(_glob.glob(os.path.join(scan_dir, '*_pending_human.json'))):
        data = load_json(pf)
        if data:
            all_items.extend(data)
        try:
            os.remove(pf)
        except OSError:
            pass

    if not all_items:
        # Check if there are ai_review.md files pending
        ai_pending = False
        if os.path.isdir(review_dir):
            for name in os.listdir(review_dir):
                if os.path.exists(os.path.join(review_dir, name, 'ai_review.md')):
                    ai_pending = True
                    break
        if ai_pending:
            print('[deliver] No human-review items — AI review still pending.',
                  file=sys.stderr)
            print('[deliver]   Run --apply-ai-review first.', file=sys.stderr)
        else:
            print('[deliver] No pending human review items — all clean.',
                  file=sys.stderr)
        return True

    # Group by episode
    by_ep = {}
    for e in all_items:
        ep = e.get('ep', '?')
        by_ep.setdefault(ep, []).append(e)

    total_entries = 0
    total_clips = 0

    for ep in sorted(by_ep.keys()):
        ep_dir = os.path.join(review_dir, ep)
        print(f'\n[deliver] {ep}: {len(by_ep[ep])} pending → {ep_dir}',
              file=sys.stderr)

        fixer = Fixer(ep, project_dir, video_dir=video_dir)
        checklist_path = fixer.review_from_items(by_ep[ep], output_dir=ep_dir)

        if checklist_path:
            clips = [f for f in os.listdir(ep_dir) if f.endswith('.mp4')]
            total_clips += len(clips)
            total_entries += len(by_ep[ep])
            print(f'[deliver]   {checklist_path} ({len(clips)} clips)',
                  file=sys.stderr)

    print(f'\n[deliver] {total_entries} pending items across {len(by_ep)} episodes',
          file=sys.stderr)
    print(f'[deliver] {total_clips} video clips extracted', file=sys.stderr)
    print(f'[deliver] After filling corrections, run:', file=sys.stderr)
    print(f'  python scripts/run_all.py --lang {lang} --apply-checklist',
          file=sys.stderr)
    return True


def _apply_ai_checklists(project_dir, lang):
    """Apply filled AI review checklists — shared by --apply-ai-review fast path.

    Scans reports/manual-review/{EP}/ai_review.md, applies corrections
    via Fixer.apply() with VAD alignment.
    """
    review_dir = os.path.join(project_dir, 'reports', 'manual-review')
    if not os.path.isdir(review_dir):
        print('[apply-ai-review] No manual-review/ directory.', file=sys.stderr)
        return False

    from fix.fix_orchestrator import Fixer

    ep_dirs = []
    for name in sorted(os.listdir(review_dir)):
        ep_dir = os.path.join(review_dir, name)
        if os.path.isdir(ep_dir) and re.match(r'EP\d{3}$', name):
            chk = os.path.join(ep_dir, 'ai_review.md')
            if os.path.exists(chk):
                ep_dirs.append((name, chk))

    if not ep_dirs:
        print('[apply-ai-review] No ai_review.md files found.', file=sys.stderr)
        return False

    total_applied = 0
    for ep, checklist_path in ep_dirs:
        fixer = Fixer(ep, project_dir)
        applied = fixer.apply(checklist_path)
        total_applied += applied
        print(f'[apply-ai-review] {ep}: {applied} corrections applied',
              file=sys.stderr)

        # Cleanup: remove applied checklist + empty folder
        ep_dir = os.path.dirname(checklist_path)
        try:
            os.remove(checklist_path)
            remaining = os.listdir(ep_dir)
            if not remaining:
                os.rmdir(ep_dir)
                print(f'[apply-ai-review]   {ep}: folder removed (all done)',
                      file=sys.stderr)
        except OSError:
            pass

    print(f'\n[apply-ai-review] Total: {total_applied} corrections across '
          f'{len(ep_dirs)} episodes', file=sys.stderr)

    # Escalate unfilled entries → VAD check → auto-cut or human review
    # Instead of reading the report, parse ai_review.md files that remain
    # after apply(). Fixer.apply() only processes filled entries (修正: has text),
    # so entries with empty 修正: in remaining ai_review.md files need escalation.
    from lib.whisper_utils import parse_srt, write_srt, to_seconds
    from fix.fix_orchestrator import Fixer

    scan_dir = os.path.join(project_dir, 'temp', 'scans')
    escalated = 0
    auto_cut = 0

    for name in sorted(os.listdir(review_dir)):
        ep_dir = os.path.join(review_dir, name)
        if not os.path.isdir(ep_dir) or not re.match(r'EP\d{3}$', name):
            continue
        ai_md = os.path.join(ep_dir, 'ai_review.md')
        if not os.path.exists(ai_md):
            continue

        # Parse ai_review.md to find UNFILLED entries (修正: empty)
        with open(ai_md, 'r', encoding='utf-8') as f:
            content = f.read()

        if 'version: 2' not in content:
            print(f'[apply-ai-review] {name}: old checklist format — skipping',
                  file=sys.stderr)
            continue

        blocks = re.split(r'\n---\n', content)
        unfilled = []
        for block in blocks:
            time_match = re.search(
                r'(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*~\s*(\d{2}:\d{2}:\d{2}[.,]\d{3})?',
                block)
            if not time_match:
                continue
            corr_match = re.search(
                r'修正:\s*\n?(.+?)(?=\n---|\n\Z|\Z)', block, re.DOTALL)
            if corr_match and corr_match.group(1).strip():
                continue  # Already filled → already applied above
            timecode = time_match.group(1).replace(',', '.')
            orig_match = re.search(r'残留:\s*(.+)', block)
            original = orig_match.group(1).strip() if orig_match else ''
            unfilled.append({
                'ep': name, 'time': timecode,
                'original': original,
            })

        if not unfilled:
            # All entries were applied — cleanup
            try:
                os.remove(ai_md)
                remaining = os.listdir(ep_dir)
                if not remaining:
                    os.rmdir(ep_dir)
            except OSError:
                pass
            continue

        # VAD check for unfilled entries
        fixer = Fixer(name, project_dir)
        speech_segs = fixer._load_speech_segs()
        srt_cues = fixer._load_cues()
        ep_escalated = []
        ep_cut = 0

        for entry in unfilled:
            ts = entry['time']
            start_s = to_seconds(ts) if ts else 0

            has_speech = False
            vad_available = bool(speech_segs)
            if vad_available:
                for ss, es in speech_segs:
                    if es >= start_s and ss <= start_s + 5.0:
                        has_speech = True
                        break

            if has_speech or not vad_available:
                ep_escalated.append(entry)
            else:
                srt_cues = [c for c in srt_cues
                            if c.get('start', '') != ts]
                ep_cut += 1

        # Write escalated items to temp JSON for step_deliver()
        if ep_escalated:
            pending_path = os.path.join(scan_dir,
                                        f'{name}_pending_human.json')
            os.makedirs(scan_dir, exist_ok=True)
            with open(pending_path, 'w', encoding='utf-8') as f:
                json.dump(ep_escalated, f, ensure_ascii=False, indent=2)
            escalated += len(ep_escalated)

        if ep_cut > 0 and fixer._srt_path:
            write_srt(fixer._srt_path, srt_cues)
            auto_cut += ep_cut

        # Clean up ai_review.md (all entries processed)
        try:
            os.remove(ai_md)
            remaining = os.listdir(ep_dir)
            if not remaining:
                os.rmdir(ep_dir)
        except OSError:
            pass

    print(f'[apply-ai-review] VAD check: {escalated} → human, '
          f'{auto_cut} auto-cut (no speech)', file=sys.stderr)
    if escalated:
        print(f'[apply-ai-review]   Run --apply-checklist after filling '
              f'human checklists.', file=sys.stderr)

    return total_applied > 0 or escalated > 0 or auto_cut > 0


def step_apply_checklist(project_dir, lang, video_dir=None):
    """Apply filled per-episode review checklists → SRT + report.

    Scans reports/manual-review/{EP}/checklist.md for each episode folder,
    applies corrections via Fixer.apply() with VAD alignment.
    """
    review_dir = os.path.join(project_dir, 'reports', 'manual-review')
    if not os.path.isdir(review_dir):
        print('[apply-checklist] No manual-review/ directory.', file=sys.stderr)
        return False

    from fix.fix_orchestrator import Fixer

    # Find per-episode folders with a checklist
    ep_dirs = []
    for name in sorted(os.listdir(review_dir)):
        ep_dir = os.path.join(review_dir, name)
        if os.path.isdir(ep_dir) and re.match(r'EP\d{3}$', name):
            chk = os.path.join(ep_dir, 'checklist.md')
            if os.path.exists(chk):
                ep_dirs.append((name, chk))

    if not ep_dirs:
        print('[apply-checklist] No per-episode checklist.md files found.',
              file=sys.stderr)
        return False

    total_applied = 0
    for ep, checklist_path in ep_dirs:
        fixer = Fixer(ep, project_dir, video_dir=video_dir)
        applied = fixer.apply(checklist_path)
        total_applied += applied
        print(f'[apply-checklist] {ep}: {applied} corrections applied', file=sys.stderr)

        # Cleanup: remove applied checklist + empty folder
        ep_dir = os.path.dirname(checklist_path)
        try:
            os.remove(checklist_path)
            remaining = os.listdir(ep_dir)
            if not remaining:
                os.rmdir(ep_dir)
                print(f'[apply-checklist]   {ep}: folder removed (all done)',
                      file=sys.stderr)
        except OSError:
            pass

    print(f'\n[apply-checklist] Total: {total_applied} corrections across '
          f'{len(ep_dirs)} episodes', file=sys.stderr)
    return total_applied > 0


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
    """Print progress summary from findings.json and filesystem state."""
    findings = load_json(os.path.join(project_dir, 'temp', 'scans', 'findings.json'))

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

    # Scan filesystem for pending work
    import glob as _glob
    scan_dir = os.path.join(project_dir, 'temp', 'scans')
    pending_ai = len(_glob.glob(os.path.join(scan_dir, '*_pending_ai.json')))
    pending_human = len(_glob.glob(os.path.join(scan_dir, '*_pending_human.json')))

    review_dir = os.path.join(project_dir, 'reports', 'manual-review')
    review_count = 0
    if os.path.isdir(review_dir):
        for name in os.listdir(review_dir):
            ep_dir = os.path.join(review_dir, name)
            if os.path.isdir(ep_dir) and re.match(r'EP\d{3}$', name):
                contents = os.listdir(ep_dir)
                if any(c.endswith('.md') or c.endswith('.mp4') for c in contents):
                    review_count += 1

    print(f'  AI review pending:    {pending_ai} episode(s)', file=sys.stderr)
    print(f'  Human review pending: {pending_human} episode(s)', file=sys.stderr)
    print(f'  Review folders:       {review_count} episode(s)', file=sys.stderr)


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
    parser.add_argument('--force-rescan', action='store_true',
                        help='Force re-scan even if findings.json is fresh')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    project_dir = args.target_dir or os.getcwd()

    # Ensure subprocess-called scripts can find lib/ via import lib._path
    if _SCRIPT_DIR not in os.environ.get('PYTHONPATH', ''):
        existing = os.environ.get('PYTHONPATH', '')
        os.environ['PYTHONPATH'] = _SCRIPT_DIR + (os.pathsep + existing if existing else '')

    mode = detect_mode(project_dir)
    fmt = detect_format(project_dir)

    # Parse episode selection
    episodes = _parse_episodes(args.episodes) if args.episodes else None

    # Resolve video directory (explicit arg > auto-detect)
    video_dir = _detect_video_dir(project_dir, explicit=args.video_dir)

    # is_full_run: initial full pipeline (no filters, no resume, no fast-paths).
    # During full run, L2.5 items stay in L2.5 for AI review before escalation.
    is_full_run = (args.limit == 0 and not args.episodes and not args.start_from
                   and not args.resume
                   and not args.apply_ai_review and not args.apply_checklist)

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
        print('\n[DRY RUN] — scan only, no files will be modified\n', file=sys.stderr)
        step_scan(project_dir, args.lang, force_rescan=args.force_rescan)
        _print_progress(project_dir, 'Status: dry-run scan')
        return

    # ── Fast path: AI review apply only ──
    if args.apply_ai_review:
        print(f'\n{"─"*40}', file=sys.stderr)
        print('  Apply AI review fixes (fast)', file=sys.stderr)
        print(f'{"─"*40}', file=sys.stderr)
        # Apply JSON-based AI fixes (proper nouns, oped)
        step_apply_all(project_dir, args.lang)
        # Apply per-episode AI review checklists (L2.5 fragments)
        _apply_ai_checklists(project_dir, args.lang)
        # Regenerate human checklists with escalated L2.5 items
        step_deliver(project_dir, args.lang, is_full_run=False, video_dir=video_dir)
        step_clean(project_dir)
        return

    # ── Fast path: checklist apply only ──
    if args.apply_checklist:
        print(f'\n{"─"*40}', file=sys.stderr)
        print('  Apply human review checklist (fast)', file=sys.stderr)
        print(f'{"─"*40}', file=sys.stderr)
        step_apply_checklist(project_dir, args.lang, video_dir=video_dir)
        return

    # ═══════════════════════════════════════════════════════════════
    # Phase 1: Scan — detect all issues
    # ═══════════════════════════════════════════════════════════════
    if not args.resume:
        print(f'\n{"─"*40}', file=sys.stderr)
        print('  Phase 1/3: Character scan', file=sys.stderr)
        print(f'{"─"*40}', file=sys.stderr)
        step_scan(project_dir, args.lang, force_rescan=args.force_rescan)

    _print_progress(project_dir, 'Status: after scan')

    # ═══════════════════════════════════════════════════════════════
    # Phase 2: Fix — Whisper → triage → AI completion
    #
    #  Triaged into 3 paths at L2:
    #    • readable Japanese         → write SRT ✅
    #    • noise (meaningful_jp < 2) → auto-cut
    #    • has JP + Latin corruption → L2.5 AI completion
    #
    #  Noun collection (Phase 3) could run in parallel here since
    #  it only reads SRT.  Currently sequential for simplicity.
    # ═══════════════════════════════════════════════════════════════
    print(f'\n{"─"*40}', file=sys.stderr)
    print('  Phase 2/3: Error fix + AI fragment completion', file=sys.stderr)
    print(f'{"─"*40}', file=sys.stderr)
    processed = step_fix_episodes(project_dir, args.lang, mode, video_dir=video_dir,
                                  skip_whisper=args.skip_whisper,
                                  episodes=episodes, limit=args.limit,
                                  start_from=args.start_from,
                                  skip_if_clean=not args.no_skip_if_clean)

    step_ai_review(project_dir, args.lang)

    # ═══════════════════════════════════════════════════════════════
    # Phase 3: Unify + Deliver
    #
    #  Nouns: collect → auto_classify → apply (after fix, to avoid
    #  write conflicts with Phase 2 SRT modifications).
    #  Deliver: human review checklist + video clips (L6).
    # ═══════════════════════════════════════════════════════════════
    print(f'\n{"─"*40}', file=sys.stderr)
    print('  Phase 3/3: Noun unification + Deliver', file=sys.stderr)
    print(f'{"─"*40}', file=sys.stderr)
    noun_results = step_nouns(project_dir, args.lang)
    print_ai_review_notice(noun_results, project_dir, args.lang)

    step_apply_all(project_dir, args.lang)

    # ASS repair: only for ASS-format projects
    if fmt['primary'] == 'ass':
        step_ass_repair(project_dir)

    step_deliver(project_dir, args.lang, processed_episodes=processed,
                 is_full_run=is_full_run, video_dir=video_dir)

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
        print(f'  Pipeline complete — all phases passed.', file=sys.stderr)
        print(f'{"="*55}', file=sys.stderr)


if __name__ == '__main__':
    main()
