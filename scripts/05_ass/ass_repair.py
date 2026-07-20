#!/usr/bin/env python3
"""ASS format repair — unified entry point for all ASS-specific checks.

Replaces 5 individual scripts:
  names_detect.py   — Name field language classification
  styles_detect.py  — Style usage statistics
  drawing_detect.py — Drawing command (\\p1) error detection
  comment_detect.py — Comment line foreign language residue
  oped_detect.py    — OP/ED multi-style track comparison

Usage:
  python ass_repair.py --target-dir <DIR>                          # All checks (default)
  python ass_repair.py --target-dir <DIR> --check names            # Single check
  python ass_repair.py --target-dir <DIR> --check names,styles     # Multiple
  python ass_repair.py --target-dir <DIR> --skip oped              # All except
"""

import argparse
import json
import re
import sys
import os
from collections import Counter, defaultdict

sys.path.insert(0, _root_dir)

from lib.ass_utils import (
    strip_ass_tags, parse_dialogue, build_dialogue_line,
    read_ass_file, write_ass_file, iter_ass_files, iter_dialogue_lines,
    time_to_ms, ms_to_time,
)

ALL_CHECKS = ['names', 'styles', 'drawing', 'comment', 'oped']

# ═══════════════════════════════════════════════════════════════
# Check: names — Name field language classification
# ═══════════════════════════════════════════════════════════════

LANG_CLASSIFIERS = {
    'cjk':        {'name': 'CJK',       'pattern': re.compile(r'[一-鿿]'),         'is_target': True},
    'japanese_kana': {'name': '日语假名',  'pattern': re.compile(r'[぀-ゟ゠-ヿ･-ﾟ]'), 'is_target': False},
    'cyrillic':   {'name': '西里尔字母',  'pattern': re.compile(r'[А-Яа-яЁё]'),     'is_target': False},
    'latin':      {'name': '拉丁字母',    'pattern': re.compile(r'[A-Za-z]'),       'is_target': False},
    'digits':     {'name': '数字',       'pattern': re.compile(r'[0-9]'),           'is_target': True},
    'punctuation':{'name': '标点/符号',   'pattern': re.compile(r'[ 　,，.。!！?？:：;；\-—・&＆·\s]'), 'is_target': True},
}


def _classify_name(name: str) -> dict:
    if not name or not name.strip():
        return {'name': name, 'primary_language': 'empty', 'languages_detected': [],
                'is_non_target': False, 'non_target_chars': []}
    detected = []
    non_target_chars = set()
    for lang_key, cl in LANG_CLASSIFIERS.items():
        matches = cl['pattern'].findall(name)
        if matches:
            detected.append(lang_key)
            if not cl['is_target']:
                non_target_chars.update(matches)
    if not detected:
        primary = 'unknown'
    elif len(detected) == 1:
        primary = detected[0]
    else:
        counts = {lk: len(LANG_CLASSIFIERS[lk]['pattern'].findall(name)) for lk in detected}
        primary = max(counts, key=counts.get)
        if len(detected) > 1 and primary in ('punctuation', 'digits'):
            remaining = {k: v for k, v in counts.items() if k not in ('punctuation', 'digits')}
            if remaining:
                primary = max(remaining, key=remaining.get)
    non_target_langs = [d for d in detected if d in LANG_CLASSIFIERS and not LANG_CLASSIFIERS[d]['is_target']]
    return {
        'name': name, 'primary_language': primary, 'languages_detected': detected,
        'is_non_target': len(non_target_langs) > 0, 'non_target_languages': non_target_langs,
        'non_target_chars': sorted(non_target_chars)[:20],
    }


