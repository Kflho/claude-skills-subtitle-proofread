"""Qwen3 worker unit tests that do not import torch/qwen_asr."""

import importlib.util
import io
import json
import subprocess
import sys
import types
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

import asr.qwen3_worker as worker_mod

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


# ═══════════════════════════════════════════════════════════════
# Zero-duration token fill (long-audio FA workaround)
# ═══════════════════════════════════════════════════════════════

def test_fill_zero_durations_interpolates_short_run():
    worker = _load_worker()
    tokens = [
        worker.Token('あ', 0.0, 1.0),
        worker.Token('い', 1.0, 1.0),
        worker.Token('う', 1.0, 1.0),
        worker.Token('え', 3.0, 4.0),
    ]
    assert worker._fill_zero_durations(tokens) == 2
    assert (tokens[1].start, tokens[1].end) == (1.0, 2.0)
    assert (tokens[2].start, tokens[2].end) == (2.0, 3.0)


def test_fill_zero_durations_leaves_long_gaps_alone():
    worker = _load_worker()
    tokens = [
        worker.Token('あ', 0.0, 1.0),
        worker.Token('い', 1.0, 1.0),
        worker.Token('う', 60.0, 61.0),
    ]
    assert worker._fill_zero_durations(tokens) == 0
    assert tokens[1].start == tokens[1].end == 1.0


def test_fill_zero_durations_leading_and_trailing_runs():
    worker = _load_worker()
    leading = [worker.Token('あ', 0.0, 0.0),
               worker.Token('い', 0.0, 0.0),
               worker.Token('う', 0.5, 1.0)]
    assert worker._fill_zero_durations(leading) == 2
    assert (leading[0].start, leading[1].end) == (0.0, 0.5)

    trailing = [worker.Token('あ', 0.0, 1.0),
                worker.Token('い', 1.0, 1.0)]
    assert worker._fill_zero_durations(trailing) == 0
    assert trailing[1].start == trailing[1].end == 1.0


# ═══════════════════════════════════════════════════════════════
# Fake model/runtime helpers (no torch, no qwen_asr, no real audio)
# ═══════════════════════════════════════════════════════════════

def _item(text, start, end):
    return SimpleNamespace(text=text, start_time=start, end_time=end)


def _part(text, *items):
    return SimpleNamespace(text=text,
                           time_stamps=SimpleNamespace(items=list(items)))


class FakeModel:
    """Stand-in for Qwen3ASRModel: records calls, replays responses."""

    def __init__(self, responses, *, has_aligner=True):
        self.responses = list(responses)
        self.calls = []
        self.forced_aligner = object() if has_aligner else None

    def transcribe(self, **kwargs):
        self.calls.append(kwargs)
        index = len(self.calls) - 1
        response = (self.responses[index] if index < len(self.responses)
                    else self.responses[-1])
        if isinstance(response, Exception):
            raise response
        return response


class FakeAligner:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def align(self, **kwargs):
        self.calls.append(kwargs)
        return self.results


def _runtime(model, *, aligner=True, chunk_sec=60.0):
    """A QwenRuntime that skips the import/load step entirely."""
    runtime = worker_mod.QwenRuntime(SimpleNamespace(
        asr='fake-asr',
        aligner='fake-aligner' if aligner else '',
        device='cpu',
        dtype='float32',
        chunk_sec=chunk_sec,
        max_new_tokens=1024,
        attn_implementation='',
    ))
    runtime.loaded = True
    runtime.model = model
    return runtime


def _audio_path(tmp_path, name='ep.wav'):
    path = tmp_path / name
    path.write_bytes(b'')
    return str(path)


def _fake_torch(*, cuda=False):
    torch = types.ModuleType('torch')
    torch.__version__ = '2.9.0'
    torch.float16 = object()
    torch.float32 = object()
    torch.bfloat16 = object()
    torch.cuda = SimpleNamespace(
        is_available=lambda: cuda,
        get_device_name=lambda index: 'Fake GPU',
    )
    return torch


def _fake_qwen_asr():
    module = types.ModuleType('qwen_asr')
    captured = {}
    aligner = object()

    class _Model:
        def __init__(self):
            self.forced_aligner = aligner

        @classmethod
        def from_pretrained(cls, name, **kwargs):
            captured['name'] = name
            captured['kwargs'] = kwargs
            captured['model'] = cls()
            return captured['model']

    module.Qwen3ASRModel = _Model
    captured['aligner'] = aligner
    return module, captured


