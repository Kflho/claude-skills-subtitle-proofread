#!/usr/bin/env python3
"""Detector-flagged failures → review items.

This is the AI entry point the skill actually defines: the automatic path
(scan triage, ``--repair``, glossary checks) clears what it can, and whatever
is **still flagged by a detector** becomes a review item.  Two sources:

- translation QA (``translated/zh/<EP>.qa.json``): ``japanese_residual``,
  ``echo`` / ``missing`` / ``empty``, ``glossary_miss``, ``too_long``, ``cps``.
  The QA item already carries the JA source and the bad Chinese target, so the
  model has everything it needs to rewrite one line.
- (scan findings are wired through the ASR pack; garbled cues that survive
  triage keep their ``garbled`` classification there.)

Items live in their own pack (``<EP>.zh_pack.json``) because they apply to a
different file than the ASR pack: the Chinese output, not the source SRT.
"""

from __future__ import annotations

import glob
import json
import os
import time

from lib.review import pack as pack_mod

KIND = 'translation'
DEFAULT_ZH_DIR = os.path.join('translated', 'zh')

# reasons that a rewrite can actually clear
TEXT_FIXABLE = {'japanese_residual', 'echo', 'empty', 'glossary_miss',
                'too_long', 'max_chars', 'cps', 'max_cps'}
# reasons that only need a different timing (not this stage's job)
NON_TEXT = {'timing'}


def classify_reasons(reasons) -> tuple[str, list[str]]:
    """Return (route, fixable_reasons) for a QA item."""
    fixable = [r for r in reasons if r in TEXT_FIXABLE]
    if fixable:
        return 'text', fixable
    return 'keep', [r for r in reasons if r in NON_TEXT]


def items_from_qa(qa: dict, zh_cues: list, *, span: int = 2) -> list[dict]:
    """Turn QA violations into pack items (``kind='translation'``)."""
    items = []
    for entry in qa.get('items') or []:
        index = entry.get('index')
        if not isinstance(index, int) or not (0 <= index < len(zh_cues)):
            continue
        cue = zh_cues[index]
        reasons = list(entry.get('reasons') or [])
        route, fixable = classify_reasons(reasons)
        start_s = float(cue.get('start_s', 0.0) or 0.0)
        end_s = float(cue.get('end_s', start_s) or start_s)
        items.append({
            'id': len(items),
            'cue_index': index,
            'kind': KIND,
            'route': route,
            'route_reason': 'detector: ' + ','.join(reasons or ['unknown']),
            'failure': fixable or reasons,
            'start': pack_mod._hms(start_s),
            'end': pack_mod._hms(end_s),
            'seconds': {'start': round(start_s, 3), 'end': round(end_s, 3)},
            'text': str(entry.get('target') or cue.get('text') or ''),
            'source_text': str(entry.get('source') or ''),
            'glossary_misses': list(entry.get('glossary_misses') or []),
            'asr_text': '',
            'similarity': 0.0,
            'local_similarity': 0.0,
            'window_shift': None,
            'length_ratio': 0.0,
            'start_error_ms': None,
            'end_error_ms': None,
            'reasons': reasons,
            'hint': 'translate',
            'suggested': {'new_start': None, 'new_end': None, 'new_text': ''},
            'context': pack_mod.context_at(zh_cues, index, span),
            'decision': '', 'new_start': None, 'new_end': None,
            'replacement': '', 'decided_by': '', 'why': '', 'rejected': '',
        })
    return items


def _episode_from_name(path: str) -> str:
    base = os.path.basename(path)
    for suffix in ('.qa.json', '.srt', '.json'):
        if base.endswith(suffix):
            return base[:-len(suffix)]
    return os.path.splitext(base)[0]


def collect_translation_failures(project_dir: str, *,
                                 zh_dir: str | None = None,
                                 log=None) -> list[tuple[str, str, list[dict]]]:
    """Find QA-flagged cues in ``translated/zh`` → [(episode, srt, items)]."""
    from lib.subtitle_io import read_subtitles

    zh_dir = zh_dir or os.path.join(project_dir, DEFAULT_ZH_DIR)
    out = []
    for qa_path in sorted(glob.glob(os.path.join(zh_dir, '*.qa.json'))):
        try:
            with open(qa_path, 'r', encoding='utf-8') as handle:
                qa = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        if not (qa.get('items') or []):
            continue
        episode = _episode_from_name(qa_path)
        srt_path = os.path.join(zh_dir, f'{episode}.srt')
        if not os.path.isfile(srt_path):
            continue
        zh_cues = read_subtitles(srt_path, mark_garbled=False)
        if not zh_cues:
            continue
        items = items_from_qa(qa, zh_cues)
        if items:
            out.append((episode, srt_path, items))
    return out


def attach_to_packs(project_dir: str, scan_dir: str, *,
                    zh_dir: str | None = None, log=None) -> list[str]:
    """Write/refresh one ``<EP>.zh_pack.json`` per episode with QA failures.

    Returns the pack paths.  Existing decisions are preserved for items whose
    cue/reason did not change, so reruns do not re-ask the model.
    """
    written = []
    for episode, srt_path, items in collect_translation_failures(
            project_dir, zh_dir=zh_dir, log=log):
        path = pack_mod.pack_path(scan_dir, episode, suffix='zh')
        existing = pack_mod.load_pack(path) or {}
        old = {(i.get('cue_index'), tuple(i.get('failure') or [])): i
               for i in existing.get('items') or []}
        for item in items:
            key = (item['cue_index'], tuple(item.get('failure') or []))
            previous = old.get(key)
            if previous and previous.get('decision'):
                item['decision'] = previous['decision']
                item['replacement'] = previous.get('replacement', '')
                item['decided_by'] = previous.get('decided_by', '')
                item['why'] = previous.get('why', '')
        pack = {
            'schema_version': pack_mod.SCHEMA_VERSION,
            'kind': pack_mod.KIND,
            'episode': episode,
            'srt': srt_path,
            'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'generator': {'source': 'translation-qa', 'dir': zh_dir or ''},
            'stats': {},
            'items': items,
        }
        pack['stats'] = pack_mod.stats(pack)
        written.append(pack_mod.save_pack(path, pack))
    return written
