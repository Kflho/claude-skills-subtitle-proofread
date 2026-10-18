#!/usr/bin/env python3
"""Timeline quality — decide whether ASR unit timestamps can be trusted.

Why this exists: a forced aligner can emit perfectly monotonic tokens that
are still useless as a time axis — collapsed runs (start == end), or one
token stretched over a music/silence gap.  The mapper has no way to tell
those apart from a genuine short utterance, so the pathology silently
poisons cue boundaries.

This module is the *diagnostic* half of P2: pure functions over
``(text, start, end)`` units that

* quantify zero-duration / overlong units and the longest collapsed run;
* mark a timeline ``degraded`` (usable, but with review-worthy regions);
* decide when to fall back from tokens to the segment timeline;
* optionally trim absurd overlong spans (opt-in: it can shift legitimate
  long tokens, so callers choose it per run).

Nothing here imports audio or models.  See docs/adr/007-cue-alignment.md.

Usage:
    from lib.alignment.quality import TimelinePolicy, assess_units
    q = assess_units(units, source='tokens')
    print(q.degraded, q.describe())
"""

from __future__ import annotations

from dataclasses import dataclass, field
import statistics

from lib.alignment.base import normalize_text

ZERO_EPS = 1e-6

# Speech rate filter for the local median: 0.02–1.5 s per visible char.
# Anything outside is punctuation noise or a silence-padded unit.
_RATE_MIN = 0.02
_RATE_MAX = 1.5
_FALLBACK_RATE = 0.20


