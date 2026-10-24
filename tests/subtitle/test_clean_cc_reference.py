"""Tests for eval/clean_cc_reference.py — Japanese CC annotation stripping."""

import json
from pathlib import Path

import pytest

from eval.clean_cc_reference import (
    clean_cues, clean_files, clean_text, main,
)
from lib.whisper_utils import parse_srt

SAMPLE = (
    "1\n00:00:01,000 --> 00:00:02,000\n（ナレーション）\nその年の夏は\n\n"
    "2\n00:00:03,000 --> 00:00:04,000\n"
    "（八神太一(やがみたいち)）ん… ん？\n\n"
    "3\n00:00:05,000 --> 00:00:06,000\n"
    "東南アジアでは全く雨が降(ふ)らず\n\n"
    "4\n00:00:07,000 --> 00:00:08,000\n♪～\n\n"
    "5\n00:00:09,000 --> 00:00:10,000\n（効果音）\n\n"
    "6\n00:00:11,000 --> 00:00:12,000\n（お腹の鳴る音）\n\n"
    "7\n00:00:13,000 --> 00:00:14,000\n（みんな）行くぞ！\n\n"
)


def _write(tmp_path, name='EP001.ja[cc].srt', text=SAMPLE):
    path = tmp_path / name
    path.write_text(text, encoding='utf-8-sig')
    return path


def test_clean_text_strips_speaker_and_furigana():
    assert clean_text('（八神太一(やがみたいち)）ん… ん？') == 'ん… ん？'
    assert clean_text('東南アジアでは全く雨が降(ふ)らず') == \
        '東南アジアでは全く雨が降らず'
    assert clean_text('（ナレーション）\nその年の夏は') == 'その年の夏は'


def test_clean_text_drops_annotation_only_lines():
    assert clean_text('♪～') == ''
    assert clean_text('（効果音）') == ''
    assert clean_text('（お腹の鳴る音）') == ''
    assert clean_text('  ') == ''


def test_clean_text_keeps_normal_punctuation_and_elongation():
    assert clean_text('ん… ん？') == 'ん… ん？'
    assert clean_text('あ～') == 'あ～'


def test_clean_cues_preserves_timing_and_counts(tmp_path):
    path = _write(tmp_path)
    cues = parse_srt(str(path), mark_garbled=False)

    cleaned, stats = clean_cues(cues)

    assert stats == {'cues_in': 7, 'cues_out': 4, 'dropped': 3}
    assert [c['text'] for c in cleaned] == [
        'その年の夏は', 'ん… ん？', '東南アジアでは全く雨が降らず',
        '行くぞ！']
    assert [c['start'] for c in cleaned[:2]] == ['00:00:01.000',
                                                 '00:00:03.000']
    assert cleaned[-1]['start'] == '00:00:13.000'
    assert cleaned[-1]['end'] == '00:00:14.000'


def test_clean_files_writes_copy_without_touching_input(tmp_path):
    in_dir = tmp_path / 'ja'
    in_dir.mkdir()
    raw = _write(in_dir)
    before = raw.read_bytes()
    out_dir = tmp_path / 'clean'

    rows = clean_files(str(in_dir), str(out_dir))

    assert raw.read_bytes() == before
    assert rows[0]['output'] == str((out_dir / raw.name).resolve())
    cleaned = parse_srt(str(out_dir / raw.name), mark_garbled=False)
    assert [c['text'] for c in cleaned] == [
        'その年の夏は', 'ん… ん？', '東南アジアでは全く雨が降らず',
        '行くぞ！']


def test_clean_files_pattern_and_dry_run(tmp_path):
    in_dir = tmp_path / 'ja'
    in_dir.mkdir()
    _write(in_dir, 'a.ja[cc].srt')
    _write(in_dir, 'b.other.srt')
    out_dir = tmp_path / 'clean'

    rows = clean_files(str(in_dir), str(out_dir), pattern='*.ja[cc].srt',
                       dry_run=True)

    assert [r['file'] for r in rows] == ['a.ja[cc].srt']
    assert rows[0]['output'] == ''
    assert not out_dir.exists()


def test_clean_files_rejects_same_directory(tmp_path):
    with pytest.raises(ValueError) as exc:
        clean_files(str(tmp_path), str(tmp_path))
    assert 'must differ' in str(exc.value)


def test_clean_files_missing_or_empty(tmp_path):
    with pytest.raises(FileNotFoundError):
        clean_files(str(tmp_path / 'missing'), str(tmp_path / 'out'))

    empty = tmp_path / 'empty'
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        clean_files(str(empty), str(tmp_path / 'out'))


def test_main_writes_report_and_returns_zero(tmp_path, capsys):
    in_dir = tmp_path / 'ja'
    in_dir.mkdir()
    _write(in_dir)
    out_dir = tmp_path / 'clean'
    report = tmp_path / 'reports' / 'cc.json'

    code = main(['--input-dir', str(in_dir), '--output-dir', str(out_dir),
                 '--report', str(report)])

    assert code == 0
    payload = json.loads(report.read_text(encoding='utf-8'))
    assert payload['summary'] == {
        'files': 1, 'cues_in': 7, 'cues_out': 4, 'dropped': 3,
        'empty_files': []}
    assert payload['dry_run'] is False
    assert (out_dir / 'EP001.ja[cc].srt').exists()
    assert '7 -> 4' in capsys.readouterr().err


def test_main_dry_run_writes_no_output(tmp_path, capsys):
    in_dir = tmp_path / 'ja'
    in_dir.mkdir()
    _write(in_dir)
    out_dir = tmp_path / 'clean'

    code = main(['--input-dir', str(in_dir), '--output-dir', str(out_dir),
                 '--dry-run'])

    assert code == 0
    assert not out_dir.exists()
    captured = capsys.readouterr()
    assert '7 -> 4' in captured.err
    assert 'wrote' not in captured.err


def test_main_reports_errors_as_exit_2(tmp_path, capsys):
    assert main(['--input-dir', str(tmp_path / 'missing'),
                 '--output-dir', str(tmp_path / 'out')]) == 2
    assert 'input dir not found' in capsys.readouterr().err

    assert main(['--input-dir', str(tmp_path), '--output-dir',
                 str(tmp_path)]) == 2
    assert 'must differ' in capsys.readouterr().err
