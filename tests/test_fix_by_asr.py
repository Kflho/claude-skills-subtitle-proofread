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


def test_asr_context_uses_series_glossary(project):
    gloss = project / 'glossaries'
    gloss.mkdir()
    (gloss / 'digimon.series.json').write_text(json.dumps({
        '_comment': 'skip me',
        'アグモン': '亚古兽',
        'タケル': '阿岳',
    }, ensure_ascii=False), encoding='utf-8')
    fixer = Fixer('EP001', str(project), srt_dir=str(project))
    context = fixer._asr_context_text()
    assert 'Japanese ASR. Hotwords:' in context
    assert 'アグモン' in context and 'タケル' in context
    assert '_comment' not in context


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


def test_fix_by_asr_audio_hints_are_optional_evidence(project, monkeypatch):
    from lib.audio.base import AudioEvent, AudioHints

    fixer = Fixer('EP001', str(project), srt_dir=str(project))
    wav = project / 'episode.wav'
    wav.write_bytes(b'fake')
    fixer._last_asr_audio = str(wav)
    result = make_result([
        ('おはよう', 1.0, 2.0),
        ('お前何してんだよ', 5.3, 6.4),
    ])
    hints = AudioHints(
        events=[AudioEvent('speech', 0, 10), AudioEvent('music', 0, 20)],
        source='fake', duration=20.0)

    def fake_attach(wav_path, out_dir, episode, provider=None):
        return hints, str(Path(out_dir) / f'{episode}.audio_hints.json')

    monkeypatch.setattr('fix.fix_orchestrator.attach_audio_hints', fake_attach)
    report = fixer.fix_by_asr(asr_result=result, audio_hints=True)

    assert report.applied == 1
    assert report.details[0].startswith('audio hints:')
    assert 'speech 50%' in report.details[0]


def test_fix_by_asr_audio_hint_failure_does_not_fail_fix(project, monkeypatch):
    fixer = Fixer('EP001', str(project), srt_dir=str(project))
    wav = project / 'episode.wav'
    wav.write_bytes(b'fake')
    fixer._last_asr_audio = str(wav)
    result = make_result([
        ('おはよう', 1.0, 2.0),
        ('お前何してんだよ', 5.3, 6.4),
    ])

    def boom(*args, **kwargs):
        raise RuntimeError('worker crashed')

    monkeypatch.setattr('fix.fix_orchestrator.attach_audio_hints', boom)
    report = fixer.fix_by_asr(asr_result=result, audio_hints=True)

    assert report.applied == 1
    assert not any('audio hints:' in d for d in report.details)


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

    worklist = json.loads(
        (project / 'temp' / 'scans' / 'EP002.asr_review.json')
        .read_text(encoding='utf-8'))
    assert worklist['schema_version'] == 1
    assert worklist['items']
    item = worklist['items'][0]
    assert item['decision'] == ''
    assert item['asr_text'] == 'お前何してんだよ'
    assert 'replace_text' in item['action'] or item['action'] == 'review_ai'

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


def test_fix_by_asr_reports_transcription_failure_not_missing_video(project,
                                                                   monkeypatch):
    """A model/worker failure must not be reported as "no video"."""
    video = project / 'EP001.mkv'
    video.write_bytes(b'')

    fixer = Fixer('EP001', str(project), srt_dir=str(project))
    fixer._video_path = str(video)
    monkeypatch.setattr(fixer, '_transcribe_for_alignment',
                        lambda engine=None: None)
    report = fixer.fix_by_asr()

    assert report.applied == 0
    assert 'transcription failed' in report.details[0].lower()
    assert 'No video' not in report.details[0]


def test_fix_by_asr_applies_consistent_global_offset(project):
    from lib.subtitle_io import format_tc

    blocks = []
    tokens = []
    for i in range(30):
        start = 10.0 + i * 5.0
        text = f'セリフ{i}'
        blocks.append(
            f"{i + 1}\n{format_tc(start + 0.5)} --> {format_tc(start + 1.5)}"
            f"\n{text}\n")
        tokens.append((text, start, start + 1.0))
    srt = project / 'EP010.srt'
    srt.write_text('\n'.join(blocks), encoding='utf-8')

    fixer = Fixer('EP010', str(project), srt_dir=str(project))
    report = fixer.fix_by_asr(asr_result=make_result(tokens),
                              global_offset=True)

    assert report.applied == 30
    text = srt.read_text(encoding='utf-8')
    assert '00:00:10,000 --> 00:00:11,000' in text
    payload = json.loads(
        (project / 'temp' / 'scans' / 'EP010.alignment.json')
        .read_text(encoding='utf-8'))
    assert payload['global_offset'] == pytest.approx(-0.5)
    assert payload['global_offset_applied'] is True


