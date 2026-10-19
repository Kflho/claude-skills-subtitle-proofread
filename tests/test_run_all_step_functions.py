"""Tests for run_all.py step functions — side effects, files, subprocess calls.

These cover the Phase-1/2/3 steps themselves (scan, nouns, apply, deliver,
clean, AI-review helpers), not just the order in which main() calls them.
Every external command goes through a mocked ``run_all._run``.
"""

import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import fix.fix_orchestrator as fx
import run_all
from utils.update_report import read_report


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')


def _names(calls):
    return [name for name, _, _ in calls]


# ═══════════════════════════════════════════════════════════════
# _run
# ═══════════════════════════════════════════════════════════════

class _Proc:
    def __init__(self, returncode=0):
        self.returncode = returncode


def test_run_returns_true_on_zero_exit(monkeypatch):
    monkeypatch.setattr(
        run_all.subprocess, 'run', lambda *a, **k: _Proc(0))
    assert run_all._run(['python', 'x.py'], '.', desc='demo') is True


def test_run_returns_false_on_nonzero_exit(monkeypatch, capsys):
    monkeypatch.setattr(
        run_all.subprocess, 'run', lambda *a, **k: _Proc(2))
    assert run_all._run(['python', 'x.py'], '.') is False
    assert 'FAILED (code 2)' in capsys.readouterr().err


def test_run_handles_timeout_and_unexpected_error(monkeypatch, capsys):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd='x', timeout=1)

    monkeypatch.setattr(run_all.subprocess, 'run', timeout)
    assert run_all._run(['x'], '.') is False

    def boom(*args, **kwargs):
        raise OSError('nope')

    monkeypatch.setattr(run_all.subprocess, 'run', boom)
    assert run_all._run(['x'], '.') is False

    err = capsys.readouterr().err
    assert 'TIMEOUT' in err
    assert 'ERROR: nope' in err


# ═══════════════════════════════════════════════════════════════
# Glossary notice
# ═══════════════════════════════════════════════════════════════

def test_print_glossary_notice_counts_entries(tmp_path, capsys):
    glossary = tmp_path / 'proper-nouns.md'
    glossary.write_text(
        '| 名前 | 回数 |\n| --- | --- |\n| サリー | 10 |\n| ポチ | 2 |\n',
        encoding='utf-8')
    run_all._print_glossary_ai_review_notice(str(tmp_path), str(glossary), 'ja')

    err = capsys.readouterr().err
    assert 'AI Glossary Review' in err
    assert 'entries in' in err
    assert 'japanese_utils.py' in err


def test_print_glossary_notice_survives_unreadable_file(tmp_path, capsys):
    missing = tmp_path / 'missing.md'
    run_all._print_glossary_ai_review_notice(str(tmp_path), str(missing), 'zh')

    err = capsys.readouterr().err
    assert '? entries' in err
    assert 'chinese_utils.py' in err


# ═══════════════════════════════════════════════════════════════
# step_scan
# ═══════════════════════════════════════════════════════════════

def test_step_scan_skips_fresh_findings(monkeypatch, tmp_path, capsys):
    scans = tmp_path / 'temp' / 'scans'
    scans.mkdir(parents=True)
    (scans / 'findings.json').write_text('{}', encoding='utf-8')
    calls = []
    monkeypatch.setattr(
        run_all, '_run', lambda *a, **k: calls.append(a) or True)

    assert run_all.step_scan(str(tmp_path), 'ja') is True
    assert calls == []
    assert 'findings.json is fresh' in capsys.readouterr().err


def test_step_scan_builds_full_command(monkeypatch, tmp_path, capsys):
    target = tmp_path / 'subs'
    target.mkdir()
    scans = tmp_path / 'temp' / 'scans'
    scans.mkdir(parents=True)
    findings = scans / 'findings.json'
    findings.write_text('{}', encoding='utf-8')
    os.utime(findings, (0, 0))  # older than the SRT below
    (target / 'EP001.srt').write_text(
        '1\n00:00:01,000 --> 00:00:02,000\nこんにちは\n', encoding='utf-8')
    (scans / 'ai_nouns.json').write_text('[]', encoding='utf-8')
    videos = tmp_path / 'videos'
    videos.mkdir()
    reports = tmp_path / 'reports'
    reports.mkdir()
    (reports / 'proper-nouns.md').write_text('| a | 1 |\n', encoding='utf-8')

    captured = {}

    def fake_run(cmd, cwd, **kwargs):
        captured['cmd'] = cmd
        captured['kwargs'] = kwargs
        return True

    monkeypatch.setattr(run_all, '_run', fake_run)

    assert run_all.step_scan(
        str(tmp_path), 'ja', target_dir=str(target), video_dir=str(videos),
        skip_vad=True, episodes=['EP001', 'EP002']) is True

    cmd = captured['cmd']
    assert cmd[1].endswith(os.path.join('scan', 'unified_scanner.py'))
    assert '--target-dir' in cmd and str(target) in cmd
    assert '--build-glossary' in cmd
    assert '--project-lang' in cmd and 'ja' in cmd
    assert '--ai-nouns' in cmd
    assert '--video-dir' in cmd and str(videos) in cmd
    assert '--vad-cache-dir' in cmd
    assert '--skip-vad' in cmd
    assert '--episodes' in cmd and 'EP001,EP002' in cmd
    assert captured['kwargs']['desc'] == 'scan'
    assert captured['kwargs']['timeout'] == 3600
    assert 'AI Glossary Review' in capsys.readouterr().err


