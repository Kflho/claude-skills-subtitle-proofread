#!/usr/bin/env python3
"""字幕调研：段 0 的可执行入口 —— 先问「有没有现成字幕」，再决定后面走哪条路。

Why this exists
---------------
查字幕是整条生产线里**最容易被跳过**的一步：跳过的代价是白跑一遍 ASR，或者明明有
日文字幕却从零转写。所以把它做成一个命令，产出落盘的清单：

- 四个站点（jimaku / kitsunekko / assrt / opensubtitles）各自的检索入口、语种、
  自动化程度；
- 每站的结论栏（有/无、语种、版本、文件落点）留空给人填；
- **没实测过的站点绝不给编造 URL**：``adapter is None`` 的站点只写「待实测 / 人工检索」。
  这条比「多支持几个站」重要——错的端点比没有端点更费时间。

    python scripts/acquire/subs_search.py sites
    python scripts/acquire/subs_search.py plan -q "赤き血のイレブン" \\
        --out bin/reference_docs/字幕调研.md --json data/temp/subs_search.json

有站点实测通过后：给 ``SITES`` 里那一项填 ``url_template``（用 ``{query}`` 占位）
和 ``adapter``（返回候选列表的函数），再补一个离线用例；不要在没实测时先填。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import quote

# 站点清单：顺序即建议的检索顺序（日文字幕优先，中文字幕只作对照）。
# url_template 为空 = 端点未实测，脚本不拼 URL、不假装能自动检索。
SITES = [
    {
        'key': 'jimaku',
        'name': 'jimaku.cc',
        'langs': 'ja',
        'note': '日文字幕，动漫为主；可直接当源语言',
        'url_template': '',
        'adapter': None,
    },
    {
        'key': 'kitsunekko',
        'name': 'kitsunekko.net',
        'langs': 'ja',
        'note': '日文字幕，老番覆盖好',
        'url_template': '',
        'adapter': None,
    },
    {
        'key': 'assrt',
        'name': 'assrt.net（射手网）',
        'langs': 'zh',
        'note': '中文字幕为主；只作译名/术语对照，不当源',
        'url_template': '',
        'adapter': None,
    },
    {
        'key': 'opensubtitles',
        'name': 'opensubtitles.org / .com',
        'langs': 'multi',
        'note': '多语言、量大；.com 的 REST API 需要 key',
        'url_template': '',
        'adapter': None,
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
            'method': '脚本检索' if automated else '人工检索（端点待实测）',
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
    lines += ['', '## 结论', '',
              '- 可用的源字幕：',
              '- 可用的对照字幕（不当中文源的译本）：',
              '- 缺口（哪些站没查/查不到、用什么替代）：', '']
    return '\n'.join(lines)


def cmd_sites(args) -> int:
    for site in SITES:
        state = '自动' if is_automated(site) else '人工（端点待实测）'
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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
