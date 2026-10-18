"""Tests for lib.alignment.diagnostics — signals, confidence, statistics."""

import pytest

from lib.alignment.base import (
    ACTION_DELETE_CANDIDATE, ACTION_FIX_TIME, ACTION_KEEP,
    ACTION_REPLACE_TEXT, ACTION_REVIEW_AI, ACTION_REVIEW_HUMAN,
    ACTION_SPLIT_CANDIDATE, AlignmentConfig, CueAlignment,
    STATUS_GARBLED, STATUS_MATCH, STATUS_NO_ASR, STATUS_NO_SPEECH,
)
from lib.alignment.diagnostics import (
    annotate, boundary_stats, confidence_for, lang_profile, repeat_score,
    speech_coverage, summarize,
)
from lib.alignment.timeline import timeline_from_cues


# ═══════════════════════════════════════════════════════════════
# Signals
# ═══════════════════════════════════════════════════════════════

def test_repeat_score_flags_loops_and_ignores_normal_text():
    assert repeat_score('今日はいい天気ですね') == 0.0
    assert repeat_score('あ' * 12) > 0.8
    assert repeat_score('はいはいはいはいはいはい') > 0.3
    assert repeat_score('あい') == 0.0


def test_lang_profile_identifies_scripts():
    ja = lang_profile('お前何してんだよ')
    assert ja['lang_score'] == pytest.approx(1.0)
    assert ja['kana_ratio'] > 0.5

    latin = lang_profile('hello world')
    assert latin['lang_score'] == 0.0
    assert latin['latin_ratio'] == pytest.approx(1.0)

    cyrillic = lang_profile('Привет мир')
    assert cyrillic['cyrillic_ratio'] == pytest.approx(1.0)
    assert cyrillic['lang_score'] == 0.0


def test_speech_coverage_merges_spans_and_reports_gaps():
    tl = timeline_from_cues([
        {'start_s': 0.0, 'end_s': 1.0, 'text': 'ああ'},
        {'start_s': 2.0, 'end_s': 3.0, 'text': 'いい'},
    ])
    assert speech_coverage(tl, 0.0, 1.0) == pytest.approx(1.0)
    assert speech_coverage(tl, 0.5, 2.5) == pytest.approx(0.5)
    assert speech_coverage(tl, 5.0, 6.0) == 0.0
    assert speech_coverage(tl, 1.0, 1.0) == 0.0


def test_internal_pause_detects_the_gap_inside_a_span():
    from lib.alignment.diagnostics import internal_pause
    tl = timeline_from_cues([
        {'start_s': 0.0, 'end_s': 1.0, 'text': 'ああ'},
        {'start_s': 1.6, 'end_s': 2.0, 'text': 'いい'},
    ])
    assert internal_pause(tl, 0, 4) == pytest.approx(0.6, abs=0.01)


# ═══════════════════════════════════════════════════════════════
# Confidence
# ═══════════════════════════════════════════════════════════════

def make_alignment(**kwargs) -> CueAlignment:
    base = dict(cue_index=0, status=STATUS_MATCH, similarity=1.0,
                cue_chars=8, matched_chars=8,
                cue_start=10.0, cue_end=12.0, start=10.0, end=12.0,
                asr_text='お前何してんだよ')
    base.update(kwargs)
    return CueAlignment(**base)


def test_clean_match_is_high_confidence():
    cfg = AlignmentConfig()
    assert confidence_for(make_alignment(), cfg) == 'high'


def test_boundary_drift_downgrades_to_medium():
    cfg = AlignmentConfig()
    al = make_alignment(start=10.3)
    assert confidence_for(al, cfg) == 'medium'


def test_heavy_drift_or_bad_text_is_low():
    cfg = AlignmentConfig()
    assert confidence_for(make_alignment(start=20.0), cfg) == 'low'
    assert confidence_for(
        make_alignment(signals={'repeat_score': 1.0}), cfg) == 'low'


def test_hallucination_signals_downgrade_confidence():
    cfg = AlignmentConfig()
    al = make_alignment(signals={'repeat_score': 0.6})
    assert confidence_for(al, cfg) == 'low'
    al = make_alignment(signals={'lang_score': 0.0})
    assert confidence_for(al, cfg) == 'medium'


