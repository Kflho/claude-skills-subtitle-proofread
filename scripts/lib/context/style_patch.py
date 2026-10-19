#!/usr/bin/env python3
"""Style patches: rewrite flagged lines under guard-rails, verify, never auto-write.

Why not just put the style in the prompt
----------------------------------------
Measured on EP051 (300 cues, same model/glossary/temperature): appending a style
block of any format to the system prompt changed almost nothing —

    arm            modern/dialect hits   register std   length
    no hint                        10         0.098    0.751
    keywords                        9         0.096    0.719
    prose                          10         0.100    0.746
    explicit banned list            10         0.102    0.741

So prompt hints are kept as optional reference, not as a quality lever.

What actually moves the needle
------------------------------
Targeted rewrite of the already-flagged cues.  But an unconstrained "make it
period-appropriate" instruction overshoots into 文言 (「此等人物，岂非盗贼乎」),
which is the exact complaint.  So every rewrite is verified before it can even
become a patch:

- modern/dialect/anachronism hits must drop and must not be replaced by others;
- 文言 density must not increase by more than ``MAX_REGISTER_DELTA``;
- length ratio stays in ``[0.7, 1.3]``;
- canonical names present before are still present after.

Patches are plain JSON (before/after/reason); applying them to subtitles is a
separate, explicit step.
"""

from __future__ import annotations

import json

from lib.context.style_lint import lint_text

MAX_REGISTER_DELTA = 0.10
MIN_LENGTH_RATIO = 0.70
MAX_LENGTH_RATIO = 1.30

_WEN_CHARS = set('乎矣焉哉兮汝吾尔岂乃亦皆莫弗焉')
_WEN_BIGRAMS = ('岂非', '此等', '乃是', '莫非', '焉能', '何故', '之乎者也')


def register_score(text: str) -> float:
    """Classical-Chinese density; used only as a *delta* guard."""
    text = str(text or '')
    if len(text) < 4:
        return 0.0
    chars = sum(1 for ch in text if ch in _WEN_CHARS)
    bigrams = sum(text.count(b) * 2 for b in _WEN_BIGRAMS)
    return (chars + bigrams) / len(text)


REWRITE_PROMPT = """你在修字幕的语体。逐条改写，规则：
1. 保持原意、人物、信息量，不合并、不拆分；
2. 去掉现代网络语与北方/东北方言（咋、啥、俺、咱、整、老铁…）；
3. **不要把口语改成文言**：原文是口语就用口语，别写「岂非…乎」「此等…」；
4. 长度与原文接近（±30%）；
5. 保留专名原样。

输入是 JSON 数组（id, text）。只输出 JSON 对象：
{"patches":[{"id":0,"text":"改写后的中文"}]}"""


def _parse_payload(text: str) -> list:
    """Accept ``[{...}]`` or ``{"patches": [...]}`` (json_mode forces objects)."""
    text = str(text or '').strip()
    if not text:
        return []
    for opener, closer in (('[', ']'), ('{', '}')):
        start, end = text.find(opener), text.rfind(closer)
        if start < 0 or end <= start:
            continue
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for value in data.values():
                if isinstance(value, list):
                    return value
    return []


def _call(chat, items):
    user = json.dumps(items, ensure_ascii=False)
    content, error = chat([
        {'role': 'system', 'content': REWRITE_PROMPT},
        {'role': 'user', 'content': user},
    ])
    if error or not content:
        return []
    return _parse_payload(content)


def verify_patch(before: str, after: str, *, names=()) -> tuple[bool, str]:
    """Return (ok, reason).  Any violation rejects the rewrite."""
    before, after = str(before or ''), str(after or '')
    if not after.strip():
        return False, 'empty rewrite'
    hits_before = lint_text(before)
    hits_after = lint_text(after)
    if len(hits_after) >= len(hits_before) and hits_before:
        return False, 'modern words not reduced'
    if hits_after and not hits_before:
        return False, 'introduced a new flagged word'
    delta = register_score(after) - register_score(before)
    if delta > MAX_REGISTER_DELTA:
        return False, f'register too literary (+{delta:.2f})'
    for name in names:
        if name and name in before and name not in after:
            return False, f'name lost: {name}'
    ratio = len(after) / max(1, len(before))
    if not (MIN_LENGTH_RATIO <= ratio <= MAX_LENGTH_RATIO):
        return False, f'length ratio {ratio:.2f} out of range'
    return True, ''


def plan_patches(flagged: list, *, chat=None, names=()) -> dict:
    """Ask for rewrites of flagged cues; return accepted/rejected patches."""
    if chat is None:
        from lib.llm import call_chat

        def chat(messages):
            text = call_chat(messages, temperature=0.2, max_tokens=1200,
                             json_mode=True)
            return text, ('' if text else 'empty response')

    if not flagged:
        return {'accepted': [], 'rejected': [], 'asked': 0}
    by_id = {}
    for start in range(0, len(flagged), 15):
        chunk = flagged[start:start + 15]
        items = [{'id': start + i, 'text': row['text']}
                 for i, row in enumerate(chunk)]
        for row in _call(chat, items):
            try:
                by_id[int(row.get('id'))] = str(row.get('text') or '')
            except (TypeError, ValueError, AttributeError):
                continue

    accepted, rejected = [], []
    for idx, row in enumerate(flagged):
        after = by_id.get(idx)
        if not after:
            rejected.append({'cue': row.get('cue'), 'before': row['text'],
                             'reason': 'no rewrite returned'})
            continue
        ok, reason = verify_patch(row['text'], after, names=names)
        patch = {'episode': row.get('episode'), 'cue': row.get('cue'),
                 'start': row.get('start'), 'before': row['text'],
                 'after': after}
        if ok:
            accepted.append(patch)
        else:
            rejected.append({**patch, 'reason': reason})
    return {'accepted': accepted, 'rejected': rejected, 'asked': len(flagged)}
