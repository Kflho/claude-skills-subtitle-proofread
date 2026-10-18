"""Tests for nouns/noun_checker.py — normalizers, tables, matching."""

import pytest

from nouns.noun_checker import (
    ChineseNormalizer, JapaneseExtractor, JapaneseNormalizer,
    _dedup_overlapping, match_candidates, parse_noun_table, text_similarity,
    get_normalizer,
)


# ═══════════════════════════════════════════════════════════════
# Normalizers
# ═══════════════════════════════════════════════════════════════

def test_japanese_normalizer_levels():
    assert JapaneseNormalizer.normalize('コーヒー') == 'コヒ'
    assert JapaneseNormalizer.normalize('キャ', 'medium') == 'キヤ'
    assert JapaneseNormalizer.normalize('ガ', 'lenient') == 'カ'
    assert JapaneseNormalizer.normalize('サリー', 'strict') == 'サリ'


def test_chinese_normalizer_strict_maps_traditional():
    out = ChineseNormalizer.normalize('龍')
    assert out == '龙'


def test_get_normalizer_factory():
    assert get_normalizer('ja') is JapaneseNormalizer
    assert get_normalizer('zh') is ChineseNormalizer
    assert get_normalizer('??') is JapaneseNormalizer


# ═══════════════════════════════════════════════════════════════
# Dedup / similarity
# ═══════════════════════════════════════════════════════════════

def test_dedup_keeps_longest_overlapping_candidate():
    candidates = [
        {'text': '茶水博士', 'start': 1},
        {'text': '御茶水博士', 'start': 0},
        {'text': '博士', 'start': 3},
        {'text': 'サリー', 'start': 10},
    ]
    kept = _dedup_overlapping(candidates)
    texts = [c['text'] for c in kept]
    assert '御茶水博士' in texts
    assert 'サリー' in texts
    assert '茶水博士' not in texts
    assert '博士' not in texts


def test_text_similarity_is_case_insensitive():
    assert text_similarity('ABC', 'abc') == 1.0
    assert text_similarity('サリー', 'サリ') > 0.5


# ═══════════════════════════════════════════════════════════════
# Noun table parsing
# ═══════════════════════════════════════════════════════════════

MD = """# Proper Nouns

| 日语 | 假名 | 说明 |
|---|---|---|
| サリー | さりー | 主角 |
| スミレ | すみれ | 朋友 |
"""


def test_parse_noun_table(tmp_path):
    path = tmp_path / 'proper-nouns.md'
    path.write_text(MD, encoding='utf-8')
    nouns = parse_noun_table(str(path))
    names = [n['name'] for n in nouns]
    assert names == ['サリー', 'スミレ']
    assert nouns[0]['reading'] == 'さりー'
    assert nouns[0]['name_norm'] == JapaneseNormalizer.normalize('サリー',
                                                                 'lenient')


def test_parse_noun_table_missing_returns_empty(tmp_path):
    assert parse_noun_table(str(tmp_path / 'nope.md')) == []


# ═══════════════════════════════════════════════════════════════
# Matching
# ═══════════════════════════════════════════════════════════════

def _nouns():
    return [{'name': 'サリー',
             'name_norm': JapaneseNormalizer.normalize('サリー', 'lenient')}]


def test_match_exact():
    result = match_candidates(
        [{'text': 'サリー', 'start': 0, 'type': 'calling'}],
        _nouns(), 'サリーが来た', JapaneseNormalizer)
    assert result[0]['status'] == 'exact'
    assert result[0]['similarity'] == 1.0


def test_match_normalized_variant():
    result = match_candidates(
        [{'text': 'サーリー', 'start': 0, 'type': 'calling'}],
        _nouns(), 'サーリーが来た', JapaneseNormalizer)
    assert result[0]['status'] == 'variant'
    assert result[0]['suggestion'] == 'サリー'


def test_match_fuzzy_and_unknown():
    fuzzy = match_candidates(
        [{'text': 'サリイ', 'start': 0, 'type': 'calling'}],
        _nouns(), 'サリイが来た', JapaneseNormalizer)
    assert fuzzy[0]['status'] in ('mismatch', 'unknown')

    unknown = match_candidates(
        [{'text': 'タロウ', 'start': 0, 'type': 'calling'}],
        _nouns(), 'タロウが来た', JapaneseNormalizer)
    assert unknown[0]['status'] == 'unknown'
    assert unknown[0]['matched'] is None


# ═══════════════════════════════════════════════════════════════
# Japanese extractor
# ═══════════════════════════════════════════════════════════════

def test_extract_katakana_with_honorific():
    candidates = JapaneseExtractor.extract('サリーちゃんが来た')
    texts = [c['text'] for c in candidates]
    assert 'サリーちゃん' in texts


def test_extract_calling_pattern():
    candidates = JapaneseExtractor.extract('おい、サリー！')
    texts = [c['text'] for c in candidates]
    assert 'サリー' in texts
