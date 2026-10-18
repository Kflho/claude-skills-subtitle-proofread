"""Tests for fix/episode_workflow.py — resource-driven step selection."""

import json
import os
import sys
from types import SimpleNamespace

import pytest

import lib.project_utils as pu
import fix.episode_workflow as ew
import fix.fix_orchestrator as fx
from fix.fix_orchestrator import FixReport


def _args(**overrides):
    base = {'step': None, 'dry_run': False, 'video_dir': None,
            'target_lang': 'ja', 'skip_whisper': False,
            'vad_clean_apply': False, 'asr_engine': '', 'asr_python': '',
            'asr_fix_text': False, 'asr_global_offset': False,
            'no_backup': False}
    base.update(overrides)
    return SimpleNamespace(**base)


def _resources(**overrides):
    base = {'has_target_subs': False, 'has_video': False,
            'has_whisper': False, 'has_reference': False, 'video_dir': None}
    base.update(overrides)
    return base


def _patch_steps(monkeypatch, calls, report=None):
    report = report or FixReport(source='audio', applied=2, ai_review=1,
                                 failed=1)

    def step_audio(project_dir, episode, scan_result, dry_run=False,
                   video_dir=None, target_lang='ja', vad_clean_apply=False,
                   asr_engine='', asr_python='', asr_fix_text=False,
                   asr_global_offset=False):
        calls.append(('audio', {'scan_result': scan_result,
                                'dry_run': dry_run,
                                'target_lang': target_lang,
                                'vad_clean_apply': vad_clean_apply,
                                'asr_engine': asr_engine,
                                'asr_global_offset': asr_global_offset}))
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

        def fix_by_asr(self, write_text=False, global_offset=None):
            self.asr_calls.append({'write_text': write_text,
                                   'global_offset': global_offset})
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
    assert created[0].asr_calls == [{'write_text': True,
                                     'global_offset': False}]


def test_step_audio_asr_route_threads_global_offset(monkeypatch):
    created = _install_fake_fixer(monkeypatch)

    ew.step_audio('/proj', 'EP001', None, asr_engine='qwen3',
                  asr_global_offset=True)

    assert created[0].asr_calls == [{'write_text': False,
                                     'global_offset': True}]


def test_step_audio_asr_route_surfaces_early_exit_reason(monkeypatch,
                                                         capsys):
    class FakeFixer:
        def __init__(self, episode, project_dir, **kwargs):
            pass

        def fix_by_asr(self, **kwargs):
            return FixReport(
                source='asr',
                details=['ASS in-place time fixes are not supported'])

    monkeypatch.setattr(fx, 'Fixer', FakeFixer)
    report = ew.step_audio('/proj', 'EP001', None, asr_engine='qwen3')

    assert report.applied == 0
    out = capsys.readouterr().out
    assert 'ASS in-place time fixes are not supported' in out


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


def test_step_apply_with_failures_but_no_fixes_still_logs(capsys):
    report = FixReport(source='audio', ai_review=1, failed=2)
    applied, items = ew.step_apply('/proj', 'EP001', report, None)
    assert (applied, items) == ([], [])
    assert '0 fixed, 1 AI review, 2 unfixable' in capsys.readouterr().out


# ═══════════════════════════════════════════════════════════════
# step_translate — reference subtitle → Japanese
# ═══════════════════════════════════════════════════════════════

def _ref_project(tmp_path, *names):
    ref_dir = tmp_path / '参考字幕'
    ref_dir.mkdir(exist_ok=True)
    for name in names:
        (ref_dir / name).write_text(
            '1\n00:00:01,000 --> 00:00:02,000\nhello\n', encoding='utf-8')
    return ref_dir


def _translated_path(tmp_path, episode='EP001'):
    return os.path.join(str(tmp_path), 'temp', 'translations',
                        f'{episode}_translated.srt')


def _install_fake_subprocess(monkeypatch, returncode=0, error=None):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append({'cmd': cmd, **kwargs})
        if error is not None:
            raise error
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(ew.subprocess, 'run', fake_run)
    return calls


@pytest.mark.parametrize('scan', [None, {}, {'issues': []}])
def test_step_translate_without_issues_is_noop(scan, tmp_path, capsys):
    assert ew.step_translate(str(tmp_path), 'EP001', scan) is None
    assert 'No issues' in capsys.readouterr().out


