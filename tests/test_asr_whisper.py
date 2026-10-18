"""Whisper fallback adapter tests — no binary/model required."""

import pytest

import lib.asr.adapters.whisper as whisper_mod
from lib.asr.base import ASRError
from lib.asr.adapters.whisper import WhisperASRBackend, _lang_code


class TestLangCode:
    def test_known(self):
        assert _lang_code('Japanese') == 'ja'
        assert _lang_code('ja') == 'ja'
        assert _lang_code('Chinese') == 'zh'

    def test_fallback(self):
        assert _lang_code('ru') == 'ru'


class TestWhisperBackend:
    def test_available_requires_model(self):
        assert WhisperASRBackend(model_path='').available() is False

    def test_transcribe_maps_segments(self, monkeypatch):
        monkeypatch.setattr(whisper_mod, 'detect_available_backends',
                            lambda: ['whisper-cpp'])
        monkeypatch.setattr(whisper_mod, 'whisper_transcribe',
                            lambda *a, **kw: [
                                {'start_s': 1.0, 'end_s': 2.0, 'text': 'お前'},
                                {'start_s': 2.5, 'end_s': 3.0, 'text': '何してんだよ'},
                            ])
        backend = WhisperASRBackend(model_path='model.bin',
                                    backend='whisper-cpp')
        result = backend.transcribe('a.wav', language='ja')
        assert result.engine == 'whisper'
        assert result.text == 'お前何してんだよ'
        assert len(result.segments) == 2
        assert result.segments[0].start == 1.0
        assert result.tokens == []
        assert result.meta['timestamps'] is False

    def test_empty_output_raises(self, monkeypatch):
        monkeypatch.setattr(whisper_mod, 'detect_available_backends',
                            lambda: ['whisper-cpp'])
        monkeypatch.setattr(whisper_mod, 'whisper_transcribe',
                            lambda *a, **kw: [])
        backend = WhisperASRBackend(model_path='model.bin',
                                    backend='whisper-cpp')
        with pytest.raises(ASRError, match='no segments'):
            backend.transcribe('a.wav', language='ja')

    def test_missing_model_raises(self):
        backend = WhisperASRBackend(model_path='')
        with pytest.raises(ASRError, match='WHISPER_MODEL'):
            backend.transcribe('a.wav')
