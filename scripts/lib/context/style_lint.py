#!/usr/bin/env python3
"""Deterministic Chinese style lint: what the LLM reviewer should not have to guess.

The LLM style review is good at nuance but expensive and non-reproducible.
These lexicons catch the objective part — modern internet slang, northern
dialect, and concepts that cannot exist in a period drama — so tests can pin
precision/recall and the reviewer only handles the rest.

Substring matching in Chinese is dangerous (「酷」 inside 「残酷」), so entries
are two characters or longer unless they are unambiguous discourse markers.
"""

from __future__ import annotations

# 网络/现代口语（出现即可疑，severity 2）
INTERNET = (
    '离谱', '搞定', '整活', '绝了', '破防', '给力', '无语', '拉满',
    '没毛病', '老铁', '大佬', '玩家', '在线', '刷新', '复制', '系统',
    '数据', '项目', '逻辑', '科学', '概率', '效率', '氛围',
)
# 方言（北方/东北腔，severity 2；用户点名过）
DIALECT = ('咋', '啥', '俺', '咱', '忒', '得瑟', '埋汰', '磕碜', '忽悠',
           '唠嗑', '干哈', '老铁', '整')
# 时代错置的现代概念（severity 3）
ANACHRONISM = ('高校', '公司', '上班', '同事', '地铁', '手机', '电话', '电视',
               '警察', '汽车', '医院', '学校', '银行', '超市', '咖啡', '啤酒')

CATEGORIES = (
    ('modern', INTERNET, 2),
    ('dialect', DIALECT, 2),
    ('anachronism', ANACHRONISM, 3),
)

ISSUE_LABEL = {
    'modern': '现代语',
    'dialect': '方言',
    'anachronism': '时代错置',
}


def lint_text(text: str) -> list[dict]:
    """Return matches: category, term, offset, severity."""
    text = str(text or '')
    hits = []
    for category, terms, severity in CATEGORIES:
        for term in terms:
            start = text.find(term)
            while start >= 0:
                hits.append({'category': category, 'issue': ISSUE_LABEL[category],
                             'term': term, 'offset': start,
                             'severity': severity})
                start = text.find(term, start + 1)
    # 「整」只在与方言搭配时算（整理/整齐 是正常词）
    hits = [h for h in hits
            if not (h['term'] == '整' and any(
                h['term'] + tail in ('整理', '整齐', '整个') for tail in ('理', '齐', '个')))]
    return sorted(hits, key=lambda h: (h['offset'], h['term']))


def lint_cues(cues: list, *, episode: str = '') -> dict:
    """Lint every cue; return per-cue flags plus aggregates."""
    flags = []
    by_issue = {}
    for index, cue in enumerate(cues):
        text = str(cue.get('text') or '')
        hits = lint_text(text)
        if not hits:
            continue
        issues = sorted({h['issue'] for h in hits})
        for issue in issues:
            by_issue[issue] = by_issue.get(issue, 0) + 1
        flags.append({
            'episode': episode, 'cue': index + 1,
            'start': cue.get('start', ''), 'text': text,
            'issues': issues,
            'severity': max(h['severity'] for h in hits),
            'terms': [h['term'] for h in hits],
        })
    return {'flags': flags, 'by_issue': by_issue}


def aggregate(results: list[dict]) -> dict:
    flags = [f for r in results for f in r['flags']]
    by_issue = {}
    for flag in flags:
        for issue in flag['issues']:
            by_issue[issue] = by_issue.get(issue, 0) + 1
    return {'cues': len(flags), 'by_issue': by_issue,
            'episodes': len(results),
            'worst': sorted(flags, key=lambda f: -f['severity'])[:20]}
