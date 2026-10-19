#!/usr/bin/env python3
"""Reading-level ASR name repair (e.g. 「サブト位置」 → 「佐武と市」).

Why this layer exists
---------------------
ASR transcribes names phonetically; a homophone wins.  「サブトイチ」 becomes
「サブト位置」 because 位置 reads イチ, and the translation then renders the
kanji literally (「世间暗处」).  Fixing the *translation* cannot recover the
name — the name has to be repaired in the ASR text, before translation.

What it does
------------
- tokenise with janome and take each token's katakana reading (falling back to
  the surface for katakana words, which janome leaves as ``*``);
- concatenate windows of noun tokens (1–3) and compare the reading with the
  reviewed reading table from ``_meta.readings``;
- exact reading match, or one-mora substitution for long/kanji-bearing spans,
  replaces the span with the canonical Japanese form (佐武 / 市 / 佐武と市…);
- every replacement is logged with the reading and the edit distance.

Deliberate limits
-----------------
- the reading table is review-built: janome reads 佐武 as サタケ and 市 as シ,
  so its readings cannot be trusted for names;
- short katakana near-misses (<5 mora, no kanji) are *not* rewritten — that is
  where loanwords live (サプリ/サブレ…), and a wrong rewrite is worse than a
  residual error;
- nothing here touches timing or the translation; the guard in
  ``eval/text_gate.py`` still applies afterwards.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

MIN_EXACT_LEN = 2
MIN_NEAR_LEN = 5

_SKIP_POS = ('助詞', '助動詞', '動詞', '形容詞', '副詞', '接続詞', '連体詞',
             '記号', '感動詞')


def _to_katakana(text: str) -> str:
    out = []
    for ch in str(text or ''):
        code = ord(ch)
        if 0x3041 <= code <= 0x3096:      # hiragana → katakana
            out.append(chr(code + 0x60))
        else:
            out.append(ch)
    return ''.join(out)


def _distance(a: str, b: str) -> int:
    """Levenshtein on mora (characters); small strings only."""
    if a == b:
        return 0
    if not a or not b:
        return max(len(a), len(b))
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


@dataclass
class NameEntry:
    canonical_ja: str
    canonical_zh: str
    readings: list[str] = field(default_factory=list)


def load_name_entries(glossary: dict, meta: dict) -> list[NameEntry]:
    """Build the inventory from the flat glossary + ``_meta.readings``.

    ``readings`` maps a Japanese surface to its kana reading(s):
    ``{"佐武": ["サブ"], "市": ["イチ"], "サブトイチ": ["サブトイチ"]}``.
    The canonical Japanese surface (kanji preferred) comes from
    ``_meta.canonical_ja`` when present, else the key itself.
    """
    readings = (meta.get('readings') or {}) if isinstance(meta, dict) else {}
    canonical_ja = (meta.get('canonical_ja') or {}) if isinstance(meta, dict) else {}
    entries = []
    seen = set()
    for ja_form, zh in glossary.items():
        surface = str(canonical_ja.get(ja_form) or ja_form)
        reading_list = readings.get(ja_form) or readings.get(surface) or []
        norm = [_to_katakana(r) for r in reading_list if str(r).strip()]
        if not norm:
            # a katakana form is its own reading; kanji needs an entry
            if all(0x30A1 <= ord(ch) <= 0x30FA or ch == 'ー' for ch in ja_form):
                norm = [_to_katakana(ja_form)]
        if not norm:
            continue
        key = (surface, zh, tuple(norm))
        if key in seen:
            continue
        seen.add(key)
        entries.append(NameEntry(canonical_ja=surface, canonical_zh=str(zh),
                                 readings=norm))
    return entries


def _reading_of(token) -> str:
    reading = getattr(token, 'reading', '*')
    if reading and reading != '*':
        return _to_katakana(reading)
    return _to_katakana(token.surface)


def _is_nounish(token) -> bool:
    pos = getattr(token, 'part_of_speech', '')
    return not any(pos.startswith(p) for p in _SKIP_POS)


def _has_kanji(text: str) -> bool:
    return any('\u3400' <= ch <= '\u9fff' for ch in str(text or ''))


def _has_katakana(text: str) -> bool:
    return any('\u30a1' <= ch <= '\u30fa' or ch == 'ー' for ch in str(text or ''))


def propose_repairs(text: str, entries: list[NameEntry], tokenizer=None,
                    allow_surfaces=frozenset(), near_miss: bool = False):
    """Return repair proposals *without* applying them.

    Same matching rules as before, but the caller can send the proposals to the
    context-review agent first.  A match is only a *hypothesis*: even an exact
    reading can be a common word (位置/イチ, サブ in a loanword), so the pipeline
    treats proposals as candidates, never as final text.
    """
    text = str(text or '')
    if not text or not entries:
        return []
    allow = {str(s) for s in (allow_surfaces or ())}
    if tokenizer is None:
        from janome.tokenizer import Tokenizer
        tokenizer = Tokenizer()
    tokens = list(tokenizer.tokenize(text))
    spans = []          # (char_start, char_end, surface, token, reading)
    cursor = 0
    for token in tokens:
        surface = token.surface
        start = text.find(surface, cursor)
        if start < 0:
            start = cursor
        end = start + len(surface)
        spans.append((start, end, surface, token, _reading_of(token)))
        cursor = end

    index = {}
    for entry in entries:
        for reading in entry.readings:
            index.setdefault(reading, entry)

    proposals = []
    used = set()
    for i in range(len(spans)):
        for width in (3, 2, 1):
            j = i + width
            if j > len(spans):
                continue
            window = spans[i:j]
            if any(idx in used for idx in range(i, j)):
                continue
            if not all(_is_nounish(s[3]) for s in window):
                continue
            reading = ''.join(s[4] for s in window)
            surface = ''.join(s[2] for s in window)
            if len(reading) < MIN_EXACT_LEN:
                continue
            entry = index.get(reading)
            distance = 0
            if entry is not None:
                if not _has_katakana(surface) and surface not in allow:
                    continue
            if entry is None:
                if not near_miss:
                    continue
                if not _has_katakana(surface):
                    continue
                if not (_has_kanji(surface) or len(reading) >= MIN_NEAR_LEN):
                    continue
                for candidate_reading, candidate in index.items():
                    if abs(len(candidate_reading) - len(reading)) > 1:
                        continue
                    if _distance(reading, candidate_reading) != 1:
                        continue
                    entry, distance = candidate, 1
                    break
            if entry is None:
                continue
            if surface == entry.canonical_ja:
                continue
            start, end = window[0][0], window[-1][1]
            proposals.append({'start': start, 'end': end, 'from': surface,
                              'to': entry.canonical_ja, 'reading': reading,
                              'distance': distance,
                              'context': text})
            used.update(range(i, j))
            break
    return sorted(proposals, key=lambda p: p['start'])


def apply_repairs(text: str, repairs: list[dict]) -> str:
    """Apply approved proposals to one line (idempotent for the same set)."""
    if not repairs:
        return text
    out, pos = [], 0
    for hit in sorted(repairs, key=lambda h: h['start']):
        out.append(text[pos:hit['start']])
        out.append(hit['to'])
        pos = hit['end']
    out.append(text[pos:])
    return ''.join(out)


def fix_text(text: str, entries: list[NameEntry], tokenizer=None,
             allow_surfaces=frozenset(), near_miss: bool = False):
    """Backwards-compatible helper: propose + apply (no AI in the loop)."""
    text = str(text or '')
    proposals = propose_repairs(text, entries, tokenizer=tokenizer,
                               allow_surfaces=allow_surfaces,
                               near_miss=near_miss)
    return apply_repairs(text, proposals), proposals


def fix_cues(cues: list, entries: list[NameEntry], tokenizer=None,
             allow_surfaces=frozenset(), near_miss: bool = False):
    """Apply ``fix_text`` per cue; returns (new_cues, changes) without mutation."""
    out, changes = [], []
    for cue in cues:
        text = str(cue.get('text') or '')
        new_text, hits = fix_text(text, entries, tokenizer=tokenizer,
                                  allow_surfaces=allow_surfaces,
                                  near_miss=near_miss)
        if hits:
            updated = dict(cue)
            updated['text'] = new_text
            out.append(updated)
            changes.append({'start': cue.get('start', ''), 'before': text,
                            'after': new_text, 'hits': hits})
        else:
            out.append(dict(cue))
    return out, changes


def load_meta(path: str) -> dict:
    if not path:
        return {}
    with open(path, 'r', encoding='utf-8') as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}
