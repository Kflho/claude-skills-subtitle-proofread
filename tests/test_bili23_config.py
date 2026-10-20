"""Two-tier config: project.json overrides global local.json for downloads."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from acquire import bili23  # noqa: E402


def _config(tmp_path, download=''):
    path = tmp_path / 'bili23.json'
    path.write_text(json.dumps({'Download': {'download_path': download},
                                'MCP': {}}, ensure_ascii=False),
                    encoding='utf-8')
    return path


def _project(tmp_path, download):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'project.json').write_text(
        json.dumps({'bili23': {'download_dir': str(download)}}),
        encoding='utf-8')
    return project


def test_missing_download_path_asks_user(tmp_path, monkeypatch):
    cfg = _config(tmp_path, '')
    monkeypatch.setattr(bili23, 'skill_download_path', lambda: '')
    with pytest.raises(bili23.MCPError, match='Ask the user'):
        bili23.resolve_download_dir(cfg, project=str(tmp_path / 'empty'))


def test_project_overrides_global(tmp_path, monkeypatch):
    download = tmp_path / 'downloads'
    download.mkdir()
    cfg = _config(tmp_path, str(download))
    project = _project(tmp_path, download)
    monkeypatch.setattr(bili23, 'skill_download_path',
                        lambda: str(tmp_path / 'global-other'))
    assert bili23.resolve_download_dir(
        cfg, project=str(project)) == download


def test_global_fallback_when_no_project(tmp_path, monkeypatch):
    download = tmp_path / 'downloads'
    download.mkdir()
    cfg = _config(tmp_path, str(download))
    monkeypatch.setattr(bili23, 'skill_download_path', lambda: str(download))
    assert bili23.resolve_download_dir(
        cfg, project=str(tmp_path / 'no-project')) == download


def test_mismatch_with_bili23_config_is_rejected(tmp_path, monkeypatch):
    current = tmp_path / 'current'
    other = tmp_path / 'other'
    current.mkdir()
    other.mkdir()
    cfg = _config(tmp_path, str(current))
    project = _project(tmp_path, other)
    monkeypatch.setattr(bili23, 'skill_download_path', lambda: '')
    with pytest.raises(bili23.MCPError, match='configured for'):
        bili23.resolve_download_dir(cfg, project=str(project))


def test_set_project_scope_writes_project_json(tmp_path, monkeypatch):
    download = tmp_path / 'downloads'
    cfg = _config(tmp_path, 'D:/old')
    project = tmp_path / 'project'
    project.mkdir()
    monkeypatch.setattr(bili23, 'bili23_running', lambda: False)
    monkeypatch.setenv('SKILL_LOCAL_CONFIG', str(tmp_path / 'local.json'))

    result = bili23.set_download_path(cfg, str(download), str(project),
                                      'project')
    saved = json.loads((project / 'project.json').read_text(encoding='utf-8'))
    assert saved['bili23']['download_dir'] == str(download.resolve())
    assert result['scope'] == 'project'
    assert json.loads(cfg.read_text(encoding='utf-8'))['Download'][
        'download_path'] == str(download.resolve()).replace('\\', '/')
    assert cfg.with_suffix('.json.bak-download').exists()


def test_set_global_scope_writes_local_json(tmp_path, monkeypatch):
    download = tmp_path / 'downloads'
    cfg = _config(tmp_path, 'D:/old')
    local = tmp_path / 'local.json'
    monkeypatch.setattr(bili23, 'bili23_running', lambda: False)
    monkeypatch.setenv('SKILL_LOCAL_CONFIG', str(local))

    result = bili23.set_download_path(cfg, str(download), scope='global')
    saved = json.loads(local.read_text(encoding='utf-8'))
    assert saved['paths']['bili23_download_dir'] == str(download.resolve())
    assert result['scope'] == 'global'
