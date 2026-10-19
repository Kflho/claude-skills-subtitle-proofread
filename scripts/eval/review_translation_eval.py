#!/usr/bin/env python3
"""Translation-failure evaluation: can the review stage clear QA failures?

The automatic repair pass handles what rules can see.  This test measures the
*remaining* class the skill hands to AI: QA-flagged lines (Japanese residue,
echo/untranslated, empty).  It injects known failures into a real Chinese
output, runs the same detector the pipeline uses, lets the review stage fix
them, and reports how many were cleared and how close the fix is to the
original (known-good) translation.

Usage:
    python scripts/eval/review_translation_eval.py \
        --zh temp/translation_eval/EP001.full.baseline.srt \
        --ja temp/references/digimon/EP001.main.srt \
        --out-dir temp/eval/tr_fill --inject 30 --seed 3
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import random
import shutil
import sys
import time

import lib._path  # noqa: F401

from lib.review import apply as review_apply
from lib.review import fill as review_fill
from lib.review import failures as review_failures
from lib.review import pack as review_pack
from lib.subtitle_io import read_subtitles, write_subtitles
from lib.translation.qa import count_kana, qa_report


def _inject(zh_cues: list, ja_cues: list, rng, count: int) -> set:
    """Replace N Chinese lines with their Japanese source (residue + echo)."""
    candidates = [i for i, cue in enumerate(zh_cues)
                  if i < len(ja_cues) and str(cue.get('text') or '').strip()
                  and str(ja_cues[i].get('text') or '').strip()
                  and not count_kana(cue.get('text') or '')]
    picked = set(rng.sample(candidates, min(count, len(candidates))))
    for index in picked:
        zh_cues[index] = dict(zh_cues[index],
                              text=str(ja_cues[index].get('text') or ''))
    return picked


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a or '', b or '').ratio()


def _write_qa(zh_dir: str, episode: str, ja_cues, zh_cues, glossary=None):
    qa = qa_report(ja_cues, zh_cues, glossary=glossary or {})
    path = os.path.join(zh_dir, f'{episode}.qa.json')
    with open(path, 'w', encoding='utf-8', newline='\n') as handle:
        json.dump(qa, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    return qa


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--zh', required=True, help='known-good Chinese SRT')
    parser.add_argument('--ja', required=True, help='Japanese source SRT')
    parser.add_argument('--episode', default='EP001')
    parser.add_argument('--out-dir', default=os.path.join('temp', 'eval', 'tr_fill'))
    parser.add_argument('--inject', type=int, default=30)
    parser.add_argument('--seed', type=int, default=3)
    parser.add_argument('--limit-items', type=int, default=0)
    parser.add_argument('--actions', default='keep,rewrite')
    parser.add_argument('--skip-llm', action='store_true')
    args = parser.parse_args(argv)

    zh_cues = read_subtitles(args.zh, mark_garbled=False)
    ja_cues = read_subtitles(args.ja, mark_garbled=False)
    rng = random.Random(args.seed)
    injected = _inject(zh_cues, ja_cues, rng, args.inject)

    zh_dir = os.path.join(args.out_dir, 'translated', 'zh')
    os.makedirs(zh_dir, exist_ok=True)
    zh_path = os.path.join(zh_dir, f'{args.episode}.srt')
    write_subtitles(zh_path, zh_cues)
    qa = _write_qa(zh_dir, args.episode, ja_cues, zh_cues)

    before = {'violations': len(qa['items']),
              'japanese_residual': qa['summary']['japanese_residual'],
              'echo': qa['summary']['echo'],
              'kana_chars': sum(count_kana(str(c.get('text') or ''))
                                for c in zh_cues)}

    packs = review_failures.attach_to_packs(args.out_dir,
                                            os.path.join(args.out_dir, 'temp', 'scans'))
    pack = review_pack.load_pack(packs[0]) if packs else None
    report = {
        'episode': args.episode, 'seed': args.seed,
        'injected': len(injected), 'before': before,
        'items': len(pack['items']) if pack else 0,
    }
    if pack is None:
        with open(os.path.join(args.out_dir, 'report.json'), 'w',
                  encoding='utf-8', newline='\n') as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        print(f'[eval] no QA failures to fix (injected {len(injected)})',
              file=sys.stderr)
        return 0

    if not args.skip_llm:
        started = time.time()
        fill_stats = review_fill.fill_pack(
            pack, max_items=args.limit_items or None,
            allowed_actions={'translation': set(args.actions.split(','))})
        apply_result = review_apply.apply_pack(pack)
        review_pack.save_pack(packs[0], pack)
        report['fill'] = fill_stats
        report['apply'] = {k: apply_result[k] for k in
                           ('applied', 'rejected', 'kept', 'error')}
        report['elapsed_s'] = round(time.time() - started, 1)

        fixed = read_subtitles(zh_path, mark_garbled=False)
        qa_after = _write_qa(zh_dir, args.episode, ja_cues, fixed)
        report['after'] = {
            'violations': len(qa_after['items']),
            'japanese_residual': qa_after['summary']['japanese_residual'],
            'echo': qa_after['summary']['echo'],
            'kana_chars': sum(count_kana(str(c.get('text') or ''))
                              for c in fixed),
        }
        report['cleared'] = max(0, before['violations']
                                - report['after']['violations'])
        # how close the fix is to the known-good translation, and did anything
        # outside the injected set change?
        sims, touched_outside = [], 0
        for i, (orig, now) in enumerate(zip(read_subtitles(args.zh,
                                                         mark_garbled=False),
                                            fixed)):
            if str(orig.get('text')) != str(now.get('text')):
                if i not in injected:
                    touched_outside += 1
                if i in injected:
                    sims.append(_similarity(orig.get('text'), now.get('text')))
        report['similarity_to_reference'] = (
            round(sum(sims) / len(sims), 4) if sims else 0.0)
        report['changed_outside_injected'] = touched_outside

    with open(os.path.join(args.out_dir, 'report.json'), 'w',
              encoding='utf-8', newline='\n') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write('\n')

    print(f"[eval] {args.episode}: injected {len(injected)} residue lines, "
          f"QA items {before['violations']}", file=sys.stderr)
    if 'after' in report:
        print(f"  after review: {report['after']['violations']} violations "
              f"(cleared {report['cleared']}), kana {before['kana_chars']} → "
              f"{report['after']['kana_chars']}", file=sys.stderr)
        print(f"  similarity to reference {report['similarity_to_reference']}, "
              f"changed outside injected: {report['changed_outside_injected']}",
              file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
