"""Tests for reuse/common.py — text cleaning, folding, bigram dice."""

from reuse.common import bg, clean, dice, make_fold, norm_only


def test_clean_strips_tags_and_converts_line_breaks():
    assert clean(r'{\i1}こんにちは{\i0}') == 'こんにちは'
    assert clean('<i>hello</i>') == 'hello'
    assert clean('one\\Ntwo') == 'one\ntwo'
    assert clean('  spaced  ') == 'spaced'


def test_norm_only_removes_punctuation_but_not_marks():
    assert norm_only('こんにちは、世界！') == 'こんにちは世界'
    assert norm_only('あ\nい') == 'あい'


def test_make_fold_unifies_variants():
    fold = make_fold([['サリー', 'サリ']])
    assert fold('サリーが来た') == fold('サリが来た')
    assert fold('サリーが来た') != fold('スミレが来た')


def test_make_fold_without_groups_normalizes_only():
    fold = make_fold()
    assert fold('あ、い') == 'あい'


def test_bg_and_dice():
    assert bg('') == set()
    assert bg('あ') == {'あ'}
    assert bg('あい') == {'あい'}
    assert bg('あいう') == {'あい', 'いう'}

    assert dice(set(), {'a'}) == 0.0
    assert dice({'a', 'b'}, {'a', 'b'}) == 1.0
    assert dice({'a', 'b'}, {'b', 'c'}) == 0.5