@pytest.fixture
def fake_qwen_asr(monkeypatch):
    """Install a fake qwen_asr.inference.utils (no torch/model download).

    ``load_audio_any`` reports ``duration`` seconds at 1 Hz, so a chunk's
    length in samples is also its duration in seconds.
    """

    def install(duration=130.0):
        utils = types.ModuleType('qwen_asr.inference.utils')
        utils.SAMPLE_RATE = 16000
        utils.load_audio_any = lambda path: ([0.0] * int(duration), 1.0)

        def split_audio_into_chunks(wav, sr, max_chunk_sec):
            step = max(1, int(max_chunk_sec))
            return [(wav[i:i + step], float(i))
                    for i in range(0, len(wav), step)]

        utils.split_audio_into_chunks = split_audio_into_chunks
        inference = types.ModuleType('qwen_asr.inference')
        inference.utils = utils
        package = types.ModuleType('qwen_asr')
        package.inference = inference
        monkeypatch.setitem(sys.modules, 'qwen_asr', package)
        monkeypatch.setitem(sys.modules, 'qwen_asr.inference', inference)
        monkeypatch.setitem(sys.modules, 'qwen_asr.inference.utils', utils)
        return utils

    return install


# ═══════════════════════════════════════════════════════════════
# transcribe: payload parsing / result contract (non-chunked path)
# ═══════════════════════════════════════════════════════════════

class TestTranscribePayload:
    def test_result_contract_and_payload_forwarding(self, tmp_path,
                                                    monkeypatch):
        model = FakeModel([[
            _part('お前、何してんだよ！', _item('お前', 0.1, 0.3),
                  _item('何', 0.3, 0.6)),
        ]])
        runtime = _runtime(model)
        audio = _audio_path(tmp_path)
        monkeypatch.setattr(worker_mod, '_audio_duration', lambda path: 2.0)

        result = runtime.transcribe({
            'audio': audio,
            'language': 'ja',
            'context': '用語集',
            'timestamps': True,
            'chunk_sec': 0,
        })

        assert result['schema_version'] == 1
        assert result['engine'] == 'qwen3'
        assert result['model'] == 'fake-asr'
        assert result['text'] == 'お前、何してんだよ！'
        assert result['language'] == 'Japanese'
        assert result['audio_duration'] == 2.0
        assert result['context_hash'] == worker_mod._context_hash('用語集')
        assert result['tokens'] == [
            {'text': 'お前', 'start': 0.1, 'end': 0.3},
            {'text': '何', 'start': 0.3, 'end': 0.6},
        ]
        assert result['segments'][0]['text'] == 'お前、何してんだよ！'
        assert result['meta']['chunks'] == 1
        assert result['meta']['timestamps'] is True

        call = model.calls[0]
        assert call['audio'] == audio
        assert call['language'] == 'Japanese'
        assert call['context'] == '用語集'
        assert call['return_time_stamps'] is True

    def test_defaults_and_no_aligner_disables_timestamps(self, tmp_path,
                                                         monkeypatch):
        model = FakeModel([[_part('お前何してんだよ。')]], has_aligner=False)
        runtime = _runtime(model, aligner=False)
        audio = _audio_path(tmp_path)
        monkeypatch.setattr(worker_mod, '_audio_duration', lambda path: 3.0)

        result = runtime.transcribe({'audio': audio, 'chunk_sec': 0})

        assert result['language'] == 'Japanese'
        assert result['tokens'] == []
        assert result['meta']['timestamps'] is False
        assert result['segments'] == [{
            'text': 'お前何してんだよ。', 'start': 0.0, 'end': 3.0,
            'tokens': [],
        }]
        call = model.calls[0]
        assert call['return_time_stamps'] is False
        assert call['context'] == ''
        assert result['context_hash'] == worker_mod._context_hash('')

    def test_timestamps_requested_without_aligner_is_downgraded(
            self, tmp_path, monkeypatch):
        model = FakeModel([[_part('本文')]], has_aligner=False)
        runtime = _runtime(model, aligner=False)
        monkeypatch.setattr(worker_mod, '_audio_duration', lambda path: 1.0)

        result = runtime.transcribe({'audio': _audio_path(tmp_path),
                                     'timestamps': True, 'chunk_sec': 0})

        assert model.calls[0]['return_time_stamps'] is False
        assert result['tokens'] == []
        assert result['meta']['timestamps'] is False

    def test_requires_audio_and_existing_file(self, tmp_path):
        runtime = _runtime(FakeModel([]))
        with pytest.raises(ValueError, match='missing "audio"'):
            runtime.transcribe({'chunk_sec': 0})
        with pytest.raises(FileNotFoundError):
            runtime.transcribe({'audio': str(tmp_path / 'nope.wav'),
                                'chunk_sec': 0})

    def test_empty_model_result_raises(self, tmp_path):
        runtime = _runtime(FakeModel([[]]))
        with pytest.raises(RuntimeError, match='returned no result'):
            runtime.transcribe({'audio': _audio_path(tmp_path),
                                'chunk_sec': 0})


