#!/usr/bin/env python3
"""Fetch **human** Bilibili CC subtitles for an upload and write SRT files.

Most subtitle tracks on Bilibili are machine-generated (``ai-zh``,
``type: 1`` + ``ai_status: 2``) and their quality is far below what a translation
pipeline needs — translating from them, or using them as a cross-check, drags
the whole project down.  This tool therefore **skips AI tracks by default** and
only keeps tracks somebody uploaded by hand (``type: 0``, language such as
``zh-CN`` / ``zh-Hans`` / ``en-US``).

Only run it when the user says the subtitles were uploaded by a person.  If the
upload has nothing but AI tracks, the honest answer is "no reference available"
and the project falls back to ASR + translation.

Output goes to the project's reference directory (``subtitles.reference_dir`` in
the project's ``projects_registry.json`` entry, default
``bin/reference_docs/参考字幕``), raw JSON alongside.

    python scripts/acquire/bili_subs.py --bvid BV... --project <project> --check
    python scripts/acquire/bili_subs.py --bvid BV... --project <project>
    python scripts/acquire/bili_subs.py --bvid BV... --project <p> --include-ai  # 仅当用户明确要
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

DEFAULT_CONFIG = pathlib.Path(os.environ.get('APPDATA', '')) / 'Bili23 Downloader' / 'config.json'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      'Chrome/131.0 Safari/537.36')


def _cookie_header(config_path: pathlib.Path) -> str:
    try:
        raw = config_path.read_text(encoding='utf-8-sig')
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return ''
    cookies = data.get('Cookie') or {}
    if not isinstance(cookies, dict):
        return ''
    return '; '.join(f'{k}={v}' for k, v in cookies.items() if isinstance(v, str) and v)


def _headers(cookie: str) -> dict:
    headers = {'User-Agent': UA, 'Referer': 'https://www.bilibili.com/'}
    if cookie:
        headers['Cookie'] = cookie
    return headers


def _get_json(url: str, headers: dict, attempts: int = 3) -> dict:
    last = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=40) as response:
                return json.loads(response.read().decode('utf-8'))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f'GET {url} failed: {last}')


def _get_bytes(url: str, headers: dict, attempts: int = 3) -> bytes:
    last = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=40) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f'GET {url} failed: {last}')


def srt_time(seconds: float) -> str:
    ms = int(round(float(seconds) * 1000))
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    secs, ms = divmod(ms, 1000)
    return f'{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}'


def to_srt(payload: dict) -> str:
    blocks = []
    for cue in payload.get('body') or []:
        text = str(cue.get('content', '')).strip()
        if not text:
            continue
        blocks.append(
            f"{len(blocks) + 1}\n"
            f"{srt_time(cue.get('from', 0))} --> {srt_time(cue.get('to', 0))}\n"
            f"{text}\n"
        )
    return '\n'.join(blocks)


def _default_out(project: str) -> str:
    if project:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
        from lib import media_paths
        return media_paths.reference_subs_dir(project)
    return ''


def is_ai_track(subtitle: dict) -> bool:
    """Bilibili marks machine-made tracks with type=1 / ai_status, plus ai-* langs."""
    lan = str(subtitle.get('lan') or '').lower()
    if lan.startswith('ai-'):
        return True
    try:
        if int(subtitle.get('type') or 0) == 1:
            return True
    except (TypeError, ValueError):
        pass
    return bool(subtitle.get('ai_status')) and int(subtitle.get('ai_status') or 0) > 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--bvid', required=True)
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    parser.add_argument('--project', default='',
                        help='project dir; default output is its reference dir')
    parser.add_argument('--out-dir', default='')
    parser.add_argument('--raw-dir', default='', help='default: <out>/raw')
    parser.add_argument('--langs', default='',
                        help='comma list (zh-CN,zh-Hans,en-US); empty = all human tracks')
    parser.add_argument('--include-ai', action='store_true',
                        help='also save AI tracks (only when the user asked for them)')
    parser.add_argument('--check', action='store_true',
                        help='only report which parts carry subtitles')
    args = parser.parse_args(argv)

    headers = _headers(_cookie_header(pathlib.Path(args.config)))
    want = {x.strip() for x in args.langs.split(',') if x.strip()}

    pages = _get_json(
        f'https://api.bilibili.com/x/player/pagelist?bvid={args.bvid}',
        headers).get('data') or []
    if not pages:
        print('no parts returned; check the bvid or log in', file=sys.stderr)
        return 2
    print(f'{len(pages)} part(s)')

    out_dir = args.out_dir or _default_out(args.project)
    if not args.check and not out_dir:
        print('no output dir: pass --out-dir or --project', file=sys.stderr)
        return 2
    out_path = pathlib.Path(out_dir) if out_dir else pathlib.Path('.')
    raw_path = pathlib.Path(args.raw_dir) if args.raw_dir else out_path / 'raw'
    if not args.check:
        out_path.mkdir(parents=True, exist_ok=True)
        raw_path.mkdir(parents=True, exist_ok=True)

    written = 0
    skipped_ai = 0
    human_parts, ai_only, no_subs = [], [], []
    for page in pages:
        number = int(page['page'])
        episode = f'EP{number:03d}'
        info = _get_json(
            'https://api.bilibili.com/x/player/v2'
            f"?bvid={args.bvid}&cid={page['cid']}", headers).get('data') or {}
        subtitles = (info.get('subtitle') or {}).get('subtitles') or []
        human = [s for s in subtitles if not is_ai_track(s)]
        ai = [s for s in subtitles if is_ai_track(s)]
        if human:
            human_parts.append(number)
        elif ai:
            ai_only.append(number)
        else:
            no_subs.append(number)
        if not args.include_ai:
            skipped_ai += len(ai)
        if not human and not args.include_ai:
            if args.check:
                if ai:
                    print(f'{episode}: 只有 AI 字幕（{", ".join(str(s.get("lan")) for s in ai)}），默认不取')
                continue
            if ai:
                print(f'{episode}: 只有 AI 字幕，跳过（--include-ai 才会取）')
            else:
                print(f'{episode}: no subtitles')
            continue
        candidates = human if not args.include_ai else subtitles
        if not candidates:
            if not args.check:
                print(f'{episode}: no subtitles')
            continue
        for subtitle in candidates:
            lan = str(subtitle.get('lan') or '')
            if want and lan not in want:
                continue
            url = str(subtitle.get('subtitle_url') or '')
            if url.startswith('//'):
                url = 'https:' + url
            if not url:
                continue
            kind = 'AI' if is_ai_track(subtitle) else '人工'
            if args.check:
                print(f'{episode}: {lan} ({kind})')
                continue
            try:
                payload = json.loads(_get_bytes(url, headers).decode('utf-8'))
            except RuntimeError as exc:
                print(f'{episode} {lan}: {exc}')
                continue
            slug = re.sub(r'[^A-Za-z0-9_-]', '', lan) or 'sub'
            (raw_path / f'{episode}.{slug}.json').write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
            srt = to_srt(payload)
            if not srt.strip():
                print(f'{episode} {lan}: empty payload')
                continue
            (out_path / f'{episode}.{slug}.srt').write_text(
                srt, encoding='utf-8', newline='\n')
            print(f"{episode} {lan} ({kind}): {srt.count(' --> ')} cues -> "
                  f"{pathlib.Path(out_path).name}/{episode}.{slug}.srt")
            written += 1
            time.sleep(0.15)

    if args.check:
        print(f'{len(pages)} part(s): {len(human_parts)} 有 {len(ai_only)} AI-only '
              f'{len(no_subs)} 无字幕；人工字幕在 '
              f'{", ".join(map(str, human_parts)) or "无"}')
        return 0
    print(f'wrote {written} file(s) into {out_path}')
    if ai_only and not args.include_ai:
        print(f'{len(ai_only)} part(s) only had AI subtitles and were skipped — '
              'B站 AI 字幕质量低，默认不当参考', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
