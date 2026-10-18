"""Tests for lib/audio contracts and the FireRedVAD provider."""

import json
from types import SimpleNamespace

import pytest

from lib.audio.base import AudioEvent, AudioHints, AudioProviderError
from lib.audio.fireredvad import FireRedVadProvider


def test_audio_event_duration_and_round_trip():
    event = AudioEvent('speech', 1.23456, 2.5)
    assert event.duration == pytest.approx(1.26544)
    assert AudioEvent.from_dict(event.to_dict()) == AudioEvent(
        'speech', 1.235, 2.5)


def test_audio_hints_summary_and_round_trip():
    hints = AudioHints(
        events=[
            AudioEvent('speech', 10, 70),
            AudioEvent('music', 0, 80),
            AudioEvent('singing', 20, 40),
        ],
        source='test', duration=100.0,
    )
    summary = hints.summary()
    assert summary['speech'] == {'segments': 1, 'seconds': 60.0, 'ratio': 0.6}
    assert summary['music']['ratio'] == 0.8
    assert summary['singing']['seconds'] == 20.0
    assert summary['duration'] == 100.0

    restored = AudioHints.from_dict(hints.to_dict())
    key = lambda d: (d['start'], d['end'], d['kind'])
    assert sorted((e.to_dict() for e in restored.events), key=key) == sorted(
        (e.to_dict() for e in hints.events), key=key)
    assert restored.source == 'test'


def _provider(tmp_path, payload=None, returncode=0, stderr=''):
    py = tmp_path / 'python.exe'
    py.write_text('', encoding='utf-8')
    worker = tmp_path / 'worker.py'
    worker.write_text('', encoding='utf-8')
    models = tmp_path / 'models'
    (models / 'AED').mkdir(parents=True)
    (models / 'AED' / 'model.pth.tar').write_bytes(b'x')
    wav = tmp_path / 'a.wav'
    wav.write_bytes(b'x')
    calls = {}

    def runner(cmd, timeout):
        calls['cmd'] = cmd
        if payload is not None:
            out = cmd[cmd.index('--out') + 1]
            with open(out, 'w', encoding='utf-8') as handle:
                json.dump(payload, handle, ensure_ascii=False)
        return SimpleNamespace(returncode=returncode, stdout='',
                               stderr=stderr)

    provider = FireRedVadProvider(
        python=str(py), models_dir=str(models), worker=str(worker),
        runner=runner)
    return provider, wav, calls


def test_provider_parses_worker_json(tmp_path):
    provider, wav, calls = _provider(tmp_path, payload={
        'contract_version': 1, 'source': 'fireredvad', 'duration': 10.0,
        'events': [
            {'kind': 'music', 'start': 0.0, 'end': 8.0},
            {'kind': 'speech', 'start': 1.0, 'end': 4.0},
        ],
        'meta': {'mode': 'aed'},
    })
    assert provider.available()
    hints = provider.detect(str(wav), mode='aed')
    assert hints.duration == 10.0
    assert [e.kind for e in hints.events] == ['music', 'speech']
    assert hints.summary()['music']['ratio'] == 0.8
    assert calls['cmd'][:2] == [str(provider.python), str(provider.worker)]
    assert '--mode' in calls['cmd'] and 'aed' in calls['cmd']


def test_provider_reports_worker_failure(tmp_path):
    provider, wav, _ = _provider(tmp_path, returncode=2, stderr='boom')
    with pytest.raises(AudioProviderError, match='exit 2'):
        provider.detect(str(wav))


def test_provider_reports_error_json(tmp_path):
    provider, wav, _ = _provider(tmp_path, payload={'error': 'no model'})
    with pytest.raises(AudioProviderError, match='no model'):
        provider.detect(str(wav))


def test_provider_requires_wav_and_model(tmp_path):
    provider, wav, _ = _provider(tmp_path, payload={})
    with pytest.raises(AudioProviderError, match='audio not found'):
        provider.detect(str(tmp_path / 'missing.wav'))
    (provider.models_dir / 'AED' / 'model.pth.tar').unlink()
    assert not provider.available()
    with pytest.raises(AudioProviderError, match='not available'):
        provider.detect(str(wav))
