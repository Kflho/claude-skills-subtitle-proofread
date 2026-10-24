#!/usr/bin/env python3
"""P2P acquisition: find a release, hand it to a local client, collect it.

Why this exists
---------------
The Bilibili route (:mod:`scripts.acquire.bili23`) only covers what Bilibili
still hosts.  For an old TV series the video usually only exists as a public
BitTorrent release, and the subtlety is not "how to download" — every client can
— it is **where the file ends up** and **whether it is really complete**:

- a torrent's file list is the only exact completion oracle.  A client's "100%"
  can lag, resume wrong, or skip files; comparing each file's size against the
  ``.torrent`` is something no client has to be trusted for;
- the library layout is ``<media root>/<category>/<KIND>/<series>``
  (:func:`lib.media_paths.series_dir`), while clients download into their own
  default folder.  Someone has to move the tree afterwards;
- on a machine with 迅雷 VIP the download itself is best handed to 迅雷 — this
  script does exactly that, and nothing else pretends to be a client.

Design: search is a set of thin indexer adapters (Nyaa / dmhy / Mikan / acg.rip);
grab resolves a ``.torrent`` first, records its file list as a *manifest*, then
launches the client with the torrent file (never a magnet when the torrent is
available — magnets depend on DHT and carry no file list).  ``status`` and
``collect`` work off the manifest, so they stay correct even after a client
restart.

    python scripts/acquire/p2p.py clients
    python scripts/acquire/p2p.py search -q "じゃりン子チエ" --limit 8
    python scripts/acquire/p2p.py grab --torrent-id nyaa:1927106 \
        --series 小麻烦千惠 --media-type tv
    python scripts/acquire/p2p.py status
    python scripts/acquire/p2p.py collect --series 小麻烦千惠 --media-type tv

Machine paths are never written here: 迅雷's executable comes from the
``magnet:`` protocol handler or ``local.json`` (``paths.thunder_exe``), its
download folder from 迅雷's own ``Profiles/config.json``, and an override wins
via ``--thunder-exe`` / ``--download-dir``.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from acquire import remote_guard  # noqa: E402
from lib.local_config import local_value  # noqa: E402

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')
DEFAULT_SITES = ('nyaa', 'dmhy', 'mikan', 'acg')


# ── HTTP with the machine's proxy ────────────────────────────────────────


def system_proxy() -> str:
    """Proxy URL from env, local.json, or the Windows system settings."""
    for name in ('HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy'):
        value = os.environ.get(name, '').strip()
        if value:
            return value
    configured = str(local_value('net', 'proxy', default='') or '').strip()
    if configured:
        return configured
    if sys.platform != 'win32':
        return ''
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Software\Microsoft\Windows\CurrentVersion\Internet Settings') as key:
            enabled, _ = winreg.QueryValueEx(key, 'ProxyEnable')
            server, _ = winreg.QueryValueEx(key, 'ProxyServer')
        if enabled and server:
            server = str(server)
            if '=' in server:                     # "http=host:port;https=host:port"
                for part in server.split(';'):
                    if part.startswith('http='):
                        return 'http://' + part.split('=', 1)[1]
            return 'http://' + server
    except OSError:
        pass
    return ''


def http_get(url: str, *, proxy: str = '', timeout: float = 30.0,
             encoding: str = '') -> tuple[bytes, str]:
    """GET a URL, honouring an explicit or system proxy.  Returns (body, text)."""
    if proxy:
        try:
            import httpx
            with httpx.Client(proxy=proxy, timeout=timeout, follow_redirects=True,
                              headers={'User-Agent': UA}) as client:
                response = client.get(url)
                response.raise_for_status()
                raw = response.content
                if not encoding:
                    encoding = response.encoding or 'utf-8'
        except ImportError:
            import urllib.request
            handler = urllib.request.ProxyHandler({'http': proxy, 'https': proxy})
            opener = urllib.request.build_opener(handler)
            request = urllib.request.Request(url, headers={'User-Agent': UA})
            with opener.open(request, timeout=timeout) as response:
                raw = response.read()
            encoding = encoding or 'utf-8'
    else:
        import urllib.request
        request = urllib.request.Request(url, headers={'User-Agent': UA})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
        encoding = encoding or 'utf-8'
    return raw, raw.decode(encoding, errors='replace')


# ── bencode: the only way to know what a torrent really contains ─────────


def bdecode(data: bytes):
    pos = 0

    def parse():
        nonlocal pos
        token = data[pos:pos + 1]
        if token == b'i':
            end = data.index(b'e', pos)
            value = int(data[pos + 1:end])
            pos = end + 1
            return value
        if token == b'l':
            pos += 1
            items = []
            while data[pos:pos + 1] != b'e':
                items.append(parse())
            pos += 1
            return items
        if token == b'd':
            pos += 1
            mapping = {}
            while data[pos:pos + 1] != b'e':
                key = parse()
                mapping[key] = parse()
            pos += 1
            return mapping
        colon = data.index(b':', pos)
        length = int(data[pos:colon])
        start = colon + 1
        pos = start + length
        return data[start:pos]

    return parse()


def torrent_manifest(raw: bytes) -> dict:
    """``{name, total, files: [{path, length}], announce}`` from .torrent bytes."""
    meta = bdecode(raw).get(b'info') or {}
    name = (meta.get(b'name') or b'').decode('utf-8', 'replace')
    files = []
    if b'files' in meta:
        for entry in meta[b'files']:
            parts = [p.decode('utf-8', 'replace') for p in entry.get(b'path') or []]
            files.append({'path': '/'.join(parts), 'length': int(entry.get(b'length') or 0)})
    else:
        files.append({'path': name, 'length': int(meta.get(b'length') or 0),
                      'single': True})
    return {
        'name': name,
        'total': sum(f['length'] for f in files),
        'files': files,
        'announce': (bdecode(raw).get(b'announce') or b'').decode('utf-8', 'replace'),
    }


# ── indexers ─────────────────────────────────────────────────────────────


def _size_to_bytes(text: str) -> int:
    match = re.search(r'([\d.]+)\s*([KMGT]i?B)', text or '', re.I)
    if not match:
        return 0
    scale = {'K': 1 << 10, 'M': 1 << 20, 'G': 1 << 30, 'T': 1 << 40}
    return int(float(match.group(1)) * scale[match.group(2)[0].upper()])


def search_nyaa(query: str, limit: int, proxy: str) -> list[dict]:
    url = 'https://nyaa.si/'
    from urllib.parse import urlencode
    full = url + '?' + urlencode({'page': 'rss', 'q': query, 'c': '1_0', 'f': '0'})
    _, text = http_get(full, proxy=proxy)
    results = []
    for item in re.findall(r'<item>(.*?)</item>', text, re.S)[:limit]:
        title = html.unescape(_pick(item, 'title'))
        link = _pick(item, 'link')
        torrent_id = re.search(r'/download/(\d+)\.torrent', link)
        results.append({
            'site': 'nyaa',
            'title': title,
            'size': _pick(item, 'nyaa:size'),
            'bytes': _size_to_bytes(_pick(item, 'nyaa:size')),
            'torrent_id': f'nyaa:{torrent_id.group(1)}' if torrent_id else '',
            'torrent_url': link,
            'magnet': _pick(item, 'nyaa:infoHash') and
                      f"magnet:?xt=urn:btih:{_pick(item, 'nyaa:infoHash')}",
            'seeders': int(_pick(item, 'nyaa:seeders') or 0),
            'published': _pick(item, 'pubDate'),
        })
    return results


def search_dmhy(query: str, limit: int, proxy: str) -> list[dict]:
    from urllib.parse import urlencode
    url = 'https://share.dmhy.org/topics/list?' + urlencode({'keyword': query})
    _, text = http_get(url, proxy=proxy)
    results = []
    for row in re.findall(r'<tr[^>]*class="topic-list"[^>]*>(.*?)</tr>', text, re.S)[:limit]:
        title = html.unescape(re.search(r'title="([^"]+)"', row).group(1)) \
            if re.search(r'title="([^"]+)"', row) else ''
        magnet = re.search(r'href="(magnet:[^"]+)"', row)
        size = re.search(r'<td[^>]*>\s*([\d.]+\s*[KMGT]B)\s*</td>', row)
        results.append({
            'site': 'dmhy',
            'title': title,
            'size': size.group(1) if size else '',
            'bytes': _size_to_bytes(size.group(1)) if size else 0,
            'torrent_id': '',
            'torrent_url': '',
            'magnet': html.unescape(magnet.group(1)) if magnet else '',
            'seeders': 0,
            'published': '',
        })
    return results


def search_mikan(query: str, limit: int, proxy: str) -> list[dict]:
    from urllib.parse import urlencode
    url = 'https://mikanani.me/RSS/Search?' + urlencode({'searchstr': query})
    _, text = http_get(url, proxy=proxy)
    results = []
    for item in re.findall(r'<item>(.*?)</item>', text, re.S)[:limit]:
        enclosure = re.search(r'<enclosure[^>]*url="([^"]+)"', item, re.S)
        results.append({
            'site': 'mikan',
            'title': html.unescape(_pick(item, 'title')),
            'size': '',
            'bytes': 0,
            'torrent_id': '',
            'torrent_url': html.unescape(enclosure.group(1)) if enclosure else '',
            'magnet': (re.search(r'(magnet:\?[^"<\s]+)', item).group(1)
                       if re.search(r'(magnet:\?[^"<\s]+)', item) else ''),
            'seeders': 0,
            'published': _pick(item, 'pubDate'),
        })
    return results


def search_acg(query: str, limit: int, proxy: str) -> list[dict]:
    from urllib.parse import urlencode
    url = 'https://acg.rip/.xml?' + urlencode({'term': query})
    _, text = http_get(url, proxy=proxy)
    results = []
    for item in re.findall(r'<item>(.*?)</item>', text, re.S)[:limit]:
        enclosure = re.search(r'<enclosure[^>]*url="([^"]+)"', item, re.S)
        results.append({
            'site': 'acg',
            'title': html.unescape(_pick(item, 'title')),
            'size': '',
            'bytes': 0,
            'torrent_id': '',
            'torrent_url': html.unescape(enclosure.group(1)) if enclosure else '',
            'magnet': (re.search(r'(magnet:\?[^"<\s]+)', item).group(1)
                       if re.search(r'(magnet:\?[^"<\s]+)', item) else ''),
            'seeders': 0,
            'published': _pick(item, 'pubDate'),
        })
    return results


def _pick(xml: str, tag: str) -> str:
    match = re.search(rf'<{re.escape(tag)}[^>]*>(.*?)</{re.escape(tag)}>', xml, re.S)
    return html.unescape(match.group(1).strip()) if match else ''


SEARCHERS = {'nyaa': search_nyaa, 'dmhy': search_dmhy,
             'mikan': search_mikan, 'acg': search_acg}


# ── clients ──────────────────────────────────────────────────────────────


def _reg_value(key: str, name: str = '') -> str:
    if sys.platform != 'win32':
        return ''
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
            value, _ = winreg.QueryValueEx(handle, name)
            return str(value)
    except OSError:
        return ''


def find_thunder(override: str = '') -> str:
    """迅雷.exe — override > local.json > magnet: handler > uninstall entry."""
    if override:
        return override
    configured = str(local_value('paths', 'thunder_exe', default='') or '')
    if configured and os.path.isfile(configured):
        return configured
    command = _reg_value(r'Software\Classes\magnet\shell\open\command')
    match = re.search(r'"([^"]+\.exe)"', command, re.I)
    if match and os.path.isfile(match.group(1)):
        return match.group(1)
    try:
        import winreg
        roots = [(winreg.HKEY_CURRENT_USER,
                  r'Software\Microsoft\Windows\CurrentVersion\Uninstall'),
                 (winreg.HKEY_LOCAL_MACHINE,
                  r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'),
                 (winreg.HKEY_LOCAL_MACHINE,
                  r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall')]
        for root, path in roots:
            with winreg.OpenKey(root, path) as parent:
                for index in range(winreg.QueryInfoKey(parent)[0]):
                    try:
                        name = winreg.EnumKey(parent, index)
                        with winreg.OpenKey(parent, name) as child:
                            display = str(winreg.QueryValueEx(child, 'DisplayName')[0])
                            location = str(winreg.QueryValueEx(child, 'InstallLocation')[0])
                    except OSError:
                        continue
                    if '迅雷' in display or 'Thunder' in display:
                        for candidate in ('Program/Thunder.exe', 'Thunder.exe'):
                            exe = os.path.join(location, *candidate.split('/'))
                            if os.path.isfile(exe):
                                return exe
    except (ImportError, OSError):
        pass
    return ''


def thunder_profile_dir(exe: str) -> str:
    """迅雷's ``Profiles`` folder, derived from the executable location."""
    if not exe:
        return ''
    for candidate in (Path(exe).parent.parent / 'Profiles',
                      Path(exe).parent / 'Profiles'):
        if candidate.is_dir():
            return str(candidate)
    return ''


