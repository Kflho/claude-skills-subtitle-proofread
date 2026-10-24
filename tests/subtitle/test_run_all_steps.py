"""Tests for run_all.py orchestration — step order, dry-run, episode selection."""

import json
import sys
from types import SimpleNamespace

import fix.episode_workflow as ew
import fix.fix_orchestrator as fx
import run_all


def _resources(**overrides):
    base = {'has_target_subs': True, 'has_video': False, 'video_dir': None,
            'has_whisper': False, 'has_reference': False,
            'reference_dir': None}
    base.update(overrides)
    return base


def _patch_main(monkeypatch, calls, noun_results=None):
    def record(name, ret=None):
        def fake(*args, **kwargs):
            calls.append((name, args, kwargs))
            return ret
        return fake

    monkeypatch.setattr(run_all, 'step_scan', record('scan'))
    monkeypatch.setattr(run_all, 'step_fix_episodes',
                        record('fix', ['EP001', 'EP002']))
    monkeypatch.setattr(run_all, 'step_ai_review', record('ai_review'))
    monkeypatch.setattr(run_all, 'step_nouns',
                        record('nouns', noun_results or {}))
    monkeypatch.setattr(run_all, 'step_apply_all', record('apply', True))
    monkeypatch.setattr(run_all, 'step_deliver', record('deliver'))
    monkeypatch.setattr(run_all, 'step_clean', record('clean'))
    monkeypatch.setattr(run_all, 'step_ass_repair', record('ass'))
    monkeypatch.setattr(run_all, '_apply_ai_checklists',
                        record('checklists'))
    monkeypatch.setattr(run_all, 'print_ai_review_notice',
                        lambda *a, **k: None)
    monkeypatch.setattr(run_all, '_print_progress', lambda *a, **k: None)
    monkeypatch.setattr(run_all, 'detect_resources',
                        lambda *a, **k: _resources())
    monkeypatch.setattr(run_all, 'detect_format',
                        lambda *a, **k: {'primary': 'srt'})


def _names(calls):
    return [name for name, _, _ in calls]


def test_main_full_run_step_order(monkeypatch, tmp_path):
    calls = []
    _patch_main(monkeypatch, calls)
    monkeypatch.setattr(sys, 'argv', [
        'run_all.py', '--lang', 'ja', '--target-dir', str(tmp_path)])

    run_all.main()

    assert _names(calls) == ['scan', 'fix', 'ai_review', 'nouns', 'apply',
                             'deliver', 'clean']
    # processed episodes flow into deliver
    deliver = next(c for c in calls if c[0] == 'deliver')
    assert deliver[2]['processed_episodes'] == ['EP001', 'EP002']


def test_main_resume_skips_scan_and_fix(monkeypatch, tmp_path):
    calls = []
    _patch_main(monkeypatch, calls)
    monkeypatch.setattr(sys, 'argv', [
        'run_all.py', '--lang', 'ja', '--target-dir', str(tmp_path),
        '--resume'])

    run_all.main()

    assert _names(calls) == ['nouns', 'apply', 'deliver', 'clean']
    deliver = next(c for c in calls if c[0] == 'deliver')
    assert deliver[2]['processed_episodes'] is None


def test_main_dry_run_stops_before_fix_and_deliver(monkeypatch, tmp_path):
    calls = []
    _patch_main(monkeypatch, calls)
    monkeypatch.setattr(sys, 'argv', [
        'run_all.py', '--lang', 'ja', '--target-dir', str(tmp_path),
        '--dry-run'])

    run_all.main()

    assert _names(calls) == ['scan', 'nouns', 'apply']
    apply_call = next(c for c in calls if c[0] == 'apply')
    assert apply_call[2]['dry_run'] is True


def test_main_apply_ai_review_fast_path(monkeypatch, tmp_path):
    calls = []
    _patch_main(monkeypatch, calls)
    monkeypatch.setattr(sys, 'argv', [
        'run_all.py', '--lang', 'ja', '--target-dir', str(tmp_path),
        '--apply-ai-review'])

    run_all.main()

    assert _names(calls) == ['apply', 'checklists', 'clean']


def test_main_ass_project_runs_repair(monkeypatch, tmp_path):
    calls = []
    _patch_main(monkeypatch, calls)
    monkeypatch.setattr(run_all, 'detect_format',
                        lambda *a, **k: {'primary': 'ass'})
    monkeypatch.setattr(sys, 'argv', [
        'run_all.py', '--lang', 'ja', '--target-dir', str(tmp_path)])

    run_all.main()

    assert 'ass' in _names(calls)


def test_main_auto_lang_calls_detector(monkeypatch, tmp_path):
    calls = []
    _patch_main(monkeypatch, calls)
    seen = {}

    def fake_detect(project_dir):
        seen['dir'] = project_dir
        return 'zh'

    monkeypatch.setattr(run_all, 'detect_project_lang', fake_detect)
    # zh projects ask whether to run AI polish; answer no in tests
    monkeypatch.setattr('builtins.input', lambda *a, **k: 'n')
    monkeypatch.setattr(sys, 'argv', [
        'run_all.py', '--target-dir', str(tmp_path)])

    run_all.main()

    assert seen['dir'] == str(tmp_path)
    scan_call = next(c for c in calls if c[0] == 'scan')
    assert scan_call[1][1] == 'zh'


