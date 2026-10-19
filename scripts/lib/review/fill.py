#!/usr/bin/env python3
"""Fill review-pack gaps with an OpenAI-compatible LLM, then guard the result.

Only **routable** gaps are sent (``pack.route`` != ``keep``): the pack already
decided where the audio can settle something.  The model may therefore only
choose inside a bounded action space:

====================  ======================================================
route                 allowed decisions
====================  ======================================================
``time``              ``keep`` / ``set_time``
``text``              ``keep`` / ``replace_text``
``delete``            ``keep`` / ``delete_line``
====================  ======================================================

Anything outside that set is rejected and the cue stays as it is.  On top of
that every decision is validated (boundary sanity, script preservation, length
sanity, no overlap) before it can reach the SRT, and the applier walks back a
decision that would damage a neighbour.

A rejected decision is *not* handed to a human: the item is marked
``rejected`` with a reason and stays ``keep``.
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
MAX_TEXT_CHARS = 100

ROUTE_ACTIONS = {
    'time': ('set_time',),
    'text': ('replace_text',),
    'delete': ('delete_line',),
}

_KANA = re.compile(r'[\u3040-\u30ff]')
_CJK = re.compile(r'[\u3400-\u9fff]')
_ASCII_WORD = re.compile(r'^[A-Za-z0-9 .,!?\'"\-]+$')

SYSTEM_PROMPT = """你是日语字幕修复流程里的复核模型。管线已经把能自动修的 cue 修完，
剩下的条目里只有「有证据可判」的会发给你，每条带 route 字段说明这条靠什么证据：

- route=time   文本与 ASR 匹配得上（similarity ≥ 0.5），但边界整体偏移；
               可用证据：start_error_ms / end_error_ms（正=字幕晚于语音）、
               suggested.new_start/new_end（匹配段边界）、asr_window（窗口内听到的语音）。
               只能选 "keep" 或 "set_time"。
- route=text   文本与 ASR 匹配度够、且 ASR 句子长度可比；
               可用证据：asr_text、length_ratio、asr_window.text。
               只能选 "keep" 或 "replace_text"。
- route=delete 极短、无可用匹配的 cue（噪声/重复行候选）。只能选 "keep" 或 "delete_line"。

判断规则：
- 拿不准就 "keep"；字幕卡、歌词、人名写法、语气差异、ASR 听错 → "keep"。
- set_time：new_start/new_end 用 suggested 或按 asr_window 的 speech_start/speech_end
  推算，单位秒，时长 0.3–12 秒，位移不超过 8 秒。
- replace_text：replacement 必须是日文、语义与原文相当、长度与原文接近；
  不要翻译，不要加说明。ASR 只听到半句就不要用它替换整句。
- delete_line：只在原文明显是噪声/重复时才删。

输出格式（只输出这个 JSON，不要解释）：
{"decisions":[{"id":0,"decision":"keep","why":"..."},
              {"id":1,"decision":"set_time","new_start":12.9,"new_end":14.2,"why":"..."},
              {"id":2,"decision":"replace_text","replacement":"…","why":"..."},
              {"id":3,"decision":"delete_line","why":"..."}]}"""


def _clip(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    text = str(text or '')
    return text if len(text) <= limit else text[:limit] + '…'


def _prompt_payload(items: list[dict]) -> str:
    rows = []
    for item in items:
        context = item.get('context') or {}
        window = item.get('asr_window') or {}
        rows.append({
            'id': item['id'],
            'route': item.get('route'),
            'route_reason': _clip(item.get('route_reason'), 60),
            'time': f"{item.get('start')} --> {item.get('end')}",
            'text': _clip(item.get('text')),
            'asr_text': _clip(item.get('asr_text')),
            'asr_window': {
                'text': _clip(window.get('text'), 60),
                'speech_start': window.get('speech_start'),
                'speech_end': window.get('speech_end'),
                'speech_seconds': window.get('speech_seconds'),
            },
            'similarity': item.get('similarity'),
            'local_similarity': item.get('local_similarity'),
            'window_shift': item.get('window_shift'),
            'length_ratio': item.get('length_ratio'),
            'start_err_ms': item.get('start_error_ms'),
            'end_err_ms': item.get('end_error_ms'),
            'suggested': item.get('suggested') or {},
            'prev': [_clip(c.get('text'), 40) for c in context.get('prev') or []],
            'next': [_clip(c.get('text'), 40) for c in context.get('next') or []],
        })
    return json.dumps(rows, ensure_ascii=False)


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
        if len(replacement) < max(2, int(len(text) * 0.35)):
            return None, 'replace_text: replacement much shorter than cue'
        if _has_japanese(text) and not _has_japanese(replacement):
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
                     allowed_actions: set | None = None) -> dict:
    by_id = {item['id']: item for item in pack.get('items') or []}
    stats = {'filled': 0, 'rejected': 0, 'unknown_id': 0}
    for decision in decisions:
        try:
            item_id = int(decision.get('id'))
        except (TypeError, ValueError):
            continue
        item = by_id.get(item_id)
        if item is None:
            stats['unknown_id'] += 1
            continue
        if str(item.get('decision') or '').strip():
            continue  # already decided (filled earlier or by a previous run)
        normalized, reason = validate_decision(
            item, decision, allow_rewrite=allow_rewrite,
            allowed_actions=allowed_actions)
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


def fill_pack(pack: dict, *, chat=None, batch_size: int = BATCH_SIZE,
              max_items: int | None = None, include_all: bool = False,
              allow_rewrite: bool = True, allowed_actions: set | None = None,
              log=sys.stderr) -> dict:
    """Fill routable gaps in ``pack`` in place.  Returns fill counters.

    ``chat`` is injectable (tests): it takes a list of messages and returns
    ``(content, error)``.  Defaults to ``lib.llm.call_chat``.
    """
    source = (pack_mod.pending_items(pack) if include_all
              else pack_mod.routable_items(pack))
    pending = list(source)
    if max_items is not None:
        pending = pending[:max_items]
    total = {'filled': 0, 'rejected': 0, 'unknown_id': 0,
             'batches': 0, 'failed_batches': 0,
             'sent': len(pending), 'skipped_unroutable':
                 len(pack_mod.pending_items(pack)) - len(source)}
    if not pending:
        return total

    if chat is None:
        from lib.llm import call_chat

        def chat(messages):
            content = call_chat(messages, temperature=0.0, max_tokens=2048)
            return content, ('' if content else 'empty response')

    for offset in range(0, len(pending), batch_size):
        batch = pending[offset:offset + batch_size]
        messages = [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': _prompt_payload(batch)},
        ]
        content, error = chat(messages)
        total['batches'] += 1
        if error:
            total['failed_batches'] += 1
            print(f'[review] batch {total["batches"]} failed: {error}',
                  file=log)
            continue
        data = _extract_json_object(content)
        decisions = (data or {}).get('decisions')
        if not isinstance(decisions, list):
            total['failed_batches'] += 1
            print(f'[review] batch {total["batches"]}: no decisions array',
                  file=log)
            continue
        counted = _apply_decisions(pack, decisions,
                                   allow_rewrite=allow_rewrite,
                                   allowed_actions=allowed_actions)
        for key in ('filled', 'rejected', 'unknown_id'):
            total[key] += counted[key]

    pack['stats'] = pack_mod.stats(pack)
    return total
