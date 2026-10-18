"""Tests for lib/whisper_backends.py — dispatch and whisper.cpp JSON parsing."""

import json
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