def test_step_translate_without_reference_dir(tmp_path, capsys):
    assert ew.step_translate(str(tmp_path), 'EP001',
                             {'issues': [{'type': 'garbled'}]}) is None
    assert 'No 参考字幕/' in capsys.readouterr().out


def test_step_translate_without_matching_reference_srt(tmp_path, capsys):
    _ref_project(tmp_path, 'EP999_ref.srt')

    assert ew.step_translate(str(tmp_path), 'EP001',
                             {'issues': [{'type': 'garbled'}]}) is None
    assert 'No reference SRT found for EP001' in capsys.readouterr().out


def test_step_translate_dry_run_previews_without_writing(monkeypatch, tmp_path,
                                                         capsys):
    _ref_project(tmp_path, 'EP001_ref.srt')
    calls = _install_fake_subprocess(monkeypatch)

    out_path = ew.step_translate(str(tmp_path), 'EP001',
                                 {'issues': [{'type': 'garbled'}]},
                                 dry_run=True)

    assert out_path == _translated_path(tmp_path)
    assert not os.path.exists(out_path)      # dry-run never writes the SRT
    assert calls == []                       # ...and never spawns the translator
    out = capsys.readouterr().out
    assert 'DRY RUN' in out
    assert 'EP001_ref.srt' in out
    assert '--to ja' in out


def test_step_translate_runs_translator_with_project_pythonpath(
        monkeypatch, tmp_path, capsys):
    _ref_project(tmp_path, 'EP001_ref.srt')
    monkeypatch.setenv('PYTHONPATH', 'preexisting-path')
    calls = _install_fake_subprocess(monkeypatch)

    out_path = ew.step_translate(str(tmp_path), 'EP001',
                                 {'issues': [{'type': 'garbled'}]},
                                 target_lang='zh')

    assert out_path == _translated_path(tmp_path)
    assert len(calls) == 1
    call = calls[0]
    assert call['cmd'][0] == 'python'
    assert call['cmd'][1] == os.path.join(ew._SCRIPT_DIR, 'translate_srt.py')
    assert call['cmd'][2] == os.path.join(
        str(tmp_path), '参考字幕', 'EP001_ref.srt')
    assert call['cmd'][3:5] == ['--output', out_path]
    assert call['cmd'][5:] == ['--to', 'zh']
    assert call['cwd'] == str(tmp_path)
    assert call['capture_output'] is False
    assert call['timeout'] == 1800
    # translator must be importable from scripts/ and keep the existing path
    assert call['env']['PYTHONPATH'] == os.pathsep.join(
        [ew._SCRIPTS_DIR, 'preexisting-path'])
    assert 'Translating: EP001_ref.srt → zh' in capsys.readouterr().out


def test_step_translate_keeps_existing_pythonpath_when_scripts_present(
        monkeypatch, tmp_path):
    _ref_project(tmp_path, 'EP001_ref.srt')
    existing = os.pathsep.join([ew._SCRIPT_DIR, 'preexisting-path'])
    monkeypatch.setenv('PYTHONPATH', existing)
    calls = _install_fake_subprocess(monkeypatch)

    ew.step_translate(str(tmp_path), 'EP001',
                      {'issues': [{'type': 'garbled'}]})

    assert calls[0]['env']['PYTHONPATH'] == existing


def test_step_translate_nonzero_exit_returns_none(monkeypatch, tmp_path,
                                                  capsys):
    _ref_project(tmp_path, 'EP001_ref.srt')
    _install_fake_subprocess(monkeypatch, returncode=3)

    assert ew.step_translate(str(tmp_path), 'EP001',
                             {'issues': [{'type': 'garbled'}]}) is None
    assert 'failed (exit 3)' in capsys.readouterr().out


def test_step_translate_subprocess_error_returns_none(monkeypatch, tmp_path,
                                                      capsys):
    _ref_project(tmp_path, 'EP001_ref.srt')
    _install_fake_subprocess(monkeypatch, error=OSError('no python'))

    assert ew.step_translate(str(tmp_path), 'EP001',
                             {'issues': [{'type': 'garbled'}]}) is None
    assert '[translate] Error: no python' in capsys.readouterr().out


# ═══════════════════════════════════════════════════════════════
# step_compare — delegate comparison to Fixer
# ═══════════════════════════════════════════════════════════════

