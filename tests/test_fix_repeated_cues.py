"""Tests for fix_repeated_cues.py — similarity and repeat grouping."""

from fix_repeated_cues import (
    _char_set, detect_similar_groups, text_similarity,
)


def cue(text, start=0.0, end=1.0):
    return {'text': text, 'start_s': start, 'end_s': end}


def test_text_similarity():
    assert text_similarity('ごめん', 'ごめん') == 1.0
    assert text_similarity('私に何のご用ですか', '私に何のご用ですが') > 0.85
    assert text_similarity('おはよう', 'さようなら') < 0.5


def test_detect_similar_groups_pairs():
    cues = [cue('ああああ'), cue('ああああ'), cue('全然違う')]
    groups = detect_similar_groups(cues)
    assert groups == [[0, 1]]


def test_detect_similar_groups_merges_adjacent_runs():
    cues = [cue('ああああ')] * 4
    groups = detect_similar_groups(cues)
    assert groups == [[0, 1, 2, 3]]


def test_detect_similar_groups_respects_min_size():
    cues = [cue('ああああ'), cue('ああああ'), cue('違う')]
    assert detect_similar_groups(cues, min_group_size=3) == []


def test_detect_similar_groups_separates_far_runs():
    cues = [
        cue('ああああ'), cue('ああああ'),
        cue('全然違う一'), cue('全然違う二'),
        cue('いいいい'), cue('いいいい'),
    ]
    groups = detect_similar_groups(cues)
    assert [0, 1] in groups
    assert [4, 5] in groups
    assert all(2 not in g and 3 not in g for g in groups)


def test_detect_similar_groups_short_input():
    assert detect_similar_groups([cue('あ')]) == []


def test_char_set_ignores_punctuation_and_space():
    assert _char_set('あ、い。う 1！') == {'あ', 'い', 'う', '1'}