def _thunder_config(exe: str) -> dict:
    profile = thunder_profile_dir(exe)
    if not profile:
        return {}
    try:
        return json.loads((Path(profile) / 'config.json').read_text(encoding='utf-8-sig'))
    except (OSError, json.JSONDecodeError):
        return {}


def thunder_download_dir(exe: str, override: str = '') -> str:
    """Where 迅雷 saves new tasks: override > local.json > 迅雷 config.json."""
    if override:
        return override
    configured = str(local_value('paths', 'xunlei_download_dir', default='') or '')
    if configured:
        return configured
    data = _thunder_config(exe)
    default = (data.get('TaskDefaultSettings') or {}).get('DefaultPath') or ''
    if default and os.path.isdir(default):
        return str(default)
    return ''


def thunder_last_path(exe: str) -> str:
    """The folder 迅雷's new-task panel defaults to (its own "last used")."""
    data = _thunder_config(exe)
    path = (data.get('PathAndCategory') or {}).get('LastUsedPath') or ''
    return str(path) if path and os.path.isdir(path) else ''


def thunder_add(exe: str, target: str, kind: str = 'torrent',
                wait: float = 0.0) -> subprocess.Popen:
    """Start 迅雷 with a .torrent path or magnet URI as the new task."""
    if not exe or not os.path.isfile(exe):
        raise SystemExit(f'[p2p] 迅雷 not found (looked for Thunder.exe); '
                         f'pass --thunder-exe or set local.json paths.thunder_exe')
    args = [exe, str(target), f'-StartType:{kind}']
    process = subprocess.Popen(args, cwd=str(Path(exe).parent),
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if wait:
        time.sleep(wait)
    return process


# ── manifests: the completion oracle ─────────────────────────────────────


def state_dir() -> str:
    from lib.project_paths import temp_root
    return os.path.join(temp_root(), 'p2p')


def manifest_path(torrent_id: str) -> str:
    slug = re.sub(r'[^\w.-]+', '_', torrent_id)
    return os.path.join(state_dir(), f'{slug}.json')


def save_manifest(torrent_id: str, payload: dict) -> str:
    os.makedirs(state_dir(), exist_ok=True)
    path = manifest_path(torrent_id)
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return path


def load_manifests() -> list[dict]:
    directory = state_dir()
    if not os.path.isdir(directory):
        return []
    out = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith('.json'):
            continue
        try:
            with open(os.path.join(directory, name), encoding='utf-8') as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        payload['_manifest'] = os.path.join(directory, name)
        out.append(payload)
    return out


def locate_task(manifest: dict, *bases: str) -> str:
    """Find the task tree: ``<base>/<torrent name>``, else a base holding the files.

    迅雷's new-task panel remembers its own "last used" folder, which can differ
    from the configured default (``PathAndCategory.LastUsedPath``), so callers
    pass every candidate root and the first that really holds this release wins.
    """
    name = manifest.get('name') or ''
    files = manifest.get('files') or []
    for base in bases:
        if not base or not os.path.isdir(base):
            continue
        nested = os.path.join(base, name) if name else ''
        if nested and os.path.isdir(nested):
            return nested
        probe = files[:5] or [{'path': ''}]
        if any(os.path.isfile(os.path.join(base, *f['path'].split('/')))
               for f in probe if f['path']):
            return base
    return ''


def _file_size(path: str) -> int:
    """最终文件或 迅雷 的临时文件（``.bt.xltd`` / ``.xltd``）的字节数。"""
    if not path:
        return 0
    for candidate in (path, path + '.bt.xltd', path + '.xltd'):
        if os.path.isfile(candidate):
            return os.path.getsize(candidate)
    return 0


def task_progress(manifest: dict, root: str) -> dict:
    """Compare every file in the manifest against what is on disk.

    迅雷 把 BT 下载写成 ``<name>.mkv.bt.xltd`` 临时文件，下完才改名。所以进度要
    同时认临时文件，否则下载中永远显示 0%。
    """
    present = 0
    done = 0
    missing = []
    on_disk = 0
    entries = manifest.get('files') or []
    for entry in entries:
        path = os.path.join(root, *entry['path'].split('/')) if root else ''
        size = _file_size(path)
        if size:
            present += 1
        if size >= entry['length']:
            done += 1
        elif not size:
            missing.append(entry['path'])
        on_disk += min(size, entry['length'])
    total = sum(f['length'] for f in entries) or 1
    return {
        'root': root,
        'exists': bool(root) and os.path.isdir(root),
        'files_done': done,
        'files_total': len(entries),
        'bytes_done': on_disk,
        'bytes_total': total,
        'percent': round(on_disk * 100.0 / total, 2),
        'complete': done == len(entries) and bool(entries),
        'missing': missing[:5],
    }


# ── commands ─────────────────────────────────────────────────────────────


def cmd_search(args) -> int:
    proxy = '' if args.no_proxy else (args.proxy or system_proxy())
    sites = [s.strip() for s in args.site.split(',') if s.strip()] or list(DEFAULT_SITES)
    unknown = [s for s in sites if s not in SEARCHERS]
    if unknown:
        print(f'[p2p] unknown site(s): {", ".join(unknown)}; '
              f'known: {", ".join(SEARCHERS)}', file=sys.stderr)
        return 2
    rows = []
    for site in sites:
        try:
            found = SEARCHERS[site](args.query, args.limit, proxy)
        except Exception as error:                      # noqa: BLE001 - report, keep going
            print(f'[p2p] {site}: {type(error).__name__}: {error}', file=sys.stderr)
            continue
        rows.extend(found)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(rows, ensure_ascii=False, indent=2),
                                   encoding='utf-8')
        print(f'[p2p] {len(rows)} result(s) -> {args.json}')
    if not args.quiet or not args.json:
        for index, row in enumerate(rows, 1):
            ident = row.get('torrent_id') or row.get('site')
            print(f"{index:3} {row['site']:5} {row['size']:>9} "
                  f"{('S' + str(row['seeders'])) if row.get('seeders') else '   ':>5} "
                  f"{ident:15} {row['title'][:78]}")
        print(f'[p2p] {len(rows)} result(s)', file=sys.stderr)
    return 0 if rows else 4