def test_step_scan_force_rescan_ignores_fresh_findings(monkeypatch, tmp_path):
    scans = tmp_path / 'temp' / 'scans'
    scans.mkdir(parents=True)
    (scans / 'findings.json').write_text('{}', encoding='utf-8')
    calls = []
    monkeypatch.setattr(
        run_all, '_run', lambda *a, **k: calls.append(a) or True)

    assert run_all.step_scan(str(tmp_path), 'ja', force_rescan=True) is True
    assert len(calls) == 1


def test_step_scan_returns_false_when_scanner_fails(monkeypatch, tmp_path,
                                                    capsys):
    monkeypatch.setattr(run_all, '_run', lambda *a, **k: False)
    reports = tmp_path / 'reports'
    reports.mkdir()
    (reports / 'proper-nouns.md').write_text('x', encoding='utf-8')

    assert run_all.step_scan(str(tmp_path), 'ja') is False
    assert 'AI Glossary Review' not in capsys.readouterr().err


# ═══════════════════════════════════════════════════════════════
# step_nouns / _step_noun_classify / _apply_classified_results
# ═══════════════════════════════════════════════════════════════

def test_step_nouns_without_glossary_skips_table_check(monkeypatch, tmp_path,
                                                       capsys):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args)
        return True

    monkeypatch.setattr(run_all, '_run', fake_run)

    results = run_all.step_nouns(str(tmp_path), 'ja',
                                 target_dir=str(tmp_path / 'subs'))

    assert results == {}
    assert len(calls) == 2
    assert calls[0][0][1].endswith(os.path.join('fix', 'oped_fixer.py'))
    assert calls[1][0][1].endswith(
        os.path.join('nouns', 'find_suspect_nouns.py'))
    assert 'No proper-nouns.md' in capsys.readouterr().err


def test_step_nouns_with_glossary_and_translation_mode(monkeypatch, tmp_path):
    reports = tmp_path / 'reports'
    reports.mkdir()
    (reports / 'proper-nouns.md').write_text('| サリー | 3 |\n',
                                             encoding='utf-8')
    _write_json(tmp_path / 'temp' / 'noun_mappings.json', {})
    (tmp_path / '日文ai修复版').mkdir()

    monkeypatch.setattr(
        run_all, '_step_noun_classify',
        lambda *a, **k: {'total_unknown': 2})

    calls = []

    def fake_run(cmd, cwd, **kwargs):
        calls.append(cmd)
        return True

    monkeypatch.setattr(run_all, '_run', fake_run)

    results = run_all.step_nouns(str(tmp_path), 'zh',
                                 target_dir=str(tmp_path / 'subs'))

    assert results == {'total_unknown': 2}
    assert len(calls) == 2
    suspect_cmd = calls[1]
    assert '--glossary' in suspect_cmd
    assert '--mappings' in suspect_cmd
    assert 'translation' in suspect_cmd
    assert '--source-dir' in suspect_cmd


def test_step_noun_classify_aggregates_unknowns(monkeypatch, tmp_path):
    target = tmp_path / 'subs'
    target.mkdir()
    glossary = tmp_path / 'glossary.md'
    glossary.write_text('| サリー | 3 |\n', encoding='utf-8')

    def fake_run(cmd, cwd, **kwargs):
        out = cmd[cmd.index('-o') + 1]
        out_path = Path(out)
        (out_path / 'EP001_nouns.json').write_text(json.dumps({
            'results': [
                {'candidate': 'サリー', 'status': 'unknown',
                 'context': 'サリーだ'},
                {'candidate': 'ポチ', 'status': 'mismatch'},
                {'candidate': '既知', 'status': 'matched'},
            ],
        }, ensure_ascii=False), encoding='utf-8')
        (out_path / 'EP002_nouns.json').write_text(json.dumps({
            'results': [
                {'candidate': 'サリー', 'status': 'unknown',
                 'context': 'サリー！'},
                {'candidate': '既知', 'status': 'matched'},
            ],
        }, ensure_ascii=False), encoding='utf-8')
        return True

    monkeypatch.setattr(run_all, '_run', fake_run)
    captured = {}

    def fake_apply(project_dir, candidates, unknowns, cands, lang):
        captured.update(candidates=candidates, unknowns=unknowns,
                        cands=dict(cands), lang=lang)
        return {'total_unknown': len(unknowns)}

    monkeypatch.setattr(run_all, '_apply_classified_results', fake_apply)

    results = run_all._step_noun_classify(
        str(tmp_path), 'ja', 'checker.py', str(target), str(glossary))

    assert results == {'total_unknown': 3}
    agg = json.loads(
        (tmp_path / 'temp' / 'scans' / 'noun_check.json').read_text(
            encoding='utf-8'))
    assert agg['total_unknown'] == 3
    assert {r['episode'] for r in agg['results']} == {'EP001', 'EP002'}
    assert captured['cands'] == {'サリー': 2, 'ポチ': 1}
    assert captured['candidates'] == [
        {'candidate': 'サリー', 'count': 2, 'context': 'サリーだ'},
        {'candidate': 'ポチ', 'count': 1, 'context': ''},
    ]


