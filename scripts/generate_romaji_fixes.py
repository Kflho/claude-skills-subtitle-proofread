#!/usr/bin/env python3
"""Generate fixes.json from bilingual detection findings for AI-transcribed SRT files.

Analyzes pure_source (romaji) and mixed (romaji+Japanese) findings,
generates appropriate replace_global and replace_text fixes.
"""

import json
import re
import sys
import os
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ═══════════════════════════════════════════════════════════════
# Romaji → Hiragana mapping (common AI transcription errors)
# ═══════════════════════════════════════════════════════════════

# Single-syllable romaji → hiragana (standalone cue replacements)
ROMAJI_TO_KANA = {
    # Vowels
    'a': 'あ', 'i': 'い', 'u': 'う', 'e': 'え', 'o': 'お',
    # K-row
    'ka': 'か', 'ki': 'き', 'ku': 'く', 'ke': 'け', 'ko': 'こ',
    # S-row
    'sa': 'さ', 'shi': 'し', 'su': 'す', 'se': 'せ', 'so': 'そ',
    # T-row
    'ta': 'た', 'chi': 'ち', 'tsu': 'つ', 'te': 'て', 'to': 'と',
    # N-row
    'na': 'な', 'ni': 'に', 'nu': 'ぬ', 'ne': 'ね', 'no': 'の',
    # H-row
    'ha': 'は', 'hi': 'ひ', 'fu': 'ふ', 'he': 'へ', 'ho': 'ほ',
    # M-row
    'ma': 'ま', 'mi': 'み', 'mu': 'む', 'me': 'め', 'mo': 'も',
    # Y-row
    'ya': 'や', 'yu': 'ゆ', 'yo': 'よ',
    # R-row
    'ra': 'ら', 'ri': 'り', 'ru': 'る', 're': 'れ', 'ro': 'ろ',
    # W-row
    'wa': 'わ', 'wo': 'を', 'n': 'ん',
    # Voiced variants
    'ga': 'が', 'gi': 'ぎ', 'gu': 'ぐ', 'ge': 'げ', 'go': 'ご',
    'za': 'ざ', 'ji': 'じ', 'zu': 'ず', 'ze': 'ぜ', 'zo': 'ぞ',
    'da': 'だ', 'de': 'で', 'do': 'ど',
    'ba': 'ば', 'bi': 'び', 'bu': 'ぶ', 'be': 'べ', 'bo': 'ぼ',
    'pa': 'ぱ', 'pi': 'ぴ', 'pu': 'ぷ', 'pe': 'ぺ', 'po': 'ぽ',
    # Common compound/diphthong patterns
    'dai': 'だい', 'dare': 'だれ', 'dame': 'だめ',
    'kara': 'から', 'koko': 'ここ', 'kore': 'これ',
    'sore': 'それ', 'soko': 'そこ',
    'demo': 'でも', 'doko': 'どこ',
    'nani': 'なに', 'naze': 'なぜ',
    'mada': 'まだ', 'mata': 'また',
    'sugu': 'すぐ', 'sore': 'それ',
    'hoka': 'ほか', 'toki': 'とき',
    'mono': 'もの', 'koto': 'こと',
    'naka': 'なか', 'mae': 'まえ',
    'ato': 'あと', 'ushiro': 'うしろ',
    'hayaku': 'はやく', 'sukoshi': 'すこし',
    # Greetings/exclamations
    'ohayou': 'おはよう', 'konnichiwa': 'こんにちは',
    'arigatou': 'ありがとう', 'gomen': 'ごめん',
    'sumimasen': 'すみません', 'sayounara': 'さようなら',
}

# Common AI transcription errors → correct katakana
ROMAJI_TO_KATAKANA = {
    'atom': 'アトム',       # Astro Boy!
    'atomu': 'アトム',
    'wan': 'ワン',         # Dog bark
}

# Words that are likely correct English (leave as-is)
ENGLISH_OK = {
    'ok', 'okay', 'yeah', 'hey', 'oh', 'wow', 'bye', 'hi',
    'yes', 'no', 'go', 'stop', 'hello', 'good',
}

# Words that are definitely AI noise (should be deleted)
AI_NOISE = {
    'wh', 'th', 'dj', 'w',  # Consonant fragments
}


def is_romaji_only(text: str) -> bool:
    """Check if text contains only Latin letters and spaces."""
    return bool(re.fullmatch(r'[a-zA-Z\s\-\']+', text.strip()))


