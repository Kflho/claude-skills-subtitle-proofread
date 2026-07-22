#!/usr/bin/env python3
"""Auto-clean proper noun glossary — identify common words that slipped through.

Runs after build_glossary.py.  Uses JMdict + heuristic patterns to find
non-proper-noun entries in proper-nouns.md and suggest (or apply) additions
to COMMON_KANJI or COMMON_KATAKANA.

This automates the manual "scan → classify → add to frozenset → re-run" loop
that would otherwise take 3-5 rounds of human inspection.

Now handles ALL sections uniformly:
  - 角色名（自动提取）  — katakana character names
  - 汉字复合词          — kanji compounds
  - 其他片假名术语      — other katakana terms

Usage:
  # Dry-run: show what would be added
  python auto_clean_glossary.py --glossary reports/proper-nouns.md --lang ja

  # Apply: auto-edit japanese_utils.py + regenerate glossary
  python auto_clean_glossary.py --glossary reports/proper-nouns.md --apply

  # Auto-accept all suggestions (skip confirmation prompt)
  python auto_clean_glossary.py --glossary reports/proper-nouns.md --apply --yes

How it works:
  1. Parses ALL tables from proper-nouns.md
  2. Checks each word against JMdict (in JMdict → common word → REJECT)
  3. Applies heuristic patterns:
     - Kanji: verb stems, time/number, modifier, pronoun fragments
     - Katakana: sound effects, onomatopoeia, laughter, common words, fragments
  4. Skips words matching name / place patterns
  5. Outputs suggested additions to COMMON_KANJI / COMMON_KATAKANA
  6. With --apply: edits japanese_utils.py + re-runs build_glossary.py
"""

import argparse
import json
import os
import re
import sys
from collections import OrderedDict, defaultdict

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

# Astro Boy proper nouns that heuristic patterns would falsely reject.
# Only whitelist entries that ARE genuine proper nouns AND match a
# reject pattern (verb ending, JMdict, modifier, time/number, pronoun).
# Common nouns like 万馬力/戦闘開始 belong in COMMON_KANJI, not here.
_ANIME_WHITELIST = frozenset({
    # Organization / place names that may be in JMdict
    '科学省',       # Ministry of Science — in JMdict, needs whitelist
    '科学省長',     # Minister of Science — title, may match heuristics
    '科学省庁',     # Science Ministry agency
    # Technology / event names (verb-ending kanji would trigger false reject)
    '幽霊製造',     # 造 matches verb-ending pattern
    # Proper names that pass all checks but are kept for clarity
    '電子相撲',     # Electronic Sumo (event)
    '風船雲',       # Balloon Cloud (proper name)
    '次元装置',     # Dimension Device (technology)
    '電磁銃',       # Electromagnetic Gun (technology)
    '地球連邦',     # Earth Federation (organization)
    '三銃士',       # The Three Musketeers
    '黄金如来',     # Golden Tathagata
    '火星銀行',     # Mars Bank (organization)
})

# ── Katakana-specific reject patterns ──

# Sound effects / onomatopoeia (reduplicated syllables, short bursts)
# NOTE: names ending in ー (アーサー, カーリー) are common in katakana, so we
# only flagー+stop (ッ/ツ) or ー+ン, not bare ー endings.
_KATAKANA_SOUND_RE = re.compile(
    r'^([ア-ヾ]{2})\1+ン?$|'            # reduplication: ワンワン, ワンワンワン
    r'^[ア-ヾ]{1,3}[ッっ]$|'            # short burst: パンッ, キュッ, ガッ
    r'^[ア-ヾ]{1,3}ー[ッツ]$|'          # long vowel + stop: パーッ, バーッ
    r'^[ア-ヾ]{2,3}ーン$|'              # ー + ン: ポカーン, ボカーン
    r'^[ア-ヾ]{2,3}ョン$'               # -yon: ヒョン
)

