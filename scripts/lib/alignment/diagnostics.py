#!/usr/bin/env python3
"""Diagnostic signals and confidence tiers for cue alignment.

The mapper decides *what* the audio says; this module decides *how much to
trust it*.  Qwen3-ASR exposes no logprob, so confidence is a multi-signal
vote instead of a model score (tech-route-2026 §6.5):

    similarity + boundary error + repeat score + language profile
    + internal pause (split hint)

Pure functions over ``CueAlignment`` — safe to unit-test without audio.

Usage:
    from lib.alignment.diagnostics import confidence_for, action_for
    al.confidence = confidence_for(al, cfg)
"""

from __future__ import annotations

from collections import Counter

from lib.alignment.base import (
    ACTION_DELETE_CANDIDATE, ACTION_FIX_TIME, ACTION_KEEP,
    ACTION_REPLACE_TEXT, ACTION_REVIEW_AI, ACTION_REVIEW_HUMAN,
    ACTION_SPLIT_CANDIDATE, AlignmentConfig, CueAlignment,
    STATUS_EMPTY_TEXT, STATUS_EXTRA_TEXT, STATUS_GARBLED, STATUS_MATCH,
    STATUS_MISSING_ASR, STATUS_NO_ASR, STATUS_NO_SPEECH,
)


# ═══════════════════════════════════════════════════════════════
# Signals
# ═══════════════════════════════════════════════════════════════

def speech_coverage(timeline, start: float, end: float,
                    window: tuple[int, int] | None = None) -> float:
    """Fraction of [start, end] covered by ASR characters (union of spans).

    This is not VAD: it answers "did the ASR timeline put any speech here",
    which is enough to separate ``no_speech`` from ``missing_asr`` in P2.
    P3's FireRedVAD will refine it, not replace it.
    """
    duration = end - start
    if duration <= 0 or not len(timeline):
        return 0.0

    lo, hi = window if window else (0, len(timeline))
    lo = max(0, lo)
    hi = min(len(timeline), hi)
    covered = 0.0
    cur_start = cur_end = None

    for i in range(lo, hi):
        s = max(timeline.starts[i], start)
        e = min(timeline.ends[i], end)
        if e <= s:
            continue
        if cur_end is None:
            cur_start, cur_end = s, e
        elif s <= cur_end + 1e-6:
            cur_end = max(cur_end, e)
        else:
            covered += cur_end - cur_start
            cur_start, cur_end = s, e
    if cur_end is not None:
        covered += cur_end - cur_start
    return min(1.0, covered / duration)


def repeat_score(text: str, n: int = 5) -> float:
    """Repeated n-gram ratio — the cheap hallucination/loop detector."""
    if len(text) < n * 2:
        return 0.0
    grams = Counter(text[i:i + n] for i in range(len(text) - n + 1))
    top = max(grams.values())
    if top <= 1:
        return 0.0
    return min(1.0, (top - 1) * n / len(text))


def lang_profile(text: str) -> dict:
    """Script mix of a cue (already normalised or raw — both work)."""
    counts = {'ja': 0, 'kana': 0, 'latin': 0, 'cyrillic': 0,
              'digit': 0, 'other': 0}
    for ch in text or '':
        cp = ord(ch)
        if 0x3040 <= cp <= 0x30FF:          # hiragana + katakana
            counts['kana'] += 1
            counts['ja'] += 1
        elif 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF \
                or ch in '々〆':
            counts['ja'] += 1               # kanji
        elif ch.isascii() and ch.isalpha():
            counts['latin'] += 1
        elif 0x0400 <= cp <= 0x04FF:
            counts['cyrillic'] += 1
        elif ch.isdigit():
            counts['digit'] += 1
        elif not ch.isspace():
            counts['other'] += 1
    total = max(1, counts['ja'] + counts['latin'] + counts['cyrillic']
                + counts['digit'] + counts['other'])
    return {
        'lang_score': counts['ja'] / total,
        'kana_ratio': counts['kana'] / max(1, counts['ja']),
        'latin_ratio': counts['latin'] / total,
        'cyrillic_ratio': counts['cyrillic'] / total,
    }


def internal_pause(timeline, char_start: int, char_end: int) -> float:
    """Longest silence between consecutive matched characters."""
    if char_end - char_start < 2:
        return 0.0
    worst = 0.0
    prev_end = timeline.ends[char_start]
    for i in range(char_start + 1, min(char_end, len(timeline))):
        gap = timeline.starts[i] - prev_end
        if gap > worst:
            worst = gap
        prev_end = max(prev_end, timeline.ends[i])
    return worst


# ═══════════════════════════════════════════════════════════════
# Confidence + action
# ═══════════════════════════════════════════════════════════════

