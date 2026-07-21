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

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_SCRIPT_DIR)  # scripts/
sys.path.insert(0, _ROOT_DIR)

from lib.japanese_utils import COMMON_KATAKANA as _COMMON_KATAKANA

# Common 2-kanji compounds that are NOT names
_COMMON_KANJI = frozenset({
    '今日', '明日', '昨日', '今年', '来年', '毎日', '一度', '一番',
    '自分', '相手', '人間', '世界', '地球', '宇宙', '時間', '場所',
    '電話', '手紙', '約束', '説明', '質問', '返事', '関係', '意味',
    '本当', '大体', '全部', '半分', '一緒', '大丈夫', '可能性',
    '人数', '方向', '速度', '温度', '距離', '重量', '電力',
    '攻撃', '防御', '破壊', '発見', '開発', '製造', '修理',
    '到着', '出発', '通過', '移動', '停止', '開始', '終了',
})

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

def build_glossary(term_freq, min_freq=_MIN_FREQ, lang='ja'):
    """Process raw term frequencies into structured glossary.

    Args:
        lang: 'ja' = katakana + kanji glossary; 'zh' = hanzi only (no katakana)

    Returns dict with keys: characters, places, organizations, terms, kanji_compounds.
    """
    # Separate katakana and kanji, filter noise
    katakana_terms = []
    kanji_terms = []

    for word, count in term_freq.items():
        if count < min_freq:
            continue

        is_katakana = bool(re.search(r'[゠-ヿ]', word))
        is_kanji = bool(re.search(r'[一-鿿]', word))

        # Skip OP/ED bracket labels
        if word in _BRACKET_LABEL_WORDS:
            continue

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

    return {
        'characters': characters,
        'places': places,
        'organizations': organizations,
        'other_terms': other_terms,
        'kanji_compounds': [{'word': w, 'freq': f} for w, f in kanji_terms],
    }


# ═══════════════════════════════════════════════════════════════
# Output formatting
# ═══════════════════════════════════════════════════════════════

def format_glossary_markdown(glossary):
    """Format glossary as markdown tables (auto-generated only, no merge)."""
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
            lines.append(f'| {g["canonical"]} | {g["total_freq"]} | {variants_str} |')
        lines.append('')

    # ── Kanji compounds ──
    kanji = glossary.get('kanji_compounds', [])
    if kanji:
        lines.append('## 汉字复合词\n')
        lines.append('| 日语 | 出现次数 |')
        lines.append('|------|---------|')
        for k in kanji[:60]:
            lines.append(f'| {k["word"]} | {k["freq"]} |')
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
            lines.append(f'| {g["canonical"]} | {g["total_freq"]} | {variants_str} |')
        lines.append('')

    lines.append('\n## 使用方法\n')
    lines.append('校对时遇到疑似专名的词，优先对照此表：')
    lines.append('- 匹配 → 接受')
    lines.append('- 不匹配但在表中 → 修正为表内写法（取最高频拼写）')
    lines.append('- 不在表中 → 保持 Whisper 输出，考虑加入词表\n')

    return '\n'.join(lines)


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
    glossary = build_glossary(term_freq, min_freq=args.min_freq, lang=args.lang)
    glossary['_source'] = args.findings

    char_count = len(glossary['characters'])
    kanji_count = len(glossary['kanji_compounds'])
    other_count = len(glossary['other_terms'])
    print(f'Characters: {char_count} | Kanji: {kanji_count} | Other: {other_count}',
          file=sys.stderr)

    # Format and write
    md = format_glossary_markdown(glossary)
    os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)
    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(md)

    print(f'→ {args.output}', file=sys.stderr)


if __name__ == '__main__':
    main()