def check_names(target_dir: str) -> dict:
    """Scan Name fields in all Dialogue lines, classify by language."""
    all_names = set()
    name_file_counts = defaultdict(set)
    name_line_counts = Counter()
    for fname, fpath in iter_ass_files(target_dir):
        lines = read_ass_file(fpath)
        for line in lines:
            d = parse_dialogue(line)
            if d is None:
                continue
            name = d['name'].strip()
            if name:
                all_names.add(name)
                name_line_counts[name] += 1
                name_file_counts[name].add(fname)
    classified = {n: _classify_name(n) for n in all_names}
    non_target = {n: c for n, c in classified.items() if c['is_non_target']}
    non_target_detail = []
    for name, info in non_target.items():
        non_target_detail.append({
            'name': name, 'primary_language': info['primary_language'],
            'non_target_languages': info['non_target_languages'],
            'occurrences': name_line_counts[name],
            'files': sorted(name_file_counts[name]),
            'file_count': len(name_file_counts[name]),
        })
    non_target_detail.sort(key=lambda x: -x['occurrences'])
    by_lang = defaultdict(list)
    for name, info in classified.items():
        by_lang[info['primary_language']].append(name)
    return {
        'findings': non_target_detail,
        'summary': {
            'total_names': len(all_names),
            'target_language_names': len(all_names) - len(non_target),
            'non_target_names': len(non_target),
            'non_target_by_language': {lang: len(items) for lang, items in by_lang.items()
                                       if lang not in ('cjk', 'digits', 'punctuation', 'empty', 'unknown')},
        },
    }


# ═══════════════════════════════════════════════════════════════
# Check: styles — style usage statistics
# ═══════════════════════════════════════════════════════════════

def check_styles(target_dir: str) -> dict:
    """Collect usage statistics for all Dialogue styles."""
    stats = {}
    for fname, fpath in iter_ass_files(target_dir):
        lines = read_ass_file(fpath)
        for line in lines:
            d = parse_dialogue(line)
            if d is None:
                continue
            style = d['style']
            if style not in stats:
                stats[style] = {'count': 0, 'files': set(), 'sample_text': None}
            entry = stats[style]
            entry['count'] += 1
            entry['files'].add(fname)
            if entry['sample_text'] is None:
                visible = strip_ass_tags(d['text']).strip()
                if visible:
                    entry['sample_text'] = visible[:30]
    result = []
    for style, entry in sorted(stats.items(), key=lambda x: -x[1]['count']):
        result.append({
            'style': style, 'count': entry['count'],
            'sample_text': entry['sample_text'] or '',
            'files': sorted(entry['files']),
        })
    return {'findings': result, 'summary': {'total_styles': len(result), 'total_cues': sum(r['count'] for r in result)}}


# ═══════════════════════════════════════════════════════════════
# Check: drawing — \p1 drawing command error detection
# ═══════════════════════════════════════════════════════════════

DRAWING_CMD_RE = re.compile(r'[mnlbspecMNLBSPEC\d\s\.\-]+')


def check_drawing(target_dir: str) -> dict:
    r"""Detect suspicious characters in \p1 drawing command lines."""
    findings = []
    for fname, fpath in iter_ass_files(target_dir):
        lines = read_ass_file(fpath)
        for i, line in enumerate(lines):
            d = parse_dialogue(line)
            if d is None:
                continue
            text = d['text']
            if '\\p1' not in text and '\\p0' not in text:
                continue
            entry = {'file': fname, 'line': i + 1, 'text': text}
            if '\\p1' in text:
                visible = strip_ass_tags(text)
                illegal = re.compile(r'[^mnlbspecMNLBSPEC\d\s\.\-\{\}]+')
                suspicious = [m.group().strip() for m in illegal.finditer(visible) if m.group().strip()]
                entry['has_suspicious_chars'] = len(suspicious) > 0
                entry['suspicious_parts'] = suspicious
            else:
                entry['has_suspicious_chars'] = False
                entry['suspicious_parts'] = []
            findings.append(entry)
    suspicious_count = sum(1 for f in findings if f['has_suspicious_chars'])
    return {'findings': findings, 'summary': {'total_drawing_lines': len(findings), 'suspicious_lines': suspicious_count}}


# ═══════════════════════════════════════════════════════════════
# Check: comment — Comment line foreign language residue
# ═══════════════════════════════════════════════════════════════

LANGUAGE_PRESETS = {
    'en': {'name': 'English',         'text_pattern': r'[A-Za-z]{2,}',      'single_char': r'[A-Za-z]'},
    'jp': {'name': 'Japanese Kana',   'text_pattern': r'[぀-ゟ゠-ヿ･-ﾟ]{1,}', 'single_char': r'[぀-ゟ゠-ヿ･-ﾟ]'},
    'ru': {'name': 'Russian Cyrillic','text_pattern': r'[А-Яа-яЁё]{1,}',    'single_char': r'[А-Яа-яЁё]'},
    'cjk':{'name': 'CJK Ideographs',  'text_pattern': r'[一-鿿]{1,}',       'single_char': r'[一-鿿]'},
}


