"""Qwen3 worker unit tests that do not import torch/qwen_asr."""

import importlib.util
import json
import subprocess
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / 'scripts' / 'asr' / 'qwen3_worker.py'


def _load_worker():
    spec = importlib.util.spec_from_file_location('qwen3_worker_test', WORKER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_align_items_to_tokens():
    worker = _load_worker()
    items = [
        SimpleNamespace(text='お前', start_time=0.1, end_time=0.3),
        SimpleNamespace(text='何', start_time=0.35, end_time=0.5),
    ]
    tokens = worker._align_items_to_tokens(SimpleNamespace(items=items))
    assert [t.text for t in tokens] == ['お前', '何']
    assert tokens[0].start == 0.1
    assert tokens[1].end == 0.5


def test_align_items_accepts_dicts():
    worker = _load_worker()
    tokens = worker._align_items_to_tokens([
        {'text': 'a', 'start': 1.0, 'end': 1.2},
        {'text': '', 'start': 1.2, 'end': 1.3},
    ])
    assert len(tokens) == 1
    assert tokens[0].text == 'a'


def test_audio_duration_wav(tmp_path):
    worker = _load_worker()
    path = tmp_path / 'one_second.wav'
    with wave.open(str(path), 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b'\x00\x00' * 16000)
    assert worker._audio_duration(str(path)) == 1.0


def test_segments_keep_punctuation():
    worker = _load_worker()
    tokens = [
        worker.Token('お前', 0.08, 0.48),
        worker.Token('何', 0.88, 1.12),
        worker.Token('し', 1.12, 1.2),
        worker.Token('て', 1.2, 1.36),
        worker.Token('ん', 1.36, 1.44),
        worker.Token('だ', 1.44, 1.52),
        worker.Token('よ', 1.52, 1.76),
        worker.Token('早く', 2.56, 2.96),
        worker.Token('行く', 2.96, 3.2),
        worker.Token('ぞ', 3.2, 3.36),
    ]
    segs = worker._segments_from_asr_text(
        'お前何してんだよ。早く行くぞ。', tokens, 4.08)
    assert len(segs) == 2
    assert segs[0].text == 'お前何してんだよ。'
    assert segs[1].text == '早く行くぞ。'
    assert segs[0].start == 0.08
    assert segs[1].end == 3.36


def test_segments_without_tokens():
    worker = _load_worker()
    segs = worker._segments_from_asr_text('テスト。', [], 1.0)
    assert len(segs) == 1
    assert segs[0].text == 'テスト。'
    assert segs[0].end == 1.0


def test_segments_fallback_without_punctuation():
    worker = _load_worker()
    tokens = [worker.Token('お前', 1.0, 1.2),
              worker.Token('何', 3.0, 3.2)]
    segs = worker._segments_from_asr_text('お前何', tokens, 3.2)
    assert len(segs) == 2
    assert segs[0].text == 'お前'
    assert segs[1].text == '何'


def test_once_ping_protocol(tmp_path):
    """The worker answers ping without loading torch/qwen_asr."""
    proc = subprocess.run(
        [sys.executable, str(WORKER), '--once'],
        input=json.dumps({'cmd': 'ping', 'id': 't1'}) + '\n',
        capture_output=True, text=True, encoding='utf-8', timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert lines, proc.stdout
    payload = json.loads(lines[-1])
    assert payload['ok'] is True
    assert payload['id'] == 't1'
    assert payload['result']['loaded'] is False


def test_once_unknown_command(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(WORKER), '--once'],
        input=json.dumps({'cmd': 'nope', 'id': 't2'}) + '\n',
        capture_output=True, text=True, encoding='utf-8', timeout=30,
    )
    assert proc.returncode == 0
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload['ok'] is False
    assert 'unknown cmd' in payload['error']