@pytest.mark.parametrize('path_kind', ['none', 'missing'])
def test_step_compare_without_usable_reference(path_kind, tmp_path, capsys):
    translated = None if path_kind == 'none' else str(
        tmp_path / 'not-there.srt')

    assert ew.step_compare(str(tmp_path), 'EP001', None, translated) == []
    assert 'No translated reference available' in capsys.readouterr().out


def test_step_compare_dry_run_never_builds_fixer(monkeypatch, tmp_path,
                                                 capsys):
    translated = tmp_path / 'translated.srt'
    translated.write_text('1\n', encoding='utf-8')

    class ExplodingFixer:
        def __init__(self, *args, **kwargs):
            raise AssertionError('Fixer must not be built on a dry run')

    monkeypatch.setattr(fx, 'Fixer', ExplodingFixer)

    assert ew.step_compare(str(tmp_path), 'EP001', None, str(translated),
                           dry_run=True) == []
    assert 'DRY RUN' in capsys.readouterr().out


def test_step_compare_delegates_to_fix_by_reference(monkeypatch, tmp_path,
                                                    capsys):
    translated = tmp_path / 'translated.srt'
    translated.write_text('1\n', encoding='utf-8')
    report = FixReport(source='reference', applied=2, failed=1)
    created = []

    class FakeFixer:
        def __init__(self, episode, project_dir, **kwargs):
            created.append(('init', episode, project_dir, kwargs))

        def fix_by_reference(self, path):
            created.append(('compare', path))
            return report

    monkeypatch.setattr(fx, 'Fixer', FakeFixer)

    result = ew.step_compare(str(tmp_path), 'EP001', None, str(translated),
                             target_lang='zh')

    assert result is report
    assert created == [
        ('init', 'EP001', str(tmp_path), {'target_lang': 'zh'}),
        ('compare', str(translated)),
    ]
    assert '2 fixed, 1 suspicious' in capsys.readouterr().out


# ═══════════════════════════════════════════════════════════════
# step_audio — Fixer context forwarding
# ═══════════════════════════════════════════════════════════════

def test_step_audio_forwards_project_context_to_fixer(monkeypatch):
    created = _install_fake_fixer(monkeypatch)

    ew.step_audio('/proj', 'EP001', None, video_dir='/videos',
                  target_lang='zh', asr_python='python -m asr')

    assert created[0].kwargs == {
        'target_lang': 'zh',
        'video_dir': '/videos',
        'asr_engine': '',
        'asr_python': 'python -m asr',
    }
    assert created[0].whisper_calls == []


# ═══════════════════════════════════════════════════════════════
# _write_ai_review_json — AI review side file
# ═══════════════════════════════════════════════════════════════

def test_write_ai_review_json_without_items_writes_nothing(tmp_path):
    ew._write_ai_review_json(str(tmp_path), 'EP001', [])

    assert not (tmp_path / 'temp').exists()


def test_write_ai_review_json_serializes_items_with_context(tmp_path, capsys):
    items = [{
        'start': '00:02:00.000', 'end': '00:02:02.000',
        'original': 'o' * 200, 'replacement': 'r' * 200,
        'confidence': 0.42, 'model': 'tier2',
        'avg_logprob': -1.1, 'no_speech_prob': 0.2,
        'compression_ratio': 2.4, 'flag_reasons': ['low_conf'],
    }]
    cues = ['not-a-dict',
            {'start_s': 10.0, 'text': 'previous line'},
            {'start_s': 121.0, 'text': 'too close to use'},
            {'start_s': 200.0, 'text': 'next line'}]

    ew._write_ai_review_json(str(tmp_path), 'EP001', items, cues=cues)

    path = tmp_path / 'temp' / 'scans' / 'EP001_ai_review.json'
    assert path.exists()
    data = json.loads(path.read_text(encoding='utf-8'))
    assert data['episode'] == 'EP001'
    assert data['total'] == 1
    assert data['generated']
    item = data['items'][0]
    assert item['original'] == 'o' * 120        # long text is clipped
    assert item['replacement'] == 'r' * 120
    assert item['pipeline_confidence'] == 0.42
    assert item['model_tier'] == 'tier2'
    assert item['avg_logprob'] == -1.1
    assert item['no_speech_prob'] == 0.2
    assert item['compression_ratio'] == 2.4
    assert item['flag_reasons'] == ['low_conf']
    assert item['context_before'] == 'previous line'
    assert item['context_after'] == 'next line'
    assert str(path) in capsys.readouterr().err


