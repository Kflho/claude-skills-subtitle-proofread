"""Tests for scripts/asr/align_cues.py — the P2 alignment CLI."""

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from asr.align_cues import main  # noqa: E402


SRT = (
    "1\n00:00:01,000 --> 00:00:02,000\nおはよう\n\n"
    "2\n00:00:05,000 --> 00:00:06,000\nお前何してんだよ\n\n"
    "3\n00:00:09,000 --> 00:00:10,000\nさようなら\n\n"
)


def make_asr(tokens) -> dict:
    return {
        'schema_version': 1,
        'text': ''.join(t[0] for t in tokens),
        'language': 'Japanese',
        'engine': 'fake',
        'model': 'fake-1',
        'audio_duration': 20.0,
        'tokens': [{'text': t, 'start': s, 'end': e} for t, s, e in tokens],
        'segments': [],
    }


@pytest.fixture
def cli_fixture(tmp_path):
    srt = tmp_path / 'ep.srt'
    srt.write_text(SRT, encoding='utf-8')
    asr = tmp_path / 'asr.json'
    asr.write_text(json.dumps(make_asr([
        ('おはよう', 1.0, 2.0),
        ('お前何してんだよ', 5.2, 6.4),
    ]), ensure_ascii=False), encoding='utf-8')
    return srt, asr


def test_dry_run_writes_report_but_not_subtitle(cli_fixture, tmp_path):
    srt, asr = cli_fixture
    output = tmp_path / 'report.json'

    code = main(['--srt', str(srt), '--asr-json', str(asr),
                 '--output', str(output)])
    assert code == 0
    assert srt.read_text(encoding='utf-8') == SRT

    payload = json.loads(output.read_text(encoding='utf-8'))
    assert payload['engine'] == 'fake'
    assert payload['summary']['cues'] == 3
    assert payload['summary']['status']['match'] == 2
    assert payload['summary']['status']['no_speech'] == 1
    assert payload['summary']['boundary']['start']['n'] == 2
    assert payload['plan']['auto'] == 1
    assert payload['apply']['applied'] == 0
    assert payload['apply']['fix_time'] == 0


def test_write_applies_time_fix_only(cli_fixture, tmp_path):
    srt, asr = cli_fixture
    output = tmp_path / 'report.json'

    code = main(['--srt', str(srt), '--asr-json', str(asr),
                 '--output', str(output), '--write'])
    assert code == 0

    text = srt.read_text(encoding='utf-8')
    assert '00:00:05,200 --> 00:00:06,400' in text
    assert 'お前何してんだよ' in text          # text untouched
    assert '00:00:09,000 --> 00:00:10,000' in text

    payload = json.loads(output.read_text(encoding='utf-8'))
    assert payload['apply']['applied'] == 1
    assert payload['apply']['fix_time'] == 1


def test_fix_text_replaces_garbled_cue(tmp_path):
    srt = tmp_path / 'ep.srt'
    srt.write_text(
        "1\n00:00:05,000 --> 00:00:06,400\nおまえなにしてんだ\n\n",
        encoding='utf-8')
    asr = tmp_path / 'asr.json'
    asr.write_text(json.dumps(make_asr([
        ('お前何してんだよ', 5.0, 6.4),
    ]), ensure_ascii=False), encoding='utf-8')
    output = tmp_path / 'report.json'

    code = main(['--srt', str(srt), '--asr-json', str(asr),
                 '--output', str(output), '--write', '--fix-text'])
    assert code == 0
    assert 'お前何してんだよ' in srt.read_text(encoding='utf-8')


def test_requires_exactly_one_source(cli_fixture):
    srt, asr = cli_fixture
    with pytest.raises(SystemExit):
        main(['--srt', str(srt), '--asr-json', str(asr),
              '--audio', 'x.wav'])


def test_missing_subtitle_returns_2(tmp_path):
    asr = tmp_path / 'asr.json'
    asr.write_text(json.dumps(make_asr([('はい', 1.0, 2.0)])),
                   encoding='utf-8')
    code = main(['--srt', str(tmp_path / 'nope.srt'),
                 '--asr-json', str(asr)])
    assert code == 2


def test_ass_write_is_refused_instead_of_silently_ignored(cli_fixture,
                                                          tmp_path):
    _, asr = cli_fixture
    ass = tmp_path / 'ep.ass'
    ass.write_text('[Script Info]\nScriptType: v4.00+\n\n[Events]\n'
                   'Format: Layer, Start, End, Style, Name, MarginL, MarginR, '
                   'MarginV, Effect, Text\n'
                   'Dialogue: 0,0:00:05.00,0:00:06.00,Default,,0,0,0,,'
                   'お前何してんだよ\n', encoding='utf-8')
    before = ass.read_text(encoding='utf-8')

    code = main(['--srt', str(ass), '--asr-json', str(asr), '--write'])
    assert code == 4
    assert ass.read_text(encoding='utf-8') == before


def _write_srt(path, cues):
    from lib.subtitle_io import format_tc
    lines = []
    for index, (start, end, text) in enumerate(cues, 1):
        lines.append(f'{index}\n{format_tc(start)} --> {format_tc(end)}\n'
                     f'{text}\n')
    path.write_text('\n'.join(lines), encoding='utf-8')


def test_global_offset_is_opt_in(tmp_path):
    cues = [(10.0 + i * 3, 11.0 + i * 3, f'あ{i}') for i in range(22)]
    srt = tmp_path / 'ep.srt'
    _write_srt(srt, cues)
    asr = tmp_path / 'asr.json'
    asr.write_text(json.dumps(make_asr([
        (text, start + 0.5, end + 0.5) for start, end, text in cues
    ]), ensure_ascii=False), encoding='utf-8')
    output = tmp_path / 'report.json'

    code = main(['--srt', str(srt), '--asr-json', str(asr),
                 '--output', str(output), '--global-offset'])
    assert code == 0
    payload = json.loads(output.read_text(encoding='utf-8'))
    assert payload['global_offset'] == pytest.approx(0.5, abs=0.01)
    assert payload['summary']['boundary']['start']['p50_ms'] < 50

    raw_out = tmp_path / 'raw.json'
    code = main(['--srt', str(srt), '--asr-json', str(asr),
                 '--output', str(raw_out)])
    assert code == 0
    raw = json.loads(raw_out.read_text(encoding='utf-8'))
    assert raw['global_offset'] is None
    assert raw['summary']['boundary']['start']['p50_ms'] >= 400
