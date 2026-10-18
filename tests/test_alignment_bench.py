"""Tests for scripts/eval/alignment_bench.py — synthetic structural bench."""

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from eval.alignment_bench import build_synthetic, load_cues, main  # noqa: E402


SRT = (
    "1\n00:00:01,000 --> 00:00:02,400\nおはようございます\n\n"
    "2\n00:00:02,600 --> 00:00:04,000\nいい天気ですね\n\n"
    "3\n00:00:05,000 --> 00:00:06,500\nお前何してんだよ\n\n"
    "4\n00:00:06,600 --> 00:00:07,800\n早く行くぞ\n\n"
)


@pytest.fixture
def srt_file(tmp_path):
    path = tmp_path / 'ep.srt'
    path.write_text(SRT, encoding='utf-8')
    return path


def test_load_cues_returns_dialogue_only(srt_file):
    cues = load_cues(str(srt_file))
    assert len(cues) == 4
    assert cues[0]['text'] == 'おはようございます'


def test_synthetic_expected_mapping_is_consistent(srt_file):
    cues = load_cues(str(srt_file))
    tokens, expected = build_synthetic(cues, jitter=0.05)
    assert len(tokens) >= len(cues)
    covered = set().union(*expected.values())
    assert covered == set(range(len(tokens)))


def test_bench_prints_table_and_json(srt_file, tmp_path, capsys):
    out_json = tmp_path / 'bench.json'
    code = main(['--srt', str(srt_file), '--scenario', 'token', 'drift',
                 '--json', str(out_json)])
    assert code == 0

    captured = capsys.readouterr().out
    assert '| token |' in captured
    assert '| drift |' in captured
    assert 'recovery' in captured

    payload = json.loads(out_json.read_text(encoding='utf-8'))
    assert payload['cues'] == 4
    assert [r['scenario'] for r in payload['results']] == ['token', 'drift']
    assert payload['results'][0]['status']['match'] >= 3


def test_limit_restricts_cue_count(srt_file, capsys):
    code = main(['--srt', str(srt_file), '--scenario', 'token', '--limit', '2'])
    assert code == 0
    assert '2 dialogue cues' in capsys.readouterr().out


def test_missing_file_returns_2(tmp_path):
    assert main(['--srt', str(tmp_path / 'nope.srt')]) == 2
