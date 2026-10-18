"""Tests for scripts/eval/release_gate.py — CC-standard PASS/FAIL gate."""

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from eval import release_gate as gate  # noqa: E402


def payload(**splits):
    return {
        'splits': {
            split: {'p50_start_ms': values[0],
                    'p90_reliable_ms': values[1],
                    'micro_cer': values[2],
                    'char_coverage': values[3],
                    'match': values[4], 'cues': 100}
            for split, values in splits.items()
        },
        'episodes': [],
    }


GOOD_TEST = (80.0, 250.0, 0.20, 0.44, 50)


def test_manifest_key_is_stable():
    assert gate.manifest_key('eval/manifest.digimon.json') == 'manifest.digimon'
    assert gate.manifest_key(Path('a/b/manifest.whisper.json')) == 'manifest.whisper'


def test_extract_metrics_keeps_gate_fields_and_ratio():
    metrics = gate.extract_metrics(payload(test=GOOD_TEST))
    assert metrics['test']['p50_start_ms'] == 80.0
    assert metrics['test']['matched_cue_ratio'] == 0.5
    assert set(metrics['test']) >= set(gate.METRICS)


def test_evaluate_passes_when_under_targets():
    current = {'m': gate.extract_metrics(payload(test=GOOD_TEST))}
    rows = gate.evaluate(current, None)
    assert gate.failed(rows) == []


def test_evaluate_fails_a_target_breach():
    current = {'m': gate.extract_metrics(payload(test=(150.0, 250.0, 0.2,
                                                      0.44, 50)))}
    rows = gate.evaluate(current, None)
    failures = gate.failed(rows)
    assert len(failures) == 1
    assert failures[0]['metric'] == 'p50_start_ms'
    assert 'target' in failures[0]['reason']


def test_evaluate_does_not_apply_targets_to_dev():
    current = {'m': gate.extract_metrics(payload(dev=(900.0, 900.0, 0.4,
                                                      0.2, 10)))}
    assert gate.failed(gate.evaluate(current, None)) == []


def test_evaluate_flags_regression_against_baseline():
    baseline = {'m': gate.extract_metrics(payload(test=(50.0, 250.0, 0.2,
                                                       0.44, 50)))}
    current = {'m': gate.extract_metrics(payload(test=(80.0, 250.0, 0.2,
                                                      0.44, 50)))}
    rows = gate.evaluate(current, baseline)
    failures = gate.failed(rows)
    assert len(failures) == 1
    assert failures[0]['metric'] == 'p50_start_ms'
    assert 'regressed' in failures[0]['reason']


def test_evaluate_flags_coverage_drop():
    baseline = {'m': gate.extract_metrics(payload(test=GOOD_TEST))}
    current = {'m': gate.extract_metrics(payload(test=(80.0, 250.0, 0.2,
                                                       0.40, 50)))}
    failures = gate.failed(gate.evaluate(current, baseline))
    assert any(row['metric'] == 'char_coverage' for row in failures)


def test_main_exit_codes_and_baseline_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, 'collect',
                        lambda manifest, out_dir: payload(test=GOOD_TEST))
    baseline = tmp_path / 'baseline.json'
    out = tmp_path / 'reports'
    args = ['--manifests', 'm.json', '--baseline', str(baseline),
            '--out-dir', str(out)]

    assert gate.main(args + ['--update-baseline']) == 0
    assert baseline.exists()
    assert gate.main(args) == 0

    monkeypatch.setattr(
        gate, 'collect',
        lambda manifest, out_dir: payload(test=(150.0, 250.0, 0.2, 0.44, 50)))
    assert gate.main(args) == 1


def test_main_ignores_baseline_without_metrics(tmp_path, monkeypatch,
                                               capsys):
    monkeypatch.setattr(gate, 'collect',
                        lambda manifest, out_dir: payload(test=GOOD_TEST))
    bad = tmp_path / 'baseline.json'
    bad.write_text(json.dumps({'splits': {}}), encoding='utf-8')
    code = gate.main(['--manifests', 'm.json', '--baseline', str(bad),
                      '--out-dir', str(tmp_path / 'reports')])
    assert code == 0
    assert 'ignoring baseline' in capsys.readouterr().err
