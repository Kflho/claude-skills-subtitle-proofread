"""Tests for the final translation stage (no network)."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from fix.translation_stage import (
    build_command, find_glossary, find_source_srt, step_translate_final,
    translate_episode,
)


def _project(tmp_path):
    (tmp_path / 'temp' / 'nouns').mkdir(parents=True)
    (tmp_path / 'temp' / 'nouns' / 'noun_mappings.json').write_text(
        json.dumps({'アグモン': '亚古兽'}, ensure_ascii=False), encoding='utf-8')
    (tmp_path / 'EP001.srt').write_text('1\n00:00:01,000 --> 00:00:02,000\nあ\n',
                                        encoding='utf-8')
    return tmp_path


def test_find_source_srt_prefers_srt(tmp_path):
    (tmp_path / 'EP001.ass').write_text('x', encoding='utf-8')
    (tmp_path / 'EP001.srt').write_text('x', encoding='utf-8')
    (tmp_path / 'EP002.srt').write_text('x', encoding='utf-8')
    found = find_source_srt(str(tmp_path), 'EP001')
    assert found and found.endswith('EP001.srt')
    assert find_source_srt(str(tmp_path), 'EP009') is None


def test_find_glossary(tmp_path):
    project = _project(tmp_path)
    found = find_glossary(str(project))
    assert found and found.endswith('noun_mappings.json')


def test_build_command_contains_contract(tmp_path):
    cmd = build_command('in.srt', 'out.srt', 'g.json', 'zh',
                        qa_report='qa.json', max_chars=14, max_cps=9)
    joined = ' '.join(cmd)
    assert 'translate_srt.py' in joined
    assert '--input in.srt' in joined
    assert '--mappings g.json' in joined
    assert '--qa-report qa.json' in joined
    assert '--max-chars 14' in joined
    assert '--repair' in joined
    with pytest.raises(ValueError):
        build_command('in.srt', 'out.srt', None, 'en')


def test_translate_episode_success_and_failure(tmp_path):
    project = _project(tmp_path)
    calls = {}

    def runner(cmd, cwd=None):
        calls['cmd'] = cmd
        out = Path(cmd[cmd.index('--output') + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text('1\n00:00:01,000 --> 00:00:02,000\n早上好\n',
                       encoding='utf-8')
        return SimpleNamespace(returncode=0)

    result = translate_episode(str(project), 'EP001', runner=runner)
    assert result['ok']
    assert Path(result['output']).exists()
    assert '--mappings' in calls['cmd']

    def fail(cmd, cwd=None):
        return SimpleNamespace(returncode=1)

    result = translate_episode(str(project), 'EP001', runner=fail)
    assert not result['ok']
    assert 'exited 1' in result['message']


def test_step_translate_final_dry_run(tmp_path):
    project = _project(tmp_path)
    results = step_translate_final(str(project), ['EP001'], dry_run=True)
    assert results[0]['ok'] and results[0]['dry_run']
    assert not (project / 'translated' / 'zh' / 'EP001.srt').exists()


def test_translate_episode_missing_source(tmp_path):
    project = _project(tmp_path)
    result = translate_episode(str(project), 'EP999')
    assert not result['ok']
    assert 'not found' in result['message']
