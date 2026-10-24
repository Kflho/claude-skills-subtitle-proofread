"""Tests for fix.align_repair — proposals, safe apply, cue re-alignment."""

import pytest

from fix.align_repair import (
    PlannedRepair, apply_repairs, estimate_global_offset, plan_repairs,
    realign_cue_window, shift_cues, summarize_plan,
)
from lib.alignment.base import (
    ACTION_DELETE_CANDIDATE, ACTION_FIX_TIME, ACTION_KEEP,
    ACTION_REPLACE_TEXT, ACTION_REVIEW_AI, AlignmentConfig, CueAlignment,
    STATUS_GARBLED, STATUS_MATCH, STATUS_NO_SPEECH,
)
from lib.alignment.diagnostics import annotate
from lib.asr.base import AlignResult, Token
from lib.subtitle_io import format_tc


def cue(start, end, text):
    return {'start_s': start, 'end_s': end, 'text': text,
            'start': format_tc(start), 'end': format_tc(end)}


def aligned(cue_index, cue_start, cue_end, *, start=None, end=None,
            status=STATUS_MATCH, **kwargs):
    base = dict(cue_index=cue_index, status=status, similarity=1.0,
                cue_start=cue_start, cue_end=cue_end,
                start=cue_start if start is None else start,
                end=cue_end if end is None else end,
                confidence='high', asr_text='お前何してんだよ')
    base.update(kwargs)
    return annotate(CueAlignment(**base), AlignmentConfig())


def test_clean_cue_produces_no_repair():
    plan = plan_repairs([cue(1.0, 2.0, 'はい')], [aligned(0, 1.0, 2.0)])
    assert plan == []


def test_drift_becomes_auto_time_fix():
    cues = [cue(10.0, 12.0, 'お前何してんだよ')]
    al = aligned(0, 10.0, 12.0, start=10.2)
    assert al.action == ACTION_FIX_TIME

    plan = plan_repairs(cues, [al])
    assert len(plan) == 1
    entry = plan[0]
    assert entry.auto is True
    assert entry.new_start == pytest.approx(10.2)
    assert entry.to_dict()['delta_start'] == pytest.approx(0.2)


def test_time_fix_that_overlaps_previous_cue_downgrades_to_review():
    cues = [cue(0.0, 5.0, 'まえ'), cue(5.0, 7.0, 'あと')]
    alignments = [aligned(0, 0.0, 5.0), aligned(1, 5.0, 7.0, start=4.8)]
    plan = plan_repairs(cues, alignments)
    assert plan[0].action == ACTION_REVIEW_AI
    assert plan[0].auto is False
    assert 'overlaps cue #0' in plan[0].reasons[-1]


def test_preexisting_overlap_does_not_block_time_fix():
    cues = [cue(0.0, 5.0, 'まえ'), cue(4.0, 7.0, 'あと')]
    alignments = [aligned(0, 0.0, 5.0), aligned(1, 4.0, 7.0, start=4.2)]
    plan = plan_repairs(cues, alignments)
    assert plan[0].action == ACTION_FIX_TIME
    assert plan[0].auto is True


def test_display_linger_keeps_the_end_and_fixes_only_the_start():
    cues = [cue(10.0, 12.0, 'お前何してんだよ')]
    # Start matches; the end runs 0.6 s past the ASR speech (reading time).
    linger = aligned(0, 10.0, 12.0, end=11.4)
    assert linger.action == ACTION_KEEP
    assert plan_repairs(cues, [linger]) == []

    # Start drifts 0.3 s but the end is still display linger: move the start,
    # keep the original end instead of truncating the subtitle.
    drift = aligned(0, 10.0, 12.0, start=10.3, end=11.4)
    assert drift.action == ACTION_FIX_TIME
    plan = plan_repairs(cues, [drift])
    assert plan[0].auto is True
    assert plan[0].new_start == pytest.approx(10.3)
    assert plan[0].new_end == pytest.approx(12.0)
    assert any('display linger' in r for r in plan[0].reasons)