def test_short_cue_never_high():
    cfg = AlignmentConfig()
    al = make_alignment(cue_chars=1, matched_chars=1)
    assert confidence_for(al, cfg) == 'medium'


def test_status_level_confidence():
    cfg = AlignmentConfig()
    assert confidence_for(
        CueAlignment(0, STATUS_GARBLED, similarity=0.7,
                     asr_text='お前何してんだよ'), cfg) == 'medium'
    assert confidence_for(
        CueAlignment(0, STATUS_GARBLED, similarity=0.2), cfg) == 'low'
    assert confidence_for(
        CueAlignment(0, STATUS_NO_SPEECH), cfg) == 'medium'
    assert confidence_for(
        CueAlignment(0, STATUS_NO_ASR), cfg) == 'low'


# ═══════════════════════════════════════════════════════════════
# Actions
# ═══════════════════════════════════════════════════════════════

def test_action_keep_fix_review():
    cfg = AlignmentConfig()
    assert annotate(make_alignment(), cfg).action == ACTION_KEEP
    drift = annotate(make_alignment(start=10.2), cfg)
    assert drift.action == ACTION_FIX_TIME
    huge = annotate(make_alignment(start=18.0), cfg)
    assert huge.action == ACTION_REVIEW_AI


def test_action_delete_is_candidate_only():
    cfg = AlignmentConfig()
    al = annotate(CueAlignment(0, STATUS_NO_SPEECH), cfg)
    assert al.action == ACTION_DELETE_CANDIDATE


def test_action_replace_text_for_plausible_garbled_cue():
    cfg = AlignmentConfig()
    al = annotate(CueAlignment(0, STATUS_GARBLED, similarity=0.4,
                               asr_text='お前何してんだよ',
                               signals={'lang_score': 1.0}), cfg)
    assert al.action == ACTION_REPLACE_TEXT


def test_action_split_for_long_paused_cue():
    cfg = AlignmentConfig()
    al = annotate(CueAlignment(
        0, STATUS_GARBLED, similarity=0.6, asr_text='長い台詞',
        signals={'lang_score': 1.0, 'internal_pause_s': 0.9,
                 'cue_duration': 9.0, 'cue_lines': 3}), cfg)
    assert al.action == ACTION_SPLIT_CANDIDATE


def test_action_review_human_without_asr():
    cfg = AlignmentConfig()
    al = annotate(CueAlignment(0, STATUS_NO_ASR), cfg)
    assert al.action == ACTION_REVIEW_HUMAN


# ═══════════════════════════════════════════════════════════════
# Statistics
# ═══════════════════════════════════════════════════════════════

def test_boundary_stats_percentiles():
    alignments = [
        make_alignment(cue_index=0, start=10.05, end=11.95),
        make_alignment(cue_index=1, start=10.20, end=11.80),
        make_alignment(cue_index=2, start=10.40, end=11.60),
    ]
    stats = boundary_stats(alignments)
    assert stats['start']['n'] == 3
    assert stats['start']['mae_ms'] == pytest.approx(216.7, abs=1.0)
    assert stats['start']['p50_ms'] == pytest.approx(200.0, abs=1.0)
    assert stats['start']['p90_ms'] == pytest.approx(360.0, abs=1.0)
    assert stats['start']['hit_100'] == pytest.approx(1 / 3, rel=1e-3)


def test_boundary_stats_empty():
    stats = boundary_stats([])
    assert stats['all']['n'] == 0
    assert stats['all']['mae_ms'] == 0.0


def test_summarize_counts_and_clean_ratio():
    alignments = [
        annotate(make_alignment(), AlignmentConfig()),
        annotate(CueAlignment(1, STATUS_NO_SPEECH), AlignmentConfig()),
    ]
    summary = summarize(alignments)
    assert summary['cues'] == 2
    assert summary['clean'] == 1
    assert summary['clean_ratio'] == 0.5
    assert summary['status'][STATUS_MATCH] == 1
    assert summary['actions'][ACTION_KEEP] == 1
