#!/usr/bin/env python3
"""Series glossary: flat ja→zh map + reviewed variant table + normalization.

Why this module exists
----------------------
Batch translation is independent per batch, so the same name can come out as
several Chinese forms inside one episode (measured: EP051 2/15 canonical, 7
distinct forms, 2 batches with mixed forms).  Injecting the glossary fixes most
of it (15/15 with the glossary), and this module closes the rest
deterministically:

- ``load_glossary``   flat ``{ja_form: zh_canonical}`` — the file every tool
  already understands (``translate_srt --mappings``, ``find_suspect_nouns``);
- ``load_meta``       reviewed metadata, including ``aliases``
  (``{zh_canonical: [observed wrong forms]}``);
- ``build_variant_map`` turns aliases into a replacement table, dropping unsafe
  entries (a variant that is a substring of its canonical, or shorter than 2
  chars, would corrupt unrelated words);
- ``Normalizer``       longest-match replacement; never touches text that only
  contains the canonical form;
- ``discover_variants`` finds *candidates* from aligned JA/ZH pairs: a cue whose
  Japanese contains a known form but whose Chinese lacks the canonical name.
  Discovery never edits anything — a human/agent promotes a candidate into
  ``_meta.aliases``.

The separation matters: discovery is noisy, decisions are reviewed, and the
reviewed table is the only thing that can rewrite subtitles.
"""

from __future__ import annotations

import json
import os
import re

MIN_VARIANT_CHARS = 2
_CJK_WORD = re.compile(r'[\u3400-\u9fff]{2,4}')


def _has_latin(text: str) -> bool:
    return any('a' <= ch.lower() <= 'z' for ch in str(text or ''))


def load_glossary(path: str) -> dict:
    """Load the flat ja→zh glossary that the rest of the toolchain uses."""
    with open(path, 'r', encoding='utf-8') as handle:
        data = json.load(handle)
    return {str(k): str(v) for k, v in data.items() if not str(k).startswith('_')}


def load_meta(path: str) -> dict:
    """Load reviewed metadata (aliases, needs_review, fixed lines, style)."""
    if not path or not os.path.isfile(path):
        return {}
    with open(path, 'r', encoding='utf-8') as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def _canonicals(glossary: dict) -> set:
    return {str(v) for v in glossary.values() if str(v).strip()}


def build_variant_map(glossary: dict, meta: dict) -> dict:
    """``{canonical_zh: [variant, ...]}`` — only safe, reviewed variants.

    Dropped silently (and reported by ``explain_variant_map``):
    - a variant equal to the canonical (no-op);
    - a variant shorter than ``MIN_VARIANT_CHARS`` (single chars corrupt words);
    - a variant that is a substring of *its own* canonical or of another
      canonical (e.g. ``市`` inside ``市之``), because replacing it would hit
      legitimate text.
    """
    canon = _canonicals(glossary)
    aliases = (meta.get('aliases') or {}) if isinstance(meta, dict) else {}
    out = {}
    for canonical, variants in aliases.items():
        canonical = str(canonical)
        if not canonical:
            continue
        keep = []
        for variant in variants or []:
            variant = str(variant).strip()
            if not variant or variant == canonical:
                continue
            if len(variant) < MIN_VARIANT_CHARS:
                continue
            if variant in canonical:
                continue
            if any(variant in other and other != canonical for other in canon):
                continue
            keep.append(variant)
        if keep:
            # longest first so "萨布扬" wins over "萨布"
            out[canonical] = sorted(set(keep), key=len, reverse=True)
    return out


def build_normalizer(glossary: dict, meta: dict) -> "Normalizer":
    """Normalizer with the reviewed table and the meta blocklist."""
    blocked = (meta.get('blocked_phrases') or []) if isinstance(meta, dict) else []
    return Normalizer(build_variant_map(glossary, meta), blocked=blocked)