def test_negative_end_error_is_not_display_linger():
    # Speech continues past the cue end: that is a real truncation.
    cues = [cue(10.0, 12.0, 'お前何してんだよ')]
    al = aligned(0, 10.0, 12.0, end=12.4)
    assert not al.end_is_display_linger(AlignmentConfig())
    assert al.action == ACTION_FIX_TIME
    plan = plan_repairs(cues, [al])
    assert plan[0].new_end == pytest.approx(12.4)


# ═══════════════════════════════════════════════════════════════
# Joint neighbour resolution (dense subtitles)
# ═══════════════════════════════════════════════════════════════

def test_adjacent_moving_cues_can_take_each_others_old_window():
    """Dense cues: each proposal overlaps the neighbour's *old* window only."""
    cues = [cue(10.0, 12.0, 'いち'), cue(12.0, 14.0, 'に')]
    alignments = [
        aligned(0, 10.0, 12.0, start=10.6, end=12.4),
        aligned(1, 12.0, 14.0, start=12.6, end=14.0),
    ]

    plan = plan_repairs(cues, alignments)

    assert [e.action for e in plan] == [ACTION_FIX_TIME, ACTION_FIX_TIME]
    assert [e.auto for e in plan] == [True, True]
    assert plan[0].new_end == pytest.approx(12.4)
    assert plan[1].new_start == pytest.approx(12.6)


def test_run_of_three_dense_cues_moves_together():
    cues = [cue(0.0, 2.0, 'a'), cue(2.0, 4.0, 'b'), cue(4.0, 6.0, 'c')]
    alignments = [
        aligned(0, 0.0, 2.0, start=0.5, end=2.5),
        aligned(1, 2.0, 4.0, start=2.5, end=4.5),
        aligned(2, 4.0, 6.0, start=4.5, end=6.5),
    ]

    plan = plan_repairs(cues, alignments)

    assert all(e.auto for e in plan)
    assert [e.new_start for e in plan] == [0.5, 2.5, 4.5]


def test_stationary_neighbour_still_blocks_one_fix_only():
    cues = [cue(0.0, 5.0, 'まえ'), cue(5.0, 7.0, 'あと'), cue(7.0, 9.0, 'さらに')]
    alignments = [
        aligned(0, 0.0, 5.0),                                # stays put
        aligned(1, 5.0, 7.0, start=4.5, end=7.0),            # hits cue #0
        aligned(2, 7.0, 9.0, start=7.2, end=9.0),            # clean, moves
    ]

    plan = plan_repairs(cues, alignments)
    by_index = {e.cue_index: e for e in plan}

    assert by_index[1].action == ACTION_REVIEW_AI
    assert by_index[1].auto is False
    assert 'overlaps cue #0' in by_index[1].reasons[-1]
    assert by_index[1].new_start is None
    assert by_index[2].auto is True


def test_conflicting_proposals_downgrade_both():
    cues = [cue(10.0, 12.0, 'いち'), cue(12.0, 14.0, 'に')]
    alignments = [
        aligned(0, 10.0, 12.0, start=10.6, end=12.4),
        aligned(1, 12.0, 14.0, start=11.8, end=13.6),
    ]

    plan = plan_repairs(cues, alignments)

    assert [e.action for e in plan] == [ACTION_REVIEW_AI, ACTION_REVIEW_AI]
    assert [e.auto for e in plan] == [False, False]
    assert 'overlaps cue #1' in plan[0].reasons[-1]
    assert 'overlaps cue #0' in plan[1].reasons[-1]


def test_preexisting_overlap_lets_both_dense_cues_move():
    cues = [cue(0.0, 5.0, 'まえ'), cue(4.0, 7.0, 'あと')]
    alignments = [
        aligned(0, 0.0, 5.0, start=0.2, end=5.2),
        aligned(1, 4.0, 7.0, start=4.2, end=7.2),
    ]

    plan = plan_repairs(cues, alignments)

    assert [e.auto for e in plan] == [True, True]


