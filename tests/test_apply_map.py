"""Tests for nouns/apply_map.py — replacement safety and file indexing."""

from pathlib import Path

from nouns.apply_map import (
    _apply_to_text, _build_repl, _effective_scope, _index_files,
)


# ═══════════════════════════════════════════════════════════════
# _build_repl: no double replacement
# ═══════════════════════════════════════════════════════════════

def test_suffix_variant_is_not_rewritten_inside_canonical():
    regex = _build_repl('茶水博士', '御茶水博士')
    text, n = regex.subn('御茶水博士', '御茶水博士')
    assert n == 0
    assert text == '御茶水博士'

    text, n = regex.subn('御茶水博士', '茶水博士')
    assert n == 1
    assert text == '御茶水博士'


def test_prefix_variant_does_not_extend_canonical():
    regex = _build_repl('乌拉尔', '乌拉尔号')
    assert regex.subn('乌拉尔号', '乌拉尔号')[1] == 0
    assert regex.subn('乌拉尔号', '乌拉尔')[1] == 1


def test_fuzzy_variant_guards_canonical_suffix():
    regex = _build_repl('多里安', '多利安博士')
    # "变体 + 标准名尾部" must not become "多利安博士博士"
    assert regex.subn('多利安博士', '多里安博士')[1] == 0
    text, n = regex.subn('多利安博士', '多里安')
    assert n == 1 and text == '多利安博士'


def test_apply_to_text_counts_all_replacements():
    repls = {
        _build_repl('サリ', 'サリー'): 'サリー',
        _build_repl('スミレちゃん', 'スミレ'): 'スミレ',
    }
    text, changed = _apply_to_text('サリとスミレちゃん', repls)
    assert text == 'サリーとスミレ'
    assert changed == 2


# ═══════════════════════════════════════════════════════════════
# Scope and indexing
# ═══════════════════════════════════════════════════════════════

def test_effective_scope_auto_uses_episode_count():
    assert _effective_scope({'scope': 'auto',
                             'episodes': ['EP001', 'EP002']}) == 'global'
    assert _effective_scope({'scope': 'auto',
                             'episodes': ['EP001']}) == 'per_episode'
    assert _effective_scope({'episodes': []}) == 'per_episode'


def test_effective_scope_explicit_wins():
    assert _effective_scope({'scope': 'per_episode',
                             'episodes': ['EP001', 'EP002']}) == 'per_episode'


def test_index_files_maps_episode_numbers(tmp_path):
    (tmp_path / 'Show - 001 - Title.srt').write_text('', encoding='utf-8')
    (tmp_path / 'Show - 002.ass').write_text('', encoding='utf-8')
    (tmp_path / 'notes.txt').write_text('', encoding='utf-8')

    index = _index_files(str(tmp_path))
    assert set(index) == {'EP001', 'EP002'}
    assert index['EP001'].endswith('Show - 001 - Title.srt')

    assert _index_files(str(tmp_path / 'missing')) == {}
