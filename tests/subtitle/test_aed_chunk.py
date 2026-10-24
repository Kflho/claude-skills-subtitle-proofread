"""Tests for the AED-boundary chunk planner (no model/audio)."""

from eval.aed_chunk_transcribe import plan_cuts


def test_plan_cuts_prefers_speech_gap_near_target():
    hints = {
        'duration': 200.0,
        'events': [
            {'kind': 'speech', 'start': 0.0, 'end': 20.0},
            {'kind': 'speech', 'start': 21.0, 'end': 55.0},
            {'kind': 'speech', 'start': 80.0, 'end': 110.0},
            {'kind': 'speech', 'start': 112.0, 'end': 150.0},
            {'kind': 'speech', 'start': 160.0, 'end': 199.0},
        ],
    }
    cuts = plan_cuts(hints, target=60.0, search=15.0)
    assert len(cuts) >= 2
    # First cut should land in the 55–80 s gap, not inside speech.
    assert 55.0 <= cuts[0] <= 80.0


def test_plan_cuts_falls_back_to_target_without_gaps():
    hints = {
        'duration': 130.0,
        'events': [{'kind': 'speech', 'start': 0.0, 'end': 130.0}],
    }
    cuts = plan_cuts(hints, target=60.0, search=15.0)
    assert cuts == [60.0, 120.0]
