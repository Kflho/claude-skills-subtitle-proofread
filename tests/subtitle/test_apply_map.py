"""Tests for nouns/apply_map.py — replacement safety and file indexing."""

import json
import sys

import pytest

from nouns import apply_map as am
from nouns.apply_map import (
    _apply_to_text, _build_repl, _effective_scope, _index_files,
    _process_srt, emit_checklist, emit_ja_to_zh, run_map,
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


def test_index_files_falls_back_to_dvd_numbering(tmp_path):
    (tmp_path / 'Show - 007v2 - Title.srt').write_text('', encoding='utf-8')
    assert set(_index_files(str(tmp_path))) == {'EP007'}


# ═══════════════════════════════════════════════════════════════
# _process_srt — only text lines, BOM/EOL preserved
# ═══════════════════════════════════════════════════════════════

def _write_bom_crlf_srt(path):
    content = ('1\r\n00:00:01,000 --> 00:00:02,000\r\n沙莉来了\r\n\r\n'
               '2\r\n00:00:03,000 --> 00:00:04,000\r\n沙莉走了\r\n')
    path.write_bytes(b'\xef\xbb\xbf' + content.encode('utf-8'))


def test_process_srt_dry_run_counts_without_writing(tmp_path):
    srt = tmp_path / 'EP001.srt'
    _write_bom_crlf_srt(srt)
    before = srt.read_bytes()
    repls = {'莎莉': [('沙莉', _build_repl('沙莉', '莎莉'))]}

    name, total, counts, variants = _process_srt(str(srt), repls,
                                                  apply=False)

    assert name == 'EP001.srt'
    assert total == 2
    assert counts == {'莎莉': 2}
    assert variants == {'莎莉': {'沙莉': 2}}
    assert srt.read_bytes() == before


def test_process_srt_apply_preserves_bom_and_crlf(tmp_path):
    srt = tmp_path / 'EP001.srt'
    _write_bom_crlf_srt(srt)
    repls = {'莎莉': [('沙莉', _build_repl('沙莉', '莎莉'))]}

    _process_srt(str(srt), repls, apply=True)

    raw = srt.read_bytes()
    assert raw.startswith(b'\xef\xbb\xbf')
    content = raw.decode('utf-8-sig')
    assert content.startswith('1\r\n')
    assert '\r\n' in content
    assert '莎莉来了' in content and '莎莉走了' in content
    assert '沙莉' not in content


def test_process_srt_never_touches_index_or_timecode(tmp_path):
    srt = tmp_path / 'EP001.srt'
    srt.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\n沙莉来了\n", encoding='utf-8')
    repls = {'莎莉': [('沙莉', _build_repl('沙莉', '莎莉'))]}

    _process_srt(str(srt), repls, apply=True)

    text = srt.read_text(encoding='utf-8')
    assert '00:00:01,000 --> 00:00:02,000' in text
    assert text.startswith('1\n')


# ═══════════════════════════════════════════════════════════════
# run_map
# ═══════════════════════════════════════════════════════════════

def _write_srt(path, *texts):
    blocks = []
    for i, text in enumerate(texts, 1):
        blocks.append(f'{i}\n00:00:{i * 2:02d},000 --> '
                      f'00:00:{i * 2 + 1:02d},000\n{text}\n')
    path.write_text('\n'.join(blocks), encoding='utf-8')


def _map(variants, episodes, scope='auto'):
    return {'莎莉': {'zh_variants': list(variants),
                     'episodes': list(episodes), 'scope': scope}}


def test_run_map_global_scope_dry_run(tmp_path):
    _write_srt(tmp_path / 'EP001.srt', '沙莉来了')
    _write_srt(tmp_path / 'EP002.srt', '沙莉走了')

    report, per_ep = run_map(_map(['沙莉'], ['EP001', 'EP002']),
                             str(tmp_path), dry_run=True)

    assert 'dry-run（预览，未改文件）' in report
    assert '莎莉 [global] 2 处' in report
    assert per_ep == {'EP001': {'莎莉': {'沙莉': 1}},
                      'EP002': {'莎莉': {'沙莉': 1}}}
    assert '沙莉' in (tmp_path / 'EP001.srt').read_text(encoding='utf-8')


def test_run_map_apply_rewrites_files(tmp_path):
    _write_srt(tmp_path / 'EP001.srt', '沙莉来了', '沙莉又来了')

    report, per_ep = run_map(_map(['沙莉'], ['EP001', 'EP002']),
                             str(tmp_path), dry_run=False)

    assert 'apply（已写入文件）' in report
    assert '莎莉来了' in (tmp_path / 'EP001.srt').read_text(encoding='utf-8')
    assert per_ep['EP001']['莎莉']['沙莉'] == 2   # 两条 cue 各一处


def test_run_map_per_episode_scope_skips_missing_file(tmp_path):
    _write_srt(tmp_path / 'EP001.srt', '沙莉来了')
    noun_map = _map(['沙莉'], ['EP009'], scope='per_episode')

    report, per_ep = run_map(noun_map, str(tmp_path), dry_run=True)

    assert '单集文件未找到' in report
    assert per_ep == {}


def test_run_map_without_files(tmp_path, capsys):
    report, per_ep = run_map(_map(['沙莉'], ['EP001']), str(tmp_path))
    assert (report, per_ep) == ('', {})
    assert '无字幕文件' in capsys.readouterr().err


def test_run_map_ignores_entries_without_variants(tmp_path):
    _write_srt(tmp_path / 'EP001.srt', '莎莉来了')
    noun_map = {'莎莉': {'zh_variants': ['莎莉'], 'episodes': ['EP001']}}

    report, per_ep = run_map(noun_map, str(tmp_path), dry_run=True)

    assert '含变体的标准名 0 条' in report
    assert per_ep == {}


# ═══════════════════════════════════════════════════════════════
# Checklist / mappings export
# ═══════════════════════════════════════════════════════════════

def test_emit_checklist_formats_and_writes(tmp_path):
    per_episode = {
        'EP002': {'莎莉': {'沙莉': 2, '沙里': 1},
                  '阿童木': {'阿托姆': 1}},
        'EP001': {'莎莉': {'沙莉': 1}},
    }
    out = tmp_path / 'checklist.md'

    lines = emit_checklist(per_episode, str(out))

    assert lines == [
        '- [x] 01集：莎莉（沙莉）',
        '- [x] 02集：莎莉（沙莉、沙里）、阿童木（阿托姆）',
    ]
    assert out.read_text(encoding='utf-8').splitlines() == lines


def test_emit_checklist_without_output_returns_lines():
    per_episode = {'SP01': {'莎莉': {'沙莉': 1}}}
    assert emit_checklist(per_episode, None) == ['- [x] 01集：莎莉（沙莉）']


def test_emit_ja_to_zh_merges_existing(tmp_path):
    noun_map = {
        '莎莉': {'ja_forms': ['サリー', 'サリィ']},
        '阿童木': {'ja_forms': ['アトム']},
    }
    existing = tmp_path / 'existing.json'
    existing.write_text(json.dumps({'既存': '旧', 'サリー': '旧莎莉'},
                                   ensure_ascii=False), encoding='utf-8')
    out = tmp_path / 'noun_mappings.json'

    emit_ja_to_zh(noun_map, str(out), merge_with=str(existing))

    data = json.loads(out.read_text(encoding='utf-8'))
    assert data == {'サリー': '莎莉', 'サリィ': '莎莉', 'アトム': '阿童木',
                    '既存': '旧'}


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def test_main_emit_mappings_only(tmp_path, monkeypatch):
    map_path = tmp_path / 'noun_map.json'
    map_path.write_text(json.dumps(
        {'莎莉': {'ja_forms': ['サリー']}}, ensure_ascii=False),
        encoding='utf-8')
    out = tmp_path / 'mappings.json'
    monkeypatch.setattr(sys, 'argv', [
        'apply_map.py', str(map_path), '--emit-mappings', str(out)])

    am.main()

    assert json.loads(out.read_text(encoding='utf-8')) == {'サリー': '莎莉'}


@pytest.mark.parametrize('options', [
    ['--apply', '--dry-run'],
    [],                                   # --target-dir missing
])
def test_main_rejects_invalid_flags(tmp_path, monkeypatch, options):
    map_path = tmp_path / 'noun_map.json'
    map_path.write_text('{}', encoding='utf-8')
    monkeypatch.setattr(sys, 'argv',
                        ['apply_map.py', str(map_path), *options])

    with pytest.raises(SystemExit):
        am.main()