def _resolve_torrent_bytes(args, proxy: str) -> tuple[bytes, str]:
    if args.torrent:
        if os.path.isfile(args.torrent):
            return Path(args.torrent).read_bytes(), os.path.basename(args.torrent)
        return http_get(args.torrent, proxy=proxy)[0], os.path.basename(args.torrent)
    if args.torrent_id:
        site, _, ident = args.torrent_id.partition(':')
        if site != 'nyaa':
            raise SystemExit('[p2p] --torrent-id supports nyaa:<id> only; '
                             'pass --magnet or --torrent for other sites')
        return http_get(f'https://nyaa.si/download/{ident}.torrent', proxy=proxy)[0], ident
    raise SystemExit('[p2p] grab needs --torrent-id, --torrent or --magnet')


def kf_service_dir() -> str:
    """kf-personal-service 的根目录：``KF_SERVICE_DIR`` > ``local.json paths.kf_service``。

    迅雷这类 GUI 的点击归 kf-personal-service（``scripts/ui/tasks.py``），
    本 skill 只调用、不复制脚本，所以这里只做「找它」这一件事。
    """
    for name in ('KF_SERVICE_DIR', 'kf_service_dir'):
        value = (os.environ.get(name, '') or '').strip()
        if value:
            return value
    return str(local_value('paths', 'kf_service', default='') or '').strip()


