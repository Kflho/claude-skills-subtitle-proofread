#!/usr/bin/env python3
"""Proper noun consistency checker for Japanese subtitles.

Scans SRT files against a known proper noun table (reports/proper-nouns.md),
detecting common Whisper errors with Japanese names:

  - Same reading, wrong kanji (天馬→店舗)
  - Katakana long vowel missing/extra (アトム→アトーム)
  - Small kana errors (キャ→キヤ)
  - Voiced/unvoiced consonant confusion (ガデム→カテム)
  - Kanji/kana inconsistency (お茶の水 vs 御茶ノ水)

Usage:
  python noun_checker.py AI审查后/EP064.srt \
    --noun-table reports/proper-nouns.md \
    --output temp/reviews/EP064_nouns.json

  python noun_checker.py AI审查后/ \
    --noun-table reports/proper-nouns.md \
    --output temp/reviews/
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from difflib import SequenceMatcher


# ═══════════════════════════════════════════════════════════════
# 1. Katakana normalizer — reduce to base form for fuzzy matching
# ═══════════════════════════════════════════════════════════════

# Long vowel pairs (2-char → 1-char, applied in order)
_LONG_VOWEL_PAIRS = [
    ('アー', 'ア'), ('イー', 'イ'), ('ウー', 'ウ'), ('エー', 'エ'), ('オー', 'オ'),
    ('カー', 'カ'), ('キー', 'キ'), ('クー', 'ク'), ('ケー', 'ケ'), ('コー', 'コ'),
    ('サー', 'サ'), ('シー', 'シ'), ('スー', 'ス'), ('セー', 'セ'), ('ソー', 'ソ'),
    ('ター', 'タ'), ('チー', 'チ'), ('ツー', 'ツ'), ('テー', 'テ'), ('トー', 'ト'),
    ('ナー', 'ナ'), ('ニー', 'ニ'), ('ヌー', 'ヌ'), ('ネー', 'ネ'), ('ノー', 'ノ'),
    ('ハー', 'ハ'), ('ヒー', 'ヒ'), ('フー', 'フ'), ('ヘー', 'ヘ'), ('ホー', 'ホ'),
    ('マー', 'マ'), ('ミー', 'ミ'), ('ムー', 'ム'), ('メー', 'メ'), ('モー', 'モ'),
    ('ラー', 'ラ'), ('リー', 'リ'), ('ルー', 'ル'), ('レー', 'レ'), ('ロー', 'ロ'),
    ('ガー', 'ガ'), ('ギー', 'ギ'), ('グー', 'グ'), ('ゲー', 'ゲ'), ('ゴー', 'ゴ'),
    ('ザー', 'ザ'), ('ジー', 'ジ'), ('ズー', 'ズ'), ('ゼー', 'ゼ'), ('ゾー', 'ゾ'),
    ('ダー', 'ダ'), ('バー', 'バ'), ('ビー', 'ビ'), ('ブー', 'ブ'), ('ベー', 'ベ'),
    ('ボー', 'ボ'), ('パー', 'パ'), ('ピー', 'ピ'), ('プー', 'プ'), ('ペー', 'ペ'),
    ('ポー', 'ポ'),
]

# Small kana → full size
_SMALL_KANA_MAP = str.maketrans({
    'ァ': 'ア', 'ィ': 'イ', 'ゥ': 'ウ', 'ェ': 'エ', 'ォ': 'オ',
    'ャ': 'ヤ', 'ュ': 'ユ', 'ョ': 'ヨ',
    'ッ': 'ツ', 'ヵ': 'カ', 'ヶ': 'ケ',
})

# Voiced/semi-voiced → clear
_VOICED_TO_CLEAR = str.maketrans({
    'ガ': 'カ', 'ギ': 'キ', 'グ': 'ク', 'ゲ': 'ケ', 'ゴ': 'コ',
    'ザ': 'サ', 'ジ': 'シ', 'ズ': 'ス', 'ゼ': 'セ', 'ゾ': 'ソ',
    'ダ': 'タ', 'ヂ': 'チ', 'ヅ': 'ツ', 'デ': 'テ', 'ド': 'ト',
    'バ': 'ハ', 'ビ': 'ヒ', 'ブ': 'フ', 'ベ': 'ヘ', 'ボ': 'ホ',
    'パ': 'ハ', 'ピ': 'ヒ', 'プ': 'フ', 'ペ': 'ヘ', 'ポ': 'ホ',
})


def normalize_katakana(text, level='strict'):
    """Normalize katakana for fuzzy comparison.

    Levels:
      'strict'  — only long vowel normalization
      'medium'  — long vowel + small kana
      'lenient' — long vowel + small kana + voiced→unvoiced
    """
    # Apply long vowel → base (collapse カー→カ)
    result = text
    for long_pair in [
        ('アー', 'ア'), ('イー', 'イ'), ('ウー', 'ウ'), ('エー', 'エ'), ('オー', 'オ'),
        ('カー', 'カ'), ('キー', 'キ'), ('クー', 'ク'), ('ケー', 'ケ'), ('コー', 'コ'),
        ('サー', 'サ'), ('シー', 'シ'), ('スー', 'ス'), ('セー', 'セ'), ('ソー', 'ソ'),
        ('ター', 'タ'), ('チー', 'チ'), ('ツー', 'ツ'), ('テー', 'テ'), ('トー', 'ト'),
        ('ナー', 'ナ'), ('ニー', 'ニ'), ('ヌー', 'ヌ'), ('ネー', 'ネ'), ('ノー', 'ノ'),
        ('ハー', 'ハ'), ('ヒー', 'ヒ'), ('フー', 'フ'), ('ヘー', 'ヘ'), ('ホー', 'ホ'),
        ('マー', 'マ'), ('ミー', 'ミ'), ('ムー', 'ム'), ('メー', 'メ'), ('モー', 'モ'),
        ('ラー', 'ラ'), ('リー', 'リ'), ('ルー', 'ル'), ('レー', 'レ'), ('ロー', 'ロ'),
        ('ガー', 'ガ'), ('ギー', 'ギ'), ('グー', 'グ'), ('ゲー', 'ゲ'), ('ゴー', 'ゴ'),
        ('ザー', 'ザ'), ('ジー', 'ジ'), ('ズー', 'ズ'), ('ゼー', 'ゼ'), ('ゾー', 'ゾ'),
        ('ダー', 'ダ'), ('バー', 'バ'), ('ビー', 'ビ'), ('ブー', 'ブ'), ('ベー', 'ベ'),
        ('ボー', 'ボ'), ('パー', 'パ'), ('ピー', 'ピ'), ('プー', 'プ'), ('ペー', 'ペ'),
        ('ポー', 'ポ'),
    ]:
        result = result.replace(long_pair[0], long_pair[1])

    if level in ('medium', 'lenient'):
        result = result.translate(_SMALL_KANA_MAP)
    if level == 'lenient':
        result = result.translate(_VOICED_TO_CLEAR)

    return result


def text_similarity(a, b):
    """SequenceMatcher ratio of two strings."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


