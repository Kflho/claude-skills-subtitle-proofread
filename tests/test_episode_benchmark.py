"""Tests for scripts/eval/episode_benchmark.py — held-out benchmark (read-only)."""

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from eval.episode_benchmark import (  # noqa: E402
    aggregate, evaluate_episode, main, window_cues,
)
from lib.asr.base import ASRResult  # noqa: E402


SRT = (
    "1\n00:01:41,000 --> 00:01:42,000\nおはよう\n\n"
    "2\n00:01:45,000 --> 00:01:46,000\nさようなら\n\n"
)


def asr_result(tokens, duration=10.0) -> ASRResult:
    from lib.asr.base import Token
    return ASRResult(
        text=''.join(t[0] for t in tokens),
        language='Japanese',
        engine='fake',
        model='fake-1',
        audio_duration=duration,
        tokens=[Token(t, s, e) for t, s, e in tokens],
    )


@pytest.fixture
def episode(tmp_path):
    srt = tmp_path / 'EP001.main.srt'
    srt.write_text(SRT, encoding='utf-8')
    asr = tmp_path / 'asr.json'
    payload = {
        'schema_version': 1,
        'text': 'おはようさようなら',
        'language': 'Japanese',
        'engine': 'fake',
        'model': 'fake-1',
        'audio_duration': 10.0,
        'tokens': [
            {'text': 'おはよう', 'start': 1.0, 'end': 2.0},
            {'text': 'さようなら', 'start': 5.0, 'end': 6.0},
        ],
        'segments': [],
    }
    asr.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    return {
        'id': 'EP001',
        'split': 'dev',
        'category': 'unclassified',
        'reference_srt': str(srt),
        'main_window': [100, 110],
        'asr_json': str(asr),
    }


# ═══════════════════════════════════════════════════════════════
# Window handling
# ═══════════════════════════════════════════════════════════════

def test_window_cues_shifts_and_clamps_edges():
    cues = [
        {'start_s': 99.0, 'end_s': 101.0, 'text': 'a'},
        {'start_s': 104.0, 'end_s': 105.0, 'text': 'b'},
        {'start_s': 111.0, 'end_s': 112.0, 'text': 'c'},
    ]
    out = window_cues(cues, [100, 110])
    assert len(out) == 2
    assert out[0]['start_s'] == 0.0
    assert out[0]['end_s'] == 1.0
    assert out[1]['start_s'] == 4.0
    assert out[1]['end_s'] == 5.0


def test_window_cues_rejects_empty_window():
    with pytest.raises(ValueError):
        window_cues([], [10, 10])


# ═══════════════════════════════════════════════════════════════
# Metrics
# ═══════════════════════════════════════════════════════════════

def test_evaluate_episode_scores_cer_and_boundaries(episode):
    result = asr_result([('おはよう', 1.0, 2.0),
                         ('さようなら', 5.0, 6.0)])
    row = evaluate_episode(episode, result)
    assert row['id'] == 'EP001'
    assert row['split'] == 'dev'
    assert row['ref_cues'] == 2
    assert row['cer'] == 0.0
    assert row['alignment']['status']['match'] == 2
    assert row['alignment']['boundary']['start']['n'] == 2
    assert row['alignment']['reliable']['n'] == 2
    assert row['plan']['end_truncating_auto'] == 0
    assert row['token_quality']['units'] == 2
    assert row['timeline']['source'] == 'tokens'


def test_evaluate_episode_reports_cer_edits(episode):
    result = asr_result([('おはよう', 1.0, 2.0),
                         ('さよなら', 5.0, 6.0)])
    row = evaluate_episode(episode, result)
    assert row['cer'] > 0
    assert row['cer_detail']['distance'] > 0
    assert row['cer_detail']['ref_chars'] == row['ref_chars']


def test_evaluate_episode_requires_reference(episode):
    episode['reference_srt'] = str(Path(episode['reference_srt']).parent / 'nope.srt')
    with pytest.raises(FileNotFoundError):
        evaluate_episode(episode, asr_result([('おはよう', 1.0, 2.0)]))


def test_aggregate_by_split_uses_micro_cer(episode):
    row = evaluate_episode(episode, asr_result([('おはよう', 1.0, 2.0),
                                                ('さようなら', 5.0, 6.0)]))
    other = dict(row, id='EP002', split='test', cer=0.5,
                 cer_detail={'ref_chars': 10, 'distance': 5},
                 alignment={'status': {'match': 1}, 'clean_ratio': 0.1,
                            'boundary': {'start': {'p50_ms': 100.0,
                                                   'p90_ms': 200.0,
                                                   'hit_100': 0.5}},
                            'reliable': {'n': 1, 'p90_ms': 150.0,
                                         'hit_100': 0.6}},
                 plan={'auto': 2},
                 token_quality={'zero_ratio': 0.2}, rtf=0.1)
    agg = aggregate([row, other])
    assert agg['dev']['episodes'] == 1
    assert agg['test']['micro_cer'] == 0.5
    assert agg['dev']['micro_cer'] == 0.0
    assert agg['test']['auto_fix'] == 2


