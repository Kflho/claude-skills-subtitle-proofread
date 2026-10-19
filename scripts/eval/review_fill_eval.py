#!/usr/bin/env python3
"""Review-fill evaluation: does the AI review stage actually help?

Protocol
--------
1. Take the Japanese CC reference of one episode as ground truth.
2. Degrade it deterministically into the "subtitle we own" (timing jitter +
   dropped characters) — that is what a real imperfect subtitle looks like.
3. Align the degraded cues to the cached Qwen3 ASR result and plan repairs;
   apply the automatic (L0) fixes only.
4. Score that output (**no-AI arm**) against the CC gold.
5. Build the review pack from the gaps, let the review model fill it, apply
   the verified decisions, score again (**AI arm**).
6. Report the delta.  CC is used for scoring only — never as prompt input.

Usage
-----
    python scripts/eval/review_fill_eval.py \
        --episode EP001 \
        --cc temp/references/digimon/EP001.main.srt \
        --asr temp/asr/digimon_ep001_main.qwen3.json \
        --out-dir temp/eval/review_fill --limit-items 40 --seed 7
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import random
import shutil
import statistics
import sys
import time

import lib._path  # noqa: F401

from lib.alignment import AlignmentConfig, map_report
from lib.asr.base import ASRResult
from lib.review import apply as review_apply
from lib.review import fill as review_fill
from lib.review import pack as review_pack
from lib.subtitle_io import read_subtitles, write_subtitles
from fix.align_repair import (
    apply_repairs, plan_repairs, summarize_plan,
)


def _shift_cue(cue, delta):
    out = dict(cue)
    out['start_s'] = max(0.0, float(cue['start_s']) + delta)
    out['end_s'] = max(out['start_s'] + 0.3, float(cue['end_s']) + delta)
    return out


def _retime(cues, rng, share):
    """Jitter a share of the cues, keeping order and a minimum duration."""
    out = []
    last_start = -1.0
    for cue in cues:
        if rng.random() < share:
            delta = rng.choice([-1, 1]) * rng.uniform(0.3, 1.2)
            moved = _shift_cue(cue, delta)
            if moved['start_s'] <= last_start + 0.1:
                moved = dict(cue)
        else:
            moved = dict(cue)
        moved['start'] = _hms(moved['start_s'])
        moved['end'] = _hms(moved['end_s'])
        last_start = moved['start_s']
        out.append(moved)
    return out


def _corrupt_text(cues, rng, share):
    """Drop one interior character — an OCR/ASR-style single-character error."""
    out = []
    for cue in cues:
        item = dict(cue)
        text = str(item.get('text') or '')
        if rng.random() < share and len(text) >= 5 and ' ' not in text[1:-1]:
            pos = rng.randrange(1, len(text) - 1)
            item['text'] = text[:pos] + text[pos + 1:]
        out.append(item)
    return out


def _hms(seconds):
    total = int(round(float(seconds) * 1000))
    hours, rest = divmod(total, 3600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, ms = divmod(rest, 1000)
    return f'{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}'


def _ratio(a, b):
    return difflib.SequenceMatcher(None, str(a or ''), str(b or '')).ratio()


def score(gold: list, out: list, *, min_sim: float = 0.5) -> dict:
    """Greedy one-to-one match by text; report text + boundary metrics."""
    used = set()
    pairs = []
    for g_index, g_cue in enumerate(gold):
        g_text = str(g_cue.get('text') or '')
        best, best_sim = None, 0.0
        for o_index, o_cue in enumerate(out):
            if o_index in used:
                continue
            sim = _ratio(g_text, o_cue.get('text'))
            if sim > best_sim:
                best, best_sim = o_index, sim
        if best is not None and best_sim >= min_sim:
            used.add(best)
            o_cue = out[best]
            pairs.append({
                'gold_index': g_index,
                'out_index': best,
                'similarity': best_sim,
                'start_error_ms': 1000.0 * (float(o_cue.get('start_s', 0.0))
                                            - float(g_cue.get('start_s', 0.0))),
                'end_error_ms': 1000.0 * (float(o_cue.get('end_s', 0.0))
                                          - float(g_cue.get('end_s', 0.0))),
                'gold_text': g_text,
                'out_text': str(o_cue.get('text') or ''),
            })

    if not pairs:
        return {'matched': 0, 'gold': len(gold), 'out': len(out),
                'mean_similarity': 0.0, 'cer': 1.0,
                'start_abs_p50_ms': None, 'start_abs_p90_ms': None,
                'within_100ms': 0, 'within_300ms': 0, 'pairs': []}

    sims = [p['similarity'] for p in pairs]
    start_abs = sorted(abs(p['start_error_ms']) for p in pairs)
    return {
        'matched': len(pairs),
        'gold': len(gold),
        'out': len(out),
        'mean_similarity': round(statistics.fmean(sims), 4),
        'cer': round(1.0 - statistics.fmean(sims), 4),
        'start_abs_p50_ms': round(_pct(start_abs, 50), 1),
        'start_abs_p90_ms': round(_pct(start_abs, 90), 1),
        'within_100ms': sum(1 for x in start_abs if x <= 100),
        'within_300ms': sum(1 for x in start_abs if x <= 300),
        'pairs': pairs,
    }


def _pct(values, pct):
    if not values:
        return 0.0
    rank = (len(values) - 1) * pct / 100.0
    low = int(rank)
    high = min(low + 1, len(values) - 1)
    frac = rank - low
    return values[low] * (1 - frac) + values[high] * frac


def compare(arm_a: dict, arm_b: dict) -> dict:
    """Per-cue delta between the two arms, matched by gold cue."""
    by_gold_a = {p['gold_index']: p for p in arm_a.get('pairs', [])}
    by_gold_b = {p['gold_index']: p for p in arm_b.get('pairs', [])}
    improved = worsened = changed = 0
    for gold_index, a in by_gold_a.items():
        b = by_gold_b.get(gold_index)
        if b is None:
            continue
        delta_time = (abs(a['start_error_ms']) - abs(b['start_error_ms']))
        delta_text = b['similarity'] - a['similarity']
        if a['out_text'] != b['out_text'] or abs(delta_time) > 1:
            changed += 1
            # a cue counts as improved only if it is strictly better on the
            # axis that moved and not worse on the other.
            better_time = delta_time > 50
            better_text = delta_text > 0.02
            worse_time = delta_time < -50
            worse_text = delta_text < -0.02
            if (better_time and not worse_text) or (better_text and not worse_time):
                improved += 1
            elif worse_time or worse_text:
                worsened += 1
    return {'changed': changed, 'improved': improved, 'worsened': worsened}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--episode', default='EP001')
    parser.add_argument('--cc', required=True, help='CC reference SRT (gold)')
    parser.add_argument('--asr', required=True, help='cached ASRResult JSON')
    parser.add_argument('--out-dir', default=os.path.join('temp', 'eval', 'review_fill'))
    parser.add_argument('--limit-items', type=int, default=40,
                        help='max gaps sent to the review model (0 = all)')
    parser.add_argument('--seed', type=int, default=7)
    parser.add_argument('--degrade-time', type=float, default=0.40)
    parser.add_argument('--degrade-text', type=float, default=0.25)
    parser.add_argument('--audio-offset', type=float, default=95.0,
                        help='seconds added to ASR times so they share the '
                             'subtitle time base (main-window ASR starts at 0)')
    parser.add_argument('--skip-llm', action='store_true')
    parser.add_argument('--actions', default='keep',
                        help="comma list of what the model may do: "
                             "keep / delete / rewrite (default: keep, triage)")
    args = parser.parse_args(argv)

    os.makedirs(args.out_dir, exist_ok=True)
    rng = random.Random(args.seed)

    gold = read_subtitles(args.cc, mark_garbled=False)
    degraded = _corrupt_text(_retime(gold, rng, args.degrade_time),
                             rng, args.degrade_text)
    input_path = os.path.join(args.out_dir, 'input.srt')
    write_subtitles(input_path, degraded)

    with open(args.asr, 'r', encoding='utf-8') as handle:
        asr_result = ASRResult.from_dict(json.load(handle))
    if args.audio_offset:
        # The cached main-window ASR starts at 0 while the CC keeps episode
        # timecodes; without this the two sides are ~95 s apart and every cue
        # looks like an unmatched line.
        for segment in asr_result.segments:
            segment.start += args.audio_offset
            segment.end += args.audio_offset
            for token in getattr(segment, 'tokens', []) or []:
                token.start += args.audio_offset
                token.end += args.audio_offset
        for token in getattr(asr_result, 'tokens', []) or []:
            token.start += args.audio_offset
            token.end += args.audio_offset

    cfg = AlignmentConfig()
    cues = read_subtitles(input_path, mark_garbled=False)
    aligned = map_report(cues, asr_result, config=cfg, language='ja')
    plan = plan_repairs(cues, aligned.alignments, config=cfg)
    stats = summarize_plan(plan)
    auto_cues, apply_stats = apply_repairs(cues, plan, config=cfg)
    auto_path = os.path.join(args.out_dir, 'arm_none.srt')
    write_subtitles(auto_path, auto_cues)

    gaps = [p for p in plan if not p.auto]
    pack = review_pack.build_pack(
        args.episode, auto_cues, aligned.alignments, gaps,
        srt_path=auto_path,
        generator={'engine': aligned.engine, 'model': aligned.model,
                   'eval': 'degraded-cc', 'seed': args.seed},
        auto_count=stats['auto'],
        segments=getattr(asr_result, 'segments', None))
    pack_file = review_pack.save_pack(
        review_pack.pack_path(os.path.join(args.out_dir, 'temp', 'scans'),
                              args.episode), pack)

    report = {
        'episode': args.episode,
        'seed': args.seed,
        'degrade': {'time_share': args.degrade_time,
                    'text_share': args.degrade_text},
        'actions': args.actions,
        'audio_offset': args.audio_offset,
        'input_cues': len(cues),
        'gold_cues': len(gold),
        'plan': stats,
        'apply_stats': apply_stats,
        'gaps': len(gaps),
        'routed': pack['stats'].get('routed', 0),
        'routes': pack['stats'].get('routes', {}),
        'pack': pack_file,
        'arms': {},
    }

    base = score(gold, auto_cues)
    base_against_input = score(gold, cues)
    report['arms']['none'] = {k: v for k, v in base.items() if k != 'pairs'}
    report['arms']['input'] = {k: v for k, v in base_against_input.items()
                               if k != 'pairs'}

    if not args.skip_llm:
        llm_pack = review_pack.load_pack(pack_file)
        limit = args.limit_items if args.limit_items > 0 else None
        started = time.time()
        fill_stats = review_fill.fill_pack(
            llm_pack, max_items=limit,
            allowed_actions=set(args.actions.split(',')))
        llm_path = os.path.join(args.out_dir, 'arm_llm.srt')
        shutil.copyfile(auto_path, llm_path)
        llm_pack['srt'] = llm_path
        apply_result = review_apply.apply_pack(llm_pack)
        review_pack.save_pack(pack_file, llm_pack)
        report['fill'] = fill_stats
        report['apply'] = {k: apply_result[k] for k in
                           ('applied', 'rejected', 'kept', 'error')}
        report['elapsed_s'] = round(time.time() - started, 1)
        report['arms']['llm'] = {k: v for k, v in score(gold, read_subtitles(
            llm_path, mark_garbled=False)).items() if k != 'pairs'}
        report['delta'] = compare(base, score(gold, read_subtitles(
            llm_path, mark_garbled=False)))

    with open(os.path.join(args.out_dir, 'report.json'), 'w',
              encoding='utf-8', newline='\n') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write('\n')

    print(f"[eval] {args.episode}: {len(gold)} gold cues, {len(cues)} input, "
          f"{stats['auto']} auto, {len(gaps)} gaps", file=sys.stderr)
    for arm in ('input', 'none', 'llm'):
        data = report['arms'].get(arm)
        if not data:
            continue
        print(f"  {arm:5s} matched {data['matched']:3d}  "
              f"sim {data['mean_similarity']:.3f}  CER {data['cer']:.3f}  "
              f"|start| P50 {data['start_abs_p50_ms']} P90 {data['start_abs_p90_ms']}"
              f"  <=100ms {data['within_100ms']}  <=300ms {data['within_300ms']}",
              file=sys.stderr)
    if 'delta' in report:
        delta = report['delta']
        print(f"  AI changed {delta['changed']} cues: "
              f"{delta['improved']} improved / {delta['worsened']} worsened",
              file=sys.stderr)
    print(f"[eval] wrote {os.path.join(args.out_dir, 'report.json')}",
          file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
