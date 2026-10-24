"""Tests for lib.alignment.cue_mapper — the P2 alignment core."""

import pytest

from lib.alignment.base import (
    ACTION_DELETE_CANDIDATE, ACTION_FIX_TIME, ACTION_KEEP,
    ACTION_REPLACE_TEXT, ACTION_REVIEW_AI, AlignmentConfig, STATUS_EMPTY_TEXT,
    STATUS_EXTRA_TEXT, STATUS_GARBLED, STATUS_MATCH, STATUS_MISSING_ASR,
    STATUS_NO_ASR, STATUS_NO_SPEECH,
)
from lib.alignment.cue_mapper import map_cues, map_report
from lib.asr.base import ASRResult, Segment, Token


def make_result(tokens=(), segments=(), duration=30.0) -> ASRResult:
    token_objs = [Token(t, s, e) for t, s, e in tokens]
    text = ''.join(t.text for t in token_objs)
    return ASRResult(text=text, tokens=token_objs,
                     segments=[Segment(*s) for s in segments],
                     engine='fake', audio_duration=duration)


def cue(start, end, text):
    return {'start_s': start, 'end_s': end, 'text': text}


# ═══════════════════════════════════════════════════════════════
# Happy path
# ═══════════════════════════════════════════════════════════════

def test_exact_match_is_high_confidence_and_left_alone():
    result = make_result([
        ('こんにちは', 1.0, 1.6),
        ('元気ですか', 1.6, 2.4),
    ])
    alignments = map_cues([
        cue(1.0, 2.4, 'こんにちは元気ですか'),
    ], result)
    al = alignments[0]
    assert al.status == STATUS_MATCH
    assert al.similarity == pytest.approx(1.0)
    assert al.confidence == 'high'
    assert al.action == ACTION_KEEP
    assert al.start == pytest.approx(1.0, abs=0.01)
    assert al.end == pytest.approx(2.4, abs=0.01)
    assert al.asr_text == 'こんにちは元気ですか'
    assert al.asr_span == (0, 2)


def test_boundary_drift_becomes_fix_time():
    result = make_result([('お前何してんだよ', 5.0, 6.4)])
    al = map_cues([cue(4.8, 6.4, 'お前何してんだよ')], result)[0]
    assert al.status == STATUS_MATCH
    assert al.action == ACTION_FIX_TIME
    assert al.signals['boundary_error_start'] == pytest.approx(0.2, abs=0.01)
    assert al.max_boundary_error >= 0.15


def test_small_drift_is_kept():
    result = make_result([('お前何してんだよ', 5.0, 6.4)])
    al = map_cues([cue(5.05, 6.45, 'お前何してんだよ')], result)[0]
    assert al.action == ACTION_KEEP


# ═══════════════════════════════════════════════════════════════
# Failure modes
# ═══════════════════════════════════════════════════════════════

def test_garbled_text_with_audio_present():
    result = make_result([('お前何してんだよ', 5.0, 6.4)])
    al = map_cues([cue(5.0, 6.4, 'おまえなにしてんだ')], result)[0]
    assert al.status == STATUS_GARBLED
    assert 0.5 <= al.similarity < 0.85
    assert al.start is not None
    assert al.action in (ACTION_REPLACE_TEXT, ACTION_REVIEW_AI)


def test_extra_text_beyond_asr_speech():
    result = make_result([('おはよう', 1.0, 2.0)])
    al = map_cues([cue(1.0, 5.0, 'おはようございますみなさん')], result)[0]
    assert al.status == STATUS_EXTRA_TEXT
    assert al.action == ACTION_REVIEW_AI


def test_missing_asr_when_speech_exists_but_text_does_not_match():
    result = make_result([('こんにちは', 10.0, 12.0)])
    al = map_cues([cue(10.0, 12.0, 'さようなら')], result)[0]
    assert al.status == STATUS_MISSING_ASR
    assert al.action == ACTION_REVIEW_AI


def test_no_speech_when_asr_has_nothing_near_the_cue():
    result = make_result([('こんにちは', 1.0, 2.0)])
    al = map_cues([cue(10.0, 12.0, 'さようなら')], result)[0]
    assert al.status == STATUS_NO_SPEECH
    assert al.action == ACTION_DELETE_CANDIDATE
    assert al.start is None


def test_empty_cue_text():
    result = make_result([('こんにちは', 1.0, 2.0)])
    al = map_cues([cue(1.0, 2.0, '   ')], result)[0]
    assert al.status == STATUS_EMPTY_TEXT
    assert al.action == ACTION_KEEP


def test_result_without_timestamps():
    result = ASRResult(text='文字だけ', engine='fake')
    al = map_cues([cue(1.0, 2.0, '文字だけ')], result)[0]
    assert al.status == STATUS_NO_ASR
    assert al.action == 'review_human'


# ═══════════════════════════════════════════════════════════════
# Structural cases the old overlap matcher could not handle
# ═══════════════════════════════════════════════════════════════

def test_one_long_asr_span_serves_two_cues_without_double_assignment():
    result = make_result([('おはようございますいい天気ですね', 1.0, 4.0)])
    alignments = map_cues([
        cue(1.0, 2.4, 'おはようございます'),
        cue(2.6, 4.0, 'いい天気ですね'),
    ], result)
    first, second = alignments
    assert first.status == STATUS_MATCH
    assert second.status == STATUS_MATCH
    assert first.char_span[1] <= second.char_span[0]
    assert first.start < first.end <= second.end


