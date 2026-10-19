#!/usr/bin/env python3
"""Review pack — the single artifact that carries "gaps" out of the pipeline.

A pack is written once per episode at the end of the fix phase.  It contains
every cue the pipeline could not fix automatically (L0), plus the evidence the
review model needs to decide — and, just as important, it says which gaps are
**routable** (there is evidence that a decision could help) and which are not.

Why the routing exists (2026-10 experiment):
- the old worklist carried ``suggested.new_text`` from any character match, so
  a 2-character ASR fragment ("おい") was offered as the replacement for a
  two-line cue.  A model that followed that hint would destroy real dialogue.
- most gaps are simply "the CC text and the ASR text disagree" — no action is
  correct there, and asking the model 275 times an episode only buys tokens.
- the routing keeps the model on the gaps where the audio can actually settle
  something (a matched span for timing, a same-length readable ASR sentence for
  text, a very short unmatched cue for noise), and answers ``keep`` for the
  rest **without a model call**.

Design notes:
- one file per episode replaces ``<EP>.asr_review.json`` / the old fixes file;
- decision vocabulary is the ``apply_fixes`` vocabulary (``keep`` /
  ``set_time`` / ``replace_text`` / ``delete_line``);
- the pack stays readable on its own: no absolute paths except ``srt``, and no
  reference-subtitle text (eval protocol: CC only scores, never feeds).
"""

from __future__ import annotations

import difflib
import json
import os
import time

SCHEMA_VERSION = 2
KIND = 'review_pack'

DECISIONS = ('keep', 'set_time', 'replace_text', 'delete_line')
ROUTES = ('time', 'text', 'delete', 'keep')
# Only these routes are dispatched to the review model.  ``time`` is evidence
# for the L0 planner, not a model task: measured boundary rewrites follow ASR
# bias and make correct cues worse (docs/review-ai-fill.md).
MODEL_ROUTES = ('text', 'delete')

CONTEXT_SPAN = 2
MAX_CONTEXT_CHARS = 80
MAX_ASR_WINDOW_CHARS = 60
ASR_WINDOW_PAD_S = 1.0

# routing thresholds (evidence, not policy: they decide who gets asked)
ROUTE_MIN_SIMILARITY = 0.50
ROUTE_MAX_TIME_ERROR_S = 2.0
ROUTE_MIN_TIME_ERROR_S = 0.15
ROUTE_MIN_LENGTH_RATIO = 0.40
ROUTE_MAX_LENGTH_RATIO = 2.20
ROUTE_DELETE_MAX_CHARS = 6
ROUTE_MAX_WINDOW_SHIFT_S = 2.5
ROUTE_MIN_LOCAL_SIMILARITY = 0.55
ROUTE_MIN_TEXT_LOCAL_SIMILARITY = 0.75


