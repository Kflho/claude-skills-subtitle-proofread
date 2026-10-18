"""P0 evaluation metric tests (no audio/model required)."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import eval.asr_eval as ev
from eval.asr_eval import boundary_metrics, compute_cer, normalize_text


class TestNormalize:
    def test_strips_punctuation_and_spaces(self):
        assert normalize_text('こんにちは、世界！') == 'こんにちは世界'
        assert normalize_text('  a b  c  ') == 'abc'

    def test_nfkc(self):
        assert normalize_text('１２３ＡＢＣ') == '123ABC'


class TestCER:
    def test_identical(self):
        out = compute_cer('お前、何してんだよ！', 'お前何してんだよ')
        assert out['cer'] == 0.0
        assert out['distance'] == 0

    def test_substitution(self):
        out = compute_cer('あいう', 'あいえ')
        assert out['distance'] == 1
        assert out['substitutions'] == 1
        assert out['cer'] == 1 / 3

    def test_insertion(self):
        out = compute_cer('あい', 'あいう')
        assert out['distance'] == 1
        assert out['insertions'] == 1
        assert out['cer'] == 0.5

    def test_deletion(self):
        out = compute_cer('あいう', 'あい')
        assert out['distance'] == 1
        assert out['deletions'] == 1
        assert out['cer'] == 1 / 3

    def test_empty_reference(self):
        out = compute_cer('', 'あ')
        assert out['ref_chars'] == 0
        assert out['cer'] == 1.0

    def test_empty_hypothesis(self):
        out = compute_cer('あいう', '')
        assert out['hyp_chars'] == 0
        assert out['deletions'] == 3
        assert out['cer'] == 1.0


class TestBoundaryMetrics:
    def test_segment_overlap(self):
        ref = [
            {'start_s': 0.0, 'end_s': 2.0},
            {'start_s': 3.0, 'end_s': 4.0},
        ]
        segs = [
            SimpleNamespace(start=0.2, end=2.2),
            SimpleNamespace(start=3.0, end=4.0),
        ]
        out = boundary_metrics(ref, segs)
        assert out['cues'] == 2
        assert out['matched'] == 2
        assert out['missed'] == 0
        assert out['mae_ms'] == 100.0  # (200 + 0) / 2

    def test_missing_overlap(self):
        ref = [{'start_s': 10.0, 'end_s': 12.0}]
        segs = [SimpleNamespace(start=0.0, end=1.0)]
        out = boundary_metrics(ref, segs)
        assert out['missed'] == 1
        assert out['matched'] == 0
        assert out['mae_ms'] == 2000.0

    def test_empty(self):
        out = boundary_metrics([], [])
        assert out['cues'] == 0
        assert out['mae_ms'] is None


# ═══════════════════════════════════════════════════════════════
# Episode evaluation (fake router / temporary files)
# ═══════════════════════════════════════════════════════════════

def _ref_srt(tmp_path, text='こんにちは'):
    path = tmp_path / 'EP001.srt'
    path.write_text(
        f'1\n00:00:01,000 --> 00:00:02,000\n{text}\n\n', encoding='utf-8')
    return path


def _result(text='こんにちは', duration=2.0):
    return SimpleNamespace(
        text=text,
        segments=[SimpleNamespace(start=1.0, end=2.0)],
        tokens=[],
        engine='fake',
        model='fake-model',
        audio_duration=duration,
        meta={'warnings': ['w']},
    )


class TestResolveAudio:
    def test_prefers_existing_audio(self, tmp_path):
        audio = tmp_path / 'a.wav'
        audio.write_bytes(b'')
        assert ev._resolve_audio(
            {'id': 'EP001', 'audio': str(audio)}, str(tmp_path)) == str(audio)

    def test_extracts_audio_from_video(self, tmp_path, monkeypatch):
        video = tmp_path / 'v.mkv'
        video.write_bytes(b'')
        calls = []
        monkeypatch.setattr('lib.whisper_utils.extract_audio_wav',
                            lambda v, o: calls.append((v, o)))

        out = ev._resolve_audio({'id': 'EP007', 'video': str(video)},
                                str(tmp_path / 'work'))

        assert out == str(tmp_path / 'work' / 'EP007.wav')
        assert Path(out).parent.is_dir()
        assert calls == [(str(video), out)]

    def test_missing_media_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError) as exc:
            ev._resolve_audio({'id': 'EP001'}, str(tmp_path))
        assert 'EP001' in str(exc.value)


class TestEvaluateEpisode:
    def test_computes_cer_boundaries_and_metadata(self, tmp_path):
        ref = _ref_srt(tmp_path)
        audio = tmp_path / 'a.wav'
        audio.write_bytes(b'')
        calls = {}

        class Router:
            def transcribe(self, path, **kwargs):
                calls.update(path=path, **kwargs)
                return _result(text='こんにちは世界')

        row = ev.evaluate_episode(
            Router(),
            {'id': 'EP001', 'category': 'clean', 'audio': str(audio),
             'reference_srt': str(ref), 'context': 'ctx'},
            language='Japanese', work_dir=str(tmp_path))

        assert row['id'] == 'EP001'
        assert row['category'] == 'clean'
        assert row['engine'] == 'fake'
        assert row['model'] == 'fake-model'
        assert row['cer'] == 0.4
        assert row['cer_detail']['insertions'] == 2
        assert row['ref_cues'] == 1
        assert row['boundary']['mae_ms'] == 0.0
        assert row['warnings'] == ['w']
        assert row['rtf'] is not None
        assert calls['path'] == str(audio)
        assert calls['language'] == 'Japanese'
        assert calls['context'] == 'ctx'
        assert calls['timestamps'] is True

    def test_missing_reference_raises(self, tmp_path):
        audio = tmp_path / 'a.wav'
        audio.write_bytes(b'')
        with pytest.raises(FileNotFoundError) as exc:
            ev.evaluate_episode(
                object(), {'id': 'EP001', 'audio': str(audio),
                           'reference_srt': str(tmp_path / 'missing.srt')},
                language='Japanese', work_dir=str(tmp_path))
        assert 'reference_srt not found' in str(exc.value)

    def test_zero_duration_has_no_rtf(self, tmp_path):
        ref = _ref_srt(tmp_path)
        audio = tmp_path / 'a.wav'
        audio.write_bytes(b'')

        class Router:
            def transcribe(self, path, **kwargs):
                return _result(duration=0.0)

        row = ev.evaluate_episode(
            Router(), {'id': 'EP001', 'audio': str(audio),
                       'reference_srt': str(ref)},
            language='Japanese', work_dir=str(tmp_path))
        assert row['rtf'] is None


class TestSummaryAndMarkdown:
    def test_summary_all_errors(self):
        assert ev._summary([{'id': 'EP001', 'error': 'boom'}]) == {
            'episodes': 1, 'ok': 0, 'mean_cer': None}

    def test_summary_ignores_missing_mae_and_rtf(self):
        rows = [
            {'cer': 0.1, 'boundary': {'mae_ms': 100.0}, 'rtf': 0.5},
            {'cer': 0.3, 'boundary': {'mae_ms': None}, 'rtf': None},
        ]
        summary = ev._summary(rows)
        assert summary['ok'] == 2
        assert summary['mean_cer'] == 0.2
        assert summary['mean_mae_ms'] == 100.0
        assert summary['mean_rtf'] == 0.5

    def test_markdown_includes_ok_and_error_rows(self):
        rows = [
            {'id': 'EP001', 'category': 'clean', 'cer': 0.0,
             'cer_detail': {'ref_chars': 5}, 'ref_cues': 1,
             'asr_segments': 1, 'asr_tokens': 0,
             'boundary': {'mae_ms': 0.0, 'p90_ms': 0.0}, 'rtf': 0.1},
            {'id': 'EP002', 'category': 'bgm', 'error': 'boom'},
        ]
        text = ev._markdown(rows, {'episodes': 2, 'ok': 1}, {
            'project': 'demo', 'language': 'Japanese'})

        assert '# ASR evaluation' in text
        assert '- project: demo' in text
        assert '| EP001 | clean | 0.0000 | 5 | 1 | 1 | 0 | 0.0 | 0.0 | 0.1 |' in text
        assert '| EP002 | bgm | ERROR |' in text
        assert '## Errors' in text
        assert '- EP002: boom' in text


class TestMain:
    def _manifest(self, tmp_path, episodes):
        path = tmp_path / 'manifest.json'
        path.write_text(json.dumps({
            'project': 'demo', 'language': 'Japanese', 'episodes': episodes,
        }, ensure_ascii=False), encoding='utf-8')
        return path

    def _episode(self, tmp_path, ep_id='EP001'):
        audio = tmp_path / f'{ep_id}.wav'
        audio.write_bytes(b'')
        ref = _ref_srt(tmp_path)
        return {'id': ep_id, 'category': 'clean', 'audio': str(audio),
                'reference_srt': str(ref)}

    def test_list_engines(self, monkeypatch, capsys):
        entry = SimpleNamespace(
            name='qwen3', kind='asr',
            info=SimpleNamespace(capabilities=['timestamps'],
                                 auto_priority=10, description='demo'))
        monkeypatch.setattr('lib.asr.registry.entries', lambda: [entry])

        assert ev.main(['--manifest', 'unused', '--list-engines']) == 0
        assert 'qwen3\tasr\t10\ttimestamps\tdemo' in capsys.readouterr().out

    def test_manifest_without_episodes_returns_2(self, tmp_path, capsys):
        manifest = self._manifest(tmp_path, [])
        assert ev.main(['--manifest', str(manifest)]) == 2
        assert 'no episodes' in capsys.readouterr().err

    def test_main_writes_json_and_markdown(self, tmp_path, monkeypatch):
        manifest = self._manifest(tmp_path, [self._episode(tmp_path)])
        out_md = tmp_path / 'reports' / 'eval.md'
        out_json = tmp_path / 'reports' / 'eval.json'
        closed = []

        class FakeRouter:
            def __init__(self, engine, fallback, config):
                assert engine == 'fake'
                assert fallback == 'none'
                self.config = config

            def warmup(self):
                pass

            def close(self):
                closed.append(True)

            def transcribe(self, audio, **kwargs):
                return _result()

        monkeypatch.setattr(ev, 'ASRRouter', FakeRouter)

        code = ev.main([
            '--manifest', str(manifest), '--engine', 'fake',
            '--output', str(out_md), '--json-out', str(out_json),
            '--work-dir', str(tmp_path / 'work'),
        ])

        assert code == 0
        assert closed == [True]
        payload = json.loads(out_json.read_text(encoding='utf-8'))
        assert payload['summary']['episodes'] == 1
        assert payload['summary']['ok'] == 1
        assert payload['episodes'][0]['id'] == 'EP001'
        assert '| EP001 |' in out_md.read_text(encoding='utf-8')

    def test_main_records_errors_and_warmup_failure(self, tmp_path,
                                                    monkeypatch, capsys):
        good = self._episode(tmp_path, 'EP001')
        bad = {'id': 'EP002', 'category': 'bgm',
               'audio': str(tmp_path / 'missing.wav'),
               'reference_srt': str(tmp_path / 'missing.srt')}
        manifest = self._manifest(tmp_path, [good, bad])

        class FakeRouter:
            def __init__(self, *a, **k):
                pass

            def warmup(self):
                raise RuntimeError('no gpu')

            def close(self):
                pass

            def transcribe(self, audio, **kwargs):
                return _result()

        monkeypatch.setattr(ev, 'ASRRouter', FakeRouter)

        code = ev.main(['--manifest', str(manifest), '--engine', 'fake',
                        '--json-out', str(tmp_path / 'out.json')])

        assert code == 0
        captured = capsys.readouterr()
        assert 'warmup failed' in captured.err
        assert 'EP002 failed' in captured.err
        assert '# ASR evaluation' in captured.out
        payload = json.loads((tmp_path / 'out.json').read_text(
            encoding='utf-8'))
        assert payload['summary']['episodes'] == 2
        assert payload['summary']['ok'] == 1
        assert payload['episodes'][1]['error']

    def test_main_limit_trims_episodes(self, tmp_path, monkeypatch):
        episodes = [self._episode(tmp_path, f'EP{i:03d}') for i in (1, 2)]
        manifest = self._manifest(tmp_path, episodes)
        seen = []

        class FakeRouter:
            def __init__(self, *a, **k):
                pass

            def warmup(self):
                pass

            def close(self):
                pass

        def fake_eval(router, ep, **kwargs):
            seen.append(ep['id'])
            return {'id': ep['id'], 'category': ep['category'], 'cer': 0.0,
                    'cer_detail': {'ref_chars': 1}, 'ref_cues': 1,
                    'asr_segments': 0, 'asr_tokens': 0,
                    'boundary': {'mae_ms': None, 'p90_ms': None}, 'rtf': None}

        monkeypatch.setattr(ev, 'ASRRouter', FakeRouter)
        monkeypatch.setattr(ev, 'evaluate_episode', fake_eval)

        code = ev.main(['--manifest', str(manifest), '--limit', '1',
                        '--json-out', str(tmp_path / 'out.json')])

        assert code == 0
        assert seen == ['EP001']
        payload = json.loads((tmp_path / 'out.json').read_text(
            encoding='utf-8'))
        assert payload['summary']['episodes'] == 1
