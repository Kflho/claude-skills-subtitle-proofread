"""远程协作守卫：远程会话开着时不要拉起迅雷。

判据来自个人运维 skill 的约定（远程协作期间不开启占用型软件），这里只验证
两件事：信号认得准（服务常驻不算、Server/UI 在跑才算），以及拦截方向是安全侧。
"""

import os

import pytest

from acquire.remote_guard import (ENV_FLAG, REMOTE_PROCESSES, block_reason,
                                  env_flag, remote_signals)


@pytest.fixture(autouse=True)
def clear_env(monkeypatch):
    monkeypatch.delenv(ENV_FLAG, raising=False)


def test_no_signal_when_nothing_remote_is_running():
    assert remote_signals(processes=['explorer.exe', 'python.exe', 'Thunder.exe']) == []
    assert block_reason(processes=['explorer.exe']) == ''


def test_server_or_ui_process_counts_as_remote_session():
    signals = remote_signals(processes=['explorer.exe', 'GameViewerServer.exe'])
    assert len(signals) == 1 and 'GameViewerServer' in signals[0]

    signals = remote_signals(processes=['gameviewer.exe'])
    assert len(signals) == 1 and 'GameViewer' in signals[0]


def test_autostart_service_alone_is_not_a_remote_session():
    """GameViewerService 是自启动服务，常驻不代表有人正在远控。"""
    assert remote_signals(processes=['GameViewerService.exe', 'services.exe']) == []
    assert 'GameViewerService.exe' not in REMOTE_PROCESSES


def test_block_reason_names_the_offending_process():
    reason = block_reason(processes=['GameViewerServer.exe'])
    assert '远程协作' in reason
    assert 'GameViewerServer.exe' in reason
    assert '--allow-thunder' in reason


def test_allow_wins_over_the_guard():
    """用户明确同意时可以放行 —— 拦截是安全侧，不是硬锁。"""
    assert block_reason(allow=True, processes=['GameViewerServer.exe']) == ''


def test_explicit_env_flag_counts_even_without_known_processes(monkeypatch):
    monkeypatch.setenv(ENV_FLAG, '1')
    assert env_flag() is True
    signals = remote_signals(processes=[])
    assert len(signals) == 1 and ENV_FLAG in signals[0]
    assert block_reason(processes=[]) != ''


@pytest.mark.parametrize('value,expected', [
    ('1', True), ('true', True), ('YES', True), ('0', False), ('', False),
])
def test_env_flag_parsing(monkeypatch, value, expected):
    monkeypatch.setenv(ENV_FLAG, value)
    assert env_flag() is expected


def test_process_lookup_failure_is_not_a_remote_signal():
    """拿不到进程表时不能反过来判定「在远程协作」，否则正常下载也被拦。"""
    reason = block_reason(processes=[])
    assert reason == ''