# ═══════════════════════════════════════════════════════════════
# _transcribe_chunked: offsets, merging, empty/error chunks
# ═══════════════════════════════════════════════════════════════

class TestChunkedTranscribe:
    def test_short_audio_is_single_chunk(self, tmp_path, fake_qwen_asr):
        fake_qwen_asr(duration=5.0)
        model = FakeModel([[_part('短い。', _item('短い', 1.0, 1.6))]])
        runtime = _runtime(model, chunk_sec=60.0)

        result = runtime.transcribe({'audio': _audio_path(tmp_path),
                                     'timestamps': True})

        assert len(model.calls) == 1
        assert result['meta']['chunks'] == 1
        assert result['meta']['chunk_sec'] == 60.0
        assert result['audio_duration'] == 5.0
        assert result['tokens'][0]['start'] == 1.0
        assert result['text'] == '短い。'
        chunk_audio, sample_rate = model.calls[0]['audio']
        assert (len(chunk_audio), sample_rate) == (5, 1.0)

    def test_chunk_boundaries_split_at_given_cuts(self, tmp_path, fake_qwen_asr):
        fake_qwen_asr(duration=10.0)
        model = FakeModel([
            [_part('あ。', _item('あ', 0.2, 0.4))],
            [_part('い。', _item('い', 0.2, 0.4))],
            [_part('う。', _item('う', 0.2, 0.4))],
        ])
        runtime = _runtime(model, chunk_sec=0.0)

        result = runtime.transcribe({
            'audio': _audio_path(tmp_path), 'timestamps': True,
            'chunk_boundaries': [2.0, 5.0],
        })

        assert len(model.calls) == 3
        assert result['meta']['chunks'] == 3
        assert result['meta']['chunk_boundaries'] == [2.0, 5.0]
        assert [t['start'] for t in result['tokens']] == [0.2, 2.2, 5.2]

    def test_long_audio_offsets_are_merged(self, tmp_path, fake_qwen_asr):
        fake_qwen_asr(duration=130.0)
        model = FakeModel([
            [_part('あ。', _item('あ', 0.2, 0.4))],
            [_part('い。', _item('い', 0.2, 0.4))],
            [_part('う。', _item('う', 0.2, 0.4))],
        ])
        runtime = _runtime(model)

        result = runtime.transcribe({'audio': _audio_path(tmp_path),
                                     'language': 'ja', 'timestamps': True})

        assert len(model.calls) == 3
        assert result['meta']['chunks'] == 3
        assert result['text'] == 'あ。い。う。'
        assert [t['text'] for t in result['tokens']] == ['あ', 'い', 'う']
        assert [t['start'] for t in result['tokens']] == [0.2, 60.2, 120.2]
        assert [t['end'] for t in result['tokens']] == [0.4, 60.4, 120.4]
        assert [s['start'] for s in result['segments']] == [0.2, 60.2, 120.2]
        assert [s['text'] for s in result['segments']] == ['あ。', 'い。', 'う。']
        assert result['audio_duration'] == 130.0

    def test_english_chunks_join_with_space(self, tmp_path, fake_qwen_asr):
        fake_qwen_asr(duration=90.0)
        model = FakeModel([
            [_part('hello', _item('hello', 0.1, 0.4))],
            [_part('world', _item('world', 0.1, 0.4))],
        ])
        runtime = _runtime(model)

        result = runtime.transcribe({'audio': _audio_path(tmp_path),
                                     'language': 'en', 'timestamps': True})

        assert result['text'] == 'hello world'
        assert result['language'] == 'English'
        assert [t['start'] for t in result['tokens']] == [0.1, 60.1]
        assert [s['text'] for s in result['segments']] == ['hello', 'world']

    def test_payload_chunk_sec_overrides_args(self, tmp_path, fake_qwen_asr):
        fake_qwen_asr(duration=130.0)
        model = FakeModel([[_part('x', _item('x', 0.0, 0.1))]])
        runtime = _runtime(model, chunk_sec=60.0)

        result = runtime.transcribe({'audio': _audio_path(tmp_path),
                                     'chunk_sec': 30, 'timestamps': True})

        assert len(model.calls) == 5
        assert result['meta']['chunk_sec'] == 30.0
        assert result['meta']['chunks'] == 5
        assert [t['start'] for t in result['tokens']] == [0.0, 30.0, 60.0,
                                                          90.0, 120.0]

    def test_chunk_without_result_is_skipped(self, tmp_path, fake_qwen_asr):
        fake_qwen_asr(duration=130.0)
        model = FakeModel([
            [],
            [_part('い。', _item('い', 0.1, 0.2))],
            [],
        ])
        runtime = _runtime(model)

        result = runtime.transcribe({'audio': _audio_path(tmp_path),
                                     'timestamps': True})

        assert len(model.calls) == 3
        assert result['text'] == 'い。'
        assert result['meta']['chunks'] == 3
        assert [(t['start'], t['end']) for t in result['tokens']] == [
            (60.1, 60.2)]
        assert [s['start'] for s in result['segments']] == [60.1]

    def test_chunk_without_timestamps_uses_chunk_span(self, tmp_path,
                                                      fake_qwen_asr):
        fake_qwen_asr(duration=130.0)
        model = FakeModel([
            [],
            [SimpleNamespace(text='本文', time_stamps=None)],
            [],
        ])
        runtime = _runtime(model)

        result = runtime.transcribe({'audio': _audio_path(tmp_path),
                                     'timestamps': True})

        assert result['tokens'] == []
        assert result['meta']['timestamps'] is False
        assert result['segments'] == [{
            'text': '本文', 'start': 60.0, 'end': 120.0, 'tokens': [],
        }]

    def test_zero_durations_filled_within_and_across_chunks(
            self, tmp_path, fake_qwen_asr):
        fake_qwen_asr(duration=120.0)
        model = FakeModel([
            [_part('あい', _item('あ', 59.4, 59.6),
                  _item('い', 59.6, 59.6))],
            [_part('うえお。', _item('う', 0.1, 0.3), _item('え', 0.3, 0.3),
                  _item('お', 0.4, 0.6))],
        ])
        runtime = _runtime(model)

        result = runtime.transcribe({'audio': _audio_path(tmp_path),
                                     'timestamps': True})

        spans = [(t['start'], t['end']) for t in result['tokens']]
        assert spans == [
            (59.4, 59.6),
            (59.6, 60.1),   # trailing run uses the next chunk's first start
            (60.1, 60.3),
            (60.3, 60.4),   # in-chunk zero run split up to the next token
            (60.4, 60.6),
        ]
        assert all(a[0] <= a[1] for a in spans)
        assert all(spans[i][1] <= spans[i + 1][0]
                   for i in range(len(spans) - 1))

    def test_chunk_error_propagates(self, tmp_path, fake_qwen_asr):
        fake_qwen_asr(duration=130.0)
        model = FakeModel([
            [_part('あ。', _item('あ', 0.1, 0.2))],
            RuntimeError('CUDA out of memory'),
        ])
        runtime = _runtime(model)

        with pytest.raises(RuntimeError, match='out of memory'):
            runtime.transcribe({'audio': _audio_path(tmp_path),
                                'timestamps': True})
        assert len(model.calls) == 2


