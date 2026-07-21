#!/usr/bin/env python3
"""Auto-classify proper noun candidates — deterministic pre-filter before AI review.

Uses Jamdict (JMdict + JMnedict) + heuristic rules to classify candidates as:
  ACCEPT  — clearly a proper noun → add to table
  REJECT  — clearly NOT a proper noun → discard
  NEEDS_AI — ambiguous → send to AI review (Layer 3.5)

Designed to reduce AI review token consumption by 60-80%.

Usage:
  python auto_classify.py --candidates candidates.json --lang ja --output classified.json
  python auto_classify.py --text "アトム" --context "...アトムという..." --lang ja

Dependencies: pip install jamdict jamdict-data
"""

import argparse
import json
import os
import re
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_SCRIPT_DIR)
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from lib.japanese_utils import COMMON_KATAKANA as _JA_COMMON_WORDS, NON_WORD_RE

# ── Try to import Jamdict (optional, graceful fallback) ──
_JAMDICT_AVAILABLE = False
_jam = None
try:
    from jamdict import Jamdict
    _jam = Jamdict()
    _JAMDICT_AVAILABLE = True
except ImportError:
    pass
except Exception:
    pass


# ═══════════════════════════════════════════════════════════════
# Heuristic rules
# ═══════════════════════════════════════════════════════════════

# Patterns indicating the candidate is embedded in an introduction
_INTRO_PATTERNS = re.compile(
    r'(って|という|と言う|と呼ぶ|って言う|といいます|って呼ばれ|とは|と呼ばれ)'
)

# Patterns indicating a name + honorific
_HONORIFIC_PATTERNS = re.compile(
    r'(さん|くん|ちゃん|様|殿|博士|警部|殿下|先生|総統|団長|伯爵|署長|所長|船長|部長|社長)$'
)


def _in_jamdict_common(text):
    """Check if text matches a JMdict common word entry."""
    if not _JAMDICT_AVAILABLE or not _jam:
        return False
    try:
        result = _jam.lookup(text.strip())
        return len(result.entries) > 0
    except Exception:
        return False


def _in_jamdict_names(text):
    """Check if text matches a JMnedict proper name entry."""
    if not _JAMDICT_AVAILABLE or not _jam:
        return False
    try:
        result = _jam.lookup(text.strip())
        return len(result.names) > 0
    except Exception:
        return False


def classify_candidate(candidate_text, context_cue='', episode_count=1, lang='ja'):
    """Classify a proper noun candidate.

    Args:
        candidate_text: the candidate word/phrase
        context_cue: the surrounding cue text (for pattern matching)
        episode_count: how many episodes this candidate appears in

    Returns:
        dict: {'verdict': 'ACCEPT'|'REJECT'|'NEEDS_AI', 'reason': str}
    """
    text = candidate_text.strip()
    if not text:
        return {'verdict': 'REJECT', 'reason': 'empty'}

    # ── Automatic REJECT ──

    # Non-word patterns (dashes, breathing sounds, repetition)
    if NON_WORD_RE.match(text):
        return {'verdict': 'REJECT', 'reason': 'non-word pattern (onomatopoeia/filler)'}

    # Pure hiragana — proper nouns are almost never pure hiragana in Japanese
    if re.fullmatch(r'[ぁ-ゟ]+', text):
        return {'verdict': 'REJECT', 'reason': 'pure hiragana (likely verb/adjective fragment)'}

    # Single character + single episode → likely ASR fragment
    if len(text) == 1 and episode_count == 1:
        return {'verdict': 'REJECT', 'reason': 'single char, single episode (ASR fragment)'}

    # Latin-only → not a Japanese proper noun
    if re.fullmatch(r'[a-zA-Z\s]+', text):
        return {'verdict': 'REJECT', 'reason': 'latin-only (not JP proper noun)'}

    # ── Jamdict check (if available) ──

    # If it's a known common word → REJECT
    if _in_jamdict_common(text):
        # But also check if it could be both (e.g., はな = flower/nose AND a name)
        if not _in_jamdict_names(text):
            return {'verdict': 'REJECT',
                    'reason': 'in JMdict (common word), not in JMnedict'}

    # If in proper names dictionary → ACCEPT
    if _in_jamdict_names(text):
        return {'verdict': 'ACCEPT', 'reason': 'in JMnedict (proper name dictionary)'}

    # ── Automatic ACCEPT ──

    # In known common katakana words list → REJECT
    if text in _JA_COMMON_WORDS:
        return {'verdict': 'REJECT', 'reason': 'known common katakana word'}

    # Appears with honorific suffix in context → likely a name
    if context_cue and _HONORIFIC_PATTERNS.search(text):
        return {'verdict': 'ACCEPT',
                'reason': f'matches honorific pattern: ...{text}...'}

    # Appears in self-introduction context
    if context_cue and re.search(
        re.escape(text) + r'(って|という|と言う|と呼ぶ|って言う|といいます)',
        context_cue
    ):
        return {'verdict': 'ACCEPT', 'reason': 'appears in intro pattern'}

    # Katakana + multi-episode → high probability proper noun
    if re.search(r'[゠-ヿ]', text) and episode_count >= 2:
        return {'verdict': 'ACCEPT',
                'reason': f'katakana + {episode_count} episodes'}

    # Kanji name pattern (2-4 kanji, multi-episode)
    if re.fullmatch(r'[一-鿿]{2,4}', text) and episode_count >= 2:
        return {'verdict': 'ACCEPT',
                'reason': f'kanji name + {episode_count} episodes'}

    # ── Remaining → NEEDS_AI ──
    return {'verdict': 'NEEDS_AI', 'reason': 'ambiguous — requires language judgment'}


def classify_batch(candidates, lang='ja'):
    """Classify a batch of candidates.

    Args:
        candidates: list of {'candidate': str, 'count': int, 'context': str, ...}
        lang: target language

    Returns:
        dict: {'accepted': [...], 'rejected': [...], 'needs_ai': [...]}
    """
    results = {'accepted': [], 'rejected': [], 'needs_ai': []}

    for cand in candidates:
        text = cand.get('candidate', cand.get('text', ''))
        context = cand.get('context', '')
        count = cand.get('count', 1)

        verdict = classify_candidate(text, context, count, lang)

        entry = {**cand, 'verdict': verdict['verdict'], 'reason': verdict['reason']}

        if verdict['verdict'] == 'ACCEPT':
            results['accepted'].append(entry)
        elif verdict['verdict'] == 'REJECT':
            results['rejected'].append(entry)
        else:
            results['needs_ai'].append(entry)

    return results


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Auto-classify proper noun candidates')
    parser.add_argument('--candidates', help='JSON file with candidates')
    parser.add_argument('--text', help='Single candidate text')
    parser.add_argument('--context', default='', help='Context cue text')
    parser.add_argument('--lang', default='ja', choices=['ja', 'zh'])
    parser.add_argument('--output', '-o', help='Output JSON file')
    args = parser.parse_args()

    if args.text:
        result = classify_candidate(args.text, args.context, lang=args.lang)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif args.candidates:
        with open(args.candidates, 'r', encoding='utf-8') as f:
            candidates = json.load(f)

        if not isinstance(candidates, list):
            candidates = [candidates]

        results = classify_batch(candidates, lang=args.lang)

        print(f'Accepted: {len(results["accepted"])}')
        print(f'Rejected: {len(results["rejected"])}')
        print(f'Needs AI: {len(results["needs_ai"])}')

        if args.output:
            os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)
            with open(args.output, 'w', encoding='utf-8') as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            print(f'→ {args.output}')
    else:
        parser.print_help()
