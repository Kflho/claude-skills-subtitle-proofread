"""Tests for lib/whisper_backends.py — dispatch and whisper.cpp JSON parsing."""

import json
import sys
import types
from types import SimpleNamespace

from lib import whisper_backends as wb


# ═══════════════════════════════════════════════════════════════
# Pure helpers
# ═══════════════════════════════════════════════════════════════

def test_is_exe(tmp_path):
    exe = tmp_path / 'whisper-cli.exe'
    exe.write_bytes(b'')
    assert wb._is_exe(str(exe)) is True
    assert wb._is_exe(str(tmp_path / 'missing.exe')) is False
    assert wb._is_exe('') is False


def test_to_seconds():
    assert wb._to_seconds('00:01:23,456') == 83.456
    assert wb._to_seconds('0:00:04.00') == 4.0
    assert wb._to_seconds('') == 0.0
    assert wb._to_seconds('bad') == 0.0


# ═══════════════════════════════════════════════════════════════
# whisper.cpp JSON parsing
# ═══════════════════════════════════════════════════════════════

def _fake_run_writer(payload):
    def fake_run(args, **kwargs):
        out = args[args.index('-of') + 1] + '.json'
        with open(out, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=False)
        return SimpleNamespace(returncode=0, stderr='')

    return fake_run


def test_transcribe_whisper_cpp_parses_both_timestamp_styles(
        tmp_path, monkeypatch):
    monkeypatch.setattr(wb.os.path, 'expanduser',
                        lambda path: str(tmp_path / 'home'))
    audio = tmp_path / 'ep.wav'
    audio.write_bytes(b'')

    payload = {
        'transcription': [
            {'timestamps': {'from': '00:00:01,000', 'to': '00:00:02,500'},
             'text': ' こんにちは '},
            {'from': '00:00:03,000', 'to': '00:00:04,000',
             'text': 'さようなら'},
        ]
    }
    monkeypatch.setattr(wb, 'run_whisper', _fake_run_writer(payload))

    segs = wb._transcribe_whisper_cpp(str(audio), 'model.bin', 'ja')
    assert [s['text'] for s in segs] == ['こんにちは', 'さようなら']
    assert segs[0]['start_s'] == 1.0
    assert segs[0]['end_s'] == 2.5
    assert segs[1]['start_s'] == 3.0
    assert not (tmp_path / 'ep.wav.whisper.json').exists()


def test_transcribe_whisper_cpp_missing_json_returns_empty(tmp_path,
                                                          monkeypatch):
    monkeypatch.setattr(wb.os.path, 'expanduser',
                        lambda path: str(tmp_path / 'home'))
    audio = tmp_path / 'ep.wav'
    audio.write_bytes(b'')

    monkeypatch.setattr(
        wb, 'run_whisper',
        lambda args, **kwargs: SimpleNamespace(returncode=0, stderr=''))
    assert wb._transcribe_whisper_cpp(str(audio), 'model.bin', 'ja') == []


# ═══════════════════════════════════════════════════════════════
# Dispatch / backend detection
# ═══════════════════════════════════════════════════════════════

def test_transcribe_rejects_unknown_backend():
    assert wb.transcribe('ep.wav', 'model.bin', backend='nope') == []


def test_transcribe_dispatches_to_selected_backend(monkeypatch):
    sentinel = [{'start_s': 0.0, 'end_s': 1.0, 'text': 'x'}]
    called = {}

    monkeypatch.setattr(wb, '_check_backend', lambda backend: True)
    monkeypatch.setattr(
        wb, '_transcribe_whisper_cpp',
        lambda audio, model, lang, **kwargs: called.update(
            audio=audio, model=model, lang=lang) or sentinel)

    out = wb.transcribe('ep.wav', 'model.bin', language='ja',
                        backend='whisper-cpp')
    assert out == sentinel
    assert called == {'audio': 'ep.wav', 'model': 'model.bin', 'lang': 'ja'}


def test_transcribe_returns_empty_when_backend_missing(monkeypatch):
    monkeypatch.setattr(wb, '_check_backend', lambda backend: False)
    assert wb.transcribe('ep.wav', 'model.bin',
                         backend='whisper-cpp') == []


def test_detect_available_backends_explicit_env(monkeypatch):
    monkeypatch.setattr(wb, 'WHISPER_BACKEND', 'faster-whisper')
    monkeypatch.setattr(wb, '_check_backend', lambda backend: True)
    assert wb.detect_available_backends() == ['faster-whisper']

    monkeypatch.setattr(wb, '_check_backend', lambda backend: False)
    assert wb.detect_available_backends() == []


def test_detect_available_backends_auto(monkeypatch):
    monkeypatch.setattr(wb, 'WHISPER_BACKEND', '')
    monkeypatch.setattr(wb, '_check_backend',
                        lambda backend: backend == 'whisper-cpp')
    assert wb.detect_available_backends() == ['whisper-cpp']


# ═══════════════════════════════════════════════════════════════
# Backend: faster-whisper (fake module, no CTranslate2 install)
# ═══════════════════════════════════════════════════════════════

def _install_fake_faster_whisper(monkeypatch, segments,
                                 raise_on_transcribe=False):
    calls = {}

    class FakeModel:
        def __init__(self, model_path, device=None, compute_type=None,
                     cpu_threads=0):
            calls['init'] = {'model_path': model_path, 'device': device,
                             'compute_type': compute_type,
                             'cpu_threads': cpu_threads}

        def transcribe(self, audio_path, **kwargs):
            calls['transcribe'] = {'audio_path': audio_path, **kwargs}
            if raise_on_transcribe:
                raise RuntimeError('boom')
            return segments, SimpleNamespace(language='ja')

    mod = types.ModuleType('faster_whisper')
    mod.WhisperModel = FakeModel
    monkeypatch.setitem(sys.modules, 'faster_whisper', mod)
    return calls


