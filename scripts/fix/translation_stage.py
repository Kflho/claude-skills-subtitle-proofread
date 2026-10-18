#!/usr/bin/env python3
"""Final translation stage: fixed source subtitles → target-language SRT.

This is deliberately a thin orchestration layer over the existing
``translate_srt.py`` (batching, glossary, OP/ED, retries).  It adds the
things the pipeline was missing: a stable input/output convention, glossary
discovery, a QA report, and non-fatal failure handling.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from lib.whisper_utils import extract_ep_number

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
TRANSLATOR = SCRIPTS_DIR / 'translate_srt.py'

GLOSSARY_CANDIDATES = (
    'temp/nouns/noun_mappings.json',
    'temp/scans/noun_mappings.json',
    'noun_mappings.json',
)


def find_source_srt(target_dir: str, episode: str) -> str | None:
    """Find the episode's source SRT/ASS in ``target_dir``."""
    if not target_dir or not os.path.isdir(target_dir):
        return None
    exact = []
    for name in os.listdir(target_dir):
        if not name.lower().endswith(('.srt', '.ass')):
            continue
        stem = os.path.splitext(name)[0]
        if extract_ep_number(stem) == episode or episode in stem:
            exact.append(os.path.join(target_dir, name))
    if not exact:
        return None
    # Prefer SRT over ASS, then the shortest path (less decorated name).
    return sorted(exact, key=lambda p: (not p.lower().endswith('.srt'),
                                       len(p)))[0]


def find_glossary(project_dir: str) -> str | None:
    for rel in GLOSSARY_CANDIDATES:
        path = os.path.join(project_dir, rel)
        if os.path.isfile(path):
            return path
    return None


def build_command(source: str, output: str, glossary: str | None,
                  target_lang: str, *, dry_run: bool = False,
                  qa_report: str = '', max_chars: int = 0,
                  max_cps: float = 0.0, repair: bool = True) -> list[str]:
    cmd = [sys.executable, str(TRANSLATOR),
           '--input', source, '--output', output,
           '--source-lang', 'ja']
    if glossary:
        cmd += ['--mappings', glossary]
    if dry_run:
        cmd.append('--dry-run')
    if qa_report:
        cmd += ['--qa-report', qa_report]
    if repair:
        cmd.append('--repair')
    if max_chars > 0:
        cmd += ['--max-chars', str(max_chars)]
    if max_cps > 0:
        cmd += ['--max-cps', str(max_cps)]
    # Output language is currently Chinese; keep the argument explicit so a
    # future backend can switch without changing callers.
    if target_lang != 'zh':
        raise ValueError(f'unsupported translation target: {target_lang!r}')
    return cmd


def translate_episode(project_dir: str, episode: str, *,
                      target_dir: str | None = None,
                      target_lang: str = 'zh',
                      dry_run: bool = False,
                      max_chars: int = 0,
                      max_cps: float = 0.0,
                      repair: bool = True,
                      runner=None) -> dict:
    """Translate one episode. Returns a result dict; never raises."""
    project_dir = os.path.abspath(project_dir)
    target_dir = os.path.abspath(target_dir or project_dir)
    source = find_source_srt(target_dir, episode)
    if not source:
        return {'episode': episode, 'ok': False,
                'message': 'source SRT not found'}
    out_dir = os.path.join(project_dir, 'translated', target_lang)
    output = os.path.join(out_dir, f'{episode}.srt')
    qa_report = os.path.join(out_dir, f'{episode}.qa.json')
    os.makedirs(out_dir, exist_ok=True)
    glossary = find_glossary(project_dir)

    try:
        cmd = build_command(source, output, glossary, target_lang,
                            dry_run=dry_run, qa_report=qa_report,
                            max_chars=max_chars, max_cps=max_cps,
                            repair=repair)
    except ValueError as exc:
        return {'episode': episode, 'ok': False, 'message': str(exc)}

    print(f'[translate] {episode}: {os.path.basename(source)} → {output}'
          + (f' (glossary: {os.path.basename(glossary)})' if glossary else ''),
          file=sys.stderr)
    if dry_run:
        print('  ' + ' '.join(cmd), file=sys.stderr)
        return {'episode': episode, 'ok': True, 'dry_run': True,
                'source': source, 'output': output, 'qa_report': qa_report}

    run = runner or subprocess.run
    try:
        proc = run(cmd, cwd=project_dir)
        code = getattr(proc, 'returncode', 0)
    except Exception as exc:
        return {'episode': episode, 'ok': False,
                'message': f'translation crashed: {exc}'}
    if code != 0:
        return {'episode': episode, 'ok': False,
                'message': f'translator exited {code}'}
    if not os.path.exists(output):
        return {'episode': episode, 'ok': False,
                'message': 'translator produced no output'}
    return {'episode': episode, 'ok': True, 'source': source,
            'output': output, 'qa_report': qa_report}


def step_translate_final(project_dir: str, episodes: list[str] | None = None,
                         *, target_dir: str | None = None,
                         target_lang: str = 'zh', dry_run: bool = False,
                         max_chars: int = 0, max_cps: float = 0.0,
                         repair: bool = True, runner=None) -> list[dict]:
    """Translate all selected episodes; failures are reported, not fatal."""
    project_dir = os.path.abspath(project_dir)
    target_dir = os.path.abspath(target_dir or project_dir)
    episodes = list(episodes or [])
    if not episodes:
        for name in sorted(os.listdir(target_dir)):
            if name.lower().endswith(('.srt', '.ass')):
                ep = extract_ep_number(os.path.splitext(name)[0])
                if ep and ep not in episodes:
                    episodes.append(ep)
    results = []
    for ep in episodes:
        results.append(translate_episode(
            project_dir, ep, target_dir=target_dir, target_lang=target_lang,
            dry_run=dry_run, max_chars=max_chars, max_cps=max_cps,
            repair=repair, runner=runner))
    ok = sum(1 for r in results if r.get('ok'))
    print(f'[translate] {ok}/{len(results)} episodes translated', file=sys.stderr)
    return results