def kf_tasks_script() -> str:
    root = kf_service_dir()
    if not root:
        return ''
    script = os.path.join(root, 'scripts', 'ui', 'tasks.py')
    return script if os.path.isfile(script) else ''


def confirm_via_tasks(torrent: str = '', save_dir: str = '', magnet: str = '',
                      dry_run: bool = False, timeout: float = 300.0) -> int:
    """调 kf-service 的任务脚本确认迅雷面板。

    返回子进程退出码；找不到脚本返回 ``-1``（调用方据此退回「请人工确认」）。
    退出码约定沿用 tasks.py：0 成功、2 输入有问题、3 被守卫拦下。
    """
    script = kf_tasks_script()
    if not script:
        return -1
    command = [sys.executable, script, 'run', 'thunder-download']
    if torrent:
        command += ['--torrent', torrent]
    if magnet:
        command += ['--magnet', magnet]
    if save_dir:
        command += ['--save-dir', save_dir]
    if dry_run:
        command.append('--dry-run')
    try:
        completed = subprocess.run(command, capture_output=True, text=True,
                                   timeout=timeout)
    except (OSError, subprocess.SubprocessError) as error:
        print(f'[p2p] 任务脚本跑不起来：{error}', file=sys.stderr)
        return -2
    for line in ((completed.stdout or '') + (completed.stderr or '')).splitlines():
        if line.strip():
            print(f'[p2p] {line}')
    return completed.returncode


