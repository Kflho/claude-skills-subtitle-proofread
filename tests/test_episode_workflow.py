"""Tests for fix/episode_workflow.py — resource-driven step selection."""

from types import SimpleNamespace

import fix.episode_workflow as ew
import fix.fix_orchestrator as fx
from fix.fix_orchestrator import FixReport


def _args(**overrides):
    base = {'step': None, 'dry_run': False, 'video_dir': None,
            'target_lang': 'ja', 'skip_whisper': False,
            'vad_clean_apply': False, 'asr_engine': '', 'asr_python': '',
            'asr_fix_text': False, 'no_backup': False}
    base.update(overrides)
    return SimpleNamespace(**base)


def _resources(**overrides):
    base = {'has_video': False, 'has_whisper': False, 'has_reference': False,
            'video_dir': None}
    base.update(overrides)
    return base


def _patch_steps(monkeypatch, calls, report=None):
    report = report or FixReport(source='audio', applied=2, ai_review=1,
                                 failed=1)

    def step_audio(project_dir, episode, scan_result, dry_run=False,
                   video_dir=None, target_lang='ja', vad_clean_apply=False,
                   asr_engine='', asr_python='', asr_fix_text=False):
        calls.append(('audio', {'scan_result': scan_result,
                                'dry_run': dry_run,
                                'target_lang': target_lang,
                                'vad_clean_apply': vad_clean_apply,
                                'asr_engine': asr_engine}))
        return report

    def step_translate(project_dir, episode, scan_result, dry_run=False,
                       target_lang='ja'):
        calls.append(('translate', {'dry_run': dry_run}))
        return 'zh/EP001.srt'

    def step_compare(project_dir, episode, scan_result, translated_path,
                     dry_run=False, target_lang='ja'):
        calls.append(('compare', {'translated_path': translated_path}))
        return [{'start': '1'}]

    def step_apply(project_dir, episode, fixes_or_report, scan_result,
                   no_backup=False):
        calls.append(('apply', {'report': fixes_or_report}))
        return [{'start': '1'}], [{'item': 1}]

    def step_diff(project_dir, episode, scan_result, applied):
        calls.append(('diff', {'applied': applied}))

    def print_prompt(project_dir, episode, items):
        calls.append(('ai-review', {'items': items}))

    monkeypatch.setattr(ew, 'step_audio', step_audio)
    monkeypatch.setattr(ew, 'step_translate', step_translate)
    monkeypatch.setattr(ew, 'step_compare', step_compare)
    monkeypatch.setattr(ew, 'step_apply', step_apply)
    monkeypatch.setattr(ew, 'step_diff', step_diff)
    monkeypatch.setattr(ew, '_print_ai_review_prompt', print_prompt)
    monkeypatch.setattr(ew, 'load_json', lambda path: None)


def _names(calls):
    return [name for name, _ in calls]


# ═══════════════════════════════════════════════════════════════
# Step selection
# ═══════════════════════════════════════════════════════════════

def test_full_audio_route(monkeypatch):
    calls = []
    _patch_steps(monkeypatch, calls)

    ew._run_pipeline('/proj', 'EP001', _resources(has_video=True,
                                                  has_whisper=True), _args())

    assert _names(calls) == ['audio', 'apply', 'diff']
    apply_call = next(c for c in calls if c[0] == 'apply')
    assert apply_call[1]['report'].applied == 2
    diff_call = next(c for c in calls if c[0] == 'diff')
    assert diff_call[1]['applied'] == [{'start': '1'}]


def test_translate_route_for_non_japanese(monkeypatch):
    calls = []
    _patch_steps(monkeypatch, calls)

    ew._run_pipeline('/proj', 'EP001',
                     _resources(has_video=True, has_whisper=True),
                     _args(target_lang='zh'))

    assert _names(calls) == ['audio', 'translate', 'apply', 'diff']


def test_no_audio_resources_goes_straight_to_apply(monkeypatch):
    calls = []
    _patch_steps(monkeypatch, calls)

    ew._run_pipeline('/proj', 'EP001', _resources(), _args())

    assert _names(calls) == ['apply', 'diff']


def test_skip_whisper_flag_removes_audio_step(monkeypatch):
    calls = []
    _patch_steps(monkeypatch, calls)

    ew._run_pipeline('/proj', 'EP001',
                     _resources(has_video=True, has_whisper=True),
                     _args(skip_whisper=True))

    assert _names(calls) == ['apply', 'diff']


def test_asr_route_needs_video_only(monkeypatch):
    calls = []
    _patch_steps(monkeypatch, calls)

    ew._run_pipeline('/proj', 'EP001', _resources(has_video=True),
                     _args(asr_engine='qwen3', asr_fix_text=True))

    assert _names(calls) == ['audio', 'apply', 'diff']
    audio_call = next(c for c in calls if c[0] == 'audio')
    assert audio_call[1]['asr_engine'] == 'qwen3'


def test_asr_route_without_video_skips_audio(monkeypatch):
    calls = []
    _patch_steps(monkeypatch, calls)

    ew._run_pipeline('/proj', 'EP001', _resources(), _args(asr_engine='qwen3'))

    assert _names(calls) == ['apply', 'diff']