def test_write_ai_review_json_defaults_and_survives_bad_timecode(tmp_path):
    items = [{'start': 'not-a-timecode'}, {'start': '00:00:05.000'}]

    # a cue list forces the context block to run; the bad start timecode makes
    # to_seconds() raise, which must not abort the review file
    ew._write_ai_review_json(str(tmp_path), 'EP002', items,
                             cues=[{'start_s': 5.0, 'text': 'ctx'}])

    path = tmp_path / 'temp' / 'scans' / 'EP002_ai_review.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    first = data['items'][0]
    assert first['end'] == ''
    assert first['original'] == ''
    assert first['replacement'] == ''
    assert first['pipeline_confidence'] == '?'
    assert first['model_tier'] == '?'
    assert first['avg_logprob'] is None
    assert first['no_speech_prob'] is None
    assert first['compression_ratio'] is None
    assert first['flag_reasons'] == []
    assert 'context_before' not in first


def test_write_ai_review_json_without_context_cues(tmp_path):
    ew._write_ai_review_json(str(tmp_path), 'EP003',
                             [{'start': '00:00:01.000'}])

    data = json.loads((tmp_path / 'temp' / 'scans' /
                       'EP003_ai_review.json').read_text(encoding='utf-8'))
    assert 'context_before' not in data['items'][0]


# ═══════════════════════════════════════════════════════════════
# step_diff — before/after report
# ═══════════════════════════════════════════════════════════════

def test_step_diff_without_scan_data(capsys):
    ew.step_diff('/proj', 'EP001', None, [])

    assert 'No scan data to compare' in capsys.readouterr().out


def test_step_diff_without_srt(monkeypatch, capsys):
    monkeypatch.setattr(ew, 'find_srt', lambda project_dir, episode: (None, None))

    ew.step_diff('/proj', 'EP001', {'issues': [{'start': '00:00:01.000'}]}, [])

    assert 'SRT not found' in capsys.readouterr().out


def test_step_diff_without_issues(monkeypatch, capsys):
    monkeypatch.setattr(ew, 'find_srt',
                        lambda project_dir, episode: ('EP001.srt', '/srt'))

    ew.step_diff('/proj', 'EP001', {'issues': []}, [])

    assert 'No issues' in capsys.readouterr().out


def test_step_diff_marks_fixed_still_and_deleted(monkeypatch, tmp_path,
                                                 capsys):
    monkeypatch.setattr(
        ew, 'find_srt',
        lambda project_dir, episode: ('EP001.srt',
                                      os.path.join(str(tmp_path), 'EP001.srt')))

    def fake_parse(path, mark_garbled=True):
        return [
            {'start': '00:00:01.000', 'text': 'new one'},
            {'start': '00:00:02.000', 'text': 'unchanged'},
        ]

    monkeypatch.setattr(ew, 'parse_srt', fake_parse)
    scan = {'issues': [
        {'start': '00:00:03.000', 'text': 'gone'},
        {'start': '00:00:01,000', 'original_text': 'old one'},
        {'start': '00:00:02.000', 'original_text': 'unchanged'},
    ]}

    ew.step_diff(str(tmp_path), 'EP001', scan,
                 [{'start': '00:00:01,000'}])

    out = capsys.readouterr().out
    assert '[FIXED]  00:00:01,000' in out
    assert 'was: old one' in out
    assert 'now: new one' in out
    assert '[STILL]  00:00:02.000 | unchanged' in out
    assert '[DELETED] 00:00:03.000 | gone' in out
    assert 'Fixed: 1' in out
    assert 'Still broken: 1' in out
    assert 'Deleted: 1' in out
    assert 'Total: 3' in out


# ═══════════════════════════════════════════════════════════════
# _print_ai_review_prompt — Claude prompt
# ═══════════════════════════════════════════════════════════════

@pytest.mark.parametrize('data', [None, {}, {'items': []}])
def test_print_ai_review_prompt_without_items(monkeypatch, tmp_path, data,
                                              capsys):
    monkeypatch.setattr(ew, 'load_json', lambda path: data)

    ew._print_ai_review_prompt(str(tmp_path), 'EP001', [])

    assert 'No items flagged for AI review' in capsys.readouterr().out