def explain_variant_map(glossary: dict, meta: dict) -> dict:
    """Report what ``build_variant_map`` kept/dropped, for review."""
    kept = build_variant_map(glossary, meta)
    canon = _canonicals(glossary)
    dropped = []
    for canonical, variants in (meta.get('aliases') or {}).items():
        for variant in variants or []:
            variant = str(variant).strip()
            if variant in kept.get(str(canonical), []):
                continue
            if not variant or variant == canonical:
                reason = 'equals canonical'
            elif len(variant) < MIN_VARIANT_CHARS:
                reason = 'shorter than 2 chars'
            elif variant in str(canonical):
                reason = 'substring of canonical'
            else:
                reason = 'substring of another canonical'
            dropped.append({'canonical': canonical, 'variant': variant,
                            'reason': reason})
    return {'kept': {k: list(v) for k, v in kept.items()}, 'dropped': dropped}


class Normalizer:
    """Longest-match variant → canonical replacement.

    Only the reviewed table is applied; the canonical form is never rewritten.
    ``blocked`` phrases suppress any replacement that would overlap them
    (e.g. an uncertain reading like 「萨布托米多」 must not become
    「佐武托米多」).  ``normalize`` returns the new text plus the hits, so
    callers can log exactly what changed (dry-run == apply minus the write).
    """

    def __init__(self, variant_map: dict, blocked: list | None = None):
        pairs = []
        for canonical, variants in (variant_map or {}).items():
            for variant in variants:
                pairs.append((variant, canonical))
        pairs.sort(key=lambda p: len(p[0]), reverse=True)
        self._pairs = pairs
        self._pattern = None
        self._latin_pattern = None
        self._blocked = [str(b) for b in (blocked or []) if str(b).strip()]
        # Canonical names themselves are protected spans: a variant that only
        # overlaps part of a canonical (绿姑娘 inside 小绿姑娘) must not fire.
        self._canonicals = sorted({canonical for _, canonical in pairs},
                                  key=len, reverse=True)
        latin = {v: c for v, c in pairs if _has_latin(v)}
        cjk = {v: c for v, c in pairs if not _has_latin(v)}
        self._latin = latin
        self._latin_lower = {v.lower(): c for v, c in latin.items()}
        self._cjk = cjk
        if cjk:
            alternation = '|'.join(re.escape(v) for v, _ in cjk.items())
            self._pattern = re.compile(alternation)
        if latin:
            alternation = '|'.join(re.escape(v) for v, _ in latin.items())
            self._latin_pattern = re.compile(
                r'(?<![A-Za-z])(?:' + alternation + r')(?![A-Za-z])',
                re.IGNORECASE)

    @property
    def has_rules(self) -> bool:
        return bool(self._pairs)

    def _blocked_spans(self, text: str) -> list[tuple[int, int]]:
        spans = []
        for phrase in self._blocked:
            start = text.find(phrase)
            while start >= 0:
                spans.append((start, start + len(phrase)))
                start = text.find(phrase, start + 1)
        return spans

    def _canonical_spans(self, text: str) -> list[tuple[int, int]]:
        spans = []
        for canonical in self._canonicals:
            start = text.find(canonical)
            while start >= 0:
                spans.append((start, start + len(canonical)))
                start = text.find(canonical, start + 1)
        return spans

    def _matches(self, text: str):
        found = []
        if self._pattern is not None:
            found.extend(self._pattern.finditer(text))
        if self._latin_pattern is not None:
            found.extend(self._latin_pattern.finditer(text))
        found.sort(key=lambda m: (m.start(), -(m.end() - m.start())))
        return found

    def normalize(self, text: str) -> tuple[str, list[dict]]:
        if not text or self._pattern is None and self._latin_pattern is None:
            return text, []
        spans = self._blocked_spans(text) + self._canonical_spans(text)
        hits = []
        result = []
        pos = 0
        for match in self._matches(text):
            if match.start() < pos:
                continue        # nested match already covered
            if any(match.start() < end and match.end() > start
                   for start, end in spans):
                continue
            result.append(text[pos:match.start()])
            variant = match.group(0)
            if _has_latin(variant):
                canonical = (self._latin.get(variant)
                             or self._latin_lower.get(variant.lower())
                             or variant)
            else:
                canonical = self._cjk.get(variant, variant)
            result.append(canonical)
            hits.append({'variant': variant, 'canonical': canonical,
                         'offset': match.start()})
            pos = match.end()
        result.append(text[pos:])
        return ''.join(result), hits


