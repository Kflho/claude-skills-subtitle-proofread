"""Standalone Qwen3 CLI options, SRT writer, and helpers (no model)."""

import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

import asr.qwen3_transcribe as cli
from asr.qwen3_transcribe import _srt_time, write_srt
from lib.asr.base import ASRResult, Segment, Token
from lib.config import ALIGNER_MODEL, ASR_MODEL, WHISPER_MODEL


class TestSrtTime:
    def test_zero(self):
        assert _srt_time(0) == '00:00:00,000'

    def test_fraction(self):
        assert _srt_time(1.234) == '00:00:01,234'

    def test_hours(self):
        assert _srt_time(3661.5) == '01:01:01,500'

    def test_negative_clamped(self):
        assert _srt_time(-1) == '00:00:00,000'


class TestWriteSrt:
    def test_writes_cues(self, tmp_path):
        path = tmp_path / 'out.srt'
        write_srt([
            Segment(text='お前', start=0.0, end=1.0),
            Segment(text='何してんだよ', start=1.2, end=2.5),
        ], str(path))
        text = path.read_text(encoding='utf-8')
        assert '00:00:00,000 --> 00:00:01,000' in text
        assert '00:00:01,200 --> 00:00:02,500' in text
        assert 'お前' in text
        assert '何してんだよ' in text

    def test_skips_empty_text(self, tmp_path):
        path = tmp_path / 'out.srt'
        write_srt([
            Segment(text='', start=0.0, end=1.0),
            Segment(text='a', start=1.0, end=2.0),
        ], str(path))
        text = path.read_text(encoding='utf-8')
        assert text.startswith('1\n')
        assert 'a' in text

    def test_rejects_empty_segment_list(self, tmp_path):
        with pytest.raises(ValueError, match='no segments'):
            write_srt([], str(tmp_path / 'out.srt'))


# ═══════════════════════════════════════════════════════════════
# Helpers: fake router and fixtures
# ═══════════════════════════════════════════════════════════════

def _result(**overrides):
    data = dict(
        text='お前、何してんだよ！',
        language='Japanese',
        segments=[Segment(text='お前、何してんだよ！', start=0.0, end=1.5,
                          tokens=[Token('お前', 0.0, 0.5)])],
        tokens=[Token('お前', 0.0, 0.5), Token('何', 0.5, 1.0)],
        engine='qwen3',
        model='fake-model',
        audio_duration=1.5,
        context_hash='abc123',
        meta={'chunks': 1},
    )
    data.update(overrides)
    return ASRResult(**data)


def _audio_file(tmp_path, name='ep.wav'):
    path = tmp_path / name
    path.write_bytes(b'')
    return path


@pytest.fixture
def fake_router(monkeypatch):
    """Replace ASRRouter with a spy; no worker/torch/model is touched."""
    state = SimpleNamespace(instances=[], result=None, error=None,
                            router_class=None)

    class Router:
        def __init__(self, **kwargs):
            self.init_kwargs = kwargs
            self.config = kwargs.get('config')
            self.audio_path = None
            self.transcribe_kwargs = None
            self.closed = False
            state.instances.append(self)

        def transcribe(self, audio_path, **kwargs):
            self.audio_path = audio_path
            self.transcribe_kwargs = kwargs
            if state.error is not None:
                raise state.error
            return state.result

        def close(self):
            self.closed = True

    state.router_class = Router
    monkeypatch.setattr(cli, 'ASRRouter', Router)
    return state


# ═══════════════════════════════════════════════════════════════
# --output / --timestamps / --context-file
# ═══════════════════════════════════════════════════════════════