# ═══════════════════════════════════════════════════════════════
# _fill_zero_durations: gap limits and anchors
# ═══════════════════════════════════════════════════════════════

class TestFillZeroDurations:
    def test_run_fills_up_to_the_combined_limit(self):
        tokens = [worker_mod.Token('あ', 0.0, 1.0),
                  worker_mod.Token('い', 1.0, 1.0),
                  worker_mod.Token('う', 1.0, 1.0),
                  worker_mod.Token('え', 4.0, 5.0)]
        # run=2 → limit max(3.0, 1.2)=3.0; span=3.0 → filled.
        assert worker_mod._fill_zero_durations(tokens) == 2
        assert (tokens[1].start, tokens[1].end) == (1.0, 2.5)
        assert (tokens[2].start, tokens[2].end) == (2.5, 4.0)

    def test_run_above_the_combined_limit_is_left_alone(self):
        tokens = [worker_mod.Token('あ', 0.0, 1.0),
                  worker_mod.Token('い', 1.0, 1.0),
                  worker_mod.Token('う', 4.01, 5.0)]
        assert worker_mod._fill_zero_durations(tokens) == 0
        assert tokens[1].start == tokens[1].end == 1.0

    def test_more_zero_tokens_raise_the_allowed_span(self):
        tokens = [worker_mod.Token('あ', 0.0, 1.0)]
        tokens += [worker_mod.Token('い', 1.0, 1.0) for _ in range(9)]
        tokens.append(worker_mod.Token('う', 7.0, 8.0))
        # run=9 → limit max(3, 5.4)=5.4; span=6 → untouched.
        assert worker_mod._fill_zero_durations(tokens) == 0

        tokens = [worker_mod.Token('あ', 0.0, 1.0)]
        tokens += [worker_mod.Token('い', 1.0, 1.0) for _ in range(10)]
        tokens.append(worker_mod.Token('う', 6.0, 7.0))
        # run=10 → limit max(3, 6)=6; span=5 → evenly split.
        assert worker_mod._fill_zero_durations(tokens) == 10
        assert tokens[1].start == 1.0
        assert tokens[-2].end == 6.0

    def test_custom_limits_are_respected(self):
        tokens = [worker_mod.Token('あ', 0.0, 1.0),
                  worker_mod.Token('い', 1.0, 1.0),
                  worker_mod.Token('う', 1.4, 2.0)]
        assert worker_mod._fill_zero_durations(
            tokens, max_gap=0.5, max_per_token=0.1) == 1
        assert (tokens[1].start, tokens[1].end) == (1.0, 1.4)

        tokens = [worker_mod.Token('あ', 0.0, 1.0),
                  worker_mod.Token('い', 1.0, 1.0),
                  worker_mod.Token('う', 1.6, 2.0)]
        assert worker_mod._fill_zero_durations(
            tokens, max_gap=0.5, max_per_token=0.1) == 0

    def test_all_zero_tokens_have_no_anchor(self):
        tokens = [worker_mod.Token('あ', 0.0, 0.0),
                  worker_mod.Token('い', 0.0, 0.0)]
        assert worker_mod._fill_zero_durations(tokens) == 0
        assert [(t.start, t.end) for t in tokens] == [(0.0, 0.0)] * 2

    def test_empty_token_list(self):
        assert worker_mod._fill_zero_durations([]) == 0

    def test_already_timed_tokens_are_untouched(self):
        tokens = [worker_mod.Token('あ', 0.0, 0.5),
                  worker_mod.Token('い', 0.5, 1.0)]
        assert worker_mod._fill_zero_durations(tokens) == 0
        assert (tokens[0].start, tokens[1].end) == (0.0, 1.0)