def _parse_comment_line(line: str) -> dict | None:
    if not line.startswith('Comment:'):
        return None
    parts = line.strip().split(',', 9)
    if len(parts) < 10:
        return {'layer': '0', 'start': '', 'end': '', 'style': '', 'name': '', 'text': '',
                'is_empty': True}
    return {
        'layer': parts[0].split(': ', 1)[1] if ': ' in parts[0] else '0',
        'start': parts[1], 'end': parts[2], 'style': parts[3], 'name': parts[4],
        'margin_l': parts[5], 'margin_r': parts[6], 'margin_v': parts[7],
        'effect': parts[8], 'text': parts[9], 'is_empty': False,
    }


def check_comment(target_dir: str, langs: str = 'en,jp,ru') -> dict:
    """Detect foreign language residue in Comment lines."""
    lang_keys = [s.strip() for s in langs.split(',')]
    detectors = {}
    for key in lang_keys:
        if key in LANGUAGE_PRESETS:
            p = LANGUAGE_PRESETS[key]
            detectors[key] = {'name': p['name'], 'text_re': re.compile(p['text_pattern']),
                              'single_re': re.compile(p['single_char'])}
    if not detectors:
        return {'findings': [], 'summary': {'total_findings': 0}}

    findings = []
    lang_stats = {}
    file_stats = {}
    for fname, fpath in iter_ass_files(target_dir):
        if not fname.lower().endswith('.ass'):
            continue
        lines = read_ass_file(fpath)
        file_findings = []
        for line_idx, line in enumerate(lines):
            c = _parse_comment_line(line)
            if c is None or c['is_empty']:
                continue
            text = c['text'].strip()
            name = c['name'].strip()
            text_matches = {}
            name_matches = {}
            for lkey, det in detectors.items():
                if text:
                    tv = strip_ass_tags(text)
                    if det['text_re'].search(tv):
                        text_matches[lkey] = {'visible': tv[:200], 'lang_name': det['name']}
                if name and det['single_re'].search(name):
                    name_matches[lkey] = {'name': name, 'lang_name': det['name']}
            if text_matches or name_matches:
                finding = {'file': fname, 'line': line_idx + 1, 'timecode': c['start'] if c['start'] else '',
                           'raw_line': line.strip()[:300]}
                if text_matches:
                    finding['text_matches'] = text_matches
                if name_matches:
                    finding['name_matches'] = name_matches
                finding['action'] = 'delete'
                finding['reason'] = 'Comment外语残留'
                findings.append(finding)
                file_findings.append(finding)
                for lk in set(list(text_matches.keys()) + list(name_matches.keys())):
                    lang_stats[lk] = lang_stats.get(lk, 0) + 1
        if file_findings:
            file_stats[fname] = len(file_findings)
    return {
        'findings': findings,
        'summary': {
            'total_findings': len(findings), 'affected_files': len(file_stats),
            'by_language': {LANGUAGE_PRESETS[k]['name']: v for k, v in lang_stats.items()},
        },
    }


# ═══════════════════════════════════════════════════════════════
# Check: oped — OP/ED multi-style lyric comparison
# ═══════════════════════════════════════════════════════════════

def _get_style(line: str) -> str:
    parts = line.strip().split(',', 9)
    return parts[3].strip() if len(parts) >= 10 else ''


def _get_text_and_start(line: str) -> tuple:
    parts = line.strip().split(',', 9)
    return (parts[1], parts[9]) if len(parts) >= 10 else ('', '')