def test_step_noun_classify_without_unknowns_skips_ai(monkeypatch, tmp_path,
                                                      capsys):
    target = tmp_path / 'subs'
    target.mkdir()
    glossary = tmp_path / 'glossary.md'
    glossary.write_text('| サリー | 3 |\n', encoding='utf-8')

    def fake_run(cmd, cwd, **kwargs):
        out = cmd[cmd.index('-o') + 1]
        out_path = Path(out)
        (out_path / 'EP001_nouns.json').write_text(json.dumps({
            'results': [{'candidate': 'サリー', 'status': 'matched'}],
        }, ensure_ascii=False), encoding='utf-8')
        (out_path / 'EP002_nouns.json').write_text('{bad json',
                                                   encoding='utf-8')
        return True

    monkeypatch.setattr(run_all, '_run', fake_run)
    monkeypatch.setattr(
        run_all, '_apply_classified_results',
        lambda *a, **k: (_ for _ in ()).throw(AssertionError('must not run')))

    results = run_all._step_noun_classify(
        str(tmp_path), 'ja', 'checker.py', str(target), str(glossary))

    assert results == {'total_unknown': 0}
    agg = json.loads(
        (tmp_path / 'temp' / 'scans' / 'noun_check.json').read_text(
            encoding='utf-8'))
    assert agg['total_unknown'] == 0
    assert agg['results'] == []
    assert 'skipping unreadable JSON' in capsys.readouterr().err


def test_apply_classified_results_writes_candidates_and_report(tmp_path):
    cands = Counter({'サリー': 3, 'ポチ': 1})
    unknowns = [{'candidate': 'サリー'}, {'candidate': 'ポチ'}]

    results = run_all._apply_classified_results(
        str(tmp_path), [], unknowns, cands, 'ja')

    assert results['total_unknown'] == 2
    assert results['ai_review_count'] == 2
    ai_path = tmp_path / 'temp' / 'scans' / 'ai_review_candidates.json'
    assert json.loads(ai_path.read_text(encoding='utf-8')) == [
        {'candidate': 'サリー', 'count': 3},
        {'candidate': 'ポチ', 'count': 1},
    ]
    report = read_report(str(tmp_path / 'reports' / '问题解决报告.md'))
    assert [e['original'] for e in report['3.5']] == ['サリー', 'ポチ']
    assert all(e['status'] == '⬜' for e in report['3.5'])


def test_apply_classified_results_survives_report_failure(monkeypatch,
                                                          tmp_path, capsys):
    monkeypatch.setattr(
        run_all, '_replace_layer',
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('disk full')))

    results = run_all._apply_classified_results(
        str(tmp_path), [], [{'candidate': 'サリー'}],
        Counter({'サリー': 1}), 'ja')

    assert results['ai_review_count'] == 1
    assert 'L3.5 report write failed' in capsys.readouterr().err


# ═══════════════════════════════════════════════════════════════
# step_apply_all
# ═══════════════════════════════════════════════════════════════

def test_step_apply_all_without_fixes_is_noop(monkeypatch, tmp_path, capsys):
    calls = []
    monkeypatch.setattr(
        run_all, '_run', lambda *a, **k: calls.append(a) or True)

    assert run_all.step_apply_all(str(tmp_path), 'zh') is True
    assert calls == []
    assert 'No fixes to apply' in capsys.readouterr().err


def test_step_apply_all_collects_dedups_and_dry_runs(monkeypatch, tmp_path):
    scans = tmp_path / 'temp' / 'scans'
    _write_json(scans / 'oped_fixes.json', {'fixes': [
        {'action': 'replace_global', 'original': 'A', 'replacement': '1'}]})
    _write_json(scans / 'noun_check.json', {'fixes': [
        {'action': 'replace_global', 'original': 'A', 'replacement': '2'},
        {'action': 'replace_text', 'file': 'a.srt', 'original': 'x',
         'replacement': 'y'}]})
    _write_json(scans / 'ai_review_fixes.json', [
        {'action': 'replace_text', 'file': 'b.srt', 'original': 'q',
         'replacement': 'r'}])

    captured = {}

    def fake_run(cmd, cwd, **kwargs):
        captured['cmd'] = cmd
        return True

    monkeypatch.setattr(run_all, '_run', fake_run)
    target = tmp_path / 'subs'

    assert run_all.step_apply_all(
        str(tmp_path), 'zh', target_dir=str(target), dry_run=True) is True

    fixes = json.loads((scans / 'all_fixes.json').read_text(encoding='utf-8'))
    assert len(fixes) == 3
    assert fixes[0]['replacement'] == '1'  # source priority preserved

    cmd = captured['cmd']
    assert cmd[1].endswith(os.path.join('apply', 'apply_fixes.py'))
    assert '--target-dir' in cmd and str(target) in cmd
    assert '--log-to-report' in cmd
    assert str(tmp_path / 'reports' / '问题解决报告.md') in cmd
    assert '--step' in cmd and '4' in cmd
    assert '--dry-run' in cmd


