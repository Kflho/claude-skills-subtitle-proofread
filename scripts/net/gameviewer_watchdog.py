#!/usr/bin/env python3
"""GameViewer (UU remote) watchdog: restart the service/UI when it drops.

The remote link is served by the Windows service ``GameViewerService`` plus the
``GameViewerServer`` process; the tray UI (``GameViewer.exe``) is optional.  Our
network experiments (system proxy / TUN) can cut the outbound link, so this
watchdog only checks **local processes/service** and starts them again — it does
not touch proxy or firewall settings, because changing those is what broke the
connection in the first place.

Usage:
    python scripts/net/gameviewer_watchdog.py --status
    python scripts/net/gameviewer_watchdog.py --once
    python scripts/net/gameviewer_watchdog.py --watch --interval 15
    python scripts/net/gameviewer_watchdog.py --install-task

Machine paths come from env ``GAMEVIEWER_DIR`` or local.json
``paths.gameviewer_dir`` (never hard-coded in the repo).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import subprocess
import sys
import time
from pathlib import Path

SERVICE = 'GameViewerService'
SERVER_IMAGE = 'GameViewerServer.exe'
UI_EXE = 'GameViewer.exe'
TASK_NAME = 'SubtitleProofread-GameViewerWatchdog'


def resolve_dir(explicit: str = '') -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get('GAMEVIEWER_DIR', '').strip()
    if env:
        return Path(env)
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from lib.local_config import pick
        configured = pick('GAMEVIEWER_DIR', 'paths', 'gameviewer_dir')
        if configured:
            return Path(configured)
    except Exception:
        pass
    return Path('')


def log_path() -> Path:
    base = os.environ.get('LOCALAPPDATA') or str(Path.home())
    return Path(base) / 'subtitle-proofread' / 'logs' / 'gameviewer_watchdog.log'


def _run(args: list[str], timeout: float = 15.0) -> tuple[int, str]:
    try:
        proc = subprocess.run(args, capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    out = (proc.stdout or b'') + (proc.stderr or b'')
    return proc.returncode, out.decode('utf-8', errors='replace')


def service_running() -> bool:
    code, out = _run(['sc.exe', 'query', SERVICE])
    return code == 0 and 'RUNNING' in out.upper()


def process_running(image: str = SERVER_IMAGE) -> bool:
    code, out = _run(['tasklist.exe', '/FI', f'IMAGENAME eq {image}', '/NH'])
    return code == 0 and image.lower() in out.lower()


def start_service() -> bool:
    code, _ = _run(['sc.exe', 'start', SERVICE], timeout=30)
    return code == 0


def start_ui(directory: Path) -> bool:
    exe = directory / UI_EXE
    if not exe.is_file():
        return False
    try:
        subprocess.Popen([str(exe)], cwd=str(directory), close_fds=True)
        return True
    except OSError:
        return False


def ensure(directory: Path, *, wait: float = 8.0) -> dict:
    """Bring the remote link back if the service/server is missing."""
    actions = []
    if not service_running():
        actions.append('start-service')
        start_service()
        deadline = time.time() + wait
        while time.time() < deadline and not service_running():
            time.sleep(1)
    if not process_running():
        actions.append('start-ui')
        start_ui(directory)
        deadline = time.time() + wait
        while time.time() < deadline and not process_running():
            time.sleep(1)
    return {
        'service': service_running(),
        'server': process_running(),
        'actions': actions,
        'ok': service_running() and process_running(),
    }


def _log(message: str) -> None:
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with path.open('a', encoding='utf-8') as handle:
        handle.write(f'[{stamp}] {message}\n')


def cmd_status(directory: Path) -> int:
    state = {'dir': str(directory), 'service': service_running(),
             'server': process_running(), 'log': str(log_path())}
    print(state)
    return 0 if state['service'] and state['server'] else 1


def cmd_once(directory: Path) -> int:
    result = ensure(directory)
    _log(str(result))
    print(result)
    return 0 if result['ok'] else 1


def cmd_watch(directory: Path, interval: float) -> int:
    _log(f'watchdog started (dir={directory}, interval={interval}s)')
    last = None
    try:
        while True:
            result = ensure(directory)
            signature = (result['service'], result['server'], tuple(result['actions']))
            if signature != last:
                _log(str(result))
                last = signature
            time.sleep(max(3.0, interval))
    except KeyboardInterrupt:
        _log('watchdog stopped')
        return 0


def cmd_install_task(directory: Path, interval: int) -> int:
    script = Path(__file__).resolve()
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    python = pythonw if pythonw.is_file() else Path(sys.executable)
    # one-shot per trigger; the task scheduler owns the repetition (a --watch
    # process per minute would leave a pile of loops running)
    command = f'"{python}" "{script}" --once --dir "{directory}"'
    code, out = _run([
        'schtasks.exe', '/Create', '/TN', TASK_NAME, '/SC', 'MINUTE',
        '/MO', '1', '/TR', command, '/F', '/RL', 'LIMITED',
    ], timeout=30)
    print(out.strip())
    return 0 if code == 0 else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dir', default='', help='GameViewer install dir')
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--interval', type=float, default=15.0)
    parser.add_argument('--install-task', action='store_true')
    args = parser.parse_args(argv)

    directory = resolve_dir(args.dir)
    if not directory or not directory.is_dir():
        print('error: GameViewer dir not found; pass --dir or set '
              'GAMEVIEWER_DIR / local.json paths.gameviewer_dir', file=sys.stderr)
        return 2
    if args.install_task:
        return cmd_install_task(directory, int(args.interval))
    if args.status:
        return cmd_status(directory)
    if args.watch:
        return cmd_watch(directory, args.interval)
    return cmd_once(directory)


if __name__ == '__main__':
    sys.exit(main())
