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
import shutil
import sys
import time

import lib._path  # noqa: F401,E402

from lib.context.profile import load_or_build, profile_brief  # noqa: E402
from lib.glossary import load_glossary, load_meta  # noqa: E402
from lib.review.context_agent import (  # noqa: E402
    apply_approvals, review_near_terms, review_style,
)
from lib.subtitle_io import read_subtitles, write_subtitles  # noqa: E402
from lib.project_paths import temp_root


def _backup(path: str) -> str:
    """One .bak per file; a timestamped name if a .bak already exists."""
    bak = path + '.bak'
    if os.path.exists(bak):
        stamp = time.strftime('%Y%m%d%H%M%S')
        bak = f'{path}.{stamp}.bak'
    shutil.copyfile(path, bak)
    return bak


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
    parser.add_argument('--mode',
                        choices=('near', 'style', 'style-fix', 'namefix', 'oped'),
                        required=True)
    parser.add_argument('--ja-dir', default='',
                        help='namefix mode: Japanese SRT directory')
    parser.add_argument('--allow-near', action='store_true',
                        help='namefix mode: also propose one-mora mismatches')
    parser.add_argument('--max-proposals', type=int, default=120)
    parser.add_argument('--in-place', action='store_true', default=False,
                        help='namefix/oped: write the source files (one .bak per file)')
    parser.add_argument('--out-dir', default='',
                        help='namefix mode: write corrected copies here')
    parser.add_argument('--sample', type=int, default=20,
                        help='style mode: cues sampled per episode')
    parser.add_argument('--dry-run', action='store_true', default=False)
    parser.add_argument('--apply-approvals', action='store_true', default=False)
    parser.add_argument('--apply-text-keyed', action='store_true', default=False,
                        help='oped mode: replace every text match (writes copies to --out-dir)')
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
    elif args.mode == 'oped':
        from lib.context.oped_canonical import fill_candidates

        if not args.candidates:
            print('--mode oped needs --candidates (oped_fixer --ai-review JSON)',
                  file=sys.stderr)
            return 2
        with open(args.candidates, 'r', encoding='utf-8') as handle:
            payload_in = json.load(handle)

        def _chat(messages):
            from lib.llm import call_chat
            text = call_chat(messages, temperature=0.1, max_tokens=1500,
                             json_mode=True)
            return text, ('' if text else 'empty response')

        filled, stats = fill_candidates(
            payload_in.get('candidates') or [], meta=meta,
            profile_brief=brief, chat=_chat)
        payload_out = dict(payload_in)
        payload_out['candidates'] = filled
        payload_out['review_stats'] = stats
        print(f'[context] oped: {len(filled)} candidates, '
              f'meta {stats["meta"]}, llm {stats["llm"]}, '
              f'kept-empty {stats["kept_empty"]}, rejected {stats["rejected"]}',
              file=sys.stderr)
        payload = {'mode': 'oped', 'profile': profile_path, **stats,
                   'candidates': filled}
        if args.report:
            with open(args.report, 'w', encoding='utf-8', newline='\n') as handle:
                json.dump(payload_out, handle, ensure_ascii=False, indent=2)
                handle.write('\n')
            print(f'[context] report {args.report}', file=sys.stderr)
        if args.apply_approvals:
            import subprocess
            zh_dir = args.zh_dir or os.path.join(args.project, 'translated', 'zh')
            script = os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), 'fix', 'oped_fixer.py')
            cmd = [sys.executable, script, zh_dir, '--lang', 'zh',
                   '--apply-ai-review', args.report]
            print('[context] apply:', ' '.join(cmd), file=sys.stderr)
            subprocess.run(cmd, check=False)
        if args.apply_text_keyed:
            from lib.context.oped_canonical import apply_text_keyed
            zh_dir = args.zh_dir or os.path.join(args.project, 'translated', 'zh')
            cues_by_ep = {}
            for path in sorted(glob.glob(os.path.join(zh_dir, '*.srt'))):
                cues_by_ep[os.path.basename(path)[:-4]] = read_subtitles(
                    path, mark_garbled=False)
            new_cues, changes = apply_text_keyed(cues_by_ep, filled)
            touched = sorted({c['episode'] for c in changes})
            if args.in_place:
                out_dir = zh_dir
                for episode in touched:
                    _backup(os.path.join(zh_dir, f'{episode}.srt'))
            else:
                out_dir = args.out_dir or os.path.join(temp_root(args.project),
                                                       'oped_textkeyed')
                os.makedirs(out_dir, exist_ok=True)
            for episode in touched:
                write_subtitles(os.path.join(out_dir, f'{episode}.srt'),
                                new_cues[episode])
            payload['text_keyed'] = {'changes': len(changes),
                                     'episodes': touched,
                                     'out_dir': out_dir}
            print(f'[context] text-keyed: {len(changes)} cue(s) in '
                  f'{len(touched)} episode(s) -> {out_dir}', file=sys.stderr)
        return 0
    elif args.mode == 'namefix':
        from janome.tokenizer import Tokenizer

        from lib.asr.namefix import (
            apply_repairs, load_name_entries, propose_repairs,
        )
        from lib.review.context_agent import review_name_repairs

        if not args.ja_dir:
            print('--mode namefix needs --ja-dir', file=sys.stderr)
            return 2
        entries = load_name_entries(glossary, meta)
        allow = frozenset(meta.get('namefix_allow_surfaces') or ())
        near = args.allow_near or bool(meta.get('namefix_near_miss'))
        tokenizer = Tokenizer()

        proposals = []
        files = {}
        for path in sorted(glob.glob(os.path.join(args.ja_dir, '*.srt'))):
            episode = os.path.basename(path)[:-4]
            cues = read_subtitles(path, mark_garbled=False)
            files[episode] = (path, cues)
            for index, cue in enumerate(cues):
                text = str(cue.get('text') or '')
                for hit in propose_repairs(text, entries, tokenizer=tokenizer,
                                          allow_surfaces=allow, near_miss=near):
                    proposals.append({'episode': episode, 'cue': index + 1,
                                      'start': cue.get('start', ''), **hit})
                    if len(proposals) >= args.max_proposals:
                        break
                if len(proposals) >= args.max_proposals:
                    break
            if len(proposals) >= args.max_proposals:
                break

        decisions = review_name_repairs(proposals, brief)
        accepted = [d for d in decisions if d['verdict'] == 'accept']
        counts = {v: 0 for v in ('accept', 'reject', 'uncertain')}
        for decision in decisions:
            counts[decision['verdict']] += 1
        print(f'[context] namefix: {len(proposals)} proposals, {counts}, '
              f'near-miss={near}', file=sys.stderr)

        by_episode = {}
        for decision in accepted:
            by_episode.setdefault(decision['episode'], []).append(decision)
        written = []
        if (args.out_dir or args.in_place) and by_episode:
            if args.out_dir:
                os.makedirs(args.out_dir, exist_ok=True)
            for episode, rows in by_episode.items():
                path, cues = files[episode]
                repaired = list(cues)
                for row in rows:
                    idx = row['cue'] - 1
                    text = str(repaired[idx].get('text') or '')
                    repaired[idx] = dict(repaired[idx],
                                         text=apply_repairs(text, [row]))
                if args.in_place:
                    _backup(path)
                    target = path
                else:
                    target = os.path.join(args.out_dir, f'{episode}.srt')
                write_subtitles(target, repaired)
                written.append(target)
        payload = {'mode': 'namefix', 'profile': profile_path,
                   'proposals': len(proposals), 'near_miss': near,
                   'counts': counts, 'decisions': decisions,
                   'written': written}
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
