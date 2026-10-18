#!/usr/bin/env python3
"""Cue ↔ ASR timeline mapper (P2 core algorithm).

Given subtitle cues and an ``ASRResult`` with timestamps, decide for every
cue whether the audio agrees, where its text actually lives in the ASR
timeline, and how far the cue boundaries drift.

Algorithm (tech-route-2026 §6.4, implemented without any O(n²) DP):

1. build a character timeline from ASR tokens/segments (lib.alignment.timeline);
2. time coarse anchor — take a window of ASR characters around the cue,
   clamped by a monotonic cursor so cues can never steal each other's chars;
3. character-level matching inside the window with ``difflib.SequenceMatcher``
   (``autojunk=False``; Japanese chars are far too repetitive for the
   autojunk heuristic);
4. keep the best *cluster* of matching blocks as the cue's audio span, so a
   stray matched character in the next sentence cannot stretch the span;
5. derive status/similarity/confidence and hand signals to diagnostics.

Everything is stdlib and works for any engine that can produce tokens or
segments — Whisper (segment-level) and Qwen3 (token-level) both go through
the same path.

Usage:
    from lib.alignment.cue_mapper import map_cues, map_report
    alignments = map_cues(cues, asr_result)
"""

from __future__ import annotations

import bisect
from difflib import SequenceMatcher

from lib.alignment.base import (
    AlignmentConfig, AlignmentReport, CueAlignment,
    STATUS_EMPTY_TEXT, STATUS_EXTRA_TEXT, STATUS_GARBLED, STATUS_MATCH,
    STATUS_MISSING_ASR, STATUS_NO_ASR, STATUS_NO_SPEECH,
    normalize_text,
)
from lib.alignment.diagnostics import (
    annotate, internal_pause, lang_profile, repeat_score, speech_coverage,
)
from lib.alignment.timeline import CharTimeline, build_char_timeline


# ═══════════════════════════════════════════════════════════════
# Window selection
# ═══════════════════════════════════════════════════════════════

