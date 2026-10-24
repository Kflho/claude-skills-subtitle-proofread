"""local.json resolution: env > local.json > default, and missing files are safe."""

import json

from lib import local_config


def _write(tmp_path, data):
    path = tmp_path / 'local.json'
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    return path


def test_local_value_reads_nested_keys(tmp_path, monkeypatch):
    path = _write(tmp_path, {'paths': {'models_dir': 'X:/models'},
                             'llm': {'model': 'm1'}})
    monkeypatch.setenv('SKILL_LOCAL_CONFIG', str(path))
    local_config.load_local(refresh=True)
    assert local_config.local_value('paths', 'models_dir') == 'X:/models'
    assert local_config.local_value('llm', 'model') == 'm1'
    assert local_config.local_value('paths', 'missing', default='d') == 'd'


def test_env_overrides_local_json(tmp_path, monkeypatch):
    path = _write(tmp_path, {'paths': {'models_dir': 'X:/models'}})
    monkeypatch.setenv('SKILL_LOCAL_CONFIG', str(path))
    local_config.load_local(refresh=True)
    assert local_config.pick('MODELS_DIR', 'paths', 'models_dir') == 'X:/models'
    monkeypatch.setenv('MODELS_DIR', 'Y:/override')
    assert local_config.pick('MODELS_DIR', 'paths', 'models_dir') == 'Y:/override'


def test_missing_config_is_safe(tmp_path, monkeypatch):
    monkeypatch.setenv('SKILL_LOCAL_CONFIG', str(tmp_path / 'nope.json'))
    local_config.load_local(refresh=True)
    assert local_config.load_local() == {}
    assert local_config.pick('NOPE', 'paths', 'x', default='fallback') == 'fallback'


def test_blank_env_falls_back_to_local(tmp_path, monkeypatch):
    path = _write(tmp_path, {'llm': {'model': 'from-file'}})
    monkeypatch.setenv('SKILL_LOCAL_CONFIG', str(path))
    monkeypatch.setenv('LLM_MODEL', '   ')
    local_config.load_local(refresh=True)
    assert local_config.pick('LLM_MODEL', 'llm', 'model') == 'from-file'
