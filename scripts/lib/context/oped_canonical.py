#!/usr/bin/env python3
"""Fill ``canonical`` in ``oped_fixer --ai-review`` candidates, reviewed by the LLM.

The existing OP/ED fixer already clusters repeated lines across episodes and
writes ``oped_ai_review.json`` with variants + a ``suggested_canonical``; it
only lacks the reviewed canonical.  This module is that missing step — it does
not re-cluster anything.

Order of decisions:

1. ``_meta.fixed_lines`` reviewed lines win when a candidate matches them
   (deterministic, no token spent);
2. the rest go to the context-review agent, which sees the series profile, the
   region and the variants;
3. a candidate whose canonical cannot be trusted stays empty → the fixer will
   skip it (``canonical`` empty = skip).

Output is a copy of the candidate JSON with ``canonical`` / ``reviewed_by``
filled; applying it is still the fixer's job (``--apply-ai-review``).
"""

from __future__ import annotations

import difflib
import json
import re

_PUNCT = re.compile(r'[\s，。、！？…—\-~〜「」『』（）()<>\.,!?\'"：:；;·]')


def normalize(text: str) -> str:
    return _PUNCT.sub('', str(text or ''))

MATCH_THRESHOLD = 0.35

FILL_PROMPT = """你在统一一部动画的 OP/ED 与固定台词（开场白、预告、标题）。
作品档案：
{profile}

下面是按「跨集重复」聚合出来的候选（同一句在不同集里被译成了多个版本）：
{items}

任务：为每条候选给出**唯一的中文规范句**（canonical），供全剧统一使用。

规则：
- 选一个已有版本，或把最完整、最通顺的版本整理成一句；
- 只在候选变体明显是同一句时统一；不确定或不构句就留空字符串；
- 保留原有专名写法（与档案人物表一致）；
- 不同句子不要合并；不要添加解释、标点风格保持与变体一致；
- 已给出的 meta 参考译文优先采用。

只输出 JSON：
{{"decisions":[{{"id":0,"canonical":"规范句","reason":"依据"}}]}}"""


def _variants(candidate: dict) -> list[str]:
    variants = candidate.get('variants') or {}
    if isinstance(variants, dict):
        rows = sorted(variants.items(), key=lambda kv: -kv[1])
        return [str(text) for text, _ in rows]
    return [str(v) for v in variants]


def match_meta(candidate: dict, meta: dict) -> str | None:
    """Return a reviewed canonical line from ``_meta.fixed_lines`` if close enough."""
    fixed = (meta or {}).get('fixed_lines') or {}
    entries = list(fixed.get('entries') or [])
    for line in fixed.get('op_narration_zh') or []:
        entries.append({'canonical_zh': line})
    variants = [normalize(v) for v in _variants(candidate)]
    best, best_score = None, 0.0
    for entry in entries:
        canonical = str(entry.get('canonical_zh') or '')
        key = normalize(canonical)
        if not key:
            continue
        for variant in variants:
            if not variant:
                continue
            score = difflib.SequenceMatcher(None, key, variant).ratio()
            if score > best_score:
                best, best_score = canonical, score
    return best if best_score >= MATCH_THRESHOLD else None


def _call(chat, prompt: str, items) -> list[dict]:
    content, error = chat([
        {'role': 'system', 'content': prompt},
        {'role': 'user', 'content': json.dumps(items, ensure_ascii=False)},
    ])
    if error or not content:
        return []
    text = str(content).strip()
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return []
    return list(data.get('decisions') or [])


def validate_canonical(canonical: str, variants: list[str]) -> tuple[bool, str]:
    canonical = str(canonical or '').strip()
    if not canonical:
        return False, 'empty'
    longest = max((len(v) for v in variants), default=0)
    if longest and len(canonical) > longest * 2 + 10:
        return False, 'canonical far longer than variants'
    if longest and len(canonical) < 2:
        return False, 'canonical too short'
    if any('\u3040' <= ch <= '\u30ff' for ch in canonical):
        return False, 'canonical still contains kana'
    return True, ''


def apply_text_keyed(cues_by_ep: dict, candidates: list) -> tuple[dict, list]:
    """Replace every cue whose normalized text matches a filled variant.

    ``oped_fixer`` applies fixes **per time bucket**, so a line that appears at
    16.3 s in one episode and 16.9 s in another can be missed (measured:
    35/95 occurrences unified).  This pass keys on text only — the candidate
    list is the same one the fixer produced, so nothing is re-clustered.

    Returns ``(new_cues_by_ep, changes)``; input is not mutated.
    """
    lookup = {}
    for candidate in candidates:
        canonical = str(candidate.get('canonical') or '').strip()
        if not canonical:
            continue
        canon_key = normalize(canonical)
        for variant in _variants(candidate):
            key = normalize(variant)
            if key and key != canon_key:
                lookup[key] = canonical

    out = {episode: [dict(c) for c in cues]
           for episode, cues in cues_by_ep.items()}
    changes = []
    for episode, cues in out.items():
        for index, cue in enumerate(cues):
            key = normalize(cue.get('text'))
            target = lookup.get(key)
            if target:
                changes.append({'episode': episode, 'cue': index + 1,
                                'before': str(cue.get('text') or ''),
                                'after': target})
                cue['text'] = target
    return out, changes


def fill_candidates(candidates: list, *, meta: dict, profile_brief: str,
                    chat=None) -> tuple[list, dict]:
    """Return (filled copy, stats).  Nothing is written to disk here."""
    filled = []
    stats = {'meta': 0, 'llm': 0, 'kept_empty': 0, 'rejected': 0}
    pending = []
    for index, candidate in enumerate(candidates):
        row = dict(candidate)
        canonical = match_meta(row, meta)
        if canonical:
            row['canonical'] = canonical
            row['reviewed_by'] = 'meta'
            stats['meta'] += 1
        else:
            pending.append((index, row))
        filled.append(row)

    if pending and chat is not None:
        payload = [{'id': index, 'region': row.get('region'),
                    'episodes': row.get('episode_count'),
                    'variants': _variants(row)[:8],
                    'suggested': row.get('suggested_canonical')}
                   for index, row in pending]
        for decision in _call(chat, FILL_PROMPT.format(
                profile=profile_brief,
                items=json.dumps(payload, ensure_ascii=False)), payload):
            try:
                index = int(decision.get('id'))
            except (TypeError, ValueError):
                continue
            if not (0 <= index < len(filled)):
                continue
            row = filled[index]
            if row.get('canonical'):
                continue
            canonical = str(decision.get('canonical') or '').strip()
            ok, reason = validate_canonical(canonical, _variants(row))
            if not ok:
                stats['rejected'] += 1
                row['review_note'] = reason
                continue
            row['canonical'] = canonical
            row['reviewed_by'] = 'llm'
            row['review_reason'] = str(decision.get('reason') or '')[:200]
            stats['llm'] += 1

    for row in filled:
        if not row.get('canonical'):
            stats['kept_empty'] += 1
    return filled, stats
