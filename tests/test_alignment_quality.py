"""Tests for lib.alignment.quality — timestamp health + fallback policy."""

import pytest

from lib.alignment.quality import (
    TimelinePolicy, TimelineQuality, assess_units, is_broken,
    median_char_rate, repair_overlong_units, should_fallback, unit_triples,
)
from lib.asr.base import Segment, Token


def units(*rows):
    return list(rows)


# ═══════════════════════════════════════════════════════════════
# Assessing one source
# ═══════════════════════════════════════════════════════════════

def test_clean_units_are_not_degraded():
    q = assess_units(units(
        ('おはよう', 1.0, 1.6),
        ('元気ですか', 1.6, 2.4),
    ), source='tokens')
    assert q.units == 2
    assert q.chars == 9
    assert q.zero_units == 0
    assert q.longest_zero_run == 0
    assert not q.degraded
    assert q.reasons == []
    assert q.score == 0.0


def test_zero_units_and_longest_run_are_counted():
    q = assess_units(units(
        ('あ', 1.0, 1.0),
        ('い', 1.0, 1.0),
        ('う', 1.0, 1.0),
        ('え', 2.0, 2.5),
        ('お', 3.0, 3.0),
    ))
    assert q.zero_units == 4
    assert q.zero_runs == 2
    assert q.longest_zero_run == 3
    assert q.zero_ratio == pytest.approx(0.8)
    assert q.degraded


def test_zero_ratio_threshold_is_configurable():
    q = assess_units(units(('あ', 1.0, 1.0), ('い', 1.0, 2.0)))
    assert q.degraded
    relaxed = assess_units(units(('あ', 1.0, 1.0), ('い', 1.0, 2.0)),
                           policy=TimelinePolicy(degrade_zero_ratio=0.6,
                                                degrade_zero_run=99))
    assert not relaxed.degraded


def test_overlong_unit_is_detected_against_local_rate():
    # "その" stretched over 3.76 s while its neighbours run at ~0.16 s/char.
    q = assess_units(units(
        ('その', 0.0, 3.76),
        ('年', 3.76, 3.92),
        ('の', 3.92, 4.08),
        ('夏', 4.08, 4.24),
        ('は', 4.24, 4.40),
    ))
    assert q.overlong_units == 1
    assert q.overlong_ratio == pytest.approx(0.2)
    assert q.degraded
    assert any('overlong' in reason for reason in q.reasons)


def test_degraded_reasons_are_human_readable():
    q = assess_units(units(('あ', 1.0, 1.0), ('い', 1.0, 1.0),
                           ('う', 1.0, 1.0), ('え', 1.0, 1.0)))
    text = q.describe()
    assert 'zero 100.0%' in text
    assert 'longest run 4' in text
    assert q.degraded


def test_empty_source_is_not_degraded_but_scores_zero():
    q = assess_units([])
    assert q.units == 0
    assert q.chars == 0
    assert not q.degraded
    assert q.score == 0.0
    assert q.describe() == 'clean'


def test_quality_roundtrip():
    q = assess_units(units(('あ', 1.0, 1.0), ('い', 1.0, 2.0)),
                     source='tokens')
    restored = TimelineQuality.from_dict(q.to_dict())
    assert restored.to_dict() == q.to_dict()
    assert restored.score == q.score


def test_unit_triples_accepts_objects_and_tuples():
    assert unit_triples([Token('あ', 1.0, 2.0)]) == [('あ', 1.0, 2.0)]
    assert unit_triples([Segment('い', 2.0, 3.0)]) == [('い', 2.0, 3.0)]
    assert unit_triples([('う', 3, 4)]) == [('う', 3.0, 4.0)]


# ═══════════════════════════════════════════════════════════════
# Comparing sources / fallback
# ═══════════════════════════════════════════════════════════════

def test_tokens_degraded_is_not_necessarily_broken():
    q = assess_units(units(
        ('あ', 1.0, 1.0),
        ('い', 1.0, 1.0),
        *[(f'う{i}', 1.0 + i, 1.2 + i) for i in range(20)],
    ))
    assert q.degraded
    assert not is_broken(q)


