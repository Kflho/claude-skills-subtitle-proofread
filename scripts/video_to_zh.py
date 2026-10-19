#!/usr/bin/env python3
"""One-command video → Japanese SRT → Chinese SRT (+ QA).

This is deliberately a thin orchestrator over existing tools:
  - `asr/qwen3_transcribe.py` (Qwen3-ASR/FA) or
    `whisper_batch_transcribe.py` (legacy Whisper)
  - `translate_srt.py` (LLM translation + QA + repair)

It does not reimplement transcription, translation, or QA.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib._path  # noqa: F401,E402
from lib.video_scan import select_videos  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent
QWEN_CLI = SCRIPTS / 'asr' / 'qwen3_transcribe.py'
WHISPER_CLI = SCRIPTS / 'whisper_batch_transcribe.py'
TRANSLATE_CLI = SCRIPTS / 'translate_srt.py'


def build_transcribe_cmd(engine: str, video: str, ja_srt: str, *,
                         python: str = '', model: str = '',
                         aligner: str = '', language: str = 'Japanese') -> list:
    if engine == 'qwen3':
        asr_json = str(Path(ja_srt).with_suffix('.asr.json'))
        cmd = [sys.executable, str(QWEN_CLI),
               '--video', video, '--language', language,
               '--timestamps', '--srt', ja_srt, '--output', asr_json]
        if python:
            cmd += ['--python', python]
        if model:
            cmd += ['--model', model]
        if aligner:
            cmd += ['--aligner', aligner]
        return cmd
    if engine == 'whisper':
        return [sys.executable, str(WHISPER_CLI),
                '--video-dir', str(Path(video).parent),
                '--output-dir', str(Path(ja_srt).parent),
                '--lang', 'ja', '--episodes', Path(ja_srt).stem]
    raise ValueError(f'unknown engine: {engine}')


def build_translate_cmd(ja_srt: str, zh_srt: str, qa_path: str, *,
                        mappings: str = '', batch: int = 10,
                        repair: bool = True) -> list:
    cmd = [sys.executable, str(TRANSLATE_CLI),
           '--input', ja_srt, '--output', zh_srt,
           '--batch', str(batch), '--qa-report', qa_path]
    if mappings:
        cmd += ['--mappings', mappings]
    if repair:
        cmd.append('--repair')
    return cmd


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--video-dir', required=True)
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--engine', choices=('qwen3', 'whisper'),
                        default='qwen3')
    parser.add_argument('--episodes', '-e', default='',
                        help='EP001-EP003 / 1-3 / SP01,...')
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--python', default='.venv-qwen/Scripts/python.exe',
                        help='Qwen3 worker interpreter')
    parser.add_argument('--model', default='', help='Qwen3-ASR path/id')
    parser.add_argument('--aligner', default='',
                        help='Qwen3-ForcedAligner path/id')
    parser.add_argument('--mappings', default='',
                        help='ja→zh glossary JSON (optional)')
    parser.add_argument('--batch', type=int, default=20)
    parser.add_argument('--no-repair', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)

    out = Path(args.output_dir).resolve()
    ja_dir = out / 'ja'
    zh_dir = out / 'zh'
    out.mkdir(parents=True, exist_ok=True)
    ja_dir.mkdir(parents=True, exist_ok=True)
    zh_dir.mkdir(parents=True, exist_ok=True)

    entries = select_videos([args.video_dir], episodes=args.episodes or None,
                            limit=args.limit)
    if not entries:
        print(f'[video_to_zh] no videos selected from {args.video_dir}',
              file=sys.stderr)
        return 1
    print(f'[video_to_zh] {len(entries)} episode(s): '
          f'{", ".join(t for t, _ in entries)}', file=sys.stderr)

    failed = 0
    repo = SCRIPTS.parent
    model = args.model or (str(repo / 'models' / 'Qwen3-ASR-1.7B')
                           if (repo / 'models' / 'Qwen3-ASR-1.7B').exists()
                           else '')
    aligner = args.aligner or (str(repo / 'models' / 'Qwen3-ForcedAligner-0.6B')
                               if (repo / 'models' / 'Qwen3-ForcedAligner-0.6B').exists()
                               else '')
    for ep, video in entries:
        ja_srt = str(ja_dir / f'{ep}.srt')
        zh_srt = str(zh_dir / f'{ep}.srt')
        qa = str(zh_dir / f'{ep}.qa.json')
        if not os.path.exists(ja_srt):
            cmd = build_transcribe_cmd(
                args.engine, video, ja_srt, python=args.python,
                model=model, aligner=aligner)
            print('[transcribe] ' + ' '.join(cmd), file=sys.stderr)
            if not args.dry_run:
                if subprocess.run(cmd).returncode != 0:
                    failed += 1
                    continue
        else:
            print(f'[transcribe] reuse {ja_srt}', file=sys.stderr)

        cmd = build_translate_cmd(
            ja_srt, zh_srt, qa, mappings=args.mappings,
            batch=args.batch, repair=not args.no_repair)
        print('[translate] ' + ' '.join(cmd), file=sys.stderr)
        if not args.dry_run and subprocess.run(cmd).returncode != 0:
            failed += 1

    print(f'[video_to_zh] done: {len(entries) - failed}/{len(entries)} ok; '
          f'output {out}', file=sys.stderr)
    if args.dry_run:
        print('[video_to_zh] DRY RUN — no model/API calls', file=sys.stderr)
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
