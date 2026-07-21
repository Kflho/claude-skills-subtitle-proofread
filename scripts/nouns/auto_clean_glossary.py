#!/usr/bin/env python3
"""Auto-clean proper noun glossary — identify common words that slipped through.

Runs after build_glossary.py.  Uses JMdict + heuristic patterns to find
non-proper-noun entries in proper-nouns.md and suggest (or apply) additions
to COMMON_KANJI.

This automates the manual "scan → classify → add to frozenset → re-run" loop
that would otherwise take 3-5 rounds of human inspection.

Usage:
  # Dry-run: show what would be added
  python auto_clean_glossary.py --glossary reports/proper-nouns.md --lang ja

  # Apply: auto-edit japanese_utils.py + regenerate glossary
  python auto_clean_glossary.py --glossary reports/proper-nouns.md --apply

  # Auto-accept all suggestions (skip confirmation prompt)
  python auto_clean_glossary.py --glossary reports/proper-nouns.md --apply --yes

How it works:
  1. Parses the 「汉字复合词」table from proper-nouns.md
  2. Checks each word against JMdict (in JMdict → common word → REJECT)
  3. Applies heuristic patterns for fragments that JMdict misses:
     - Verb stems (着替, 見捨, …)
     - Time / number fragments (時間後, 日前, …)
     - Modifier fragments (一番大, 全部聞, …)
     - Pronoun / suffix fragments (僕行, 君僕, …)
  4. Skips words matching name / place patterns (surnames, cities, etc.)
  5. Outputs suggested additions to COMMON_KANJI
  6. With --apply: edits japanese_utils.py + re-runs build_glossary.py
"""

import argparse
import json
import os
import re
import sys
from collections import OrderedDict

import lib._path  # noqa: F401

from lib.japanese_utils import COMMON_KANJI as _COMMON_KANJI
from lib.japanese_utils import COMMON_KATAKANA as _COMMON_KATAKANA
from lib.japanese_utils import NON_WORD_RE

_JAMDICT_AVAILABLE = False
_jam = None
try:
    from jamdict import Jamdict
    _jam = Jamdict()
    _JAMDICT_AVAILABLE = True
except Exception:
    pass


# ═══════════════════════════════════════════════════════════════
# Heuristic patterns for common-word fragments
# ═══════════════════════════════════════════════════════════════

# Verb stems — okurigana endings that signal verb/adjective conjugation
# Note: 生 excluded — too ambiguous (verb 生きる vs name suffix 莉生/美生)
# Note: 立 excluded — too ambiguous (verb 立てる vs proper name 日立)
_VERB_ENDINGS_RE = re.compile(
    r'(行|来|出|入|見|言|思|取|持|待|通|違|守|助|探|許|頼|騒|急|知|動|揃|遅|鳴|経|過|儲|隠|殺|捨|回|直|替|聞|切|焼|改|造|払|戻|覚|失|逃|去|帰|歩|走|飛|乗|泳|渡|曲|止|始|終|残|進|向|付|着|続|開|閉|治|落|流|組|張|越|減|増|足|引|押|倒|消|壊|割|育|死|当|打|負|勝|戦|闘)$'
)

# Time / number indicators — words that are probably counting or temporal
_TIME_NUMBER_RE = re.compile(
    r'[今前後日時分秒年月何億万千]'
)

# Modifier / adverb fragments
_MODIFIER_RE = re.compile(
    r'^(全|一|大|最|不|非|無|未|随|絶|随分|全然|絶対)'
)

# Pronoun / person fragments (common first-person/second-person fragments)
_PRONOUN_RE = re.compile(
    r'(僕|私|俺|君|お前|我|彼|誰)'
)

# ── KEEP patterns (indicators of genuine proper nouns) ──

# Common Japanese surname components — words containing these are likely names
_NAME_KEEP_RE = re.compile(
    r'(田|藤|井|村|木|林|森|山|川|谷|沼|瀬|崎|島|湾|沢|坂|原|野|池|浦|塚|畑|岡)'
)

# Place indicators
_PLACE_KEEP_RE = re.compile(
    r'(市|区|町|県|国|都|道|府|京|駅|港|空港)$'
)