# ═══════════════════════════════════════════════════════════════
# 2. Noun table parser
# ═══════════════════════════════════════════════════════════════

def parse_noun_table(path):
    """Parse proper-nouns.md into structured list.

    Returns list of {name, reading, category, aliases}.
    """
    nouns = []
    if not os.path.exists(path):
        print(f'WARNING: {path} not found.', file=sys.stderr)
        return nouns

    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Parse markdown tables: | 日语 | 假名/读法 | 说明 |
    table_pattern = re.compile(
        r'^\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|',
        re.MULTILINE
    )

    for m in table_pattern.finditer(content):
        name = m.group(1).strip()
        reading = m.group(2).strip()
        desc = m.group(3).strip()

        # Skip header rows
        if name in ('日语', '---', '仮名', '假名'):
            continue

        # Clean name (remove bold markers, links)
        name = re.sub(r'\*\*|\[|\]|\([^)]*\)', '', name).strip()
        reading = re.sub(r'\*\*|\[|\]|\([^)]*\)', '', reading).strip()

        if name and len(name) >= 2:
            nouns.append({
                'name': name,
                'reading': reading,
                'description': desc,
                'name_norm': normalize_katakana(name, 'lenient'),
            })

    return nouns


# ═══════════════════════════════════════════════════════════════
# 3. SRT scanner — extract potential proper nouns ONLY in name context
# ═══════════════════════════════════════════════════════════════

