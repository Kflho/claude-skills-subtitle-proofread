#!/usr/bin/env python3
"""Fill review packs with an OpenAI-compatible LLM, then guard the result.

Who gets asked (see ``lib/review/pack.py``):
- ``kind=translation`` — QA-flagged lines (Japanese residue, echo/untranslated,
  empty, glossary miss, CPS). This is the failure class the automatic repair
  pass cannot clear, and rewriting one line is the intended fix.
- ``kind=asr_repair`` with ``route=text`` (a readable ASR sentence of
  comparable length) or ``route=delete`` (very short unmatched cue).

Timing is **not** the model's job.  ``route=time`` cues are never sent: the
experiment in ``sk_01_dev/docs/review-ai-fill.md`` shows model-driven boundary
moves follow ASR bias and make correct cues worse; the L0 rules own the
timeline.  (The external post-processing protocol in the research links makes
the same choice: the model must not write times.)

Every decision is validated before it can reach the SRT:
- action must be allowed for the item's route *and* for its kind policy;
- ``replace_text`` must stay in the item's language (Japanese for ASR items,
  no kana for Chinese translation items), length-sane, non-empty;
- ``delete_line`` only for short or poorly matched cues.

A rejected decision is not handed to a human: the item is marked ``rejected``
with a reason and stays ``keep``.
"""

from __future__ import annotations

import json
import re
import sys

from lib.review import pack as pack_mod

MAX_SHIFT_S = 8.0
MIN_DURATION_S = 0.3
MAX_DURATION_S = 12.0
BATCH_SIZE = 15
MAX_BATCH_ITEMS = 40
MAX_BATCH_CHARS = 4000
MAX_TEXT_CHARS = 100
MAX_RETRIES = 1

ROUTE_ACTIONS = {
    'time': ('set_time',),          # kept for validation, never dispatched
    'text': ('replace_text',),
    'delete': ('delete_line',),
}

_KANA = re.compile(r'[\u3040-\u30ff]')
_CJK = re.compile(r'[\u3400-\u9fff]')
_ASCII_WORD = re.compile(r'^[A-Za-z0-9 .,!?\'"\-]+$')

SYSTEM_PROMPT = """你是字幕修复流程的复核模型。你只在自动流程判失败时才被调用，
任务是把那一条修好，或者在无法确定时保留原样。只输出一个 JSON 对象。

输入里每条都有：
- id：本次请求内的编号，输出必须与输入一一对应，**每个 id 恰好出现一次**；
- kind：translation（中文字幕被 QA 判失败）或 asr_repair（日文字幕与语音不一致）；
- route：允许你做的动作类别；
- text：当前字幕；source_text（translation）是日文原句；
- failure（translation）：japanese_residual 残留假名 / echo 未翻译 / empty 空行 /
  glossary_miss 专名不一致 / too_long 或 cps 过长；
- glossary_misses：必须使用的规范译名；
- context：前后各两条，用来判断这句话在说什么；
- asr_text / local_similarity / length_ratio（asr_repair）：语音侧证据。

动作（严格按 kind + route）：
- translation：只能 "keep" 或 "replace_text"。replacement 必须是**中文**，
  不含假名，长度不超过一行字幕（一般 ≤20 字），保留 glossary 里的规范译名。
- asr_repair + route=text：只能 "keep" 或 "replace_text"，replacement 是**日文**，
  长度与原句接近（0.4–2.2 倍）。
- asr_repair + route=delete：只能 "keep" 或 "delete_line"。
- 时间轴不由你决定：不要输出任何时间字段。

判断规则：
- 拿不准 → "keep"。字幕卡、歌词、人名写法、语气/方言差异、QA 误报 → "keep"。
- 原文已是对的（包括「假名里其实没有日文词」的情况）→ "keep"。
- 不要翻译整段、不要合并/拆分、不要加解释或编号。
- 每条 why 写你依据的字段（例如 "japanese_residual + source_text"）。

格式（唯一输出，无 markdown、无前后说明）：
{"decisions":[
  {"id":0,"decision":"keep","why":"字幕卡，原文正确"},
  {"id":1,"decision":"replace_text","replacement":"快来这边！","why":"japanese_residual"},
  {"id":2,"decision":"delete_line","why":"纯噪声，长度 1"}
]}
"""


