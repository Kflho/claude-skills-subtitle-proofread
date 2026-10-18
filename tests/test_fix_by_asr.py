"""Tests for Fixer.fix_by_asr (P2-12) and run_all --asr wiring."""

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from fix.fix_orchestrator import Fixer  # noqa: E402
from lib.asr.base import ASRResult, Token  # noqa: E402


SRT = (
    "1\n00:00:01,000 --> 00:00:02,000\nおはよう\n\n"
    "2\n00:00:05,000 --> 00:00:06,000\nお前何してんだよ\n\n"
)


def make_result(tokens) -> ASRResult:
    return ASRResult(
        text=''.join(t[0] for t in tokens),
        tokens=[Token(*t) for t in tokens],
        engine='fake', model='fake-1', audio_duration=20.0)


@pytest.fixture
def project(tmp_path):
    (tmp_path / 'temp' / 'scans').mkdir(parents=True)
    (tmp_path / 'reports').mkdir()
    srt = tmp_path / 'EP001.srt'
    srt.write_text(SRT, encoding='utf-8')
    return tmp_path


def test_fix_by_asr_applies_time_fix_only(project):
    fixer = Fixer('EP001', str(project), srt_dir=str(project))
    result = make_result([
        ('おはよう', 1.0, 2.0),
        ('お前何してんだよ', 5.3, 6.4),
    ])

    report = fixer.fix_by_asr(asr_result=result)

    assert report.source == 'asr'
    assert report.applied == 1
    text = (project / 'EP001.srt').read_text(encoding='utf-8')
    assert '00:00:05,300 --> 00:00:06,400' in text
    assert 'お前何してんだよ' in text          # text untouched

    align_json = project / 'temp' / 'scans' / 'EP001.alignment.json'
    payload = json.loads(align_json.read_text(encoding='utf-8'))
    assert payload['engine'] == 'fake'
    assert payload['summary']['status']['match'] == 2
    assert payload['plan']['auto'] == 1


def test_fix_by_asr_does_not_delete_or_rewrite_clean_cues(project):
    fixer = Fixer('EP001', str(project), srt_dir=str(project))
    result = make_result([
        ('おはよう', 1.0, 2.0),
        ('お前何してんだよ', 5.0, 6.0),
    ])
    report = fixer.fix_by_asr(asr_result=result)
    assert report.applied == 0
    assert (project / 'EP001.srt').read_text(encoding='utf-8') == SRT


def test_fix_by_asr_replaces_text_only_with_write_text(project):
    srt = project / 'EP002.srt'
    srt.write_text(
        "1\n00:00:05,000 --> 00:00:06,400\nおまえなにしてんだ\n\n",
        encoding='utf-8')
    result = make_result([('お前何してんだよ', 5.0, 6.4)])

    fixer = Fixer('EP002', str(project), srt_dir=str(project))
    report = fixer.fix_by_asr(asr_result=result)      # time-only default
    assert report.applied == 0
    assert 'おまえなにしてんだ' in srt.read_text(encoding='utf-8')

    fixer = Fixer('EP002', str(project), srt_dir=str(project))
    report = fixer.fix_by_asr(asr_result=result, write_text=True)
    assert report.applied == 1
    assert 'お前何してんだよ' in srt.read_text(encoding='utf-8')


def test_fix_by_asr_refuses_ass_in_place(project):
    ass = project / 'EP003.ass'
    ass.write_text('[Script Info]\nScriptType: v4.00+\n\n[Events]\n'
                   'Format: Layer, Start, End, Style, Name, MarginL, MarginR, '
                   'MarginV, Effect, Text\n'
                   'Dialogue: 0,0:00:05.00,0:00:06.00,Default,,0,0,0,,'
                   'お前何してんだよ\n', encoding='utf-8')
    before = ass.read_text(encoding='utf-8')

    fixer = Fixer('EP003', str(project), srt_dir=str(project))
    report = fixer.fix_by_asr(asr_result=make_result([('はい', 1.0, 2.0)]))
    assert report.applied == 0
    assert 'not supported' in report.details[0]
    assert ass.read_text(encoding='utf-8') == before


def test_fix_by_asr_without_subtitle(tmp_path):
    fixer = Fixer('EP009', str(tmp_path), srt_dir=str(tmp_path))
    report = fixer.fix_by_asr(asr_result=make_result([('はい', 1.0, 2.0)]))
    assert report.applied == 0
    assert 'No subtitle' in report.details[0]


def test_step_fix_episodes_threads_asr_flags(monkeypatch, tmp_path):
    import run_all

    captured = {}

    def fake_pipeline(project_dir, episode, resources, args):
        captured['engine'] = args.asr_engine
        captured['python'] = args.asr_python
        captured['text'] = args.asr_fix_text

    monkeypatch.setattr('fix.episode_workflow._run_pipeline', fake_pipeline)
    resources = {'has_video': True, 'has_whisper': False,
                 'has_reference': False, 'video_dir': str(tmp_path)}

    selected = run_all.step_fix_episodes(
        str(tmp_path), 'ja', resources, episodes=['EP001'],
        skip_if_clean=False, asr_engine='qwen3',
        asr_python='.venv-qwen\\Scripts\\python.exe', asr_fix_text=True)

    assert selected == ['EP001']
    assert captured == {'engine': 'qwen3',
                        'python': '.venv-qwen\\Scripts\\python.exe',
                        'text': True}
