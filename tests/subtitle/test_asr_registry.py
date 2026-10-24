"""Adapter registry tests — new engines register without core edits."""

import pytest

from lib.asr import registry
from lib.asr.base import (
    ASRConfig, ASRResult, BackendInfo, validate_aligner, validate_backend,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Do not leak test-only adapters into router tests."""
    yield
    registry.reset_for_tests()


class FakeBackend:
    name = 'fake'

    def available(self):
        return True

    def transcribe(self, audio_path, *, language='ja', context='',
                   timestamps=False):
        return ASRResult(text='ok', engine='fake')


class FakeAligner:
    name = 'fake-aligner'

    def available(self):
        return True

    def align(self, audio_path, text, *, language='ja'):
        from lib.asr.base import AlignResult
        return AlignResult(text=text, language=language)


def test_builtin_adapters_discovered():
    registry.discover()
    names = registry.names('asr')
    assert 'qwen3' in names
    assert 'whisper' in names
    assert 'qwen3_fa' in registry.names('aligner')


def test_register_and_build_new_adapter():
    calls = {}

    def factory(config, **kwargs):
        calls['config'] = config
        calls['kwargs'] = kwargs
        return FakeBackend()

    registry.register('unit-fake', factory, kind='asr',
                      info=BackendInfo(name='unit-fake', auto_priority=1))
    cfg = ASRConfig(model='m', device='cpu')
    backend = registry.build('unit-fake', config=cfg)
    assert isinstance(backend, FakeBackend)
    assert calls['config'] is cfg
    assert 'unit-fake' in registry.names('asr')


def test_validate_backend():
    assert validate_backend(FakeBackend()) == []

    class Broken:
        name = ''

    errors = validate_backend(Broken())
    assert any('name' in e for e in errors)
    assert any('available' in e for e in errors)
    assert any('transcribe' in e for e in errors)


def test_validate_aligner():
    assert validate_aligner(FakeAligner()) == []

    class Broken:
        name = ''

    errors = validate_aligner(Broken())
    assert any('name' in e for e in errors)
    assert any('available' in e for e in errors)
    assert any('align' in e for e in errors)


def test_build_qwen3_aligner_contract():
    adapter = registry.build('qwen3_fa', config=ASRConfig())
    assert validate_aligner(adapter) == []
    assert adapter.name == 'qwen3_fa'


def test_qwen3_aligner_honours_local_model_paths():
    """A caller's offline/local ASR path must reach the worker, not the Hub."""
    config = ASRConfig(model='models/Qwen3-ASR-local',
                       aligner_model='models/Qwen3-FA-local',
                       python='python.exe')
    adapter = registry.build('qwen3_fa', config=config)
    assert adapter.client.asr_model == 'models/Qwen3-ASR-local'
    assert adapter.client.aligner_model == 'models/Qwen3-FA-local'


def test_auto_candidates_do_not_raise_with_unavailable_engines():
    # Main env has no qwen_asr and likely no Whisper model; must not raise.
    candidates = registry.auto_candidates('asr', config=ASRConfig())
    assert isinstance(candidates, list)