# Common Japanese loanwords that happen to be in katakana — NOT proper nouns
_COMMON_LOANWORDS = frozenset({
    'ドア', 'テーブル', 'パック', 'バック', 'テスト', 'メモ', 'データ',
    'タイプ', 'レベル', 'モデル', 'システム', 'プログラム', 'サービス',
    'ケース', 'グループ', 'チーム', 'クラス', 'ルール', 'コード',
    'イメージ', 'デザイン', 'コピー', 'チェック', 'リスト', 'ファイル',
    'メッセージ', 'レポート', 'サポート', 'プロジェクト', 'マシン',
    'ライン', 'ポイント', 'ボタン', 'スイッチ', 'パネル', 'ケーブル',
    'エネルギー', 'スピード', 'バランス', 'コントロール', 'センター',
    'エリア', 'ゾーン', 'スペース', 'ホール', 'ルーム', 'ハウス',
    'カード', 'キー', 'ロック', 'ベル', 'サイン', 'マーク',
    'パパ', 'ママ', 'ボーイ', 'ガール', 'ベビー',
})

# Name context patterns — words likely to be someone's name
# Pattern 1: [Name] + suffix (さん/くん/博士/警部/etc.)
_NAME_WITH_SUFFIX = re.compile(
    r'([一-鿿぀-ゟ゠-ヿ]{1,6})'
    r'(さん|くん|ちゃん|様|殿|博士|警部|殿下|先生|総統|団長|部長|署長|伯爵|社長|所長|船長)'
)

# Pattern 2: calling pattern — 「おい、[Name]」or 「[Name]!」or 「[Name]〜」
_CALLING_PATTERN = re.compile(
    r'(?:おい[、\s]*|なあ[、\s]*|ねえ[、\s]*|もしもし[、\s]*)'
    r'([゠-ヿ]{2,6})'
    r'(?:[!！〜～\s]|$)'
)

# Pattern 3: 「[Name]という」— "called [Name]"
_INTRO_PATTERN = re.compile(
    r'([一-鿿぀-ゟ゠-ヿ]{2,8})'
    r'(?:って|という|と言う|と呼ぶ|って言う|といいます|って呼ばれ)'
)

# Pattern 4: standalone katakana that matches known noun table entries
# (applied only if the word appears in proper-nouns.md)


def extract_candidates(cue_text, known_names_set=None):
    """Extract proper noun candidates ONLY from name-relevant contexts.

    Never extracts common loanwords. Only extracts from:
      1. Name + honorific suffix (〜さん/〜博士 etc.)
      2. Calling/interjection patterns (おい、〇〇)
      3. Introduction patterns (〇〇という)
      4. Katakana words that match known names (if known_names_set provided)

    Returns list of {text, position, type, context_evidence}.
    """
    if known_names_set is None:
        known_names_set = frozenset()

    candidates = []

    # ── Pattern 1: Name + suffix ──
    for m in _NAME_WITH_SUFFIX.finditer(cue_text):
        name = m.group(1)
        suffix = m.group(2)
        full = name + suffix
        # Only if name part contains kanji or katakana (not pure hiragana)
        if not name.isascii() and name not in _COMMON_LOANWORDS:
            candidates.append({
                'text': full,
                'name_part': name,
                'start': m.start(),
                'type': 'name_with_suffix',
                'context': f'{name}＋{suffix} (敬称付き)',
            })

    # ── Pattern 2: Calling/interjection ──
    for m in _CALLING_PATTERN.finditer(cue_text):
        name = m.group(1)
        if name not in _COMMON_LOANWORDS and len(name) >= 2:
            candidates.append({
                'text': name,
                'name_part': name,
                'start': m.start(1),
                'type': 'calling',
                'context': f'呼びかけ: ...{cue_text[max(0,m.start()-3):m.end()+3]}...',
            })

    # ── Pattern 3: Introduction ──
    for m in _INTRO_PATTERN.finditer(cue_text):
        name = m.group(1)
        if name not in _COMMON_LOANWORDS:
            candidates.append({
                'text': name,
                'name_part': name,
                'start': m.start(1),
                'type': 'introduction',
                'context': f'紹介: ...{cue_text[max(0,m.start()-2):m.end()+2]}...',
            })

    # ── Pattern 4: Katakana words matching known names ──
    # Only extract katakana words that appear in the known noun table
    if known_names_set:
        for m in re.finditer(r'[゠-ヿ]{2,}', cue_text):
            word = m.group()
            if word in known_names_set:
                # Check this position isn't already covered
                already_covered = any(
                    abs(c['start'] - m.start()) < len(word)
                    for c in candidates
                )
                if not already_covered:
                    candidates.append({
                        'text': word,
                        'name_part': word,
                        'start': m.start(),
                        'type': 'known_katakana',
                        'context': f'既知名詞: {word}',
                    })

    return candidates


