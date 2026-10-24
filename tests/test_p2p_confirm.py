"""p2p → kf-service 任务脚本的接线。

派单之后那一下「立即下载」属于 GUI 点击，归 kf-personal-service 的 tasks.py；
这里只验证接线：找到没有、参数对不对、什么时候不该调。
"""

import subprocess

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
        self.confirm = None
        self.save_dir = ''
        self.torrent = ''
        self.torrent_id = ''
        self.magnet = ''
        self.__dict__.update(overrides)


MAGNET = 'magnet:?xt=urn:btih:' + 'a' * 40


@pytest.fixture
def client(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(p2p, 'find_thunder', lambda override='': 'thunder.exe')
    monkeypatch.setattr(p2p, 'thunder_download_dir',
                        lambda exe, override='': str(tmp_path / 'dl'))
    monkeypatch.setattr(p2p, 'thunder_add',
                        lambda exe, target, kind='torrent', **kw: calls.append(('add', kind)))
    monkeypatch.setattr(p2p.remote_guard, 'block_reason',
                        lambda allow=False, processes=None: '')
    monkeypatch.setattr(p2p, '_maybe_confirm',
                        lambda confirm, **kw: calls.append(('confirm', confirm, kw))
                        if confirm else None)
    return calls


def test_kf_service_dir_prefers_the_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv('KF_SERVICE_DIR', str(tmp_path / 'svc'))
    assert p2p.kf_service_dir() == str(tmp_path / 'svc')


def test_kf_service_dir_falls_back_to_local_json(monkeypatch):
    monkeypatch.delenv('KF_SERVICE_DIR', raising=False)
    monkeypatch.delenv('kf_service_dir', raising=False)
    monkeypatch.setattr(p2p, 'local_value',
                        lambda *a, **kw: '/somewhere/kf-personal-service')
    assert p2p.kf_service_dir() == '/somewhere/kf-personal-service'


def test_kf_tasks_script_requires_the_file(monkeypatch, tmp_path):
    service = tmp_path / 'svc'
    (service / 'scripts' / 'ui').mkdir(parents=True)
    monkeypatch.setenv('KF_SERVICE_DIR', str(service))
    assert p2p.kf_tasks_script() == ''
    (service / 'scripts' / 'ui' / 'tasks.py').write_text('# stub', encoding='utf-8')
    assert p2p.kf_tasks_script().endswith('tasks.py')


def test_confirm_without_script_reports_minus_one(monkeypatch, tmp_path):
    monkeypatch.setenv('KF_SERVICE_DIR', str(tmp_path / 'missing'))
    assert p2p.confirm_via_tasks(torrent='x.torrent') == -1


def test_confirm_builds_the_task_command(monkeypatch, tmp_path):
    service = tmp_path / 'svc'
    script = service / 'scripts' / 'ui' / 'tasks.py'
    script.parent.mkdir(parents=True)
    script.write_text('# stub', encoding='utf-8')
    monkeypatch.setenv('KF_SERVICE_DIR', str(service))

    seen = {}

    def fake_run(command, **kwargs):
        seen['command'] = command
        return subprocess.CompletedProcess(command, 0, 'ok', '')

    monkeypatch.setattr(p2p.subprocess, 'run', fake_run)
    code = p2p.confirm_via_tasks(torrent='D:\\x\\EP01.torrent',
                                 save_dir='D:\\videos\\TV', magnet=MAGNET)
    assert code == 0
    command = seen['command']
    assert command[1].endswith('tasks.py')
    assert command[2:] == ['run', 'thunder-download',
                           '--torrent', 'D:\\x\\EP01.torrent',
                           '--magnet', MAGNET,
                           '--save-dir', 'D:\\videos\\TV']


def test_should_confirm_defaults_to_available_script(monkeypatch, tmp_path):
    monkeypatch.setenv('KF_SERVICE_DIR', str(tmp_path / 'none'))
    assert p2p.should_confirm(FakeArgs()) is False

    service = tmp_path / 'svc'
    script = service / 'scripts' / 'ui' / 'tasks.py'
    script.parent.mkdir(parents=True)
    script.write_text('# stub', encoding='utf-8')
    monkeypatch.setenv('KF_SERVICE_DIR', str(service))
    assert p2p.should_confirm(FakeArgs()) is True
    assert p2p.should_confirm(FakeArgs(confirm=True)) is True
    assert p2p.should_confirm(FakeArgs(confirm=False)) is False


def test_grab_confirms_after_handing_the_magnet(monkeypatch, client):
    assert p2p.cmd_grab(FakeArgs(magnet=MAGNET, confirm=True)) == 0
    assert [call[0] for call in client] == ['add', 'confirm'], '先派单，再确认'
    assert client[1][1] is True
    assert client[1][2]['magnet'] == MAGNET


def test_grab_no_confirm_skips_the_task_script(monkeypatch, client):
    assert p2p.cmd_grab(FakeArgs(magnet=MAGNET, confirm=False)) == 0
    assert [call[0] for call in client] == ['add']


def test_grab_dry_run_never_touches_the_client(monkeypatch, client):
    assert p2p.cmd_grab(FakeArgs(magnet=MAGNET, dry_run=True, confirm=True)) == 0
    assert client == []


def test_maybe_confirm_explains_when_the_script_is_missing(monkeypatch, capsys, tmp_path):
    """找不到 kf-service 就明说「需要人工点」，不能假装自动确认成功。"""
    monkeypatch.setenv('KF_SERVICE_DIR', str(tmp_path / 'none'))
    p2p._maybe_confirm(True, magnet=MAGNET)
    err = capsys.readouterr().err
    assert 'kf-service' in err
    assert '人工' in err
