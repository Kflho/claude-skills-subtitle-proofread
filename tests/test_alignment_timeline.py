"""Tests for lib.alignment.timeline — ASR units → character timeline."""

import pytest

from lib.asr.base import ASRResult, Segment, Token
from lib.alignment.quality import TimelinePolicy
from lib.alignment.timeline import (
    build_char_timeline, timeline_from_cues,
)


def test_tokens_are_expanded_and_interpolated():
    result = ASRResult(text='', tokens=[Token('あい', 1.0, 2.0)])
    tl = build_char_timeline(result)
    assert tl.source == 'tokens'
    assert tl.text == 'あい'
    assert tl.starts == pytest.approx([1.0, 1.5])
    assert tl.ends == pytest.approx([1.5, 2.0])
    assert tl.duration == pytest.approx(2.0)


def test_punctuation_tokens_keep_unit_indices_stable():
    result = ASRResult(text='', tokens=[
        Token('あ', 1.0, 1.5),
        Token('、', 1.5, 1.6),
        Token('い', 1.6, 2.0),
    ])
    tl = build_char_timeline(result)
    assert tl.chars == ['あ', 'い']
    assert tl.unit_indices == [0, 2]
    assert tl.unit_span(0, 2) == (0, 3)
    assert tl.raw_text(0, 3) == 'あ、い'


def test_segments_are_used_when_tokens_are_absent():
    result = ASRResult(text='はい', segments=[Segment('はい', 1.0, 3.0)])
    tl = build_char_timeline(result)
    assert tl.source == 'segments'
    assert tl.text == 'はい'
    assert tl.starts == pytest.approx([1.0, 2.0])
    assert tl.ends == pytest.approx([2.0, 3.0])
    assert tl.unit_indices == [0, 0]


def test_text_only_result_yields_empty_timeline():
    tl = build_char_timeline(ASRResult(text='タイムスタンプなし'))
    assert tl.source == 'none'
    assert len(tl) == 0
    assert tl.time_span(0, 1) == (0.0, 0.0)


def test_timeline_stays_monotonic_when_tokens_overlap():
    result = ASRResult(text='', tokens=[
        Token('あい', 1.0, 2.0),
        Token('うえ', 1.5, 2.5),
    ])
    tl = build_char_timeline(result)
    assert tl.starts == sorted(tl.starts)
    assert tl.ends == sorted(tl.ends)
    for start, end in zip(tl.starts, tl.ends):
        assert end >= start


def test_zero_length_unit_does_not_collapse_the_axis():
    result = ASRResult(text='', tokens=[Token('あ', 1.0, 1.0),
                                        Token('い', 1.0, 2.0)])
    tl = build_char_timeline(result)
    assert tl.starts == pytest.approx([1.0, 1.0])
    assert tl.ends[-1] >= tl.starts[-1]


def test_timeline_from_cues_roundtrips_cue_times():
    cues = [
        {'start_s': 1.0, 'end_s': 2.0, 'text': 'あい'},
        {'start_s': 2.5, 'end_s': 3.5, 'text': 'うえ'},
    ]
    tl = timeline_from_cues(cues)
    assert tl.source == 'cues'
    assert tl.text == 'あいうえ'
    assert tl.unit_span(0, 2) == (0, 1)
    assert tl.unit_span(2, 4) == (1, 2)
    assert tl.time_span(2, 4) == (2.5, 3.5)


def test_slice_and_raw_text_helpers():
    cues = [{'start_s': 0.0, 'end_s': 1.0, 'text': 'こんにちは'}]
    tl = timeline_from_cues(cues)
    assert tl.slice_text(0, 2) == 'こん'
    assert tl.raw_text(0, 1) == 'こんにちは'
    assert tl.raw_text(3, 1) == ''

    data = tl.to_dict()
    assert data['source'] == 'cues'
    assert len(data['chars']) == 5


# ═══════════════════════════════════════════════════════════════
# Quality verdict + segment fallback (EP01 token-collapse work)
# ═══════════════════════════════════════════════════════════════

def test_quality_verdict_travels_with_the_timeline():
    tokens = [Token('あ', 1.0, 1.0)]
    tokens += [Token(f'い{i}', 1.0 + 0.2 * i, 1.2 + 0.2 * i)
               for i in range(20)]
    tl = build_char_timeline(ASRResult(text='', tokens=tokens))
    assert tl.source == 'tokens'
    assert tl.quality is not None
    assert tl.quality.zero_units == 1
    assert not tl.quality.degraded       # 1/21 < 5 %, run 1 < 4
    assert tl.warnings == []