# ═══════════════════════════════════════════════════════════════
# 4. Matcher — find known names in text, flag variants
# ═══════════════════════════════════════════════════════════════

def match_candidates(candidates, known_nouns, cue_text, threshold=0.7):
    """Match candidates against known noun table.

    Returns list of {candidate, match_status, matched_noun, suggestion, similarity}.
    """
    results = []

    for cand in candidates:
        cand_text = cand['text']
        cand_norm = normalize_katakana(cand_text, 'lenient')

        best_match = None
        best_score = 0
        best_level = 'none'

        for noun in known_nouns:
            name = noun['name']
            name_norm = noun.get('name_norm', normalize_katakana(name, 'lenient'))

            # Exact match
            if cand_text == name:
                best_match = noun
                best_score = 1.0
                best_level = 'exact'
                break

            # Normalized match (lenient)
            if cand_norm == name_norm:
                if best_score < 0.95:
                    best_match = noun
                    best_score = 0.95
                    best_level = 'normalized'
                continue

            # Similarity match
            sim = text_similarity(cand_norm, name_norm)
            if sim > best_score and sim >= threshold:
                best_match = noun
                best_score = sim
                best_level = 'fuzzy'

        status = 'match'
        suggestion = None

        if best_level == 'exact':
            status = 'exact'
        elif best_level == 'normalized':
            status = 'variant'
            suggestion = best_match['name'] if best_match else None
        elif best_level == 'fuzzy':
            status = 'mismatch'
            suggestion = best_match['name'] if best_match else None
        else:
            status = 'unknown'
            # Unknown katakana word — flag for manual review
            if cand['type'] == 'katakana' and len(cand_text) >= 3:
                status = 'unknown_katakana'

        results.append({
            'candidate': cand_text,
            'position': cand.get('start', 0),
            'type': cand['type'],
            'status': status,
            'matched': best_match['name'] if best_match else None,
            'suggestion': suggestion,
            'similarity': round(best_score, 3),
            'context': cue_text[max(0, cand.get('start', 0) - 5):cand.get('start', 0) + len(cand_text) + 10],
        })

    return results


# ═══════════════════════════════════════════════════════════════
# 5. Main check function
# ═══════════════════════════════════════════════════════════════

def check_srt(srt_path, noun_table_path):
    """Check one SRT file against the noun table."""
    # Parse SRT
    cues = []
    with open(srt_path, 'r', encoding='utf-8-sig') as f:
        content = f.read()

    cue_pattern = re.compile(
        r'(\d+)\s*\n'
        r'(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*\n'
        r'((?:.+\n?)+?)(?=\n\d+\n|\n*\Z)',
        re.MULTILINE
    )

    for m in cue_pattern.finditer(content):
        cues.append({
            'index': int(m.group(1)),
            'start': m.group(2),
            'end': m.group(3),
            'text': m.group(4).strip(),
        })

    # Parse noun table
    known_nouns = parse_noun_table(noun_table_path)
    if not known_nouns:
        return {'error': 'No known nouns found in table'}

    # Build known names set for quick lookup
    known_names_set = frozenset(n['name'] for n in known_nouns)

    # Check each cue
    all_results = []
    stats = defaultdict(int)

    for cue in cues:
        candidates = extract_candidates(cue['text'], known_names_set)
        if not candidates:
            continue

        matches = match_candidates(candidates, known_nouns, cue['text'])

        for r in matches:
            r['cue_start'] = cue['start']
            r['cue_end'] = cue['end']
            r['cue_text'] = cue['text']
            stats[r['status']] += 1
            all_results.append(r)

    return {
        'srt_file': srt_path,
        'known_nouns_count': len(known_nouns),
        'cues_scanned': len(cues),
        'findings': len(all_results),
        'stats': dict(stats),
        'results': sorted(all_results, key=lambda r: (r['status'], r['cue_start'])),
    }


