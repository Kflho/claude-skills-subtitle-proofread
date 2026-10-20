"""GameViewer watchdog: detect a dropped remote link and start it again."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from net import gameviewer_watchdog as wd  # noqa: E402


def test_resolve_dir_prefers_explicit(tmp_path):
    assert wd.resolve_dir(str(tmp_path)) == tmp_path


def test_resolve_dir_uses_env(tmp_path, monkeypatch):
    monkeypatch.setenv('GAMEVIEWER_DIR', str(tmp_path))
    assert wd.resolve_dir('') == tmp_path


def test_healthy_link_takes_no_action(tmp_path, monkeypatch):
    monkeypatch.setattr(wd, 'service_running', lambda: True)
    monkeypatch.setattr(wd, 'process_running', lambda image=wd.SERVER_IMAGE: True)
    result = wd.ensure(tmp_path)
    assert result['ok'] is True
    assert result['actions'] == []


def test_missing_service_and_server_are_started(tmp_path, monkeypatch):
    calls = {'service': 0, 'process': 0, 'started_service': 0, 'started_ui': 0}

    def service():
        calls['service'] += 1
        return calls['started_service'] > 0

    def process(image=wd.SERVER_IMAGE):
        calls['process'] += 1
        return calls['started_ui'] > 0

    def start_service():
        calls['started_service'] += 1
        return True

    def start_ui(directory):
        calls['started_ui'] += 1
        return True

    monkeypatch.setattr(wd, 'service_running', service)
    monkeypatch.setattr(wd, 'process_running', process)
    monkeypatch.setattr(wd, 'start_service', start_service)
    monkeypatch.setattr(wd, 'start_ui', start_ui)
    monkeypatch.setattr(wd.time, 'sleep', lambda seconds: None)

    result = wd.ensure(tmp_path)
    assert result['ok'] is True
    assert result['actions'] == ['start-service', 'start-ui']
    assert calls['started_service'] == 1 and calls['started_ui'] == 1


def test_start_ui_requires_existing_executable(tmp_path, monkeypatch):
    assert wd.start_ui(tmp_path) is False
    (tmp_path / wd.UI_EXE).write_bytes(b'MZ')
    launched = []
    monkeypatch.setattr(wd.subprocess, 'Popen',
                        lambda args, **kwargs: launched.append(args))
    assert wd.start_ui(tmp_path) is True
    assert launched and launched[0][0].endswith(wd.UI_EXE)


def test_status_reports_missing_link(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(wd, 'service_running', lambda: False)
    monkeypatch.setattr(wd, 'process_running', lambda image=wd.SERVER_IMAGE: False)
    assert wd.cmd_status(tmp_path) == 1
    out = capsys.readouterr().out
    assert "'service': False" in out and "'server': False" in out