def confidence_for(al: CueAlignment, cfg: AlignmentConfig) -> str:
    """high / medium / low — the multi-signal vote from §6.5."""
    signals = al.signals or {}
    repeat = float(signals.get('repeat_score', 0.0))
    lang = float(signals.get('lang_score', 1.0))
    boundary = al.max_boundary_error

    if al.status == STATUS_MATCH:
        # 1–2 character cues match half the script by accident (あ, の, っ);
        # never let them become "high confidence, leave untouched".
        short_cue = al.cue_chars <= 2 or al.matched_chars <= 2
        if (al.similarity >= cfg.match_similarity
                and boundary <= cfg.clean_boundary
                and repeat <= cfg.max_repeat_score
                and lang >= 0.5
                and not short_cue):
            return 'high'
        if (al.similarity >= cfg.garbled_similarity
                and boundary <= cfg.max_time_shift
                and repeat <= cfg.max_repeat_score):
            return 'medium'
        return 'low'

    if al.status == STATUS_GARBLED:
        if (al.similarity >= 0.6
                and lang >= 0.5
                and repeat <= cfg.max_repeat_score
                and bool(al.asr_text)):
            return 'medium'
        return 'low'

    if al.status == STATUS_NO_SPEECH:
        # timing evidence is strong, but the audio says nothing: just no
        # automatic deletion ever, so "medium" only feeds the review list.
        return 'medium'
    if al.status == STATUS_NO_ASR:
        return 'low'
    if al.status == STATUS_EMPTY_TEXT:
        return 'low'
    return 'low'


def action_for(al: CueAlignment, cfg: AlignmentConfig) -> str:
    """Map status + confidence onto the repair planner's vocabulary."""
    boundary = al.max_boundary_error
    signals = al.signals or {}
    pause = float(signals.get('internal_pause_s', 0.0))
    duration = float(signals.get('cue_duration', al.cue_duration))
    lines = int(signals.get('cue_lines', 1))

    if al.status == STATUS_MATCH:
        if boundary <= cfg.clean_boundary:
            return ACTION_KEEP
        if boundary > cfg.max_time_shift:
            return ACTION_REVIEW_AI
        return ACTION_FIX_TIME

    if al.status == STATUS_GARBLED:
        if pause >= cfg.split_pause_seconds and (
                duration >= cfg.long_cue_seconds or lines > 2):
            return ACTION_SPLIT_CANDIDATE
        repeat = float(signals.get('repeat_score', 0.0))
        lang = float(signals.get('lang_score', 0.0))
        if (al.similarity < 0.6 and lang >= 0.5
                and repeat <= cfg.max_repeat_score
                and bool(al.asr_text)):
            return ACTION_REPLACE_TEXT
        return ACTION_REVIEW_AI

    if al.status == STATUS_EXTRA_TEXT:
        return ACTION_REVIEW_AI
    if al.status == STATUS_MISSING_ASR:
        return ACTION_REVIEW_AI
    if al.status == STATUS_NO_SPEECH:
        return ACTION_DELETE_CANDIDATE
    if al.status == STATUS_EMPTY_TEXT:
        return ACTION_KEEP
    if al.status == STATUS_NO_ASR:
        return ACTION_REVIEW_HUMAN
    return ACTION_REVIEW_AI


def annotate(al: CueAlignment, cfg: AlignmentConfig) -> CueAlignment:
    """Fill ``confidence`` and ``action`` in place (mapper convenience)."""
    al.confidence = confidence_for(al, cfg)
    al.action = action_for(al, cfg)
    return al


# ═══════════════════════════════════════════════════════════════
# Batch statistics — P2 acceptance metrics
# ═══════════════════════════════════════════════════════════════

def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * pct
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    frac = pos - low
    return ordered[low] * (1 - frac) + ordered[high] * frac


def boundary_stats(alignments: list[CueAlignment]) -> dict:
    """Boundary error distribution over matched cues (start/end separately).

    Acceptance targets come from tech-route-2026 §8.3: P50 ≤100 ms and
    P90 ≤300 ms against hand-checked boundaries.  This function measures
    the *mapper's* error (aligned vs cue), so a clean subtitle should show
    ≈0 — real hand-marked ground truth is the external eval's job.
    """
    starts = [abs(a.start_error) for a in alignments
              if a.status == STATUS_MATCH and a.start_error is not None]
    ends = [abs(a.end_error) for a in alignments
            if a.status == STATUS_MATCH and a.end_error is not None]
    both = starts + ends

    def _pack(values: list[float]) -> dict:
        if not values:
            return {'n': 0, 'mae_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0,
                    'hit_100': 0.0, 'hit_200': 0.0, 'hit_500': 0.0}
        return {
            'n': len(values),
            'mae_ms': round(1000 * sum(values) / len(values), 1),
            'p50_ms': round(1000 * _percentile(values, 0.5), 1),
            'p90_ms': round(1000 * _percentile(values, 0.9), 1),
            'hit_100': round(sum(1 for v in values if v <= 0.1) / len(values), 3),
            'hit_200': round(sum(1 for v in values if v <= 0.2) / len(values), 3),
            'hit_500': round(sum(1 for v in values if v <= 0.5) / len(values), 3),
        }

    return {'start': _pack(starts), 'end': _pack(ends), 'all': _pack(both)}


def summarize(alignments: list[CueAlignment]) -> dict:
    """Status/action counts, clean ratio and boundary stats."""
    total = len(alignments)
    status = Counter(a.status for a in alignments)
    actions = Counter(a.action for a in alignments)
    clean = sum(1 for a in alignments if a.is_clean)
    return {
        'cues': total,
        'clean': clean,
        'clean_ratio': round(clean / total, 3) if total else 0.0,
        'status': dict(status),
        'actions': dict(actions),
        'boundary': boundary_stats(alignments),
    }
