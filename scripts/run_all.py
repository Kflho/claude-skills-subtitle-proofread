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

from utils.update_report import upsert_entries as _upsert_report
from utils.update_report import replace_layer as _replace_layer

from lib.project_utils import load_json, detect_resources, resources_summary, can_use_whisper, detect_format, detect_project_lang


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

def _save_glossary_borderline(project_dir, glossary_path, lang):
    """Save low-frequency kept entries from auto_clean for AI review.

    After auto_clean prunes the glossary, some non-proper-nouns may survive
    because they don't match any reject pattern. AI reviews only these
    borderline entries (frequency ≤ 5) — NOT the full glossary.
    """
    try:
        from nouns.auto_clean_glossary import scan_glossary
        result = scan_glossary(glossary_path, lang=lang)
    except Exception as e:
        print(f'[scan] Borderline scan failed: {e}', file=sys.stderr)
        return

    kept = result.get('kept', [])
    if not kept:
        return

    # Filter: only low-frequency entries that aren't in the anime whitelist
    from nouns.auto_clean_glossary import _ANIME_WHITELIST
    borderline = [
        {'word': w, 'freq': f, 'section': s}
        for w, f, s in kept
        if f <= 5 and w not in _ANIME_WHITELIST
    ]

    if not borderline:
        return

    # Save to small JSON — AI reads this instead of 200+ line glossary
    out_path = os.path.join(project_dir, 'temp', 'scans', 'glossary_borderline.json')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(borderline, f, ensure_ascii=False, indent=2)

    print(f'\n[scan] {len(borderline)} borderline glossary entries → {out_path}',
          file=sys.stderr)

    # Print the entries directly so Claude sees them without reading the file
    print(f'[scan] 🤖 Glossary AI Review candidates (low-freq, ≤5 occurrences):',
          file=sys.stderr)
    for entry in borderline:
        print(f'  {entry["word"]} ({entry["freq"]}x) [{entry["section"]}]',
              file=sys.stderr)
    print(f'[scan] Judge each: proper noun? If not, add to COMMON_KANJI/KATAKANA '
          f'in japanese_utils.py.', file=sys.stderr)


def step_scan(project_dir, lang, force_rescan=False, target_dir=None):
    """Layer 1: unified_scanner + build_glossary.

    If findings.json already exists and SRT files haven't changed since
    last scan, skip re-scanning (idempotent). Use --force-rescan to override.
    """
    target = target_dir or os.path.join(project_dir, 'AI审查后')
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
                    if fname.endswith(('.srt', '.ass')):
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
    ok = _run(cmd, project_dir, timeout=3600, desc='scan')
    if not ok:
        return False

    # ── Auto-clean glossary (remove common words that slipped through) ──
    glossary_path = os.path.join(project_dir, 'reports', 'proper-nouns.md')
    if os.path.exists(glossary_path):
        auto_clean = os.path.join(_SCRIPT_DIR, 'nouns', 'auto_clean_glossary.py')
        print('\n[scan] Auto-cleaning glossary...', file=sys.stderr)
        clean_ok = _run([
            'python', auto_clean,
            '--glossary', glossary_path,
            '--lang', lang,
            '--apply', '--yes',
        ], project_dir, desc='auto_clean')
        if clean_ok:
            # Rebuild glossary with updated COMMON_KANJI/KATAKANA filters
            build_glossary = os.path.join(_SCRIPT_DIR, 'nouns', 'build_glossary.py')
            cmd_rebuild = [
                'python', build_glossary,
                '--findings', findings,
                '-o', glossary_path,
                '--lang', lang,
            ]
            if os.path.exists(ai_nouns):
                cmd_rebuild.extend(['--ai-nouns', ai_nouns])
            _run(cmd_rebuild, project_dir, desc='glossary_rebuild')

            # ── Save borderline entries for AI review ──
            # After auto_clean, entries that survived may still include
            # non-proper-nouns. Save low-frequency kept entries so AI can
            # review them without reading the full 200+ line glossary.
            _save_glossary_borderline(project_dir, glossary_path, lang)
        else:
            print('[scan] auto_clean failed — glossary may contain common words',
                  file=sys.stderr)

    return True


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