def test_explicit_step_runs_only_that_step(monkeypatch):
    calls = []
    _patch_steps(monkeypatch, calls)

    ew._run_pipeline('/proj', 'EP001', _resources(), _args(step='compare'))

    assert _names(calls) == ['compare']
    assert calls[0][1]['translated_path'] is None


def test_dry_run_skips_apply_and_summary(monkeypatch, capsys):
    calls = []
    _patch_steps(monkeypatch, calls)

    ew._run_pipeline('/proj', 'EP001',
                     _resources(has_video=True, has_whisper=True),
                     _args(dry_run=True))

    assert _names(calls) == ['audio', 'diff']
    out = capsys.readouterr().out
    assert 'DRY RUN' in out
    assert 'Done:' not in out


def test_summary_counts_come_from_fix_report(monkeypatch, capsys):
    calls = []
    report = FixReport(source='audio', applied=7, ai_review=2, failed=3)
    _patch_steps(monkeypatch, calls, report=report)

    ew._run_pipeline('/proj', 'EP001',
                     _resources(has_video=True, has_whisper=True), _args())

    out = capsys.readouterr().out
    assert 'Done: 7 auto-keep, 2 AI review, 3 → L6 human' in out


def test_findings_are_passed_as_scan_result(monkeypatch):
    calls = []
    _patch_steps(monkeypatch, calls)
    monkeypatch.setattr(ew, 'load_json', lambda path: {
        'per_episode_issues': {'EP001': [{'type': 'garbled'}]},
    })

    ew._run_pipeline('/proj', 'EP001',
                     _resources(has_video=True, has_whisper=True), _args())

    audio_call = next(c for c in calls if c[0] == 'audio')
    assert audio_call[1]['scan_result'] == {'issues': [{'type': 'garbled'}]}


# ═══════════════════════════════════════════════════════════════
# step_audio — dispatch to Fixer
# ═══════════════════════════════════════════════════════════════

def _install_fake_fixer(monkeypatch):
    created = []

    class FakeFixer:
        def __init__(self, episode, project_dir, **kwargs):
            self.episode = episode
            self.kwargs = kwargs
            self.whisper_calls = []
            self.asr_calls = []
            created.append(self)

        def fix_by_whisper(self, **kwargs):
            self.whisper_calls.append(kwargs)
            return FixReport(source='whisper', applied=3, ai_review=1,
                             failed=2)

        def fix_by_asr(self, write_text=False):
            self.asr_calls.append({'write_text': write_text})
            return FixReport(source='asr', applied=5)

    monkeypatch.setattr(fx, 'Fixer', FakeFixer)
    return created


def test_step_audio_dispatches_garbled_cues_to_whisper(monkeypatch):
    created = _install_fake_fixer(monkeypatch)
    scan = {'issues': [{'type': 'garbled'}, {'type': 'missing_subtitle'}]}

    report = ew.step_audio('/proj', 'EP001', scan, target_lang='ja',
                           vad_clean_apply=True)

    assert report.applied == 3
    assert created[0].whisper_calls == [{
        'separate_vocals': True, 'vad_clean_apply': True}]


def test_step_audio_skips_when_only_missing_subtitles(monkeypatch):
    created = _install_fake_fixer(monkeypatch)

    report = ew.step_audio('/proj', 'EP001',
                           {'issues': [{'type': 'missing_subtitle'}]})

    assert report.applied == 0
    assert created[0].whisper_calls == []   # nothing dispatched to Whisper


def test_step_audio_dry_run_reports_garbled_count(monkeypatch):
    created = _install_fake_fixer(monkeypatch)

    report = ew.step_audio('/proj', 'EP001',
                           {'issues': [{'type': 'garbled'}]}, dry_run=True)

    assert report.applied == 1
    assert created[0].whisper_calls == []


def test_step_audio_asr_route_calls_fix_by_asr(monkeypatch):
    created = _install_fake_fixer(monkeypatch)

    report = ew.step_audio('/proj', 'EP001', None, asr_engine='qwen3',
                           asr_fix_text=True)

    assert report.applied == 5
    assert created[0].asr_calls == [{'write_text': True}]


def test_step_audio_asr_dry_run_does_not_call_model(monkeypatch):
    created = _install_fake_fixer(monkeypatch)

    report = ew.step_audio('/proj', 'EP001', None, dry_run=True,
                           asr_engine='qwen3')

    assert report.applied == 0
    assert created[0].asr_calls == []


# ═══════════════════════════════════════════════════════════════
# step_apply — report only, never rewrites SRT here
# ═══════════════════════════════════════════════════════════════

def test_step_apply_no_fixes(capsys):
    applied, items = ew.step_apply('/proj', 'EP001',
                                   FixReport(source='audio'), None)
    assert (applied, items) == ([], [])
    assert 'No fixes to report' in capsys.readouterr().out


def test_step_apply_logs_counts(capsys):
    report = FixReport(source='audio', applied=2, ai_review=1, failed=3)
    applied, items = ew.step_apply('/proj', 'EP001', report, None)
    assert (applied, items) == ([], [])
    out = capsys.readouterr().out
    assert '2 fixed, 1 AI review, 3 unfixable' in out
