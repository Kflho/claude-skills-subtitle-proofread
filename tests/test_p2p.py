"""P2P 获取：bencode 解析、清单（manifest）比对、落点定位。

这一层不碰网络也不碰迅雷，但它是整套「下完没有」判定的地基：
- 客户端说 100% 不算数，只有逐文件比对 .torrent 里的 length 才算；
- 迅雷面板的落点可能和默认下载目录不同，所以定位必须能在多个根下找。
"""

import pytest

from acquire.p2p import (bdecode, locate_task, task_progress, torrent_manifest,
                         _size_to_bytes)


def bencode(value):
    """Minimal encoder used only by the tests to build torrent bytes."""
    if isinstance(value, int):
        return b'i%de' % value
    if isinstance(value, str):
        value = value.encode('utf-8')
    if isinstance(value, bytes):
        return b'%d:%s' % (len(value), value)
    if isinstance(value, list):
        return b'l' + b''.join(bencode(item) for item in value) + b'e'
    if isinstance(value, dict):
        items = sorted((k.encode() if isinstance(k, str) else k, v)
                       for k, v in value.items())
        return b'd' + b''.join(bencode(k) + bencode(v) for k, v in items) + b'e'
    raise TypeError(type(value))


def make_torrent(name='[Group] Show (1981) [BD]', files=None, announce='http://x/announce'):
    info = {'name': name, 'piece length': 16384, 'pieces': b'0' * 20}
    if files is None:
        info['length'] = 100
    else:
        info['files'] = [{'length': size, 'path': path.split('/')}
                         for path, size in files]
    return bencode({'announce': announce, 'info': info})


# ── bencode / manifest ──────────────────────────────────────────────────


def test_bdecode_handles_nested_types():
    raw = bencode({'a': [1, b'xy', {'b': 2}]})
    assert bdecode(raw) == {b'a': [1, b'xy', {b'b': 2}]}


def test_manifest_multi_file_sums_and_keeps_paths():
    raw = make_torrent(files=[('01.mkv', 1000), ('EXTRA/bonus.mkv', 500)])
    manifest = torrent_manifest(raw)
    assert manifest['name'] == '[Group] Show (1981) [BD]'
    assert manifest['total'] == 1500
    assert [f['path'] for f in manifest['files']] == ['01.mkv', 'EXTRA/bonus.mkv']
    assert manifest['announce'] == 'http://x/announce'


def test_manifest_single_file_torrent():
    manifest = torrent_manifest(make_torrent(name='movie.mkv'))
    assert manifest['total'] == 100
    assert manifest['files'] == [{'path': 'movie.mkv', 'length': 100, 'single': True}]


@pytest.mark.parametrize('text,expected', [
    ('24.46GB', 26_263_725_015),      # nyaa 页面用二进制单位显示
    ('420.99MB', 441_440_010),
    ('1.5GiB', 1_610_612_736),
    ('', 0),
])
def test_size_to_bytes(text, expected):
    assert _size_to_bytes(text) == expected


# ── progress: the completion oracle ─────────────────────────────────────


def _write(path, size):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'\0' * size)


def test_progress_reports_complete_only_when_every_file_matches(tmp_path):
    manifest = {'name': 'release', 'total': 1500, 'files': [
        {'path': '01.mkv', 'length': 1000},
        {'path': 'EXTRA/bonus.mkv', 'length': 500},
    ]}
    root = tmp_path / 'release'
    _write(root / '01.mkv', 1000)
    _write(root / 'EXTRA' / 'bonus.mkv', 500)
    progress = task_progress(manifest, str(root))
    assert progress['complete'] is True
    assert progress['percent'] == 100.0
    assert progress['files_done'] == 2


def test_progress_counts_short_file_as_incomplete(tmp_path):
    manifest = {'name': 'release', 'total': 1500, 'files': [
        {'path': '01.mkv', 'length': 1000},
        {'path': '02.mkv', 'length': 500},
    ]}
    root = tmp_path / 'release'
    _write(root / '01.mkv', 1000)
    _write(root / '02.mkv', 100)          # 正在下的半截文件
    progress = task_progress(manifest, str(root))
    assert progress['complete'] is False
    assert progress['files_done'] == 1
    assert progress['bytes_done'] == 1100
    assert progress['percent'] == pytest.approx(73.33, abs=0.01)


def test_progress_missing_root_is_zero_not_complete():
    manifest = {'name': 'release', 'total': 10, 'files': [{'path': 'a.mkv', 'length': 10}]}
    progress = task_progress(manifest, '')
    assert progress['exists'] is False
    assert progress['complete'] is False
    assert progress['percent'] == 0.0


# ── locate: 迅雷 panel folder vs configured default ─────────────────────


def test_locate_prefers_nested_release_folder(tmp_path):
    manifest = {'name': 'release', 'files': [{'path': '01.mkv', 'length': 1}]}
    base = tmp_path / 'download'
    _write(base / 'release' / '01.mkv', 1)
    assert locate_task(manifest, str(base)) == str(base / 'release')


def test_locate_accepts_flat_folder_that_holds_the_files(tmp_path):
    manifest = {'name': 'release', 'files': [{'path': '01.mkv', 'length': 1}]}
    flat = tmp_path / 'tv'
    _write(flat / '01.mkv', 1)
    assert locate_task(manifest, str(flat)) == str(flat)


def test_locate_returns_empty_when_nothing_matches(tmp_path):
    manifest = {'name': 'release', 'files': [{'path': '01.mkv', 'length': 1}]}
    assert locate_task(manifest, str(tmp_path / 'empty')) == ''


def test_locate_skips_first_missing_root(tmp_path):
    manifest = {'name': 'release', 'files': [{'path': '01.mkv', 'length': 1}]}
    good = tmp_path / 'last-used'
    _write(good / 'release' / '01.mkv', 1)
    assert locate_task(manifest, str(tmp_path / 'nope'), str(good)) == str(good / 'release')
