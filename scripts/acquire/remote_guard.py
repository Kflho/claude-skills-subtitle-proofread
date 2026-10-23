#!/usr/bin/env python3
"""远程协作守卫：远程会话开着时，不要拉起迅雷这类占用型软件。

Why this exists
---------------
本机同时承担「远程控制」和「批量下载」两件事。远程会话（GameViewer / 向日葵 /
UU 远程等）对带宽、磁盘 IO 和 CPU 都很敏感，而迅雷一旦开任务就会占满这三样：
远程画面卡、输入延迟、同步队列堆积。个人运维 skill 的约定是
**远程协作期间不开启迅雷等占用型软件**（见 ``kf-personal-service`` 的
``references/remote.md``），这里把它落成可执行的判断，而不是靠记性。

信号只取「现在真的在远控」的那些：

- ``GameViewerServer.exe`` / ``GameViewer.exe`` 在进程表里 —— 远程会话的
  监听端或托盘 UI 正在跑。**只看服务（``GameViewerService``）不算**：它是自启动
  的 Windows 服务，常驻并不代表有人正在远控；
- 环境变量 ``KF_REMOTE_SESSION=1`` —— 人工或上层脚本显式声明「此刻在远程协作」，
  也是无法自动探测时的兜底（例如远控换了别的软件）。

误判方向要安全：宁可让 ``grab`` 拒绝一次（用户可以明确加 ``--allow-thunder``），
也不要偷偷开起迅雷把远程会话挤掉。

    from acquire.remote_guard import remote_signals, block_reason
    remote_signals()                      # ['GameViewerServer.exe']
    block_reason()                        # '' 或中文理由
"""

from __future__ import annotations

import os
import subprocess
import sys

TRUTHY = {'1', 'true', 'yes', 'on'}
ENV_FLAG = 'KF_REMOTE_SESSION'

# 进程名（小写）→ 说明。只放「有人在用远程」的证据，不放自启动服务。
REMOTE_PROCESSES = {
    'gameviewerserver.exe': 'GameViewer 远程会话监听（GameViewerServer.exe）',
    'gameviewer.exe': 'GameViewer 托盘/远控 UI（GameViewer.exe）',
}


def _process_names() -> list[str]:
    """本机进程名列表；拿不到就返回空（不因此判定远程会话）。"""
    if sys.platform != 'win32':
        return []
    try:
        output = subprocess.run(
            ['tasklist', '/FO', 'CSV', '/NH'],
            capture_output=True, text=True, timeout=20,
            encoding='mbcs', errors='replace').stdout
    except (OSError, subprocess.SubprocessError):
        return []
    names = []
    for line in output.splitlines():
        if not line.startswith('"'):
            continue
        names.append(line.split('"')[1])
    return names


def env_flag(names: list[str] | None = None) -> bool:
    """``KF_REMOTE_SESSION`` 是否显式打开了远程会话标记。"""
    raw = (os.environ.get(ENV_FLAG, '') or os.environ.get(ENV_FLAG.lower(), '')).strip()
    return raw.lower() in TRUTHY


def remote_signals(processes: list[str] | None = None) -> list[str]:
    """当前指向「正在远程协作」的证据，空列表表示没有。

    ``processes`` 可注入（测试用），默认读本机进程表。
    """
    signals: list[str] = []
    if env_flag():
        signals.append(f'{ENV_FLAG}=1（显式声明远程协作）')
    names = _process_names() if processes is None else processes
    seen = set()
    for name in names:
        key = name.strip().lower()
        if key in REMOTE_PROCESSES and key not in seen:
            seen.add(key)
            signals.append(REMOTE_PROCESSES[key])
    return signals


def block_reason(allow: bool = False,
                 processes: list[str] | None = None) -> str:
    """要不要拦下「开迅雷」这个动作；返回空串表示放行。"""
    if allow:
        return ''
    signals = remote_signals(processes=processes)
    if not signals:
        return ''
    return ('远程协作进行中，禁止开启迅雷等占用型软件：' + '；'.join(signals) +
            '。远程会话结束后再下载；确需现在下载，由用户明确同意后加 --allow-thunder。')
