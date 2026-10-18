"""Tests for run_all.py pure helpers (episode parsing, dedup, filtering)."""

import run_all


def test_parse_episodes_ranges_and_lists():
    assert run_all._parse_episodes('EP001-EP003') == [
        'EP001', 'EP002', 'EP003']
    assert run_all._parse_episodes('1,3') == ['EP001', 'EP003']
    assert run_all._parse_episodes('5') == ['EP005']
    assert run_all._parse_episodes('EP002,EP001') == ['EP001', 'EP002']


def test_parse_episodes_none_uses_findings():
    assert run_all._parse_episodes(None) == []
    findings = {'per_episode_issues': {'EP005': [], 'EP001': []}}
    assert run_all._parse_episodes(None, findings) == ['EP001', 'EP005']


def test_filter_by_start():
    episodes = ['EP001', 'EP002', 'EP010']
    assert run_all._filter_by_start(episodes, 'EP002') == ['EP002', 'EP010']
    assert run_all._filter_by_start(episodes, '10') == ['EP010']
    assert run_all._filter_by_start(episodes, None) == episodes


def test_dedup_fixes_keeps_first_occurrence():
    fixes = [
        {'action': 'replace_text', 'file': 'a.srt', 'original': 'x',
         'replacement': '1'},
        {'action': 'replace_text', 'file': 'a.srt', 'original': 'x',
         'replacement': '2'},
        {'action': 'replace_text', 'file': 'a.srt', 'original': 'y',
         'replacement': '3'},
    ]
    result = run_all._dedup_fixes(fixes)
    assert len(result) == 2
    assert result[0]['replacement'] == '1'
    assert result[1]['replacement'] == '3'


def test_dedup_fixes_global_uses_original_or_pattern():
    fixes = [
        {'action': 'replace_global', 'original': 'A', 'replacement': '1'},
        {'action': 'replace_global', 'original': 'A', 'replacement': '2'},
        {'action': 'replace_global_regex', 'pattern': 'B',
         'replacement': '3'},
    ]
    result = run_all._dedup_fixes(fixes)
    assert len(result) == 2
    assert result[0]['replacement'] == '1'
    assert result[1]['action'] == 'replace_global_regex'