# ═══════════════════════════════════════════════════════════════
# _segments_from_asr_text: punctuation, token allocation
# ═══════════════════════════════════════════════════════════════

class TestSegmentsFromText:
    def test_single_sentence_keeps_internal_punctuation(self):
        tokens = [worker_mod.Token('お前', 0.1, 0.3),
                  worker_mod.Token('何', 0.3, 0.6)]
        segs = worker_mod._segments_from_asr_text(
            'お前、何してんだよ！', tokens, 1.0)
        assert [s.text for s in segs] == ['お前、何してんだよ！']
        assert (segs[0].start, segs[0].end) == (0.1, 0.6)

    def test_single_segment_keeps_spaces(self):
        segs = worker_mod._segments_from_asr_text(
            'hello world', [worker_mod.Token('hello', 0.0, 0.4),
                            worker_mod.Token('world', 0.5, 0.9)], 1.0)
        assert segs[0].text == 'hello world'

    def test_surplus_tokens_extend_last_sentence(self):
        tokens = [worker_mod.Token(ch, i * 0.1, (i + 1) * 0.1)
                  for i, ch in enumerate('あいうえお')]
        segs = worker_mod._segments_from_asr_text('あ。い。', tokens, 0.5)
        assert [s.text for s in segs] == ['あ。', 'い。']
        assert len(segs[1].tokens) == 4
        assert segs[1].end == 0.5

    def test_extra_sentence_without_tokens_is_appended(self):
        tokens = [worker_mod.Token('あ', 0.0, 0.2)]
        segs = worker_mod._segments_from_asr_text('あ。い。う。', tokens, 1.0)
        assert [s.text for s in segs] == ['あ。い。う。']
        assert (segs[0].start, segs[0].end) == (0.0, 0.2)

    def test_empty_text_with_tokens_groups_by_gap(self):
        tokens = [worker_mod.Token('あ', 0.0, 0.2),
                  worker_mod.Token('い', 1.0, 1.2)]
        segs = worker_mod._segments_from_asr_text('', tokens, 1.2)
        assert [s.text for s in segs] == ['あ', 'い']

    def test_empty_text_and_tokens_returns_nothing(self):
        assert worker_mod._segments_from_asr_text('', [], 3.0) == []

    def test_blank_tokens_fall_back_to_text(self):
        tokens = [worker_mod.Token('', 0.5, 1.0)]
        segs = worker_mod._segments_from_asr_text('本文。', tokens, 1.0)
        assert [s.text for s in segs] == ['本文。']
        assert (segs[0].start, segs[0].end) == (0.5, 1.0)

    def test_single_chunk_split_by_gap_keeps_final_mark(self):
        tokens = [worker_mod.Token('あ', 0.0, 0.2),
                  worker_mod.Token('い', 2.0, 2.2)]
        segs = worker_mod._segments_from_asr_text('あい。', tokens, 2.2)
        assert [s.text for s in segs] == ['あ', 'い。']