class TestJsonOutput:
    def test_writes_json_and_forwards_options(self, tmp_path, fake_router,
                                              capsys):
        fake_router.result = _result()
        audio = _audio_file(tmp_path)
        out = tmp_path / 'nested' / 'ep.json'

        rc = cli.main([
            '--audio', str(audio), '--language', 'ja', '--context', '用語',
            '--output', str(out),
        ])

        assert rc == 0
        payload = json.loads(out.read_text(encoding='utf-8'))
        assert payload['schema_version'] == 1
        assert payload['engine'] == 'qwen3'
        assert payload['text'] == 'お前、何してんだよ！'
        assert payload['language'] == 'Japanese'
        assert payload['tokens'][0] == {
            'text': 'お前', 'start': 0.0, 'end': 0.5}
        assert payload['segments'][0]['text'] == 'お前、何してんだよ！'
        assert payload['audio_duration'] == 1.5

        router = fake_router.instances[0]
        assert router.audio_path == str(audio)
        assert router.transcribe_kwargs == {
            'language': 'ja', 'context': '用語', 'timestamps': True,
        }
        assert router.init_kwargs['engine'] == 'qwen3'
        assert router.init_kwargs['fallback'] == 'none'
        assert router.config.model == ASR_MODEL
        assert router.config.aligner_model == ALIGNER_MODEL
        assert router.closed is True

        err = capsys.readouterr().err
        assert '[qwen3] wrote' in err
        assert '[qwen3] done:' in err

    def test_prints_json_when_no_output_path(self, tmp_path, fake_router,
                                             capsys):
        fake_router.result = _result()

        rc = cli.main(['--audio', str(_audio_file(tmp_path))])

        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload['text'] == 'お前、何してんだよ！'

    def test_no_timestamps_is_forwarded(self, tmp_path, fake_router):
        fake_router.result = _result(tokens=[], segments=[])
        out = tmp_path / 'ep.json'

        rc = cli.main([
            '--audio', str(_audio_file(tmp_path)), '--no-timestamps',
            '--output', str(out),
        ])

        assert rc == 0
        assert fake_router.instances[0].transcribe_kwargs['timestamps'] is False
        assert json.loads(out.read_text(encoding='utf-8'))['tokens'] == []

    def test_context_file_strips_utf8_bom(self, tmp_path, fake_router):
        fake_router.result = _result()
        context = tmp_path / 'glossary.txt'
        context.write_text('用語集', encoding='utf-8-sig')

        rc = cli.main([
            '--audio', str(_audio_file(tmp_path)),
            '--context-file', str(context),
            '--output', str(tmp_path / 'ep.json'),
        ])

        assert rc == 0
        assert fake_router.instances[0].transcribe_kwargs['context'] == '用語集'

    def test_engine_model_defaults_and_overrides(self, tmp_path, fake_router):
        fake_router.result = _result()
        audio = str(_audio_file(tmp_path))

        assert cli.main([
            '--audio', audio, '--engine', 'whisper',
            '--output', str(tmp_path / 'whisper.json'),
        ]) == 0
        whisper_config = fake_router.instances[0].config
        assert whisper_config.model == WHISPER_MODEL
        assert whisper_config.options['whisper_model'] == WHISPER_MODEL

        assert cli.main([
            '--audio', audio, '--engine', 'qwen3', '--model', 'custom/model',
            '--aligner', 'custom/aligner', '--python', 'python.exe',
            '--device', 'cpu', '--dtype', 'float16',
            '--output', str(tmp_path / 'qwen.json'),
        ]) == 0
        config = fake_router.instances[1].config
        assert config.model == 'custom/model'
        assert config.aligner_model == 'custom/aligner'
        assert config.python == 'python.exe'
        assert config.device == 'cpu'
        assert config.dtype == 'float16'
        assert config.options['whisper_model'] == WHISPER_MODEL


# ═══════════════════════════════════════════════════════════════
# --srt
# ═══════════════════════════════════════════════════════════════

class TestSrtOutput:
    def test_writes_srt_from_segments(self, tmp_path, fake_router):
        fake_router.result = _result()
        srt = tmp_path / 'ep.srt'

        rc = cli.main([
            '--audio', str(_audio_file(tmp_path)), '--srt', str(srt),
            '--output', str(tmp_path / 'ep.json'),
        ])

        assert rc == 0
        text = srt.read_text(encoding='utf-8')
        assert text.startswith('1\n')
        assert '00:00:00,000 --> 00:00:01,500' in text
        assert 'お前、何してんだよ！' in text

    def test_falls_back_to_tokens_when_segments_empty(self, tmp_path,
                                                     fake_router):
        fake_router.result = _result(
            text='あい', segments=[],
            tokens=[Token('あ', 0.0, 0.5), Token('い', 0.5, 1.0)])
        srt = tmp_path / 'ep.srt'

        assert cli.main([
            '--audio', str(_audio_file(tmp_path)), '--srt', str(srt),
            '--output', str(tmp_path / 'ep.json'),
        ]) == 0

        text = srt.read_text(encoding='utf-8')
        assert '00:00:00,000 --> 00:00:01,000' in text
        assert 'あい' in text

    def test_falls_back_to_text_when_no_timeline(self, tmp_path, fake_router):
        fake_router.result = _result(
            text='本文', segments=[], tokens=[], audio_duration=2.5)
        srt = tmp_path / 'ep.srt'

        assert cli.main([
            '--audio', str(_audio_file(tmp_path)), '--srt', str(srt),
            '--output', str(tmp_path / 'ep.json'),
        ]) == 0

        text = srt.read_text(encoding='utf-8')
        assert '00:00:00,000 --> 00:00:02,500' in text
        assert '本文' in text


# ═══════════════════════════════════════════════════════════════
# --video / input validation / failure paths
# ═══════════════════════════════════════════════════════════════