def step_fix_episodes(project_dir, lang, resources,
                      skip_whisper=False, episodes=None, limit=0, start_from=None,
                      skip_if_clean=True, target_dir=None):
    """Phase 2: Unified error-fix via Fixer (reference → Whisper → human).

    Each episode goes through the cascading priority:
    1. Reference text injection (if 参考字幕/ exists — injected as context into AI fragments)
    2. Whisper audio transcription (if video + Whisper available)
    3. Human review checklist generation (for unfixable items)

    Graceful degradation: if video or Whisper is missing, skip that step and
    continue with what's available. Phase 2 can run with reference only, Whisper
    only, both, or neither (skip entirely).
    """
    findings = load_json(os.path.join(project_dir, 'temp', 'scans', 'findings.json'))

    # Determine episode list
    if episodes:
        selected = episodes
    elif findings:
        selected = sorted(findings.get('per_episode_issues', {}).keys())
    else:
        # No findings — try to find all subtitle files in target
        target = target_dir or os.path.join(project_dir, 'AI审查后')
        from lib.whisper_utils import extract_file_id
        selected = []
        if os.path.isdir(target):
            for f in sorted(os.listdir(target)):
                if f.endswith(('.srt', '.ass')):
                    fid = extract_file_id(f)
                    if fid and fid != '???':
                        selected.append(fid)

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
                if Fixer(ep, project_dir, target_lang=lang, srt_dir=target_dir).is_clean():
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

    can_whisper = can_use_whisper(resources, skip_whisper=skip_whisper)
    if skip_whisper:
        print('[fix] --skip-whisper: Whisper disabled by user', file=sys.stderr)
    elif not resources['has_video']:
        print('[fix] No video files — skipping Whisper audio fix', file=sys.stderr)
    elif not resources['has_whisper']:
        print('[fix] Whisper not installed — skipping audio fix', file=sys.stderr)
    if resources['has_reference']:
        print(f'[fix] Reference subtitles: {resources["reference_dir"]} — will inject into AI fragments',
              file=sys.stderr)

    for i, ep in enumerate(selected):
        if not can_whisper and not resources['has_reference']:
            # Nothing to do in Phase 2 — skip this episode
            print(f'[fix] {ep} ({i+1}/{len(selected)}): '
                  f'no Whisper + no reference — skipping Phase 2',
                  file=sys.stderr)
            continue
        args = _Args()
        args.video_dir = resources.get('video_dir')
        args.no_backup = False
        args.target_lang = lang
        print(f'[fix] {ep} ({i+1}/{len(selected)})', file=sys.stderr)
        try:
            _run_pipeline(project_dir, ep, resources, args)
        except Exception as e:
            print(f'[fix] {ep} FAILED: {e}', file=sys.stderr)

    return selected


