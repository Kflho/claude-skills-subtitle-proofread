#!/usr/bin/env python3
"""Align an existing subtitle against ASR audio and propose repairs (P2 CLI).

This is the runnable slice of the P2 alignment layer.  It does **not**
change the default `run_all.py` pipeline: it reads a subtitle file, aligns
every cue against an ASR result, prints the diagnosis, optionally writes
the time fixes, and always writes a JSON report for evaluation.

Examples:
    # Dry-run against a cached ASR JSON (no model, no audio)
    python scripts\\asr\\align_cues.py --srt ep.srt --asr-json temp\\asr\\EP001.json

    # Transcribe a video with Qwen3, then align
    python scripts\\asr\\align_cues.py --srt ep.srt --video ep.mkv `
        --engine qwen3 --python .venv-qwen\\Scripts\\python.exe

    # Apply time fixes (text replacement still needs --fix-text)
    python scripts\\asr\\align_cues.py --srt ep.srt --asr-json asr.json --write

Output JSON:
    {"schema_version": 1, "srt": ..., "engine": ..., "summary": {...},
     "apply": {...}, "plan": [...], "cues": [...]}
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from fix.align_repair import (  # noqa: E402
    apply_repairs, estimate_global_offset, plan_repairs, shift_cues,
    summarize_plan,
)
from lib.alignment.base import (  # noqa: E402
    AlignmentConfig, ALIGNMENT_VERSION,
)
from lib.alignment.cue_mapper import map_report  # noqa: E402
from lib.alignment.diagnostics import boundary_stats  # noqa: E402
from lib.alignment.quality import TimelinePolicy  # noqa: E402
from lib.asr.base import ASRConfig, ASRResult  # noqa: E402
from lib.config import (  # noqa: E402
    ALIGNER_MODEL, ASR_DEVICE, ASR_DTYPE, ASR_MODEL, QWEN_PYTHON,
    WHISPER_MODEL,
)
from lib.subtitle_io import read_subtitles, write_subtitles  # noqa: E402


def _read_asr_json(path: str) -> ASRResult:
    payload = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    return ASRResult.from_dict(payload)


def _extract_audio(video: str, work_dir: str) -> str:
    from lib.whisper_utils import extract_audio_wav
    out = Path(work_dir) / (Path(video).stem + '.wav')
    out.parent.mkdir(parents=True, exist_ok=True)
    extract_audio_wav(video, str(out))
    return str(out)


def _transcribe(args, audio_path: str) -> ASRResult:
    from lib.asr.router import ASRRouter

    model = args.model or (WHISPER_MODEL if args.engine == 'whisper'
                           else ASR_MODEL)
    config = ASRConfig(
        model=model,
        aligner_model=args.aligner,
        python=args.python or '',
        device=args.device,
        dtype=args.dtype,
        options={'whisper_model': WHISPER_MODEL},
    )
    router = ASRRouter(engine=args.engine, fallback='none', config=config)
    try:
        return router.transcribe(
            audio_path, language=args.language,
            context=args.context or '', timestamps=True)
    finally:
        router.close()


def _resolve_audio(args) -> str | None:
    if args.audio:
        return args.audio
    if args.video:
        return _extract_audio(args.video, args.work_dir)
    return None


def _print_summary(summary: dict, plan_stats: dict,
                   timeline: dict | None = None) -> None:
    if timeline:
        quality = timeline.get('quality') or {}
        degraded = 'degraded' if quality.get('degraded') else 'ok'
        line = (f"timeline source={timeline.get('source') or '-'} "
                f"quality={degraded}")
        if quality:
            line += (f" zero={quality.get('zero_ratio', 0):.1%}"
                     f" run={quality.get('longest_zero_run', 0)}"
                     f" overlong={quality.get('overlong_ratio', 0):.1%}")
        if timeline.get('fallback'):
            line += f" (fallback from {timeline['fallback']})"
        print(line)
    status = summary.get('status', {})
    actions = summary.get('actions', {})
    boundary = summary.get('boundary', {}).get('start', {})
    print(f"cues={summary.get('cues', 0)} "
          f"clean={summary.get('clean', 0)} "
          f"({summary.get('clean_ratio', 0.0):.1%})")
    print('status  ' + ', '.join(f'{k}={v}' for k, v in status.items()))
    print('actions ' + ', '.join(f'{k}={v}' for k, v in actions.items()))
    print(f"boundary start: n={boundary.get('n', 0)} "
          f"MAE={boundary.get('mae_ms', 0)}ms "
          f"P50={boundary.get('p50_ms', 0)}ms "
          f"P90={boundary.get('p90_ms', 0)}ms "
          f"±100ms={boundary.get('hit_100', 0):.1%}")
    print(f"plan: total={plan_stats.get('total', 0)} "
          f"auto={plan_stats.get('auto', 0)} "
          f"review={plan_stats.get('review', 0)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Align cues against ASR and propose repairs (P2)')
    parser.add_argument('--srt', required=True,
                        help='subtitle to align (SRT or ASS)')
    parser.add_argument('--asr-json', default='',
                        help='cached ASRResult JSON (skips transcription)')
    parser.add_argument('--audio', default='', help='16k mono WAV')
    parser.add_argument('--video', default='', help='video (ffmpeg extracts audio)')
    parser.add_argument('--language', default='Japanese')
    parser.add_argument('--context', default='', help='glossary/context string')
    parser.add_argument('--engine', default='qwen3',
                        help='registered ASR engine')
    parser.add_argument('--model', default=None, help='engine model id/path')
    parser.add_argument('--aligner', default=ALIGNER_MODEL)
    parser.add_argument('--python', default=QWEN_PYTHON or '',
                        help='isolated venv python for worker engines')
    parser.add_argument('--device', default=ASR_DEVICE)
    parser.add_argument('--dtype', default=ASR_DTYPE,
                        choices=['bfloat16', 'float16', 'float32'])
    parser.add_argument('--output', default='',
                        help='alignment JSON output (default: temp/align/<stem>.json)')
    parser.add_argument('--write', action='store_true',
                        help='write time fixes back to the subtitle')
    parser.add_argument('--fix-text', action='store_true',
                        help='also apply high-confidence text replacements')
    parser.add_argument('--search-seconds', type=float, default=6.0)
    parser.add_argument('--max-shift', type=float, default=2.0,
                        help='largest auto time shift in seconds')
    parser.add_argument('--timeline-source', default='auto',
                        choices=['auto', 'tokens', 'segments'],
                        help='force a timestamp source (default: auto)')
    parser.add_argument('--repair-overlong', action='store_true',
                        help='clamp absurd overlong token spans using the '
                             'local speech rate (opt-in; can shift long '
                             'sung/vowel tokens)')
    parser.add_argument('--global-offset', dest='global_offset',
                        action='store_true', default=False,
                        help='estimate and apply one whole-file drift before '
                             'mapping. Off by default: on the Digimon CC the '
                             'estimator absorbs the ASR-vs-CC onset bias, '
                             'which the user confirmed is not a video offset. '
                             'Use only with independent evidence of a real '
                             'cut/frame-rate drift.')
    parser.add_argument('--work-dir', default='temp/align')
    args = parser.parse_args(argv)

    sources = [bool(args.asr_json), bool(args.audio), bool(args.video)]
    if sum(sources) != 1:
        parser.error('exactly one of --asr-json / --audio / --video is required')
    if not os.path.exists(args.srt):
        print(f'[align] subtitle not found: {args.srt}', file=sys.stderr)
        return 2
    if args.write and args.srt.lower().endswith('.ass'):
        # ASS in-place writing only updates dialogue text; timecodes would be
        # silently ignored (and _ass_time() expects SRT-style milliseconds).
        # Refuse instead of "succeeding" without applying anything.
        print('[align] --write does not support ASS in-place time fixes yet; '
              'run without --write for the report, or convert to SRT first.',
              file=sys.stderr)
        return 4

    try:
        result = (_read_asr_json(args.asr_json) if args.asr_json
                  else _transcribe(args, _resolve_audio(args)))
    except Exception as exc:
        print(f'[align] ASR failed: {exc}', file=sys.stderr)
        return 3

    cues = read_subtitles(args.srt, mark_garbled=False)
    if not cues:
        print(f'[align] no cues parsed from {args.srt}', file=sys.stderr)
        return 2

    cfg = AlignmentConfig(search_seconds=args.search_seconds,
                          max_time_shift=args.max_shift,
                          allow_text_replace=args.fix_text)
    policy = TimelinePolicy(
        repair_overlong=args.repair_overlong,
        force_source='' if args.timeline_source == 'auto'
        else args.timeline_source,
    )
    report = map_report(cues, result, config=cfg,
                        language=args.language, policy=policy)
    global_offset = None
    if args.global_offset:
        global_offset = estimate_global_offset(report.alignments)
        if global_offset:
            cues = shift_cues(cues, global_offset)
            report = map_report(cues, result, config=cfg,
                                language=args.language, policy=policy)
            print(f'[align] global offset {global_offset:+.3f}s applied '
                  f'to all cues', file=sys.stderr)
    plan = plan_repairs(cues, report.alignments, config=cfg)
    plan_stats = summarize_plan(plan)

    summary = report.summary()
    summary['boundary'] = boundary_stats(report.alignments)
    timeline = {
        'source': report.timeline_source,
        'fallback': report.timeline_fallback,
        'quality': report.timeline_quality,
    }
    _print_summary(summary, plan_stats, timeline)
    for warning in report.warnings:
        print(f'[align] warning: {warning}', file=sys.stderr)

    apply_stats = {'applied': 0, 'fix_time': 0, 'replace_text': 0,
                   'skipped': 0, 'manual_review': 0}
    if args.write:
        new_cues, applied = apply_repairs(cues, plan, config=cfg)
        apply_stats = dict(applied,
                           applied=applied['fix_time'] + applied['replace_text'])
        write_subtitles(args.srt, new_cues)
        print(f"[align] wrote {args.srt}: "
              f"{apply_stats['fix_time']} time fixes, "
              f"{apply_stats['replace_text']} text fixes",
              file=sys.stderr)
    else:
        print(f"[align] dry-run: {plan_stats.get('auto', 0)} auto fixes ready "
              f"(--write to apply)", file=sys.stderr)

    output = args.output or str(
        Path(args.work_dir) / (Path(args.srt).stem + '.alignment.json'))
    payload = {
        'schema_version': ALIGNMENT_VERSION,
        'srt': str(Path(args.srt).resolve()),
        'engine': report.engine,
        'model': report.model,
        'global_offset': (round(global_offset, 3)
                          if global_offset is not None else None),
        'timeline': timeline,
        'summary': summary,
        'plan': plan_stats,
        'apply': apply_stats,
        'warnings': report.warnings,
        'cues': [al.to_dict() for al in report.alignments],
        'repairs': [entry.to_dict() for entry in plan],
    }
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                            encoding='utf-8')
    print(f'[align] report: {output}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
