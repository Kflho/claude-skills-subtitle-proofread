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

def _print_glossary_ai_review_notice(project_dir, glossary_path, lang):
    """Notify AI to review the full glossary — no heuristic pre-filtering.

    The glossary is small (typically 30-100 entries for a TV series).
    AI reads the full file, judges each entry in one pass, and directly
    edits the utils file to manage whitelist/blacklist:
      - Proper noun → PROPER_NOUNS_WHITELIST (prevent false rejection)
      - Common word → COMMON_KANJI / COMMON_KATAKANA (reject next time)
    Then re-run build_glossary to regenerate the clean glossary.
    """
    # Count entries
    try:
        with open(glossary_path, 'r', encoding='utf-8') as f:
            content = f.read()
        entry_count = len([l for l in content.split('\n')
                          if l.startswith('| ') and ' | ' in l and not l.startswith('| -')])
    except Exception:
        entry_count = '?'

    utils_file = 'japanese_utils.py' if lang == 'ja' else ('chinese_utils.py' if lang == 'zh' else 'english_utils.py')

    print(f'\n[scan] 🤖 AI Glossary Review — {entry_count} entries in {glossary_path}',
          file=sys.stderr)
    print(f'[scan]    Read the full glossary (small file, ~{entry_count} entries).',
          file=sys.stderr)
    print(f'[scan]    For each entry, judge: proper noun or common word?',
          file=sys.stderr)
    print(f'[scan]    → Proper noun → add to PROPER_NOUNS_WHITELIST in {utils_file}',
          file=sys.stderr)
    print(f'[scan]    → Common word  → add to COMMON_KANJI/KATAKANA in {utils_file}',
          file=sys.stderr)
    print(f'[scan]    Then re-run: build_glossary.py to regenerate clean glossary.',
          file=sys.stderr)