class TestAlignItems:
    def test_none_and_missing_fields_are_skipped(self):
        assert worker_mod._align_items_to_tokens(None) == []
        assert worker_mod._align_items_to_tokens(
            SimpleNamespace(items=None)) == []
        tokens = worker_mod._align_items_to_tokens([
            {'text': 'ok', 'start': 0.1, 'end': 0.2},
            {'text': 'no end', 'start': 0.3},
            SimpleNamespace(text='no start', end_time=0.5),
        ])
        assert [t.text for t in tokens] == ['ok']


# ═══════════════════════════════════════════════════════════════
# align / runtime loading / worker protocol
# ═══════════════════════════════════════════════════════════════

class TestAlign:
    def test_align_returns_tokens(self, tmp_path):
        aligner = FakeAligner([SimpleNamespace(items=[
            _item('お前', 0.1, 0.3), _item('何', 0.4, 0.6),
        ])])
        runtime = _runtime(SimpleNamespace(forced_aligner=aligner))
        audio = _audio_path(tmp_path, 'scene.wav')

        result = runtime.align({'audio': audio, 'text': 'お前何',
                                'language': 'ja'})

        assert result['text'] == 'お前何'
        assert result['language'] == 'Japanese'
        assert [t['text'] for t in result['tokens']] == ['お前', '何']
        assert result['meta'] == {
            'device': 'cpu', 'dtype': 'float32', 'aligner': 'fake-aligner'}
        assert aligner.calls == [{
            'audio': audio, 'text': 'お前何', 'language': 'Japanese'}]

    def test_align_requires_aligner(self, tmp_path):
        runtime = _runtime(SimpleNamespace(), aligner=False)
        with pytest.raises(RuntimeError, match='without --aligner'):
            runtime.align({'audio': _audio_path(tmp_path), 'text': 'x'})

    def test_align_validates_audio_and_text(self, tmp_path):
        runtime = _runtime(
            SimpleNamespace(forced_aligner=FakeAligner([])))
        audio = _audio_path(tmp_path)
        with pytest.raises(FileNotFoundError, match='missing audio'):
            runtime.align({'text': 'x'})
        with pytest.raises(ValueError, match='empty text'):
            runtime.align({'audio': audio, 'text': '   '})

    def test_align_without_result_raises(self, tmp_path):
        runtime = _runtime(
            SimpleNamespace(forced_aligner=FakeAligner([])))
        with pytest.raises(RuntimeError, match='returned no result'):
            runtime.align({'audio': _audio_path(tmp_path), 'text': 'x'})


