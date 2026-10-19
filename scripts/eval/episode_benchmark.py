#!/usr/bin/env python3
"""Held-out episode benchmark: ASR text + token calendar + cue alignment.

Why this exists: `asr_eval.py` scores a *full* transcription against a
reference, and `diagnose_timeline.py` inspects timestamps without any
reference.  This harness is the release gate that combines both on the
main-story window, and it is deliberately **read-only**:

* it never writes thresholds, config or manifests;
* it reports dev/test splits separately (see ``eval/protocol.md``);
* it does not decide whether a number is "good" — the protocol does.

That keeps the Japanese CC a *benchmark*, not a tuning input.  Parameters
must come from audio/ASR-internal evidence or a stated prior; the reference
only measures the outcome.

Besides CER and boundary percentiles the report now carries the measurement
validity blocks (added round 8, see ``eval/protocol.md`` §2.1): signed start
error (bias vs spread), a text decomposition (character coverage +
matched-span CER), P90 attribution flags, and timeline holes from
``alignment.quality.lost_spans``.  None of them feeds a threshold.

Manifest additions (on top of ``asr_eval.py``):

    {"id": "EP002", "split": "test", "main_window": [95, 1233],
     "asr_json": "temp/asr/digimon_ep002_main.qwen3.json"}

Usage:
    python scripts/eval/episode_benchmark.py \\
        --manifest eval/manifest.digimon.json --asr-json-dir temp/asr \\
        --output reports/benchmark.md --json-out reports/benchmark.json

    # Transcribe the missing main-window audio with Qwen3:
    ... --transcribe --engine qwen3 --python .venv-qwen\\Scripts\\python.exe
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from eval.asr_eval import compute_cer, normalize_text  # noqa: E402
from fix.align_repair import (  # noqa: E402
    estimate_global_offset, plan_repairs, shift_cues, summarize_plan,
)
from lib.alignment.base import AlignmentConfig  # noqa: E402
from lib.alignment.cue_mapper import map_report  # noqa: E402
from lib.alignment.diagnostics import boundary_stats  # noqa: E402
from lib.alignment.quality import (  # noqa: E402
    TimelinePolicy, assess_units, lost_spans, unit_triples,
)
from lib.alignment.timeline import build_char_timeline  # noqa: E402
from lib.asr.base import ASRConfig, ASRResult  # noqa: E402
from lib.asr.router import ASRRouter  # noqa: E402
from lib.config import (  # noqa: E402
    ALIGNER_MODEL, ASR_DEVICE, ASR_DTYPE, ASR_MODEL, QWEN_PYTHON,
    WHISPER_MODEL,
)
from lib.subtitle_io import read_subtitles  # noqa: E402


# ═══════════════════════════════════════════════════════════════
# Reference window
# ═══════════════════════════════════════════════════════════════

def window_cues(cues: list[dict], window: list[float] | tuple[float, float]
                ) -> list[dict]:
    """Keep cues overlapping ``[start, end]`` and rebase them to zero.

    Cues crossing a window edge are clamped, not dropped: the benchmark
    should not silently ignore a line because the cut fell mid-cue.
    """
    start, end = float(window[0]), float(window[1])
    if end <= start:
        raise ValueError(f'bad main_window: {window!r}')
    out = []
    for cue in cues:
        c_start = float(cue.get('start_s', 0.0) or 0.0)
        c_end = float(cue.get('end_s', c_start) or c_start)
        if c_end <= start or c_start >= end:
            continue
        item = dict(cue)
        item['start_s'] = max(0.0, c_start - start)
        item['end_s'] = max(item['start_s'], min(end, c_end) - start)
        out.append(item)
    return out


def ref_text(cues: list[dict]) -> str:
    return ''.join(str(cue.get('text', '') or '') for cue in cues)


# ═══════════════════════════════════════════════════════════════
# Loading / transcribing
# ═══════════════════════════════════════════════════════════════

def _cached_asr_path(ep: dict, asr_dir: str) -> Path:
    if ep.get('asr_json'):
        return Path(ep['asr_json'])
    sub = Path(asr_dir) if asr_dir else Path('temp/asr')
    return sub / f"{ep.get('id', 'EP')}_main.qwen3.json"


def load_asr(ep: dict, asr_dir: str) -> ASRResult:
    path = _cached_asr_path(ep, asr_dir)
    if not path.exists():
        raise FileNotFoundError(f'ASR JSON not found: {path}')
    payload = json.loads(path.read_text(encoding='utf-8-sig'))
    return ASRResult.from_dict(payload)


def extract_main_audio(video: str, window: list[float], out: str) -> str:
    """Cut the main-story window to a 16 kHz mono WAV (ffmpeg)."""
    start, end = float(window[0]), float(window[1])
    duration = max(0.05, end - start)
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    from lib.whisper_utils import extract_audio_wav
    extract_audio_wav(video, str(out_path), ss=start, duration=duration,
                      prefer_lang='ja')
    return str(out_path)


def transcribe_episode(router: ASRRouter, ep: dict, *, window: list[float],
                       language: str, work_dir: str) -> tuple[ASRResult, float]:
    video = ep.get('video')
    audio = ep.get('audio')
    if not audio:
        if not video or not os.path.exists(video):
            raise FileNotFoundError(f"{ep.get('id')}: video/audio not found")
        audio = extract_main_audio(video, window,
                                   str(Path(work_dir) /
                                       f"{ep.get('id', 'EP')}_main.wav"))
    started = time.time()
    result = router.transcribe(audio, language=language,
                               context=ep.get('context', ''),
                               timestamps=bool(ep.get('timestamps', True)))
    return result, time.time() - started


# ═══════════════════════════════════════════════════════════════
# Metrics
# ═══════════════════════════════════════════════════════════════

def _quality(result: ASRResult) -> dict:
    tokens = assess_units(unit_triples(result.tokens), source='tokens')
    segments = assess_units(unit_triples(result.segments), source='segments')
    return {
        'tokens': tokens.to_dict(),
        'segments': segments.to_dict(),
    }


# ═══════════════════════════════════════════════════════════════
# Measurement validity: dialogue-covered CER + P90 attribution
# ═══════════════════════════════════════════════════════════════
#
# The plain CER denominator is *all* reference text, including signs, song
# lyrics and speaker labels the ASR was never asked to transcribe.  Those
# show up as deletions and inflate the number.  ``dialogue_cer`` therefore
# re-scores the same hypothesis against only those reference cues that have
# a character match in the ASR; comparing it with the global CER says how
# much of the error is reference text with no ASR counterpart.
#
# ``explain_p90`` answers the *other* half of the question: when the P90
# start error is large, what kind of cue produced it (collapsed FA span,
# 1–2 character particle, stretched span, repeated/ambiguous line)?
# It is read-only diagnosis, never a repair input.
#
# ``signed_start_stats`` exposes the systematic part: against Japanese CC
# (the boundary test standard, user decision) the ASR start is consistently
# ~0.4 s earlier on EP02/03 — an alignment defect, not reference noise.
# Absolute P50/P90 alone hide that, so the sign and the early share are
# reported next to them.  Nothing is subtracted from the output: an offset
# estimated from the same cues would be self-confirming, and raw stays the
# gate.

def _pct(values: list[float], p: float) -> float:
    values = sorted(values)
    if not values:
        return 0.0
    pos = (len(values) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] * (1 - (pos - lo)) + values[hi] * (pos - lo)


def dialogue_cer(cues: list[dict], timeline, alignments: list, *,
                 cover_similarity: float = 0.5,
                 match_similarity: float = 0.85) -> dict:
    """Text decomposition: how much CC text has an ASR counterpart?

    The plain CER counts every reference character, including signs, song
    lyrics and lines the mapper never matched, against the whole ASR text.
    This function splits the reference instead:

    * ``match`` (similarity ≥ 0.85): CER inside the matched ASR span;
    * ``covered`` (similarity ≥ 0.5): same, for partially matched dialogue;
    * ``unsupported``: cues with no character match at all.

    The hypothesis of each bucket is the ASR characters the mapper actually
    placed under the cue, so the score describes *text mismatch inside
    covered dialogue* and does not re-count context as insertions.  The
    global CER keeps the insertions/deletions view; the two are meant to be
    read together.
    """
    buckets = {
        'covered': {'distance': 0, 'ref_chars': 0, 'cues': 0},
        'match': {'distance': 0, 'ref_chars': 0, 'cues': 0},
    }
    total_ref = unsupported_ref = matched_chars = 0
    unsupported = 0
    for cue, al in zip(cues, alignments):
        text = str(cue.get('text', '') or '')
        ref_chars = len(normalize_text(text))
        if not ref_chars:
            continue
        total_ref += ref_chars
        matched_chars += min(al.matched_chars, ref_chars)
        if al.similarity < cover_similarity or al.char_span is None:
            unsupported += 1
            unsupported_ref += ref_chars
            continue
        detail = compute_cer(text, timeline.slice_text(*al.char_span))
        for key in ('covered', 'match'):
            if key == 'match' and al.similarity < match_similarity:
                continue
            buckets[key]['distance'] += detail['distance']
            buckets[key]['ref_chars'] += detail['ref_chars']
            buckets[key]['cues'] += 1

    def _rate(bucket):
        return (round(bucket['distance'] / bucket['ref_chars'], 4)
                if bucket['ref_chars'] else None)

    return {
        'kind': 'matched-span',
        'cover_similarity': cover_similarity,
        'match_similarity': match_similarity,
        'covered_cues': buckets['covered']['cues'],
        'match_cues': buckets['match']['cues'],
        'covered_ref_chars': buckets['covered']['ref_chars'],
        'covered_distance': buckets['covered']['distance'],
        'covered_cer': _rate(buckets['covered']),
        'match_ref_chars': buckets['match']['ref_chars'],
        'match_distance': buckets['match']['distance'],
        'match_cer': _rate(buckets['match']),
        'unsupported_cues': unsupported,
        'unsupported_ref_chars': unsupported_ref,
        'ref_chars_total': total_ref,
        'matched_chars': matched_chars,
        'char_coverage': (round(matched_chars / total_ref, 3)
                          if total_ref else 0.0),
    }


def error_bands(alignments: list) -> dict:
    """Distribution of absolute start errors over matched cues."""
    starts = [abs(al.start_error) for al in alignments
              if al.status == 'match' and al.start_error is not None]
    bands = {'n': len(starts), 'le_100': 0, 'le_200': 0, 'le_300': 0,
             'le_500': 0, 'gt_500': 0}
    for value in starts:
        if value <= 0.1:
            bands['le_100'] += 1
        if value <= 0.2:
            bands['le_200'] += 1
        if value <= 0.3:
            bands['le_300'] += 1
        if value <= 0.5:
            bands['le_500'] += 1
        else:
            bands['gt_500'] += 1
    return bands


def signed_start_stats(alignments: list) -> dict:
    """Signed start-error distribution — separates bias from spread.

    ``signed`` keeps the direction (negative = ASR starts before the cue).
    Against the CC test standard a tight distribution centred on −0.4 s is
    a systematic alignment defect, not per-cue noise; the absolute P50/P90
    alone cannot tell the two apart.
    """
    values = [al.start_error for al in alignments
              if al.status == 'match' and al.start_error is not None]
    if not values:
        return {'n': 0, 'early': 0, 'early_share': 0.0,
                'p10_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0,
                'spread_ms': 0.0, 'abs_p50_ms': 0.0, 'abs_p90_ms': 0.0}
    p10, p50, p90 = (_pct(values, 0.1), _pct(values, 0.5), _pct(values, 0.9))
    early = sum(1 for v in values if v < 0)
    abs_values = [abs(v) for v in values]
    return {
        'n': len(values),
        'early': early,
        'early_share': round(early / len(values), 3),
        'p10_ms': round(1000 * p10, 1),
        'p50_ms': round(1000 * p50, 1),
        'p90_ms': round(1000 * p90, 1),
        'spread_ms': round(1000 * (p90 - p10), 1),
        'abs_p50_ms': round(1000 * _pct(abs_values, 0.5), 1),
        'abs_p90_ms': round(1000 * _pct(abs_values, 0.9), 1),
    }


def outlier_flags(al, cue_norm: str, timeline_text: str,
                  cfg: AlignmentConfig) -> list[str]:
    """Explain one large start error with cheap, model-free signals."""
    flags = []
    aligned = al.aligned_duration
    if (al.start is not None and al.end is not None
            and al.end - al.start <= 1e-3):
        flags.append('zero_span')
    elif al.cue_duration >= 0.5 and aligned < 0.15:
        flags.append('collapsed')
    elif aligned > max(1.0, 2.0 * al.cue_duration):
        flags.append('stretched')
    if al.cue_chars <= 2:
        flags.append('short_cue')
    if al.confidence == 'low':
        flags.append('low_confidence')
    if float((al.signals or {}).get('repeat_score', 0.0)) > cfg.max_repeat_score:
        flags.append('repeat')
    if cue_norm and timeline_text and timeline_text.count(cue_norm) > 1:
        flags.append('ambiguous')
    return flags


def explain_p90(cues: list[dict], alignments: list, timeline=None, *,
                bound_ms: float = 300.0, limit: int = 10,
                cfg: AlignmentConfig | None = None) -> dict:
    """Attribute the largest start errors to their likely mechanism.

    ``bound_ms`` is the P2 acceptance bound (300 ms), not a tuned value.
    A cue can carry several flags; ``plain`` counts cues whose error has no
    cheap explanation (the ones worth investigating by hand).
    """
    cfg = cfg or AlignmentConfig()
    bound = bound_ms / 1000.0
    timeline_text = timeline.text if timeline is not None else ''
    cue_norm = [normalize_text(str(c.get('text', '') or '')) for c in cues]
    counts = {key: 0 for key in
              ('zero_span', 'collapsed', 'stretched', 'short_cue',
               'low_confidence', 'repeat', 'ambiguous', 'plain')}
    matched = 0
    examples: list[dict] = []
    for al in alignments:
        if al.status != 'match' or al.start_error is None:
            continue
        matched += 1
        if abs(al.start_error) <= bound:
            continue
        norm = (cue_norm[al.cue_index]
                if 0 <= al.cue_index < len(cue_norm) else '')
        raw = (str(cues[al.cue_index].get('text', '') or '')
               if 0 <= al.cue_index < len(cues) else '')
        flags = outlier_flags(al, norm, timeline_text, cfg)
        if flags:
            for flag in flags:
                counts[flag] += 1
        else:
            counts['plain'] += 1
        examples.append({
            'cue_index': al.cue_index,
            'start_error_ms': round(1000 * al.start_error, 1),
            'cue_start': round(al.cue_start, 3),
            'cue_chars': al.cue_chars,
            'similarity': round(al.similarity, 3),
            'confidence': al.confidence,
            'flags': flags,
            'cue_text': raw[:60],
            'asr_text': (al.asr_text or '')[:60],
        })
    examples.sort(key=lambda item: abs(item['start_error_ms']), reverse=True)
    over = len(examples)
    return {
        'bound_ms': bound_ms,
        'matched': matched,
        'n_over': over,
        'share_over': round(over / matched, 3) if matched else 0.0,
        'flags': counts,
        'examples': examples[:limit],
    }


def evaluate_episode(ep: dict, result: ASRResult, *,
                     elapsed_s: float | None = None,
                     policy: TimelinePolicy | None = None,
                     apply_global_offset: bool = False) -> dict:
    """Score one cached/transcribed ASR result.  Pure measurement.

    ``apply_global_offset`` is a *diagnostic*: it estimates one consistent
    file-level drift from the cue↔ASR matches, shifts all cues, then
    re-maps.  Production ``fix_by_asr`` no longer does this by default
    (raw is the gate), so the flag exists only to show what a whole-file
    shift would do.
    """
    ep_id = ep.get('id', '?')
    ref_path = ep.get('reference_srt')
    if not ref_path or not os.path.exists(ref_path):
        raise FileNotFoundError(f'{ep_id}: reference_srt not found: '
                                f'{ref_path!r}')
    window = ep.get('main_window')
    cues = read_subtitles(ref_path, mark_garbled=False)
    if window:
        cues = window_cues(cues, window)
    if not cues:
        raise ValueError(f'{ep_id}: no reference cues in main_window={window!r}')

    cer = compute_cer(ref_text(cues), result.text)
    timeline = build_char_timeline(result, policy=policy)
    report = map_report(cues, result, policy=policy, timeline=timeline)
    global_offset = None
    if apply_global_offset:
        global_offset = estimate_global_offset(report.alignments)
        if global_offset:
            cues = shift_cues(cues, global_offset)
            report = map_report(cues, result, policy=policy, timeline=timeline)
    stats = report.summary()
    stats['boundary'] = boundary_stats(report.alignments)
    reliable = [al for al in report.alignments
                if al.status == 'match' and al.cue_chars >= 3
                and al.confidence != 'low']
    reliable_boundary = boundary_stats(reliable)['start']
    plan = plan_repairs(cues, report.alignments)
    plan_stats = summarize_plan(plan)
    clean = AlignmentConfig().clean_boundary
    truncating = sum(
        1 for entry in plan
        if entry.auto and entry.new_end is not None
        and entry.new_end < entry.cue_end - clean)
    plan_stats['end_truncating_auto'] = truncating
    quality = _quality(result)
    dialogue = dialogue_cer(cues, timeline, report.alignments)
    p90 = explain_p90(cues, report.alignments, timeline)
    signed = signed_start_stats(report.alignments)
    spans = lost_spans(unit_triples(result.tokens)) if result.tokens else []
    collapsed = [s for s in spans if s['reason'] == 'collapsed_run']

    duration = float(result.audio_duration or 0.0)
    return {
        'id': ep_id,
        'split': ep.get('split', 'unassigned'),
        'category': ep.get('category', ''),
        'reference_srt': ref_path,
        'main_window': list(window) if window else None,
        'audio_duration': round(duration, 3),
        'elapsed_s': None if elapsed_s is None else round(elapsed_s, 2),
        'rtf': (round(elapsed_s / duration, 4)
                if elapsed_s is not None and duration else None),
        'ref_cues': len(cues),
        'global_offset': (round(global_offset, 3)
                          if global_offset is not None else None),
        'ref_chars': cer['ref_chars'],
        'asr_chars': cer['hyp_chars'],
        'asr_tokens': len(result.tokens),
        'asr_segments': len(result.segments),
        'cer': round(cer['cer'], 4),
        'cer_detail': cer,
        'dialogue_cer': dialogue,
        'token_quality': quality['tokens'],
        'segment_quality': quality['segments'],
        'timeline': {
            'source': report.timeline_source,
            'fallback': report.timeline_fallback,
            'quality': report.timeline_quality,
            'collapsed_runs': len(collapsed),
            'collapsed_gap_s': round(sum(s['gap_s'] for s in collapsed), 3),
            'collapsed_chars': sum(s['chars'] for s in collapsed),
            'overlong_tokens': sum(1 for s in spans
                                   if s['reason'] == 'overlong_token'),
        },
        'alignment': {
            'status': stats['status'],
            'clean_ratio': stats['clean_ratio'],
            'boundary': stats['boundary'],
            'signed_start': signed,
            'error_bands': error_bands(report.alignments),
            'p90': p90,
            'reliable': reliable_boundary,
        },
        'plan': plan_stats,
        'warnings': list(report.warnings),
    }


def _mean(values: list[float]) -> float | None:
    values = [v for v in values if v is not None]
    return round(statistics.fmean(values), 4) if values else None


def aggregate(rows: list[dict]) -> dict:
    """Per-split averages.  Micro CER uses summed edit distance/chars."""
    out = {}
    for split in sorted({r.get('split', 'unassigned') for r in rows
                         if 'error' not in r}):
        ok = [r for r in rows
              if 'error' not in r and r.get('split', 'unassigned') == split]
        if not ok:
            continue
        total_ref = sum(r['cer_detail']['ref_chars'] for r in ok)
        total_dist = sum(r['cer_detail']['distance'] for r in ok)
        dialogue_rows = [r.get('dialogue_cer') or {} for r in ok]
        covered_ref = sum(d.get('covered_ref_chars', 0) for d in dialogue_rows)
        covered_dist = sum(d.get('covered_distance', 0) for d in dialogue_rows)
        match_ref = sum(d.get('match_ref_chars', 0) for d in dialogue_rows)
        match_dist = sum(d.get('match_distance', 0) for d in dialogue_rows)
        matched_chars = sum(d.get('matched_chars', 0) for d in dialogue_rows)
        bands = [r['alignment'].get('error_bands') or {} for r in ok]
        band_n = sum(b.get('n', 0) for b in bands)
        band_le300 = sum(b.get('le_300', 0) for b in bands)
        p90_rows = [r['alignment'].get('p90') or {} for r in ok]
        p90_flags: dict[str, int] = {}
        for item in p90_rows:
            for key, value in (item.get('flags') or {}).items():
                p90_flags[key] = p90_flags.get(key, 0) + int(value)
        out[split] = {
            'episodes': len(ok),
            'macro_cer': _mean([r['cer'] for r in ok]),
            'micro_cer': round(total_dist / total_ref, 4) if total_ref else None,
            'covered_cer': (round(covered_dist / covered_ref, 4)
                            if covered_ref else None),
            'covered_ref_chars': covered_ref,
            'match_cer': (round(match_dist / match_ref, 4)
                          if match_ref else None),
            'match_ref_chars': match_ref,
            'char_coverage': (round(matched_chars / total_ref, 3)
                              if total_ref else None),
            'dialogue_covered_cues': sum(d.get('covered_cues', 0)
                                         for d in dialogue_rows),
            'dialogue_match_cues': sum(d.get('match_cues', 0)
                                       for d in dialogue_rows),
            'match': sum(r['alignment']['status'].get('match', 0) for r in ok),
            'cues': sum(r['ref_cues'] for r in ok),
            'p50_start_ms': _mean([r['alignment']['boundary']['start']['p50_ms']
                                   for r in ok]),
            'p90_start_ms': _mean([r['alignment']['boundary']['start']['p90_ms']
                                   for r in ok]),
            'hit100_start': _mean([r['alignment']['boundary']['start']['hit_100']
                                   for r in ok]),
            'hit300_start': (round(band_le300 / band_n, 3)
                             if band_n else None),
            'signed_p50_ms': _mean(
                [(r['alignment'].get('signed_start') or {}).get('p50_ms')
                 for r in ok]),
            'signed_spread_ms': _mean(
                [(r['alignment'].get('signed_start') or {}).get('spread_ms')
                 for r in ok]),
            'early_share': _mean(
                [(r['alignment'].get('signed_start') or {}).get('early_share')
                 for r in ok]),
            'p90_over': sum(item.get('n_over', 0) for item in p90_rows),
            'p90_flags': p90_flags,
            'collapsed_runs': sum(r['timeline'].get('collapsed_runs', 0)
                                  for r in ok),
            'collapsed_gap_s': round(sum(r['timeline'].get('collapsed_gap_s', 0)
                                         for r in ok), 3),
            'collapsed_chars': sum(r['timeline'].get('collapsed_chars', 0)
                                   for r in ok),
            'overlong_tokens': sum(r['timeline'].get('overlong_tokens', 0)
                                   for r in ok),
            'token_zero_ratio': _mean([r['token_quality']['zero_ratio']
                                       for r in ok]),
            'auto_fix': sum(r['plan']['auto'] for r in ok),
            'end_truncating_auto': sum(
                r['plan'].get('end_truncating_auto', 0) for r in ok),
            'reliable_n': sum(r['alignment']['reliable']['n'] for r in ok),
            'p90_reliable_ms': _mean(
                [r['alignment']['reliable']['p90_ms'] for r in ok]),
            'hit100_reliable': _mean(
                [r['alignment']['reliable']['hit_100'] for r in ok]),
            'rtf': _mean([r['rtf'] for r in ok]),
        }
    return out


# ═══════════════════════════════════════════════════════════════
# Rendering
# ═══════════════════════════════════════════════════════════════

def _fmt(value, suffix: str = '') -> str:
    if value is None:
        return '—'
    return f'{value}{suffix}'


def render_markdown(rows: list[dict], agg: dict, manifest: dict) -> str:
    lines = [
        '# held-out episode benchmark',
        '',
        f"- project: {manifest.get('project', '')}",
        f"- language: {manifest.get('language', 'Japanese')}",
        '- note: reference Japanese CC is the boundary test standard (user '
        'decision) and a benchmark, not a tuning input (see eval/protocol.md).',
        '',
        '| split | id | CER | ref chars | cues | match | '
        'P50 起 (ms) | P90 起 (ms) | ±100ms | ±300ms | rel | rel P90 | '
        'offset | auto | trunc | token zero | RTF |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|',
    ]
    for r in rows:
        if 'error' in r:
            lines.append(f"| {r.get('split', '?')} | {r.get('id', '?')} | "
                         f"ERROR: {r['error']} | | | | | | | | | | | | | | |")
            continue
        b = r['alignment']['boundary']['start']
        rel = r['alignment']['reliable']
        bands = r['alignment'].get('error_bands') or {}
        hit300 = (bands.get('le_300', 0) / bands['n']) if bands.get('n') else None
        offset = ('' if r.get('global_offset') is None
                  else f"{r['global_offset']:+.2f}s")
        lines.append(
            f"| {r['split']} | {r['id']} | {r['cer']:.4f} | "
            f"{r['ref_chars']} | {r['ref_cues']} | "
            f"{r['alignment']['status'].get('match', 0)} | "
            f"{b['p50_ms']} | {b['p90_ms']} | {b['hit_100']:.1%} | "
            f"{_fmt(None if hit300 is None else round(100 * hit300, 1), '%')} | "
            f"{rel['n']} | {rel['p90_ms']} | "
            f"{offset} | {r['plan']['auto']} | "
            f"{r['plan'].get('end_truncating_auto', 0)} | "
            f"{r['token_quality']['zero_ratio']:.1%} | {r['rtf']} |")
    if agg:
        lines += ['', '## Aggregate by split', '',
                  '| split | eps | macro CER | micro CER | '
                  'match | P50 起 | P90 起 | ±100ms | ±300ms | rel | rel P90 | '
                  'auto | trunc | token zero | RTF |',
                  '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
        for split, a in agg.items():
            lines.append(
                f"| {split} | {a['episodes']} | {a['macro_cer']} | "
                f"{a['micro_cer']} | "
                f"{a['match']}/{a['cues']} | "
                f"{a['p50_start_ms']} | {a['p90_start_ms']} | "
                f"{a['hit100_start']:.1%} | "
                f"{_fmt(None if a.get('hit300_start') is None else round(100 * a['hit300_start'], 1), '%')} | "
                f"{a['reliable_n']} | "
                f"{a['p90_reliable_ms']} | {a['auto_fix']} | "
                f"{a['end_truncating_auto']} | "
                f"{a['token_zero_ratio']:.1%} | {a['rtf']} |")
    text_rows = [r for r in rows if 'error' not in r and r.get('dialogue_cer')]
    if text_rows:
        lines += ['', '## Text decomposition (reference coverage)', '',
                  '`char cov` = matched reference characters / all reference '
                  'characters. `match CER` and `covered CER` score only cues '
                  'the mapper matched (similarity ≥ 0.85 / ≥ 0.5) against the '
                  'ASR characters placed under them; context insertions are '
                  'excluded and remain visible in the global CER.',
                  '',
                  '| split | id | ref chars | char cov | no-match chars | '
                  'match cues | match CER | covered cues | covered CER |',
                  '|---|---|---:|---:|---:|---:|---:|---:|---:|']
        for r in text_rows:
            d = r['dialogue_cer']
            lines.append(
                f"| {r['split']} | {r['id']} | {d.get('ref_chars_total', 0)} | "
                f"{_fmt(d.get('char_coverage'))} | "
                f"{d.get('unsupported_ref_chars', 0)} | "
                f"{d.get('match_cues', 0)} | {_fmt(d.get('match_cer'))} | "
                f"{d.get('covered_cues', 0)} | {_fmt(d.get('covered_cer'))} |")
        for split, a in (agg or {}).items():
            if a.get('covered_cer') is None and a.get('match_cer') is None:
                continue
            lines.append(
                f"| {split} | (split) | — | {_fmt(a.get('char_coverage'))} | — | "
                f"{a.get('dialogue_match_cues', 0)} | {_fmt(a.get('match_cer'))} | "
                f"{a.get('dialogue_covered_cues', 0)} | {_fmt(a.get('covered_cer'))} |")
    hole_rows = [r for r in rows if 'error' not in r
                 and r['timeline'].get('collapsed_runs')]
    if hole_rows:
        lines += ['', '## Timeline holes (ForcedAligner collapse)', '',
                  'A collapsed run is a stretch of dialogue whose timestamps '
                  'all sit at one instant; the audio in the preceding gap has '
                  'no time axis and cannot be matched. Re-aligning the same '
                  'window with the same FA was measured on EP002 and came out '
                  'worse, so this is a P3 hand-off, not a P2 repair.',
                  '',
                  '| split | id | collapsed runs | gap seconds | chars stacked | '
                  'overlong tokens |',
                  '|---|---|---:|---:|---:|---:|']
        for r in hole_rows:
            t = r['timeline']
            lines.append(
                f"| {r['split']} | {r['id']} | {t.get('collapsed_runs', 0)} | "
                f"{t.get('collapsed_gap_s', 0)} | "
                f"{t.get('collapsed_chars', 0)} | "
                f"{t.get('overlong_tokens', 0)} |")
        for split, a in (agg or {}).items():
            if not a.get('collapsed_runs'):
                continue
            lines.append(
                f"| {split} | (split) | {a['collapsed_runs']} | "
                f"{a['collapsed_gap_s']} | {a['collapsed_chars']} | "
                f"{a['overlong_tokens']} |")
    attributed = [r for r in rows if 'error' not in r
                  and (r['alignment'].get('p90') or {}).get('matched')]
    signed_rows = [r for r in rows if 'error' not in r
                   and (r['alignment'].get('signed_start') or {}).get('n')]
    if signed_rows:
        lines += ['', '## Signed start error (bias vs spread)', '',
                  'Signed = ASR start − cue start. The Japanese CC is the '
                  'boundary test standard (user decision), so a distribution '
                  'centred away from 0 is a systematic alignment defect. It is '
                  'reported, never corrected by an offset estimated from the '
                  'same cues (raw stays the gate).',
                  '',
                  '| split | id | n | early | P10 (ms) | P50 (ms) | P90 (ms) | '
                  'P90−P10 (ms) | |err| P50 | |err| P90 |',
                  '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|']
        for r in signed_rows:
            s = r['alignment']['signed_start']
            lines.append(
                f"| {r['split']} | {r['id']} | {s['n']} | "
                f"{s['early_share']:.1%} | {s['p10_ms']} | {s['p50_ms']} | "
                f"{s['p90_ms']} | {s['spread_ms']} | {s['abs_p50_ms']} | "
                f"{s['abs_p90_ms']} |")
        for split, a in (agg or {}).items():
            if a.get('signed_p50_ms') is None:
                continue
            lines.append(
                f"| {split} | (split mean) | — | "
                f"{_fmt(None if a.get('early_share') is None else round(100 * a['early_share'], 1), '%')} | "
                f"— | {a['signed_p50_ms']} | — | {a['signed_spread_ms']} | "
                f"— | — |")
    if attributed:
        lines += ['', '## P90 attribution (matched cues, |start error| > 300 ms)',
                  '',
                  'A cue can carry several flags; `plain` means no cheap '
                  'explanation (inspect by hand). `stretched`/`collapsed`/'
                  '`zero_span` describe the ForcedAligner span, not the cue.',
                  '',
                  '| split | id | matched | over | zero span | collapsed | '
                  'stretched | short cue | low conf | ambiguous | repeat | '
                  'plain |',
                  '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
        for r in attributed:
            p90 = r['alignment']['p90']
            flags = p90.get('flags') or {}
            lines.append(
                f"| {r['split']} | {r['id']} | {p90.get('matched', 0)} | "
                f"{p90.get('n_over', 0)} | {flags.get('zero_span', 0)} | "
                f"{flags.get('collapsed', 0)} | {flags.get('stretched', 0)} | "
                f"{flags.get('short_cue', 0)} | "
                f"{flags.get('low_confidence', 0)} | "
                f"{flags.get('ambiguous', 0)} | {flags.get('repeat', 0)} | "
                f"{flags.get('plain', 0)} |")
        examples = []
        for r in attributed:
            for item in (r['alignment']['p90'].get('examples') or [])[:5]:
                examples.append((r['id'], item))
        if examples:
            lines += ['', '### Largest start errors', '',
                      '| id | cue | start error (ms) | chars | sim | conf | '
                      'flags | reference text |',
                      '|---|---:|---:|---:|---:|---|---|---|']
            for ep_id, item in examples:
                text = (item.get('cue_text') or '').replace('|', '/')
                lines.append(
                    f"| {ep_id} | {item.get('cue_index')} | "
                    f"{item.get('start_error_ms')} | {item.get('cue_chars')} | "
                    f"{item.get('similarity')} | {item.get('confidence') or '—'} | "
                    f"{', '.join(item.get('flags') or ['plain'])} | {text} |")
    errors = [r for r in rows if 'error' in r]
    if errors:
        lines += ['', '## Errors', '']
        lines += [f"- {r.get('id')}: {r['error']}" for r in errors]
    lines += [
        '',
        '> This report is read-only.  Do not edit thresholds by looking at '
        'test-split numbers; see eval/protocol.md.',
    ]
    return '\n'.join(lines) + '\n'


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Held-out episode benchmark (read-only)')
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--asr-json-dir', default='temp/asr',
                        help='directory for {id}_main.qwen3.json caches')
    parser.add_argument('--transcribe', action='store_true',
                        help='transcribe missing episodes (main window only)')
    parser.add_argument('--engine', default='qwen3')
    parser.add_argument('--python', default=QWEN_PYTHON or '')
    parser.add_argument('--model', '--asr-model', dest='model', default=None)
    parser.add_argument('--aligner-model', default=ALIGNER_MODEL)
    parser.add_argument('--chunk-sec', type=float, default=0.0,
                        help='Qwen3 ASR chunk length (0 = worker default/60s)')
    parser.add_argument('--device', default=ASR_DEVICE)
    parser.add_argument('--dtype', default=ASR_DTYPE)
    parser.add_argument('--work-dir', default='temp/benchmark')
    parser.add_argument('--repair-overlong', action='store_true',
                        help='evaluate the opt-in local-rate overlong repair')
    parser.add_argument('--timeline-source', default='auto',
                        choices=['auto', 'tokens', 'segments'],
                        help='force a timestamp source (diagnostics)')
    parser.add_argument('--global-offset', action='store_true',
                        help='estimate and apply one whole-file offset first '
                             '(diagnostic only: production fix_by_asr is '
                             'raw by default)')
    parser.add_argument('--output', default='')
    parser.add_argument('--json-out', default='')
    parser.add_argument('--limit', type=int, default=0)
    args = parser.parse_args(argv)

    policy = TimelinePolicy(
        repair_overlong=args.repair_overlong,
        force_source='' if args.timeline_source == 'auto'
        else args.timeline_source,
    )

    manifest = json.loads(Path(args.manifest).read_text(encoding='utf-8-sig'))
    episodes = manifest.get('episodes', [])
    if args.limit:
        episodes = episodes[:args.limit]
    if not episodes:
        print('[benchmark] manifest has no episodes', file=sys.stderr)
        return 2

    language = manifest.get('language', 'Japanese')
    router = None
    if args.transcribe:
        model = args.model or ASR_MODEL
        config = ASRConfig(
            model=model,
            aligner_model=args.aligner_model,
            python=args.python or '',
            device=args.device,
            dtype=args.dtype,
            options={'whisper_model': WHISPER_MODEL,
                     'chunk_sec': args.chunk_sec},
        )
        router = ASRRouter(engine=args.engine, fallback='none',
                           config=config)
        try:
            router.warmup()
        except Exception as exc:
            print(f'[benchmark] warmup failed: {exc}', file=sys.stderr)

    rows = []
    try:
        for ep in episodes:
            try:
                elapsed = None
                try:
                    result = load_asr(ep, args.asr_json_dir)
                    print(f"[benchmark] {ep.get('id')}: cached ASR",
                          file=sys.stderr)
                except FileNotFoundError:
                    if router is None:
                        raise
                    window = ep.get('main_window')
                    if not window:
                        raise ValueError(
                            f"{ep.get('id')}: --transcribe needs main_window")
                    result, elapsed = transcribe_episode(
                        router, ep, window=window, language=language,
                        work_dir=args.work_dir)
                    print(f"[benchmark] {ep.get('id')}: transcribed",
                          file=sys.stderr)
                row = evaluate_episode(ep, result, elapsed_s=elapsed,
                                       policy=policy,
                                       apply_global_offset=args.global_offset)
                print(f"[benchmark] {row['id']} [{row['split']}]: "
                      f"CER={row['cer']:.4f} "
                      f"P50={row['alignment']['boundary']['start']['p50_ms']}ms",
                      file=sys.stderr)
                rows.append(row)
            except Exception as exc:
                print(f"[benchmark] {ep.get('id', '?')} failed: {exc}",
                      file=sys.stderr)
                rows.append({'id': ep.get('id', '?'),
                             'split': ep.get('split', 'unassigned'),
                             'error': str(exc)})
    finally:
        if router is not None:
            router.close()

    agg = aggregate(rows)
    payload = {
        'schema_version': 1,
        'project': manifest.get('project', ''),
        'language': language,
        'read_only': True,
        'policy': policy.to_dict(),
        'splits': agg,
        'episodes': rows,
    }
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                       encoding='utf-8')
        print(f'[benchmark] wrote {out}', file=sys.stderr)
    text = render_markdown(rows, agg, manifest)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding='utf-8')
        print(f'[benchmark] wrote {out}', file=sys.stderr)
    else:
        print(text)

    return 1 if any('error' in r for r in rows) else 0


if __name__ == '__main__':
    raise SystemExit(main())