def _clip(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    text = str(text or '')
    return text if len(text) <= limit else text[:limit] + '…'


def _prompt_payload(items: list[dict]) -> str:
    rows = []
    for item in items:
        context = item.get('context') or {}
        window = item.get('asr_window') or {}
        row = {
            'id': item['id'],
            'kind': item.get('kind') or 'asr_repair',
            'route': item.get('route'),
            'text': _clip(item.get('text')),
            'prev': [_clip(c.get('text'), 40) for c in context.get('prev') or []],
            'next': [_clip(c.get('text'), 40) for c in context.get('next') or []],
        }
        if row['kind'] == 'translation':
            row.update({
                'source_text': _clip(item.get('source_text')),
                'failure': item.get('failure'),
                'glossary_misses': list(item.get('glossary_misses') or [])[:6],
            })
        else:
            row.update({
                'asr_text': _clip(item.get('asr_text')),
                'local_similarity': item.get('local_similarity'),
                'length_ratio': item.get('length_ratio'),
                'route_reason': _clip(item.get('route_reason'), 60),
                'asr_window': {
                    'text': _clip(window.get('text'), 60),
                    'speech_seconds': window.get('speech_seconds'),
                },
            })
        rows.append(row)
    return json.dumps(rows, ensure_ascii=False)


def _batch_cost(items: list[dict]) -> int:
    total = 0
    for item in items:
        total += len(str(item.get('text') or ''))
        total += len(str(item.get('source_text') or ''))
        total += len(str(item.get('asr_text') or ''))
        for side in ('prev', 'next'):
            total += sum(len(str(c.get('text') or ''))
                         for c in (item.get('context') or {}).get(side) or [])
    return total


def _make_batches(items: list[dict], *, max_items: int = MAX_BATCH_ITEMS,
                  max_chars: int = MAX_BATCH_CHARS) -> list[list[dict]]:
    """Character-budget batching: ids alone are a poor size proxy."""
    batches, current, cost = [], [], 0
    for item in items:
        item_cost = _batch_cost([item])
        if current and (len(current) >= max_items
                        or cost + item_cost > max_chars):
            batches.append(current)
            current, cost = [], 0
        current.append(item)
        cost += item_cost
    if current:
        batches.append(current)
    return batches


def _extract_json_object(text: str) -> dict | None:
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith('```'):
        cleaned = re.sub(r'^```[a-zA-Z]*\s*', '', cleaned)
        cleaned = re.sub(r'```\s*$', '', cleaned).strip()
    start = cleaned.find('{')
    end = cleaned.rfind('}')
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _norm_decision(value) -> str:
    text = str(value or '').strip().lower()
    aliases = {
        'fix_time': 'set_time', 'set_time': 'set_time', 'time': 'set_time',
        'replace': 'replace_text', 'replace_text': 'replace_text',
        'text': 'replace_text',
        'delete': 'delete_line', 'delete_line': 'delete_line',
        'drop': 'delete_line', 'remove': 'delete_line',
        'keep': 'keep', '': 'keep', 'none': 'keep', 'ok': 'keep',
    }
    return aliases.get(text, '')


def _has_japanese(text: str) -> bool:
    return bool(_KANA.search(text) or _CJK.search(text))


def _allowed_for(item: dict, allowed_actions) -> set | None:
    """Per-kind policy: ASR gaps and translation failures are gated apart."""
    if isinstance(allowed_actions, dict):
        kind = str(item.get('kind') or 'asr_repair')
        return allowed_actions.get(kind, allowed_actions.get('asr_repair', set()))
    return allowed_actions


def validate_decision(item: dict, decision: dict,
                      allow_rewrite: bool = True,
                      allowed_actions: set | None = None
                      ) -> tuple[dict | None, str]:
    """Normalise one model decision; second value is a rejection reason."""
    action = _norm_decision(decision.get('decision'))
    if not action:
        return None, f"unknown decision {decision.get('decision')!r}"
    if action == 'keep':
        return {'decision': 'keep'}, ''
    if allowed_actions is not None:
        if action in ('set_time', 'replace_text') and 'rewrite' not in allowed_actions:
            return None, f'{action} disabled by REVIEW_ACTIONS'
        if action == 'delete_line' and 'delete' not in allowed_actions:
            return None, 'delete_line disabled by REVIEW_ACTIONS'
    elif not allow_rewrite and action in ('set_time', 'replace_text'):
        return None, 'rewrite disabled (triage-only mode)'

    route = item.get('route') or 'keep'
    allowed = ROUTE_ACTIONS.get(route, ())
    if action not in allowed:
        return None, f'{action} not allowed for route {route!r}'

    kind = str(item.get('kind') or 'asr_repair')
    text = str(item.get('text') or '')
    seconds = item.get('seconds') or {}
    cue_start = float(seconds.get('start') or 0.0)
    cue_end = float(seconds.get('end') or cue_start)

    if action == 'set_time':
        new_start = pack_mod._seconds(decision.get('new_start'))
        new_end = pack_mod._seconds(decision.get('new_end'))
        if new_start is None and new_end is None:
            suggested = item.get('suggested') or {}
            new_start = pack_mod._seconds(suggested.get('new_start'))
            new_end = pack_mod._seconds(suggested.get('new_end'))
        if new_start is None and new_end is None:
            window = item.get('asr_window') or {}
            new_start = pack_mod._seconds(window.get('speech_start'))
            new_end = pack_mod._seconds(window.get('speech_end'))
        if new_start is None and new_end is None:
            return None, 'set_time without usable times'
        new_start = cue_start if new_start is None else new_start
        new_end = cue_end if new_end is None else new_end
        if new_end <= new_start:
            return None, 'set_time: end <= start'
        duration = new_end - new_start
        if not (MIN_DURATION_S <= duration <= MAX_DURATION_S):
            return None, f'set_time: duration {duration:.2f}s out of range'
        if abs(new_start - cue_start) > MAX_SHIFT_S:
            return None, f'set_time: shift {new_start - cue_start:+.2f}s too large'
        return {'decision': 'set_time',
                'new_start': round(new_start, 3),
                'new_end': round(new_end, 3)}, ''

    if action == 'replace_text':
        replacement = str(decision.get('replacement') or '').strip()
        if not replacement:
            return None, 'replace_text without replacement'
        if len(replacement) > max(16, int(len(text) * 2.2) + 8):
            return None, 'replace_text: replacement far longer than cue'
        floor = 1 if kind == 'translation' else max(2, int(len(text) * 0.35))
        if len(replacement) < floor:
            return None, 'replace_text: replacement much shorter than cue'
        if kind == 'translation':
            from lib.translation.qa import count_kana
            if count_kana(replacement):
                return None, 'replace_text: translation still contains kana'
        elif _has_japanese(text) and not _has_japanese(replacement):
            return None, 'replace_text: replacement dropped Japanese'
        if _ASCII_WORD.match(replacement) and len(replacement.split()) > 5:
            return None, 'replace_text: latin-only replacement'
        return {'decision': 'replace_text', 'replacement': replacement}, ''

    # delete_line
    similarity = float(item.get('similarity') or 0.0)
    if len(text.strip()) > 12 and similarity > 0.25:
        return None, 'delete_line: cue looks like real dialogue'
    return {'decision': 'delete_line'}, ''


def _apply_decisions(pack: dict, decisions: list[dict],
                     allow_rewrite: bool = True,
                     allowed_actions: set | dict | None = None) -> dict:
    by_id = {item['id']: item for item in pack.get('items') or []}
    stats = {'filled': 0, 'rejected': 0, 'unknown_id': 0, 'duplicate_id': 0}
    seen = set()
    for decision in decisions:
        try:
            item_id = int(decision.get('id'))
        except (TypeError, ValueError):
            continue
        item = by_id.get(item_id)
        if item is None:
            stats['unknown_id'] += 1
            continue
        if item_id in seen:
            stats['duplicate_id'] += 1
            continue
        seen.add(item_id)
        if str(item.get('decision') or '').strip():
            continue  # already decided (filled earlier or by a previous run)
        normalized, reason = validate_decision(
            item, decision, allow_rewrite=allow_rewrite,
            allowed_actions=_allowed_for(item, allowed_actions))
        if normalized is None:
            item['rejected'] = reason
            item['decision'] = 'keep'
            item['decided_by'] = 'review-model'
            item['why'] = f'[rejected] {reason}'
            stats['rejected'] += 1
            continue
        item['decision'] = normalized['decision']
        item['new_start'] = normalized.get('new_start')
        item['new_end'] = normalized.get('new_end')
        item['replacement'] = normalized.get('replacement', '')
        item['decided_by'] = 'review-model'
        item['why'] = str(decision.get('why') or '')[:200]
        item['rejected'] = ''
        stats['filled'] += 1
    return stats


def _ask(chat, batch: list[dict], log) -> list[dict] | None:
    messages = [
        {'role': 'system', 'content': SYSTEM_PROMPT},
        {'role': 'user', 'content': _prompt_payload(batch)},
    ]
    content, error = chat(messages)
    if error:
        print(f'[review] batch failed: {error}', file=log)
        return None
    data = _extract_json_object(content)
    decisions = (data or {}).get('decisions')
    if not isinstance(decisions, list):
        print('[review] batch: no decisions array', file=log)
        return None
    return decisions


def fill_pack(pack: dict, *, chat=None, batch_size: int = BATCH_SIZE,
              max_items: int | None = None, include_all: bool = False,
              allow_rewrite: bool = True, allowed_actions: set | dict | None = None,
              log=sys.stderr) -> dict:
    """Fill routable gaps in ``pack`` in place.  Returns fill counters.

    ``chat`` is injectable (tests): it takes a list of messages and returns
    ``(content, error)``.  Defaults to ``lib.llm.call_chat`` (JSON mode).
    """
    source = (pack_mod.pending_items(pack) if include_all
              else pack_mod.routable_items(pack))
    pending = list(source)
    if max_items is not None:
        pending = pending[:max_items]
    total = {'filled': 0, 'rejected': 0, 'unknown_id': 0, 'duplicate_id': 0,
             'batches': 0, 'failed_batches': 0, 'retries': 0,
             'sent': len(pending),
             'skipped_unroutable': len(pack_mod.pending_items(pack)) - len(source)}
    if not pending:
        return total

    if chat is None:
        from lib.llm import call_chat

        def chat(messages):
            content = call_chat(messages, temperature=0.1, max_tokens=2048,
                                json_mode=True)
            return content, ('' if content else 'empty response')

    for batch in _make_batches(pending, max_items=batch_size):
        total['batches'] += 1
        decisions = _ask(chat, batch, log)
        if decisions is None:
            total['failed_batches'] += 1
            continue
        counted = _apply_decisions(pack, decisions,
                                   allow_rewrite=allow_rewrite,
                                   allowed_actions=allowed_actions)
        for key in ('filled', 'rejected', 'unknown_id', 'duplicate_id'):
            total[key] += counted[key]

        # ID completeness: re-ask once for the cues the model skipped.
        decided = {int(d.get('id')) for d in decisions
                   if str(d.get('id', '')).lstrip('-').isdigit()}
        missing = [i for i in batch if i['id'] not in decided]
        if missing and total['retries'] < MAX_RETRIES:
            total['retries'] += 1
            total['batches'] += 1
            retry_decisions = _ask(chat, missing, log)
            if retry_decisions is None:
                total['failed_batches'] += 1
                continue
            counted = _apply_decisions(pack, retry_decisions,
                                       allow_rewrite=allow_rewrite,
                                       allowed_actions=allowed_actions)
            for key in ('filled', 'rejected', 'unknown_id', 'duplicate_id'):
                total[key] += counted[key]

    pack['stats'] = pack_mod.stats(pack)
    return total