def normalize_cues(cues: list, normalizer: Normalizer) -> tuple[list, list]:
    """Return (new_cues, changes); input cues are not mutated."""
    out, changes = [], []
    for cue in cues:
        text = str(cue.get('text') or '')
        new_text, hits = normalizer.normalize(text)
        if hits:
            updated = dict(cue)
            updated['text'] = new_text
            out.append(updated)
            changes.append({'start': cue.get('start', ''), 'before': text,
                            'after': new_text, 'hits': hits})
        else:
            out.append(dict(cue))
    return out, changes


def _cjk_substrings(text: str, sizes=(2, 3, 4)) -> set:
    """All CJK-only substrings of the given lengths in one cue."""
    out = set()
    text = str(text or '')
    for size in sizes:
        for i in range(len(text) - size + 1):
            token = text[i:i + size]
            if all('\u3400' <= ch <= '\u9fff' for ch in token):
                out.add(token)
    return out


def discover_variants(ja_cues_by_ep: dict, zh_cues_by_ep: dict,
                      glossary: dict, *, min_count: int = 2,
                      max_candidates: int = 50) -> list[dict]:
    """Candidate alternate renderings — evidence only, never applied.

    Discriminative scoring, not a bare n-gram count: for each term, compare the
    Chinese substrings that appear in cues where the Japanese **has** that term
    (``positive``) against cues where it does not (``negative``).  A real
    alternate rendering shows up repeatedly on the positive side and rarely on
    the negative side; a filler word like 「来了」 appears on both.

    A candidate is kept when ``positive >= min_count`` and
    ``positive > negative``.  Reviewers promote survivors into
    ``_meta.aliases``; nothing is rewritten here.
    """
    canon = _canonicals(glossary)
    by_canonical = {}
    for ja_form, canonical in glossary.items():
        by_canonical.setdefault(canonical, set()).add(ja_form)

    # Build the full cue-level corpus first so negatives are meaningful.
    corpus = []
    for ep, ja_cues in ja_cues_by_ep.items():
        zh_cues = zh_cues_by_ep.get(ep) or []
        for i, cue in enumerate(ja_cues):
            if i >= len(zh_cues):
                break
            corpus.append((ep, i,
                           str(cue.get('text') or ''),
                           str(zh_cues[i].get('text') or '')))

    positives = {c: {} for c in by_canonical}
    negatives = {c: {} for c in by_canonical}
    where = {c: {} for c in by_canonical}
    for ep, index, ja_text, zh_text in corpus:
        hits = [c for c, forms in by_canonical.items()
                if any(f in ja_text for f in forms)]
        tokens = _cjk_substrings(zh_text)
        for canonical in by_canonical:
            if canonical in hits:
                # already correct → no candidate, and it is not a "missing" cue
                if canonical in zh_text:
                    continue
                bucket = positives
            else:
                bucket = negatives
            for token in tokens:
                if token in canon or token in str(canonical):
                    continue
                bucket[canonical][token] = bucket[canonical].get(token, 0) + 1
                if bucket is positives:
                    if len(where[canonical].get(token, [])) < 3:
                        where[canonical].setdefault(token, []).append(
                            {'episode': ep, 'cue': index + 1,
                             'ja': ja_text[:40], 'zh': zh_text[:40]})

    scored = []
    for canonical in by_canonical:
        pos, neg = positives[canonical], negatives[canonical]
        for token, count in pos.items():
            negative = neg.get(token, 0)
            if count >= min_count and count > negative:
                scored.append({
                    'candidate': token, 'canonical': canonical,
                    'positive': count, 'negative': negative,
                    'examples': where[canonical].get(token, [])[:3],
                })
    scored.sort(key=lambda row: (-row['positive'], -len(row['candidate'])))
    return scored[:max_candidates]
