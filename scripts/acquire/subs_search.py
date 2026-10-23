#!/usr/bin/env python3
"""字幕调研：段 0 的可执行入口 —— 先问「有没有现成字幕」，再决定后面走哪条路。

Why this exists
---------------
查字幕是整条生产线里**最容易被跳过**的一步：跳过的代价是白跑一遍 ASR，或者明明有
日文字幕却从零转写。所以把它做成一个命令，产出落盘的清单：

- 四个站点（jimaku / kitsunekko / assrt / opensubtitles）各自的检索入口、语种、
  自动化程度；
- 每站的结论栏（有/无、语种、版本、文件落点）留空给人填；
- **只有实测过的端点才写进适配器**。没实测的站点只写「人工检索」，绝不拼 URL：
  错的端点比没有端点更费时间。

当前实测状态（2026-10，结论与证据见 ``references/research.md``）：

- jimaku.cc：有免 key 的 OpenSearch 建议接口，能探到作品条目（没有文件名/下载链接，
  那些要账号 key）；条目页 HTML 免登录可见下载链接 → **半自动**；
- kitsunekko.net：无 API、无站内搜索，且 robots.txt 全站 ``Disallow: /`` → **人工**；
- assrt.net：本机不通，接口文档取自存档 → **人工（需 token 人工申请）**；
- opensubtitles：REST 无 key 一律 403 → **人工（需 key）**。

    python scripts/acquire/subs_search.py sites
    python scripts/acquire/subs_search.py plan -q "赤き血のイレブン" \\
        --out bin/reference_docs/字幕调研.md --json data/temp/subs_search.json
    python scripts/acquire/subs_search.py search -q "Frieren" --limit 5
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from acquire.p2p import http_get, system_proxy  # noqa: E402

JIMAKU_SUGGEST = 'https://jimaku.cc/opensearch/suggest?anime=true&query={query}'
KITSUNEKKO_LIST = 'https://kitsunekko.net/dirlist.php?dir=subtitles%2Fjapanese%2F'


# ── adapters：只放实测过的端点 ──────────────────────────────────────────


def search_jimaku(query: str, limit: int, proxy: str = '') -> list[dict]:
    """jimaku 的 OpenSearch 建议接口：免 key，返回 ``"<id>: <罗马字名>"``。

    拿得到的是**作品条目**，不是文件：文件名/语言/下载链接要账号 key 才有。
    条目页 ``/entry/<id>`` 免登录能看到下载链接，所以下一步是人工点进去。
    """
    url = JIMAKU_SUGGEST.format(query=quote(query))
    _, text = http_get(url, proxy=proxy)
    payload = json.loads(text)
    names = payload[1] if isinstance(payload, list) and len(payload) > 1 else []
    results = []
    for raw in names[:limit]:
        ident, _, title = str(raw).partition(':')
        ident, title = ident.strip(), title.strip()
        results.append({
            'site': 'jimaku',
            'title': title or str(raw).strip(),
            'langs': 'ja',
            'entry_id': ident,
            'url': f'https://jimaku.cc/entry/{ident}' if ident.isdigit() else '',
            'note': '条目页免登录可见下载链接；文件清单需账号 key',
        })
    return results


def _manual_only(query: str, limit: int, proxy: str = '') -> list[dict]:
    raise NotImplementedError('该站点只有人工检索入口')


# ── 站点清单：顺序即建议检索顺序 ────────────────────────────────────────

SITES = [
    {
        'key': 'jimaku',
        'name': 'jimaku.cc',
        'langs': 'ja',
        'note': '日文字幕，动漫为主；可直接当源语言',
        'url_template': JIMAKU_SUGGEST,
        'adapter': search_jimaku,
        'manual': '条目页免登录能看下载链接；文件清单与批量下载要账号 key（/account 生成）',
    },
    {
        'key': 'kitsunekko',
        'name': 'kitsunekko.net',
        'langs': 'ja',
        'note': '日文字幕，老番覆盖好',
        'url_template': '',
        'adapter': None,
        'manual': f'无站内搜索，只有一个全量目录页：{KITSUNEKKO_LIST}；'
                  f'robots.txt 是 Disallow: /，脚本不去抓，人工在浏览器里找',
    },
    {
        'key': 'assrt',
        'name': 'assrt.net（射手网）',
        'langs': 'zh',
        'note': '中文字幕为主；只作译名/术语对照，不当源',
        'url_template': '',
        'adapter': None,
        'manual': '本机实测整站不通；接口 api.assrt.net/v1/sub/search?token=&q= 需账号 token'
                  '（20 次/分），先打通网络再用',
    },
    {
        'key': 'opensubtitles',
        'name': 'opensubtitles.org / .com',
        'langs': 'multi',
        'note': '多语言、量大；.com 的 REST API 需要 key',
        'url_template': '',
        'adapter': None,
        'manual': 'REST 无 key 一律 403；老站 XML-RPC 可用但需先 LogIn 换 token',
    },
]


def site_by_key(key: str) -> dict:
    for site in SITES:
        if site['key'] == key:
            return site
    raise KeyError(key)


def is_automated(site: dict) -> bool:
    """只有「实测过的模板 + 适配器」才算能自动检索。"""
    return bool(site.get('url_template')) and callable(site.get('adapter'))


def search_url(site: dict, query: str) -> str:
    """检索 URL；未实测的站点直接拒绝，绝不猜。"""
    template = site.get('url_template') or ''
    if not template:
        raise ValueError(f"{site['name']} 的检索端点未实测：不要拼 URL，改用人工检索")
    return template.format(query=quote(query))


def plan(query: str) -> dict:
    """一份可直接落盘的调研清单（结构见 ``render_markdown``）。"""
    rows = []
    for site in SITES:
        automated = is_automated(site)
        rows.append({
            'key': site['key'],
            'name': site['name'],
            'langs': site['langs'],
            'note': site['note'],
            'automated': automated,
            'url': search_url(site, query) if automated else '',
            'method': '脚本检索' if automated else '人工检索（端点未实测/robots 不允许）',
            'manual': site.get('manual', ''),
            'result': '',      # 有 / 无 / 语种 / 版本 —— 人填
        })
    return {'query': query, 'sites': rows}


def render_markdown(payload: dict) -> str:
    lines = [f"# 字幕调研：{payload['query']}", '',
             '> 段 0 产物。每站的「结果」栏必须填：有/无、语种、版本（对不上就写对不上）、'
             '文件落点或链接。查不到也是结论，写「无」。', '',
             '| 站点 | 语种 | 检索方式 | 检索入口 | 结果（有/无、语种、版本、落点） |',
             '|---|---|---|---|---|']
    for row in payload['sites']:
        entry = row['url'] or '—'
        lines.append(f"| {row['name']} | {row['langs']} | {row['method']} | {entry} | {row['result']} |")
    notes = [row for row in payload['sites'] if row.get('manual')]
    if notes:
        lines += ['', '## 人工检索怎么做', '']
        for row in notes:
            lines.append(f"- **{row['name']}**：{row['manual']}")
    lines += ['', '## 结论', '',
              '- 可用的源字幕：',
              '- 可用的对照字幕（不当中文源的译本）：',
              '- 缺口（哪些站没查/查不到、用什么替代）：', '']
    return '\n'.join(lines)


def cmd_sites(args) -> int:
    for site in SITES:
        state = '自动' if is_automated(site) else '人工'
        print(f"{site['key']:14} {site['name']:26} {site['langs']:6} {state}  {site['note']}")
    return 0


def cmd_plan(args) -> int:
    payload = plan(args.query)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                   encoding='utf-8')
        print(f'[subs] plan -> {args.json}')
    markdown = render_markdown(payload)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(markdown, encoding='utf-8')
        print(f'[subs] checklist -> {args.out}')
    else:
        print(markdown)
    return 0


def cmd_search(args) -> int:
    """跑所有能自动检索的站点；都不行时给人工入口，别静默成功。"""
    proxy = '' if args.no_proxy else (args.proxy or system_proxy())
    sites = [s.strip() for s in args.site.split(',') if s.strip()] or \
        [s['key'] for s in SITES if is_automated(s)]
    rows, errors = [], []
    for key in sites:
        site = site_by_key(key)
        if not is_automated(site):
            errors.append(f"{site['name']}: 只能人工检索（{site.get('manual', '')}）")
            continue
        try:
            rows.extend(site['adapter'](args.query, args.limit, proxy))
        except Exception as error:                      # noqa: BLE001 - 报错继续
            errors.append(f'{site["name"]}: {type(error).__name__}: {error}')
    for line in errors:
        print(f'[subs] {line}', file=sys.stderr)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(rows, ensure_ascii=False, indent=2),
                                   encoding='utf-8')
        print(f'[subs] {len(rows)} candidate(s) -> {args.json}')
    for index, row in enumerate(rows, 1):
        print(f"{index:3} {row['site']:10} {row.get('entry_id', ''):>8} {row['title'][:70]}")
    print(f'[subs] {len(rows)} candidate(s); 未自动检索的站点见 plan', file=sys.stderr)
    return 0 if rows else 4


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description='字幕调研：列出检索入口，产出每站的结论清单（段 0）')
    sub = parser.add_subparsers(dest='command', required=True)

    sites = sub.add_parser('sites', help='列出站点与自动化程度')
    sites.set_defaults(func=cmd_sites)

    plan_cmd = sub.add_parser('plan', help='为一部件名生成调研清单')
    plan_cmd.add_argument('-q', '--query', required=True)
    plan_cmd.add_argument('--out', default='', help='Markdown 清单落盘路径')
    plan_cmd.add_argument('--json', default='', help='JSON 清单落盘路径')
    plan_cmd.set_defaults(func=cmd_plan)

    search = sub.add_parser('search', help='跑能自动检索的站点')
    search.add_argument('-q', '--query', required=True)
    search.add_argument('--site', default='', help='逗号分隔；默认所有可自动站点')
    search.add_argument('--limit', type=int, default=8)
    search.add_argument('--proxy', default='')
    search.add_argument('--no-proxy', action='store_true')
    search.add_argument('--json', default='')
    search.set_defaults(func=cmd_search)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
