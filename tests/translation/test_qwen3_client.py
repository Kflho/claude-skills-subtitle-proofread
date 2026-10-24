"""Qwen3 worker client tests — a fake worker, no torch/model required."""

import json
import sys
import textwrap

import pytest

from lib.asr.base import ASRError
from lib.asr.qwen3 import Qwen3WorkerClient, Qwen3ASRBackend


FAKE_WORKER = textwrap.dedent(r'''
    import json
    import sys
    import time

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        cmd = req.get('cmd')
        rid = req.get('id')
        if cmd == 'shutdown':
            print(json.dumps({'ok': True, 'id': rid, 'result': {'bye': True}}),
                  flush=True)
            break
        if cmd == 'ping':
            res = {'ok': True, 'id': rid, 'result': {'loaded': False}}
        elif cmd == 'load':
            res = {'ok': True, 'id': rid, 'result': {'loaded': True}}
        elif cmd == 'transcribe':
            audio = req.get('audio', '')
            if audio == 'missing.wav':
                res = {'ok': False, 'id': rid, 'error': 'missing audio'}
            elif audio == 'slow.wav':
                time.sleep(0.5)
                res = {'ok': True, 'id': rid, 'result': {'text': 'slow'}}
            else:
                res = {'ok': True, 'id': rid, 'result': {
                    'text': 'お前、何してんだよ！',
                    'language': 'Japanese',
                    'segments': [],
                    'tokens': [
                        {'text': 'お前', 'start': 0.1, 'end': 0.3},
                        {'text': '何', 'start': 0.35, 'end': 0.5},
                    ],
                    'engine': 'qwen3',
                    'model': 'fake',
                    'audio_duration': 1.0,
                    'context_hash': 'x',
                    'meta': {'timestamps': True,
                             'chunk_sec': req.get('chunk_sec'),
                             'chunk_boundaries': req.get('chunk_boundaries')},
                }}
        elif cmd == 'align':
            res = {'ok': True, 'id': rid, 'result': {
                'text': req.get('text', ''),
                'language': 'Japanese',
                'tokens': [{'text': 'お前', 'start': 0.1, 'end': 0.3}],
                'meta': {},
            }}
        else:
            res = {'ok': False, 'id': rid, 'error': 'unknown'}
        print(json.dumps(res, ensure_ascii=False), flush=True)
''')


@pytest.fixture
def fake_worker(tmp_path):
    path = tmp_path / 'fake_worker.py'
    path.write_text(FAKE_WORKER, encoding='utf-8')
    return path


@pytest.fixture
def client(fake_worker, tmp_path):
    c = Qwen3WorkerClient(
        python_exe=sys.executable,
        worker_script=fake_worker,
        project_root=tmp_path,
        asr_model='fake',
        aligner_model='fake-aligner',
        startup_timeout=5.0,
        request_timeout=2.0,
    )
    c.start()
    yield c
    c.close()


class TestQwen3WorkerClient:
    def test_ping(self, client):
        assert client.ping()['loaded'] is False

    def test_load(self, client):
        assert client.load()['loaded'] is True

    def test_transcribe_roundtrip(self, client):
        result = client.transcribe('a.wav', language='Japanese',
                                   context='glossary', timestamps=True)
        assert result.text == 'お前、何してんだよ！'
        assert result.engine == 'qwen3'
        assert len(result.tokens) == 2
        assert result.tokens[0].text == 'お前'
        assert result.audio_duration == 1.0

    def test_transcribe_passes_chunk_sec(self, client):
        result = client.transcribe('a.wav', chunk_sec=30)
        assert result.meta['chunk_sec'] == 30

    def test_transcribe_chunk_sec_zero_disables_chunking(self, client):
        result = client.transcribe('a.wav', chunk_sec=0)
        assert result.meta['chunk_sec'] == 0

    def test_transcribe_passes_chunk_boundaries(self, client):
        result = client.transcribe('a.wav', chunk_boundaries=[10.5, 20.0])
        assert result.meta['chunk_boundaries'] == [10.5, 20.0]

    def test_align_roundtrip(self, client):
        aligned = client.align('a.wav', 'お前', language='Japanese')
        assert aligned.text == 'お前'
        assert aligned.tokens[0].start == 0.1

    def test_worker_error(self, client):
        with pytest.raises(ASRError, match='missing audio'):
            client.transcribe('missing.wav')

    def test_request_timeout(self, client):
        with pytest.raises(ASRError, match='timeout'):
            client.transcribe('slow.wav', timeout=0.1)

    def test_close_is_idempotent(self, fake_worker, tmp_path):
        c = Qwen3WorkerClient(
            python_exe=sys.executable, worker_script=fake_worker,
            project_root=tmp_path, startup_timeout=5.0,
        )
        c.start()
        c.close()
        c.close()
        assert c._proc is None


class TestQwen3Availability:
    def test_available_requires_files(self, fake_worker, tmp_path):
        fake_py = tmp_path / 'python.exe'
        fake_py.write_bytes(b'')
        backend = Qwen3ASRBackend(Qwen3WorkerClient(
            python_exe=str(fake_py), worker_script=fake_worker,
            project_root=tmp_path,
        ))
        assert backend.available() is True

        missing = Qwen3ASRBackend(Qwen3WorkerClient(
            python_exe=tmp_path / 'nope.exe', worker_script=fake_worker,
            project_root=tmp_path,
        ))
        assert missing.available() is False