def _select_window(timeline: CharTimeline, cue_start: float, cue_end: float,
                   cursor: int, cfg: AlignmentConfig) -> tuple[int, int]:
    """Pick the ASR character window [lo, hi) for one cue.

    The window is bounded by *time* (±search seconds around the cue), not
    by a character count: expanding "until we have N chars" would reach
    minutes ahead in sparse audio and happily match a repeated line there.
    Within that bound the cursor guarantees monotonic assignment.
    """
    n = len(timeline)
    if n == 0:
        return (0, 0)

    search = max(0.0, cfg.search_seconds)
    lo = bisect.bisect_left(timeline.starts, cue_start - search)
    hi = bisect.bisect_right(timeline.ends, cue_end + search)

    lo = max(lo, cursor)
    lo = min(lo, n)
    hi = min(max(hi, lo), n)

    if hi - lo > cfg.max_window_chars:
        mid = (cue_start + cue_end) / 2
        pivot = bisect.bisect_left(timeline.starts, mid)
        lo = max(cursor, pivot - cfg.max_window_chars // 4)
        lo = max(0, min(lo, n - cfg.max_window_chars))
        hi = min(n, lo + cfg.max_window_chars)

    return (lo, max(lo, hi))


# ═══════════════════════════════════════════════════════════════
# Block clustering
# ═══════════════════════════════════════════════════════════════

def _best_cluster(blocks, max_gap: int, *, timeline=None, lo: int = 0,
                  cue_mid: float | None = None) -> tuple[int, int, int]:
    """Return (cue_start, window_start, window_end) of the best cluster.

    A cluster is a run of matching blocks with small gaps in *both* strings.
    Scattered single-character matches (の, は, っ) are a classic false
    signal; picking the densest run keeps the span honest.

    Among near-equal clusters, the one closest in time to the cue wins.
    Without that tie-break a long cue can match a repeated line seconds
    away (OP lyrics, catchphrases) and report a 7 s boundary error.
    """
    clusters: list[list] = []
    current = [blocks[0]]
    for block in blocks[1:]:
        prev = current[-1]
        gap_a = block.a - (prev.a + prev.size)
        gap_b = block.b - (prev.b + prev.size)
        if gap_a <= max_gap and gap_b <= max_gap:
            current.append(block)
        else:
            clusters.append(current)
            current = [block]
    clusters.append(current)

    scored = []
    for cluster in clusters:
        matched = sum(b.size for b in cluster)
        win_start = cluster[0].b
        win_end = cluster[-1].b + cluster[-1].size
        distance = 0.0
        if timeline is not None and cue_mid is not None and win_end > win_start:
            start = timeline.starts[lo + win_start]
            end = timeline.ends[min(len(timeline) - 1, lo + win_end - 1)]
            distance = abs((start + end) / 2 - cue_mid)
        scored.append((matched, distance, cluster))

    best_matched = max(item[0] for item in scored)
    candidates = [item for item in scored if item[0] >= best_matched - 1]
    chosen = min(candidates, key=lambda item: (item[1], -item[0]))
    cluster = chosen[2]
    return (cluster[0].a, cluster[0].b,
            cluster[-1].b + cluster[-1].size)


def _matched_window_indices(blocks) -> set[int]:
    indices: set[int] = set()
    for block in blocks:
        indices.update(range(block.b, block.b + block.size))
    return indices


def _exact_occurrences(haystack: str, needle: str) -> list[int]:
    """All positions where ``needle`` occurs verbatim (cheap, no DP)."""
    if not needle:
        return []
    positions = []
    start = haystack.find(needle)
    while start != -1:
        positions.append(start)
        start = haystack.find(needle, start + 1)
    return positions


# ═══════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════

def map_cues(cues, source, *, config: AlignmentConfig | None = None,
             language: str = 'ja') -> list[CueAlignment]:
    """Align every cue against an ASR result or a ready-made timeline.

    Cues are processed in *time* order (real ASS files are not always
    sorted), then returned in the caller's original order.  The monotonic
    cursor applies to the time order, which is what the ASR timeline is.
    """
    cfg = config or AlignmentConfig()
    if isinstance(source, CharTimeline):
        timeline = source
    else:
        timeline = build_char_timeline(source)

    order = sorted(
        range(len(cues)),
        key=lambda i: (float(cues[i].get('start_s', 0.0) or 0.0), i),
    )
    results: dict[int, CueAlignment] = {}
    cursor = 0
    max_end = float('-inf')

    for index in order:
        cue_start = float(cues[index].get('start_s', 0.0) or 0.0)
        cue_end = float(cues[index].get('end_s', cue_start) or cue_start)
        # Overlapping cues (signs, simultaneous speakers) legitimately refer
        # to the same audio; the monotonic cursor must not starve the second
        # one.  Everything else is still cursor-clamped.
        overlaps_previous = cue_start < max_end - 1e-3
        alignment = _map_one(index, cues[index], timeline,
                             0 if overlaps_previous else cursor, cfg,
                             language=language)
        results[index] = alignment
        if alignment.char_span is not None:
            cursor = max(cursor, alignment.char_span[1])
        max_end = max(max_end, cue_end)

    return [results[i] for i in range(len(cues))]


def _map_one(index: int, cue: dict, timeline: CharTimeline, cursor: int,
             cfg: AlignmentConfig, *, language: str) -> CueAlignment:
    cue_start = float(cue.get('start_s', 0.0) or 0.0)
    cue_end = float(cue.get('end_s', cue_start) or cue_start)
    if cue_end < cue_start:
        cue_end = cue_start
    raw_text = str(cue.get('text', '') or '')
    cue_norm = normalize_text(raw_text)

    al = CueAlignment(
        cue_index=index,
        status=STATUS_NO_ASR,
        cue_start=cue_start,
        cue_end=cue_end,
        cue_chars=len(cue_norm),
    )
    al.signals.update({
        'cue_duration': round(cue_end - cue_start, 3),
        'cue_lines': raw_text.count('\n') + 1 if raw_text else 0,
        **{k: round(v, 4) for k, v in lang_profile(cue_norm).items()},
        'repeat_score': round(repeat_score(cue_norm, cfg.repeat_window), 4),
    })

    if not cue_norm:
        al.status = STATUS_EMPTY_TEXT
        al.reasons.append('cue text is empty after normalisation')
        return annotate(al, cfg)

    if len(timeline) == 0:
        al.status = STATUS_NO_ASR
        al.reasons.append('ASR result has no timestamps')
        return annotate(al, cfg)

    lo, hi = _select_window(timeline, cue_start, cue_end, cursor, cfg)
    window_text = timeline.slice_text(lo, hi)
    coverage = speech_coverage(timeline, cue_start, cue_end)
    al.signals['speech_coverage'] = round(coverage, 3)
    al.signals['window_chars'] = hi - lo

    if not window_text:
        al.status = (STATUS_NO_SPEECH if coverage < cfg.min_speech_coverage
                     else STATUS_MISSING_ASR)
        al.reasons.append('no ASR characters in the cue window')
        return annotate(al, cfg)

    cue_mid = (cue_start + cue_end) / 2
    exact_positions = _exact_occurrences(window_text, cue_norm)

    if exact_positions:
        # Duplicate lines (OP lyrics, catchphrases) are the norm in anime.
        # difflib returns one monotone alignment, which always lands on the
        # *earliest* occurrence; pick the occurrence nearest in time instead.
        def distance(position: int) -> float:
            span_start = timeline.starts[lo + position]
            span_end = timeline.ends[min(len(timeline) - 1,
                                         lo + position + len(cue_norm) - 1)]
            return abs((span_start + span_end) / 2 - cue_mid)

        best_position = min(exact_positions, key=distance)
        matched = len(cue_norm)
        blocks = []
        char_start = lo + best_position
        char_end = char_start + len(cue_norm)
        matched_indices = set(range(char_start, char_end))
    else:
        matcher = SequenceMatcher(None, cue_norm, window_text, autojunk=False)
        blocks = [b for b in matcher.get_matching_blocks() if b.size > 0]
        matched = sum(b.size for b in blocks)
        if matched == 0:
            al.status = (STATUS_NO_SPEECH
                         if coverage < cfg.min_speech_coverage
                         else STATUS_MISSING_ASR)
            al.reasons.append(
                'no character match; speech_coverage='
                f'{coverage:.2f}')
            return annotate(al, cfg)

        _cue_core_start, win_core_start, win_core_end = _best_cluster(
            blocks, cfg.block_gap_chars, timeline=timeline, lo=lo,
            cue_mid=cue_mid)
        char_start = lo + win_core_start
        char_end = lo + win_core_end
        matched_indices = {lo + i for i in _matched_window_indices(blocks)}

    similarity = matched / len(cue_norm)
    al.similarity = similarity
    al.matched_chars = matched
    al.signals['asr_coverage'] = round(matched / len(window_text), 3)

    al.start, al.end = timeline.time_span(char_start, char_end)
    al.char_span = (char_start, char_end)
    al.asr_span = timeline.unit_span(char_start, char_end)
    if al.asr_span is not None:
        al.asr_text = timeline.raw_text(*al.asr_span)

    pause = internal_pause(timeline, char_start, char_end)
    unmatched_speech = 0.0
    for i in range(lo, hi):
        if i in matched_indices:
            continue
        char_s = max(timeline.starts[i], cue_start)
        char_e = min(timeline.ends[i], cue_end)
        if char_e > char_s:
            unmatched_speech += char_e - char_s

    al.signals.update({
        'boundary_error_start': round(al.start - cue_start, 3),
        'boundary_error_end': round(cue_end - al.end, 3),
        'max_boundary_error': round(al.max_boundary_error, 3),
        'overflow': round(max(0.0, al.end - cue_end)
                          + max(0.0, cue_start - al.start), 3),
        'unmatched_speech_s': round(unmatched_speech, 3),
        'internal_pause_s': round(pause, 3),
    })

    if similarity >= cfg.match_similarity:
        al.status = STATUS_MATCH
    elif similarity >= cfg.garbled_similarity:
        al.status = STATUS_GARBLED
    else:
        if blocks:
            tail_unmatched = len(cue_norm) - max(b.a + b.size for b in blocks)
            asr_tail = len(window_text) - max(b.b + b.size for b in blocks)
        else:
            tail_unmatched = len(cue_norm)
            asr_tail = len(window_text)
        if tail_unmatched >= max(4, int(0.4 * len(cue_norm))) and asr_tail <= 1:
            al.status = STATUS_EXTRA_TEXT
            al.reasons.append('cue text continues past the end of ASR speech')
        else:
            al.status = STATUS_GARBLED
            al.reasons.append(f'low similarity ({similarity:.2f})')

    if al.max_boundary_error > cfg.clean_boundary:
        al.reasons.append(
            f'boundary drift {1000 * al.max_boundary_error:.0f} ms')
    return annotate(al, cfg)


def map_report(cues, result, *, config: AlignmentConfig | None = None,
               language: str = 'ja') -> AlignmentReport:
    """Convenience wrapper: alignments + engine metadata + warnings."""
    cfg = config or AlignmentConfig()
    timeline = build_char_timeline(result)
    alignments = map_cues(cues, timeline, config=cfg, language=language)
    report = AlignmentReport(
        alignments=alignments,
        engine=str(getattr(result, 'engine', '') or ''),
        model=str(getattr(result, 'model', '') or ''),
        audio_duration=float(getattr(result, 'audio_duration', 0.0) or 0.0),
    )
    if timeline.source == 'segments':
        report.warnings.append(
            'ASR has no tokens; alignment used segment-level interpolation '
            '(boundary precision is limited)')
    elif timeline.source == 'none':
        report.warnings.append('ASR result has no timestamp data')
    return report
