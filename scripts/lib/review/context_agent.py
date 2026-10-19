#!/usr/bin/env python3
"""Context review agents: does this name/form fit the story, and is the register right?

Two reviews, both read-only until an explicit approval step:

- **near terms** — candidates from ``nouns/normalize_variants.py --discover``
  (a Chinese substring that keeps appearing where a known term should be) and
  from the reading-level ASR repair (near-miss surfaces).  The agent gets the
  series profile + the cue context and answers ``promote`` / ``reject`` /
  ``uncertain``.  Only ``promote`` can be applied, and only to ``_meta.aliases``.
- **style** — sampled translated cues: is the register right for a period
  drama (narration semi-classical, dialogue colloquial, no modern slang or
  regional dialect)?  Produces flags, never rewrites.

Why an agent and not a rule: 「この語がこの作品世界に存在するか」「語体が合うか」は
一回の行では決まらず、作品背景（時代・人物・職掌）と前後文が要る。
"""

from __future__ import annotations

import json
import os
import shutil

VERDICTS = ('promote', 'reject', 'uncertain')
STYLE_ISSUES = ('文言混用', '现代语', '方言', '称谓不符', '专名未统一', '时代错置')

NEAR_PROMPT = """你在做字幕的「语境审查」。先读作品档案，再逐条判断候选写法。

作品档案：
{profile}

待判候选（每条含 id、候选写法 candidate、它应归属的规范名 canonical、
出现次数 positive/negative，以及例句 ja/zh）：
{items}

判断标准：
1. 这个词形是否确实是该规范名的另一种写法（音译/听写错误/旧译）？
2. 它有没有可能只是普通词、别的人名、地名，或与作品时代不符的东西？
3. 证据不足（例句太少、只出现一次）就判 uncertain，不要硬给结论。

只输出 JSON：
{{"decisions":[{{"id":0,"verdict":"promote|reject|uncertain","canonical":"规范中文名","reason":"依据"}}]}}"""

STYLE_PROMPT = """你在做字幕的「语境审查」（语体）。作品档案：
{profile}

待审条目（id、集、cue、中文）：
{items}

判断每条中文是否符合这部时代剧的语体：
- 旁白：半文半白，允许书面腔；
- 对白：口语，符合町人/武士身份；
- 禁止：现代网络语、外来语硬译、东北话等地域方言；
- 专名是否用了统一写法（与档案人物表一致）。
注意：制作人员名单、鸣谢、台标不是对白，直接判 issues 为空。

只输出 JSON：
{{"decisions":[{{"id":0,"issues":["现代语"],"severity":1,"suggestion":"改法方向"}}]}}
issues 允许空数组；severity 1=轻微 2=明显 3=破坏语境。"""


def _batch(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _call(chat, system: str, payload) -> dict | None:
    messages = [{'role': 'system', 'content': system},
                {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}]
    content, error = chat(messages)
    if error or not content:
        return None
    text = str(content).strip()
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def review_near_terms(candidates: list, profile_brief: str, *, chat=None,
                      batch_size: int = 12, log=None) -> list[dict]:
    """Decide promote/reject/uncertain for candidate variant forms."""
    if chat is None:
        from lib.llm import call_chat

        def chat(messages):
            text = call_chat(messages, temperature=0.1, max_tokens=1200,
                             json_mode=True)
            return text, ('' if text else 'empty response')

    rows = []
    for index, item in enumerate(candidates):
        row = dict(item)
        row['id'] = index
        rows.append(row)

    decisions = []
    for chunk in _batch(rows, batch_size):
        payload = [{'id': r['id'], 'candidate': r.get('candidate'),
                    'canonical': r.get('canonical'),
                    'positive': r.get('positive'), 'negative': r.get('negative'),
                    'examples': r.get('examples', [])[:3]} for r in chunk]
        data = _call(chat, NEAR_PROMPT.format(
            profile=profile_brief,
            items=json.dumps(payload, ensure_ascii=False)), payload)
        if not data:
            continue
        for decision in data.get('decisions') or []:
            try:
                idx = int(decision.get('id'))
            except (TypeError, ValueError):
                continue
            if not (0 <= idx < len(rows)):
                continue
            verdict = str(decision.get('verdict') or '').strip().lower()
            if verdict not in VERDICTS:
                continue
            merged = dict(rows[idx])
            merged.update({
                'verdict': verdict,
                'canonical_final': str(decision.get('canonical')
                                       or rows[idx].get('canonical') or ''),
                'reason': str(decision.get('reason') or '')[:300],
            })
            decisions.append(merged)
    return decisions


def review_style(items: list, profile_brief: str, *, chat=None,
                 batch_size: int = 15) -> list[dict]:
    """Flag register problems; never rewrites text."""
    if chat is None:
        from lib.llm import call_chat

        def chat(messages):
            text = call_chat(messages, temperature=0.1, max_tokens=1200,
                             json_mode=True)
            return text, ('' if text else 'empty response')

    rows = []
    for index, item in enumerate(items):
        row = dict(item)
        row['id'] = index
        rows.append(row)

    flags = []
    for chunk in _batch(rows, batch_size):
        payload = [{'id': r['id'], 'episode': r.get('episode'),
                    'cue': r.get('cue'), 'text': r.get('text')} for r in chunk]
        data = _call(chat, STYLE_PROMPT.format(
            profile=profile_brief,
            items=json.dumps(payload, ensure_ascii=False)), payload)
        if not data:
            continue
        for decision in data.get('decisions') or []:
            try:
                idx = int(decision.get('id'))
            except (TypeError, ValueError):
                continue
            if not (0 <= idx < len(rows)):
                continue
            issues = [str(i) for i in (decision.get('issues') or [])
                      if str(i) in STYLE_ISSUES]
            if not issues:
                continue
            merged = dict(rows[idx])
            merged.update({'issues': issues,
                           'severity': int(decision.get('severity') or 1),
                           'suggestion': str(decision.get('suggestion') or '')[:200]})
            flags.append(merged)
    return flags


def apply_approvals(meta_path: str, decisions: list[dict], glossary: dict) -> dict:
    """Add promoted candidates to ``_meta.aliases`` (backup first, no subtitles)."""
    canonicals = {str(v) for v in glossary.values()}
    with open(meta_path, 'r', encoding='utf-8') as handle:
        meta = json.load(handle)
    aliases = meta.setdefault('aliases', {})
    applied, skipped = [], []
    for decision in decisions:
        if decision.get('verdict') != 'promote':
            continue
        candidate = str(decision.get('candidate') or '').strip()
        canonical = str(decision.get('canonical_final')
                        or decision.get('canonical') or '').strip()
        if not candidate or canonical not in canonicals:
            skipped.append({'candidate': candidate, 'canonical': canonical,
                            'reason': 'canonical not in glossary'})
            continue
        bucket = aliases.setdefault(canonical, [])
        if candidate in bucket or candidate == canonical:
            skipped.append({'candidate': candidate, 'canonical': canonical,
                            'reason': 'already known'})
            continue
        bucket.append(candidate)
        applied.append({'candidate': candidate, 'canonical': canonical})
    if applied:
        shutil.copyfile(meta_path, meta_path + '.bak')
        with open(meta_path, 'w', encoding='utf-8', newline='\n') as handle:
            json.dump(meta, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
    return {'applied': applied, 'skipped': skipped}