# Common katakana words that are never proper nouns
_KATAKANA_COMMON_WORDS = frozenset({
    'パパ', 'ママ', 'パパママ',
    'バイキン', 'アイスクリー', 'アイスクリーム',
    'バイバイ', 'バイバーイ', 'パパー',
    'エネルギ', 'ネルギー', 'エネルギータ',
    'プロダクショ', 'リボリュー',
})

# Katakana fragments — words that look like they're missing their first/last kana
# (often ASR artifacts where the first syllable was garbled)
_KATAKANA_FRAGMENT_RE = re.compile(
    r'^ー[ア-ヾ]'  # starts with long vowel mark: ートム (fragment of アトーム)
)

# Laughter / exclamation / animal sounds
_KATAKANA_EXCLAMATION = frozenset({
    'ハッハー', 'ハハハ', 'アハハ', 'イェーイ', 'ワーイ',
    'エーッ', 'アーッ', 'キャー', 'ウワー', 'ヤッター',
    'ハーイ',  # "はい" drawn out
    'ニャー', 'ニャーニャー',  # cat meow
    'チャー',  # shoo / charge sound
    'モー', 'モーモー',  # cow moo
    'ワンワン',  # dog bark (ワンワンワン caught by reduplication regex)
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
    """Determine why a kanji word should be rejected. Returns reason string or None."""
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


def _get_katakana_reject_reason(word):
    """Determine why a katakana word should be rejected. Returns reason string or None."""
    # JMdict check
    if _in_jamdict(word):
        return 'in JMdict (common dictionary word)'

    # Hard-coded common words
    if word in _KATAKANA_COMMON_WORDS:
        return 'common non-name word'

    # Sound effect / onomatopoeia
    if _KATAKANA_SOUND_RE.match(word):
        return 'sound effect / onomatopoeia'

    # Laughter / exclamation
    if word in _KATAKANA_EXCLAMATION:
        return 'laughter / exclamation'

    # Fragment (starts with long vowel)
    if _KATAKANA_FRAGMENT_RE.match(word):
        return 'fragment (starts with ー)'

    # Fragment check: word is substring of a common longer word
    # e.g. ネルギー is suffix of エネルギー
    _FRAGMENT_PARENTS = {
        'エネルギー': ['ネルギー', 'エネルギ'],
        'アイスクリーム': ['アイスクリー'],
    }
    for parent, frags in _FRAGMENT_PARENTS.items():
        if word in frags:
            return f'fragment of {parent}'

    return None


def _parse_glossary_section(content, section_header, stop_headers):
    """Parse a markdown table section from the glossary.

    Args:
        content: full glossary text
        section_header: '## 角色名' or '## 汉字复合词' or '## 其他片假名'
        stop_headers: list of '## ...' headers that mark the end of this section

    Returns list of (word, freq) tuples.
    """
    sections = content.split(section_header)
    if len(sections) < 2:
        return []

    body = sections[1]
    # Truncate at the next section header
    for stop in stop_headers:
        if stop in body:
            body = body.split(stop)[0]
            break

    words = []
    for m in re.finditer(r'\| (.+?) \| (\d+) \|', body):
        words.append((m.group(1), int(m.group(2))))

    return words


def scan_glossary(glossary_path):
    """Scan ALL sections of proper-nouns.md for common words that should be filtered.

    Returns:
        dict: {
            'suggestions': [(word, freq, reason, section), ...],
            'kept': [(word, freq, section), ...],
            'total_scanned': int,
            'jamdict_available': bool,
        }
    """
    if not os.path.exists(glossary_path):
        return {'error': f'Glossary not found: {glossary_path}'}

    with open(glossary_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Define sections to scan
    sections_config = [
        {
            'header': '## 角色名',
            'stop_headers': ['## 汉字复合词', '## 其他片假名', '## 使用方法'],
            'is_katakana': True,
            'name': '角色名（片假名）',
        },
        {
            'header': '## 汉字复合词',
            'stop_headers': ['## 其他片假名', '## 使用方法'],
            'is_katakana': False,
            'name': '汉字复合词',
        },
        {
            'header': '## 其他片假名',
            'stop_headers': ['## 使用方法'],
            'is_katakana': True,
            'name': '其他片假名术语',
        },
    ]

    all_suggestions = []
    all_kept = []
    total_scanned = 0

    for cfg in sections_config:
        words = _parse_glossary_section(content, cfg['header'], cfg['stop_headers'])
        if not words:
            continue

        for word, freq in words:
            total_scanned += 1

            # Already in COMMON sets → skip
            if word in _COMMON_KANJI or word in _COMMON_KATAKANA:
                continue

            # Check if it looks like a genuine proper noun
            if _is_likely_name(word):
                all_kept.append((word, freq, cfg['name']))
                continue

            # Apply language-appropriate reject patterns
            if cfg['is_katakana']:
                reason = _get_katakana_reject_reason(word)
            else:
                reason = _get_reject_reason(word)

            if reason:
                all_suggestions.append((word, freq, reason, cfg['name']))
            else:
                # No pattern matched → keep (conservative)
                all_kept.append((word, freq, cfg['name']))

    # Deduplicate across sections (same word appears in multiple sections)
    seen_words = set()
    unique_suggestions = []
    for s in all_suggestions:
        if s[0] not in seen_words:
            seen_words.add(s[0])
            unique_suggestions.append(s)

    return {
        'suggestions': unique_suggestions,
        'kept': all_kept,
        'total_scanned': total_scanned,
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
    """Add suggested words to COMMON_KANJI / COMMON_KATAKANA in japanese_utils.py.

    Katakana words go to COMMON_KATAKANA; kanji words go to COMMON_KANJI.
    Returns the number of words actually added.
    """
    utils_path = _get_japanese_utils_path()

    if not os.path.exists(utils_path):
        print(f'ERROR: japanese_utils.py not found at {utils_path}', file=sys.stderr)
        return 0

    with open(utils_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Find existing words in both frozensets
    existing = set(_COMMON_KANJI) | set(_COMMON_KATAKANA)
    for m in re.finditer(r"'([^']+)'", content):
        existing.add(m.group(1))

    # Separate katakana from kanji suggestions
    katakana_words = []
    kanji_words = []
    for s in suggestions:
        word = s[0]
        freq = s[1]
        reason = s[2]
        # s[3] is section name — used to determine type
        section = s[3] if len(s) > 3 else ''
        if word in existing:
            continue
        # Katakana check: has katakana range chars
        if bool(re.search(r'[゠-ヿ]', word)):
            katakana_words.append((word, freq, reason))
        else:
            kanji_words.append((word, freq, reason))
        existing.add(word)

    total_added = 0
    modified = content

    # ── Insert into COMMON_KATAKANA ──
    if katakana_words:
        insert_lines = []
        insert_lines.append(f'    # ── auto_clean_glossary ({len(katakana_words)} katakana words) ──')
        for word, freq, reason in katakana_words:
            insert_lines.append(f"    '{word}',  # {reason}")
        insert_block = '\n'.join(insert_lines) + '\n'

        result = _insert_into_frozenset(modified, 'COMMON_KATAKANA', insert_block)
        if result:
            modified = result
            total_added += len(katakana_words)
            print(f'Added {len(katakana_words)} words to COMMON_KATAKANA', file=sys.stderr)

    # ── Insert into COMMON_KANJI ──
    if kanji_words:
        insert_lines = []
        insert_lines.append(f'    # ── auto_clean_glossary ({len(kanji_words)} kanji words) ──')
        for word, freq, reason in kanji_words:
            insert_lines.append(f"    '{word}',  # {reason}")
        insert_block = '\n'.join(insert_lines) + '\n'

        result = _insert_into_frozenset(modified, 'COMMON_KANJI', insert_block)
        if result:
            modified = result
            total_added += len(kanji_words)
            print(f'Added {len(kanji_words)} words to COMMON_KANJI', file=sys.stderr)

    if total_added:
        with open(utils_path, 'w', encoding='utf-8') as f:
            f.write(modified)

    return total_added


def _insert_into_frozenset(content, var_name, insert_block):
    """Insert lines before the closing }}) of a frozenset variable.

    Returns modified content or None on error.
    """
    start_marker = f'{var_name} = frozenset({{'
    pos = content.find(start_marker)
    if pos == -1:
        print(f'ERROR: Could not find {var_name} frozenset', file=sys.stderr)
        return None

    # Count braces from the opening
    brace_count = 0
    end_pos = pos
    for i in range(pos, len(content)):
        if content[i] == '{':
            brace_count += 1
        elif content[i] == '}':
            brace_count -= 1
            if brace_count == 0:
                end_pos = i + 1
                break

    if brace_count != 0:
        print(f'ERROR: Could not find closing brace of {var_name}', file=sys.stderr)
        return None

    # Walk backwards to find the line start of '})'
    insert_at = end_pos - 1
    while insert_at > pos and content[insert_at] in '}) \t':
        insert_at -= 1
    insert_at += 1

    line_start = content.rfind('\n', 0, insert_at) + 1
    line_content = content[line_start:end_pos].strip()
    if not line_content.startswith('})'):
        search_start = content.rfind('\n})', 0, end_pos + 10)
        if search_start != -1:
            line_start = search_start + 1
        else:
            print(f'ERROR: Could not locate \"}})\" line for {var_name}', file=sys.stderr)
            return None

    return content[:line_start] + insert_block + content[line_start:]


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

    jamdict_status = '[OK] available' if result['jamdict_available'] else '[WARN] NOT available'
    print(f'Scanned {total} entries across all sections  (Jamdict: {jamdict_status})')
    print()

    if suggestions:
        # Group by section
        by_section = defaultdict(list)
        for word, freq, reason, section in suggestions:
            by_section[section].append((word, freq, reason))

        for section, items in by_section.items():
            print(f'[*] [{section}] Suggested REJECT ({len(items)}):')
            print(f'   {"Word":<20s} {"Freq":>5s}  Reason')
            print(f'   {"-"*20} {"-"*5}  {"-"*40}')
            for word, freq, reason in items:
                print(f'   {word:<20s} {freq:>5d}  {reason}')
            print()

    # Kept summary by section
    kept_by_section = defaultdict(list)
    for word, freq, section in kept:
        kept_by_section[section].append((word, freq))
    for section, items in kept_by_section.items():
        print(f'[OK] [{section}] Would KEEP: {len(items)}')
        for word, freq in items[:10]:
            print(f'   {word} ({freq})')
        if len(items) > 10:
            print(f'   ... and {len(items) - 10} more')
        print()

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
        print('\n[OK] Glossary is clean — no common words found.')
        return

    if not args.apply:
        print(f'\nRun with --apply to auto-add {len(suggestions)} words to COMMON_KANJI / COMMON_KATAKANA.')
        return

    # ── Apply mode ──
    if not args.yes:
        print(f'\nAbout to add {len(suggestions)} words to japanese_utils.py:')
        for s in suggestions:
            word, freq, reason = s[0], s[1], s[2]
            target = 'KATAKANA' if bool(re.search(r'[゠-ヿ]', word)) else 'KANJI'
            print(f'  + [{target}] {word}  ({reason})')
        response = input('\nProceed? [y/N] ')
        if response.lower() not in ('y', 'yes'):
            print('Aborted.')
            return

    n_added = apply_suggestions(suggestions)
    if n_added:
        print(f'[OK] Added {n_added} words to COMMON_KANJI.')
        print(f'\nRe-run build_glossary.py to regenerate the clean glossary:')
        print(f'  python nouns/build_glossary.py --findings temp/scans/findings.json \\')
        print(f'    --output reports/proper-nouns.md --lang ja')
    else:
        print('No new words added (all already present).')


if __name__ == '__main__':
    main()