def should_confirm(args) -> bool:
    """``--confirm`` 显式开、``--no-confirm`` 显式关；默认「找到脚本就确认」。"""
    flag = getattr(args, 'confirm', None)
    if flag is True:
        return True
    if flag is False:
        return False
    return bool(kf_tasks_script())


def cmd_grab(args) -> int:
    proxy = '' if args.no_proxy else (args.proxy or system_proxy())
    exe = find_thunder(args.thunder_exe)
    download_dir = thunder_download_dir(exe, args.download_dir)

    blocked = remote_guard.block_reason(allow=getattr(args, 'allow_thunder', False))
    if blocked:
        if not args.dry_run:
            print(f'[p2p] {blocked}', file=sys.stderr)
            return 3
        print(f'[p2p] would refuse: {blocked}', file=sys.stderr)

    confirm = should_confirm(args)
    save_dir = getattr(args, 'save_dir', '')

    if args.magnet and not (args.torrent_id or args.torrent):
        if not download_dir:
            print('[p2p] 迅雷 download folder unknown; pass --download-dir', file=sys.stderr)
            return 2
        if args.dry_run:
            print(f"[p2p] would launch: {exe} \"{args.magnet[:60]}...\" -StartType:magnet")
            if confirm:
                print(f'[p2p] would run: tasks.py run thunder-download --magnet …'
                      f'{" --save-dir " + save_dir if save_dir else ""} --dry-run')
            return 0
        thunder_add(exe, args.magnet, 'magnet')
        print('[p2p] magnet handed to 迅雷 (no manifest: no file list without metadata)')
        _maybe_confirm(confirm, save_dir=save_dir, magnet=args.magnet)
        return 0

    raw, ident = _resolve_torrent_bytes(args, proxy)
    manifest = torrent_manifest(raw)
    torrent_id = args.torrent_id or f'file:{manifest["name"]}'
    os.makedirs(state_dir(), exist_ok=True)
    slug = re.sub(r'[^\w.-]+', '_', torrent_id)
    torrent_file = os.path.join(state_dir(), f'{slug}.torrent')
    Path(torrent_file).write_bytes(raw)

    print(f"[p2p] {manifest['name']}")
    print(f"[p2p] {len(manifest['files'])} file(s), {manifest['total'] / 2**30:.1f} GiB")
    if not download_dir:
        print('[p2p] 迅雷 download folder unknown; pass --download-dir', file=sys.stderr)
        return 2
    print(f'[p2p] client: {exe or "(none)"}  download dir: {download_dir}')
    if args.dry_run:
        print(f'[p2p] would launch: {exe} "{torrent_file}" -StartType:torrent')
        if confirm:
            print(f'[p2p] would run: tasks.py run thunder-download --torrent "{torrent_file}"'
                  f'{" --save-dir " + save_dir if save_dir else ""} --dry-run')
        return 0

    payload = dict(manifest)
    payload.update({'torrent_id': torrent_id, 'torrent_file': torrent_file,
                    'download_dir': download_dir, 'series': args.series,
                    'media_type': args.media_type, 'added_at': time.strftime('%Y-%m-%dT%H:%M:%S')})
    path = save_manifest(torrent_id, payload)
    thunder_add(exe, torrent_file, 'torrent')
    print(f'[p2p] task handed to 迅雷; manifest -> {path}')
    _maybe_confirm(confirm, torrent=torrent_file, save_dir=save_dir)
    print('[p2p] check with: p2p.py status')
    return 0


