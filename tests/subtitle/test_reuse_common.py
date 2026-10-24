"""Tests for reuse/common.py — text cleaning, folding, bigram dice."""

import json

import pytest

from reuse import common
from reuse.common import (
    bg, clean, dice, group_hint, load_reference, load_target, load_variants,
    make_fold, norm_only, reference_files, resolve_tag_file,
)


def _srt(path, texts):
    blocks = []
    for i, text in enumerate(texts, 1):
        blocks.append(f'{i}\n00:00:{i * 2:02d},000 --> '
                      f'00:00:{i * 2 + 1:02d},000\n{text}\n')
    path.write_text('\n'.join(blocks), encoding='utf-8')
    return path


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


# ═══════════════════════════════════════════════════════════════
# variants
# ═══════════════════════════════════════════════════════════════

def test_load_variants_empty_and_missing(tmp_path):
    assert load_variants(None) == ([], [])

    with pytest.raises(SystemExit) as exc:
        load_variants(str(tmp_path / 'nope.json'))
    assert '--variants 文件不存在' in str(exc.value)


def test_load_variants_dict_groups_and_never_swap(tmp_path):
    path = tmp_path / 'variants.json'
    path.write_text(json.dumps({
        'groups': {'女主': ['瓦尔古蕾', '瓦尔Q蕾']},
        'never_swap': [['小瓦尔', '瓦尔古蕾'], ['bad'], ['a', 'b', 'c']],
    }, ensure_ascii=False), encoding='utf-8')

    groups, pairs = load_variants(str(path))
    assert groups == [['瓦尔古蕾', '瓦尔Q蕾']]
    assert pairs == [('小瓦尔', '瓦尔古蕾')]


def test_load_variants_list_groups(tmp_path):
    path = tmp_path / 'variants.json'
    path.write_text(json.dumps({'groups': ['A', 'B']}), encoding='utf-8')
    groups, pairs = load_variants(str(path))
    assert groups == ['A', 'B']
    assert pairs == []


# ═══════════════════════════════════════════════════════════════
# reference / target loading
# ═══════════════════════════════════════════════════════════════

def test_reference_files_missing_dir_and_glob(tmp_path):
    assert reference_files(str(tmp_path / 'missing')) == []

    _srt(tmp_path / 'EP001.srt', ['一'])
    (tmp_path / 'EP002.ass').write_text('', encoding='utf-8')
    (tmp_path / 'notes.txt').write_text('', encoding='utf-8')

    all_files = reference_files(str(tmp_path))
    assert [p.rsplit('\\', 1)[-1].rsplit('/', 1)[-1] for p in all_files] == [
        'EP001.srt', 'EP002.ass']
    assert [p.rsplit('\\', 1)[-1].rsplit('/', 1)[-1]
            for p in reference_files(str(tmp_path), '*.ass')] == ['EP002.ass']


def test_load_reference_assigns_gi_and_filters_styles(tmp_path, monkeypatch):
    _srt(tmp_path / 'EP001.srt', ['dummy'])
    cues = [
        {'text': '对白一', 'start': '00:00:01,000', 'start_s': 1.0,
         '_ass_dialogue': {'style': 'Default'}},
        {'text': '片头歌', 'start': '00:00:02,000', 'start_s': 2.0,
         '_ass_dialogue': {'style': 'OP'}},
        {'text': '无样式对白', 'start': '00:00:03,000', 'start_s': 3.0},
        {'text': '   ', 'start': '00:00:04,000', 'start_s': 4.0},
    ]
    monkeypatch.setattr(common, 'read_subtitles', lambda *a, **k: cues)

    out = load_reference(str(tmp_path), styles='Default,DefaultS')
    assert [c['text'] for c in out] == ['对白一', '无样式对白']
    assert [c['gi'] for c in out] == [0, 1]
    assert out[0]['ep'] == 'EP001'
    assert out[0]['f'] == '对白一'
    assert out[0]['bg']


def test_load_reference_rejects_empty_result(tmp_path, monkeypatch):
    _srt(tmp_path / 'EP001.srt', ['dummy'])
    monkeypatch.setattr(common, 'read_subtitles', lambda *a, **k: [
        {'text': '片头', 'start': '00:00:01,000', 'start_s': 1.0,
         '_ass_dialogue': {'style': 'OP'}},
    ])

    with pytest.raises(SystemExit) as exc:
        load_reference(str(tmp_path), styles='Default')
    assert '解析出 0 条 cue' in str(exc.value)
    assert '--styles' in str(exc.value)


def test_load_reference_missing_directory(tmp_path):
    with pytest.raises(SystemExit) as exc:
        load_reference(str(tmp_path / 'missing'))
    assert '没有字幕文件' in str(exc.value)


def test_resolve_tag_file_exact_and_token_fallback(tmp_path):
    assert resolve_tag_file(str(tmp_path / 'missing'), 'SP01') is None

    exact = _srt(tmp_path / 'SP01.srt', ['一'])
    fallback = _srt(tmp_path / 'Show_EP002.srt', ['二'])
    _srt(tmp_path / 'EP003.srt', ['三'])

    assert resolve_tag_file(str(tmp_path), 'SP01') == str(exact)
    assert resolve_tag_file(str(tmp_path), 'EP002') == str(fallback)
    assert resolve_tag_file(str(tmp_path), 'EP999') is None


def test_load_target_aligns_source_and_translation(tmp_path):
    src = tmp_path / 'src'
    dst = tmp_path / 'zh'
    src.mkdir()
    dst.mkdir()
    _srt(src / 'SP01.srt', ['こんにちは', 'さようなら'])
    _srt(dst / 'SP01.srt', ['你好', '再见'])

    out = load_target(str(src), str(dst), 'SP01')

    assert [c['src'] for c in out] == ['こんにちは', 'さようなら']
    assert [c['zh'] for c in out] == ['你好', '再见']
    assert [c['start'] for c in out] == ['00:00:02.000', '00:00:04.000']
    assert all(c['f'] and c['bg'] for c in out)


def test_load_target_errors(tmp_path):
    src = tmp_path / 'src'
    dst = tmp_path / 'zh'
    src.mkdir()
    dst.mkdir()
    _srt(src / 'SP01.srt', ['一', '二'])
    _srt(dst / 'SP01.srt', ['一'])

    with pytest.raises(SystemExit) as exc:
        load_target(str(src), str(dst), 'SP01')
    assert '无法逐条对齐' in str(exc.value)

    with pytest.raises(SystemExit) as exc:
        load_target(str(src), str(dst), 'SP02')
    assert '找不到 SP02' in str(exc.value)


def test_group_hint():
    assert group_hint([]) == ''
    assert group_hint([['サリー', 'サリ'], []]) == '（サリー / サリ 指同一个人）'
    assert group_hint([['A', 'B'], ['C', 'D']]) == (
        '（A / B 指同一个人；C / D 指同一个人）')
