"""字幕调研（段 0）：站点清单、未实测站点不拼 URL、jimaku 建议接口、清单落盘。

这条测试锁两个约定：
1. 没实测过的站点不许给编造的检索端点；
2. jimaku 的免 key 接口只回「条目建议」，解析错位会让人以为拿到了文件。
"""

import json

import pytest

from acquire import subs_search

SUGGEST = json.dumps(
    ["frieren", ["729: Sousou no Frieren", "11446: Sousou no Frieren 2nd Season"],
     [], []]).encode('utf-8')


@pytest.fixture
def fake_http(monkeypatch):
    """把 http_get 换成回放，同时记录请求 URL。"""
    seen = []

    def http_get(url, *, proxy='', timeout=30.0, encoding=''):
        seen.append(url)
        return SUGGEST, SUGGEST.decode('utf-8')

    monkeypatch.setattr(subs_search, 'http_get', http_get)
    return seen


# ── 站点清单与「不许编端点」 ────────────────────────────────────────────


def test_plan_covers_all_four_sources():
    payload = subs_search.plan('赤き血のイレブン')
    keys = [row['key'] for row in payload['sites']]
    assert keys == ['jimaku', 'kitsunekko', 'assrt', 'opensubtitles']


def test_unverified_sites_have_no_guessed_endpoint():
    for site in subs_search.SITES:
        if subs_search.is_automated(site):
            continue
        assert site['url_template'] == '', f"{site['key']} 未实测却写了端点"
        with pytest.raises(ValueError):
            subs_search.search_url(site, 'x')


def test_kitsunekko_stays_manual_because_robots_disallows():
    """无搜索 + robots 全禁：不给适配器，但要把人工入口写清楚。"""
    site = subs_search.site_by_key('kitsunekko')
    assert not subs_search.is_automated(site)
    assert 'dirlist.php' in site['manual']
    assert 'robots' in site['manual'].lower()


def test_plan_marks_every_unverified_site_as_manual():
    payload = subs_search.plan('作品名')
    for row in payload['sites']:
        if row['automated']:
            continue
        assert row['method'].startswith('人工')
        assert row['url'] == ''


def test_jimaku_is_the_verified_half_automated_site():
    site = subs_search.site_by_key('jimaku')
    assert subs_search.is_automated(site)
    assert subs_search.search_url(site, 'frieren').endswith('query=frieren')


# ── jimaku 建议接口 ─────────────────────────────────────────────────────


def test_jimaku_adapter_parses_entry_suggestions(fake_http):
    rows = subs_search.search_jimaku('frieren', 5)
    assert [row['entry_id'] for row in rows] == ['729', '11446']
    assert rows[0]['title'] == 'Sousou no Frieren'
    assert rows[0]['url'] == 'https://jimaku.cc/entry/729'
    assert 'key' in rows[0]['note'], '提示里要说清文件清单需要 key'
    assert fake_http[0].startswith('https://jimaku.cc/opensearch/suggest')


def test_jimaku_adapter_url_encodes_the_query(fake_http):
    subs_search.search_jimaku('赤き血のイレブン', 5)
    assert '%E8%B5%A4' in fake_http[0]


def test_jimaku_adapter_respects_limit(fake_http):
    assert len(subs_search.search_jimaku('frieren', 1)) == 1


def test_jimaku_empty_suggestions_is_not_an_error(fake_http, monkeypatch):
    empty = json.dumps(['赤き血', [], [], []]).encode('utf-8')
    monkeypatch.setattr(subs_search, 'http_get', lambda *a, **kw: (empty, empty.decode()))
    assert subs_search.search_jimaku('赤き血', 5) == []


# ── search 命令 ────────────────────────────────────────────────────────


def test_search_reports_manual_sites_and_exit_code(tmp_path, capsys, fake_http):
    js = tmp_path / 'candidates.json'
    code = subs_search.main(['search', '-q', 'frieren', '--json', str(js), '--no-proxy'])
    assert code == 0
    rows = json.loads(js.read_text(encoding='utf-8'))
    assert len(rows) == 2
    assert rows[0]['site'] == 'jimaku'


def test_search_returns_4_when_nothing_found(fake_http):
    empty = json.dumps(['zzz', [], [], []]).encode('utf-8')
    import acquire.subs_search as module
    module.http_get = lambda *a, **kw: (empty, empty.decode())
    assert module.main(['search', '-q', 'zzz', '--no-proxy']) == 4


def test_search_keeps_going_when_an_adapter_raises(monkeypatch):
    def boom(query, limit, proxy=''):
        raise RuntimeError('network down')

    monkeypatch.setattr(subs_search, 'site_by_key',
                        lambda key: {'key': key, 'name': key, 'langs': 'ja',
                                     'note': '', 'url_template': 'x{query}',
                                     'adapter': boom, 'manual': ''})
    assert subs_search.main(['search', '-q', 'x', '--no-proxy']) == 4


# ── 清单渲染与落盘 ──────────────────────────────────────────────────────


def test_markdown_has_a_result_column_and_conclusion_fields():
    text = subs_search.render_markdown(subs_search.plan('作品名'))
    assert '结果（有/无、语种、版本、落点）' in text
    assert '## 结论' in text
    assert '## 人工检索怎么做' in text
    for site in subs_search.SITES:
        assert site['name'] in text


def test_plan_command_writes_markdown_and_json(tmp_path):
    out = tmp_path / 'docs' / '字幕调研.md'
    js = tmp_path / 'temp' / 'plan.json'
    code = subs_search.main(['plan', '-q', '作品名', '--out', str(out), '--json', str(js)])
    assert code == 0
    assert '作品名' in out.read_text(encoding='utf-8')
    payload = json.loads(js.read_text(encoding='utf-8'))
    assert payload['query'] == '作品名'
    assert len(payload['sites']) == len(subs_search.SITES)


def test_sites_command_lists_everything(capsys):
    assert subs_search.main(['sites']) == 0
    printed = capsys.readouterr().out
    for site in subs_search.SITES:
        assert site['key'] in printed
