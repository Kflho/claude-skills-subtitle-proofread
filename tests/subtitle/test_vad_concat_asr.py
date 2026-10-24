"""Tests for VAD speech-only concatenation helpers (no model)."""

import numpy as np

from eval.vad_concat_asr import build_concat, map_time, merge_speech


def test_merge_speech_merges_small_gaps_and_pads():
    events = [
        {'kind': 'speech', 'start': 1.0, 'end': 2.0},
        {'kind': 'speech', 'start': 2.1, 'end': 3.0},
        {'kind': 'music', 'start': 3.0, 'end': 4.0},
        {'kind': 'speech', 'start': 5.0, 'end': 6.0},
    ]
    spans = merge_speech(events, gap=0.2, pad=0.0, duration=10.0)
    assert spans == [(1.0, 3.0), (5.0, 6.0)]


def test_build_concat_and_map_time():
    data = np.arange(1000, dtype=np.int16)
    concat, mapping, duration = build_concat(data, sr=100, spans=[(2.0, 3.0),
                                                                  (5.0, 6.0)])
    assert duration == 2.0
    assert mapping == [(0.0, 1.0, 2.0, 3.0), (1.0, 2.0, 5.0, 6.0)]
    assert map_time(0.2, mapping) == 2.2
    assert map_time(1.2, mapping) == 5.2
    assert map_time(99.0, mapping) == 6.0
