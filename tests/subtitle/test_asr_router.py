"""ASR router tests — engine selection and fallback, no models required."""

import pytest

import lib.asr.router as router_mod
from lib.asr.base import ASRError, ASRResult
from lib.asr.router import ASRRouter, build_backend


class FakeBackend:
    def __init__(self, name, *, result=None, error=None):
        self.name = name
        self.result = result or ASRResult(text=name, engine=name)
        self.error = error
        self.calls = 0
        self.closed = False

    def available(self):
        return True

    def transcribe(self, audio_path, *, language='ja', context='',
                   timestamps=True):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result

    def close(self):
        self.closed = True


def test_build_backend_unknown():
    with pytest.raises((KeyError, ValueError)):
        build_backend('nope')


def test_router_primary(monkeypatch):
    fake = FakeBackend('qwen3')
    monkeypatch.setattr(router_mod, 'build_backend', lambda engine, **kw: fake)
    router = ASRRouter(engine='qwen3', fallback='whisper')
    result = router.transcribe('a.wav')
    assert result.engine == 'qwen3'
    assert router.last_engine == 'qwen3'
    assert fake.calls == 1


def test_router_fallback(monkeypatch):
    primary = FakeBackend('qwen3', error=ASRError('boom'))
    fallback = FakeBackend('whisper', result=ASRResult(text='fallback',
                                                       engine='whisper'))
    monkeypatch.setattr(router_mod, 'build_backend',
                        lambda engine, **kw: {'qwen3': primary,
                                              'whisper': fallback}[engine])
    router = ASRRouter(engine='qwen3', fallback='whisper')
    result = router.transcribe('a.wav')
    assert result.engine == 'whisper'
    assert result.meta['fallback_from'] == 'qwen3'
    assert 'boom' in result.meta['fallback_reason']
    assert router.last_engine == 'whisper'
    assert primary.calls == 1
    assert fallback.calls == 1


def test_router_no_fallback(monkeypatch):
    primary = FakeBackend('qwen3', error=ASRError('boom'))
    monkeypatch.setattr(router_mod, 'build_backend', lambda engine, **kw: primary)
    router = ASRRouter(engine='qwen3', fallback='none')
    with pytest.raises(ASRError, match='boom'):
        router.transcribe('a.wav')


def test_router_none_engine():
    router = ASRRouter(engine='none', fallback='none')
    with pytest.raises(ASRError):
        router.transcribe('a.wav')


def test_resolve_auto(monkeypatch):
    # No dedicated interpreter → keep legacy Whisper default.
    monkeypatch.setattr(router_mod, 'QWEN_PYTHON', '')
    router = ASRRouter(engine='auto', fallback='whisper', qwen_python=None)
    assert router.resolve_engine() == 'whisper'
    # If the registry advertises Qwen3, auto picks it (highest priority).
    monkeypatch.setattr(router_mod.registry, 'auto_candidates',
                        lambda kind, config=None: ['qwen3'])
    router = ASRRouter(engine='auto', fallback='whisper')
    assert router.resolve_engine() == 'qwen3'


def test_router_close(monkeypatch):
    fake = FakeBackend('qwen3')
    monkeypatch.setattr(router_mod, 'build_backend', lambda engine, **kw: fake)
    router = ASRRouter(engine='qwen3', fallback='none')
    router.transcribe('a.wav')
    router.close()
    assert fake.closed
    assert router._backends == {}
