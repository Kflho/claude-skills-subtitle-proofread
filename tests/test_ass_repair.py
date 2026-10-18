"""Tests for ass/ass_repair.py — ASS-specific checks (names/styles/...)."""

from ass.ass_repair import (
    _classify_name, check_comment, check_drawing, check_names, check_oped,
    check_styles,
)

ASS = '''[Script Info]
ScriptType: v4.00+

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:02.00,Default,Ivan,0,0,0,,こんにちは
Dialogue: 0,0:00:03.00,0:00:04.00,Default,太郎,0,0,0,,こんばんは
Dialogue: 0,0:00:05.00,0:00:06.00,OP,,0,0,0,,歌です
Dialogue: 0,0:00:05.00,0:00:06.00,OP-REF,,0,0,0,,песня
Dialogue: 0,0:00:07.00,0:00:08.00,Sign,,0,0,0,,{\\p1}m 0 0 l 10 0{\\p0}
Dialogue: 0,0:00:09.00,0:00:10.00,Sign,,0,0,0,,{\\p1}abc{\\p0}
Comment: 0,0:00:11.00,0:00:12.00,Default,,0,0,0,,Translated by Someone
'''


def _write_ass(tmp_path, name='EP001.ass'):
    path = tmp_path / name
    path.write_text(ASS, encoding='utf-8')
    return path


def test_classify_name_languages():
    assert _classify_name('Ivan')['primary_language'] == 'latin'
    assert _classify_name('Ivan')['is_non_target'] is True
    assert _classify_name('太郎')['primary_language'] == 'cjk'
    assert _classify_name('太郎')['is_non_target'] is False
    assert _classify_name('')['primary_language'] == 'empty'


def test_check_names_flags_non_target_names(tmp_path):
    _write_ass(tmp_path)
    result = check_names(str(tmp_path))
    assert result['summary']['total_names'] == 2
    assert result['summary']['non_target_names'] == 1
    assert result['findings'][0]['name'] == 'Ivan'
    assert result['findings'][0]['occurrences'] == 1


def test_check_styles_counts_usage(tmp_path):
    _write_ass(tmp_path)
    result = check_styles(str(tmp_path))
    styles = {row['style']: row['count'] for row in result['findings']}
    assert styles['Default'] == 2
    assert styles['OP'] == 1
    assert styles['OP-REF'] == 1
    assert result['summary']['total_cues'] == 6


def test_check_drawing_flags_letters_in_drawing(tmp_path):
    _write_ass(tmp_path)
    result = check_drawing(str(tmp_path))
    assert result['summary']['total_drawing_lines'] == 2
    assert result['summary']['suspicious_lines'] == 1
    assert result['findings'][1]['has_suspicious_chars'] is True


def test_check_comment_flags_foreign_residue(tmp_path):
    _write_ass(tmp_path)
    result = check_comment(str(tmp_path), langs='en,ru')
    assert result['summary']['total_findings'] == 1
    finding = result['findings'][0]
    assert finding['action'] == 'delete'
    assert 'en' in finding['text_matches']


def test_check_oped_compares_styles(tmp_path):
    _write_ass(tmp_path)
    result = check_oped(str(tmp_path), source_style='OP', ref_style='OP-REF')
    assert result['summary']['total_text_diffs'] == 1
    assert result['summary']['total_comment_remnants'] == 0
    diff = result['findings']['per_file_diffs'][0]
    assert diff['source_text'] == '歌です'
    assert diff['ref_text'] == 'песня'