def step_nouns(project_dir, lang, target_dir=None):
    """Layer 3: noun_checker — proper nouns + OP/ED consistency."""
    target = target_dir or os.path.join(project_dir, 'AI审查后')
    glossary = os.path.join(project_dir, 'reports', 'proper-nouns.md')
    checker = os.path.join(_SCRIPT_DIR, 'nouns', 'noun_checker.py')

    results = {}

    # OP/ED cross-episode consistency — new oped_fixer (replaces old noun_checker --oped)
    # Uses cross-episode similarity to classify: instrumental (→ auto-clean) vs vocal (→ AI review)
    print('\n[oped] OP/ED consistency check...', file=sys.stderr)
    oped_fixer = os.path.join(_SCRIPT_DIR, 'fix', 'oped_fixer.py')
    oped_output = os.path.join(project_dir, 'temp', 'scans', 'oped_fixes.json')
    _run(['python', oped_fixer, target, '--lang', lang, '--auto-only',
          '-o', oped_output],
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

        # ── Write L3.5 report entries (fallback) ──
        try:
            report_path = os.path.join(project_dir, 'reports', '问题解决报告.md')
            _upsert_report(report_path, step='3.5', entries=[
                {'ep': '', 'time': '', 'original': c['candidate'],
                 'corrected': '', 'status': '⬜'}
                for c in results['ai_candidates']
            ])
        except Exception as e:
            print(f'[noun] L3.5 report write failed (fallback): {e}', file=sys.stderr)

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
        _append_to_glossary(project_dir, classified['accepted'], lang)

        # ── Write L3 report entries (replace: auto_classify is a full snapshot) ──
        try:
            report_path = os.path.join(project_dir, 'reports', '问题解决报告.md')
            _replace_layer(report_path, step='3', entries=[
                {'ep': '', 'time': '', 'original': c['candidate'],
                 'corrected': c['candidate'], 'status': '✅'}
                for c in classified['accepted']
            ])
        except Exception as e:
            print(f'[noun] L3 report write failed: {e}', file=sys.stderr)

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

        # ── Write L3.5 report entries (replace: auto_classify is a full snapshot) ──
        try:
            report_path = os.path.join(project_dir, 'reports', '问题解决报告.md')
            _replace_layer(report_path, step='3.5', entries=[
                {'ep': '', 'time': '', 'original': c['candidate'],
                 'corrected': '', 'status': '⬜'}
                for c in classified['needs_ai']
            ])
        except Exception as e:
            print(f'[noun] L3.5 report write failed: {e}', file=sys.stderr)

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


def step_apply_all(project_dir, lang, target_dir=None):
    """Layer 4: apply_fixes — collect all fixes, apply at once."""
    target = target_dir or os.path.join(project_dir, 'AI审查后')
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
    report_path = os.path.join(project_dir, 'reports', '问题解决报告.md')
    return _run([
        'python', apply_script,
        '--target-dir', target,
        '--fixes', fixes_path,
        '--lang', lang,
        '--log-to-report', report_path,
        '--step', '4',
    ], project_dir, desc='apply')


def step_ass_repair(project_dir, target_dir=None):
    """Layer 5: ASS format repair (ASS only)."""
    fmt = detect_format(project_dir)
    if fmt['primary'] != 'ass':
        print('[ass] SRT project — skipping.', file=sys.stderr)
        return True

    target = target_dir or os.path.join(project_dir, 'AI审查后')
    repair = os.path.join(_SCRIPT_DIR, 'ass', 'ass_repair.py')
    return _run(['python', repair, '--target-dir', target, '--check', 'all'],
                project_dir, desc='ass')


def _append_to_glossary(project_dir, accepted_candidates, lang):
    """Append auto-classified proper nouns to the glossary — with validation.

    Only appends entries that pass basic proper-noun checks (not sound effects,
    not common words, not fragments).  This prevents the auto_classify →
    glossary feedback loop from accumulating garbage.
    """
    glossary_path = os.path.join(project_dir, 'reports', 'proper-nouns.md')
    if not accepted_candidates:
        return

    # ── Language-aware validation helpers ──
    import re as _re
    from lib.language_utils import get_lang_utils
    _LU = get_lang_utils(lang)
    _COMMON_WORDS = getattr(_LU, 'COMMON_WORDS', frozenset())
    _COMMON_KATAKANA = getattr(_LU, 'COMMON_KATAKANA', frozenset())
    _COMMON_KANJI = getattr(_LU, 'COMMON_KANJI', frozenset())
    _EXCLAMATION_CHARS = getattr(_LU, 'EXCLAMATION_CHARS', frozenset())

    # Japanese-specific validation patterns (only used when lang='ja')
    _SOUND_RE = _re.compile(
        r'^([ア-ヾ]{2})\1+ン?$|'
        r'^[ア-ヾ]{1,3}[ッっ]$|'
        r'^[ア-ヾ]{1,3}ー[ッツ]$|'
        r'^[ア-ヾ]{2,3}ーン$|'
        r'^[ア-ヾ]{2,3}ョン$'
    )
    _FRAGMENT_RE = _re.compile(r'^ー[ア-ヾ]')

    def _is_valid_proper_noun(name):
        """Quick validation: reject obvious non-proper-nouns (language-aware)."""
        if len(name) < 2:
            return False
        # Language-specific common word lists
        if name in _COMMON_WORDS or name in _COMMON_KATAKANA or name in _COMMON_KANJI:
            return False
        # Japanese-specific checks
        if lang == 'ja':
            if _SOUND_RE.match(name):
                return False
            if _FRAGMENT_RE.match(name):
                return False
        # Chinese: reject pure kana (shouldn't be in Chinese glossary)
        if lang == 'zh':
            if _re.fullmatch(r'[ぁ-ヿ]+', name):
                return False
        # English: reject non-Latin (shouldn't be in English glossary)
        if lang == 'en':
            if not _re.search(r'[a-zA-Z]', name):
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


def step_clean(project_dir, target_dir=None):
    """Clean up empty cues."""
    target = target_dir or os.path.join(project_dir, 'AI审查后')
    cleaner = os.path.join(_SCRIPT_DIR, 'utils', 'clean_empty_cues.py')
    return _run(['python', cleaner, '--target-dir', target],
                project_dir, desc='clean')


def step_ai_review(project_dir, lang, target_dir=None):
    """Report AI-review fragments generated by fix_by_whisper().

    fix_by_whisper() now calls _write_ai_fragments_json() directly,
    writing temp/scans/ai_fragments_{EP}.json per episode.
    This step scans for those files and prints instructions.
    """
    scan_dir = os.path.join(project_dir, 'temp', 'scans')

    if not os.path.isdir(scan_dir):
        print('[ai-review] No temp/scans/ directory.', file=sys.stderr)
        return True

    total_entries = 0
    ep_count = 0

    for fname in sorted(os.listdir(scan_dir)):
        if not fname.startswith('ai_fragments_') or not fname.endswith('.json'):
            continue
        json_path = os.path.join(scan_dir, fname)
        data = load_json(json_path)
        if not data:
            continue
        ep = data.get('episode', fname)
        fragments = data.get('fragments', [])
        count = len(fragments)
        if count == 0:
            continue
        ep_count += 1
        total_entries += count
        print(f'\n[ai-review] {ep}: {count} pending → {json_path}',
              file=sys.stderr)

    if total_entries > 0:
        print(f'\n[ai-review] {total_entries} entries across {ep_count} episodes',
              file=sys.stderr)
        print(f'[ai-review] Claude: read ai_fragments_EP*.json, fill "correction" fields,',
              file=sys.stderr)
        print(f'  then run: python run_all.py --lang {lang} --apply-ai-review',
              file=sys.stderr)
    else:
        print('[ai-review] No pending AI review entries.', file=sys.stderr)

    return True


def _write_degraded_report(project_dir, garbled_cues, findings_path, review_dir, scan_dir):
    """Generate a human-review report from Phase 1 scan findings.

    Used when Whisper is unavailable (degraded mode) — garbled cues from
    the scan are written directly to 问题解决报告.md for manual/AI review.
    """
    report_path = os.path.join(project_dir, 'reports', '问题解决报告.md')
    os.makedirs(os.path.dirname(report_path), exist_ok=True)

    from datetime import datetime
    lines = []
    lines.append('# 问题解决报告')
    lines.append('')
    lines.append(f'> 生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    lines.append(f'> 模式: 残血运行（无 Whisper）— 以下问题需手动/AI 处理')
    lines.append(f'> 乱码总数: {len(garbled_cues)}')
    lines.append('')
    lines.append('---')
    lines.append('')
    lines.append('## ⬜ 未修复乱码（扫描发现，待手动处理）')
    lines.append('')
    lines.append('| # | 文件 | 行号 | 时间码 | 原文 | 问题类型 |')
    lines.append('|---|------|------|--------|------|---------|')
    for i, g in enumerate(garbled_cues, 1):
        text = g.get('text', '')[:60].replace('|', '\\|')
        fname = g.get('file', '?')
        line_no = g.get('line', '?')
        tc = g.get('timecode', '?')
        # Classify issue type
        if any('Ѐ' <= c <= 'ӿ' for c in text):
            itype = '俄语残留'
        elif any('぀' <= c <= 'ヿ' for c in text):
            itype = '日语残留'
        elif any(c.isascii() and c.isalpha() for c in text) and any('一' <= c <= '鿿' for c in text):
            itype = '双语混合'
        else:
            itype = '纯外语'
        lines.append(f'| {i} | {fname} | {line_no} | {tc} | {text} | {itype} |')
    lines.append('')
    lines.append('---')
    lines.append('')
    lines.append('## 处理指南')
    lines.append('')
    lines.append('1. 对照参考字幕（如有）逐条确认正确文本')
    lines.append('2. 俄语/日语残留 → 删除源语言字符或翻译为中文')
    lines.append('3. 双语混合 → 删除外语部分，保留中文')
    lines.append('4. 纯外语 → 参考上下文判断是否保留（如歌曲、咒语）')
    lines.append('5. 修复后删除对应行的 ⬜ 标记')
    lines.append('')

    with open(report_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    print(f'[deliver] Degraded report → {report_path}', file=sys.stderr)


def step_deliver(project_dir, lang, processed_episodes=None, is_full_run=True,
                 video_dir=None, target_dir=None):
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
        # Check for unresolved garbled cues from Phase 1 scan (degraded mode)
        findings_path = os.path.join(scan_dir, 'findings.json')
        garbled = []
        if os.path.exists(findings_path):
            fdata = load_json(findings_path)
            if fdata:
                garbled = fdata.get('garbled_cues', [])
                # Filter to only those still in the current target
                if garbled and target_dir:
                    target_files = set()
                    if os.path.isdir(target_dir):
                        target_files = {f for f in os.listdir(target_dir)
                                        if f.endswith(('.ass', '.srt'))}
                    garbled = [g for g in garbled if g.get('file', '') in target_files]

        if garbled and not (os.environ.get('WHISPER_CLI') or os.environ.get('WHISPER_MODEL')):
            # Degraded mode: generate human-review report from scan findings
            _write_degraded_report(project_dir, garbled, findings_path, review_dir, scan_dir)
            print(f'[deliver] Degraded mode: {len(garbled)} unresolved garbled cues '
                  f'→ reports/问题解决报告.md (manual/AI review needed)',
                  file=sys.stderr)
            return True

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
            if garbled:
                print(f'[deliver] {len(garbled)} garbled cues remain — '
                      f'run with Whisper or manually review.', file=sys.stderr)
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

        fixer = Fixer(ep, project_dir, target_lang=lang, video_dir=video_dir, srt_dir=target_dir)
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
    """Apply filled AI fragment corrections — shared by --apply-ai-review fast path.

    Scans temp/scans/ai_fragments_EP*.json, applies corrections via
    Fixer.apply_ai_fragments() which handles VAD alignment + escalation
    to human review for unfilled entries.
    """
    scan_dir = os.path.join(project_dir, 'temp', 'scans')
    if not os.path.isdir(scan_dir):
        print('[apply-ai-review] No temp/scans/ directory.', file=sys.stderr)
        return False

    from fix.fix_orchestrator import Fixer

    json_files = []
    for fname in sorted(os.listdir(scan_dir)):
        if fname.startswith('ai_fragments_') and fname.endswith('.json'):
            ep = fname.replace('ai_fragments_', '').replace('.json', '')
            json_files.append((ep, os.path.join(scan_dir, fname)))

    if not json_files:
        print('[apply-ai-review] No ai_fragments_*.json files found.', file=sys.stderr)
        return False

    total_applied = 0
    for ep, json_path in json_files:
        fixer = Fixer(ep, project_dir, target_lang=lang, srt_dir=target_dir)
        applied = fixer.apply_ai_fragments(json_path)
        total_applied += applied
        print(f'[apply-ai-review] {ep}: {applied} corrections applied',
              file=sys.stderr)

    print(f'\n[apply-ai-review] Total: {total_applied} corrections across '
          f'{len(json_files)} episodes', file=sys.stderr)

    return total_applied > 0


def step_apply_checklist(project_dir, lang, video_dir=None, target_dir=None):
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
        fixer = Fixer(ep, project_dir, target_lang=lang, video_dir=video_dir, srt_dir=target_dir)
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
    parser.add_argument('--lang', default='auto',
                        help='Target language: auto (detect), ja, zh. Default: auto-detect from SRT files.')
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
    parser.add_argument('--input-dir', default='AI审查后',
                        help='Subtitle input directory name (default: AI审查后). '
                             'Use "." to point --target-dir directly at the subtitle files.')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    project_dir = args.target_dir or os.getcwd()
    input_dir = args.input_dir
    if input_dir == '.':
        target_dir = project_dir
    else:
        target_dir = os.path.join(project_dir, input_dir)

    # Propagate to subprocess scripts via env var (used by get_target_dir())
    if input_dir != 'AI审查后':
        os.environ['INPUT_DIR'] = input_dir

    # Ensure subprocess-called scripts can find lib/ via import lib._path
    if _SCRIPT_DIR not in os.environ.get('PYTHONPATH', ''):
        existing = os.environ.get('PYTHONPATH', '')
        os.environ['PYTHONPATH'] = _SCRIPT_DIR + (os.pathsep + existing if existing else '')

    # ── Resolve language: auto-detect or use explicit --lang ──
    if args.lang == 'auto':
        resolved_lang = detect_project_lang(project_dir)
        print(f'[lang] Auto-detected: {resolved_lang} '
              f'(use --lang ja/zh to override)', file=sys.stderr)
    else:
        resolved_lang = args.lang
        print(f'[lang] Manual override: {resolved_lang}', file=sys.stderr)

    # Parse episode selection
    episodes = _parse_episodes(args.episodes) if args.episodes else None

    # Resolve video directory (explicit arg > auto-detect in detect_resources)
    # Pass explicit --video-dir flag to detect_resources; it handles fallback.
    resources = detect_resources(project_dir, video_dir=args.video_dir)
    video_dir = resources.get('video_dir')  # use detected dir everywhere below

    # is_full_run: initial full pipeline (no filters, no resume, no fast-paths).
    # During full run, L2.5 items stay in L2.5 for AI review before escalation.
    is_full_run = (args.limit == 0 and not args.episodes and not args.start_from
                   and not args.resume
                   and not args.apply_ai_review and not args.apply_checklist)

    fmt = detect_format(project_dir)
    print(f'{"="*55}', file=sys.stderr)
    print(f'  Subtitle Proofread — {(fmt["primary"] or "NONE").upper()} format, '
          f'--lang {resolved_lang}', file=sys.stderr)
    print(f'  Project: {project_dir}', file=sys.stderr)
    print(f'  {resources_summary(resources)}', file=sys.stderr)

    # ── Warn if missing critical resources ──
    missing = []
    if not resources['has_video']:
        missing.append('视频')
    if not resources['has_whisper']:
        missing.append('Whisper')
    if missing:
        print(f'  [WARN] 缺少 {"+".join(missing)} — 残血运行（跳过音频修复，仍可扫描+专名统一）',
              file=sys.stderr)
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
        step_scan(project_dir, resolved_lang, force_rescan=args.force_rescan, target_dir=target_dir)
        _print_progress(project_dir, 'Status: dry-run scan')
        return

    # ── Fast path: AI review apply only ──
    if args.apply_ai_review:
        print(f'\n{"─"*40}', file=sys.stderr)
        print('  Apply AI review fixes (fast)', file=sys.stderr)
        print(f'{"─"*40}', file=sys.stderr)
        # Apply JSON-based AI fixes (proper nouns, oped)
        step_apply_all(project_dir, resolved_lang, target_dir=target_dir)
        # Apply per-episode AI review checklists (L2.5 fragments)
        _apply_ai_checklists(project_dir, resolved_lang)
        # Regenerate human checklists with escalated L2.5 items
        step_deliver(project_dir, resolved_lang, is_full_run=False, video_dir=video_dir, target_dir=target_dir)
        step_clean(project_dir, target_dir=target_dir)
        return

    # ── Fast path: checklist apply only ──
    if args.apply_checklist:
        print(f'\n{"─"*40}', file=sys.stderr)
        print('  Apply human review checklist (fast)', file=sys.stderr)
        print(f'{"─"*40}', file=sys.stderr)
        step_apply_checklist(project_dir, resolved_lang, video_dir=video_dir, target_dir=target_dir)
        return

    # ═══════════════════════════════════════════════════════════════
    # Phase 1: Scan — detect all issues
    # ═══════════════════════════════════════════════════════════════
    if not args.resume:
        print(f'\n{"─"*40}', file=sys.stderr)
        print('  Phase 1/3: Character scan', file=sys.stderr)
        print(f'{"─"*40}', file=sys.stderr)
        step_scan(project_dir, resolved_lang, force_rescan=args.force_rescan, target_dir=target_dir)

    _print_progress(project_dir, 'Status: after scan')

    # ═══════════════════════════════════════════════════════════════
    # Phase 2: Fix — Whisper → triage → AI completion
    #
    #  Skipped with --resume (AI review already done, only re-run Phase 3).
    # ═══════════════════════════════════════════════════════════════
    if not args.resume:
        print(f'\n{"─"*40}', file=sys.stderr)
        print('  Phase 2/3: Error fix + AI fragment completion', file=sys.stderr)
        print(f'{"─"*40}', file=sys.stderr)
        processed = step_fix_episodes(project_dir, resolved_lang, resources, target_dir=target_dir,
                                      skip_whisper=args.skip_whisper,
                                      episodes=episodes, limit=args.limit,
                                      start_from=args.start_from,
                                      skip_if_clean=not args.no_skip_if_clean)

        step_ai_review(project_dir, resolved_lang, target_dir=target_dir)
    else:
        print(f'\n{"─"*40}', file=sys.stderr)
        print('  Phase 2/3: SKIPPED (--resume: AI review done, re-running Phase 3 only)',
              file=sys.stderr)
        print(f'{"─"*40}', file=sys.stderr)
        processed = None

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
    noun_results = step_nouns(project_dir, resolved_lang, target_dir=target_dir)
    print_ai_review_notice(noun_results, project_dir, resolved_lang)

    step_apply_all(project_dir, resolved_lang)

    # ASS repair: only for ASS-format projects
    if fmt['primary'] == 'ass':
        step_ass_repair(project_dir, target_dir=target_dir)

    step_deliver(project_dir, resolved_lang, processed_episodes=processed,
                 is_full_run=is_full_run, video_dir=video_dir, target_dir=target_dir)

    step_clean(project_dir, target_dir=target_dir)

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