def test_repeated_identical_lines_map_to_their_own_occurrence():
    result = make_result([('はい', 1.0, 1.5), ('はい', 10.0, 10.5)])
    alignments = map_cues([
        cue(1.0, 1.5, 'はい'),
        cue(10.0, 10.5, 'はい'),
    ], result)
    assert alignments[0].start == pytest.approx(1.0, abs=0.01)
    assert alignments[1].start == pytest.approx(10.0, abs=0.01)
    assert alignments[0].char_span[1] <= alignments[1].char_span[0]


def test_duplicate_line_within_one_window_picks_the_nearest_occurrence():
    # Both "はい" occurrences fall inside the cue's ±6 s window; difflib
    # alone would always land on the earliest one (5.0 s).
    result = make_result([('はい', 5.0, 5.5), ('はい', 8.0, 8.5)])
    al = map_cues([cue(7.6, 8.5, 'はい')], result)[0]
    assert al.status == STATUS_MATCH
    assert al.start == pytest.approx(8.0, abs=0.01)


def test_unsorted_cue_list_is_aligned_in_time_order():
    result = make_result([('いち', 1.0, 1.5), ('さん', 10.0, 10.5)])
    cues = [cue(10.0, 10.5, 'さん'), cue(1.0, 1.5, 'いち')]
    alignments = map_cues(cues, result)
    assert [a.cue_index for a in alignments] == [0, 1]
    assert alignments[0].status == STATUS_MATCH
    assert alignments[0].start == pytest.approx(10.0, abs=0.01)
    assert alignments[1].status == STATUS_MATCH
    assert alignments[1].start == pytest.approx(1.0, abs=0.01)


def test_cursor_prevents_stealing_the_same_audio_twice():
    result = make_result([('はい', 1.0, 2.0)])
    alignments = map_cues([
        cue(1.0, 2.0, 'はい'),
        cue(2.5, 3.5, 'はい'),
    ], result)
    assert alignments[0].status == STATUS_MATCH
    assert alignments[1].status in (STATUS_NO_SPEECH, STATUS_MISSING_ASR,
                                    STATUS_GARBLED, STATUS_EXTRA_TEXT)


def test_global_offset_is_absorbed_by_the_search_window():
    result = make_result([('おはよう', 1.0, 1.8), ('こんばんは', 10.0, 11.0)])
    alignments = map_cues([
        cue(3.0, 3.8, 'おはよう'),
        cue(12.0, 13.0, 'こんばんは'),
    ], result)
    assert all(a.status == STATUS_MATCH for a in alignments)
    assert alignments[0].start == pytest.approx(1.0, abs=0.01)
    assert alignments[1].start == pytest.approx(10.0, abs=0.01)


def test_one_character_cue_is_never_high_confidence():
    result = make_result([('あ', 1.0, 1.2)])
    al = map_cues([cue(1.0, 1.2, 'あ')], result)[0]
    assert al.status == STATUS_MATCH
    assert al.confidence != 'high'
    assert al.action == ACTION_KEEP


# ═══════════════════════════════════════════════════════════════
# Config / report
# ═══════════════════════════════════════════════════════════════

def test_short_cues_can_be_kept_but_not_trusted():
    result = make_result([('うん', 1.0, 1.3)])
    cfg = AlignmentConfig(clean_boundary=0.15)
    al = map_cues([cue(1.0, 1.3, 'うん')], result, config=cfg)[0]
    assert al.confidence in ('medium', 'low')


def test_map_report_notes_segment_only_asr():
    result = ASRResult(text='こんにちは',
                       segments=[Segment('こんにちは', 1.0, 2.0)],
                       engine='whisper')
    report = map_report([cue(1.0, 2.0, 'こんにちは')], result)
    assert report.engine == 'whisper'
    assert report.alignments[0].status == STATUS_MATCH
    assert report.warnings and 'segment' in report.warnings[0]


def test_map_report_without_timestamps_warns():
    result = ASRResult(text='なし', engine='fake')
    report = map_report([cue(1.0, 2.0, 'なし')], result)
    assert report.alignments[0].status == STATUS_NO_ASR
    assert report.warnings


def test_map_report_surfaces_degraded_token_quality():
    result = make_result([('あ', 1.0, 1.0)] * 4)
    report = map_report([cue(1.0, 2.0, 'あ')], result)
    assert report.timeline_source == 'tokens'
    assert report.timeline_quality['degraded'] is True
    assert any('degraded' in w for w in report.warnings)
    payload = report.to_dict()
    assert payload['timeline']['source'] == 'tokens'
    assert payload['timeline']['quality']['zero_units'] == 4


def test_map_report_records_segment_fallback():
    result = make_result(tokens=[('あ', 1.0, 1.0)] * 10,
                         segments=[('あ' * 10, 1.0, 3.0)])
    report = map_report([cue(1.0, 3.0, 'あ' * 10)], result)
    assert report.timeline_source == 'segments'
    assert report.timeline_fallback == 'tokens'
    assert any('fell back' in w for w in report.warnings)
    assert report.to_dict()['timeline']['fallback'] == 'tokens'


def test_alignment_records_timeline_evidence():
    result = make_result([('あ', 1.0, 1.0)] * 4)
    al = map_cues([cue(1.0, 2.0, 'あ')], result)[0]
    assert al.signals['timeline_source'] == 'tokens'
    assert al.signals['timeline_degraded'] is True
    assert al.signals['aligned_duration_s'] == 0.0
    assert any('zero duration' in reason for reason in al.reasons)