def _maybe_confirm(confirm: bool, torrent: str = '', save_dir: str = '',
                   magnet: str = '') -> None:
    """面板弹出后让 kf-service 的任务脚本点确认；拿不到脚本就明说，不假装成功。"""
    if not confirm:
        return
    if not kf_tasks_script():
        print('[p2p] kf-service 任务脚本不可用（设 KF_SERVICE_DIR 或 local.json '
              'paths.kf_service）；迅雷面板需要人工点「立即下载」', file=sys.stderr)
        return
    code = confirm_via_tasks(torrent=torrent, save_dir=save_dir, magnet=magnet)
    if code == 0:
        print('[p2p] 面板已由 kf-service 任务脚本确认')
    elif code == 3:
        print('[p2p] 任务脚本拒绝执行（远程协作/缺锚点）；请人工确认，'
              '远程会话结束后再试', file=sys.stderr)
    else:
        print(f'[p2p] 任务脚本退出 {code}；看上面的输出，必要时人工接管', file=sys.stderr)


def _candidate_roots(manifest: dict, args, exe: str) -> list[str]:
    """Every folder a task can plausibly be in on this machine."""
    return [args.download_dir, manifest.get('download_dir', ''),
            thunder_download_dir(exe), thunder_last_path(exe)]


def cmd_status(args) -> int:
    manifests = load_manifests()
    if not manifests:
        print(f'[p2p] no manifests in {state_dir()}')
        return 0
    exe = find_thunder(args.thunder_exe)
    payload = []
    for manifest in manifests:
        root = locate_task(manifest, *_candidate_roots(manifest, args, exe))
        progress = task_progress(manifest, root)
        payload.append({'name': manifest['name'], 'torrent_id': manifest.get('torrent_id', ''),
                        **progress})
        flag = 'complete' if progress['complete'] else ('not found' if not root else 'downloading')
        print(f"{flag:11} {progress['percent']:6.2f}% "
              f"{progress['files_done']}/{progress['files_total']} files  "
              f"{progress['bytes_done'] / 2**30:6.2f}/"
              f"{progress['bytes_total'] / 2**30:6.2f} GiB  {manifest['name'][:60]}")
        if root:
            print(f'             at {root}')
        if progress['missing']:
            print(f"             missing: {', '.join(progress['missing'][:2])}")
    if args.json:
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                   encoding='utf-8')
        print(f'[p2p] status -> {args.json}')
    return 0