def test_degraded_timeline_is_flagged_in_warnings():
    result = ASRResult(text='', tokens=[
        Token('あ', 1.0, 1.0),
        Token('い', 1.0, 1.0),
        Token('う', 1.0, 1.0),
        Token('え', 1.0, 1.0),
    ])
    tl = build_char_timeline(result)
    assert tl.quality.degraded
    assert any('degraded' in w for w in tl.warnings)
    assert tl.quality.reasons


def test_catastrophic_tokens_fall_back_to_segments():
    tokens = [Token('あ', 1.0, 1.0) for _ in range(10)]
    result = ASRResult(text='あ' * 10,
                       segments=[Segment('あ' * 10, 1.0, 3.0)],
                       tokens=tokens)
    tl = build_char_timeline(result)
    assert tl.source == 'segments'
    assert tl.fallback == 'tokens'
    assert tl.quality is not None
    assert not tl.quality.degraded
    assert tl.starts[0] == pytest.approx(1.0)
    assert tl.ends[-1] == pytest.approx(3.0)
    assert any('fell back' in w for w in tl.warnings)


def test_healthy_tokens_are_not_replaced_by_segments():
    result = ASRResult(
        text='あい',
        tokens=[Token('あい', 1.0, 2.0)],
        segments=[Segment('あい', 5.0, 9.0)],
    )
    tl = build_char_timeline(result)
    assert tl.source == 'tokens'
    assert tl.fallback == ''
    assert tl.starts == pytest.approx([1.0, 1.5])


def test_force_source_allows_token_vs_segment_ab():
    result = ASRResult(
        text='あい',
        tokens=[Token('あい', 1.0, 2.0)],
        segments=[Segment('あい', 5.0, 9.0)],
    )
    tl = build_char_timeline(result, policy=TimelinePolicy(
        force_source='segments'))
    assert tl.source == 'segments'
    assert tl.starts == pytest.approx([5.0, 7.0])


def test_repair_overlong_is_opt_in_and_tracks_warnings():
    result = ASRResult(text='', tokens=[
        Token('その', 0.0, 3.76),
        Token('年', 3.76, 3.92),
        Token('の', 3.92, 4.08),
    ])
    plain = build_char_timeline(result)
    assert plain.starts[0] == pytest.approx(0.0)

    repaired = build_char_timeline(result, policy=TimelinePolicy(
        repair_overlong=True))
    assert repaired.starts[0] == pytest.approx(3.44)
    assert any('trimmed 1 overlong' in w for w in repaired.warnings)


def test_repair_only_touches_segment_initial_tokens():
    tokens = [Token('あ', 0.0, 0.2), Token('長', 0.2, 5.0),
              Token('い', 5.0, 5.2)]
    # '長' is mid-utterance: overlong, but not leading silence -> untouched.
    mid = ASRResult(text='あ長い', tokens=tokens, segments=[
        Segment('あ長い', 0.0, 5.2, tokens=tokens)])
    tl = build_char_timeline(mid, policy=TimelinePolicy(
        repair_overlong=True))
    assert tl.starts[1] == pytest.approx(0.2)
    assert not any('trimmed' in w for w in tl.warnings)

    # Same token as the first token of a segment -> trimmed.
    edge = ASRResult(text='あ長い', tokens=tokens, segments=[
        Segment('あ', 0.0, 0.2, tokens=tokens[:1]),
        Segment('長い', 0.2, 5.2, tokens=tokens[1:]),
    ])
    tl = build_char_timeline(edge, policy=TimelinePolicy(
        repair_overlong=True))
    assert tl.starts[1] > 0.2
    assert any('trimmed 1 overlong' in w for w in tl.warnings)


def test_segment_timeline_also_carries_quality():
    result = ASRResult(text='あい', segments=[
        Segment('あ', 1.0, 1.0), Segment('い', 1.0, 1.2)])
    tl = build_char_timeline(result)
    assert tl.source == 'segments'
    assert tl.quality.source == 'segments'
    assert tl.quality.zero_units == 1


def test_timeline_to_dict_includes_quality_when_present():
    result = ASRResult(text='あい', tokens=[Token('あい', 1.0, 2.0)])
    data = build_char_timeline(result).to_dict()
    assert data['quality']['source'] == 'tokens'
    assert 'fallback' not in data
    assert data['quality']['units'] == 1

    cues = timeline_from_cues([{'start_s': 0, 'end_s': 1, 'text': 'あ'}])
    assert 'quality' not in cues.to_dict()


def test_text_only_result_still_has_no_quality():
    tl = build_char_timeline(ASRResult(text='タイムスタンプなし'))
    assert tl.source == 'none'
    assert tl.quality is None