# Honorific / title patterns — these make it clear the word is a name
_HONORIFIC_KEEP_RE = re.compile(
    r'(博士|君|様|さん|ちゃん|殿下|警部|総統|団長|伯爵|署長|所長|船長|部長|社長)'
)

# Ship / vehicle naming
_VEHICLE_KEEP_RE = re.compile(
    r'号$'
)

# Known anime-specific concepts that should always be kept
# (avoids false positives from heuristic patterns)
_ANIME_WHITELIST = frozenset({
    '科学省', '万馬力', '反重力', '機械人形', '電子相撲', '電子計算',
    '電子図', '電子角', '宇宙艇', '惑星号', '馬力号', '宇宙放送',
    '幽霊製造', '風船雲', '次元装置', '念動力', '予知感覚', '神隠',
    '物質伝送', '伝送機', '誘爆装置', '電磁銃', '小型電子', '新兵器',
    '人工太陽', '人工人間', '人口人間', '人間軍', '美術品泥',
    '百万馬力', '最新型', '地球防衛', '戦闘開始', '戦闘準備',
    '攻撃開始', '攻撃準備', '地球攻撃', '地球最後', '地球連邦',
    '地球大統', '全人類', '世界最高', '省長官', '日本科学',
    '秘密研究', '国際宇宙', '宇宙博覧', '海底王国', '大帝国',
    '三銃士', '黄金如来', '火星銀行', '火星探検', '物質縮小',
})


# ═══════════════════════════════════════════════════════════════
# Core logic
# ═══════════════════════════════════════════════════════════════

def _in_jamdict(word):
    """Check if word is in JMdict (common dictionary)."""
    if not _JAMDICT_AVAILABLE or not _jam:
        return False
    try:
        result = _jam.lookup(word.strip())
        return len(result.entries) > 0
    except Exception:
        return False


def _is_likely_name(word):
    """Check if word looks like a genuine proper noun (surname, place, etc.)."""
    # Anime whitelist
    if word in _ANIME_WHITELIST:
        return True

    # Contains honorific → likely name
    if _HONORIFIC_KEEP_RE.search(word):
        return True

    # Contains common surname components
    if _NAME_KEEP_RE.search(word):
        return True

    # Place name suffix
    if _PLACE_KEEP_RE.search(word):
        return True

    # Ship/vehicle name
    if _VEHICLE_KEEP_RE.search(word):
        return True

    # Well-known company/place names that trip heuristics
    if word in {'日立', '日本亭', '日本'}:
        return True

    return False


def _get_reject_reason(word):
    """Determine why a word should be rejected. Returns reason string or None."""
    # JMdict check (most reliable)
    if _in_jamdict(word):
        return 'in JMdict (common dictionary word)'

    # Verb stem ending
    if _VERB_ENDINGS_RE.search(word):
        return f'verb stem: ends with "{word[-1:]}"'

    # Time/number fragment (but only if short AND no name indicators)
    if _TIME_NUMBER_RE.search(word) and len(word) <= 4:
        if not _NAME_KEEP_RE.search(word):
            return 'time/number fragment'

    # Modifier fragment
    if _MODIFIER_RE.match(word) and len(word) <= 4:
        return 'modifier/adverb fragment'

    # Pronoun fragment
    if _PRONOUN_RE.search(word) and len(word) <= 4:
        return 'pronoun/person fragment'

    return None