def cmd_collect(args) -> int:
    from lib.media_paths import series_dir
    target = series_dir(args.series, args.media_type, args.category)
    if not target:
        print('[p2p] cannot resolve the library folder; check local.json paths.media',
              file=sys.stderr)
        return 2
    manifests = [m for m in load_manifests()
                 if not args.torrent_id or m.get('torrent_id') == args.torrent_id]
    if not manifests:
        print('[p2p] no manifest matched', file=sys.stderr)
        return 2
    exe = find_thunder(args.thunder_exe)
    moved = 0
    for manifest in manifests:
        root = locate_task(manifest, *_candidate_roots(manifest, args, exe))
        progress = task_progress(manifest, root)
        if not progress['complete'] and not args.partial:
            print(f"[p2p] skip {manifest['name']}: {progress['percent']:.1f}% "
                  f"({progress['files_done']}/{progress['files_total']})", file=sys.stderr)
            continue
        if not root:
            print(f'[p2p] skip {manifest["name"]}: task folder not found', file=sys.stderr)
            continue
        print(f'[p2p] {manifest["name"]} -> {target}')
        for entry in manifest.get('files') or []:
            src = os.path.join(root, *entry['path'].split('/'))
            if not os.path.isfile(src):
                continue
            # A torrent with a single top folder already names the release; the
            # library folder is the series, so drop that one level.
            parts = entry['path'].split('/')
            if len(parts) > 1:
                parts = parts[1:]
            dst = os.path.join(target, *parts)
            if args.dry_run:
                print(f'    {entry["path"]} -> {dst}')
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.exists(dst) and not args.overwrite:
                continue
            shutil.move(src, dst)
            moved += 1
        if not args.dry_run:
            manifest['collected_to'] = target
            save_manifest(manifest.get('torrent_id') or manifest['name'], manifest)
    if args.dry_run:
        print('[p2p] dry-run: nothing moved')
    else:
        print(f'[p2p] moved {moved} file(s) into {target}')
    return 0