def _hms(seconds) -> str:
    """Format seconds as an SRT timecode (ms precision)."""
    total_ms = int(round(float(seconds or 0.0) * 1000))
    hours, rest = divmod(total_ms, 3600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, ms = divmod(rest, 1000)
    return f'{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}'


def _seconds(value):
    """Accept int/float seconds or an SRT timecode; return float|None."""
    if value is None or value == '':
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace('.', ',', 1)
    try:
        hours, minutes, rest = text.split(':')
        secs, ms = rest.split(',')
        return (int(hours) * 3600 + int(minutes) * 60 + int(secs)
                + int(ms.ljust(3, '0')[:3]) / 1000.0)
    except (TypeError, ValueError):
        return None


def _cue_text(cue) -> str:
    return str(cue.get('text') or '').strip()


def context_at(cues, index: int, span: int = CONTEXT_SPAN) -> dict:
    """Neighbouring cues around ``index`` (never crosses into eval refs)."""
    prev, nxt = [], []
    for offset in range(span, 0, -1):
        j = index - offset
        if j < 0:
            continue
        prev.append({'start': _hms(cues[j].get('start_s', 0.0)),
                     'text': _cue_text(cues[j])[:MAX_CONTEXT_CHARS]})
    for offset in range(1, span + 1):
        j = index + offset
        if j >= len(cues):
            continue
        nxt.append({'start': _hms(cues[j].get('start_s', 0.0)),
                    'text': _cue_text(cues[j])[:MAX_CONTEXT_CHARS]})
    return {'prev': prev, 'next': nxt}


def asr_window(segments, start_s: float, end_s: float) -> dict:
    """Audio-side evidence: what the ASR heard inside the cue window.

    ``segments`` are ASR segments (``start``/``end``/``text``).  The window is
    padded so a slightly mistimed cue still shows its own speech.
    """
    lo = max(0.0, float(start_s) - ASR_WINDOW_PAD_S)
    hi = float(end_s) + ASR_WINDOW_PAD_S
    parts, speech_seconds, first, last = [], 0.0, None, None
    for seg in segments or []:
        seg_start = float(getattr(seg, 'start', 0.0) or 0.0)
        seg_end = float(getattr(seg, 'end', seg_start) or seg_start)
        if seg_end <= lo or seg_start >= hi:
            continue
        parts.append(str(getattr(seg, 'text', '') or ''))
        speech_seconds += max(0.0, min(seg_end, hi) - max(seg_start, lo))
        first = seg_start if first is None else min(first, seg_start)
        last = seg_end if last is None else max(last, seg_end)
    text = ''.join(parts).strip()
    return {
        'text': text[:MAX_ASR_WINDOW_CHARS],
        'speech_seconds': round(speech_seconds, 2),
        'speech_start': None if first is None else round(first, 2),
        'speech_end': None if last is None else round(last, 2),
    }


def _length_ratio(text: str, asr_text: str) -> float:
    if not text:
        return 0.0
    return len(asr_text or '') / float(len(text))


def _local_similarity(text: str, window_text: str) -> float:
    if not text or not window_text:
        return 0.0
    return difflib.SequenceMatcher(None, text, window_text).ratio()


def route_item(*, cue_text: str, asr_text: str, similarity: float,
               start_error: float | None, hint: str,
               local_similarity: float = 0.0,
               window_shift: float | None = None) -> tuple[str, str]:
    """Decide whether a gap can be settled by the audio (and how).

    ``local_similarity`` compares the cue text with what the ASR heard *inside
    the cue window* — it does not need a global text match, which only happens
    for ~40% of CC cues.  ``window_shift`` is ``speech_start - cue_start``.
    """
    text = str(cue_text or '').strip()
    asr = str(asr_text or '').strip()
    ratio = _length_ratio(text, asr)
    if (start_error is not None
            and ROUTE_MIN_TIME_ERROR_S < abs(start_error) <= ROUTE_MAX_TIME_ERROR_S
            and similarity >= ROUTE_MIN_SIMILARITY):
        return 'time', 'matched span gives a usable boundary'
    if (window_shift is not None
            and ROUTE_MIN_TIME_ERROR_S < abs(window_shift) <= ROUTE_MAX_WINDOW_SHIFT_S
            and local_similarity >= ROUTE_MIN_LOCAL_SIMILARITY):
        return 'time', 'cue window speech starts away from the cue'
    if (similarity >= ROUTE_MIN_SIMILARITY
            and ROUTE_MIN_LENGTH_RATIO <= ratio <= ROUTE_MAX_LENGTH_RATIO
            and asr and asr != text):
        return 'text', 'readable ASR sentence of comparable length'
    if (local_similarity >= ROUTE_MIN_TEXT_LOCAL_SIMILARITY
            and window_shift is not None
            and abs(window_shift) <= ROUTE_MAX_WINDOW_SHIFT_S
            and asr and asr != text
            and ROUTE_MIN_LENGTH_RATIO <= ratio <= ROUTE_MAX_LENGTH_RATIO):
        return 'text', 'window speech matches roughly and can be re-read'
    if hint == 'delete_candidate' and len(text) <= ROUTE_DELETE_MAX_CHARS:
        return 'delete', 'very short cue with no usable match'
    if not asr:
        return 'keep', 'no speech evidence in the cue window'
    return 'keep', 'ASR disagrees but offers no replacement of comparable size'


def build_item(item_id: int, cue_index: int, cue: dict, plan_item,
               alignment, context: dict, segments=None) -> dict:
    """One pack item: the gap plus the evidence a reviewer needs."""
    start_s = float(cue.get('start_s', 0.0) or 0.0)
    end_s = float(cue.get('end_s', start_s) or start_s)
    start_error = alignment.start_error if alignment is not None else None
    end_error = alignment.end_error if alignment is not None else None
    asr_text = getattr(alignment, 'asr_text', '') if alignment is not None else ''
    text = _cue_text(cue) or plan_item.old_text
    window = asr_window(segments, start_s, end_s)
    if not asr_text:
        asr_text = window['text']
    local_similarity = _local_similarity(text, window['text'])
    window_shift = (None if window['speech_start'] is None
                    else round(window['speech_start'] - start_s, 3))
    route, route_reason = route_item(
        cue_text=text, asr_text=asr_text,
        similarity=float(plan_item.similarity or 0.0),
        start_error=start_error, hint=plan_item.action,
        local_similarity=local_similarity, window_shift=window_shift)

    # Only matched spans (or the speech inside the cue window) are usable as a
    # timing proposal; a raw character match on an unrelated line is not.
    proposed_start = proposed_end = None
    if route == 'time':
        if alignment is not None and alignment.start is not None:
            proposed_start = round(float(alignment.start), 3)
            proposed_end = (None if alignment.end is None
                            else round(float(alignment.end), 3))
        else:
            proposed_start = window['speech_start']
            proposed_end = window['speech_end']

    return {
        'id': item_id,
        'cue_index': cue_index,
        'kind': 'asr_repair',
        'route': route,
        'route_reason': route_reason,
        'start': _hms(start_s),
        'end': _hms(end_s),
        'seconds': {'start': round(start_s, 3), 'end': round(end_s, 3)},
        'text': text,
        'asr_text': asr_text,
        'asr_window': window,
        'similarity': round(float(plan_item.similarity or 0.0), 3),
        'local_similarity': round(local_similarity, 3),
        'window_shift': window_shift,
        'length_ratio': round(_length_ratio(text, asr_text), 2),
        'confidence': plan_item.confidence or (
            alignment.confidence if alignment is not None else ''),
        'start_error_ms': (None if start_error is None
                           else round(1000 * start_error, 1)),
        'end_error_ms': (None if end_error is None
                         else round(1000 * end_error, 1)),
        'reasons': list(plan_item.reasons),
        'hint': plan_item.action,
        'suggested': {
            'new_start': proposed_start,
            'new_end': proposed_end,
            'new_text': asr_text if route == 'text' else '',
        },
        'context': context,
        # ── filled by the review stage ──────────────────────────────
        'decision': '',
        'new_start': None,
        'new_end': None,
        'replacement': '',
        'decided_by': '',
        'why': '',
        'rejected': '',
    }


def build_pack(episode: str, cues: list, alignments: list, plan: list,
               srt_path: str = '', generator: dict | None = None,
               auto_count: int | None = None, segments=None) -> dict:
    """Turn the non-auto repairs into one pack for the whole episode."""
    by_index = {a.cue_index: a for a in alignments}
    items = []
    for plan_item in plan:
        index = plan_item.cue_index
        if not (0 <= index < len(cues)):
            continue
        items.append(build_item(
            len(items), index, cues[index], plan_item,
            by_index.get(index), context_at(cues, index), segments=segments))

    pack = {
        'schema_version': SCHEMA_VERSION,
        'kind': KIND,
        'episode': episode,
        'srt': srt_path or '',
        'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'generator': dict(generator or {}),
        'stats': {},
        'items': items,
    }
    pack['stats'] = stats(pack, cues=len(cues), auto=auto_count)
    return pack


def stats(pack: dict, cues: int | None = None, auto: int | None = None) -> dict:
    """Recompute counters, including the auto/routed/decided breakdown."""
    items = pack.get('items') or []
    filled = [i for i in items if str(i.get('decision') or '').strip()]
    by_decision = {}
    for item in filled:
        key = str(item['decision']).strip().lower()
        by_decision[key] = by_decision.get(key, 0) + 1
    rejected = [i for i in items if i.get('rejected')]
    by_route = {}
    for item in items:
        key = item.get('route') or 'keep'
        by_route[key] = by_route.get(key, 0) + 1
    out = {
        'cues': cues if cues is not None else pack.get('stats', {}).get('cues', 0),
        'gaps': len(items),
        'routed': sum(n for r, n in by_route.items() if r in MODEL_ROUTES),
        'timed': by_route.get('time', 0),
        'routes': by_route,
        'decided': len(filled) - len(rejected),
        'kept': by_decision.get('keep', 0),
        'set_time': by_decision.get('set_time', 0),
        'replace_text': by_decision.get('replace_text', 0),
        'delete_line': by_decision.get('delete_line', 0),
        'rejected': len(rejected),
        'pending': len(items) - len(filled),
    }
    if auto is not None:
        out['auto'] = auto
    elif 'auto' in pack.get('stats', {}):
        out['auto'] = pack['stats']['auto']
    return out


def pending_items(pack: dict) -> list[dict]:
    return [i for i in (pack.get('items') or [])
            if not str(i.get('decision') or '').strip()]


def routable_items(pack: dict) -> list[dict]:
    """Undecided gaps the review model is asked about (text / delete routes)."""
    return [i for i in pending_items(pack) if i.get('route') in MODEL_ROUTES]


def save_pack(path: str, pack: dict) -> str:
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    pack['stats'] = stats(pack)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as handle:
        json.dump(pack, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    os.replace(tmp, path)
    return path


def load_pack(path: str) -> dict | None:
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get('kind') != KIND:
        return None
    data.setdefault('items', [])
    return data


def pack_path(scan_dir: str, episode: str, suffix: str = 'ai') -> str:
    return os.path.join(scan_dir, f'{episode}.{suffix}_pack.json')


def applied_path(scan_dir: str, episode: str, suffix: str = 'ai') -> str:
    return os.path.join(scan_dir, f'{episode}.{suffix}_applied.json')
