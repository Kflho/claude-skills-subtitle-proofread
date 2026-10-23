"""字幕调研（段 0）：站点清单、未实测站点不拼 URL、清单可落盘。

这条测试真正锁的是一个**约定**：没实测过的站点不许给编造的检索端点。
以后接适配器时，先把 ``url_template`` 和 ``adapter`` 一起填上，再改这里的断言。
"""

import json

import pytest

from acquire import subs_search


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


def test_plan_marks_every_unverified_site_as_manual():
    payload = subs_search.plan('作品名')
    for row in payload['sites']:
        if row['automated']:
            continue
        assert row['method'].startswith('人工')
        assert row['url'] == ''


def test_verified_site_gets_a_real_url(monkeypatch):
    """接上实测端点后：URL 要按模板拼，query 要转义。"""
    fake = {'key': 'demo', 'name': 'demo', 'langs': 'ja', 'note': '',
            'url_template': 'https://example.invalid/search?q={query}',
            'adapter': lambda query, limit, proxy: []}
    assert subs_search.is_automated(fake)
    assert subs_search.search_url(fake, '赤き血のイレブン') == \
        'https://example.invalid/search?q=%E8%B5%A4%E3%81%8D%E8%A1%80%E3%81%AE%E3%82%A4%E3%83%AC%E3%83%96%E3%83%B3'


def test_markdown_has_a_result_column_and_conclusion_fields():
    text = subs_search.render_markdown(subs_search.plan('作品名'))
    assert '结果（有/无、语种、版本、落点）' in text
    assert '## 结论' in text
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
