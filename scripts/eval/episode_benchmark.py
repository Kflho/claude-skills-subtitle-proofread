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

from eval.asr_eval import compute_cer  # noqa: E402
from fix.align_repair import (  # noqa: E402
    estimate_global_offset, plan_repairs, shift_cues, summarize_plan,
)
from lib.alignment.base import AlignmentConfig  # noqa: E402
from lib.alignment.cue_mapper import map_report  # noqa: E402
from lib.alignment.diagnostics import boundary_stats  # noqa: E402
from lib.alignment.quality import (  # noqa: E402
    TimelinePolicy, assess_units, unit_triples,
)
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


def evaluate_episode(ep: dict, result: ASRResult, *,
                     elapsed_s: float | None = None,
                     policy: TimelinePolicy | None = None,
                     apply_global_offset: bool = False) -> dict:
    """Score one cached/transcribed ASR result.  Pure measurement.

    ``apply_global_offset`` mirrors the production pipeline
    (``fix_by_asr``): estimate one consistent file-level drift from the
    cue↔ASR matches, shift all cues, then re-map.  It is off by default so
    the raw token calendar stays visible; report both when judging.
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
    report = map_report(cues, result, policy=policy)
    global_offset = None
    if apply_global_offset:
        global_offset = estimate_global_offset(report.alignments)
        if global_offset:
            cues = shift_cues(cues, global_offset)
            report = map_report(cues, result, policy=policy)
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
        'token_quality': quality['tokens'],
        'segment_quality': quality['segments'],
        'timeline': {
            'source': report.timeline_source,
            'fallback': report.timeline_fallback,
            'quality': report.timeline_quality,
        },
        'alignment': {
            'status': stats['status'],
            'clean_ratio': stats['clean_ratio'],
            'boundary': stats['boundary'],
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
        out[split] = {
            'episodes': len(ok),
            'macro_cer': _mean([r['cer'] for r in ok]),
            'micro_cer': round(total_dist / total_ref, 4) if total_ref else None,
            'match': sum(r['alignment']['status'].get('match', 0) for r in ok),
            'cues': sum(r['ref_cues'] for r in ok),
            'p50_start_ms': _mean([r['alignment']['boundary']['start']['p50_ms']
                                   for r in ok]),
            'p90_start_ms': _mean([r['alignment']['boundary']['start']['p90_ms']
                                   for r in ok]),
            'hit100_start': _mean([r['alignment']['boundary']['start']['hit_100']
                                   for r in ok]),
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

def render_markdown(rows: list[dict], agg: dict, manifest: dict) -> str:
    lines = [
        '# held-out episode benchmark',
        '',
        f"- project: {manifest.get('project', '')}",
        f"- language: {manifest.get('language', 'Japanese')}",
        '- note: reference Japanese CC is a benchmark, not a tuning input '
        '(see eval/protocol.md).',
        '',
        '| split | id | CER | ref chars | cues | match | P50 起 (ms) | '
        'P90 起 (ms) | ±100ms | rel | rel P90 | offset | auto | trunc | '
        'token zero | RTF |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|',
    ]
    for r in rows:
        if 'error' in r:
            lines.append(f"| {r.get('split', '?')} | {r.get('id', '?')} | "
                         f"ERROR: {r['error']} | | | | | | | | | | | | | |")
            continue
        b = r['alignment']['boundary']['start']
        rel = r['alignment']['reliable']
        offset = ('' if r.get('global_offset') is None
                  else f"{r['global_offset']:+.2f}s")
        lines.append(
            f"| {r['split']} | {r['id']} | {r['cer']:.4f} | "
            f"{r['ref_chars']} | {r['ref_cues']} | "
            f"{r['alignment']['status'].get('match', 0)} | "
            f"{b['p50_ms']} | {b['p90_ms']} | {b['hit_100']:.1%} | "
            f"{rel['n']} | {rel['p90_ms']} | "
            f"{offset} | {r['plan']['auto']} | "
            f"{r['plan'].get('end_truncating_auto', 0)} | "
            f"{r['token_quality']['zero_ratio']:.1%} | {r['rtf']} |")
    if agg:
        lines += ['', '## Aggregate by split', '',
                  '| split | eps | macro CER | micro CER | match | '
                  'P50 起 | P90 起 | ±100ms | rel | rel P90 | auto | trunc | '
                  'token zero | RTF |',
                  '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
        for split, a in agg.items():
            lines.append(
                f"| {split} | {a['episodes']} | {a['macro_cer']} | "
                f"{a['micro_cer']} | {a['match']}/{a['cues']} | "
                f"{a['p50_start_ms']} | {a['p90_start_ms']} | "
                f"{a['hit100_start']:.1%} | {a['reliable_n']} | "
                f"{a['p90_reliable_ms']} | {a['auto_fix']} | "
                f"{a['end_truncating_auto']} | "
                f"{a['token_zero_ratio']:.1%} | {a['rtf']} |")
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
                             '(mirrors fix_by_asr)')
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
            options={'whisper_model': WHISPER_MODEL},
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
