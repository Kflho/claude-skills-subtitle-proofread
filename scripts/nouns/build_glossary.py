#!/usr/bin/env python3
"""Build comprehensive proper noun glossary from corpus-level term frequencies.

Reads unified_scanner output (findings.json) and produces a structured
proper-nouns.md with terms grouped by type, frequency, and spelling variants.

Usage:
  # Generate full glossary from scan results
  python build_glossary.py --findings temp/scans/findings.json \
    --output reports/proper-nouns.md --lang ja
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict

import lib._path  # noqa: F401

from lib.language_utils import get_lang_utils

# Minimum frequency to include in glossary
_MIN_FREQ = 3

# Words that dominate due to OP/ED bracketed labels — always exclude
_BRACKET_LABEL_WORDS = frozenset({
    '音楽', '拍手', '足音', '効果音', '歓声', '爆発音',
    'BGM', 'SE', 'OP', 'ED',
})

# Minimum katakana length (too short = fragment, not meaningful)
_MIN_KATAKANA_LEN = 3


# ═══════════════════════════════════════════════════════════════
# Spelling variant grouping (katakana)
# ═══════════════════════════════════════════════════════════════

def _norm_for_grouping(word):
    """Normalize katakana for variant grouping.
    Strip long vowels and small kana to find spelling variants.
    """
    result = word
    # Remove long vowel marks
    result = result.replace('ー', '')
    # Normalize small kana
    trans = str.maketrans({
        'ァ': 'ア', 'ィ': 'イ', 'ゥ': 'ウ', 'ェ': 'エ', 'ォ': 'オ',
        'ャ': 'ヤ', 'ュ': 'ユ', 'ョ': 'ヨ', 'ッ': 'ツ',
    })
    return result.translate(trans)


def group_katakana_variants(terms):
    """Group katakana terms by normalized form to find spelling variants.

    Returns list of {canonical, variants: [{word, freq}, ...], total_freq}.
    """
    groups = defaultdict(list)
    for word, freq in terms:
        if len(word) < 2:
            continue
        key = _norm_for_grouping(word)
        groups[key].append({'word': word, 'freq': freq})

    result = []
    for key, variants in groups.items():
        variants.sort(key=lambda v: -v['freq'])
        result.append({
            'canonical': variants[0]['word'],  # most frequent = canonical
            'variants': variants,
            'total_freq': sum(v['freq'] for v in variants),
        })

    result.sort(key=lambda g: -g['total_freq'])
    return result


# ═══════════════════════════════════════════════════════════════
# Main processing
# ═══════════════════════════════════════════════════════════════

def build_glossary(term_freq, min_freq=_MIN_FREQ, lang='ja', use_jamdict=True,
                   ai_nouns_path=None):
    """Process raw term frequencies into structured glossary.

    Args:
        lang: 'ja' = katakana + kanji glossary; 'zh' = hanzi only (no katakana)
        use_jamdict: if True, use Jamdict to filter common words (non-proper-nouns)
        ai_nouns_path: optional path to ai_nouns.json from WebSearch enrichment.
                       AI-sourced names bypass min_freq threshold.

    Returns dict with keys: characters, places, organizations, terms, kanji_compounds.
    """
    # ── Language-specific utilities ──
    _LU = get_lang_utils(lang)
    _COMMON_KATAKANA = getattr(_LU, 'COMMON_KATAKANA', frozenset())
    _COMMON_KANJI = getattr(_LU, 'COMMON_KANJI', frozenset())
    _COMMON_WORDS = getattr(_LU, 'COMMON_WORDS', frozenset())
    _PROPER_NOUNS_WHITELIST = getattr(_LU, 'PROPER_NOUNS_WHITELIST', frozenset())
    NON_WORD_RE = getattr(_LU, 'NON_WORD_RE', re.compile(r'$^'))

    # ── Jamdict pre-filter (Japanese only) ──
    _jam = None
    _jamdict_warned = False
    if use_jamdict and lang == 'ja':
        try:
            from jamdict import Jamdict
            _jam = Jamdict()
        except (ImportError, Exception) as e:
            if not _jamdict_warned:
                print(f'WARNING: Jamdict unavailable ({e}) — '
                      f'falling back to COMMON_KANJI frozenset only.',
                      file=sys.stderr)
                _jamdict_warned = True

    # ── jieba pre-filter (Chinese only) ──
    _jieba_freq = None
    _jieba_warned = False
    if use_jamdict and lang == 'zh':
        try:
            import jieba
            jieba.initialize()
            _jieba_freq = jieba.dt.FREQ
        except (ImportError, Exception) as e:
            if not _jieba_warned:
                print(f'WARNING: jieba unavailable ({e}) — '
                      f'falling back to COMMON_KANJI frozenset only.',
                      file=sys.stderr)
                _jieba_warned = True

    # jieba internal frequency threshold: words with freq ≤ this are likely
    # proper nouns that happen to be in jieba's dict (e.g. rare names)
    _JIEBA_MIN_FREQ = 10

    def _is_common_word(word):
        """Check if a word is a common (non-proper-noun) entry.

        Filter chain (first match wins):
          1. PROPER_NOUNS_WHITELIST → skip, always KEEP
          2. COMMON_KANJI / COMMON_KATAKANA → REJECT (AI-confirmed)
          3. COMMON_WORDS → REJECT (language built-ins)
          4. ja: Jamdict lookup → REJECT if in JMdict
          5. zh: jieba dict lookup → REJECT if freq > _JIEBA_MIN_FREQ
          6. Fall through → KEEP (candidate proper noun)
        """
        # 1. Whitelist override (jieba false-positive protection)
        if word in _PROPER_NOUNS_WHITELIST:
            return False

        # 2-3. Hard override: language-specific common word lists
        if word in _COMMON_KANJI or word in _COMMON_KATAKANA or word in _COMMON_WORDS:
            return True

        # 4. Jamdict: Japanese only — in JMdict → common word
        if _jam:
            try:
                result = _jam.lookup(word.strip())
                if len(result.entries) > 0:
                    return True
            except Exception:
                pass

        # 5. jieba: Chinese only — in dict with high freq → common word
        if _jieba_freq is not None:
            freq = _jieba_freq.get(word, 0)
            if freq > _JIEBA_MIN_FREQ:
                return True
            # Low/zero freq → proper noun candidate (keep)

        return False

    # Separate katakana and kanji, filter noise
    katakana_terms = []
    kanji_terms = []

    for word, count in term_freq.items():
        if count < min_freq:
            continue

        # Skip non-word patterns
        if NON_WORD_RE.match(word):
            continue

        # Skip OP/ED bracket labels
        if word in _BRACKET_LABEL_WORDS:
            continue

        # Skip common dictionary words (not proper nouns)
        if _is_common_word(word):
            continue

        is_katakana = bool(re.search(r'[゠-ヿ]', word))
        is_kanji = bool(re.search(r'[一-鿿]', word))

        # Katakana filtering: only for Japanese
        if lang == 'ja' and is_katakana and not is_kanji:
            if word not in _COMMON_KATAKANA and len(word) >= _MIN_KATAKANA_LEN:
                katakana_terms.append((word, count))
        elif is_kanji and len(word) >= 2:
            # For Chinese: use a smaller exclusion list or none
            if lang == 'ja' and word in _COMMON_KANJI:
                continue
            kanji_terms.append((word, count))

    # Group katakana by spelling variants (Japanese only)
    katakana_groups = group_katakana_variants(katakana_terms) if lang == 'ja' else []
    kanji_terms.sort(key=lambda x: -x[1])

    # Heuristic classification
    characters = []
    places = []
    organizations = []
    other_terms = []

    for g in katakana_groups:
        name = g['canonical']
        # Human name heuristics
        if any(suffix in name for suffix in ('さん', 'くん', 'ちゃん')):
            characters.append(g)
        elif len(name) <= 5 and g['total_freq'] >= 5:
            # Short, frequent katakana → likely character name
            characters.append(g)
        else:
            other_terms.append(g)

    result = {
        'characters': characters,
        'places': places,
        'organizations': organizations,
        'other_terms': other_terms,
        'kanji_compounds': [{'word': w, 'freq': f} for w, f in kanji_terms],
    }

    # ── Merge AI-enriched nouns (bypass min_freq, mark source) ──
    if ai_nouns_path and os.path.exists(ai_nouns_path):
        result = _merge_ai_nouns(result, ai_nouns_path, lang)

    return result


def _merge_ai_nouns(glossary, ai_nouns_path, lang='ja'):
    """Merge AI-web-search-enriched proper nouns into the glossary.

    AI-sourced names:
    - Appear even if they have zero corpus frequency
    - Are marked with [AI] source tag
    - Don't duplicate existing entries (matched by canonical name)
    """
    try:
        with open(ai_nouns_path, 'r', encoding='utf-8') as f:
            ai_data = json.load(f)
    except Exception as e:
        print(f'WARNING: Failed to read AI nouns: {e}', file=sys.stderr)
        return glossary

    # Build lookup of existing names
    existing_chars = {g['canonical'] for g in glossary['characters']}
    for g in glossary['characters']:
        for v in g.get('variants', []):
            existing_chars.add(v['word'])

    existing_kanji = {k['word'] for k in glossary['kanji_compounds']}

    # Merge AI characters
    for name in ai_data.get('characters', []):
        if name not in existing_chars and len(name) >= 2:
            glossary['characters'].append({
                'canonical': name,
                'variants': [{'word': name, 'freq': 0}],
                'total_freq': 0,
                'source': '[AI]',
            })
            existing_chars.add(name)

    # Merge AI places & organizations (if any)
    for name in ai_data.get('places', []):
        if name not in existing_kanji:
            glossary['kanji_compounds'].append({
                'word': name, 'freq': 0, 'source': '[AI]',
            })
            existing_kanji.add(name)

    for name in ai_data.get('organizations', []):
        if name not in existing_kanji:
            glossary['kanji_compounds'].append({
                'word': name, 'freq': 0, 'source': '[AI]',
            })
            existing_kanji.add(name)

    for name in ai_data.get('terms', []):
        if name not in existing_chars:
            glossary['other_terms'].append({
                'canonical': name,
                'variants': [{'word': name, 'freq': 0}],
                'total_freq': 0,
                'source': '[AI]',
            })

    n_added = sum(1 for g in glossary['characters'] if g.get('source') == '[AI]')
    print(f'[AI nouns] +{n_added} characters, '
          f'+{sum(1 for k in glossary["kanji_compounds"] if k.get("source") == "[AI]")} '
          f'kanji/places from web search', file=sys.stderr)

    return glossary


# ═══════════════════════════════════════════════════════════════
# Existing glossary parsing (for merge mode)
# ═══════════════════════════════════════════════════════════════

def _parse_existing_glossary(path):
    """Parse existing proper-nouns.md to extract preserved entries.

    Returns dict compatible with build_glossary output structure,
    containing only entries that should be preserved (non-auto-generated).
    """
    if not os.path.exists(path):
        return None

    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    preserved = {'characters': [], 'kanji_compounds': [], 'other_terms': []}

    # Parse 角色名 table
    char_section = _extract_section(content, '角色名')
    for m in re.finditer(r'\|\s*([^\s|]+)\s*\|\s*(\d+)\s*\|\s*(.+?)\s*\|', char_section):
        name_raw, freq, variants_str = m.group(1), int(m.group(2)), m.group(3)
        # Detect and strip source tags from name column (e.g. "アトム[AI]" → "アトム")
        has_source = '[AI]' in name_raw or '[manual]' in name_raw
        source_tag = '[AI]' if '[AI]' in name_raw else ('[manual]' if '[manual]' in name_raw else '')
        name = name_raw.replace('[AI]', '').replace('[manual]', '').strip()
        if freq == 0 or has_source:
            variants = [{'word': name, 'freq': freq}]
            if variants_str != '—':
                for v in variants_str.split(','):
                    v = v.strip()
                    if v and v != name:
                        variants.append({'word': v, 'freq': 0})
            preserved['characters'].append({
                'canonical': name, 'variants': variants,
                'total_freq': freq, 'source': source_tag or ('[AI]' if freq == 0 else '[manual]'),
            })

    # Parse 汉字复合词 table
    kanji_section = _extract_section(content, '汉字复合词')
    for m in re.finditer(r'\|\s*([^\s|]+)\s*\|\s*(\d+)\s*\|', kanji_section):
        name_raw, freq = m.group(1), int(m.group(2))
        has_source = '[AI]' in name_raw or '[manual]' in name_raw
        name = name_raw.replace('[AI]', '').replace('[manual]', '').strip()
        if freq == 0 or has_source:  # freq=0 or has source tag → preserve
            preserved['kanji_compounds'].append({
                'word': name, 'freq': freq,
                'source': '[AI]' if '[AI]' in name_raw else '[manual]',
            })

    # Parse 其他片假名术语 table
    other_section = _extract_section(content, '其他片假名术语')
    for m in re.finditer(r'\|\s*([^\s|]+)\s*\|\s*(\d+)\s*\|\s*(.+?)\s*\|', other_section):
        name_raw, freq, variants_str = m.group(1), int(m.group(2)), m.group(3)
        has_source = '[AI]' in name_raw or '[manual]' in name_raw
        source_tag = '[AI]' if '[AI]' in name_raw else ('[manual]' if '[manual]' in name_raw else '')
        name = name_raw.replace('[AI]', '').replace('[manual]', '').strip()
        if freq == 0 or has_source:
            variants = [{'word': name, 'freq': freq}]
            preserved['other_terms'].append({
                'canonical': name, 'variants': variants,
                'total_freq': freq, 'source': source_tag or '[manual]',
            })

    total = sum(len(v) for v in preserved.values())
    if total > 0:
        print(f'[glossary] Preserved {total} existing entries '
              f'(characters: {len(preserved["characters"])}, '
              f'kanji: {len(preserved["kanji_compounds"])}, '
              f'other: {len(preserved["other_terms"])})', file=sys.stderr)
    return preserved


def _extract_section(content, keyword):
    """Extract a markdown section by header keyword."""
    pattern = rf'##\s+{keyword}[^\n]*\n(.*?)(?=\n##\s|\Z)'
    m = re.search(pattern, content, re.DOTALL)
    return m.group(1) if m else ''


def _merge_preserved(glossary, preserved):
    """Merge preserved entries into glossary, keeping manual/AI entries.

    Existing entries (by canonical name) are not overwritten.
    New preserved entries are appended.
    freq is updated to max(old, new) for existing entries.
    """
    if not preserved:
        return glossary

    # Build name lookup for each category
    for category in ['characters', 'other_terms']:
        existing_names = {g.get('canonical', '') for g in glossary.get(category, [])}
        for entry in preserved.get(category, []):
            name = entry.get('canonical', '')
            if name and name not in existing_names:
                glossary.setdefault(category, []).append(entry)
                existing_names.add(name)

    # Kanji compounds
    existing_kanji = {k.get('word', '') for k in glossary.get('kanji_compounds', [])}
    for entry in preserved.get('kanji_compounds', []):
        name = entry.get('word', '')
        if name and name not in existing_kanji:
            glossary.setdefault('kanji_compounds', []).append(entry)
            existing_kanji.add(name)

    return glossary


# ═══════════════════════════════════════════════════════════════
# Output formatting
# ═══════════════════════════════════════════════════════════════

def format_glossary_markdown(glossary):
    """Format glossary as markdown tables."""
    lines = []

    # ── Characters ──
    chars = glossary.get('characters', [])
    if chars:
        lines.append('## 角色名（自动提取）\n')
        lines.append('| 日语 | 出现次数 | 拼写变体 |')
        lines.append('|------|---------|---------|')
        for g in chars[:80]:
            variants_str = ', '.join(
                v['word'] for v in g['variants'][1:4]
            ) if len(g['variants']) > 1 else '—'
            source_tag = g.get('source', '')
            lines.append(f'| {g["canonical"]}{source_tag} | {g["total_freq"]} | {variants_str} |')
        lines.append('')

    # ── Kanji compounds ──
    kanji = glossary.get('kanji_compounds', [])
    if kanji:
        lines.append('## 汉字复合词\n')
        lines.append('| 日语 | 出现次数 |')
        lines.append('|------|---------|')
        for k in kanji:
            source_tag = k.get('source', '')
            lines.append(f'| {k["word"]}{source_tag} | {k["freq"]} |')
        lines.append('')

    # ── Other terms ──
    other = glossary.get('other_terms', [])
    if other:
        lines.append('## 其他片假名术语\n')
        lines.append('| 日语 | 出现次数 | 拼写变体 |')
        lines.append('|------|---------|---------|')
        for g in other[:60]:
            variants_str = ', '.join(
                v['word'] for v in g['variants'][1:4]
            ) if len(g['variants']) > 1 else '—'
            source_tag = g.get('source', '')
            lines.append(f'| {g["canonical"]}{source_tag} | {g["total_freq"]} | {variants_str} |')
        lines.append('')

    lines.append('\n## 使用方法\n')
    lines.append('校对时遇到疑似专名的词，优先对照此表：')
    lines.append('- 匹配 → 接受')
    lines.append('- 不匹配但在表中 → 修正为表内写法（取最高频拼写）')
    lines.append('- 不在表中 → 保持 Whisper 输出，考虑加入词表\n')

    return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════
# JSON mappings output (machine-readable, for translate_srt.py)
# ═══════════════════════════════════════════════════════════════

def _write_mappings_json(glossary, mappings_path, md_path):
    """Write ja→zh mappings as a simple JSON dict.

    Extracts all unique terms from the glossary, writes to mappings_path.
    If an existing mappings file exists, preserves any zh translations already
    filled in by AI review (new terms added with empty zh value).

    Format: {"ja_term": "zh_translation", ...}
    """
    # Collect all unique Japanese terms from glossary
    new_terms = {}

    for g in glossary.get('characters', []):
        name = g.get('canonical', '')
        if name and len(name) >= 2:
            new_terms[name] = ''
        for v in g.get('variants', []):
            vname = v.get('word', '') if isinstance(v, dict) else v
            if vname and len(vname) >= 2 and vname != name:
                new_terms[vname] = ''

    for k in glossary.get('kanji_compounds', []):
        word = k.get('word', '')
        if word and len(word) >= 2:
            new_terms[word] = ''

    for g in glossary.get('other_terms', []):
        name = g.get('canonical', '')
        if name and len(name) >= 2:
            new_terms[name] = ''
        for v in g.get('variants', []):
            vname = v.get('word', '') if isinstance(v, dict) else v
            if vname and len(vname) >= 2 and vname != name:
                new_terms[vname] = ''

    # Merge with existing mappings (preserve AI-filled translations)
    if os.path.exists(mappings_path):
        try:
            with open(mappings_path, 'r', encoding='utf-8') as f:
                existing = json.load(f)
            preserved = 0
            for term, zh in existing.items():
                if zh and term in new_terms:
                    new_terms[term] = zh
                    preserved += 1
            if preserved:
                print(f'[mappings] Preserved {preserved} AI-filled translations '
                      f'from existing {mappings_path}', file=sys.stderr)
        except Exception as e:
            print(f'WARNING: Could not read existing mappings: {e}', file=sys.stderr)

    # Write
    os.makedirs(os.path.dirname(mappings_path) or '.', exist_ok=True)
    with open(mappings_path, 'w', encoding='utf-8') as f:
        json.dump(new_terms, f, ensure_ascii=False, indent=2)

    filled = sum(1 for v in new_terms.values() if v)
    print(f'→ {mappings_path} ({len(new_terms)} terms, {filled} with zh translation)',
          file=sys.stderr)


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description='Build proper noun glossary from corpus frequency data'
    )
    parser.add_argument('--findings', required=True,
                        help='Path to findings.json (from unified_scanner)')
    parser.add_argument('--output', '-o', required=True,
                        help='Output path for proper-nouns.md')
    parser.add_argument('--min-freq', type=int, default=_MIN_FREQ,
                        help=f'Minimum frequency to include (default: {_MIN_FREQ})')
    parser.add_argument('--lang', default='ja', choices=['ja', 'zh'],
                        help='Target language (default: ja). ja=katakana+kanji glossary, zh=hanzi only')
    parser.add_argument('--ai-nouns',
                        help='Path to ai_nouns.json from WebSearch enrichment')
    parser.add_argument('--no-merge', action='store_true',
                        help='Fully regenerate glossary, discarding manual/AI entries')
    parser.add_argument('--mappings-output',
                        help='Optional JSON output path for ja→zh noun mappings '
                             '(format: {"ja_term": "", ...}, zh values empty '
                             'until AI review fills them in)')
    args = parser.parse_args()

    # Load term frequencies
    with open(args.findings, 'r', encoding='utf-8') as f:
        data = json.load(f)

    term_freq = data.get('term_frequencies', {})
    if not term_freq:
        print('ERROR: No term_frequencies found in findings.json. '
              'Re-run unified_scanner first.', file=sys.stderr)
        sys.exit(1)

    total_terms = len(term_freq)
    print(f'Raw terms: {total_terms}', file=sys.stderr)

    # Build glossary
    ai_nouns = args.ai_nouns if hasattr(args, 'ai_nouns') else None
    glossary = build_glossary(term_freq, min_freq=args.min_freq, lang=args.lang,
                              ai_nouns_path=ai_nouns)
    glossary['_source'] = args.findings

    char_count = len(glossary['characters'])
    kanji_count = len(glossary['kanji_compounds'])
    other_count = len(glossary['other_terms'])
    print(f'Characters: {char_count} | Kanji: {kanji_count} | Other: {other_count}',
          file=sys.stderr)

    # Merge with existing glossary (preserve manual/AI entries)
    if not args.no_merge and os.path.exists(args.output):
        preserved = _parse_existing_glossary(args.output)
        if preserved:
            before_chars = len(glossary['characters'])
            before_kanji = len(glossary['kanji_compounds'])
            before_other = len(glossary['other_terms'])
            glossary = _merge_preserved(glossary, preserved)
            merged_chars = len(glossary['characters']) - before_chars
            merged_kanji = len(glossary['kanji_compounds']) - before_kanji
            merged_other = len(glossary['other_terms']) - before_other
            if merged_chars or merged_kanji or merged_other:
                print(f'[merge] +{merged_chars} characters, '
                      f'+{merged_kanji} kanji, '
                      f'+{merged_other} other terms preserved',
                      file=sys.stderr)

    # Format and write
    md = format_glossary_markdown(glossary)
    os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)
    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(md)

    print(f'→ {args.output}', file=sys.stderr)

    # ── Optional: write machine-readable ja→zh JSON ──
    if args.mappings_output:
        _write_mappings_json(glossary, args.mappings_output, args.output)

    return glossary


if __name__ == '__main__':
    main()
