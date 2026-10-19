#!/usr/bin/env python3
"""Translation QA — objective checks before calling a translation done.

Pure stdlib.  These checks are the evidence layer for the translation
pipeline (docs/translation-architecture.md): coverage, Japanese residue,
echo/untranslated cues, glossary adherence, line length and CPS.
"""

from __future__ import annotations

import re
from collections import Counter

KANA_RE = re.compile(r'[\u3040-\u30ff\u31f0-\u31ff]')
NUMBERING_RE = re.compile(r'^\s*(?:\d+[.、)]|[-*])\s*')


def count_kana(text: str) -> int:
    return len(KANA_RE.findall(text or ''))


def clean_text(text: str) -> str:
    """Visible characters for length/CPS: drop whitespace and line breaks."""
    return re.sub(r'\s+', '', text or '')


def is_untranslated(source: str, target: str) -> bool:
    """True when the target is the source, or a numbered echo of it.

    A kanji-only line can be a legitimate proper noun (武之内空 / 泉光子郎),
    so exact equality is only an echo when the source is actually Japanese
    (contains kana) or Latin script.  Numbered echoes still count.
    """
    src = (source or '').strip()
    tgt = (target or '').strip()
    if not tgt:
        return True
    stripped = NUMBERING_RE.sub('', tgt).strip()
    equal = stripped == src
    if not equal:
        return False
    if NUMBERING_RE.match(tgt):
        return True
    return bool(KANA_RE.search(src) or re.search(r'[A-Za-z]', src))


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
    """Japanese terms present in source but whose Chinese form is absent.

    Longest term wins: 高石タケル→高石岳 must not also trigger the shorter
    タケル→阿岳 check.  Occurrences of the short term outside the longer
    span are still checked.
    """
    if not glossary:
        return []
    src = source or ''
    tgt = target or ''
    terms = []
    for ja, zh in glossary.items():
        ja = str(ja or '').strip()
        zh = str(zh or '').strip()
        if ja and zh and ja in src:
            terms.append((ja, zh))
    terms.sort(key=lambda pair: len(pair[0]), reverse=True)
    covered: list[tuple[int, int]] = []
    misses = []
    for ja, zh in terms:
        start = 0
        active = False
        while True:
            pos = src.find(ja, start)
            if pos < 0:
                break
            end = pos + len(ja)
            if not any(a <= pos and end <= b for a, b in covered):
                active = True
                covered.append((pos, end))
            start = pos + 1
        if active and zh not in tgt:
            misses.append(ja)
    return misses


def _char_ngrams(text: str, n: int) -> list[str]:
    text = clean_text(text)
    return [text[i:i + n] for i in range(max(0, len(text) - n + 1))]


def chrf_score(hyp: str, ref: str, *, max_n: int = 6,
               beta: float = 2.0) -> float:
    """Character n-gram F-score (chrF-like), 0–100.

    Guardrail only: the existing Chinese subtitles are not a gold standard,
    so this is used to detect obvious drift, not to tune prompts.
    """
    hyp = clean_text(hyp)
    ref = clean_text(ref)
    if not hyp and not ref:
        return 100.0
    if not hyp or not ref:
        return 0.0
    scores = []
    b2 = beta * beta
    for n in range(1, max_n + 1):
        h = _char_ngrams(hyp, n)
        r = _char_ngrams(ref, n)
        if not h or not r:
            continue
        hc, rc = Counter(h), Counter(r)
        overlap = sum((hc & rc).values())
        p = overlap / len(h)
        rec = overlap / len(r)
        if p + rec == 0:
            scores.append(0.0)
        else:
            scores.append((1 + b2) * p * rec / (b2 * p + rec))
    return round(100.0 * sum(scores) / len(scores), 2) if scores else 0.0


def timing_mismatches(source_cues: list[dict], output_cues: list[dict],
                      *, tolerance: float = 0.05) -> list[int]:
    """Indexes whose output start/end drifted from the source cue."""
    bad = []
    for i, src in enumerate(source_cues):
        if i >= len(output_cues):
            break
        out = output_cues[i]
        try:
            ss = float(src.get('start_s', 0.0) or 0.0)
            se = float(src.get('end_s', ss) or ss)
            os_ = float(out.get('start_s', 0.0) or 0.0)
            oe = float(out.get('end_s', os_) or os_)
        except (TypeError, ValueError):
            continue
        if abs(ss - os_) > tolerance or abs(se - oe) > tolerance:
            bad.append(i)
    return bad


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
        'timing': 0,
    }
    timing_bad = set(timing_mismatches(source_cues, output_cues))
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
        if i in timing_bad:
            reasons.append('timing')
            counts['timing'] += 1
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
