#!/usr/bin/env python3
"""Read a B站 upload's structure: pages, durations, subtitle availability.

Why this exists
---------------
Before translating a 52-part upload you need three facts that the video files
alone do not tell you:

- **page → episode mapping** (``P1`` is episode 1 only if the uploader says so);
- **per-page duration**, so a "double-length" part (an extra-long teaser, a
  merged two-episode file) shows up before you spend hours on it;
- **subtitle tracks on the upload**, which decide whether the project has a
  reference at all (an empty ``subtitle.list`` means ASR is the only route).

It also records the title and description, which is where the uploader often
puts the series' Chinese/English name — the first hint for naming.

    python scripts/acquire/bili_meta.py --bvid BV1Ub411P7CU
    python scripts/acquire/bili_meta.py --bvid BV... --out data/reference/bili_meta.json
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

VIEW_API = 'https://api.bilibili.com/x/web-interface/view?bvid='
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')


def fetch_view(bvid: str, timeout: float = 20.0) -> dict:
    request = urllib.request.Request(VIEW_API + bvid,
                                     headers={'User-Agent': UA})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode('utf-8'))
    if payload.get('code') != 0:
        raise RuntimeError(f'B站 API error {payload.get("code")}: '
                           f'{payload.get("message")}')
    return payload['data']


def summarize(data: dict) -> dict:
    """Structure only: what a subtitle project needs, not the whole payload."""
    import statistics

    pages = []
    for page in data.get('pages') or []:
        pages.append({'page': page.get('page'), 'cid': page.get('cid'),
                      'part': page.get('part'),
                      'duration_s': page.get('duration')})
    durations = [p['duration_s'] for p in pages if p.get('duration_s')]
    typical = statistics.median(durations) if durations else 0
    tracks = ((data.get('subtitle') or {}).get('list') or [])
    return {
        'bvid': data.get('bvid'),
        'title': data.get('title'),
        'desc': (data.get('desc') or '').strip(),
        'owner': (data.get('owner') or {}).get('name'),
        'pages': len(pages),
        'duration_total_s': data.get('duration'),
        'duration_min': round((data.get('duration') or 0) / 60, 1),
        'page_durations': sorted({p['duration_s'] for p in pages}),
        'odd_pages': [p for p in pages if p.get('duration_s') and
                      abs(p['duration_s'] - typical) > 60],
        'subtitle_tracks': [{'lang': t.get('lan'), 'ai': t.get('ai_type')}
                            for t in tracks],
        'has_subtitles': bool(tracks),
        'items': pages,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--bvid', required=True)
    ap.add_argument('--out', default='', help='write the summary JSON here')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args(argv)

    try:
        data = fetch_view(args.bvid)
    except (urllib.error.URLError, RuntimeError, TimeoutError) as exc:
        print(f'bili_meta: {exc}', file=sys.stderr)
        return 2
    summary = summarize(data)

    if args.out:
        with open(args.out, 'w', encoding='utf-8', newline='\n') as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=1)
            fh.write('\n')
        print(f'wrote {args.out}', file=sys.stderr)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    print(f"title      : {summary['title']}")
    print(f"owner      : {summary['owner']}")
    print(f"pages      : {summary['pages']} "
          f"({summary['duration_min']} min total)")
    print(f"durations  : {summary['page_durations']} s")
    print(f"subtitles  : {summary['subtitle_tracks'] or 'none'}")
    for page in summary['odd_pages']:
        print(f"  ! page {page['page']} is {page['duration_s']} s "
              f"— check it is not two episodes")
    print(f"desc       : {summary['desc'][:120]}")
    return 0


if __name__ == '__main__':
    import os

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    raise SystemExit(main())
