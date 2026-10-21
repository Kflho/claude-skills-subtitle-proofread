#!/usr/bin/env python3
"""Bili23 Downloader acquisition client (MCP).

Bili23's GUI has no CLI, but it ships an MCP server (disabled by default) with
exactly the tools a pipeline needs:

    parse_url(url) -> episodes
    create_download(episode_ids, options{subtitle, subtitle_format, quality...})
    list_tasks / get_task_status / get_login_status

This module is a thin JSON-RPC client for that server.  It never stores the
MCP token: the token and port live in Bili23's own config.json (machine-local),
and the path to that config comes from the harness ``local.json``
(``paths.bili23_config``) or ``%APPDATA%\\Bili23 Downloader\\config.json``.

Commands:
    status                 config + server/login status
    enable                 set MCP.mcp_enabled, generate a token if missing
    parse <url>            parse and list episodes
    download <url> ...     parse -> create tasks (subtitle srt by default)
    tasks                  list download tasks

Run ``enable`` with Bili23 **closed**, then start the app again.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PROTOCOL_VERSION = '2025-06-18'


def default_config_path() -> Path:
    override = os.environ.get('BILI23_CONFIG')
    if override:
        return Path(override)
    appdata = os.environ.get('APPDATA') or str(Path.home() / 'AppData' / 'Roaming')
    return Path(appdata) / 'Bili23 Downloader' / 'config.json'


def resolve_config_path(explicit: str = '') -> Path:
    if explicit:
        return Path(explicit)
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from lib.local_config import pick
        configured = pick('BILI23_CONFIG', 'paths', 'bili23_config')
        if configured:
            return Path(configured)
    except Exception:
        pass
    return default_config_path()


def load_config(path: Path) -> dict:
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict):
        raise ValueError(f'{path} is not a JSON object')
    return data


def bili23_running() -> bool:
    """The GUI rewrites config.json on exit; only edit it while closed."""
    try:
        out = subprocess.run(['tasklist.exe', '/FI',
                              'IMAGENAME eq Bili23.exe', '/NH'],
                             capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return b'Bili23.exe' in (out.stdout or b'')


def read_download_path(path: Path) -> str:
    if not path.is_file():
        return ''
    config = load_config(path)
    return str((config.get('Download') or {}).get('download_path') or '').strip()


def skill_download_path() -> str:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from lib.local_config import pick
        return str(pick('BILI23_DOWNLOAD_DIR', 'paths', 'bili23_download_dir')
                   or '').strip()
    except Exception:
        return ''


def _save_skill_download_path(directory: Path) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from lib.local_config import config_path, load_local
    path = config_path()
    data = load_local(refresh=True)
    if not isinstance(data, dict):
        data = {}
    paths = data.setdefault('paths', {})
    paths['bili23_download_dir'] = str(directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n',
                    encoding='utf-8', newline='\n')


def _registry_module():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from lib import projects_registry
    return projects_registry


def resolve_download_dir(config_path: Path, explicit: str = '',
                         project: str = '', media_type: str = '',
                         series: str = '') -> Path:
    """CLI > registry > media library > local.json; ask if none is set.

    ``--media-type`` + ``--series`` resolve the folder from the machine library
    (``local.json`` → ``paths.media``) so a series always lands under
    ``<root>/<category>/<TV|OVA|Movie>/<series>`` instead of a per-project path.
    Project-level memory lives in the global registry (``projects_registry.json``
    → ``<project>.bili23.download_dir``), never inside the project folder.
    """
    reg = _registry_module()
    project_dir = reg.project_dir(project or None)
    project_value = str(reg.abs_path(project_dir,
                                     str(reg.value(project_dir, 'bili23', 'download_dir')
                                         or ''))).strip()
    current = read_download_path(config_path)
    remembered = skill_download_path()
    library_value = ''
    if media_type:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from lib import media_paths
        category = str(reg.value(project_dir, 'category') or 'animation')
        library_value = media_paths.series_dir(
            series or str(reg.value(project_dir, 'series') or '').strip(),
            media_type, category)
        if not library_value:
            raise MCPError(
                'media library is not configured: add paths.media.root to '
                'local.json (or pass --download-dir / set-download-path)')
    chosen = explicit.strip() or project_value or library_value or remembered
    if not chosen:
        hint = f' Bili23 currently points at {current!r}.' if current else ''
        raise MCPError(
            'download path is not configured for this project. Ask the user '
            'where to save the video/subtitles, then run: bili23.py '
            'set-download-path "<dir>" --project "<project-dir>" '
            '(close Bili23 first).' + hint)
    chosen_path = Path(chosen)
    if current and os.name == 'nt':
        same = os.path.normcase(os.path.abspath(current)) == os.path.normcase(
            os.path.abspath(str(chosen_path)))
    else:
        same = Path(current).absolute() == chosen_path.absolute()
    if current and not same:
        raise MCPError(
            f'Bili23 is configured for {current!r}, not {str(chosen_path)!r}. '
            'Close Bili23, run set-download-path, then start it again')
    return chosen_path


def set_download_path(config_path: Path, directory: str,
                      project: str = '', scope: str = 'auto') -> dict:
    if bili23_running():
        raise MCPError('close Bili23 completely before changing its download '
                       'path (it rewrites config.json on exit)')
    reg = _registry_module()
    target = Path(directory).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    project_dir = ''
    chosen_scope = scope
    if project or scope == 'project':
        if not project:
            raise MCPError('--project is required with --scope project')
        project_dir = reg.project_dir(project)
        if not os.path.isdir(project_dir):
            raise MCPError(f'project dir not found: {project_dir}')
    if scope == 'auto':
        chosen_scope = 'project' if project_dir else 'global'
    if chosen_scope == 'project' and not project_dir:
        raise MCPError('no project given; pass --project or --scope global')
    config = load_config(config_path)
    download = config.setdefault('Download', {})
    previous = download.get('download_path')
    download['download_path'] = str(target).replace('\\', '/')
    backup = config_path.with_suffix(config_path.suffix + '.bak-download')
    if not backup.exists():
        shutil.copyfile(config_path, backup)
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2)
                           + '\n', encoding='utf-8', newline='\n')
    if chosen_scope == 'project':
        reg.set_value(project_dir, 'bili23', 'download_dir', value=str(target))
    else:
        _save_skill_download_path(target)
    return {'config': str(config_path), 'previous': previous,
            'download_path': str(target), 'scope': chosen_scope,
            'registry_project': reg.project_key(project_dir) if project_dir else None}


def cmd_set_download_path(args) -> int:
    path = resolve_config_path(args.config)
    result = set_download_path(path, args.directory, args.project, args.scope)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def enable_mcp(path: Path, *, force_token: bool = False) -> dict:
    """Turn the MCP server on and make sure a token exists (backup first)."""
    if not path.is_file():
        raise FileNotFoundError(path)
    config = load_config(path)
    mcp = config.setdefault('MCP', {})
    mcp['mcp_enabled'] = True
    if force_token or not mcp.get('mcp_token'):
        mcp['mcp_token'] = secrets.token_urlsafe(32)
    backup = path.with_suffix(path.suffix + '.bak')
    if not backup.exists():
        shutil.copyfile(path, backup)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n',
                    encoding='utf-8')
    return {'enabled': True, 'port': mcp.get('mcp_port'), 'token_set': bool(mcp.get('mcp_token'))}


class MCPError(RuntimeError):
    pass


class Bili23MCP:
    def __init__(self, port: int, token: str, host: str = '127.0.0.1',
                 timeout: float = 30.0):
        self.url = f'http://{host}:{port}/mcp'
        self.token = token
        self.timeout = timeout
        self._id = 0

    def _post(self, payload: dict) -> dict:
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        request = urllib.request.Request(
            self.url, data=body, method='POST',
            headers={'Content-Type': 'application/json',
                     'Authorization': f'Bearer {self.token}'})
        last_error = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read()
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode('utf-8', errors='replace')[:200]
                raise MCPError(f'HTTP {exc.code}: {detail}') from exc
            except (urllib.error.URLError, ConnectionResetError) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(1.0 + attempt)
        else:
            raise MCPError(
                f'cannot reach Bili23 MCP at {self.url}: {last_error}; '
                'is the app running with MCP enabled?')
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MCPError(f'invalid JSON from MCP: {raw[:120]!r}') from exc
        if 'error' in data:
            raise MCPError(str(data['error']))
        return data.get('result') or {}

    def request(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        payload = {'jsonrpc': '2.0', 'id': self._id, 'method': method,
                   'params': params or {}}
        return self._post(payload)

    def initialize(self) -> dict:
        return self.request('initialize', {
            'protocolVersion': PROTOCOL_VERSION,
            'capabilities': {},
            'clientInfo': {'name': 'subtitle-proofread', 'version': '1'},
        })

    def call(self, name: str, arguments: dict | None = None) -> dict:
        result = self.request('tools/call', {'name': name,
                                             'arguments': arguments or {}})
        if result.get('isError'):
            messages = [item.get('text', '') for item in result.get('content') or []]
            raise MCPError('; '.join(m for m in messages if m) or f'{name} failed')
        return result.get('structuredContent') or {}

    # ---- high-level helpers -------------------------------------------
    def login_status(self) -> dict:
        return self.call('get_login_status')

    def parse(self, url: str, limit: int = 100) -> dict:
        return self.call('parse_url', {'url': url, 'limit': limit})

    def create_download(self, episode_ids: list[str], options: dict | None = None,
                        redownload: bool = False) -> dict:
        args = {'episode_ids': episode_ids, 'redownload': redownload}
        if options:
            args['options'] = options
        return self.call('create_download', args)

    def tasks(self, state: str = 'downloading', limit: int = 30) -> dict:
        return self.call('list_tasks', {'state': state, 'limit': limit})

    def task_status(self, task_id: str) -> dict:
        return self.call('get_task_status', {'task_id': task_id})


def _cookie_header(config: dict) -> str:
    cookie = config.get('Cookie') or {}
    parts = []
    for key, value in cookie.items():
        if key in ('is_login', 'buvid_expires', 'bili_ticket_expires') \
                or key.endswith('__ckMd5'):
            continue
        if isinstance(value, str) and value:
            parts.append(f'{key}={value}')
    return '; '.join(parts)


def _clean_title(text: str) -> str:
    return re.sub(r'</?em[^>]*>', '', str(text or '')).strip()


def _proxy_from_local() -> str:
    try:
        from lib.local_config import pick
        return str(pick('HTTP_PROXY', 'network', 'proxy')
                   or pick('HTTPS_PROXY', 'network', 'proxy') or '')
    except Exception:
        return ''


def _open(url: str, headers: dict, proxy: str, timeout: float = 25.0):
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({'http': proxy,
                                                     'https': proxy}))
    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(url, headers=headers)
    return opener.open(request, timeout=timeout).read()


def search_videos(keyword: str, *, config_path: Path, proxy: str = '',
                  limit: int = 20, timeout: float = 25.0) -> list[dict]:
    """Search Bilibili videos (the MCP server has no search tool)."""
    config = load_config(config_path) if config_path.is_file() else {}
    headers = {
        'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                       'AppleWebKit/537.36 Chrome/131.0 Safari/537.36'),
        'Referer': 'https://www.bilibili.com/',
    }
    cookie = _cookie_header(config)
    if cookie:
        headers['Cookie'] = cookie
    query = urllib.parse.urlencode({'search_type': 'video',
                                    'keyword': keyword, 'page': 1})
    payload = json.loads(_open(
        'https://api.bilibili.com/x/web-interface/search/type?' + query,
        headers, proxy or _proxy_from_local(), timeout))
    if payload.get('code') != 0:
        raise MCPError(f"Bilibili search failed: {payload.get('code')} "
                       f"{payload.get('message')}")
    rows = (payload.get('data') or {}).get('result') or []
    out = []
    for row in rows[:limit]:
        out.append({
            'bvid': row.get('bvid', ''),
            'title': _clean_title(row.get('title')),
            'author': row.get('author', ''),
            'duration': row.get('duration', ''),
            'play': row.get('play', ''),
            'pubdate': row.get('pubdate', ''),
            'description': _clean_title(row.get('description'))[:120],
        })
    return out


def _client_from_config(config_path: Path, timeout: float = 30.0) -> Bili23MCP:
    config = load_config(config_path)
    mcp = config.get('MCP') or {}
    if not mcp.get('mcp_enabled'):
        raise MCPError(f'MCP is disabled in {config_path}; run `bili23.py enable` '
                       'with the app closed, then start it again')
    port = int(mcp.get('mcp_port') or 23330)
    token = str(mcp.get('mcp_token') or '')
    if not token:
        raise MCPError(f'no MCP token in {config_path}; run `bili23.py enable`')
    return Bili23MCP(port, token, timeout=timeout)


def _print_json(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _episode_rows(parsed: dict) -> list[dict]:
    return list(parsed.get('episodes') or [])


def _select_ids(parsed: dict, spec: str) -> list[str]:
    episodes = _episode_rows(parsed)
    if not episodes:
        raise MCPError('parse returned no episodes')
    if spec in ('all', '0', ''):
        return [str(item['episode_id']) for item in episodes]
    wanted = [part.strip() for part in spec.split(',') if part.strip()]
    by_id = {str(item['episode_id']): item for item in episodes}
    missing = [item for item in wanted if item not in by_id]
    if missing:
        raise MCPError(f'unknown episode_id: {missing}; available: '
                       f'{", ".join(by_id)}')
    return wanted


def _wait_for_tasks(client: Bili23MCP, task_ids: list[str], timeout: float,
                    interval: float = 3.0) -> list[dict]:
    deadline = time.time() + timeout
    pending = set(task_ids)
    results = {}
    while pending and time.time() < deadline:
        for task_id in list(pending):
            status = client.task_status(task_id)
            results[task_id] = status
            state = str(status.get('status') or '')
            if state in ('completed', 'failed', 'cancelled', 'error'):
                pending.discard(task_id)
        if pending:
            time.sleep(interval)
    return [results[t] for t in task_ids if t in results]


def cmd_search(args) -> int:
    path = resolve_config_path(args.config)
    rows = search_videos(args.keyword, config_path=path, proxy=args.proxy,
                         limit=args.limit, timeout=args.timeout)
    if args.json:
        _print_json(rows)
        return 0
    print(f'{len(rows)} result(s) for {args.keyword!r}')
    for row in rows:
        print(f"  {row['bvid']}\t{row['title'][:60]}\t{row['author']}\t{row['duration']}")
    return 0


def cmd_status(args) -> int:
    path = resolve_config_path(args.config)
    if not path.is_file():
        print(f'config not found: {path}', file=sys.stderr)
        return 2
    config = load_config(path)
    mcp = config.get('MCP') or {}
    download = config.get('Download') or {}
    reg = _registry_module()
    project_dir = reg.project_dir()
    project_download = str(reg.value(project_dir, 'bili23', 'download_dir') or '').strip()
    info = {'config': str(path), 'mcp_enabled': bool(mcp.get('mcp_enabled')),
            'mcp_port': mcp.get('mcp_port'), 'mcp_token_set': bool(mcp.get('mcp_token')),
            'download_path': download.get('download_path'),
            'project': reg.project_key(project_dir),
            'project_file': str(reg.registry_path()),
            'project_download_dir': project_download or None,
            'global_download_path': skill_download_path() or None,
            'concurrency': read_concurrency(path),
            'server': None}
    if mcp.get('mcp_enabled') and mcp.get('mcp_token'):
        try:
            client = _client_from_config(path, timeout=args.timeout)
            client.initialize()
            info['server'] = client.login_status()
        except MCPError as exc:
            info['server'] = {'error': str(exc)}
    _print_json(info)
    return 0


def cmd_enable(args) -> int:
    path = resolve_config_path(args.config)
    result = enable_mcp(path)
    print(json.dumps({'config': str(path), **result}, ensure_ascii=False, indent=2))
    print('Close Bili23 completely and start it again; MCP listens on 127.0.0.1.',
          file=sys.stderr)
    return 0


def cmd_parse(args) -> int:
    client = _client_from_config(resolve_config_path(args.config), args.timeout)
    client.initialize()
    parsed = client.parse(args.url, args.limit)
    if args.json:
        _print_json(parsed)
        return 0
    print(f"category={parsed.get('category')} total={parsed.get('total')}")
    for item in _episode_rows(parsed):
        mark = ' *' if item.get('is_link_target') else ''
        print(f"  {item.get('episode_id')}\t{item.get('title')}{mark}")
    return 0


def cmd_download(args) -> int:
    path = resolve_config_path(args.config)
    try:
        directory = resolve_download_dir(path, args.download_dir, args.project,
                                         args.media_type, args.series)
    except MCPError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2
    print(f'[download] target: {directory}')
    client = _client_from_config(path, args.timeout)
    client.initialize()
    parsed = client.parse(args.url, args.limit)
    ids = _select_ids(parsed, args.episodes)
    options = {
        'subtitle': not args.no_subtitle,
        'subtitle_format': args.subtitle_format,
        'subtitle_languages': [args.subtitle_lang] if args.subtitle_lang else [],
        'video_quality': args.quality,
        'video_codec': args.codec,
        'container': args.container,
    }
    created = client.create_download(ids, options, redownload=args.redownload)
    if args.json:
        _print_json(created)
    else:
        print(f"created {created.get('created')} task(s)")
        for task in created.get('tasks') or []:
            print(f"  {task.get('task_id')}\t{task.get('title')}")
        for note in created.get('notes') or []:
            print(f'  note: {note}')
        concurrency = read_concurrency(path)
        print(f"[download] Bili23 concurrency: parallel={concurrency['parallel']} "
              f"threads={concurrency['threads']} "
              f"(download_parallel / download_thread in its config)")
        if concurrency['parallel'] is not None and concurrency['parallel'] < 2 \
                and len(created.get('tasks') or []) > 1:
            print('  hint: only one episode downloads at a time; raise '
                  'download_parallel (close Bili23 first: it rewrites config)',
                  file=sys.stderr)
    if args.wait and created.get('tasks'):
        task_ids = [str(t['task_id']) for t in created['tasks']]
        results = _wait_for_tasks(client, task_ids, args.wait_timeout)
        if not args.json:
            print('--- finished ---')
        for status in results:
            print(json.dumps(status, ensure_ascii=False))
    return 0


def read_concurrency(config_path: Path) -> dict:
    """Bili23's own knobs: parallel = tasks at once, threads = chunks per file.

    From the app source: ``DownloadListModel._manageConcurrentDownloads``
    dispatches queued tasks while ``active_count < download_parallel``, and
    ``Downloader.__init__`` calls ``thread_pool.setMaxThreadCount(
    download_thread)`` per task.
    """
    try:
        data = load_config(config_path)
    except (OSError, json.JSONDecodeError):
        return {'parallel': None, 'threads': None}
    block = data.get('Download') or {}
    return {'parallel': block.get('download_parallel'),
            'threads': block.get('download_thread')}


def suggest_media_type(parsed: dict) -> dict:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from lib import media_paths

    episodes = _episode_rows(parsed)
    durations = [float(item.get('duration') or 0) for item in episodes]
    kind, reason = media_paths.suggest_media_type(len(episodes), durations)
    return {'media_type': kind, 'reason': reason,
            'target_dir': media_paths.series_dir(
                str(parsed.get('title') or '').strip() or '<series>', kind)}


def cmd_plan(args) -> int:
    """Parse and show where this upload would land — before any download."""
    path = resolve_config_path(args.config)
    client = _client_from_config(path, args.timeout)
    client.initialize()
    parsed = client.parse(args.url, args.limit)
    episodes = _episode_rows(parsed)
    guess = suggest_media_type(parsed)
    concurrency = read_concurrency(path)
    target = ''
    try:
        if args.media_type in ('tv', 'ova', 'movie'):
            target = str(resolve_download_dir(path, args.download_dir, args.project,
                                              args.media_type, args.series))
        else:
            target = str(resolve_download_dir(path, args.download_dir, args.project))
    except MCPError as exc:
        target = f'(unresolved: {exc})'
    payload = {
        'title': parsed.get('title'),
        'category': parsed.get('category'),
        'total': parsed.get('total'),
        'durations_min': sorted({round(float(e.get('duration') or 0) / 60, 1)
                                 for e in episodes}),
        'suggested': guess,
        'declared_media_type': args.media_type,
        'download_dir': target,
        'bili23_concurrency': concurrency,
        'available': parsed.get('available'),
    }
    _print_json(payload)
    if args.media_type in ('auto', ''):
        print(f"# 建议 media_type={guess['media_type']} ({guess['reason']})；"
              f"确认后用 --media-type {guess['media_type']} --series '<系列名>'",
              file=sys.stderr)
    return 0


def cmd_tasks(args) -> int:
    client = _client_from_config(resolve_config_path(args.config), args.timeout)
    client.initialize()
    data = client.tasks(args.state, args.limit)
    if args.json:
        _print_json(data)
        return 0
    for task in data.get('tasks') or []:
        print(f"{task.get('task_id')}\t{task.get('status')}\t"
              f"{task.get('progress')}%\t{task.get('title')}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='',
                        help='Bili23 config.json (default: local.json path)')
    parser.add_argument('--timeout', type=float, default=30.0)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--proxy', default='',
                        help='HTTP proxy, e.g. http://127.0.0.1:7890')
    sub = parser.add_subparsers(dest='command', required=True)

    sub.add_parser('status')
    sub.add_parser('enable')

    set_path = sub.add_parser('set-download-path')
    set_path.add_argument('directory')
    set_path.add_argument('--project', default='',
                          help='project dir (writes its projects_registry entry)')
    set_path.add_argument('--scope', choices=('auto', 'project', 'global'),
                          default='auto')

    search = sub.add_parser('search')
    search.add_argument('keyword')
    search.add_argument('--limit', type=int, default=20)

    parse = sub.add_parser('parse')
    parse.add_argument('url')
    parse.add_argument('--limit', type=int, default=100)

    plan = sub.add_parser('plan', help='parse + suggest TV/OVA/MOVIE + target dir')
    plan.add_argument('url')
    plan.add_argument('--limit', type=int, default=100)
    plan.add_argument('--media-type', default='auto',
                      choices=('auto', 'tv', 'ova', 'movie'))
    plan.add_argument('--series', default='')
    plan.add_argument('--project', default='')
    plan.add_argument('--download-dir', default='')

    download = sub.add_parser('download')
    download.add_argument('url')
    download.add_argument('--episodes', default='all',
                          help="'all' or comma-separated episode_id values")
    download.add_argument('--limit', type=int, default=100)
    download.add_argument('--subtitle-format', default='srt')
    download.add_argument('--subtitle-lang', default='zh-CN')
    download.add_argument('--no-subtitle', action='store_true')
    download.add_argument('--quality', default='auto')
    download.add_argument('--codec', default='auto')
    download.add_argument('--container', default='mkv')
    download.add_argument('--download-dir', default='',
                          help='must match the project/Bili23 configured dir')
    download.add_argument('--media-type', default='',
                          choices=('', 'tv', 'ova', 'movie'),
                          help='resolve the folder from local.json paths.media')
    download.add_argument('--series', default='',
                          help='series folder name under <category>/<KIND>/')
    download.add_argument('--project', default='',
                          help='project dir whose registry entry holds the download dir')
    download.add_argument('--redownload', action='store_true',
                          help='re-create tasks Bili23 considers already downloaded')
    download.add_argument('--wait', action='store_true')
    download.add_argument('--wait-timeout', type=float, default=1800.0)

    tasks = sub.add_parser('tasks')
    tasks.add_argument('--state', choices=('downloading', 'completed', 'all'),
                       default='downloading')
    tasks.add_argument('--limit', type=int, default=30)

    args = parser.parse_args(argv)
    handlers = {'status': cmd_status, 'enable': cmd_enable, 'parse': cmd_parse,
                'plan': cmd_plan, 'download': cmd_download, 'tasks': cmd_tasks,
                'search': cmd_search, 'set-download-path': cmd_set_download_path}
    try:
        return handlers[args.command](args)
    except (MCPError, FileNotFoundError, ValueError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
