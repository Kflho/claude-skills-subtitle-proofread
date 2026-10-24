"""Tests for scripts/eval/diagnose_timeline.py — offline ASR timestamp report."""

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from eval.diagnose_timeline import main, overlong_units, zero_runs  # noqa: E402
from lib.alignment.quality import TimelinePolicy  # noqa: E402


def make_asr(tokens, segments=(), duration=10.0) -> dict:
    return {
        'schema_version': 1,
        'text': ''.join(t[0] for t in tokens),
        'language': 'Japanese',
        'engine': 'fake',
        'model': 'fake-1',
        'audio_duration': duration,
        'tokens': [{'text': t, 'start': s, 'end': e} for t, s, e in tokens],
        'segments': [{'text': t, 'start': s, 'end': e} for t, s, e in segments],
    }


@pytest.fixture
def asr_json(tmp_path):
    path = tmp_path / 'asr.json'
    path.write_text(json.dumps(make_asr([
        ('あ', 1.0, 1.0),
        ('い', 1.0, 1.0),
        ('う', 1.0, 1.0),
        ('え', 1.0, 1.0),
        ('お', 2.0, 2.4),
        ('か', 2.4, 2.8),
    ]), ensure_ascii=False), encoding='utf-8')
    return path


def test_zero_runs_helper_counts_and_bounds():
    units = [('あ', 1.0, 1.0), ('い', 1.0, 1.0), ('う', 1.5, 1.8)]
    runs = zero_runs(units)
    assert len(runs) == 1
    assert runs[0]['units'] == 2
    assert runs[0]['next_start'] == pytest.approx(1.5)
    assert runs[0]['text'] == 'あい'


def test_overlong_units_helper_flags_the_smear():
    units = [('その', 0.0, 3.76), ('年', 3.76, 3.92), ('の', 3.92, 4.08)]
    flagged = overlong_units(units, policy=TimelinePolicy())
    assert len(flagged) == 1
    assert flagged[0]['text'] == 'その'
    assert flagged[0]['ratio'] > 6


def test_diagnostic_writes_quality_and_runs(asr_json, tmp_path):
    out = tmp_path / 'diag.json'
    code = main(['--asr-json', str(asr_json), '--json', str(out)])
    assert code == 0

    payload = json.loads(out.read_text(encoding='utf-8'))
    assert payload['tokens']['quality']['units'] == 6
    assert payload['tokens']['quality']['zero_units'] == 4
    assert payload['tokens']['quality']['degraded'] is True
    assert payload['tokens']['zero_runs'][0]['units'] == 4
    assert payload['cases'] == {}


def test_diagnostic_scores_reference_with_shift(asr_json, tmp_path):
    srt = tmp_path / 'ep.srt'
    srt.write_text(
        "1\n00:01:41,000 --> 00:01:42,000\nあいうえ\n\n",
        encoding='utf-8')
    out = tmp_path / 'diag.json'
    code = main(['--asr-json', str(asr_json), '--srt', str(srt),
                 '--shift', '-100', '--json', str(out)])
    assert code == 0

    payload = json.loads(out.read_text(encoding='utf-8'))
    assert payload['cases']['tokens']['cues'] == 1
    assert payload['cases']['tokens']['status']['match'] == 1
    assert payload['cases']['tokens']['boundary']['start']['n'] == 1


def test_diagnostic_adds_repair_case_when_requested(tmp_path):
    asr = tmp_path / 'asr.json'
    asr.write_text(json.dumps(make_asr([
        ('その', 1.0, 4.76),
        ('年', 4.76, 4.92),
        ('の', 4.92, 5.08),
        ('夏', 5.08, 5.24),
    ]), ensure_ascii=False), encoding='utf-8')
    srt = tmp_path / 'ep.srt'
    srt.write_text(
        "1\n00:00:04,700 --> 00:00:05,200\nその年の夏\n\n",
        encoding='utf-8')
    out = tmp_path / 'diag.json'
    code = main(['--asr-json', str(asr), '--srt', str(srt),
                 '--repair-overlong', '--json', str(out)])
    assert code == 0

    payload = json.loads(out.read_text(encoding='utf-8'))
    assert 'tokens_repaired' in payload['cases']
    assert (payload['cases']['tokens_repaired']['quality']['overlong_units']
            < payload['cases']['tokens']['quality']['overlong_units'])


def test_missing_asr_json_returns_2(tmp_path):
    code = main(['--asr-json', str(tmp_path / 'nope.json')])
    assert code == 2


def test_missing_reference_returns_2(asr_json, tmp_path):
    code = main(['--asr-json', str(asr_json),
                 '--srt', str(tmp_path / 'nope.srt')])
    assert code == 2
