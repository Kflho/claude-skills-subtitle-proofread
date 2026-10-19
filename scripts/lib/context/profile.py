#!/usr/bin/env python3
"""Series profile: the story background every context review is grounded in.

A reviewer that only sees one line cannot answer questions like "would this
thing exist in this story" or "is this register right"; it needs a compact
series bible.  This module builds one from material we already have and keeps
it cacheable, so the review agents get the same context every time:

- deterministic facts: cast (glossary + ``_meta.series.cast``), setting terms,
  era/genre keywords, style policy, episode list;
- optional one-shot LLM synthesis (``summarize``) that turns those facts into a
  short prose background; cached by content hash so reruns reuse it.

Nothing here decides anything — it only prepares the context.  Decisions live
in ``lib/review/context_agent.py`` and are reviewed before they can touch a
glossary or a subtitle.
"""

from __future__ import annotations

import hashlib
import json
import os
import re

ERA_HINTS = {
    '江戸': '江户时代',
    '大江戸': '江户时代',
    '奉行': '江户幕府体制',
    '同心': '江户幕府体制',
    '岡っ引き': '江户町奉行所',
    '捕物': '江户捕物帐',
    '仇討': '武士社会',
}
REGISTER_POLICY = {
    'narration': '半文半白，保留时代剧旁白的书面感',
    'dialogue': '口语，符合身份/年龄/町人语气',
    'forbidden': ['现代网络语', '外来语硬译', '东北方言等地域方言'],
}


def _episode_titles(project_dir: str) -> list[str]:
    titles = []
    zh = os.path.join(project_dir, 'translated', 'zh')
    for name in sorted(os.listdir(zh)) if os.path.isdir(zh) else []:
        if name.endswith('.srt'):
            titles.append(name[:-4])
    return titles


def _hash(payload) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode('utf-8')
    return hashlib.sha256(blob).hexdigest()[:16]


def build_profile(*, series: str, glossary: dict, meta: dict,
                  project_dir: str = '') -> dict:
    """Assemble the deterministic part of the series bible."""
    meta = meta if isinstance(meta, dict) else {}
    series_meta = meta.get('series') or {}
    cast_meta = series_meta.get('cast') or {}

    cast = []
    for ja_form, zh in glossary.items():
        info = cast_meta.get(ja_form) or cast_meta.get(zh) or {}
        cast.append({
            'ja_forms': sorted({ja_form, str(info.get('ja') or ja_form)}),
            'zh': str(info.get('zh') or zh),
            'role': str(info.get('role') or ''),
            'notes': str(info.get('notes') or ''),
        })

    titles = _episode_titles(project_dir) if project_dir else []
    blob = json.dumps({'titles': titles, 'meta': series_meta,
                       'cast': cast}, ensure_ascii=False)
    era = series_meta.get('era', '')
    if not era:
        for hint, label in ERA_HINTS.items():
            if hint in blob:
                era = label
                break

    profile = {
        'schema_version': 1,
        'series': series,
        'era': era or '时代剧',
        'genre': series_meta.get('genre') or '时代剧 / 捕物帐',
        'setting_terms': sorted(series_meta.get('setting_terms') or []),
        'cast': cast,
        'register': series_meta.get('register') or REGISTER_POLICY,
        'episodes': titles,
        'episode_count': len(titles),
        'summary': str(series_meta.get('summary') or ''),
        'source_hash': _hash(blob),
    }
    return profile


SUMMARY_PROMPT = """你在为字幕翻译的「语境审查」准备背景资料。
下面是一个剧集的元信息（年代、类型、登场人物、固定用语）和若干集标题。
请用 150–250 字概括：故事发生在什么时代/社会，主角是谁、以什么为业，
这个世界里通常会出现什么（人物、职业、器物、事件），
以及翻译时最容易搞错的点（例如人名读音、职务称谓、时代错置的现代词）。
只输出这段背景说明，不要列表、不要解释你的思路。"""


def summarize(profile: dict, chat=None) -> str:
    """One-shot LLM background prose; callers cache it (see ``load_or_build``)."""
    if chat is None:
        from lib.llm import call_chat

        def chat(messages):
            text = call_chat(messages, temperature=0.2, max_tokens=600)
            return text, ('' if text else 'empty response')

    payload = {
        'series': profile.get('series'),
        'era': profile.get('era'),
        'genre': profile.get('genre'),
        'setting_terms': profile.get('setting_terms'),
        'cast': [{'zh': c.get('zh'), 'role': c.get('role'),
                  'ja_forms': c.get('ja_forms')} for c in profile.get('cast') or []],
        'episodes': (profile.get('episodes') or [])[:10],
    }
    messages = [
        {'role': 'system', 'content': SUMMARY_PROMPT},
        {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
    ]
    text, error = chat(messages)
    if error or not text:
        return ''
    return str(text).strip()


def load_or_build(path: str, *, series: str, glossary: dict, meta: dict,
                  project_dir: str = '', chat=None, refresh: bool = False) -> dict:
    """Cache the profile (+ its LLM summary) on disk, keyed by content hash."""
    profile = build_profile(series=series, glossary=glossary, meta=meta,
                            project_dir=project_dir)
    cached = {}
    if not refresh and os.path.isfile(path):
        try:
            with open(path, 'r', encoding='utf-8') as handle:
                cached = json.load(handle)
        except (OSError, json.JSONDecodeError):
            cached = {}
    if cached.get('source_hash') == profile['source_hash'] and cached.get('summary'):
        profile['summary'] = cached['summary']
        return profile
    summary = summarize(profile, chat=chat)
    profile['summary'] = summary or profile.get('summary', '')
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as handle:
        json.dump(profile, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    return profile


def profile_brief(profile: dict, limit: int = 1200) -> str:
    """Compact text block for prompts (background + cast + register rules)."""
    lines = [
        f"作品：{profile.get('series', '')}（{profile.get('era', '')}，{profile.get('genre', '')}）",
    ]
    if profile.get('summary'):
        lines.append('背景：' + str(profile['summary']).replace('\n', ' ')[:600])
    cast = profile.get('cast') or []
    if cast:
        names = '、'.join(f"{c.get('zh')}({'/'.join(c.get('ja_forms') or [])})"
                          for c in cast[:12])
        lines.append('人物：' + names)
    if profile.get('setting_terms'):
        lines.append('固定用语：' + '、'.join(profile['setting_terms'][:12]))
    register = profile.get('register') or {}
    if register:
        parts = []
        if register.get('narration'):
            parts.append('旁白=' + register['narration'])
        if register.get('dialogue'):
            parts.append('对白=' + register['dialogue'])
        if register.get('forbidden'):
            parts.append('禁用=' + '、'.join(register['forbidden']))
        if parts:
            lines.append('语体：' + '；'.join(parts))
    text = '\n'.join(lines)
    return text[:limit]
