"""Tests for fix.align_repair — proposals, safe apply, cue re-alignment."""

import pytest

from fix.align_repair import (
    PlannedRepair, apply_repairs, plan_repairs, realign_cue_window,
    summarize_plan,
)
from lib.alignment.base import (
    ACTION_DELETE_CANDIDATE, ACTION_FIX_TIME,
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
