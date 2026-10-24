"""Tests for the direct CC→FA grouping/masking helpers (no model)."""

import numpy as np

from eval.cc_fa_align import group_cues, mask_speech


def _cue(start, end, text='あ'):
    return {'start_s': start, 'end_s': end, 'text': text}


def test_group_cues_limits_window():
    cues = [_cue(i * 10.0, i * 10.0 + 2.0) for i in range(10)]
    groups = group_cues(cues, max_sec=30.0, pad=0.0)
    assert len(groups) >= 3
    for group in groups:
        assert group[-1]['end_s'] - group[0]['start_s'] <= 30.0


def test_mask_speech_zeros_outside_intervals():
    data = np.ones(1000, dtype=np.int16)
    out = mask_speech(data, sr=100, speech=[(2.0, 3.0)])
    assert out[200:300].sum() > 0
    assert out[:180].sum() == 0
    assert out[320:].sum() == 0