def check_oped(target_dir: str, source_style: str, ref_style: str,
               tolerance_ms: int = 500, canonical_starts: list | None = None) -> dict:
    """Compare two OP/ED style tracks for inconsistencies."""
    per_file_diffs = []
    count_mismatches = []
    comment_remnants = []
    extra_timecodes = []
    missing_timecodes = []
    all_source_texts = {}

    for fname, fpath in iter_ass_files(target_dir):
        lines = read_ass_file(fpath)
        ep_match = re.search(r'(\d+)', fname)
        ep_name = ep_match.group(1) if ep_match else fname

        # Text diff
        ref_map = {}
        for line in lines:
            d = parse_dialogue(line)
            if d and d['style'] == ref_style:
                ref_map[time_to_ms(d['start'])] = d['text']
        source_map = {}
        for line in lines:
            d = parse_dialogue(line)
            if d and d['style'] == source_style:
                ms = time_to_ms(d['start'])
                source_map[ms] = d['text']
                all_source_texts.setdefault(ep_name, {})[ms] = d['text']

        if ref_map:
            for start_ms, source_text in source_map.items():
                best_ref_ms = None
                best_dist = float('inf')
                for ref_ms in ref_map:
                    dist = abs(start_ms - ref_ms)
                    if dist <= tolerance_ms and dist < best_dist:
                        best_ref_ms, best_dist = ref_ms, dist
                if best_ref_ms is not None and ref_map[best_ref_ms] != source_text:
                    line_no = None
                    for i, line in enumerate(lines):
                        d = parse_dialogue(line)
                        if d and d['style'] == source_style and time_to_ms(d['start']) == start_ms:
                            line_no = i + 1
                            break
                    per_file_diffs.append({
                        'file': fname, 'line': line_no, 'start_ms': start_ms,
                        'source_text': source_text, 'ref_text': ref_map[best_ref_ms],
                        'style': source_style,
                    })

        # Count mismatch
        s_count = sum(1 for l in lines if l.startswith('Dialogue:') and _get_style(l) == source_style)
        r_count = sum(1 for l in lines if l.startswith('Dialogue:') and _get_style(l) == ref_style)
        if s_count != r_count:
            count_mismatches.append({'file': fname, 'source_count': s_count, 'ref_count': r_count,
                                     'diff': s_count - r_count})

        # Comment remnants
        for i, line in enumerate(lines):
            if line.startswith('Comment:'):
                style = _get_style(line)
                if style in (source_style, ref_style):
                    _, text = _get_text_and_start(line)
                    comment_remnants.append({
                        'file': fname, 'line': i + 1, 'style': style,
                        'text_preview': text[:50] if text else '(空)',
                    })

        # Extra/missing timecodes
        if canonical_starts:
            source_starts = set()
            for line in lines:
                if line.startswith('Dialogue:') and _get_style(line) == source_style:
                    start, _ = _get_text_and_start(line)
                    source_starts.add(time_to_ms(start))
            extra = source_starts - set(canonical_starts)
            missing = set(canonical_starts) - source_starts
            if extra:
                extra_details = []
                for line in lines:
                    if line.startswith('Dialogue:') and _get_style(line) == source_style:
                        start, text = _get_text_and_start(line)
                        if time_to_ms(start) in extra:
                            extra_details.append({'start': start, 'text_preview': text[:60]})
                extra_timecodes.append({'file': fname, 'extra_timecodes': sorted(extra), 'details': extra_details})
            if missing:
                missing_timecodes.append({'file': fname, 'missing_timecodes': sorted(missing)})

    # Text variants across files
    text_variants = {}
    for start_ms in sorted(set().union(*[set(d.keys()) for d in all_source_texts.values()])):
        texts = Counter()
        for ep, text_map in all_source_texts.items():
            if start_ms in text_map:
                texts[text_map[start_ms]] += 1
        if len(texts) > 1:
            text_variants[start_ms] = [{'text': t, 'count': c} for t, c in texts.most_common()]

    return {
        'findings': {
            'per_file_diffs': per_file_diffs,
            'count_mismatches': count_mismatches,
            'comment_remnants': comment_remnants,
            'extra_timecodes': extra_timecodes,
            'missing_timecodes': missing_timecodes,
            'text_variants': {str(k): v for k, v in text_variants.items()},
        },
        'summary': {
            'total_files': len(all_source_texts),
            'total_text_diffs': len(per_file_diffs),
            'files_with_count_mismatch': len(count_mismatches),
            'total_comment_remnants': len(comment_remnants),
            'text_variant_count': len(text_variants),
        },
    }


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description='ASS format repair — unified entry point for all ASS-specific checks',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Available checks: names, styles, drawing, comment, oped