def test_moving_cue_blocked_by_unmatched_kept_neighbour():
    """A cue with no alignment (ACTION_KEEP path) still protects its window."""
    cues = [cue(0.0, 5.0, 'まえ'), cue(5.0, 7.0, 'あと')]
    alignments = [aligned(1, 5.0, 7.0, start=4.6, end=7.0)]

    plan = plan_repairs(cues, alignments)

    assert plan[0].cue_index == 1
    assert plan[0].action == ACTION_REVIEW_AI
    assert 'overlaps cue #0' in plan[0].reasons[-1]


def test_min_duration_is_enforced_before_overlap_checks():
    # The ASR speech ends *after* the cue (negative end error), so this is
    # not display linger; the short span is clamped to min_cue_duration.
    cues = [cue(10.0, 11.05, 'いち')]
    alignments = [aligned(0, 10.0, 11.05, start=11.0, end=11.1)]

    plan = plan_repairs(cues, alignments)

    assert plan[0].auto is True
    assert plan[0].new_end - plan[0].new_start == pytest.approx(0.3)


def test_time_fix_beyond_max_shift_downgrades_to_review():
    cues = [cue(10.0, 12.0, 'お前何してんだよ')]
    al = aligned(0, 10.0, 12.0, start=16.0, end=18.0)
    assert al.action == ACTION_REVIEW_AI
    plan = plan_repairs(cues, [al])
    assert plan[0].auto is False


def test_delete_candidate_is_never_auto():
    cues = [cue(10.0, 12.0, 'さようなら')]
    al = annotate(CueAlignment(0, STATUS_NO_SPEECH, cue_start=10.0,
                               cue_end=12.0), AlignmentConfig())
    plan = plan_repairs(cues, [al])
    assert plan[0].action == ACTION_DELETE_CANDIDATE
    assert plan[0].auto is False
    assert 'never automatic' in plan[0].reasons[-1]


def test_replace_text_requires_opt_in():
    cues = [cue(5.0, 6.4, 'おまえなにしてんだ')]
    al = aligned(0, 5.0, 6.4, status=STATUS_GARBLED, similarity=0.4,
                 confidence='low')
    al.action = ACTION_REPLACE_TEXT

    plan = plan_repairs(cues, [al])
    assert plan[0].action == ACTION_REPLACE_TEXT
    assert plan[0].auto is False
    assert plan[0].new_text == 'お前何してんだよ'

    cfg = AlignmentConfig(allow_text_replace=True)
    plan = plan_repairs(cues, [al], config=cfg)
    assert plan[0].auto is True


def test_apply_repairs_updates_timecodes_and_leaves_input_untouched():
    cues = [cue(10.0, 12.0, 'お前何してんだよ')]
    plan = plan_repairs(cues, [aligned(0, 10.0, 12.0, start=10.2,
                                       end=11.9)])
    new_cues, stats = apply_repairs(cues, plan)

    assert stats['fix_time'] == 1
    assert cues[0]['start_s'] == 10.0          # original untouched
    assert new_cues[0]['start_s'] == pytest.approx(10.2)
    assert new_cues[0]['end_s'] == pytest.approx(11.9)
    assert new_cues[0]['start'] == '00:00:10,200'
    assert new_cues[0]['end'] == '00:00:11,900'
    assert new_cues[0]['text'] == 'お前何してんだよ'


def test_apply_repairs_ignores_auto_text_without_flag():
    cues = [cue(5.0, 6.4, 'おまえなにしてんだ')]
    plan = [PlannedRepair(cue_index=0, action=ACTION_REPLACE_TEXT, auto=True,
                          new_text='お前何してんだよ')]
    new_cues, stats = apply_repairs(cues, plan)
    assert stats['replace_text'] == 0
    assert stats['skipped'] == 1
    assert new_cues[0]['text'] == 'おまえなにしてんだ'

    cfg = AlignmentConfig(allow_text_replace=True)
    new_cues, stats = apply_repairs(cues, plan, config=cfg)
    assert stats['replace_text'] == 1
    assert new_cues[0]['text'] == 'お前何してんだよ'


def test_apply_repairs_keeps_non_auto_entries_for_review():
    cues = [cue(1.0, 2.0, 'はい')]
    plan = [PlannedRepair(cue_index=0, action=ACTION_REVIEW_AI, auto=False)]
    new_cues, stats = apply_repairs(cues, plan)
    assert stats['manual_review'] == 1
    assert new_cues == cues