class TestRuntimeLoading:
    def test_import_failure_is_cached(self, monkeypatch):
        monkeypatch.setitem(sys.modules, 'torch', None)
        runtime = _runtime(None)
        runtime.loaded = False

        with pytest.raises(RuntimeError, match='qwen-asr import failed'):
            runtime.ensure_loaded()
        assert runtime.load_error
        with pytest.raises(RuntimeError, match='qwen-asr import failed'):
            runtime.ensure_loaded()

    def test_cuda_requested_but_unavailable(self, monkeypatch):
        monkeypatch.setitem(sys.modules, 'torch', _fake_torch(cuda=False))
        monkeypatch.setitem(
            sys.modules, 'qwen_asr', _fake_qwen_asr()[0])
        runtime = _runtime(None)
        runtime.loaded = False
        runtime.args.device = 'cuda:0'

        with pytest.raises(RuntimeError, match='CUDA requested'):
            runtime.ensure_loaded()
        assert runtime.load_error

    def test_cpu_bfloat16_loads_float32_model(self, monkeypatch):
        fake_torch = _fake_torch(cuda=False)
        fake_qwen, captured = _fake_qwen_asr()
        monkeypatch.setitem(sys.modules, 'torch', fake_torch)
        monkeypatch.setitem(sys.modules, 'qwen_asr', fake_qwen)
        runtime = _runtime(None)
        runtime.loaded = False
        runtime.args.device = 'cpu'
        runtime.args.dtype = 'bfloat16'
        runtime.args.attn_implementation = 'sdpa'

        runtime.ensure_loaded()

        assert runtime.loaded is True
        assert captured['name'] == 'fake-asr'
        kwargs = captured['kwargs']
        assert kwargs['dtype'] is fake_torch.float32
        assert kwargs['device_map'] == 'cpu'
        assert kwargs['max_inference_batch_size'] == 1
        assert kwargs['max_new_tokens'] == 1024
        assert kwargs['attn_implementation'] == 'sdpa'
        assert kwargs['forced_aligner'] == 'fake-aligner'
        assert kwargs['forced_aligner_kwargs'] == {
            'dtype': fake_torch.float32, 'device_map': 'cpu'}
        assert runtime.aligner is captured['aligner']

    def test_dtype_mapping(self, monkeypatch):
        fake_torch = _fake_torch()
        monkeypatch.setitem(sys.modules, 'torch', fake_torch)
        assert worker_mod._dtype('float16') is fake_torch.float16
        assert worker_mod._dtype('float32') is fake_torch.float32
        assert worker_mod._dtype('anything') is fake_torch.bfloat16

    def test_ping_reports_torch_state(self, monkeypatch):
        monkeypatch.setitem(sys.modules, 'torch', _fake_torch(cuda=True))
        runtime = _runtime(None)
        runtime.loaded = False

        info = runtime.ping()

        assert info['asr_model'] == 'fake-asr'
        assert info['aligner_model'] == 'fake-aligner'
        assert info['device'] == 'cpu'
        assert info['dtype'] == 'float32'
        assert info['loaded'] is False
        assert info['cuda_available'] is True
        assert info['torch'] == '2.9.0'

    def test_ping_survives_missing_torch(self, monkeypatch):
        monkeypatch.setitem(sys.modules, 'torch', None)
        runtime = _runtime(None)
        runtime.loaded = False

        info = runtime.ping()

        assert info['cuda_available'] is False
        assert 'torch' not in info

    def test_audio_duration_unknown_file_is_zero(self, tmp_path):
        assert worker_mod._audio_duration(str(tmp_path / 'nope.wav')) == 0.0