def generate_fixes(bilingual_findings_path: str) -> list[dict]:
    """Generate fixes.json from bilingual detection findings."""
    with open(bilingual_findings_path, 'r', encoding='utf-8') as f:
        findings = json.load(f)

    fixes = []
    seen_global = set()
    seen_replace_text = set()

    # ═══════════════════════════════════════════════════════════
    # Priority: Character names / important terms
    # ═══════════════════════════════════════════════════════════

    # Astro Boy's name variations - must fix
    astro_patterns = [
        (r'\batom\b', 'アトム', 'Atom → アトム (鉄腕アトム)'),
        (r'\batomu\b', 'アトム', 'Atomu → アトム'),
        (r'\bA[ts][to][mo]\b', 'アトム', 'Atom (case) → アトム'),
    ]
    for pattern, replacement, note in astro_patterns:
        fixes.append({
            'action': 'replace_global_regex',
            'pattern': pattern,
            'replacement': replacement,
            'note': note,
        })

    # Ochanomizu → お茶の水博士
    fixes.append({
        'action': 'replace_global_regex',
        'pattern': r'\b[Oo]chanomizu\b',
        'replacement': 'お茶の水',
        'note': 'Ochanomizu → お茶の水',
    })

    # ═══════════════════════════════════════════════════════════
    # Global romaji → kana replacements (safe patterns)
    # ═══════════════════════════════════════════════════════════

    # Collect frequency of pure_source romaji texts
    pure_texts = Counter()
    pure_files = {}
    for f in findings:
        if f['type'] == 'pure_source':
            text = f['visible'].strip()
            if text and len(text) <= 20:
                pure_texts[text] += 1
                if text not in pure_files:
                    pure_files[text] = []
                pure_files[text].append(f['file'])

    # Global replacements for high-frequency patterns
    for text, count in pure_texts.most_common(100):
        if count < 10:
            break

        lower = text.lower().strip()

        # Skip: English words that are OK
        if lower in ENGLISH_OK:
            continue

        # Skip: Single characters (too risky globally)
        if len(lower) <= 1:
            continue

        # Skip: AI noise fragments
        if lower in AI_NOISE:
            continue

        # Known romaji → kana
        if lower in ROMAJI_TO_KANA:
            kana = ROMAJI_TO_KANA[lower]
            key = f'global_romaji_{lower}'
            if key not in seen_global and lower not in ('me', 'ni', 're', 'ra', 'ri', 'ru', 'ro', 'wa', 'wo', 'ha', 'hi', 'fu', 'he', 'ho', 'no', 'ka', 'ki', 'ku', 'ke', 'ko', 'sa', 'su', 'se', 'so', 'ta', 'te', 'to', 'na', 'nu', 'ne', 'ma', 'mi', 'mu', 'mo', 'ya', 'yu', 'yo', 'a', 'i', 'u', 'e', 'o'):
                # Only apply to multi-char romaji with higher confidence
                if len(lower) >= 2:
                    fixes.append({
                        'action': 'replace_global_regex',
                        'pattern': rf'\b{re.escape(lower)}\b',
                        'replacement': kana,
                        'note': f'Romaji→Kana: {lower}→{kana} ({count}x)',
                    })
                    seen_global.add(key)

        # Known romaji → katakana
        if lower in ROMAJI_TO_KATAKANA:
            katakana = ROMAJI_TO_KATAKANA[lower]
            key = f'global_katakana_{lower}'
            if key not in seen_global:
                fixes.append({
                    'action': 'replace_global_regex',
                    'pattern': rf'\b{re.escape(lower)}\b',
                    'replacement': katakana,
                    'note': f'Romaji→カタカナ: {lower}→{katakana} ({count}x)',
                })
                seen_global.add(key)

    # ═══════════════════════════════════════════════════════════
    # Per-file: replace pure_source cues (entire cue is romaji)
    # ═══════════════════════════════════════════════════════════

    pure_source_replacements = 0
    for f in findings:
        if f['type'] != 'pure_source':
            continue
        text = f['visible'].strip()
        lower = text.lower().strip()

        # Only process short texts
        if len(lower) > 20:
            continue

        # Skip English OK words
        if lower in ENGLISH_OK:
            continue

        # AI noise → delete
        if lower in AI_NOISE or (len(lower) <= 2 and lower not in ROMAJI_TO_KANA):
            # Mark for deletion
            key = (f['file'], f['line'], 'delete')
            if key not in seen_replace_text:
                seen_replace_text.add(key)
                fixes.append({
                    'action': 'delete_line',
                    'file': f['file'],
                    'line': f['line'],
                    'note': f'AIノイズ削除: \"{text}\"',
                })
                pure_source_replacements += 1
            continue

        # Known romaji → kana for entire cue
        if lower in ROMAJI_TO_KANA:
            kana = ROMAJI_TO_KANA[lower]
            key = (f['file'], f['line'], lower)
            if key not in seen_replace_text:
                seen_replace_text.add(key)
                fixes.append({
                    'action': 'replace_text',
                    'file': f['file'],
                    'line': f['line'],
                    'replacement': kana,
                    'note': f'Romaji→Kana: \"{text}\"→\"{kana}\"',
                })
                pure_source_replacements += 1

    # ═══════════════════════════════════════════════════════════
    # Per-file: fix mixed cues (romaji within Japanese text)
    # ═══════════════════════════════════════════════════════════
    mixed_replacements = 0
    for f in findings:
        if f['type'] != 'mixed':
            continue
        text = f['visible'].strip()

        # Only process if there's clear romaji to fix
        # Extract romaji words from the text
        romaji_words = re.findall(r'[a-zA-Z]{2,}', text)
        if not romaji_words:
            continue

        # Try to fix each romaji word
        new_text = text
        changed = False
        for word in romaji_words:
            lower = word.lower()
            if lower in ENGLISH_OK:
                continue
            if lower in ROMAJI_TO_KANA:
                new_text = new_text.replace(word, ROMAJI_TO_KANA[lower])
                changed = True
            elif lower in ROMAJI_TO_KATAKANA:
                new_text = new_text.replace(word, ROMAJI_TO_KATAKANA[lower])
                changed = True

        if changed and new_text != text:
            key = (f['file'], f['line'])
            if key not in seen_replace_text:
                seen_replace_text.add(key)
                fixes.append({
                    'action': 'replace_text',
                    'file': f['file'],
                    'line': f['line'],
                    'replacement': new_text,
                    'note': f'Romaji修正: \"{text[:40]}...\"→\"{new_text[:40]}...\"',
                })
                mixed_replacements += 1

    return fixes


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python generate_romaji_fixes.py <bilingual_findings.json>")
        sys.exit(1)

    fixes = generate_fixes(sys.argv[1])
    json.dump(fixes, sys.stdout, ensure_ascii=False, indent=2)
