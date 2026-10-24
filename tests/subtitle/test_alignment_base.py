"""Tests for lib.alignment.base — contracts, normalisation, statuses."""

import pytest

from lib.alignment.base import (
    ACTION_KEEP, ALIGNMENT_VERSION, AlignmentConfig, AlignmentReport,
    CueAlignment, STATUSES, STATUS_EMPTY_TEXT, STATUS_MATCH,
    STATUS_NO_ASR, STATUS_NO_SPEECH, normalize_text, similarity_ratio,
    strip_tags,
)


# ═══════════════════════════════════════════════════════════════
# Normalisation
# ═══════════════════════════════════════════════════════════════

def test_strip_tags_removes_srt_and_ass_markup():
    assert strip_tags('<i>はい</i>') == 'はい'
    assert strip_tags(r'{\fad(200,200)}おはよう') == 'おはよう'
    assert strip_tags('{\\pos(10,20)}セリフ') == 'セリフ'


def test_normalize_text_drops_punct_space_and_markup():
    raw = r'{\fad(200,200)}お前、何してんだよ！  <i>早く</i>行くぞ。'
    assert normalize_text(raw) == 'お前何してんだよ早く行くぞ'


def test_normalize_text_nfkc_and_casefold():
    assert normalize_text('ﾊﾛｰ') == 'ハロー'
    assert normalize_text('ABC・ｱｲｳ') == 'abcアイウ'


def test_normalize_text_drops_ff_subtitle_labels():
    assert normalize_text('【ナレーション】昔々あるところに') == '昔々あるところに'


def test_normalize_text_keeps_cyrillic_and_digits():
    assert normalize_text('Привет, мир! 123') == 'приветмир123'


def test_normalize_text_empty_input():
    assert normalize_text('') == ''
    assert normalize_text('   \n ') == ''


def test_similarity_ratio_bounds():
    assert similarity_ratio('あいう', 'あいう') == 1.0
    assert similarity_ratio('', 'あいう') == 0.0
    assert 0.0 < similarity_ratio('あいうえお', 'あいうかき') < 1.0


# ═══════════════════════════════════════════════════════════════
# CueAlignment
# ═══════════════════════════════════════════════════════════════

def make_alignment(**kwargs) -> CueAlignment:
    base = dict(cue_index=0, status=STATUS_MATCH, similarity=1.0,
                cue_start=10.0, cue_end=12.0, start=10.1, end=11.9)
    base.update(kwargs)
    return CueAlignment(**base)


def test_cue_alignment_derived_errors():
    al = make_alignment()
    assert al.cue_duration == pytest.approx(2.0)
    assert al.start_error == pytest.approx(0.1)
    assert al.end_error == pytest.approx(0.1)
    assert al.max_boundary_error == pytest.approx(0.1)
    assert al.aligned_duration == pytest.approx(1.8)


def test_cue_alignment_is_clean_requires_high_confidence():
    al = make_alignment()
    assert not al.is_clean
    al.confidence = 'high'
    assert al.is_clean
    al.status = 'garbled'
    assert not al.is_clean


def test_cue_alignment_roundtrip_preserves_fields():
    al = make_alignment(confidence='high', action=ACTION_KEEP,
                        asr_span=(3, 7), char_span=(10, 20),
                        asr_text='お前何してんだよ',
                        reasons=['boundary drift 20 ms'],
                        signals={'speech_coverage': 0.98})
    data = al.to_dict()
    assert data['schema_version'] == ALIGNMENT_VERSION
    assert data['asr_span'] == [3, 7]
    restored = CueAlignment.from_dict(data)
    assert restored.to_dict() == data


def test_cue_alignment_handles_missing_times():
    al = CueAlignment(cue_index=4, status=STATUS_NO_SPEECH)
    assert al.start_error is None
    assert al.end_error is None
    assert al.max_boundary_error == 0.0
    assert 'start' not in al.to_dict()


# ═══════════════════════════════════════════════════════════════
# Report + config
# ═══════════════════════════════════════════════════════════════

def test_report_counts_and_summary():
    report = AlignmentReport(alignments=[
        make_alignment(status=STATUS_MATCH, confidence='high',
                       action=ACTION_KEEP),
        make_alignment(cue_index=1, status=STATUS_NO_SPEECH,
                       confidence='medium', action='delete_candidate'),
        make_alignment(cue_index=2, status=STATUS_MATCH,
                       confidence='high', action=ACTION_KEEP),
    ], engine='qwen3', model='Qwen/Qwen3-ASR-1.7B')
    assert report.counts() == {STATUS_MATCH: 2, STATUS_NO_SPEECH: 1}
    assert report.actions()[ACTION_KEEP] == 2
    summary = report.summary()
    assert summary['cues'] == 3
    assert summary['clean'] == 2
    assert summary['clean_ratio'] == pytest.approx(2 / 3, rel=1e-3)


def test_report_roundtrip():
    report = AlignmentReport(alignments=[make_alignment()], engine='qwen3')
    restored = AlignmentReport.from_dict(report.to_dict())
    assert restored.engine == 'qwen3'
    assert len(restored.alignments) == 1
    assert restored.alignments[0].cue_index == 0


def test_config_defaults_are_conservative():
    cfg = AlignmentConfig()
    assert cfg.allow_text_replace is False
    assert cfg.clean_boundary == pytest.approx(0.15)
    assert cfg.max_time_shift > cfg.time_fix_threshold
    assert cfg.match_similarity > cfg.garbled_similarity


def test_status_and_action_vocabularies_are_exported():
    assert STATUS_MATCH in STATUSES
    assert STATUS_EMPTY_TEXT in STATUSES
    assert STATUS_NO_ASR in STATUSES