def test_broken_tokens_fall_back_to_healthier_segments():
    tokens = assess_units(units(*[('あ', 1.0, 1.0) for _ in range(10)]),
                          source='tokens')
    segments = assess_units(units(('あいうえお', 1.0, 2.0)),
                            source='segments')
    assert is_broken(tokens)
    assert should_fallback(tokens, segments)


def test_no_fallback_when_segments_are_not_better():
    tokens = assess_units(units(*[('あ', 1.0, 1.0) for _ in range(10)]),
                          source='tokens')
    segments = assess_units(units(*[('あ', 1.0, 1.0) for _ in range(10)]),
                            source='segments')
    assert not should_fallback(tokens, segments)


def test_no_fallback_without_segment_data():
    tokens = assess_units(units(*[('あ', 1.0, 1.0) for _ in range(10)]))
    empty = assess_units([])
    assert not should_fallback(tokens, empty)


def test_healthy_tokens_never_fall_back():
    tokens = assess_units(units(('あい', 1.0, 2.0)))
    segments = assess_units(units(('あい', 1.0, 2.0)))
    assert not should_fallback(tokens, segments)


# ═══════════════════════════════════════════════════════════════
# Opt-in overlong repair
# ═══════════════════════════════════════════════════════════════

def test_repair_trims_the_leading_smear_only():
    raw = units(
        ('その', 0.0, 3.76),
        ('年', 3.76, 3.92),
        ('の', 3.92, 4.08),
        ('夏', 4.08, 4.24),
    )
    repaired, count = repair_overlong_units(raw)
    assert count == 1
    assert repaired[0][0] == 'その'
    assert repaired[0][2] == pytest.approx(3.76)      # end is preserved
    assert repaired[0][1] == pytest.approx(3.44)      # 2 chars * 0.16 s
    assert repaired[1:] == raw[1:]


def test_repair_leaves_normal_spans_alone():
    raw = units(('おはよう', 1.0, 1.6), ('元気ですか', 1.6, 2.4))
    repaired, count = repair_overlong_units(raw)
    assert count == 0
    assert repaired == raw


def test_repair_keeps_the_axis_monotonic():
    raw = units(
        ('あ', 0.0, 5.0),
        ('い', 5.0, 5.2),
        ('う', 5.2, 5.4),
        ('え', 5.4, 5.6),
    )
    repaired, count = repair_overlong_units(raw)
    assert count == 1
    starts = [u[1] for u in repaired]
    ends = [u[2] for u in repaired]
    assert starts == sorted(starts)
    assert all(end >= start for start, end in zip(starts, ends))
    assert repaired[0][1] < repaired[0][2]


def test_repair_respects_factor_and_floor():
    # A single 2.5 s unit has no local sample; the fallback rate (0.2 s/char)
    # still flags it as overlong and trims the start, keeping the end.
    repaired, count = repair_overlong_units(units(('あ', 0.0, 2.5)))
    assert count == 1
    assert repaired[0][1] == pytest.approx(2.3)
    # 2.2 s over 2 chars is only overlong with a lower factor.
    raw = units(('あい', 0.0, 2.2), ('う', 2.2, 2.4), ('え', 2.4, 2.6))
    _repaired, count = repair_overlong_units(
        raw, policy=TimelinePolicy(overlong_floor=2.0, overlong_factor=6.0))
    assert count == 0
    repaired, count = repair_overlong_units(
        raw, policy=TimelinePolicy(overlong_floor=2.0, overlong_factor=2.0))
    assert count == 1


def test_repair_allowed_starts_restricts_to_segment_edges():
    raw = units(('その', 0.0, 3.76), ('年', 3.76, 3.92),
                ('の', 3.92, 4.08))
    repaired, count = repair_overlong_units(raw, allowed_starts={0.0})
    assert count == 1
    untouched, count = repair_overlong_units(raw, allowed_starts={99.0})
    assert count == 0
    assert untouched == raw


def test_median_char_rate_falls_back_when_no_sane_unit():
    assert median_char_rate([]) == pytest.approx(0.20)
    assert median_char_rate(units(('あ', 1.0, 1.0))) == pytest.approx(0.20)