class TestInputHandling:
    def test_video_extracts_audio(self, tmp_path, fake_router, monkeypatch,
                                  capsys):
        video = tmp_path / 'ep01.mkv'
        video.write_bytes(b'')
        work = tmp_path / 'work'
        calls = {}

        def fake_extract(src, dst):
            calls['src'] = src
            calls['dst'] = dst
            Path(dst).parent.mkdir(parents=True, exist_ok=True)
            Path(dst).write_bytes(b'')

        monkeypatch.setattr('lib.whisper_utils.extract_audio_wav',
                            fake_extract)
        fake_router.result = _result()

        rc = cli.main([
            '--video', str(video), '--work-dir', str(work),
            '--output', str(tmp_path / 'ep.json'),
        ])

        assert rc == 0
        assert calls == {'src': str(video), 'dst': str(work / 'ep01.wav')}
        assert fake_router.instances[0].audio_path == str(work / 'ep01.wav')
        assert '[qwen3] extracted audio' in capsys.readouterr().err

    def test_missing_audio_file_returns_2(self, tmp_path, fake_router,
                                          capsys):
        rc = cli.main(['--audio', str(tmp_path / 'missing.wav')])

        assert rc == 2
        assert 'audio not found' in capsys.readouterr().err
        assert fake_router.instances == []

    def test_requires_audio_or_video(self, capsys):
        with pytest.raises(SystemExit) as exc:
            cli.main([])

        assert exc.value.code == 2
        assert 'one of --audio / --video is required' in capsys.readouterr().err

    def test_audio_and_video_are_exclusive(self, capsys):
        with pytest.raises(SystemExit) as exc:
            cli.main(['--audio', 'a.wav', '--video', 'b.mp4'])

        assert exc.value.code == 2
        assert 'use only one of --audio / --video' in capsys.readouterr().err

    def test_transcription_failure_returns_3(self, tmp_path, fake_router,
                                             capsys):
        fake_router.error = RuntimeError('model exploded')

        rc = cli.main(['--audio', str(_audio_file(tmp_path))])

        assert rc == 3
        assert 'transcription failed: model exploded' in capsys.readouterr().err
        assert fake_router.instances[0].closed is True


# ═══════════════════════════════════════════════════════════════
# --check / --list-engines
# ═══════════════════════════════════════════════════════════════

class TestListEngines:
    def test_prints_registry_entries(self, monkeypatch, capsys):
        entries = [
            SimpleNamespace(
                name='qwen3', kind='asr',
                info=SimpleNamespace(
                    auto_priority=10,
                    capabilities=('timestamps', 'context'),
                    description='Qwen3 worker')),
            SimpleNamespace(
                name='plain', kind='asr',
                info=SimpleNamespace(
                    auto_priority=0, capabilities=(),
                    description='plain engine')),
        ]
        monkeypatch.setattr('lib.asr.registry.entries',
                            lambda kind=None: entries)

        rc = cli.main(['--list-engines'])

        assert rc == 0
        out = capsys.readouterr().out
        assert ('qwen3\tasr\t10\ttimestamps,context\tQwen3 worker'
                in out)
        assert 'plain\tasr\t0\t-\tplain engine' in out


class TestCheck:
    def _install_modules(self, monkeypatch, *, cuda):
        torch = types.ModuleType('torch')
        torch.__version__ = '2.9.0'
        torch.cuda = SimpleNamespace(
            is_available=lambda: cuda,
            get_device_name=lambda index: 'Fake GPU 0')
        qwen_asr = types.ModuleType('qwen_asr')
        qwen_asr.__version__ = '0.0.6'
        monkeypatch.setitem(sys.modules, 'torch', torch)
        monkeypatch.setitem(sys.modules, 'qwen_asr', qwen_asr)

    def test_ready_reports_versions_and_gpu(self, monkeypatch, capsys):
        self._install_modules(monkeypatch, cuda=True)

        rc = cli.main([
            '--check', '--model', 'my/model', '--aligner', 'my/aligner',
        ])

        assert rc == 0
        out = capsys.readouterr().out
        assert 'qwen3_worker.py' in out
        assert 'engine: qwen3' in out
        assert 'model: my/model' in out
        assert 'aligner_model: my/aligner' in out
        assert 'torch: 2.9.0 cuda=True' in out
        assert 'gpu: Fake GPU 0' in out
        assert 'qwen_asr: 0.0.6' in out
        assert out.splitlines()[-1] == 'OK'

    def test_ready_without_cuda_omits_gpu_line(self, monkeypatch, capsys):
        self._install_modules(monkeypatch, cuda=False)

        rc = cli.main(['--check'])

        assert rc == 0
        out = capsys.readouterr().out
        assert 'torch: 2.9.0 cuda=False' in out
        assert 'gpu:' not in out
        assert out.splitlines()[-1] == 'OK'

    def test_missing_dependencies_reports_not_ready(self, monkeypatch,
                                                    capsys):
        monkeypatch.setitem(sys.modules, 'torch', None)
        monkeypatch.setitem(sys.modules, 'qwen_asr', None)

        rc = cli.main(['--check'])

        assert rc == 1
        out = capsys.readouterr().out
        assert 'torch: MISSING' in out
        assert 'qwen_asr: MISSING' in out
        assert 'install: pip install -U qwen-asr==0.0.6' in out
        assert out.splitlines()[-1] == 'NOT READY'


class TestHelpers:
    def test_read_context_none_is_empty(self):
        assert cli._read_context(None) == ''
        assert cli._read_context('') == ''
