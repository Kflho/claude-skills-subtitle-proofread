#!/usr/bin/env python3
"""Review stage: fill the gaps and apply them, inside the normal pipeline.

This replaces the old hand-off (write a worksheet → print "Claude, fill it
in" → run ``asr_review_to_fixes.py`` → rerun with ``--resume``).  The stage
runs as an ordinary pipeline step:

1. every ``temp/scans/<EP>.ai_pack.json`` is loaded;
2. undecided gaps are filled by the review model (``lib.review.fill``);
3. decisions are applied to the SRT with per-cue verification
   (``lib.review.apply``);
4. the pack is written back (decisions + rejections), an ``ai_applied`` log
   is written next to it, and only real changes reach the report.

There is no "pending human review" queue: a gap the model cannot decide, or
whose decision fails verification, stays ``keep`` and is counted as such.
"""

from __future__ import annotations

import glob
import json
import os
import sys

from lib.review import apply as apply_mod
from lib.review import fill as fill_mod
from lib.review import pack as pack_mod

STATUS_APPLIED = '✅'
STATUS_DELETED = '🗑️'
REPORT_STEP = '2'


def find_packs(scan_dir: str) -> list[str]:
    return sorted(glob.glob(os.path.join(scan_dir, '*.ai_pack.json')) +
                  glob.glob(os.path.join(scan_dir, '*.zh_pack.json')))


def _report_entries(pack: dict, outcomes: list[dict]) -> list[dict]:
    """Only real changes go to the report — keeps are machine-only."""
    by_id = {item.get('id'): item for item in pack.get('items') or []}
    entries = []
    for outcome in outcomes:
        if outcome.get('status') != 'applied':
            continue
        item = by_id.get(outcome.get('id')) or {}
        decision = outcome.get('decision')
        corrected = ''
        status = STATUS_APPLIED
        if decision == 'set_time':
            corrected = (f'（AI 复核：时间轴 {item.get("start")} → '
                         f'{item.get("new_start")} / {item.get("new_end")}）')
        elif decision == 'replace_text':
            corrected = str(item.get('replacement') or '')[:120]
        elif decision == 'delete_line':
            corrected = str(item.get('why') or '（AI 复核：噪声/重复）')[:120]
            status = STATUS_DELETED
        entries.append({
            'ep': pack.get('episode', ''),
            'time': item.get('start', outcome.get('start', '')),
            'original': str(outcome.get('before') or '')[:120],
            'corrected': corrected,
            'status': status,
        })
    return entries


def run_review_stage(project_dir: str, *, scan_dir: str | None = None,
                     fill: bool = True, chat=None, dry_run: bool = False,
                     update_report: bool = True, max_items: int | None = None,
                     allowed_actions: dict | set | None = None,
                     attach_failures: bool = True,
                     log=sys.stderr) -> dict:
    """Run the fill+apply stage over every pack in ``project_dir``."""
    scan_dir = scan_dir or os.path.join(project_dir, 'temp', 'scans')
    if allowed_actions is None:
        from lib.config import REVIEW_ACTIONS, REVIEW_TRANSLATION_ACTIONS
        allowed_actions = {
            'asr_repair': set(REVIEW_ACTIONS),
            'translation': set(REVIEW_TRANSLATION_ACTIONS),
        }
    if attach_failures:
        try:
            from lib.review.failures import attach_to_packs
            added = attach_to_packs(project_dir, scan_dir)
            if added:
                print(f'[review] {len(added)} translation pack(s) from QA',
                      file=log)
        except Exception as exc:  # never kill the run for a missing QA dir
            print(f'[review] translation failures skipped: {exc}', file=log)
    summary = {
        'episodes': 0, 'packs': 0, 'gaps': 0, 'filled': 0, 'applied': 0,
        'rejected': 0, 'kept': 0, 'undecided': 0, 'errors': [],
        'allowed_actions': ({k: sorted(v) for k, v in allowed_actions.items()}
                            if isinstance(allowed_actions, dict)
                            else sorted(allowed_actions)),
        'details': [],
    }
    paths = find_packs(scan_dir)
    if not paths:
        return summary

    report_path = os.path.join(project_dir, 'reports', '问题解决报告.md')

    for path in paths:
        pack = pack_mod.load_pack(path)
        if not pack:
            summary['errors'].append(f'{path}: unreadable pack')
            continue
        summary['packs'] += 1
        summary['episodes'] += 1
        pending = pack_mod.pending_items(pack)
        if pending and fill:
            fill_stats = fill_mod.fill_pack(pack, chat=chat,
                                            max_items=max_items,
                                            allowed_actions=allowed_actions,
                                            log=log)
            summary['filled'] += fill_stats['filled']
        elif pending:
            summary['undecided'] += len(pending)

        result = apply_mod.apply_pack(pack, dry_run=dry_run)
        if result.get('error'):
            summary['errors'].append(f'{pack.get("episode")}: {result["error"]}')
        summary['gaps'] += len(pack.get('items') or [])
        summary['applied'] += result['applied']
        summary['rejected'] += result['rejected']
        summary['kept'] += result['kept']

        pack_mod.save_pack(path, pack)
        suffix = 'zh' if path.endswith('.zh_pack.json') else 'ai'
        log_path = pack_mod.applied_path(
            scan_dir, str(pack.get('episode') or 'unknown'), suffix=suffix)
        try:
            with open(log_path, 'w', encoding='utf-8', newline='\n') as handle:
                json.dump({
                    'schema_version': 1,
                    'kind': 'review_applied',
                    'episode': pack.get('episode'),
                    'srt': pack.get('srt'),
                    'stats': pack.get('stats'),
                    'summary': {k: result.get(k) for k in
                                ('applied', 'rejected', 'kept', 'error')},
                    'outcomes': result.get('outcomes', []),
                }, handle, ensure_ascii=False, indent=2)
                handle.write('\n')
        except OSError as exc:
            summary['errors'].append(f'{pack.get("episode")}: log write {exc}')

        if update_report and not dry_run:
            entries = _report_entries(pack, result.get('outcomes', []))
            if entries:
                try:
                    from utils.update_report import upsert_entries
                    upsert_entries(report_path, step=REPORT_STEP, entries=entries)
                except Exception as exc:  # report must never kill the run
                    summary['errors'].append(f'report: {exc}')

        summary['details'].append({
            'episode': pack.get('episode'),
            'gaps': len(pack.get('items') or []),
            'stats': pack.get('stats'),
            'applied': result['applied'],
            'rejected': result['rejected'],
        })

    print(f'[review] {summary["packs"]} pack(s): {summary["gaps"]} gaps, '
          f'{summary["filled"]} filled, {summary["applied"]} applied, '
          f'{summary["rejected"]} rejected, {summary["kept"]} kept',
          file=log)
    for error in summary['errors']:
        print(f'  [review] {error}', file=log)
    return summary