def test_print_ai_review_prompt_renders_flagged_items(monkeypatch, tmp_path,
                                                      capsys):
    data = {'items': [
        {'start': '00:02:00.490', 'original': 'old', 'replacement': 'new',
         'pipeline_confidence': 0.42, 'model_tier': 'tier2',
         'flag_reasons': ['low_conf', 'noise'],
         'context_before': 'before line', 'context_after': 'after line'},
        {'start': '00:02:05.120', 'original': 'old2', 'replacement': 'new2',
         'pipeline_confidence': 0.9, 'model_tier': 'tier1'},
    ]}
    monkeypatch.setattr(ew, 'load_json', lambda path: data)

    ew._print_ai_review_prompt(str(tmp_path), 'EP001', [])

    out = capsys.readouterr().out
    assert 'AI 置信度审查' in out
    assert 'EP001 (2 items)' in out
    assert '─── [1/2] 00:02:00.490 ───' in out
    assert '原文:      old' in out
    assert 'Whisper:   new' in out
    assert '置信度:    0.42 (tier=tier2)' in out
    assert '标记原因: low_conf, noise' in out
    assert '上文:      before line' in out
    assert '下文:      after line' in out
    assert '─── [2/2] 00:02:05.120 ───' in out
    assert out.count('标记原因:') == 1      # second item has no flags
    assert '数据文件:' in out


# ═══════════════════════════════════════════════════════════════
# _run_pipeline — remaining step branches
# ═══════════════════════════════════════════════════════════════

def test_video_without_whisper_skips_audio(monkeypatch):
    calls = []
    _patch_steps(monkeypatch, calls)

    ew._run_pipeline('/proj', 'EP001',
                     _resources(has_video=True, has_whisper=False), _args())

    assert _names(calls) == ['apply', 'diff']


def test_reference_subtitles_alone_do_not_select_audio_or_translate(
        monkeypatch):
    calls = []
    _patch_steps(monkeypatch, calls)

    ew._run_pipeline('/proj', 'EP001', _resources(has_reference=True),
                     _args(target_lang='zh'))

    assert _names(calls) == ['apply', 'diff']


def test_reference_with_video_and_whisper_adds_translate(monkeypatch):
    calls = []
    _patch_steps(monkeypatch, calls)

    ew._run_pipeline('/proj', 'EP001',
                     _resources(has_video=True, has_whisper=True,
                                has_reference=True),
                     _args(target_lang='zh'))

    assert _names(calls) == ['audio', 'translate', 'apply', 'diff']


def test_run_pipeline_minimal_args_match_run_all_caller(monkeypatch):
    calls = []
    _patch_steps(monkeypatch, calls)
    # run_all.step_fix_episodes passes this minimal shape (no skip_whisper)
    minimal = SimpleNamespace(step=None, dry_run=True, video_dir=None)

    ew._run_pipeline('/proj', 'EP001',
                     _resources(has_video=True, has_whisper=True), minimal)

    assert _names(calls) == ['audio', 'diff']
    audio = next(c for c in calls if c[0] == 'audio')[1]
    assert audio['target_lang'] == 'ja'
    assert audio['vad_clean_apply'] is False
    assert audio['asr_engine'] == ''


def test_explicit_apply_step_honours_no_backup(monkeypatch):
    calls = []

    def fake_apply(project_dir, episode, fixes_or_report, scan_result,
                   no_backup=False):
        calls.append((fixes_or_report, no_backup))
        return [], []

    monkeypatch.setattr(ew, 'step_apply', fake_apply)
    monkeypatch.setattr(ew, 'load_json', lambda path: None)

    ew._run_pipeline('/proj', 'EP001', _resources(),
                     _args(step='apply', no_backup=True))

    assert calls[0][0].source == 'workflow'
    assert calls[0][1] is True


def test_ai_review_step_prints_prompt(monkeypatch):
    calls = []
    monkeypatch.setattr(
        ew, '_print_ai_review_prompt',
        lambda project_dir, episode, items:
            calls.append((project_dir, episode, items)))
    monkeypatch.setattr(ew, 'load_json', lambda path: None)

    ew._run_pipeline('/proj', 'EP001', _resources(), _args(step='ai-review'))

    assert calls == [('/proj', 'EP001', [])]


