"""Tests for lib.alignment.aligner — registry-backed aligner service."""

import pytest

from lib.alignment.aligner import AlignerService, build_aligner
from lib.alignment.base import AlignmentError
from lib.asr import registry
from lib.asr.base import AlignResult, BackendInfo, Token


class FakeAlignerBackend:
    name = 'fake_fa'

    def __init__(self):
        self.closed = False
        self.calls = []

    def available(self) -> bool:
        return True

    def align(self, audio_path, text, *, language='ja'):
        self.calls.append((audio_path, text, language))
        return AlignResult(text=text, language=language,
                           tokens=[Token('お前', 0.1, 0.3)])

    def close(self):
        self.closed = True


@pytest.fixture
def fake_aligner_plugin():
    registry.reset_for_tests()
    backend = FakeAlignerBackend()
    registry.register(
        'fake_fa', lambda config, **kwargs: backend, kind='aligner',
        info=BackendInfo(name='fake_fa', kind='aligner', auto_priority=99),
    )
    yield backend
    registry.reset_for_tests()


def test_service_builds_aligner_through_registry(fake_aligner_plugin):
    service = AlignerService('fake_fa')
    assert service.enabled is True
    assert service.available() is True

    result = service.align('cue.wav', 'お前何してんだよ', language='Japanese')
    assert isinstance(result, AlignResult)
    assert result.tokens[0].text == 'お前'
    assert service.last_engine == 'fake_fa'
    assert fake_aligner_plugin.calls == [('cue.wav', 'お前何してんだよ',
                                          'Japanese')]


def test_service_close_delegates_to_backend(fake_aligner_plugin):
    service = AlignerService('fake_fa')
    service.align('cue.wav', 'はい')
    service.close()
    assert fake_aligner_plugin.closed is True


def test_auto_engine_picks_available_aligner(fake_aligner_plugin):
    service = AlignerService('auto')
    assert service.resolve_engine() == 'fake_fa'
    assert service.available() is True


def test_none_engine_disables_alignment():
    service = AlignerService('none')
    assert service.enabled is False
    assert service.available() is False
    with pytest.raises(AlignmentError):
        service.align('cue.wav', 'はい')


def test_unknown_engine_lists_known_aligners():
    with pytest.raises(AlignmentError) as excinfo:
        AlignerService('nope').resolve_engine()
    assert 'nope' in str(excinfo.value)
    assert 'known' in str(excinfo.value)


def test_empty_text_rejected():
    service = AlignerService('none')
    with pytest.raises(AlignmentError):
        service.align('cue.wav', '   ')


def test_build_aligner_factory():
    service = build_aligner('none')
    assert isinstance(service, AlignerService)
    assert service.enabled is False