Examples:
  python ass_repair.py --target-dir ./target/                     # All checks
  python ass_repair.py --target-dir ./target/ --check names       # Single check
  python ass_repair.py --target-dir ./target/ --check names,styles
  python ass_repair.py --target-dir ./target/ --skip oped          # All except oped
  python ass_repair.py --target-dir ./target/ --check oped --oped-config config.json
        """
    )
    parser.add_argument('--target-dir', required=True, help='Target ASS subtitle directory')
    parser.add_argument('--check', default='all',
                        help=f'Comma-separated checks to run: {",".join(ALL_CHECKS)} (default: all)')
    parser.add_argument('--skip', default='',
                        help='Comma-separated checks to skip')
    parser.add_argument('--langs', default='en,jp,ru',
                        help='Languages for comment check (default: en,jp,ru)')
    parser.add_argument('--oped-config',
                        help='JSON config for oped check: {"source_style":"...","ref_style":"..."}')
    parser.add_argument('--oped-tolerance-ms', type=int, default=500)
    parser.add_argument('--oped-canonical-timecodes',
                        help='JSON file with canonical timecode array (ms) for oped check')
    parser.add_argument('-o', '--output', help='Output JSON file path (default: stdout)')
    args = parser.parse_args()

    # Determine which checks to run
    if args.check == 'all':
        selected = set(ALL_CHECKS)
    else:
        selected = set(c.strip() for c in args.check.split(',') if c.strip())
    skip = set(s.strip() for s in args.skip.split(',') if s.strip())
    selected -= skip

    invalid = selected - set(ALL_CHECKS)
    if invalid:
        print(f'ERROR: Unknown check(s): {",".join(invalid)}', file=sys.stderr)
        print(f'       Available: {",".join(ALL_CHECKS)}', file=sys.stderr)
        sys.exit(1)

    if not selected:
        print('ERROR: No checks selected.', file=sys.stderr)
        sys.exit(1)

    # Run selected checks
    results = {}
    for check_name in sorted(selected):
        print(f'[{check_name}] Running...', file=sys.stderr)
        try:
            if check_name == 'names':
                results['names'] = check_names(args.target_dir)
            elif check_name == 'styles':
                results['styles'] = check_styles(args.target_dir)
            elif check_name == 'drawing':
                results['drawing'] = check_drawing(args.target_dir)
            elif check_name == 'comment':
                results['comment'] = check_comment(args.target_dir, langs=args.langs)
            elif check_name == 'oped':
                if not args.oped_config:
                    print('[oped] SKIP — no --oped-config provided', file=sys.stderr)
                    continue
                with open(args.oped_config, 'r', encoding='utf-8') as f:
                    oped_cfg = json.load(f)
                canonical = None
                if args.oped_canonical_timecodes:
                    with open(args.oped_canonical_timecodes, 'r', encoding='utf-8') as f:
                        canonical = json.load(f)
                results['oped'] = check_oped(
                    args.target_dir,
                    source_style=oped_cfg['source_style'],
                    ref_style=oped_cfg['ref_style'],
                    tolerance_ms=oped_cfg.get('tolerance_ms', args.oped_tolerance_ms),
                    canonical_starts=canonical,
                )
        except Exception as e:
            print(f'[{check_name}] ERROR: {e}', file=sys.stderr)
            results[check_name] = {'error': str(e)}

    # Print summaries
    for check_name, result in results.items():
        if 'error' in result:
            print(f'  {check_name}: ERROR — {result["error"]}', file=sys.stderr)
        else:
            s = result.get('summary', {})
            if check_name == 'names':
                print(f'  names: {s.get("total_names", 0)} unique, {s.get("non_target_names", 0)} non-target',
                      file=sys.stderr)
            elif check_name == 'styles':
                print(f'  styles: {s.get("total_styles", 0)} styles, {s.get("total_cues", 0)} cues', file=sys.stderr)
            elif check_name == 'drawing':
                print(f'  drawing: {s.get("total_drawing_lines", 0)} lines, {s.get("suspicious_lines", 0)} suspicious',
                      file=sys.stderr)
            elif check_name == 'comment':
                print(f'  comment: {s.get("total_findings", 0)} findings in {s.get("affected_files", 0)} files',
                      file=sys.stderr)
            elif check_name == 'oped':
                print(f'  oped: {s.get("total_text_diffs", 0)} diffs, {s.get("files_with_count_mismatch", 0)} mismatches',
                      file=sys.stderr)

    # Output
    output = {'checks_run': sorted(selected), 'results': results}
    if args.output:
        os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f'\n→ {args.output}', file=sys.stderr)
    else:
        json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write('\n')


if __name__ == '__main__':
    main()
