#!/usr/bin/env python3
"""Write the harness-local ``local.json`` for this machine.

The repo must not carry machine paths or keys.  Run this **inside the synced
harness copy** after ``sync_skill.py`` (or point ``--output`` at it): it fills
model paths, the Qwen venv, Whisper binaries, ffmpeg and the LLM endpoint from
the environment / ``~/.claude/settings.json``.

    python scripts/harness/init_local.py --check
    python scripts/harness/init_local.py --force
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[2]


def _settings_env() -> dict:
    path = Path.home() / '.claude' / 'settings.json'
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, json.JSONDecodeError):
        return {}
    env = data.get('env') if isinstance(data, dict) else None
    return env if isinstance(env, dict) else {}


def _detect(args) -> dict:
    env = _settings_env()
    repo_models = SKILL_ROOT / 'models'
    synced = {}
    try:
        synced = json.loads((SKILL_ROOT / 'SYNCED.json').read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        synced = {}
    source = Path(synced['source']) if synced.get('source') else None

    models_dir = args.models_dir or os.environ.get('MODELS_DIR') or ''
    if not models_dir and repo_models.is_dir():
        models_dir = str(repo_models)
    if not models_dir and source and (source / 'models').is_dir():
        models_dir = str(source / 'models')

    qwen = args.qwen_python or os.environ.get('QWEN_PYTHON') or ''
    if not qwen:
        candidate = SKILL_ROOT / '.venv-qwen' / 'Scripts' / 'python.exe'
        if candidate.is_file():
            qwen = str(candidate)
        elif source and (source / '.venv-qwen' / 'Scripts' / 'python.exe').is_file():
            qwen = str(source / '.venv-qwen' / 'Scripts' / 'python.exe')

    ffmpeg = args.ffmpeg or os.environ.get('FFMPEG_BIN') or shutil.which('ffmpeg') or ''

    whisper_cli = args.whisper_cli or os.environ.get('WHISPER_CLI') or env.get('WHISPER_CLI', '')
    whisper_model = (args.whisper_model or os.environ.get('WHISPER_MODEL')
                     or env.get('WHISPER_MODEL', ''))
    whisper_retry = (args.whisper_retry_model
                     or os.environ.get('WHISPER_RETRY_MODEL')
                     or env.get('WHISPER_RETRY_MODEL', ''))

    llm = {
        'api_key': args.api_key or os.environ.get('LLM_API_KEY')
        or env.get('LLM_API_KEY', ''),
        'base_url': args.llm_base_url or os.environ.get('LLM_BASE_URL')
        or env.get('LLM_BASE_URL', ''),
        'model': args.llm_model or os.environ.get('LLM_MODEL')
        or env.get('LLM_MODEL', ''),
    }
    bili23_config = (args.bili23_config or os.environ.get('BILI23_CONFIG')
                     or str(Path(os.environ.get('APPDATA', '')) /
                            'Bili23 Downloader' / 'config.json'))
    if not Path(bili23_config).is_file():
        bili23_config = ''
    bili23_exe = args.bili23_exe or os.environ.get('BILI23_EXE') or ''
    gameviewer_dir = (args.gameviewer_dir or os.environ.get('GAMEVIEWER_DIR')
                      or '')

    return {
        'schema_version': 1,
        'paths': {
            'models_dir': models_dir,
            'qwen_python': qwen,
            'ffmpeg': ffmpeg,
            'whisper_cli': whisper_cli,
            'whisper_model': whisper_model,
            'whisper_retry_model': whisper_retry,
            'asr_model': args.asr_model or '',
            'aligner_model': args.aligner_model or '',
            'bili23_config': bili23_config,
            'bili23_exe': bili23_exe,
            'gameviewer_dir': gameviewer_dir,
        },
        'llm': llm,
        'video_roots': [v for v in (args.video_roots or '').split(';') if v],
        'projects_root': args.projects_root or '',
    }


def _check(config: dict) -> list[str]:
    problems = []
    paths = config['paths']
    for key in ('models_dir', 'qwen_python', 'whisper_cli', 'whisper_model',
                'ffmpeg'):
        value = paths.get(key, '')
        if not value:
            problems.append(f'{key}: empty')
        elif key == 'ffmpeg' and value == 'ffmpeg':
            continue          # resolved from PATH
        elif not os.path.exists(value):
            problems.append(f'{key}: missing -> {value}')
    for key in ('api_key', 'base_url', 'model'):
        if not config['llm'].get(key):
            problems.append(f'llm.{key}: empty')
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(SKILL_ROOT / 'local.json'))
    parser.add_argument('--models-dir', default='')
    parser.add_argument('--qwen-python', default='')
    parser.add_argument('--ffmpeg', default='')
    parser.add_argument('--whisper-cli', default='')
    parser.add_argument('--whisper-model', default='')
    parser.add_argument('--whisper-retry-model', default='')
    parser.add_argument('--asr-model', default='')
    parser.add_argument('--aligner-model', default='')
    parser.add_argument('--api-key', default='')
    parser.add_argument('--llm-base-url', default='')
    parser.add_argument('--llm-model', default='')
    parser.add_argument('--video-roots', default='', help=';-separated')
    parser.add_argument('--projects-root', default='')
    parser.add_argument('--bili23-config', default='')
    parser.add_argument('--bili23-exe', default='')
    parser.add_argument('--gameviewer-dir', default='')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args(argv)

    config = _detect(args)
    problems = _check(config)
    if args.check:
        display = json.loads(json.dumps(config))
        key = display.get('llm', {}).get('api_key', '')
        if key:
            display['llm']['api_key'] = key[:6] + '…' + key[-4:]
        print(json.dumps({'config': display, 'problems': problems},
                         ensure_ascii=False, indent=2))
        return 0 if not problems else 2

    out = Path(args.output)
    if out.exists() and not args.force:
        print(f'{out} exists; use --force to overwrite', file=sys.stderr)
        return 2
    payload = dict(config)
    key = payload['llm'].get('api_key', '')
    if key and 'api_key_masked' not in payload:
        payload['api_key_masked'] = key[:6] + '…' + key[-4:]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n',
                   encoding='utf-8')
    print(f'wrote {out}')
    for problem in problems:
        print(f'  ! {problem}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