class TestWorkerProtocol:
    def test_handle_dispatches_known_commands(self):
        calls = []

        class SpyRuntime:
            def ping(self):
                calls.append('ping')
                return {'loaded': True}

            def ensure_loaded(self):
                calls.append('load')

            def transcribe(self, request):
                calls.append('transcribe')
                return {'text': 'x'}

            def align(self, request):
                calls.append('align')
                return {'tokens': []}

        runtime = SpyRuntime()
        assert worker_mod._handle(runtime, {'cmd': 'ping'}) == {'loaded': True}
        assert worker_mod._handle(runtime, {'cmd': 'load'}) == {'loaded': True}
        assert worker_mod._handle(runtime, {'cmd': 'transcribe'}) == {
            'text': 'x'}
        assert worker_mod._handle(runtime, {'cmd': 'align'}) == {'tokens': []}
        with pytest.raises(ValueError, match='unknown cmd'):
            worker_mod._handle(runtime, {'cmd': 'nope'})
        assert calls == ['ping', 'load', 'ping', 'transcribe', 'align']

    def test_main_turns_exception_into_error_payload(self, monkeypatch,
                                                     capsys):
        class FailingRuntime:
            def transcribe(self, request):
                raise RuntimeError('model exploded')

        monkeypatch.setattr(worker_mod, 'QwenRuntime',
                            lambda args: FailingRuntime())
        monkeypatch.setattr(
            sys, 'stdin',
            io.StringIO(json.dumps({'cmd': 'transcribe', 'id': 'x1'}) + '\n'))

        assert worker_mod.main([]) == 0

        payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert payload['ok'] is False
        assert payload['id'] == 'x1'
        assert 'model exploded' in payload['error']
        assert 'Traceback' in payload['traceback']

    def test_bad_json_lines_do_not_kill_worker(self, monkeypatch, capsys):
        runtime = SimpleNamespace(ping=lambda: {'loaded': False})
        monkeypatch.setattr(worker_mod, 'QwenRuntime', lambda args: runtime)
        stdin = '\n'.join([
            'not json',
            '',
            '[]',
            json.dumps({'cmd': 'ping', 'id': 'p1'}),
            json.dumps({'cmd': 'shutdown', 'id': 'bye'}),
            json.dumps({'cmd': 'ping', 'id': 'after'}),
        ]) + '\n'
        monkeypatch.setattr(sys, 'stdin', io.StringIO(stdin))

        assert worker_mod.main([]) == 0

        lines = [json.loads(line)
                 for line in capsys.readouterr().out.splitlines()
                 if line.strip()]
        assert len(lines) == 4
        assert lines[0]['ok'] is False
        assert 'bad json' in lines[0]['error']
        assert lines[1]['ok'] is False
        assert 'must be a JSON object' in lines[1]['error']
        assert lines[2] == {'ok': True, 'id': 'p1',
                            'result': {'loaded': False}}
        assert lines[3] == {'ok': True, 'id': 'bye',
                            'result': {'bye': True}}

    @pytest.mark.parametrize('line, expected_ok', [
        ('not json', False),
        ('[]', False),
        (json.dumps({'cmd': 'ping', 'id': 'p1'}), True),
    ])
    def test_once_handles_one_line(self, monkeypatch, capsys, line,
                                   expected_ok):
        runtime = SimpleNamespace(ping=lambda: {'loaded': False})
        monkeypatch.setattr(worker_mod, 'QwenRuntime', lambda args: runtime)
        monkeypatch.setattr(
            sys, 'stdin',
            io.StringIO(line + '\n'
                        + json.dumps({'cmd': 'ping', 'id': 'later'}) + '\n'))

        assert worker_mod.main(['--once']) == 0

        lines = [json.loads(item)
                 for item in capsys.readouterr().out.splitlines()
                 if item.strip()]
        assert len(lines) == 1
        assert lines[0]['ok'] is expected_ok

    def test_main_reconfigures_stdin_to_utf8(self, monkeypatch, capsys):
        class ReconfigurableStdin(io.StringIO):
            def __init__(self, text):
                super().__init__(text)
                self.reconfigured = None

            def reconfigure(self, **kwargs):
                self.reconfigured = kwargs

        runtime = SimpleNamespace(ping=lambda: {'loaded': False})
        monkeypatch.setattr(worker_mod, 'QwenRuntime', lambda args: runtime)
        stdin = ReconfigurableStdin(json.dumps({'cmd': 'ping'}) + '\n')
        monkeypatch.setattr(sys, 'stdin', stdin)

        assert worker_mod.main(['--once']) == 0
        assert stdin.reconfigured == {'encoding': 'utf-8'}
