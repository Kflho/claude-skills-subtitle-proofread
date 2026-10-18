#!/usr/bin/env python3
"""Translation QA — objective checks before calling a translation done.

Pure stdlib.  These checks are the evidence layer for the translation
pipeline (docs/translation-architecture.md): coverage, Japanese residue,
echo/untranslated cues, glossary adherence, line length and CPS.
"""

from __future__ import annotations

import re

KANA_RE = re.compile(r'[\u3040-\u30ff\u31f0-\u31ff]')
NUMBERING_RE = re.compile(r'^\s*(?:\d+[.、)]|[-*])\s*')


def count_kana(text: str) -> int:
    return len(KANA_RE.findall(text or ''))


def clean_text(text: str) -> str:
    """Visible characters for length/CPS: drop whitespace and line breaks."""
    return re.sub(r'\s+', '', text or '')


def is_untranslated(source: str, target: str) -> bool:
    """True when the target is the source, or a numbered echo of it."""
    src = (source or '').strip()
    tgt = (target or '').strip()
    if not tgt:
        return True
    if tgt == src:
        return True
    stripped = NUMBERING_RE.sub('', tgt).strip()
    return stripped == src


def cue_violations(cue: dict, *, max_chars: int = 20,
                   max_cps: float = 12.0) -> list[dict]:
    """Return line-length / CPS violations for one output cue."""
    text = str(cue.get('text', '') or '')
    out = []
    lines = text.splitlines() or ['']
    longest = max((len(clean_text(line)) for line in lines), default=0)
    if longest > max_chars:
        out.append({'rule': 'max_chars', 'value': longest, 'limit': max_chars})
    try:
        start = float(cue.get('start_s', 0.0) or 0.0)
        end = float(cue.get('end_s', start) or start)
    except (TypeError, ValueError):
        start, end = 0.0, 0.0
    duration = max(0.0, end - start)
    chars = len(clean_text(text))
    if duration > 0:
        cps = chars / duration
        if cps > max_cps:
            out.append({'rule': 'max_cps', 'value': round(cps, 2),
                        'limit': max_cps})
    return out


def glossary_misses(source: str, target: str, glossary: dict | None) -> list[str]:
    """Japanese terms present in source but whose Chinese form is absent."""
    if not glossary:
        return []
    misses = []
    src = source or ''
    tgt = target or ''
    for ja, zh in glossary.items():
        ja = str(ja or '').strip()
        zh = str(zh or '').strip()
        if not ja or not zh or ja not in src:
            continue
        if zh not in tgt:
            misses.append(ja)
    return misses


def qa_report(source_cues: list[dict], output_cues: list[dict], *,
              glossary: dict | None = None,
              max_chars: int = 20, max_cps: float = 12.0,
              allow_kana: bool = False) -> dict:
    """Per-cue + aggregate QA report.

    ``source_cues`` / ``output_cues`` are aligned by index (same order).
    Missing output cues count as coverage failures.
    """
    total = len(source_cues)
    items = []
    counts = {
        'total': total,
        'output': len(output_cues),
        'missing': 0,
        'japanese_residual': 0,
        'echo': 0,
        'glossary_miss': 0,
        'max_chars': 0,
        'max_cps': 0,
    }
    for i, src in enumerate(source_cues):
        if i >= len(output_cues):
            counts['missing'] += 1
            items.append({'index': i, 'reasons': ['missing']})
            continue
        out = output_cues[i]
        src_text = str(src.get('text', '') or '')
        tgt_text = str(out.get('text', '') or '')
        reasons = []
        if not tgt_text.strip():
            reasons.append('empty')
        if not allow_kana and count_kana(tgt_text):
            reasons.append('japanese_residual')
            counts['japanese_residual'] += 1
        if is_untranslated(src_text, tgt_text):
            reasons.append('echo')
            counts['echo'] += 1
        misses = glossary_misses(src_text, tgt_text, glossary)
        if misses:
            reasons.append('glossary_miss')
            counts['glossary_miss'] += 1
        violations = cue_violations(out, max_chars=max_chars, max_cps=max_cps)
        for v in violations:
            reasons.append(v['rule'])
            counts[v['rule']] += 1
        if reasons:
            items.append({
                'index': i,
                'reasons': reasons,
                'source': src_text,
                'target': tgt_text,
                'glossary_misses': misses,
                'violations': violations,
            })

    counts['violations'] = len(items)
    counts['coverage'] = (round(len(output_cues) / total, 4)
                          if total else 0.0)
    return {'summary': counts, 'items': items}