# ═══════════════════════════════════════════════════════════════
# 6. Output formatting
# ═══════════════════════════════════════════════════════════════

def print_summary(report):
    """Print human-readable summary."""
    stats = report.get('stats', {})
    print(f'\n=== Noun Check: {os.path.basename(report["srt_file"])} ===')
    print(f'  Known nouns: {report["known_nouns_count"]}')
    print(f'  Cues scanned: {report["cues_scanned"]}')
    print(f'  Candidates found: {report["findings"]}')
    print()
    for status, count in sorted(stats.items()):
        label = {
            'exact': 'Exact match',
            'variant': 'Variant (ok, normalized)',
            'mismatch': 'MISMATCH — needs fix',
            'unknown_katakana': 'Unknown katakana — review',
            'unknown': 'Unknown — review',
        }.get(status, status)
        print(f'  {label}: {count}')

    # Show mismatches
    mismatches = [r for r in report['results'] if r['status'] in ('mismatch', 'unknown_katakana')]
    if mismatches:
        print(f'\n--- {len(mismatches)} items need attention ---')
        for r in mismatches[:20]:
            tag = '[FIX]' if r['status'] == 'mismatch' else '[REVIEW]'
            sugg = f' → {r["suggestion"]}' if r['suggestion'] else ''
            print(f'  {tag} {r["cue_start"]} | {r["candidate"]}{sugg}')
            print(f'         context: ...{r["context"]}...')
        if len(mismatches) > 20:
            print(f'  ... and {len(mismatches) - 20} more')


# ═══════════════════════════════════════════════════════════════
# 7. CLI
# ═══════════════════════════════════════════════════════════════

def main():
    # UTF-8 on Windows
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        description='Proper noun consistency checker for Japanese subtitles'
    )
    parser.add_argument('target', help='SRT file or directory')
    parser.add_argument('--noun-table', required=True,
                        help='Path to proper-nouns.md')
    parser.add_argument('--output', '-o',
                        help='Output JSON path (or directory for batch mode)')
    parser.add_argument('--threshold', '-t', type=float, default=0.7,
                        help='Fuzzy match threshold (default: 0.7)')
    args = parser.parse_args()

    # Determine single file vs directory
    if os.path.isfile(args.target):
        srt_files = [args.target]
    elif os.path.isdir(args.target):
        srt_files = sorted([
            os.path.join(args.target, f)
            for f in os.listdir(args.target)
            if f.endswith('.srt')
        ])
    else:
        print(f'ERROR: {args.target} not found.', file=sys.stderr)
        sys.exit(1)

    # Process each file
    for srt_path in srt_files:
        print(f'Checking: {os.path.basename(srt_path)} ...', file=sys.stderr)
        report = check_srt(srt_path, args.noun_table)
        print_summary(report)

        # Write output
        if args.output:
            if os.path.isdir(args.output) or len(srt_files) > 1:
                # Batch: output dir
                out_dir = args.output if os.path.isdir(args.output) else os.path.dirname(args.output)
                os.makedirs(out_dir, exist_ok=True)
                base = os.path.splitext(os.path.basename(srt_path))[0]
                out_path = os.path.join(out_dir, f'{base}_nouns.json')
            else:
                out_path = args.output
                os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)

            with open(out_path, 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            print(f'→ {out_path}', file=sys.stderr)


if __name__ == '__main__':
    main()
