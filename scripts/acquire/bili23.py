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
    info = {'config': str(path), 'mcp_enabled': bool(mcp.get('mcp_enabled')),
            'mcp_port': mcp.get('mcp_port'), 'mcp_token_set': bool(mcp.get('mcp_token')),
            'download_path': download.get('download_path'), 'server': None}
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
    client = _client_from_config(resolve_config_path(args.config), args.timeout)
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
    created = client.create_download(ids, options)
    if args.json:
        _print_json(created)
    else:
        print(f"created {created.get('created')} task(s)")
        for task in created.get('tasks') or []:
            print(f"  {task.get('task_id')}\t{task.get('title')}")
        for note in created.get('notes') or []:
            print(f'  note: {note}')
    if args.wait and created.get('tasks'):
        task_ids = [str(t['task_id']) for t in created['tasks']]
        results = _wait_for_tasks(client, task_ids, args.wait_timeout)
        if not args.json:
            print('--- finished ---')
        for status in results:
            print(json.dumps(status, ensure_ascii=False))
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

    search = sub.add_parser('search')
    search.add_argument('keyword')
    search.add_argument('--limit', type=int, default=20)

    parse = sub.add_parser('parse')
    parse.add_argument('url')
    parse.add_argument('--limit', type=int, default=100)

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
    download.add_argument('--wait', action='store_true')
    download.add_argument('--wait-timeout', type=float, default=1800.0)

    tasks = sub.add_parser('tasks')
    tasks.add_argument('--state', choices=('downloading', 'completed', 'all'),
                       default='downloading')
    tasks.add_argument('--limit', type=int, default=30)

    args = parser.parse_args(argv)
    handlers = {'status': cmd_status, 'enable': cmd_enable, 'parse': cmd_parse,
                'download': cmd_download, 'tasks': cmd_tasks,
                'search': cmd_search}
    try:
        return handlers[args.command](args)
    except (MCPError, FileNotFoundError, ValueError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