# ═══════════════════════════════════════════════════════════════
# step_fix_episodes
# ═══════════════════════════════════════════════════════════════

def _project_with_findings(project_dir, episodes):
    scans = project_dir / 'temp' / 'scans'
    scans.mkdir(parents=True)
    (scans / 'findings.json').write_text(json.dumps({
        'per_episode_issues': {ep: [] for ep in episodes},
    }, ensure_ascii=False), encoding='utf-8')


def _patch_pipeline(monkeypatch, calls, fail=()):
    def fake_pipeline(project_dir, episode, resources, args):
        calls.append((episode, args))
        if episode in fail:
            raise RuntimeError('boom')

    monkeypatch.setattr(ew, '_run_pipeline', fake_pipeline)


def test_step_fix_episodes_runs_selected_in_sorted_order(monkeypatch,
                                                         tmp_path):
    _project_with_findings(tmp_path, ['EP002', 'EP001'])
    calls = []
    _patch_pipeline(monkeypatch, calls)
    monkeypatch.setattr(fx, 'Fixer',
                        lambda *a, **k: SimpleNamespace(is_clean=lambda: False))

    selected = run_all.step_fix_episodes(
        str(tmp_path), 'ja', _resources(has_reference=True))

    assert selected == ['EP001', 'EP002']
    assert [ep for ep, _ in calls] == ['EP001', 'EP002']
    episode, args = calls[0]
    assert args.target_lang == 'ja'
    assert args.vad_clean_apply is False
    assert args.asr_engine == ''


def test_step_fix_episodes_skips_clean_episodes(monkeypatch, tmp_path):
    _project_with_findings(tmp_path, ['EP001', 'EP002'])
    calls = []
    _patch_pipeline(monkeypatch, calls)

    def fake_fixer(ep, *args, **kwargs):
        return SimpleNamespace(is_clean=lambda: ep == 'EP002')

    monkeypatch.setattr(fx, 'Fixer', fake_fixer)

    selected = run_all.step_fix_episodes(
        str(tmp_path), 'ja', _resources(has_reference=True))

    assert selected == ['EP001']
    assert [ep for ep, _ in calls] == ['EP001']


def test_step_fix_episodes_asr_route_bypasses_clean_check(monkeypatch,
                                                          tmp_path):
    _project_with_findings(tmp_path, ['EP001'])
    calls = []
    _patch_pipeline(monkeypatch, calls)

    def broken_fixer(*args, **kwargs):
        raise AssertionError('is_clean must not run on the ASR route')

    monkeypatch.setattr(fx, 'Fixer', broken_fixer)

    run_all.step_fix_episodes(
        str(tmp_path), 'ja', _resources(has_video=True),
        asr_engine='qwen3', asr_fix_text=True, vad_clean_apply=True)

    episode, args = calls[0]
    assert args.asr_engine == 'qwen3'
    assert args.asr_fix_text is True
    assert args.vad_clean_apply is True


def test_step_fix_episodes_without_audio_or_reference_skips(monkeypatch,
                                                            tmp_path):
    _project_with_findings(tmp_path, ['EP001'])
    calls = []
    _patch_pipeline(monkeypatch, calls)
    monkeypatch.setattr(fx, 'Fixer',
                        lambda *a, **k: SimpleNamespace(is_clean=lambda: False))

    selected = run_all.step_fix_episodes(
        str(tmp_path), 'ja', _resources())

    assert selected == ['EP001']
    assert calls == []


def test_step_fix_episodes_continues_after_failure(monkeypatch, tmp_path,
                                                   capsys):
    _project_with_findings(tmp_path, ['EP001', 'EP002'])
    calls = []
    _patch_pipeline(monkeypatch, calls, fail=('EP001',))
    monkeypatch.setattr(fx, 'Fixer',
                        lambda *a, **k: SimpleNamespace(is_clean=lambda: False))

    selected = run_all.step_fix_episodes(
        str(tmp_path), 'ja', _resources(has_reference=True))

    assert selected == ['EP001', 'EP002']
    assert [ep for ep, _ in calls] == ['EP001', 'EP002']
    assert 'EP001 FAILED' in capsys.readouterr().err


def test_step_fix_episodes_limit_and_start_from(monkeypatch, tmp_path):
    _project_with_findings(
        tmp_path, [f'EP{i:03d}' for i in range(1, 11)])
    calls = []
    _patch_pipeline(monkeypatch, calls)
    monkeypatch.setattr(fx, 'Fixer',
                        lambda *a, **k: SimpleNamespace(is_clean=lambda: False))
    resources = _resources(has_reference=True)

    assert run_all.step_fix_episodes(
        str(tmp_path), 'ja', resources, limit=2) == ['EP001', 'EP002']
    assert run_all.step_fix_episodes(
        str(tmp_path), 'ja', resources, start_from='EP005', limit=2) == [
        'EP005', 'EP006']


def test_step_fix_episodes_without_any_episodes(monkeypatch, tmp_path,
                                                capsys):
    monkeypatch.setattr(run_all, 'load_json', lambda path: None)
    calls = []
    _patch_pipeline(monkeypatch, calls)

    assert run_all.step_fix_episodes(str(tmp_path), 'ja', _resources()) == []
    assert 'No episodes to process' in capsys.readouterr().err
    assert calls == []