# ═══════════════════════════════════════════════════════════════
# CLI + read-only contract
# ═══════════════════════════════════════════════════════════════

def _manifest(tmp_path, ep) -> Path:
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps({'project': 'test', 'language': 'Japanese',
                                'episodes': [ep]}), encoding='utf-8')
    return path


def test_main_writes_report_and_never_touches_inputs(episode, tmp_path):
    manifest = _manifest(tmp_path, episode)
    before_manifest = manifest.read_text(encoding='utf-8')
    before_asr = Path(episode['asr_json']).read_text(encoding='utf-8')
    before_srt = Path(episode['reference_srt']).read_text(encoding='utf-8')
    out_json = tmp_path / 'report.json'
    out_md = tmp_path / 'report.md'

    code = main(['--manifest', str(manifest),
                 '--json-out', str(out_json), '--output', str(out_md)])
    assert code == 0
    assert manifest.read_text(encoding='utf-8') == before_manifest
    assert Path(episode['asr_json']).read_text(encoding='utf-8') == before_asr
    assert Path(episode['reference_srt']).read_text(encoding='utf-8') == before_srt

    payload = json.loads(out_json.read_text(encoding='utf-8'))
    assert payload['read_only'] is True
    assert payload['splits']['dev']['episodes'] == 1
    assert 'threshold' not in json.dumps(payload).lower()
    assert 'EP001' in out_md.read_text(encoding='utf-8')


def test_main_reports_missing_asr_without_transcribe(tmp_path):
    srt = tmp_path / 'ref.srt'
    srt.write_text(SRT, encoding='utf-8')
    ep = {'id': 'EP009', 'split': 'test', 'reference_srt': str(srt),
          'main_window': [100, 110],
          'asr_json': str(tmp_path / 'missing.json')}
    manifest = _manifest(tmp_path, ep)
    out = tmp_path / 'report.json'

    code = main(['--manifest', str(manifest), '--json-out', str(out)])
    assert code == 1
    payload = json.loads(out.read_text(encoding='utf-8'))
    assert 'error' in payload['episodes'][0]


def test_main_rejects_manifest_without_episodes(tmp_path):
    manifest = tmp_path / 'manifest.json'
    manifest.write_text('{"episodes": []}', encoding='utf-8')
    assert main(['--manifest', str(manifest)]) == 2


def test_main_records_policy_snapshot_for_ab(episode, tmp_path):
    manifest = _manifest(tmp_path, episode)
    out = tmp_path / 'report.json'
    code = main(['--manifest', str(manifest), '--json-out', str(out),
                 '--repair-overlong', '--timeline-source', 'tokens'])
    assert code == 0
    payload = json.loads(out.read_text(encoding='utf-8'))
    assert payload['policy']['repair_overlong'] is True
    assert payload['policy']['force_source'] == 'tokens'
    assert payload['read_only'] is True


def test_evaluate_episode_accepts_forced_segment_source(episode):
    from lib.alignment.quality import TimelinePolicy
    result = asr_result([('おはよう', 1.0, 2.0)])
    row = evaluate_episode(episode, result, policy=TimelinePolicy(
        force_source='segments'))
    # No segments in the fixture: the builder keeps the token data rather
    # than inventing an empty axis.
    assert row['timeline']['source'] == 'tokens'
    assert row['global_offset'] is None


def _write_srt(path, cues):
    lines = []
    for index, (start, end, text) in enumerate(cues, 1):
        def tc(value):
            ms = int(round(value * 1000))
            h, rem = divmod(ms, 3600000)
            m, rem = divmod(rem, 60000)
            s, ms = divmod(rem, 1000)
            return f'{h:02d}:{m:02d}:{s:02d},{ms:03d}'
        lines.append(f'{index}\n{tc(start)} --> {tc(end)}\n{text}\n')
    path.write_text('\n'.join(lines), encoding='utf-8')


def test_evaluate_episode_applies_global_offset(tmp_path):
    cues = [(10.0 + i * 3, 11.0 + i * 3, f'あ{i}') for i in range(22)]
    srt = tmp_path / 'ref.srt'
    _write_srt(srt, cues)
    ep = {'id': 'EP010', 'split': 'dev', 'reference_srt': str(srt),
          'main_window': [0, 100]}
    result = asr_result([(text, start + 0.5, end + 0.5)
                         for start, end, text in cues], duration=100.0)

    raw = evaluate_episode(ep, result)
    shifted = evaluate_episode(ep, result, apply_global_offset=True)
    assert raw['global_offset'] is None
    assert shifted['global_offset'] == pytest.approx(0.5, abs=0.01)
    assert (shifted['alignment']['boundary']['start']['p50_ms']
            < raw['alignment']['boundary']['start']['p50_ms'])
