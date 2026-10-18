#!/usr/bin/env python3
"""Character-level timeline built from an ASR result.

Why a character timeline?  Cue alignment needs a *monotonic, fine-grained*
time axis.  ASR engines give either tokens (Qwen3 ForcedAligner: word/char
level) or segments (Whisper: 5–30 s blocks).  Spreading each unit's
characters evenly across its own start/end yields one comparable axis for
every engine, which is what let the mapper stay engine-agnostic.

The timeline is a pure derivation — no audio, no models, no I/O.  It also
carries a :class:`~lib.alignment.quality.TimelineQuality` verdict so callers
can tell a normal token axis from a collapsed one, and optionally falls back
to the segment axis when tokens are catastrophically broken.

Usage:
    from lib.alignment.timeline import build_char_timeline
    tl = build_char_timeline(asr_result)
    tl.chars, tl.starts, tl.ends, tl.quality, tl.warnings
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lib.alignment.base import normalize_text
from lib.alignment.quality import (
    TimelinePolicy, TimelineQuality, assess_units, repair_overlong_units,
    should_fallback, unit_triples,
)


@dataclass
class CharTimeline:
    """Normalised characters with absolute start/end times.

    Attributes:
        chars: normalised characters (see base.normalize_text).
        starts/ends: per-character times in seconds, non-decreasing.
        unit_indices: timeline char → ASR unit index (token index for
            Qwen3, segment index for Whisper).
        unit_texts: original (un-normalised) text of every ASR unit.
        source: ``tokens`` | ``segments`` | ``cues`` | ``none``.
        duration: end of the last character (0 for an empty timeline).
        quality: health of the chosen source (None for synthetic timelines).
        fallback: source that was rejected in favour of this one, if any.
        warnings: human-readable quality/fallback notes for reports.
    """

    chars: list[str] = field(default_factory=list)
    starts: list[float] = field(default_factory=list)
    ends: list[float] = field(default_factory=list)
    unit_indices: list[int] = field(default_factory=list)
    unit_texts: list[str] = field(default_factory=list)
    source: str = 'none'
    duration: float = 0.0
    quality: TimelineQuality | None = None
    fallback: str = ''
    warnings: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.chars)

    @property
    def text(self) -> str:
        return ''.join(self.chars)

    def slice_text(self, start: int, end: int) -> str:
        return ''.join(self.chars[start:end])

    def time_span(self, start: int, end: int) -> tuple[float, float]:
        """Times covering chars [start, end)."""
        if not self.chars or start >= end:
            return (0.0, 0.0)
        start = max(0, start)
        end = min(len(self.chars), end)
        return (self.starts[start], self.ends[end - 1])

    def unit_span(self, start: int, end: int) -> tuple[int, int] | None:
        """ASR unit indices covering chars [start, end)."""
        if not self.chars or start >= end:
            return None
        end = min(len(self.chars), end)
        if end <= 0:
            return None
        first = self.unit_indices[max(0, start)]
        last = self.unit_indices[end - 1]
        return (first, last + 1)

    def raw_text(self, unit_start: int, unit_end: int) -> str:
        """Original ASR text for a unit span (used for replacement proposals)."""
        if not self.unit_texts:
            return ''
        unit_start = max(0, unit_start)
        unit_end = min(len(self.unit_texts), unit_end)
        if unit_end <= unit_start:
            return ''
        return ''.join(self.unit_texts[unit_start:unit_end]).strip()

    # ── construction ────────────────────────────────────────

    @classmethod
    def from_units(cls, units, *, source: str = 'tokens') -> 'CharTimeline':
        """Build a timeline from (text, start, end) triples.

        Empty (punctuation-only, whitespace-only) units contribute no
        characters but still occupy a unit index, so unit spans stay valid.
        """
        tl = cls(source=source)
        last_start = float('-inf')
        last_end = float('-inf')

        for unit_index, (text, start, end) in enumerate(units):
            start = float(start or 0.0)
            end = float(end if end is not None else start)
            if end < start:
                end = start
            tl.unit_texts.append(str(text or ''))
            norm = normalize_text(text or '')
            if not norm:
                continue

            count = len(norm)
            step = (end - start) / count if count else 0.0
            for k, ch in enumerate(norm):
                ch_start = start + step * k
                ch_end = start + step * (k + 1) if step else start
                # keep the axis monotonic; don't collapse zero-length spans
                if ch_start < last_start:
                    ch_start = last_start
                if ch_end < ch_start:
                    ch_end = ch_start
                if ch_end < last_end:
                    ch_end = last_end
                tl.chars.append(ch)
                tl.starts.append(ch_start)
                tl.ends.append(ch_end)
                tl.unit_indices.append(unit_index)
                last_start = ch_start
                last_end = ch_end

        if tl.ends:
            tl.duration = tl.ends[-1]
        return tl

    def to_dict(self) -> dict:
        out = {
            'source': self.source,
            'duration': round(self.duration, 3),
            'chars': self.chars,
            'starts': [round(x, 3) for x in self.starts],
            'ends': [round(x, 3) for x in self.ends],
        }
        if self.quality is not None:
            out['quality'] = self.quality.to_dict()
        if self.fallback:
            out['fallback'] = self.fallback
        if self.warnings:
            out['warnings'] = list(self.warnings)
        return out


def build_char_timeline(result, *,
                        policy: TimelinePolicy | None = None) -> CharTimeline:
    """Build a timeline from an ``ASRResult``-like object.

    Preference order: tokens (finest) → segments → none.  An engine with
    neither (text only) cannot be aligned in time; the empty timeline makes
    the mapper report ``no_asr`` instead of inventing timestamps.

    ``policy`` adds two behaviours on top of the old "tokens always win":

    * the chosen source is assessed (zero runs / overlong units) and the
      verdict travels with the timeline;
    * when tokens are *catastrophically* collapsed and the segment timeline
      is clearly healthier, segments are used instead (``tl.fallback``).

    ``repair_overlong`` is opt-in because clamping spans can shift
    legitimately long sung/vowel tokens as well as broken ones.
    """
    policy = policy or TimelinePolicy()
    tokens = getattr(result, 'tokens', None) or []
    segments = getattr(result, 'segments', None) or []
    token_units = unit_triples(tokens)
    segment_units = unit_triples(segments)

    forced = policy.force_source
    use_tokens = bool(token_units) and policy.prefer_tokens
    if forced == 'tokens':
        use_tokens = bool(token_units)
    elif forced == 'segments':
        use_tokens = False

    if use_tokens:
        units = token_units
        repaired = 0
        if policy.repair_overlong:
            # Mechanism: the forced aligner absorbs leading silence into the
            # first token of an utterance.  Restrict the trim to segment
            # starts; mid-segment long tokens may be legitimate.
            allowed = {round(float(s.start), 3) for s in segments
                       if getattr(s, 'tokens', None)}
            units, repaired = repair_overlong_units(
                units, policy=policy, allowed_starts=allowed or None)
        quality = assess_units(units, source='tokens', policy=policy)

        if segment_units:
            segment_quality = assess_units(segment_units, source='segments',
                                           policy=policy)
            if should_fallback(quality, segment_quality, policy):
                tl = CharTimeline.from_units(segment_units, source='segments')
                tl.quality = segment_quality
                tl.fallback = 'tokens'
                tl.warnings.append(
                    'token timeline collapsed; fell back to segments '
                    f'({quality.describe()})')
                return tl

        tl = CharTimeline.from_units(units, source='tokens')
        tl.quality = quality
        if repaired:
            tl.warnings.append(
                f'trimmed {repaired} overlong token span(s) '
                f'using {quality.median_char_rate:.3f}s/char')
        if quality.degraded:
            tl.warnings.append(
                f'token timeline degraded: {quality.describe()}')
        return tl

    if segment_units:
        quality = assess_units(segment_units, source='segments',
                               policy=policy)
        tl = CharTimeline.from_units(segment_units, source='segments')
        tl.quality = quality
        if quality.degraded:
            tl.warnings.append(
                f'segment timeline degraded: {quality.describe()}')
        return tl

    if token_units:
        # forced segment-only mode, but tokens are the only timestamp data
        quality = assess_units(token_units, source='tokens', policy=policy)
        tl = CharTimeline.from_units(token_units, source='tokens')
        tl.quality = quality
        return tl

    return CharTimeline(source='none')


def timeline_from_cues(cues) -> CharTimeline:
    """Build a timeline from cue dicts (synthetic ASR, tests/benchmarks)."""
    units = []
    for cue in cues:
        start = float(cue.get('start_s', 0.0))
        end = float(cue.get('end_s', start))
        units.append((cue.get('text', ''), start, end))
    return CharTimeline.from_units(units, source='cues')