def step_scan(project_dir, lang, force_rescan=False, target_dir=None,
              video_dir=None, skip_vad=False, episodes=None):
    """Layer 1: unified_scanner + build_glossary + VAD missing-sub detection.

    v5.0: When video_dir is provided, runs VAD-based detection of
    speech segments without subtitle coverage (有人声无字幕).

    Args:
        episodes: optional list of episode IDs to scan (None = all)
    """
    target = target_dir or os.path.join(project_dir, 'AI审查后')
    findings = os.path.join(project_dir, 'temp', 'scans', 'findings.json')
    issues = os.path.join(project_dir, 'temp', 'scans', 'issues')
    ai_nouns = os.path.join(project_dir, 'temp', 'scans', 'ai_nouns.json')
    vad_cache = os.path.join(project_dir, 'temp', 'scans')
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
    # v5.0: VAD missing subtitle detection
    if video_dir and os.path.isdir(video_dir):
        cmd.extend(['--video-dir', video_dir,
                    '--vad-cache-dir', vad_cache])
        if skip_vad:
            cmd.append('--skip-vad')
    # Episode filter (limit scan to specific episodes)
    if episodes:
        episode_arg = ','.join(episodes)
        cmd.extend(['--episodes', episode_arg])
    ok = _run(cmd, project_dir, timeout=3600, desc='scan')
    if not ok:
        return False

    # ── AI glossary review ──
    # The glossary is small (typically 30-100 entries). Instead of heuristic
    # auto-clean scripts, AI reviews the full glossary directly — one pass,
    # reasonable token cost, no complex rules to maintain.
    glossary_path = os.path.join(project_dir, 'reports', 'proper-nouns.md')
    if os.path.exists(glossary_path):
        _print_glossary_ai_review_notice(project_dir, glossary_path, lang)

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
    """Phase 2: Unified error-fix via Fixer (reference → Whisper → AI fragments).

    Each episode goes through the cascading priority:
    1. Reference text injection (if 参考字幕/ exists — injected as context into AI fragments)
    2. Whisper audio transcription (if video + Whisper available)
    3. AI fragment completion → unfixable items get [???] markers in SRT

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
        # v5.0: Also include episodes with missing_subtitles but no garbled cues
        missing_subs = findings.get('missing_subtitles', {})
        for ep in missing_subs:
            if ep not in selected and missing_subs[ep]:
                selected.append(ep)
        selected = sorted(selected)
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
    # v5.0: Don't skip episodes that have missing_subtitles even if garbled-clean
    if skip_if_clean:
        from fix.fix_orchestrator import Fixer
        missing_subs = (findings or {}).get('missing_subtitles', {})
        clean_eps = []
        for ep in selected:
            try:
                fixer = Fixer(ep, project_dir, target_lang=lang, srt_dir=target_dir)
                has_missing = bool(missing_subs.get(ep, []))
                if fixer.is_clean() and not has_missing:
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

    # Suspect noun search — proactively find potential proper nouns
    # Uses word segmentation to flag terms not in glossary that look like names
    suspect_script = os.path.join(_SCRIPT_DIR, 'nouns', 'find_suspect_nouns.py')
    suspect_output = os.path.join(project_dir, 'temp', 'scans', 'suspect_nouns.json')
    mappings = os.path.join(project_dir, 'temp', 'noun_mappings.json')
    suspect_args = ['python', suspect_script, '--input-dir', target,
                    '--lang', lang, '--mode', 'source', '--output', suspect_output]
    if os.path.exists(glossary):
        suspect_args.extend(['--glossary', glossary])
    if os.path.exists(mappings):
        suspect_args.extend(['--mappings', mappings])

    # Translation mode: also cross-reference with Japanese source if available
    source_dir = os.path.join(project_dir, '日文ai修复版')
    if lang == 'zh' and os.path.isdir(source_dir):
        suspect_args.extend(['--mode', 'translation', '--source-dir', source_dir])

    _run(suspect_args, project_dir, desc='suspect-nouns')

    # Wire suspect nouns to report layer '3'
    report_path = os.path.join(project_dir, 'reports', '问题解决报告.md')
    if os.path.exists(suspect_output):
        _wire_suspect_nouns_to_report(suspect_output, report_path)

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
        'fixes': [],  # placeholder — actual fixes come from AI review
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

    # All candidates → AI review (no heuristic pre-classification)
    return _apply_classified_results(project_dir, candidates_for_classify, unknowns, cands, lang)


def _apply_classified_results(project_dir, candidates, unknowns, cands, lang):
    """Send all unknown noun candidates to AI for review — no heuristic pre-filter.

    The candidate list is small (≤50, capped by noun_checker). AI reviews all
    candidates in one pass — much simpler than maintaining heuristic classification
    rules (JMdict lookups, verb-stem patterns, sound-effect regexes, etc.).
    AI directly manages the whitelist/blacklist in the language utils file.
    """
    results = {'total_unknown': len(unknowns)}

    # All candidates → AI review (capped at 50 most frequent)
    top_candidates = [{'candidate': c, 'count': n}
                      for c, n in cands.most_common(50)]
    results['ai_review_count'] = len(top_candidates)
    results['ai_candidates'] = top_candidates

    ai_path = os.path.join(project_dir, 'temp', 'scans', 'ai_review_candidates.json')
    os.makedirs(os.path.dirname(ai_path), exist_ok=True)
    with open(ai_path, 'w', encoding='utf-8') as f:
        json.dump(top_candidates, f, ensure_ascii=False, indent=2)
    results['ai_review_file'] = ai_path

    print(f'\n[noun] {len(top_candidates)} candidates → AI review ({ai_path})',
          file=sys.stderr)

    # ── Write L3.5 report entries ──
    try:
        report_path = os.path.join(project_dir, 'reports', '问题解决报告.md')
        _replace_layer(report_path, step='3.5', entries=[
            {'ep': '', 'time': '', 'original': c['candidate'],
             'corrected': '', 'status': '⬜'}
            for c in top_candidates
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


def step_polish(project_dir, target_dir=None):
    """Layer 6 (zh only): AI polish — DeepSeek 批量去翻译腔。

    Reads Chinese SRT from AI审查后/, polishes via DeepSeek API,
    writes to 中文润色后/.
    """
    target = target_dir or os.path.join(project_dir, 'AI审查后')
    output_dir = os.path.join(project_dir, '中文润色后')
    glossary = os.path.join(project_dir, 'reports', 'proper-nouns.md')

    polish_script = os.path.join(_SCRIPT_DIR, 'polish_zh.py')
    cmd = [
        'python', polish_script,
        '--input-dir', target,
        '--output-dir', output_dir,
    ]
    if os.path.exists(glossary):
        cmd.extend(['--glossary', glossary])

    print(f'[polish] 润色 {target} → {output_dir}', file=sys.stderr)
    return _run(cmd, project_dir, timeout=7200, desc='polish')


def _wire_suspect_nouns_to_report(suspect_json_path, report_path):
    """Read suspect_nouns.json and write entries to report layer '3'.

    Handles both legacy format (groups+singletons) and new unified candidates format.
    """
    import json as _json
    try:
        with open(suspect_json_path, 'r', encoding='utf-8') as f:
            data = _json.load(f)
    except Exception:
        return

    entries = []

    # ── New unified candidates format ──
    if 'candidates' in data:
        for c in data['candidates'][:80]:  # cap for report
            ctype = c.get('type', '')
            if ctype == 'inconsistency':
                zh_wrong = ''
                zh_right = c.get('zh_canonical_in_mappings', '')
                ja_src = c.get('ja_source', '')[:30]
                for a in c.get('zh_appearances', []):
                    if a.get('text', '') != zh_right:
                        zh_wrong = a.get('text', '')
                        break
                locs = c.get('sample_contexts', [])
                ctx_text = locs[0].get('zh_text', '')[:40] if locs else ''
                ts = locs[0].get('timestamp', '') if locs else ''
                fn = locs[0].get('file', '') if locs else ''
                entries.append({
                    'ep': fn[-20:],
                    'time': ts,
                    'original': zh_wrong,
                    'corrected': f'→ {zh_right} (ja: {ja_src})',
                    'status': '⬜',
                    'note': f'inconsistency; ctx: {ctx_text}',
                })
            elif ctype == 'unknown_suspect':
                ctxs = c.get('sample_contexts', [])
                ctx_text = ctxs[0].get('zh_text', '')[:40] if ctxs else ''
                ts = ctxs[0].get('timestamp', '') if ctxs else ''
                fn = ctxs[0].get('file', '') if ctxs else ''
                entries.append({
                    'ep': fn[-20:],
                    'time': ts,
                    'original': c.get('zh_term', ''),
                    'corrected': '',
                    'status': '⬜',
                    'note': f'unknown_suspect (×{c.get("frequency", 0)}); ctx: {ctx_text}',
                })
    else:
        # ── Legacy format (groups + singletons) ──
        for g in data.get('groups', []):
            variants = g.get('variants', [])
            source_ja = g.get('source_ja', g.get('suspected_canonical', '?'))
            reason = g.get('reason', '')
            for v in variants[:5]:
                ctx_list = v.get('contexts', []) if isinstance(v, dict) else []
                ctx_text = ctx_list[0].get('cue_text', '')[:40] if ctx_list else ''
                entries.append({
                    'ep': v.get('ep', '') if isinstance(v, dict) else '',
                    'time': v.get('cue_start', '') if isinstance(v, dict) else '',
                    'original': v['text'] if isinstance(v, dict) else v,
                    'corrected': f'→ {g["suspected_canonical"]} (ja: {source_ja[:30]})',
                    'status': '⬜',
                    'note': f'{reason}; ctx: {ctx_text}',
                })

        for s in data.get('singletons', [])[:30]:
            ctx = s.get('contexts', [{}])
            ctx_text = ctx[0].get('cue_text', '')[:40] if ctx else ''
            entries.append({
                'ep': s.get('eps', [''])[0] if s.get('eps') else '',
                'time': ctx[0].get('cue_start', '') if ctx else '',
                'original': s['text'],
                'corrected': '',
                'status': '⬜',
                'note': f'{s.get("reason", "")} (×{s.get("count", 0)}); ctx: {ctx_text}',
            })

    if entries:
        _replace_layer(report_path, step='3', entries=entries)
        print(f'[suspect-nouns] {len(entries)} entries → report layer 3', file=sys.stderr)


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
    """Final delivery step — degraded-mode report + cleanup.

    v5.1: Human review checklist + video clip extraction has been removed.
    [???] markers are written directly to SRT by apply_ai_fragments().
    Humans review in Aegisub (Search → Find → "[???]").

    This function now only handles the degraded-mode report (残血运行:
    garbled cues without Whisper → report for manual/AI processing).
    """
    review_dir = os.path.join(project_dir, 'reports', 'manual-review')
    scan_dir = os.path.join(project_dir, 'temp', 'scans')

    # ── Degraded mode: unresolved garbled cues from Phase 1 scan ──
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

    # ── Final status ──
    # Count remaining [???] markers across all SRT files
    target = target_dir or os.path.join(project_dir, 'AI审查后')
    marker_count = 0
    if os.path.isdir(target):
        for fname in sorted(os.listdir(target)):
            if not fname.endswith(('.srt', '.ass')):
                continue
            fpath = os.path.join(target, fname)
            try:
                with open(fpath, 'r', encoding='utf-8') as f:
                    content = f.read()
                count = content.count('[???]')
                if count > 0:
                    marker_count += count
                    print(f'[deliver] {fname}: {count} [???] markers → review in Aegisub',
                          file=sys.stderr)
            except Exception:
                pass

    if marker_count > 0:
        print(f'\n[deliver] {marker_count} [???] markers total — '
              f'open in Aegisub, Search → Find → "[???]" to review.',
              file=sys.stderr)
    elif garbled:
        print(f'[deliver] {len(garbled)} garbled cues remain — '
              f'run with Whisper or manually review.', file=sys.stderr)
    else:
        print('[deliver] All clean — no [???] markers, no garbled cues.',
              file=sys.stderr)
    return True


def _apply_ai_checklists(project_dir, lang, target_dir=None):
    """Apply filled AI fragment corrections — shared by --apply-ai-review fast path.

    Scans temp/scans/ai_fragments_EP*.json, applies corrections via
    Fixer.apply_ai_fragments() which handles VAD alignment + escalation
    to human review for unfilled entries.
    """
    srt_dir = target_dir or os.path.join(project_dir, 'AI审查后')
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
        fixer = Fixer(ep, project_dir, target_lang=lang, srt_dir=srt_dir)
        applied = fixer.apply_ai_fragments(json_path)
        total_applied += applied
        print(f'[apply-ai-review] {ep}: {applied} corrections applied',
              file=sys.stderr)

    print(f'\n[apply-ai-review] Total: {total_applied} corrections across '
          f'{len(json_files)} episodes', file=sys.stderr)

    return total_applied > 0



# ── AI Review flagging ──

def print_ai_review_notice(noun_results, project_dir, lang):
    """Print AI review instructions for noun candidates — no heuristic pre-filter.

    All unknown candidates from noun_checker go directly to AI review.
    The list is capped at 50 (by noun_checker frequency ranking).
    """
    count = noun_results.get('ai_review_count', 0)
    total = noun_results.get('total_unknown', 0)

    if count == 0:
        if total > 0:
            print(f'\n[AI review] {total} candidates filtered (all matched). '
                  f'Nothing needs AI review.', file=sys.stderr)
        else:
            print('\n[AI review] All proper nouns matched — nothing to review.',
                  file=sys.stderr)
        return

    candidates = noun_results.get('ai_candidates', [])
    ai_file = noun_results.get('ai_review_file', '')

    print(f'\n{"="*60}', file=sys.stderr)
    print(f'  AI REVIEW NEEDED: {count} proper noun candidates '
          f'(out of {total} total unknown)', file=sys.stderr)
    print(f'{"="*60}', file=sys.stderr)
    print(f'\n  Candidates saved to: {ai_file}', file=sys.stderr)
    print(f'\n  Candidates:', file=sys.stderr)
    for c in candidates:
        print(f'    {c["candidate"]} ({c["count"]}x)', file=sys.stderr)

    utils_file = 'japanese_utils.py' if lang == 'ja' else ('chinese_utils.py' if lang == 'zh' else 'english_utils.py')
    print(f'\n  AI任务（一次性审查所有候选项，≤50条）：', file=sys.stderr)
    print(f'  1. 判断每个候选项是否为专有名词', file=sys.stderr)
    print(f'  2. 专有名词 → 给出规范形式，写入 ai_review_fixes.json', file=sys.stderr)
    print(f'     Format: [{{"action":"replace_global","original":"...","replacement":"..."}},...]', file=sys.stderr)
    print(f'  3. 常见词 → 加入 {utils_file} 的 COMMON_KANJI/KATAKANA 黑名单', file=sys.stderr)
    print(f'  4. 专名白名单 → 加入 PROPER_NOUNS_WHITELIST（防误杀）', file=sys.stderr)
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
        if s.get('missing_subtitle_gaps'):
            print(f'  Missing subtitles:   {s["missing_subtitle_gaps"]} gaps '
                  f'in {s.get("episodes_with_missing_subs", "?")} episodes',
                  file=sys.stderr)
        if s.get('repeat_count'):
            print(f'  Repeat patterns:     {s["repeat_count"]}', file=sys.stderr)
    else:
        print(f'  (no findings.json)', file=sys.stderr)

    # Scan filesystem for pending work
    import glob as _glob
    scan_dir = os.path.join(project_dir, 'temp', 'scans')
    pending_ai = len(_glob.glob(os.path.join(scan_dir, '*_pending_ai.json')))
    print(f'  AI review pending:    {pending_ai} episode(s)', file=sys.stderr)


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
                   and not args.apply_ai_review)

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
        step_scan(project_dir, resolved_lang, force_rescan=args.force_rescan,
                  target_dir=target_dir, video_dir=video_dir, episodes=episodes)
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
        _apply_ai_checklists(project_dir, resolved_lang, target_dir=target_dir)
        # [???] markers are written directly to SRT by apply_ai_fragments()
        # — no separate deliver step needed. Review in Aegisub.
        step_clean(project_dir, target_dir=target_dir)
        return

    # ═══════════════════════════════════════════════════════════════
    # Phase 1: Scan — detect all issues
    # ═══════════════════════════════════════════════════════════════
    if not args.resume:
        print(f'\n{"─"*40}', file=sys.stderr)
        print('  Phase 1/3: Character scan + VAD missing-sub detection', file=sys.stderr)
        print(f'{"─"*40}', file=sys.stderr)
        step_scan(project_dir, resolved_lang, force_rescan=args.force_rescan,
                  target_dir=target_dir, video_dir=video_dir, episodes=episodes)

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

    # ── AI Polish (--lang zh only) ──
    if not args.dry_run and resolved_lang == 'zh':
        print(f'\n{"─"*40}', file=sys.stderr)
        print(f'  中文字幕 AI 润色（去翻译腔）', file=sys.stderr)
        print(f'{"─"*40}', file=sys.stderr)
        print(f'  是否对最终字幕进行 AI 润色？(y/n) ', end='', file=sys.stderr)
        sys.stderr.flush()
        try:
            answer = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = 'n'
        if answer in ('y', 'yes'):
            step_polish(project_dir, target_dir=target_dir)
        else:
            print(f'  跳过 AI 润色。', file=sys.stderr)


if __name__ == '__main__':
    main()