def cmd_clients(args) -> int:
    proxy = system_proxy()
    exe = find_thunder(args.thunder_exe)
    download_dir = thunder_download_dir(exe, args.download_dir)
    report = {
        'proxy': proxy,
        'xunlei': {
            'exe': exe,
            'version': _file_version(exe),
            'download_dir': download_dir,
            'profile': thunder_profile_dir(exe),
        },
        'qbittorrent': _port_open(8080),
        'aria2': _port_open(6800),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not exe:
        print('[p2p] 迅雷 not detected; install it or pass --thunder-exe', file=sys.stderr)
    return 0


def _file_version(path: str) -> str:
    if not path or not os.path.isfile(path) or sys.platform != 'win32':
        return ''
    try:
        output = subprocess.run(
            ['powershell', '-NoProfile', '-Command',
             f"(Get-Item -LiteralPath '{path}').VersionInfo.FileVersion"],
            capture_output=True, text=True, timeout=25).stdout.strip()
        return output
    except (OSError, subprocess.SubprocessError):
        return ''


def _port_open(port: int, host: str = '127.0.0.1') -> bool:
    import socket
    with socket.socket() as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((host, port)) == 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description='Search torrent indexers, hand releases to 迅雷, collect into the library.')
    sub = parser.add_subparsers(dest='command', required=True)

    def common(node):
        node.add_argument('--proxy', default='', help='explicit proxy URL')
        node.add_argument('--no-proxy', action='store_true', help='ignore system proxy')
        node.add_argument('--thunder-exe', default='', help='override 迅雷 executable')
        node.add_argument('--download-dir', default='', help='override 迅雷 download folder')

    search = sub.add_parser('search', help='search indexers')
    search.add_argument('-q', '--query', required=True)
    search.add_argument('--site', default=','.join(DEFAULT_SITES))
    search.add_argument('--limit', type=int, default=8)
    search.add_argument('--json', default='')
    search.add_argument('--quiet', action='store_true')
    common(search)

    grab = sub.add_parser('grab', help='add a torrent/magnet to the client')
    grab.add_argument('--torrent-id', default='', help='nyaa:<id>')
    grab.add_argument('--torrent', default='', help='local .torrent path or URL')
    grab.add_argument('--magnet', default='')
    grab.add_argument('--series', default='')
    grab.add_argument('--media-type', default='tv', choices=['tv', 'ova', 'movie'])
    grab.add_argument('--dry-run', action='store_true')
    grab.add_argument('--save-dir', default='',
                      help='落点目录（绝对路径）：交给 kf-service 任务脚本改「下载到」')
    grab.add_argument('--confirm', dest='confirm', action='store_true', default=None,
                      help='派单后用 kf-service 任务脚本点「立即下载」（默认：找到脚本就做）')
    grab.add_argument('--no-confirm', dest='confirm', action='store_false',
                      help='只派单，不自动确认面板')
    grab.add_argument('--allow-thunder', action='store_true',
                      help='远程协作进行中仍要派单（需用户明确同意）')
    common(grab)

    status = sub.add_parser('status', help='compare manifests with the disk')
    status.add_argument('--json', default='')
    common(status)

    collect = sub.add_parser('collect', help='move finished tasks into the library')
    collect.add_argument('--series', required=True)
    collect.add_argument('--media-type', default='tv', choices=['tv', 'ova', 'movie'])
    collect.add_argument('--category', default='animation')
    collect.add_argument('--torrent-id', default='')
    collect.add_argument('--partial', action='store_true')
    collect.add_argument('--overwrite', action='store_true')
    collect.add_argument('--dry-run', action='store_true')
    common(collect)

    clients = sub.add_parser('clients', help='detect clients and their folders')
    common(clients)

    args = parser.parse_args(argv)
    handlers = {'search': cmd_search, 'grab': cmd_grab, 'status': cmd_status,
                'collect': cmd_collect, 'clients': cmd_clients}
    return handlers[args.command](args)


if __name__ == '__main__':
    sys.exit(main())
