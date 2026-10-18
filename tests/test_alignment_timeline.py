"""Tests for lib.alignment.timeline — ASR units → character timeline."""

import pytest

from lib.asr.base import ASRResult, Segment, Token
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
