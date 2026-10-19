#!/usr/bin/env python3
"""Context review CLI: near-term promotion + style flags, grounded in a series profile.

Examples:
    # candidates come from: nouns/normalize_variants.py --discover
    python nouns/context_review.py --project <proj> \
        --glossary <proj>/glossaries/sabu.series.json \
        --meta <proj>/glossaries/sabu.series.meta.json \
        --candidates <proj>/temp/variants_candidates.json \
        --mode near --dry-run --report <proj>/temp/context_near.json

    # sampled style review (read-only)
    python nouns/context_review.py ... --mode style --sample 40 \
        --report <proj>/temp/context_style.json

    # apply only the 'promote' verdicts to _meta.aliases (backup first)
    python nouns/context_review.py ... --mode near --apply-approvals

Nothing here rewrites subtitles.  ``--apply-approvals`` only edits the reviewed
alias table; the normalizer still has to be run afterwards.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys

import lib._path  # noqa: F401,E402

from lib.context.profile import load_or_build, profile_brief  # noqa: E402
from lib.glossary import load_glossary, load_meta  # noqa: E402
from lib.review.context_agent import (  # noqa: E402
    apply_approvals, review_near_terms, review_style,
)
from lib.subtitle_io import read_subtitles  # noqa: E402


def _load_candidates(path: str) -> list[dict]:
    with open(path, 'r', encoding='utf-8') as handle:
        data = json.load(handle)
    if isinstance(data, dict):
        return list(data.get('candidates') or [])
    return list(data or [])


CREDIT_PATTERNS = ('字幕制作', '翻译：', '时间轴', '校对', '压制', 'Society',
                   'TheSkaroHunting', 'HokutonoGun', '特别鸣谢')


def _is_credit_line(text: str) -> bool:
    return any(pattern in str(text or '') for pattern in CREDIT_PATTERNS)


def _sample_style(zh_dir: str, per_episode: int) -> list[dict]:
    items = []
    for path in sorted(glob.glob(os.path.join(zh_dir, '*.srt'))):
        episode = os.path.basename(path)[:-4]
        cues = read_subtitles(path, mark_garbled=False)
        if not cues:
            continue
        step = max(1, len(cues) // max(1, per_episode))
        picked = 0
        for index in range(0, len(cues), step):
            if picked >= per_episode:
                break
            cue = cues[index]
            text = str(cue.get('text') or '')
            if not text.strip() or _is_credit_line(text):
                continue
            items.append({'episode': episode, 'cue': index + 1,
                          'start': cue.get('start', ''), 'text': text})
            picked += 1
    return items


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', default='.')
    parser.add_argument('--glossary', required=True)
    parser.add_argument('--meta', required=True)
    parser.add_argument('--series', default='')
    parser.add_argument('--profile', default='')
    parser.add_argument('--candidates', default='')
    parser.add_argument('--zh-dir', default='')
    parser.add_argument('--mode', choices=('near', 'style', 'style-fix'),
                        required=True)
    parser.add_argument('--sample', type=int, default=20,
                        help='style mode: cues sampled per episode')
    parser.add_argument('--dry-run', action='store_true', default=False)
    parser.add_argument('--apply-approvals', action='store_true', default=False)
    parser.add_argument('--refresh-profile', action='store_true', default=False)
    parser.add_argument('--report', default='')
    args = parser.parse_args(argv)

    glossary = load_glossary(args.glossary)
    meta = load_meta(args.meta)
    profile_path = args.profile or os.path.join(
        args.project, 'temp', 'series_profile.json')
    series = args.series or os.path.basename(os.path.abspath(args.project))
    profile = load_or_build(profile_path, series=series, glossary=glossary,
                            meta=meta, project_dir=args.project,
                            refresh=args.refresh_profile)
    brief = profile_brief(profile)
    print(f'[context] profile {profile_path} '
          f'(episodes {profile.get("episode_count")}, '
          f'summary {len(profile.get("summary") or "")} chars)', file=sys.stderr)

    if args.mode == 'near':
        if not args.candidates:
            print('--mode near needs --candidates', file=sys.stderr)
            return 2
        candidates = _load_candidates(args.candidates)
        decisions = review_near_terms(candidates, brief)
        summary = {'promote': 0, 'reject': 0, 'uncertain': 0}
        for decision in decisions:
            summary[decision['verdict']] += 1
        print(f'[context] near terms: {len(decisions)} decisions {summary}',
              file=sys.stderr)
        payload = {'mode': 'near', 'profile': profile_path,
                   'decisions': decisions, 'summary': summary}
        if args.apply_approvals:
            result = apply_approvals(args.meta, decisions, glossary)
            payload['applied'] = result
            print(f'[context] applied {len(result["applied"])} alias(es), '
                  f'skipped {len(result["skipped"])}', file=sys.stderr)
        elif args.dry_run:
            payload['applied'] = {'applied': [], 'skipped': []}
    elif args.mode == 'style-fix':
        from lib.context.style_lint import lint_cues
        from lib.context.style_patch import plan_patches
        from lib.subtitle_io import read_subtitles as _read

        zh_dir = args.zh_dir or os.path.join(args.project, 'translated', 'zh')
        flagged = []
        for path in sorted(glob.glob(os.path.join(zh_dir, '*.srt'))):
            episode = os.path.basename(path)[:-4]
            result = lint_cues(_read(path, mark_garbled=False), episode=episode)
            flagged.extend(result['flags'])
        names = {str(v) for v in glossary.values()}
        patches = plan_patches(flagged, names=names)
        print(f'[context] style-fix: flagged {len(flagged)}, '
              f'accepted {len(patches["accepted"])}, '
              f'rejected {len(patches["rejected"])}', file=sys.stderr)
        payload = {'mode': 'style-fix', 'profile': profile_path,
                   'flagged': len(flagged), **patches}
    else:
        zh_dir = args.zh_dir or os.path.join(args.project, 'translated', 'zh')
        items = _sample_style(zh_dir, args.sample)
        flags = review_style(items, brief)
        by_issue = {}
        for flag in flags:
            for issue in flag['issues']:
                by_issue[issue] = by_issue.get(issue, 0) + 1
        print(f'[context] style: sampled {len(items)}, flagged {len(flags)} '
              f'{by_issue}', file=sys.stderr)
        payload = {'mode': 'style', 'profile': profile_path,
                   'sampled': len(items), 'flags': flags,
                   'by_issue': by_issue}

    if args.report:
        os.makedirs(os.path.dirname(args.report) or '.', exist_ok=True)
        with open(args.report, 'w', encoding='utf-8', newline='\n') as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
        print(f'[context] report {args.report}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