def scan_glossary(glossary_path):
    """Scan proper-nouns.md for common words that should be filtered.

    Returns:
        dict: {
            'suggestions': [(word, freq, reason), ...],   # words to add to COMMON_KANJI
            'kept': [(word, freq), ...],                  # words that look like genuine proper nouns
            'total_scanned': int,
            'jamdict_available': bool,
        }
    """
    if not os.path.exists(glossary_path):
        return {'error': f'Glossary not found: {glossary_path}'}

    with open(glossary_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Parse kanji compounds table
    sections = content.split('## 汉字复合词')
    if len(sections) < 2:
        return {'error': 'No 「汉字复合词」 section found in glossary'}

    kanji_section = sections[1].split('## 其他')[0] if '## 其他' in sections[1] else sections[1]

    words = []
    for m in re.finditer(r'\| (.+?) \| (\d+) \|', kanji_section):
        words.append((m.group(1), int(m.group(2))))

    suggestions = []
    kept = []

    for word, freq in words:
        # Already in COMMON_KANJI → skip (shouldn't happen, but safety check)
        if word in _COMMON_KANJI or word in _COMMON_KATAKANA:
            continue

        # Check if it looks like a genuine proper noun
        if _is_likely_name(word):
            kept.append((word, freq))
            continue

        # Check for common-word patterns
        reason = _get_reject_reason(word)
        if reason:
            suggestions.append((word, freq, reason))
        else:
            # No pattern matched → keep (conservative)
            kept.append((word, freq))

    return {
        'suggestions': suggestions,
        'kept': kept,
        'total_scanned': len(words),
        'jamdict_available': _JAMDICT_AVAILABLE,
    }


# ═══════════════════════════════════════════════════════════════
# Apply: edit japanese_utils.py
# ═══════════════════════════════════════════════════════════════

def _get_japanese_utils_path():
    """Find japanese_utils.py relative to this script."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(script_dir, '..', 'lib', 'japanese_utils.py')


def apply_suggestions(suggestions):
    """Add suggested words to COMMON_KANJI in japanese_utils.py.

    Appends each new word to the frozenset literal.
    Returns the number of words actually added.
    """
    utils_path = _get_japanese_utils_path()

    if not os.path.exists(utils_path):
        print(f'ERROR: japanese_utils.py not found at {utils_path}', file=sys.stderr)
        return 0

    with open(utils_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Find existing COMMON_KANJI words (from import + from file content)
    existing = set(_COMMON_KANJI) | set(_COMMON_KATAKANA)
    # Also scan file content for previously inserted words (handles same-process re-calls)
    for m in re.finditer(r"'([^']+)'", content):
        existing.add(m.group(1))

    # Filter to genuinely new words
    new_words = []
    for word, freq, reason in suggestions:
        if word not in existing:
            new_words.append((word, freq, reason))
            existing.add(word)

    if not new_words:
        print('All suggestions already in COMMON_KANJI — nothing to add.', file=sys.stderr)
        return 0

    # Build the insertion — find the last entry before the closing })
    # We insert before the closing '})' of the COMMON_KANJI frozenset
    insert_lines = []
    insert_lines.append(f'    # ── auto_clean_glossary ({len(new_words)} words) ──')
    for word, freq, reason in new_words:
        insert_lines.append(f"    '{word}',  # {reason}")

    insert_block = '\n'.join(insert_lines) + '\n'

    # Find the end of the COMMON_KANJI frozenset:
    # We look for a line that is just '})' after the COMMON_KANJI section.
    # Strategy: find the start marker, then find the matching closing '})'
    start_marker = 'COMMON_KANJI = frozenset({'
    pos = content.find(start_marker)
    if pos == -1:
        print('ERROR: Could not find COMMON_KANJI frozenset', file=sys.stderr)
        return 0

    # Count braces from the opening
    brace_count = 0
    end_pos = pos
    for i in range(pos, len(content)):
        if content[i] == '{':
            brace_count += 1
        elif content[i] == '}':
            brace_count -= 1
            if brace_count == 0:
                end_pos = i + 1  # include the closing brace
                break

    if brace_count != 0:
        print('ERROR: Could not find closing brace of COMMON_KANJI', file=sys.stderr)
        return 0

    # Find the closing '})' — it's at end_pos, content[end_pos-1] is '}'
    # We need to find the last line before the closing. Look backwards from end_pos
    # for the last non-empty, non-comment line before '})'.
    # Actually, the frozenset ends with '})' — the '}' closes the set, ')' closes frozenset.
    # We insert before the line that has just '})'.

    # Walk backwards from end_pos to find the line start of '})'
    insert_at = end_pos - 1  # right before the closing '}'
    # Walk back past whitespace and the '}'
    while insert_at > pos and content[insert_at] in '}) \t':
        insert_at -= 1
    insert_at += 1  # position right before '})'

    # But we want to insert at line granularity. Find the beginning of the line
    # containing '})':
    line_start = content.rfind('\n', 0, insert_at) + 1
    # Check that this line is essentially just '})'
    line_content = content[line_start:end_pos].strip()
    if not line_content.startswith('})'):
        print(f'WARNING: Expected \"}})\" but found \"{line_content[:20]}...\"', file=sys.stderr)
        # Try to find it
        search_start = content.rfind('\n})', 0, end_pos + 10)
        if search_start != -1:
            line_start = search_start + 1
        else:
            print('ERROR: Could not locate \"}})\" line', file=sys.stderr)
            return 0

    # Insert before the closing line
    new_content = content[:line_start] + insert_block + content[line_start:]

    with open(utils_path, 'w', encoding='utf-8') as f:
        f.write(new_content)

    print(f'Added {len(new_words)} words to COMMON_KANJI in {utils_path}', file=sys.stderr)
    return len(new_words)


# ═══════════════════════════════════════════════════════════════
# Output formatting
# ═══════════════════════════════════════════════════════════════

def print_report(result):
    """Print human-readable report."""
    if 'error' in result:
        print(f'ERROR: {result["error"]}')
        return 1

    suggestions = result['suggestions']
    kept = result['kept']
    total = result['total_scanned']

    jamdict_status = '✅ available' if result['jamdict_available'] else '⚠️  NOT available'
    print(f'Scanned {total} kanji compounds  (Jamdict: {jamdict_status})')
    print()

    if suggestions:
        print(f'⟳  Suggested REJECT ({len(suggestions)}):')
        print(f'   {"Word":<16s} {"Freq":>5s}  Reason')
        print(f'   {"-"*16} {"-"*5}  {"-"*40}')
        for word, freq, reason in suggestions:
            print(f'   {word:<16s} {freq:>5d}  {reason}')

    print()
    print(f'✅ Would KEEP: {len(kept)}')
    if kept:
        for word, freq in kept[:15]:
            print(f'   {word} ({freq})')
        if len(kept) > 15:
            print(f'   ... and {len(kept) - 15} more')

    return 0


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description='Auto-clean proper noun glossary — identify common words',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry-run
  python auto_clean_glossary.py --glossary reports/proper-nouns.md

  # Apply (with confirmation)
  python auto_clean_glossary.py --glossary reports/proper-nouns.md --apply

  # Apply without confirmation
  python auto_clean_glossary.py --glossary reports/proper-nouns.md --apply --yes
        """
    )
    parser.add_argument('--glossary', required=True,
                        help='Path to proper-nouns.md')
    parser.add_argument('--lang', default='ja', choices=['ja', 'zh'],
                        help='Target language (default: ja)')
    parser.add_argument('--apply', action='store_true',
                        help='Apply suggestions (edit japanese_utils.py)')
    parser.add_argument('--yes', '-y', action='store_true',
                        help='Skip confirmation prompt (with --apply)')
    parser.add_argument('--json', action='store_true',
                        help='Output as JSON instead of human-readable')
    args = parser.parse_args()

    if not os.path.exists(args.glossary):
        print(f'ERROR: {args.glossary} not found.', file=sys.stderr)
        sys.exit(1)

    result = scan_glossary(args.glossary)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    ret = print_report(result)
    if ret:
        sys.exit(ret)

    suggestions = result['suggestions']
    if not suggestions:
        print('\n✨ Glossary is clean — no common words found.')
        return

    if not args.apply:
        print(f'\nRun with --apply to auto-add {len(suggestions)} words to COMMON_KANJI.')
        return

    # ── Apply mode ──
    if not args.yes:
        print(f'\nAbout to add {len(suggestions)} words to COMMON_KANJI in japanese_utils.py:')
        for word, freq, reason in suggestions:
            print(f'  + {word}  ({reason})')
        response = input('\nProceed? [y/N] ')
        if response.lower() not in ('y', 'yes'):
            print('Aborted.')
            return

    n_added = apply_suggestions(suggestions)
    if n_added:
        print(f'✅ Added {n_added} words to COMMON_KANJI.')
        print(f'\nRe-run build_glossary.py to regenerate the clean glossary:')
        print(f'  python nouns/build_glossary.py --findings temp/scans/findings.json \\')
        print(f'    --output reports/proper-nouns.md --lang ja')
    else:
        print('No new words added (all already present).')


if __name__ == '__main__':
    main()
