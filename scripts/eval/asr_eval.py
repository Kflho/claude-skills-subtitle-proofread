#!/usr/bin/env python3
"""P0/P2 ASR evaluation: CER + segment/cue boundary diagnostics.

This is deliberately model-agnostic: it drives whichever ASR engine the
router selects and compares against a reference SRT.  It does NOT edit
subtitles.

Manifest (JSON):
    {
      "project": "astro-boy",
      "language": "Japanese",
      "episodes": [
        {"id": "EP001", "category": "clean",
         "audio": "<media-dir>/EP001.wav",
         "reference_srt": "C:/subs/EP001.ja.srt"},
        {"id": "EP002", "category": "overlap",
         "video": "<media-dir>/EP002.mkv",
         "reference_srt": "C:/subs/EP002.ja.srt"}
      ]
    }

Usage:
    python scripts/eval/asr_eval.py --manifest eval/manifest.json \
        --engine whisper --output reports/asr_eval_whisper.md
    python scripts/eval/asr_eval.py --manifest eval/manifest.json \
        --engine qwen3 --python .venv-qwen\\Scripts\\python.exe \
        --output reports/asr_eval_qwen3.md
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import unicodedata
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from lib.asr.base import ASRConfig  # noqa: E402
from lib.asr.router import ASRRouter  # noqa: E402
from lib.config import (  # noqa: E402
    ALIGNER_MODEL, ASR_DEVICE, ASR_DTYPE, ASR_MODEL, QWEN_PYTHON,
    WHISPER_MODEL,
)


# ═══════════════════════════════════════════════════════════════
# Text metrics
# ═══════════════════════════════════════════════════════════════

def normalize_text(text: str) -> str:
    """NFKC, drop punctuation/symbols/whitespace, keep letters/numbers/marks."""
    text = unicodedata.normalize('NFKC', text or '')
    keep = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat.startswith('Z'):
            continue
        if cat.startswith('P') or cat.startswith('S'):
            continue
        keep.append(ch)
    return ''.join(keep)


def _levenshtein(ref: str, hyp: str) -> tuple[int, int, int, int]:
    """Return (distance, substitutions, deletions, insertions).

    Two-row DP; exact.  Evaluation scripts are offline, so the O(n*m)
    cost is acceptable (a 24-min episode is ~4-6k chars).
    """
    n, m = len(ref), len(hyp)
    if n == 0:
        return m, 0, 0, m
    if m == 0:
        return n, 0, n, 0
    prev = list(range(m + 1))
    # Track op counts in parallel rows.
    prev_ops = [(0, 0, j) for j in range(m + 1)]  # (sub, del, ins)
    for i in range(1, n + 1):
        cur = [i] + [0] * m
        cur_ops = [(0, i, 0)] + [(0, 0, 0)] * m
        ri = ref[i - 1]
        for j in range(1, m + 1):
            cost = 0 if ri == hyp[j - 1] else 1
            sub = prev[j - 1] + cost
            dele = prev[j] + 1
            ins = cur[j - 1] + 1
            best = min(sub, dele, ins)
            cur[j] = best
            ps, pd, pi = prev_ops[j - 1]
            cs, cd, ci = prev_ops[j]
            ls, ld, li = cur_ops[j - 1]
            if best == sub:
                cur_ops[j] = (ps + cost, pd, pi)
            elif best == dele:
                cur_ops[j] = (cs, cd + 1, ci)
            else:
                cur_ops[j] = (ls, ld, li + 1)
        prev, prev_ops = cur, cur_ops
    subs, dels, ins = prev_ops[m]
    return prev[m], subs, dels, ins


def compute_cer(reference: str, hypothesis: str) -> dict:
    ref = normalize_text(reference)
    hyp = normalize_text(hypothesis)
    dist, subs, dels, ins = _levenshtein(ref, hyp)
    return {
        'cer': dist / max(1, len(ref)),
        'ref_chars': len(ref),
        'hyp_chars': len(hyp),
        'distance': dist,
        'substitutions': subs,
        'deletions': dels,
        'insertions': ins,
    }


def boundary_metrics(ref_cues: list[dict], asr_segments: list) -> dict:
    """Compare reference cue boundaries to ASR segment boundaries.

    This is a P0 diagnostic, not true cue alignment: each reference cue is
    matched to the ASR segment with maximum time overlap.  P2 replaces it
    with token-level cue mapping.
    """
    if not ref_cues:
        return {'cues': 0, 'matched': 0, 'missed': 0,
                'mae_ms': None, 'p50_ms': None, 'p90_ms': None}

    errors = []
    matched = 0
    missed = 0
    for cue in ref_cues:
        c_start = float(cue.get('start_s', 0.0))
        c_end = float(cue.get('end_s', c_start))
        best = None
        best_overlap = 0.0
        for seg in asr_segments:
            s_start = float(getattr(seg, 'start', 0.0))
            s_end = float(getattr(seg, 'end', s_start))
            overlap = max(0.0, min(c_end, s_end) - max(c_start, s_start))
            if overlap > best_overlap:
                best_overlap = overlap
                best = seg
        if best is None or best_overlap <= 0:
            missed += 1
            errors.append(min(max(c_end - c_start, 0.5), 10.0) * 1000)
            continue
        matched += 1
        start_err = abs(c_start - float(best.start))
        end_err = abs(c_end - float(best.end))
        errors.append((start_err + end_err) / 2 * 1000)

    errors.sort()

    def pct(p):
        if not errors:
            return None
        idx = min(len(errors) - 1, int(round((len(errors) - 1) * p)))
        return errors[idx]

    return {
        'cues': len(ref_cues),
        'matched': matched,
        'missed': missed,
        'mae_ms': round(statistics.fmean(errors), 1) if errors else None,
        'p50_ms': round(pct(0.50), 1) if errors else None,
        'p90_ms': round(pct(0.90), 1) if errors else None,
    }


# ═══════════════════════════════════════════════════════════════
# Episode evaluation
# ═══════════════════════════════════════════════════════════════

def _parse_reference(path: str) -> list[dict]:
    from lib.whisper_utils import parse_srt
    return parse_srt(path, mark_garbled=False)


def _resolve_audio(ep: dict, work_dir: str) -> str:
    audio = ep.get('audio')
    if audio and os.path.exists(audio):
        return audio
    video = ep.get('video')
    if video and os.path.exists(video):
        from lib.whisper_utils import extract_audio_wav
        out = Path(work_dir) / f'{ep.get("id", "ep")}.wav'
        out.parent.mkdir(parents=True, exist_ok=True)
        extract_audio_wav(video, str(out))
        return str(out)
    raise FileNotFoundError(
        f'{ep.get("id")}: audio/video not found '
        f'(audio={audio!r}, video={video!r})')


def evaluate_episode(router: ASRRouter, ep: dict, *, language: str,
                     work_dir: str) -> dict:
    ep_id = ep.get('id') or Path(ep.get('audio') or ep.get('video') or '?').stem
    ref_path = ep.get('reference_srt')
    if not ref_path or not os.path.exists(ref_path):
        raise FileNotFoundError(f'{ep_id}: reference_srt not found: {ref_path!r}')

    audio = _resolve_audio(ep, work_dir)
    ref_cues = _parse_reference(ref_path)
    ref_text = ''.join(str(c.get('text', '')) for c in ref_cues)

    started = time.time()
    result = router.transcribe(
        audio, language=language,
        context=ep.get('context', ''),
        timestamps=bool(ep.get('timestamps', True)),
    )
    elapsed = time.time() - started
    cer = compute_cer(ref_text, result.text)
    boundaries = boundary_metrics(ref_cues, result.segments)

    return {
        'id': ep_id,
        'category': ep.get('category', ''),
        'audio': audio,
        'reference_srt': ref_path,
        'engine': result.engine,
        'model': result.model,
        'duration_s': round(result.audio_duration, 2),
        'elapsed_s': round(elapsed, 2),
        'rtf': round(elapsed / result.audio_duration, 3)
        if result.audio_duration else None,
        'cer': round(cer['cer'], 4),
        'cer_detail': cer,
        'ref_cues': len(ref_cues),
        'asr_segments': len(result.segments),
        'asr_tokens': len(result.tokens),
        'boundary': boundaries,
        'warnings': result.meta.get('warnings', []),
    }


def _summary(rows: list[dict]) -> dict:
    ok = [r for r in rows if 'error' not in r]
    if not ok:
        return {'episodes': len(rows), 'ok': 0, 'mean_cer': None}
    return {
        'episodes': len(rows),
        'ok': len(ok),
        'mean_cer': round(sum(r['cer'] for r in ok) / len(ok), 4),
        'mean_mae_ms': round(
            sum(r['boundary']['mae_ms'] for r in ok
                if r['boundary']['mae_ms'] is not None)
            / max(1, sum(1 for r in ok
                         if r['boundary']['mae_ms'] is not None)), 1),
        'mean_rtf': round(
            sum(r['rtf'] for r in ok if r['rtf'] is not None)
            / max(1, sum(1 for r in ok if r['rtf'] is not None)), 3),
    }


def _markdown(rows: list[dict], summary: dict, manifest: dict) -> str:
    lines = [
        '# ASR evaluation',
        '',
        f"- project: {manifest.get('project', '')}",
        f"- language: {manifest.get('language', 'Japanese')}",
        f"- engine: {rows[0].get('engine', '?') if rows else '?'}",
        f"- summary: {json.dumps(summary, ensure_ascii=False)}",
        '',
        '| id | category | CER | ref chars | cues | ASR segs | tokens | '
        'boundary MAE ms | P90 ms | RTF |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|',
    ]
    for r in rows:
        if 'error' in r:
            lines.append(f"| {r.get('id')} | {r.get('category','')} | "
                         f"ERROR | | | | | | | |")
            continue
        b = r['boundary']
        lines.append(
            f"| {r['id']} | {r['category']} | {r['cer']:.4f} | "
            f"{r['cer_detail']['ref_chars']} | {r['ref_cues']} | "
            f"{r['asr_segments']} | {r['asr_tokens']} | "
            f"{b['mae_ms']} | {b['p90_ms']} | {r['rtf']} |"
        )
    errors = [r for r in rows if 'error' in r]
    if errors:
        lines += ['', '## Errors', '']
        for r in errors:
            lines.append(f"- {r.get('id')}: {r['error']}")
    lines += [
        '',
        '> `boundary MAE` 是 ASR segment 与参考 cue 的最大重叠匹配，',
        '> 仅作 P0 诊断；P2 的 token 级 cue 对齐会替换它。',
    ]
    return '\n'.join(lines) + '\n'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='ASR CER/boundary evaluation')
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--engine', default='whisper',
                        help='registered ASR engine (see --list-engines)')
    parser.add_argument('--python', default=QWEN_PYTHON or '',
                        help='isolated venv python for worker engines')
    parser.add_argument('--model', '--asr-model', dest='model', default=None)
    parser.add_argument('--aligner-model', default=ALIGNER_MODEL)
    parser.add_argument('--whisper-model', default=WHISPER_MODEL)
    parser.add_argument('--device', default=ASR_DEVICE)
    parser.add_argument('--dtype', default=ASR_DTYPE)
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--work-dir', default='temp/asr_eval')
    parser.add_argument('--output', default='')
    parser.add_argument('--json-out', default='')
    parser.add_argument('--list-engines', action='store_true')
    args = parser.parse_args(argv)

    if args.list_engines:
        from lib.asr import registry
        for entry in registry.entries():
            caps = ','.join(entry.info.capabilities) or '-'
            print(f'{entry.name}\t{entry.kind}\t{entry.info.auto_priority}\t'
                  f'{caps}\t{entry.info.description}')
        return 0

    manifest = json.loads(Path(args.manifest).read_text(encoding='utf-8-sig'))
    episodes = manifest.get('episodes', [])
    if args.limit:
        episodes = episodes[:args.limit]
    if not episodes:
        print('manifest has no episodes', file=sys.stderr)
        return 2

    language = manifest.get('language', 'Japanese')
    engine = args.engine
    model = args.model or (WHISPER_MODEL if engine == 'whisper' else ASR_MODEL)
    whisper_model = model if engine == 'whisper' else args.whisper_model
    config = ASRConfig(
        model=model,
        aligner_model=args.aligner_model,
        python=args.python or '',
        device=args.device,
        dtype=args.dtype,
        options={'whisper_model': whisper_model},
    )
    router = ASRRouter(engine=engine, fallback='none', config=config)

    rows = []
    try:
        try:
            router.warmup()
        except Exception as exc:
            print(f'[eval] warmup failed (timing will include load): {exc}',
                  file=sys.stderr)
        for ep in episodes:
            try:
                row = evaluate_episode(router, ep, language=language,
                                       work_dir=args.work_dir)
                print(f"[eval] {row['id']}: CER={row['cer']:.4f} "
                      f"MAE={row['boundary']['mae_ms']}ms "
                      f"RTF={row['rtf']}", file=sys.stderr)
                rows.append(row)
            except Exception as exc:
                print(f"[eval] {ep.get('id', '?')} failed: {exc}",
                      file=sys.stderr)
                rows.append({'id': ep.get('id', '?'),
                             'category': ep.get('category', ''),
                             'error': str(exc)})
    finally:
        router.close()

    summary = _summary(rows)
    payload = {'summary': summary, 'episodes': rows,
               'manifest': {'project': manifest.get('project', ''),
                            'language': language,
                            'engine': args.engine}}
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding='utf-8')
    text = _markdown(rows, summary, manifest)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(text, encoding='utf-8')
        print(f'[eval] wrote {args.output}', file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