def test_step_apply_all_without_dry_run_does_not_pass_flag(monkeypatch,
                                                           tmp_path):
    _write_json(tmp_path / 'temp' / 'scans' / 'oped_fixes.json', {'fixes': [
        {'action': 'replace_global', 'original': 'A', 'replacement': 'B'}]})
    captured = {}
    monkeypatch.setattr(
        run_all, '_run',
        lambda cmd, cwd, **k: captured.update(cmd=cmd) or True)

    assert run_all.step_apply_all(str(tmp_path), 'ja') is True
    assert '--dry-run' not in captured['cmd']


# ═══════════════════════════════════════════════════════════════
# step_ass_repair / step_polish / step_clean
# ═══════════════════════════════════════════════════════════════

def test_step_ass_repair_skips_srt_projects(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(run_all, 'detect_format',
                        lambda *a, **k: {'primary': 'srt'})
    calls = []
    monkeypatch.setattr(
        run_all, '_run', lambda *a, **k: calls.append(a) or True)

    assert run_all.step_ass_repair(str(tmp_path)) is True
    assert calls == []
    assert 'SRT project — skipping' in capsys.readouterr().err


def test_step_ass_repair_runs_checks(monkeypatch, tmp_path):
    monkeypatch.setattr(run_all, 'detect_format',
                        lambda *a, **k: {'primary': 'ass'})
    captured = {}
    monkeypatch.setattr(
        run_all, '_run',
        lambda cmd, cwd, **k: captured.update(cmd=cmd) or True)

    assert run_all.step_ass_repair(
        str(tmp_path), target_dir=str(tmp_path / 'subs')) is True

    cmd = captured['cmd']
    assert cmd[1].endswith(os.path.join('ass', 'ass_repair.py'))
    assert '--target-dir' in cmd and str(tmp_path / 'subs') in cmd
    assert '--check' in cmd and 'all' in cmd


def test_step_polish_passes_glossary(monkeypatch, tmp_path):
    reports = tmp_path / 'reports'
    reports.mkdir()
    (reports / 'proper-nouns.md').write_text('x', encoding='utf-8')
    captured = {}
    monkeypatch.setattr(
        run_all, '_run',
        lambda cmd, cwd, **k: captured.update(cmd=cmd, kwargs=k) or True)

    assert run_all.step_polish(
        str(tmp_path), target_dir=str(tmp_path / 'subs')) is True

    cmd = captured['cmd']
    assert cmd[1].endswith('polish_zh.py')
    assert '--glossary' in cmd
    assert '--output-dir' in cmd and str(tmp_path / '中文润色后') in cmd
    assert captured['kwargs']['desc'] == 'polish'


def test_step_clean_invokes_cleaner(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(
        run_all, '_run',
        lambda cmd, cwd, **k: captured.update(cmd=cmd) or True)

    assert run_all.step_clean(
        str(tmp_path), target_dir=str(tmp_path / 'subs')) is True
    cmd = captured['cmd']
    assert cmd[1].endswith(os.path.join('utils', 'clean_empty_cues.py'))
    assert '--target-dir' in cmd


# ═══════════════════════════════════════════════════════════════
# _wire_suspect_nouns_to_report
# ═══════════════════════════════════════════════════════════════

def test_wire_suspect_nouns_new_candidates_format(tmp_path):
    scans = tmp_path / 'temp' / 'scans'
    scans.mkdir(parents=True)
    _write_json(scans / 'suspect_nouns.json', {'candidates': [
        {'type': 'inconsistency',
         'zh_canonical_in_mappings': '莎莉',
         'zh_appearances': [{'text': '萨莉'}, {'text': '莎莉'}],
         'ja_source': 'サリー',
         'sample_contexts': [{'zh_text': '萨莉来了',
                              'timestamp': '00:01:02',
                              'file': 'EP001.srt'}]},
        {'type': 'unknown_suspect', 'zh_term': '波奇', 'frequency': 3,
         'sample_contexts': [{'zh_text': '波奇在叫',
                              'timestamp': '00:02:03',
                              'file': 'EP002.srt'}]},
    ]})
    report = tmp_path / 'reports' / '问题解决报告.md'

    run_all._wire_suspect_nouns_to_report(
        str(scans / 'suspect_nouns.json'), str(report))

    entries = read_report(str(report))['3']
    assert len(entries) == 2
    assert entries[0]['original'] == '萨莉'
    assert entries[0]['corrected'] == '→ 莎莉 (ja: サリー)'
    assert entries[0]['ep'] == 'EP001.srt'
    assert entries[0]['time'] == '00:01:02'
    assert entries[0]['note'].startswith('inconsistency; ctx: 萨莉来了')
    assert entries[1]['original'] == '波奇'
    assert 'unknown_suspect (×3)' in entries[1]['note']


def test_wire_suspect_nouns_legacy_groups_and_singletons(tmp_path):
    scans = tmp_path / 'temp' / 'scans'
    scans.mkdir(parents=True)
    _write_json(scans / 'suspect_nouns.json', {
        'groups': [{
            'variants': [
                {'text': '阿童木', 'ep': 'EP001', 'cue_start': '00:00:05',
                 'contexts': [{'cue_text': '阿童木来了'}]},
                '阿托姆',
            ],
            'suspected_canonical': '阿童木',
            'source_ja': 'アトム',
            'reason': 'variant',
        }],
        'singletons': [{
            'text': '飞雄', 'eps': ['EP002'], 'count': 2, 'reason': 'unknown',
            'contexts': [{'cue_text': '飞雄啊', 'cue_start': '00:00:07'}],
        }],
    })
    report = tmp_path / 'reports' / '问题解决报告.md'

    run_all._wire_suspect_nouns_to_report(
        str(scans / 'suspect_nouns.json'), str(report))

    entries = read_report(str(report))['3']
    assert len(entries) == 3
    assert entries[0]['original'] == '阿童木'
    assert entries[0]['corrected'] == '→ 阿童木 (ja: アトム)'
    assert entries[1]['original'] == '阿托姆'
    assert entries[1]['ep'] == ''
    assert entries[2]['original'] == '飞雄'
    assert entries[2]['ep'] == 'EP002'
    assert entries[2]['time'] == '00:00:07'


def test_wire_suspect_nouns_ignores_invalid_json(tmp_path):
    scans = tmp_path / 'temp' / 'scans'
    scans.mkdir(parents=True)
    (scans / 'suspect_nouns.json').write_text('{bad', encoding='utf-8')
    report = tmp_path / 'reports' / '问题解决报告.md'

    run_all._wire_suspect_nouns_to_report(
        str(scans / 'suspect_nouns.json'), str(report))

    assert read_report(str(report)) == {}


# ═══════════════════════════════════════════════════════════════
# _append_to_glossary
# ═══════════════════════════════════════════════════════════════

GLOSSARY = (
    '# 专有名词\n\n'
    '| 名前 | 回数 | 変異 |\n'
    '|---|---|---|\n'
    '| サリー | 10 | — |\n\n'
    '## 使用方法\n\n'
    '1. 正常运行。\n'
)


def test_append_to_glossary_validates_and_skips_existing(tmp_path):
    reports = tmp_path / 'reports'
    reports.mkdir()
    glossary = reports / 'proper-nouns.md'
    glossary.write_text(GLOSSARY, encoding='utf-8')

    run_all._append_to_glossary(str(tmp_path), [
        {'candidate': 'ポチ', 'count': 3},
        {'candidate': 'サリー', 'count': 9},    # already in glossary
        {'candidate': 'ア', 'count': 1},         # too short
        {'candidate': 'パパ', 'count': 1},       # common katakana
        {'candidate': 'アアアア', 'count': 1},   # sound effect
        {'candidate': 'ポチ', 'count': 4},       # duplicate in batch
    ], 'ja')

    text = glossary.read_text(encoding='utf-8')
    assert text.count('| ポチ |') == 1
    assert '| ポチ | 3 | — |' in text
    assert text.index('| ポチ | 3 | — |') < text.index('## 使用方法')
    assert text.count('| サリー |') == 1
    assert '| ア |' not in text


def test_append_to_glossary_noop_paths(tmp_path, capsys):
    reports = tmp_path / 'reports'
    reports.mkdir()
    glossary = reports / 'proper-nouns.md'
    glossary.write_text(GLOSSARY, encoding='utf-8')
    before = glossary.read_text(encoding='utf-8')

    run_all._append_to_glossary(str(tmp_path), [], 'ja')
    assert glossary.read_text(encoding='utf-8') == before

    run_all._append_to_glossary(
        str(tmp_path), [{'candidate': 'ア', 'count': 1}], 'ja')
    assert glossary.read_text(encoding='utf-8') == before
    assert 'nothing appended' in capsys.readouterr().err


def test_append_to_glossary_without_usage_section_appends_at_end(tmp_path):
    reports = tmp_path / 'reports'
    reports.mkdir()
    glossary = reports / 'proper-nouns.md'
    glossary.write_text('# 专名\n| サリー | 1 | — |\n', encoding='utf-8')

    run_all._append_to_glossary(
        str(tmp_path), [{'candidate': 'ポチ', 'count': 2}], 'ja')

    text = glossary.read_text(encoding='utf-8')
    assert text.rstrip().endswith('| ポチ | 2 | — |')


# ═══════════════════════════════════════════════════════════════
# step_ai_review / _apply_ai_checklists / print_ai_review_notice
# ═══════════════════════════════════════════════════════════════

def test_step_ai_review_without_scans_dir(tmp_path, capsys):
    assert run_all.step_ai_review(str(tmp_path), 'ja') is True
    assert 'No temp/scans/' in capsys.readouterr().err


def test_step_ai_review_counts_pending_fragments(tmp_path, capsys):
    scans = tmp_path / 'temp' / 'scans'
    scans.mkdir(parents=True)
    _write_json(scans / 'ai_fragments_EP001.json',
                {'episode': 'EP001', 'fragments': [{}, {}]})
    _write_json(scans / 'ai_fragments_EP002.json',
                {'episode': 'EP002', 'fragments': []})
    (scans / 'ai_fragments_bad.json').write_text('{bad', encoding='utf-8')
    _write_json(scans / 'unrelated.json', {})

    assert run_all.step_ai_review(str(tmp_path), 'ja') is True

    err = capsys.readouterr().err
    assert 'EP001: 2 pending' in err
    assert '2 entries across 1 episodes' in err
    assert '--apply-ai-review' in err
    assert 'skipping unreadable JSON' in err


def test_step_ai_review_without_pending_fragments(tmp_path, capsys):
    scans = tmp_path / 'temp' / 'scans'
    scans.mkdir(parents=True)
    _write_json(scans / 'ai_fragments_EP001.json',
                {'episode': 'EP001', 'fragments': []})

    assert run_all.step_ai_review(str(tmp_path), 'ja') is True
    assert 'No pending AI review entries' in capsys.readouterr().err


def test_step_review_fill_no_packs(tmp_path):
    assert run_all.step_review_fill(str(tmp_path), 'ja') is True


def test_step_review_fill_applies_decisions(tmp_path):
    import json

    scans = tmp_path / 'temp' / 'scans'
    scans.mkdir(parents=True)
    zh_dir = tmp_path / 'translated' / 'zh'
    zh_dir.mkdir(parents=True)
    srt = zh_dir / 'EP001.srt'
    srt.write_text("1\n00:00:05,000 --> 00:00:06,000\nおまえなにしてんだ\n\n",
                   encoding='utf-8')
    # translation packs are the ones allowed to rewrite by default
    _write_json(scans / 'EP001.zh_pack.json', {
        'schema_version': 2, 'kind': 'review_pack', 'episode': 'EP001',
        'srt': str(srt), 'items': [{
            'id': 0, 'cue_index': 0, 'kind': 'translation', 'route': 'text',
            'start': '00:00:05,000', 'end': '00:00:06,000',
            'seconds': {'start': 5.0, 'end': 6.0},
            'text': 'おまえなにしてんだ', 'source_text': 'おまえなにしてんだ',
            'failure': ['japanese_residual'],
            'decision': 'replace_text', 'replacement': '你这混蛋在干什么',
        }],
    })

    assert run_all.step_review_fill(str(tmp_path), 'ja') is True
    assert '你这混蛋在干什么' in srt.read_text(encoding='utf-8')
    applied = json.loads((scans / 'EP001.zh_applied.json')
                         .read_text(encoding='utf-8'))
    assert applied['summary']['applied'] == 1


def test_apply_ai_checklists_without_dir(tmp_path, capsys):
    assert run_all._apply_ai_checklists(str(tmp_path), 'ja') is False
    assert 'No temp/scans/' in capsys.readouterr().err


def test_apply_ai_checklists_without_files(tmp_path, capsys):
    (tmp_path / 'temp' / 'scans').mkdir(parents=True)
    assert run_all._apply_ai_checklists(str(tmp_path), 'ja') is False
    assert 'No ai_fragments_*.json' in capsys.readouterr().err


def test_apply_ai_checklists_applies_each_episode(monkeypatch, tmp_path,
                                                  capsys):
    scans = tmp_path / 'temp' / 'scans'
    scans.mkdir(parents=True)
    for ep in ('EP001', 'EP002'):
        _write_json(scans / f'ai_fragments_{ep}.json', {})
    applied = []

    class FakeFixer:
        def __init__(self, ep, project_dir, target_lang=None, srt_dir=None):
            self.ep = ep

        def apply_ai_fragments(self, path):
            applied.append((self.ep, path))
            return 2

    monkeypatch.setattr(fx, 'Fixer', FakeFixer)

    assert run_all._apply_ai_checklists(str(tmp_path), 'ja') is True
    assert [ep for ep, _ in applied] == ['EP001', 'EP002']
    assert 'Total: 4 corrections' in capsys.readouterr().err


def test_apply_ai_checklists_zero_applied_returns_false(monkeypatch, tmp_path):
    scans = tmp_path / 'temp' / 'scans'
    scans.mkdir(parents=True)
    _write_json(scans / 'ai_fragments_EP001.json', {})

    class ZeroFixer:
        def __init__(self, *args, **kwargs):
            pass

        def apply_ai_fragments(self, path):
            return 0

    monkeypatch.setattr(fx, 'Fixer', ZeroFixer)

    assert run_all._apply_ai_checklists(str(tmp_path), 'ja') is False


def test_print_ai_review_notice_quiet_branches(capsys):
    run_all.print_ai_review_notice({'ai_review_count': 0, 'total_unknown': 4},
                                   '.', 'ja')
    assert 'Nothing needs AI review' in capsys.readouterr().err

    run_all.print_ai_review_notice({'ai_review_count': 0, 'total_unknown': 0},
                                   '.', 'ja')
    assert 'All proper nouns matched' in capsys.readouterr().err


def test_print_ai_review_notice_lists_candidates(capsys):
    run_all.print_ai_review_notice({
        'ai_review_count': 1, 'total_unknown': 3,
        'ai_candidates': [{'candidate': 'サリー', 'count': 3}],
        'ai_review_file': 'temp/scans/ai_review_candidates.json',
    }, '.', 'ja')

    err = capsys.readouterr().err
    assert 'AI REVIEW NEEDED: 1' in err
    assert 'サリー (3x)' in err
    assert 'japanese_utils.py' in err


# ═══════════════════════════════════════════════════════════════
# _print_progress
# ═══════════════════════════════════════════════════════════════

def test_print_progress_summarizes_findings(tmp_path, capsys):
    scans = tmp_path / 'temp' / 'scans'
    scans.mkdir(parents=True)
    _write_json(scans / 'findings.json', {
        'summary': {'garbled_count': 3, 'vad_episodes_scanned': 4,
                    'repeat_count': 2},
        'per_episode_issues': {'EP001': [1], 'EP002': []},
    })
    (scans / 'EP003_pending_ai.json').write_text('{}', encoding='utf-8')

    run_all._print_progress(str(tmp_path), 'Status: test')

    err = capsys.readouterr().err
    assert 'Status: test' in err
    assert 'Episodes with issues: 1/2' in err
    assert 'Garbled cues:' in err
    assert 'VAD speech scan:' in err and '4 episodes' in err
    assert 'Repeat patterns:' in err
    assert 'AI review pending:' in err and '1 episode(s)' in err


def test_print_progress_without_findings(tmp_path, capsys):
    run_all._print_progress(str(tmp_path), 'Status: empty')
    err = capsys.readouterr().err
    assert '(no findings.json)' in err


# ═══════════════════════════════════════════════════════════════
# _write_degraded_report / step_deliver
# ═══════════════════════════════════════════════════════════════

def test_write_degraded_report_classifies_cues(tmp_path, capsys):
    garbled = [
        {'text': 'Привет', 'file': 'EP001.srt', 'line': 3,
         'timecode': '00:00:01,000'},
        {'text': 'こんにちは', 'file': 'EP001.srt', 'line': 5,
         'timecode': '00:00:02,000'},
        {'text': 'hello 世界', 'file': 'EP002.srt', 'line': 7,
         'timecode': '00:00:03,000'},
        {'text': 'hello', 'file': 'EP002.srt', 'line': 9,
         'timecode': '00:00:04,000'},
        {'text': 'a|b', 'file': 'EP002.srt', 'line': 11,
         'timecode': '00:00:05,000'},
    ]

    run_all._write_degraded_report(str(tmp_path), garbled, None, None, None)

    text = (tmp_path / 'reports' / '问题解决报告.md').read_text(
        encoding='utf-8')
    assert '乱码总数: 5' in text
    assert '残血运行' in text
    assert '俄语残留' in text
    assert '日语残留' in text
    assert '双语混合' in text
    assert '纯外语' in text
    assert 'a\\|b' in text
    assert 'Degraded report' in capsys.readouterr().err


def _write_findings(project_dir, garbled):
    scans = project_dir / 'temp' / 'scans'
    scans.mkdir(parents=True, exist_ok=True)
    _write_json(scans / 'findings.json', {'garbled_cues': garbled})


def test_step_deliver_degrades_without_whisper(monkeypatch, tmp_path):
    monkeypatch.setattr(run_all, 'WHISPER_CLI', '')
    monkeypatch.setattr(run_all, 'WHISPER_MODEL', '')
    subs = tmp_path / 'subs'
    subs.mkdir()
    (subs / 'EP001.srt').write_text(
        '1\n00:00:01,000 --> 00:00:02,000\nПривет\n', encoding='utf-8')
    _write_findings(tmp_path, [
        {'text': 'Привет', 'file': 'EP001.srt', 'line': 3,
         'timecode': '00:00:01,000'},
        {'text': 'other', 'file': 'EP002.srt', 'line': 3,
         'timecode': '00:00:01,000'},
    ])

    assert run_all.step_deliver(
        str(tmp_path), 'zh', target_dir=str(subs)) is True

    text = (tmp_path / 'reports' / '问题解决报告.md').read_text(
        encoding='utf-8')
    assert '乱码总数: 1' in text
    assert 'Привет' in text


def test_step_deliver_counts_question_markers(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(run_all, 'WHISPER_CLI', '')
    monkeypatch.setattr(run_all, 'WHISPER_MODEL', '')
    subs = tmp_path / 'subs'
    subs.mkdir()
    (subs / 'EP001.srt').write_text(
        '1\n00:00:01,000 --> 00:00:02,000\n[???] 何？\n\n'
        '2\n00:00:03,000 --> 00:00:04,000\n[???]\n', encoding='utf-8')

    assert run_all.step_deliver(
        str(tmp_path), 'zh', target_dir=str(subs)) is True

    err = capsys.readouterr().err
    assert 'EP001.srt: 2 [???] markers' in err
    assert '2 [???] markers total' in err


def test_step_deliver_reports_remaining_garbled(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(run_all, 'WHISPER_CLI', 'whisper-cli')
    monkeypatch.setattr(run_all, 'WHISPER_MODEL', '')
    subs = tmp_path / 'subs'
    subs.mkdir()
    (subs / 'EP001.srt').write_text(
        '1\n00:00:01,000 --> 00:00:02,000\nhello\n', encoding='utf-8')
    _write_findings(tmp_path, [
        {'text': 'hello', 'file': 'EP001.srt', 'line': 3,
         'timecode': '00:00:01,000'}])

    assert run_all.step_deliver(
        str(tmp_path), 'zh', target_dir=str(subs)) is True
    assert '1 garbled cues remain' in capsys.readouterr().err


def test_step_deliver_all_clean(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(run_all, 'WHISPER_CLI', '')
    monkeypatch.setattr(run_all, 'WHISPER_MODEL', '')
    subs = tmp_path / 'subs'
    subs.mkdir()
    (subs / 'EP001.srt').write_text(
        '1\n00:00:01,000 --> 00:00:02,000\nこんにちは\n', encoding='utf-8')

    assert run_all.step_deliver(
        str(tmp_path), 'zh', target_dir=str(subs)) is True
    assert 'All clean' in capsys.readouterr().err


# ═══════════════════════════════════════════════════════════════
# main() — remaining resource/flag branches
# ═══════════════════════════════════════════════════════════════

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


def test_main_input_dir_dot_uses_project_root(monkeypatch, tmp_path):
    calls = []
    _patch_main(monkeypatch, calls)
    monkeypatch.setenv('INPUT_DIR', 'orig')
    monkeypatch.setattr(sys, 'argv', [
        'run_all.py', '--lang', 'ja', '--target-dir', str(tmp_path),
        '--input-dir', '.'])

    run_all.main()

    scan = next(c for c in calls if c[0] == 'scan')
    assert scan[2]['target_dir'] == str(tmp_path)
    assert os.environ['INPUT_DIR'] == '.'


def test_main_custom_input_dir_propagates_env(monkeypatch, tmp_path):
    calls = []
    _patch_main(monkeypatch, calls)
    monkeypatch.setenv('INPUT_DIR', 'orig')
    monkeypatch.setattr(sys, 'argv', [
        'run_all.py', '--lang', 'ja', '--target-dir', str(tmp_path),
        '--input-dir', 'subtitles'])

    run_all.main()

    scan = next(c for c in calls if c[0] == 'scan')
    assert scan[2]['target_dir'] == str(tmp_path / 'subtitles')
    assert os.environ['INPUT_DIR'] == 'subtitles'


def test_main_logs_episode_range(monkeypatch, tmp_path, capsys):
    calls = []
    _patch_main(monkeypatch, calls)
    monkeypatch.setattr(sys, 'argv', [
        'run_all.py', '--lang', 'ja', '--target-dir', str(tmp_path),
        '--episodes', '1-3'])

    run_all.main()

    err = capsys.readouterr().err
    assert 'Episodes: 3 selected (EP001~EP003)' in err
    scan = next(c for c in calls if c[0] == 'scan')
    assert scan[2]['episodes'] == ['EP001', 'EP002', 'EP003']


def test_main_logs_limit(monkeypatch, tmp_path, capsys):
    calls = []
    _patch_main(monkeypatch, calls)
    monkeypatch.setattr(sys, 'argv', [
        'run_all.py', '--lang', 'ja', '--target-dir', str(tmp_path),
        '--limit', '5'])

    run_all.main()

    assert 'Episodes: first 5 with issues' in capsys.readouterr().err


def test_main_no_skip_if_clean_forwards_flag(monkeypatch, tmp_path):
    calls = []
    _patch_main(monkeypatch, calls)
    monkeypatch.setattr(sys, 'argv', [
        'run_all.py', '--lang', 'ja', '--target-dir', str(tmp_path),
        '--no-skip-if-clean'])

    run_all.main()

    fix = next(c for c in calls if c[0] == 'fix')
    assert fix[2]['skip_if_clean'] is False


def test_main_dry_run_repairs_ass_format(monkeypatch, tmp_path):
    calls = []
    _patch_main(monkeypatch, calls)
    monkeypatch.setattr(run_all, 'detect_format',
                        lambda *a, **k: {'primary': 'ass'})
    monkeypatch.setattr(sys, 'argv', [
        'run_all.py', '--lang', 'ja', '--target-dir', str(tmp_path),
        '--dry-run'])

    run_all.main()

    assert _names(calls) == ['scan', 'nouns', 'apply', 'ass']


def test_main_zh_polish_confirmation_yes(monkeypatch, tmp_path):
    calls = []
    _patch_main(monkeypatch, calls)
    monkeypatch.setattr(
        run_all, 'step_polish',
        lambda *a, **k: calls.append(('polish', a, k)))
    monkeypatch.setattr('builtins.input', lambda *a, **k: 'y')
    monkeypatch.setattr(sys, 'argv', [
        'run_all.py', '--lang', 'zh', '--target-dir', str(tmp_path)])

    run_all.main()

    assert 'polish' in _names(calls)
    polish = next(c for c in calls if c[0] == 'polish')
    assert polish[2]['target_dir'] == str(tmp_path / 'AI审查后')


def test_main_flags_ai_review_needed(monkeypatch, tmp_path, capsys):
    calls = []
    _patch_main(monkeypatch, calls, noun_results={'ai_review_count': 2})
    monkeypatch.setattr(sys, 'argv', [
        'run_all.py', '--lang', 'ja', '--target-dir', str(tmp_path)])

    run_all.main()

    assert 'AI review still needed!' in capsys.readouterr().err