def test_fix_by_asr_records_offset_but_does_not_apply_by_default(project):
    """The Digimon reference has no real drift: raw must stay the default."""
    from lib.subtitle_io import format_tc

    blocks = []
    tokens = []
    for i in range(30):
        start = 10.0 + i * 5.0
        text = f'セリフ{i}'
        blocks.append(
            f"{i + 1}\n{format_tc(start + 0.5)} --> {format_tc(start + 1.5)}"
            f"\n{text}\n")
        tokens.append((text, start, start + 1.0))
    srt = project / 'EP012.srt'
    srt.write_text('\n'.join(blocks), encoding='utf-8')

    fixer = Fixer('EP012', str(project), srt_dir=str(project))
    fixer.fix_by_asr(asr_result=make_result(tokens))

    payload = json.loads(
        (project / 'temp' / 'scans' / 'EP012.alignment.json')
        .read_text(encoding='utf-8'))
    assert payload['global_offset'] == pytest.approx(-0.5)
    assert payload['global_offset_applied'] is False


def test_fix_by_asr_env_flag_enables_global_offset(project, monkeypatch):
    """ASR_GLOBAL_OFFSET is the config-level default; explicit arg wins."""
    from lib.subtitle_io import format_tc

    blocks = []
    tokens = []
    for i in range(30):
        start = 10.0 + i * 5.0
        text = f'セリフ{i}'
        blocks.append(
            f"{i + 1}\n{format_tc(start + 0.5)} --> {format_tc(start + 1.5)}"
            f"\n{text}\n")
        tokens.append((text, start, start + 1.0))
    srt = project / 'EP013.srt'
    srt.write_text('\n'.join(blocks), encoding='utf-8')
    monkeypatch.setattr('lib.config.ASR_GLOBAL_OFFSET', True)

    fixer = Fixer('EP013', str(project), srt_dir=str(project))
    fixer.fix_by_asr(asr_result=make_result(tokens))
    payload = json.loads(
        (project / 'temp' / 'scans' / 'EP013.alignment.json')
        .read_text(encoding='utf-8'))
    assert payload['global_offset_applied'] is True
    assert '00:00:10,000 --> 00:00:11,000' in srt.read_text(encoding='utf-8')


def test_fix_by_asr_moves_two_adjacent_cues_together(project):
    """Dense cues: each fix lands on the neighbour's old window; both apply."""
    srt = project / 'EP011.srt'
    srt.write_text(
        "1\n00:00:05,000 --> 00:00:06,000\nおはよう\n\n"
        "2\n00:00:06,000 --> 00:00:07,000\nさようなら\n\n",
        encoding='utf-8')
    result = make_result([
        ('おはよう', 5.2, 6.2),
        ('さようなら', 6.2, 7.2),
    ])

    fixer = Fixer('EP011', str(project), srt_dir=str(project))
    report = fixer.fix_by_asr(asr_result=result)

    assert report.applied == 2
    text = srt.read_text(encoding='utf-8')
    assert '00:00:05,200 --> 00:00:06,200' in text
    assert '00:00:06,200 --> 00:00:07,200' in text
    payload = json.loads(
        (project / 'temp' / 'scans' / 'EP011.alignment.json')
        .read_text(encoding='utf-8'))
    assert payload['plan']['auto'] == 2


def test_step_fix_episodes_threads_asr_flags(monkeypatch, tmp_path):
    import run_all

    captured = {}

    def fake_pipeline(project_dir, episode, resources, args):
        captured['engine'] = args.asr_engine
        captured['python'] = args.asr_python
        captured['text'] = args.asr_fix_text
        captured['offset'] = args.asr_global_offset

    monkeypatch.setattr('fix.episode_workflow._run_pipeline', fake_pipeline)
    resources = {'has_video': True, 'has_whisper': False,
                 'has_reference': False, 'video_dir': str(tmp_path)}

    selected = run_all.step_fix_episodes(
        str(tmp_path), 'ja', resources, episodes=['EP001'],
        skip_if_clean=False, asr_engine='qwen3',
        asr_python='.venv-qwen\\Scripts\\python.exe', asr_fix_text=True,
        asr_global_offset=True)

    assert selected == ['EP001']
    assert captured == {'engine': 'qwen3',
                        'python': '.venv-qwen\\Scripts\\python.exe',
                        'text': True,
                        'offset': True}
