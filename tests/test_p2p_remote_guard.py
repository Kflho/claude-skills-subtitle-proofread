"""p2p grab 与远程协作守卫的接线。

守卫本身在 test_remote_guard.py 里测；这里只验证**接线**：远程协作开着时
``grab`` 必须在把任务交给迅雷之前退出，而不是先开任务再报错。
"""

import pytest

from acquire import p2p


class FakeArgs:
    def __init__(self, **overrides):
        self.no_proxy = True
        self.proxy = ''
        self.thunder_exe = ''
        self.download_dir = ''
        self.series = 'demo'
        self.media_type = 'tv'
        self.dry_run = False
        self.allow_thunder = False
        self.torrent = ''
        self.torrent_id = ''
        self.magnet = ''
        self.__dict__.update(overrides)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """假迅雷：记录有没有真的被派单。"""
    calls = []

    monkeypatch.setattr(p2p, 'find_thunder', lambda override='': 'thunder.exe')
    monkeypatch.setattr(p2p, 'thunder_download_dir',
                        lambda exe, override='': str(tmp_path / 'dl'))
    monkeypatch.setattr(p2p, 'thunder_add',
                        lambda exe, target, kind='torrent', **kw: calls.append(target))
    return calls


def fake_guard(after_allow=''):
    """按 allow 参数决定拦不拦。"""
    def block_reason(allow=False, processes=None):
        return after_allow if allow else '远程协作进行中，禁止开启迅雷等占用型软件'
    return block_reason


def test_grab_refuses_before_launching_client(tmp_path, monkeypatch, client):
    monkeypatch.setattr(p2p.remote_guard, 'block_reason', fake_guard())
    args = FakeArgs(magnet='magnet:?xt=urn:btih:deadbeef')
    assert p2p.cmd_grab(args) == 3
    assert client == [], '远程协作期间不能真的把任务交给迅雷'


def test_grab_dry_run_reports_refusal_but_exits_zero(tmp_path, monkeypatch, client):
    monkeypatch.setattr(p2p.remote_guard, 'block_reason', fake_guard())
    args = FakeArgs(magnet='magnet:?xt=urn:btih:deadbeef', dry_run=True)
    assert p2p.cmd_grab(args) == 0
    assert client == []


def test_allow_thunder_is_passed_to_the_guard(tmp_path, monkeypatch, client):
    """--allow-thunder 必须把 allow=True 传给守卫，而不是自己跳过检查。"""
    seen = []

    def guard(allow=False, processes=None):
        seen.append(allow)
        return ''

    monkeypatch.setattr(p2p.remote_guard, 'block_reason', guard)
    args = FakeArgs(magnet='magnet:?xt=urn:btih:deadbeef', allow_thunder=True)
    assert p2p.cmd_grab(args) == 0
    assert seen == [True]
    assert client, '放行后应当真的派单'