def test_summarize_plan():
    plan = [
        PlannedRepair(0, ACTION_FIX_TIME, auto=True),
        PlannedRepair(1, ACTION_FIX_TIME, auto=True),
        PlannedRepair(2, ACTION_REVIEW_AI, auto=False),
    ]
    stats = summarize_plan(plan)
    assert stats['total'] == 3
    assert stats['auto'] == 2
    assert stats['review'] == 1
    assert stats['actions'][ACTION_FIX_TIME] == 2


# ═══════════════════════════════════════════════════════════════
# Step 6: re-align one cue's audio window
# ═══════════════════════════════════════════════════════════════

class FakeAligner:
    name = 'fake-aligner'

    def __init__(self, tokens):
        self.tokens = tokens
        self.calls = []

    def align(self, audio_path, text, *, language='ja'):
        self.calls.append((audio_path, text, language))
        return AlignResult(text=text, language=language, tokens=self.tokens)


def test_realign_cue_window_shifts_tokens_to_absolute_time(monkeypatch,
                                                          tmp_path):
    commands = []

    def fake_ffmpeg(args, **kwargs):
        commands.append(args)
        # simulate ffmpeg writing the clip
        with open(args[-1], 'wb') as handle:
            handle.write(b'RIFF')

    monkeypatch.setattr('lib.subprocess_utils.run_ffmpeg', fake_ffmpeg)
    aligner = FakeAligner([Token('お前', 0.0, 0.2),
                           Token('何してんだよ', 0.2, 1.4)])

    result = realign_cue_window('ep.mkv', 'お前何してんだよ', 10.0, 12.0,
                                aligner=aligner, pad=0.15,
                                workdir=str(tmp_path))

    assert commands and '-ss' in commands[0]
    ss_index = commands[0].index('-ss')
    assert float(commands[0][ss_index + 1]) == pytest.approx(9.85)
    assert result.tokens[0].start == pytest.approx(9.85)
    assert result.tokens[-1].end == pytest.approx(11.25)
    assert result.meta['window_start'] == pytest.approx(9.85)
    assert list(tmp_path.iterdir()) == []     # temp wav cleaned up


def test_realign_cue_window_rejects_bad_window():
    with pytest.raises(ValueError):
        realign_cue_window('ep.mkv', 'はい', 5.0, 5.0, aligner=FakeAligner([]))


# ═══════════════════════════════════════════════════════════════
# Global offset detection
# ═══════════════════════════════════════════════════════════════

def _offset_alignments(n=30, delta=0.5, jitter=0.0):
    return [
        aligned(i, i * 3.0, i * 3.0 + 2.0,
                start=i * 3.0 + delta + (jitter if i % 2 else -jitter))
        for i in range(n)
    ]


def test_estimate_global_offset_detects_consistent_shift():
    al = _offset_alignments(30, delta=0.5)
    assert estimate_global_offset(al) == pytest.approx(0.5)


def test_estimate_global_offset_rejects_small_or_noisy_shifts():
    assert estimate_global_offset(_offset_alignments(30, delta=0.1)) is None
    assert estimate_global_offset(_offset_alignments(30, delta=0.5,
                                                     jitter=0.45)) is None
    assert estimate_global_offset(_offset_alignments(10, delta=0.5)) is None


def test_shift_cues_moves_timecodes_and_clamps_at_zero():
    cues = [cue(1.0, 2.0, 'はい')]
    shifted = shift_cues(cues, -0.5)
    assert shifted[0]['start_s'] == 0.5
    assert shifted[0]['end_s'] == pytest.approx(1.5)
    assert shifted[0]['start'] == '00:00:00,500'
    assert shifted[0]['end'] == '00:00:01,500'
    assert cues[0]['start_s'] == 1.0          # original untouched

    clamped = shift_cues(cues, -2.0)
    assert clamped[0]['start_s'] == 0.0
    assert clamped[0]['end_s'] == pytest.approx(1.0)
