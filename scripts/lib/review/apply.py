#!/usr/bin/env python3
"""Apply review-pack decisions to the subtitles, with per-cue rollback.

The applier is the second half of the old "fill the correction, run the
apply script" loop.  It differs in two ways:

- it applies **decisions in place** (no separate fixes file, no CLI step);
- every edit is verified before it is kept.  A decision that would create an
  overlap, an empty line or an unnatural neighbour duplicate is rolled back and
  the item is marked ``rejected`` — the pack then reports "kept with reason"
  instead of handing the cue to a human.
"""

from __future__ import annotations

import copy
import os

from lib.review import pack as pack_mod

MAX_NEIGHBOUR_OVERLAP_S = 0.05


def _fix_for(item: dict, srt: str) -> dict | None:
    decision = str(item.get('decision') or '').strip().lower()
    start = str(item.get('start') or '')
    if not start or decision in ('', 'keep'):
        return None
    if decision == 'set_time':
        fix = {'action': 'set_time', 'file': srt, 'start': start}
        if item.get('new_start') not in (None, ''):
            fix['new_start'] = item['new_start']
        if item.get('new_end') not in (None, ''):
            fix['new_end'] = item['new_end']
        return fix
    if decision == 'replace_text':
        replacement = str(item.get('replacement') or '').strip()
        if not replacement:
            return None
        return {'action': 'replace_text', 'file': srt, 'start': start,
                'replacement': replacement}
    if decision == 'delete_line':
        return {'action': 'delete_line', 'file': srt, 'start': start}
    return None


def _index_of(cues: list[dict], item: dict) -> int | None:
    """Locate the cue by its original start time (indices shift when cues die)."""
    start = str(item.get('start') or '')
    for i, cue in enumerate(cues):
        if str(cue.get('start') or '') == start:
            return i
    index = item.get('cue_index')
    if isinstance(index, int) and 0 <= index < len(cues):
        return index
    return None


def _neighbours_ok(cues: list[dict], index: int) -> bool:
    cue = cues[index]
    start = float(cue.get('start_s') or 0.0)
    end = float(cue.get('end_s') or start)
    if end <= start:
        return False
    if index > 0:
        prev_end = float(cues[index - 1].get('end_s') or 0.0)
        if start + MAX_NEIGHBOUR_OVERLAP_S < prev_end:
            return False
    if index + 1 < len(cues):
        next_start = float(cues[index + 1].get('start_s') or 0.0)
        if next_start and end - MAX_NEIGHBOUR_OVERLAP_S > next_start:
            return False
    return True


def _has_neighbour_duplicate(cues: list[dict], index: int) -> bool:
    text = str(cues[index].get('text') or '').strip()
    for j in (index - 1, index + 1):
        if 0 <= j < len(cues) and str(cues[j].get('text') or '').strip() == text:
            return True
    return False


def _apply_one(cues: list[dict], fix: dict, item: dict) -> tuple[bool, str]:
    """Apply one fix to ``cues``; returns (ok, message).  Caller snapshots."""
    from apply.apply_fixes import (
        apply_delete_line, apply_replace_text, apply_set_time,
    )

    action = fix['action']
    if action == 'delete_line':
        if len(cues) <= 1:
            return False, 'refusing to delete the last cue'
        return apply_delete_line(cues, fix)

    index = _index_of(cues, item)
    if index is None:
        return False, 'cue not found for its start time'
    if action == 'set_time':
        ok, message = apply_set_time(cues, fix)
        if not ok:
            return False, message
        if not _neighbours_ok(cues, index):
            return False, 'set_time overlaps a neighbour'
        return True, message
    if action == 'replace_text':
        duplicated_before = _has_neighbour_duplicate(cues, index)
        ok, message = apply_replace_text(cues, fix)
        if not ok:
            return False, message
        if not str(cues[index].get('text') or '').strip():
            return False, 'replace_text produced an empty cue'
        if not duplicated_before and _has_neighbour_duplicate(cues, index):
            return False, 'replace_text duplicates a neighbour'
        return True, message
    return False, f'unsupported action {action}'


def apply_pack(pack: dict, *, srt_path: str | None = None,
               dry_run: bool = False) -> dict:
    """Apply every decided item of ``pack`` to its SRT.

    Returns ``{'srt', 'applied', 'rejected', 'kept', 'outcomes', 'error'}``.
    Items get ``applied`` / ``rejected`` marks so the pack records what
    happened without a second file being the source of truth.
    """
    srt = srt_path or str(pack.get('srt') or '')
    result = {'srt': srt, 'applied': 0, 'rejected': 0, 'kept': 0,
              'outcomes': [], 'error': ''}
    if not srt or not os.path.isfile(srt):
        result['error'] = 'missing srt'
        return result
    if not srt.lower().endswith('.srt'):
        result['error'] = 'apply supports SRT only'
        return result

    from lib.project_utils import backup_file
    from lib.subtitle_io import read_subtitles, write_subtitles

    cues = read_subtitles(srt, mark_garbled=False)
    if not cues:
        result['error'] = 'no cues parsed'
        return result

    for item in pack.get('items') or []:
        decision = str(item.get('decision') or '').strip().lower()
        item.pop('applied', None)
        outcome = {
            'id': item.get('id'),
            'cue_index': item.get('cue_index'),
            'start': item.get('start'),
            'decision': decision or 'keep',
            'status': 'kept',
            'reason': '',
            'before': str(item.get('text') or ''),
        }
        if decision in ('', 'keep'):
            item['applied'] = False
            outcome['reason'] = 'undecided' if not decision else 'model said keep'
            result['kept'] += 1
            result['outcomes'].append(outcome)
            continue

        fix = _fix_for(item, srt)
        if fix is None:
            item['applied'] = False
            item['rejected'] = item.get('rejected') or f'incomplete {decision}'
            outcome.update(status='rejected', reason=item['rejected'])
            result['rejected'] += 1
            result['outcomes'].append(outcome)
            continue

        snapshot = copy.deepcopy(cues)
        ok, message = _apply_one(cues, fix, item)
        if not ok:
            cues = snapshot
            item['applied'] = False
            item['rejected'] = message
            outcome.update(status='rejected', reason=message)
            result['rejected'] += 1
            result['outcomes'].append(outcome)
            continue

        item['applied'] = True
        item['rejected'] = ''
        outcome.update(status='applied', reason=message,
                       after=str(item.get('replacement') or '') or None)
        result['applied'] += 1
        result['outcomes'].append(outcome)

    if result['applied'] and not dry_run:
        backup_file(srt)
        write_subtitles(srt, cues)
    pack['stats'] = pack_mod.stats(pack)
    return result