# ═══════════════════════════════════════════════════════════════
# main() — argument handling and degraded projects
# ═══════════════════════════════════════════════════════════════

def test_main_normalizes_episode_and_forwards_args(monkeypatch, tmp_path,
                                                   capsys):
    seen = {}

    def fake_detect(project_dir, video_dir=None):
        seen['detect'] = (project_dir, video_dir)
        return _resources(has_target_subs=True, has_video=True,
                          has_whisper=True, video_dir=video_dir)

    monkeypatch.setattr(pu, 'detect_resources', fake_detect)
    calls = []
    monkeypatch.setattr(ew, '_run_pipeline',
                        lambda *a: calls.append(a))
    monkeypatch.setattr(sys, 'argv', [
        'episode_workflow.py', '7',
        '--project-dir', str(tmp_path),
        '--video-dir', str(tmp_path / 'video'),
        '--dry-run', '--no-backup', '--vad-clean-apply',
        '--step', 'diff',
    ])

    ew.main()

    assert seen['detect'] == (str(tmp_path), str(tmp_path / 'video'))
    assert len(calls) == 1
    project_dir, episode, resources, args = calls[0]
    assert (project_dir, episode) == (str(tmp_path), 'EP007')
    assert resources['has_target_subs'] is True
    assert args.step == 'diff'
    assert args.dry_run is True
    assert args.no_backup is True
    assert args.vad_clean_apply is True
    out = capsys.readouterr().out
    assert 'EP007 — Proofread Workflow' in out
    assert 'Resources:' in out


@pytest.mark.parametrize('mode', ['auto', 'text', 'audio'])
def test_main_accepts_mode_flag_for_compat(monkeypatch, mode):
    # Since the resource-driven refactor (cacffe9) --mode is vestigial but
    # must stay accepted so existing invocations keep working.
    monkeypatch.setattr(pu, 'detect_resources', lambda *a, **k: _resources())
    calls = []
    monkeypatch.setattr(ew, '_run_pipeline', lambda *a: calls.append(a))
    monkeypatch.setattr(sys, 'argv', [
        'episode_workflow.py', 'EP001', '--mode', mode, '--dry-run',
    ])

    ew.main()

    assert len(calls) == 1
    assert calls[0][3].mode == mode


@pytest.mark.parametrize('extra', [['--step', 'bogus'],
                                   ['--mode', 'bogus']])
def test_main_rejects_unknown_choices(monkeypatch, extra, capsys):
    monkeypatch.setattr(sys, 'argv', ['episode_workflow.py', 'EP001'] + extra)

    with pytest.raises(SystemExit) as excinfo:
        ew.main()

    assert excinfo.value.code == 2
    assert 'invalid choice' in capsys.readouterr().err


def test_main_missing_project_degrades_gracefully(monkeypatch, tmp_path,
                                                  capsys):
    missing = tmp_path / 'no-such-project'
    seen = {}

    def fake_detect(project_dir, video_dir=None):
        seen['detect'] = project_dir
        return _resources()

    monkeypatch.setattr(pu, 'detect_resources', fake_detect)
    monkeypatch.setattr(sys, 'argv', [
        'episode_workflow.py', 'EP001', '--project-dir', str(missing),
    ])

    ew.main()

    assert seen['detect'] == str(missing)
    out = capsys.readouterr().out
    assert 'Proofread Workflow' in out
    assert '[apply] No fixes to report.' in out
    assert '[diff] No scan data to compare.' in out
    assert not missing.exists()


def test_main_diff_step_reports_missing_srt(monkeypatch, tmp_path, capsys):
    scans = tmp_path / 'temp' / 'scans'
    scans.mkdir(parents=True)
    (scans / 'findings.json').write_text(json.dumps({
        'per_episode_issues': {
            'EP001': [{'type': 'garbled', 'start': '00:00:01.000'}],
        },
    }), encoding='utf-8')
    monkeypatch.setattr(pu, 'detect_resources',
                        lambda *a, **k: _resources(has_target_subs=False))
    monkeypatch.setattr(ew, 'find_srt',
                        lambda project_dir, episode: (None, None))
    monkeypatch.setattr(sys, 'argv', [
        'episode_workflow.py', 'EP001',
        '--project-dir', str(tmp_path), '--step', 'diff',
    ])

    ew.main()

    assert '[diff] SRT not found.' in capsys.readouterr().out
