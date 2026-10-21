"""Global project registry: one machine-local file knows every project.

The registry replaced per-project ``project.json``: identity, style, folder
overrides and download dirs all live in ``projects_registry.json`` beside
``local.json``.  These tests pin the lookup rules every consumer relies on.
"""

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from lib import media_paths, projects_registry  # noqa: E402


@pytest.fixture()
def registry(tmp_path, monkeypatch):
    path = tmp_path / 'projects_registry.json'
    monkeypatch.setenv('SKILL_LOCAL_CONFIG', str(tmp_path / 'local.json'))
    monkeypatch.delenv('SUBTITLE_PROJECT', raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(projects_registry, 'registry_path', lambda: path)
    monkeypatch.setattr(projects_registry, '_cache', None)
    projects_registry.save({'schema_version': 1, 'projects': {}}, path=path)
    return path


def _project(tmp_path, name='赤胴铃之助'):
    project = tmp_path / name
    (project / 'data' / 'ja').mkdir(parents=True)
    (project / 'bin' / 'reference_docs' / '参考字幕').mkdir(parents=True)
    return project


def test_missing_registry_is_empty_not_an_error(registry):
    data = projects_registry.load(refresh=True)
    assert data['projects'] == {}


def test_register_and_lookup_by_project_dir(registry, tmp_path):
    project = _project(tmp_path)
    projects_registry.register(project, series='赤胴铃之助', media_type='tv')
    entry = projects_registry.entry(project)
    assert entry['series'] == '赤胴铃之助'
    assert projects_registry.value(project, 'media_type') == 'tv'
    # Registered on disk under the project's folder name.
    saved = json.loads(registry.read_text(encoding='utf-8'))
    assert saved['projects']['赤胴铃之助']['series'] == '赤胴铃之助'


def test_relative_paths_resolve_against_project_root(registry, tmp_path):
    project = _project(tmp_path)
    projects_registry.register(project, subtitles={'input_dir': 'data/ja'})
    resolved = projects_registry.abs_path(project, 'data/ja')
    assert resolved == str(project / 'data' / 'ja')
    assert os.path.isabs(resolved)
    absolute = str(tmp_path / 'elsewhere')
    assert projects_registry.abs_path(project, absolute) == absolute


def test_media_paths_reads_registry(registry, tmp_path):
    project = _project(tmp_path)
    projects_registry.register(
        project, series='赤胴铃之助', media_type='tv',
        video_dir=str(tmp_path / 'videos' / '赤胴铃之助'),
        subtitles={'input_dir': 'data/ja', 'output_dir': 'data/zh',
                   'reference_dir': 'bin/reference_docs/参考字幕'})
    assert media_paths.project_video_dir(str(project)) == str(
        tmp_path / 'videos' / '赤胴铃之助')
    assert media_paths.input_subs_dir(str(project)) == str(project / 'data' / 'ja')
    assert media_paths.output_subs_dir(str(project)) == str(project / 'data' / 'zh')
    assert media_paths.reference_subs_dir(str(project)) == str(
        project / 'bin' / 'reference_docs' / '参考字幕')


def test_unregistered_project_warns_through_empty_entry(registry, tmp_path):
    project = tmp_path / '新项目'
    (project / 'data').mkdir(parents=True)
    assert projects_registry.entry(project) == {}
    # Defaults still resolve, so nothing crashes mid-run.
    assert media_paths.input_subs_dir(str(project)) == str(project / 'data' / 'ja')


def test_set_value_creates_nested_entry(registry, tmp_path):
    project = _project(tmp_path)
    projects_registry.set_value(project, 'bili23', 'download_dir',
                               value=str(tmp_path / 'dl'))
    assert projects_registry.value(project, 'bili23', 'download_dir') == str(
        tmp_path / 'dl')