def test_faster_whisper_maps_segments(monkeypatch):
    segments = [
        SimpleNamespace(start=0.0, end=1.5, text=' こんにちは ',
                        no_speech_prob=0.02, avg_log_prob=-0.25,
                        compression_ratio=1.1),
        SimpleNamespace(start=2.0, end=3.0, text='   ',
                        no_speech_prob=0.0, avg_log_prob=-0.1,
                        compression_ratio=1.0),
    ]
    calls = _install_fake_faster_whisper(monkeypatch, segments)

    out = wb._transcribe_faster_whisper('ep.wav', 'model.bin', 'ja')

    assert out == [{'start_s': 0.0, 'end_s': 1.5, 'text': 'こんにちは',
                    'no_speech_prob': 0.02, 'avg_logprob': -0.25,
                    'compression_ratio': 1.1}]
    assert calls['init'] == {'model_path': 'model.bin', 'device': 'auto',
                             'compute_type': 'auto', 'cpu_threads': 0}
    assert calls['transcribe']['vad_filter'] is True
    assert calls['transcribe']['vad_parameters']['threshold'] == 0.6
    assert calls['transcribe']['temperature'] == [0.0, 0.2, 0.4]


def test_faster_whisper_nth_zero_disables_vad_and_fallback(monkeypatch):
    calls = _install_fake_faster_whisper(monkeypatch, [])

    out = wb._transcribe_faster_whisper(
        'ep.wav', 'model.bin', 'ja', nth=0, no_fallback=True,
        compute_type='int8', cpu_threads=4)

    assert out == []
    assert calls['init']['compute_type'] == 'int8'
    assert calls['init']['cpu_threads'] == 4
    assert calls['transcribe']['vad_filter'] is False
    assert calls['transcribe']['vad_parameters'] is None
    assert calls['transcribe']['temperature'] == 0.0


def test_faster_whisper_failure_returns_empty(monkeypatch, capsys):
    _install_fake_faster_whisper(monkeypatch, [], raise_on_transcribe=True)

    assert wb._transcribe_faster_whisper('ep.wav', 'm', 'ja') == []
    assert 'faster-whisper transcription failed' in capsys.readouterr().err


def test_faster_whisper_missing_package_returns_empty(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, 'faster_whisper', None)

    assert wb._transcribe_faster_whisper('ep.wav', 'm', 'ja') == []
    assert 'faster-whisper not installed' in capsys.readouterr().err


# ═══════════════════════════════════════════════════════════════
# Backend: openai-whisper (fake module)
# ═══════════════════════════════════════════════════════════════

def _install_fake_openai_whisper(monkeypatch, result):
    calls = {}

    class FakeModel:
        def transcribe(self, audio_path, **kwargs):
            calls['audio_path'] = audio_path
            calls['kwargs'] = kwargs
            return result

    def load_model(model_path):
        calls['model_path'] = model_path
        return FakeModel()

    mod = types.ModuleType('whisper')
    mod.load_model = load_model
    monkeypatch.setitem(sys.modules, 'whisper', mod)
    return calls


def test_openai_whisper_maps_segments(monkeypatch):
    result = {'segments': [
        {'start': 0.5, 'end': 2.0, 'text': ' さようなら ',
         'no_speech_prob': 0.01, 'avg_logprob': -0.3,
         'compression_ratio': 1.0},
        {'start': 2.5, 'end': 3.0, 'text': '  ', 'no_speech_prob': 0.0},
    ]}
    calls = _install_fake_openai_whisper(monkeypatch, result)

    segs = wb._transcribe_openai_whisper('ep.wav', 'large-v3', 'ja')

    assert segs == [{'start_s': 0.5, 'end_s': 2.0, 'text': 'さようなら',
                     'no_speech_prob': 0.01, 'avg_logprob': -0.3,
                     'compression_ratio': 1.0}]
    assert calls['model_path'] == 'large-v3'
    assert calls['kwargs']['language'] == 'ja'
    assert calls['kwargs']['no_speech_threshold'] == 0.6
    assert calls['kwargs']['temperature'] == [0.0, 0.2, 0.4]


def test_openai_whisper_omits_threshold_when_disabled(monkeypatch):
    calls = _install_fake_openai_whisper(monkeypatch, {'segments': []})

    assert wb._transcribe_openai_whisper('ep.wav', 'm', 'ja', nth=0,
                                         no_fallback=True) == []
    assert 'no_speech_threshold' not in calls['kwargs']
    assert calls['kwargs']['temperature'] == 0.0


def test_openai_whisper_failure_returns_empty(monkeypatch, capsys):
    def broken_load(model_path):
        raise RuntimeError('no cuda')

    mod = types.ModuleType('whisper')
    mod.load_model = broken_load
    monkeypatch.setitem(sys.modules, 'whisper', mod)

    assert wb._transcribe_openai_whisper('ep.wav', 'm', 'ja') == []
    assert 'openai-whisper transcription failed' in capsys.readouterr().err


def test_openai_whisper_missing_package_returns_empty(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, 'whisper', None)

    assert wb._transcribe_openai_whisper('ep.wav', 'm', 'ja') == []
    assert 'openai-whisper not installed' in capsys.readouterr().err