@dataclass
class TimelinePolicy:
    """Knobs for building and validating a character timeline.

    Defaults are conservative: tokens win unless they are *catastrophically*
    broken, because on engines that derive segments from tokens (Qwen3) a
    segment fallback cannot recover the missing time — it only coarsens it.
    """

    # prefer tokens when present; False forces the segment timeline
    prefer_tokens: bool = True
    # force one source regardless of availability (diagnostics/A-B only)
    force_source: str = ''            # '' | 'tokens' | 'segments'
    # opt-in: clamp overlong unit spans using the local speech rate
    repair_overlong: bool = False
    overlong_floor: float = 2.0       # never call < 2 s "overlong"
    overlong_factor: float = 6.0      # x median seconds-per-char
    # "degraded" thresholds (usable, but flagged in reports).  These are
    # *operational definitions*, not fitted values: >1 in 20 units without
    # time is review-worthy; 4 consecutive units is ~0.64 s of speech at the
    # observed 0.16 s/char; 6x the local rate is not a normal syllable.
    degrade_zero_ratio: float = 0.05
    degrade_zero_run: int = 4
    degrade_overlong_ratio: float = 0.05
    # "broken" thresholds: relative to the source size, so a 3000-unit
    # episode and a 300-unit clip are judged on the same scale.
    fallback_zero_ratio: float = 0.50
    fallback_zero_run_fraction: float = 0.10
    fallback_overlong_ratio: float = 0.20
    fallback_margin: float = 0.05

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class TimelineQuality:
    """Measured health of one timestamp source (tokens or segments)."""

    source: str
    units: int
    chars: int
    duration: float
    zero_units: int = 0
    zero_ratio: float = 0.0
    zero_runs: int = 0
    longest_zero_run: int = 0
    overlong_units: int = 0
    overlong_ratio: float = 0.0
    max_unit_seconds: float = 0.0
    median_unit_seconds: float = 0.0
    median_char_rate: float = 0.0
    degraded: bool = False
    reasons: list[str] = field(default_factory=list)

    @property
    def score(self) -> float:
        """0 = clean, 1 = useless; used only to compare two sources."""
        run_penalty = min(0.20, self.longest_zero_run / max(1, self.units))
        return round(min(1.0, self.zero_ratio + self.overlong_ratio
                         + run_penalty), 4)

    def describe(self) -> str:
        parts = []
        if self.zero_units:
            parts.append(f'zero {self.zero_ratio:.1%} '
                         f'({self.zero_units}/{self.units}, '
                         f'longest run {self.longest_zero_run})')
        if self.overlong_units:
            parts.append(f'overlong {self.overlong_ratio:.1%}')
        return '; '.join(parts) or 'clean'

    def to_dict(self) -> dict:
        return {
            'source': self.source,
            'units': self.units,
            'chars': self.chars,
            'duration': round(self.duration, 3),
            'zero_units': self.zero_units,
            'zero_ratio': round(self.zero_ratio, 4),
            'zero_runs': self.zero_runs,
            'longest_zero_run': self.longest_zero_run,
            'overlong_units': self.overlong_units,
            'overlong_ratio': round(self.overlong_ratio, 4),
            'max_unit_seconds': round(self.max_unit_seconds, 3),
            'median_unit_seconds': round(self.median_unit_seconds, 3),
            'median_char_rate': round(self.median_char_rate, 4),
            'degraded': self.degraded,
            'reasons': list(self.reasons),
            'score': self.score,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'TimelineQuality':
        return cls(
            source=str(data.get('source', '')),
            units=int(data.get('units', 0)),
            chars=int(data.get('chars', 0)),
            duration=float(data.get('duration', 0.0)),
            zero_units=int(data.get('zero_units', 0)),
            zero_ratio=float(data.get('zero_ratio', 0.0)),
            zero_runs=int(data.get('zero_runs', 0)),
            longest_zero_run=int(data.get('longest_zero_run', 0)),
            overlong_units=int(data.get('overlong_units', 0)),
            overlong_ratio=float(data.get('overlong_ratio', 0.0)),
            max_unit_seconds=float(data.get('max_unit_seconds', 0.0)),
            median_unit_seconds=float(data.get('median_unit_seconds', 0.0)),
            median_char_rate=float(data.get('median_char_rate', 0.0)),
            degraded=bool(data.get('degraded', False)),
            reasons=list(data.get('reasons', [])),
        )


def visible_len(text: str) -> int:
    """Characters that carry speech (punct/space/symbol ignored)."""
    return len(normalize_text(text or ''))


def median_char_rate(units) -> float:
    """Median seconds per visible character over sane units.

    Zero/overlong units are excluded (they are what we are trying to
    detect); an empty sample falls back to 200 ms per char.
    """
    rates = []
    for text, start, end in units:
        n = visible_len(text)
        if n <= 0:
            continue
        rate = (float(end) - float(start)) / n
        if _RATE_MIN <= rate <= _RATE_MAX:
            rates.append(rate)
    return statistics.median(rates) if rates else _FALLBACK_RATE


def _zero_runs(units) -> list[tuple[int, int]]:
    runs = []
    i = 0
    total = len(units)
    while i < total:
        if float(units[i][2]) - float(units[i][1]) > ZERO_EPS:
            i += 1
            continue
        j = i
        while (j < total
               and float(units[j][2]) - float(units[j][1]) <= ZERO_EPS):
            j += 1
        runs.append((i, j))
        i = j
    return runs


def assess_units(units, *, source: str = 'tokens',
                 policy: TimelinePolicy | None = None) -> TimelineQuality:
    """Measure one timestamp source.  ``units`` is a list of triples."""
    policy = policy or TimelinePolicy()
    units = list(units or [])
    total = len(units)
    chars = sum(visible_len(u[0]) for u in units)
    durations = [max(0.0, float(u[2]) - float(u[1])) for u in units]
    rate = median_char_rate(units)

    zero_units = sum(1 for d in durations if d <= ZERO_EPS)
    runs = _zero_runs(units)
    longest_run = max((b - a for a, b in runs), default=0)

    overlong = 0
    for text, start, end in units:
        n = visible_len(text)
        if n <= 0:
            continue
        dur = float(end) - float(start)
        if dur > max(policy.overlong_floor, policy.overlong_factor * rate * n):
            overlong += 1

    quality = TimelineQuality(
        source=source,
        units=total,
        chars=chars,
        duration=max((float(u[2]) for u in units), default=0.0),
        zero_units=zero_units,
        zero_ratio=zero_units / total if total else 0.0,
        zero_runs=len(runs),
        longest_zero_run=longest_run,
        overlong_units=overlong,
        overlong_ratio=overlong / total if total else 0.0,
        max_unit_seconds=max(durations, default=0.0),
        median_unit_seconds=(statistics.median(durations) if durations else 0.0),
        median_char_rate=rate,
    )

    reasons = []
    if total and quality.zero_ratio >= policy.degrade_zero_ratio:
        reasons.append(f'zero-duration units {quality.zero_ratio:.1%} '
                       f'({zero_units}/{total})')
    if longest_run >= policy.degrade_zero_run:
        reasons.append(f'collapsed run of {longest_run} units')
    if total and quality.overlong_ratio >= policy.degrade_overlong_ratio:
        reasons.append(f'overlong units {quality.overlong_ratio:.1%} '
                       f'({overlong}/{total})')
    quality.reasons = reasons
    quality.degraded = bool(reasons)
    return quality


def is_broken(quality: TimelineQuality,
              policy: TimelinePolicy | None = None) -> bool:
    """Catastrophic collapse — worth trying the segment timeline.

    Thresholds are *relative* (ratio of units, share in one run) so the same
    policy applies to a 5-minute clip and a 50-minute episode.  A timeline
    with 18 collapsed units out of 962 is degraded, not broken.
    """
    policy = policy or TimelinePolicy()
    run_fraction = (quality.longest_zero_run / quality.units
                    if quality.units else 0.0)
    return (quality.zero_ratio >= policy.fallback_zero_ratio
            or run_fraction >= policy.fallback_zero_run_fraction
            or quality.overlong_ratio >= policy.fallback_overlong_ratio)


def should_fallback(tokens: TimelineQuality, segments: TimelineQuality,
                    policy: TimelinePolicy | None = None) -> bool:
    """True when tokens are broken and segments are clearly better.

    Segments derived from the same tokens (Qwen3) rarely pass the second
    test; engines with independent segment times (Whisper-style APIs that
    also expose tokens) can.
    """
    policy = policy or TimelinePolicy()
    if segments.units <= 0 or segments.chars <= 0:
        return False
    if not is_broken(tokens, policy):
        return False
    return segments.score + policy.fallback_margin < tokens.score


def repair_overlong_units(units, *,
                          policy: TimelinePolicy | None = None,
                          allowed_starts: set[float] | None = None
                          ) -> tuple[list[tuple[str, float, float]], int]:
    """Clamp absurd unit spans using the local speech rate (opt-in).

    Only the *start* moves: the aligner tends to stretch a unit backwards
    over leading silence/OP music while its end stays near the speech.  The
    repair keeps the axis monotonic and never invents a duration longer than
    the median rate allows.

    ``allowed_starts`` restricts the repair to units that begin a segment
    (rounded to 3 decimals).  That is the mechanism we measured: the
    forced aligner absorbs leading silence into the *first* token of an
    utterance; mid-segment long tokens are left alone because they may be
    legitimate sustained vowels or sung lines.

    Returns ``(repaired_units, count)``.  Callers must treat this as a
    deliberate pass (``TimelinePolicy.repair_overlong``), not a default.
    """
    policy = policy or TimelinePolicy()
    rate = median_char_rate(units)
    out = []
    repaired = 0
    prev_end = float('-inf')
    for text, start, end in units:
        start = float(start)
        end = float(end)
        n = visible_len(text)
        allowed = (allowed_starts is None
                   or round(start, 3) in allowed_starts)
        if n > 0 and end > start and allowed:
            expected = rate * n
            if (end - start) > max(policy.overlong_floor,
                                   policy.overlong_factor * expected):
                trimmed = max(prev_end, end - expected)
                if trimmed > start:
                    start = trimmed
                    repaired += 1
        out.append((text, start, end))
        prev_end = max(prev_end, end)
    return out, repaired


def unit_triples(units) -> list[tuple[str, float, float]]:
    """Normalise Token/Segment objects or triples into ``(text, s, e)``."""
    triples = []
    for unit in units:
        if isinstance(unit, (tuple, list)):
            text, start, end = unit[0], unit[1], unit[2]
        else:
            text = getattr(unit, 'text', '')
            start = getattr(unit, 'start', 0.0)
            end = getattr(unit, 'end', start)
        triples.append((str(text or ''), float(start or 0.0),
                        float(end if end is not None else start or 0.0)))
    return triples
