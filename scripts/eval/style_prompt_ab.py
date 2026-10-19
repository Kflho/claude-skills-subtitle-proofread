#!/usr/bin/env python3
"""Which style-hint format actually helps? A controlled A/B on one episode.

The question "keywords vs prose vs a structured spec" is an empirical one, so
this harness holds everything else constant (same cues, model, glossary,
temperature, batch size) and varies only the style block appended to the system
prompt.  Metrics are deterministic:

- modern/dialect/anachronism hits (``lib.context.style_lint``) — lower is better;
- Japanese-residue / echo violations and length ratio — must not regress;
- canonical-name mentions — must not drop.

No reference translation is needed, and nothing is applied to any project.

Usage:
    python scripts/eval/style_prompt_ab.py --ja <EP051.srt> \
        --mappings <glossary.json> --limit 120 --out-dir temp/exp/style_ab
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

import lib._path  # noqa: F401

from lib.context.style_lint import lint_text
from lib.subtitle_io import read_subtitles
from lib.translation.qa import qa_report

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from translate_srt import USER_TEMPLATE, _build_system_prompt  # noqa: E402

NAME_FORMS = {'佐武': ('サブ', 'サブヤン', '佐武'),
              '市': ('イチ', '市')}

ARMS = {
    'A_none': '',
    'B_keywords': '译制风格（参考）：古语腔、粗口、敬语较多、短句为主',
    'C_prose': ('本片是江户时代剧。对白以口语为主，粗话较多；旁白略带书面腔。'
                '请保持这种语体，不要用现代网络语。'),
    'D_structured': ('旁白：半文半白\n对白：江户町人口语\n'
                     '禁止：现代网络语、地域方言'),
    'E_negative': '禁止使用现代网络语、网络梗、东北方言；保持时代剧用语。',
    'F_lexicon': ('禁止使用以下词及其变体：离谱、搞定、整活、绝了、破防、给力、'
                  '无语、拉满、没毛病、老铁、咋、啥、俺、咱、忒、贼。'
                  '时代剧对白用「你这厮／混账／在下／阁下」这类词。'),
}

WEN = set('之其乃矣焉兮汝吾莫勿亦尔斯遂故也者皆')
KOU = set('你我啊吧呢嘛啦呗咋甭咱啥嘿喂呀哦')


def _register_scores(texts) -> dict:
    """Per-cue register score; low spread = consistent voice."""
    scores = []
    for text in texts:
        text = str(text or '')
        if len(text) < 4:
            continue
        wen = sum(1 for ch in text if ch in WEN) / len(text)
        kou = sum(1 for ch in text if ch in KOU) / len(text)
        scores.append(wen - kou)
    if len(scores) < 3:
        return {'register_mean': 0.0, 'register_std': 0.0,
                'register_range': 0.0, 'cues': len(scores)}
    mean = sum(scores) / len(scores)
    var = sum((s - mean) ** 2 for s in scores) / len(scores)
    return {'register_mean': round(mean, 4),
            'register_std': round(var ** 0.5, 4),
            'register_range': round(max(scores) - min(scores), 4),
            'cues': len(scores)}


def _parse(text: str, expected: int) -> list[str]:
    if not text:
        return []
    start, end = text.find('['), text.rfind(']')
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return []
    out = ['' for _ in range(expected)]
    for row in data if isinstance(data, list) else []:
        try:
            idx = int(row.get('id'))
        except (TypeError, ValueError, AttributeError):
            continue
        if 1 <= idx <= expected:
            out[idx - 1] = str(row.get('translation') or '')
    return out


def _translate_arm(cues, hint, glossary_block, chat, batch=20):
    system = _build_system_prompt('ja')
    if hint:
        system += '\n\n' + hint
    out = []
    for start in range(0, len(cues), batch):
        chunk = cues[start:start + batch]
        targets = '\n'.join(f'{i + 1}. {c.get("text", "")}'
                            for i, c in enumerate(chunk))
        user = USER_TEMPLATE.format(context_before='（无）', targets=targets,
                                    glossary=glossary_block or '（无）')
        content, error = chat([
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ])
        if error:
            print(f'  [ab] batch failed: {error}', file=sys.stderr)
            out.extend([''] * len(chunk))
            continue
        parsed = _parse(content, len(chunk))
        if len(parsed) < len(chunk):
            parsed.extend([''] * (len(chunk) - len(parsed)))
        out.extend(parsed[:len(chunk)])
    return out


def _metrics(ja_cues, zh_texts) -> dict:
    flags = {'modern': 0, 'dialect': 0, 'anachronism': 0}
    for text in zh_texts:
        for hit in lint_text(text):
            flags[hit['category']] += 1
    zh_cues = [{'start': c.get('start', ''), 'end': c.get('end', ''),
                'text': t} for c, t in zip(ja_cues, zh_texts)]
    qa = qa_report(ja_cues, zh_cues)['summary']
    ratios = [len(t) / max(1, len(str(c.get('text') or '')))
              for t, c in zip(zh_texts, ja_cues) if t]
    names = {}
    for zh_name, forms in NAME_FORMS.items():
        expected = sum(1 for c in ja_cues
                       if any(f in str(c.get('text') or '') for f in forms))
        got = sum(t.count(zh_name) for t in zh_texts)
        names[zh_name] = {'expected': expected, 'got': got}
    return {
        'hits': flags,
        'hit_total': sum(flags.values()),
        'qa': {k: qa.get(k) for k in
               ('japanese_residual', 'echo', 'missing', 'max_cps', 'max_chars')},
        'mean_length_ratio': round(sum(ratios) / max(1, len(ratios)), 3),
        'empty': sum(1 for t in zh_texts if not t.strip()),
        'names': names,
        'register': _register_scores(zh_texts),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ja', required=True)
    parser.add_argument('--mappings', default='')
    parser.add_argument('--limit', type=int, default=120)
    parser.add_argument('--batch', type=int, default=20)
    parser.add_argument('--out-dir', default=os.path.join('temp', 'exp', 'style_ab'))
    parser.add_argument('--arms', default='A_none,B_keywords,C_prose,D_structured,E_negative')
    args = parser.parse_args(argv)

    from lib.llm import call_chat

    def chat(messages):
        text = call_chat(messages, temperature=0.1, max_tokens=4096)
        return text, ('' if text else 'empty response')

    cues = read_subtitles(args.ja, mark_garbled=False)[:args.limit]
    glossary_block = ''
    if args.mappings:
        with open(args.mappings, encoding='utf-8') as fh:
            gloss = json.load(fh)
        pairs = [(str(k), str(v)) for k, v in gloss.items()
                 if not str(k).startswith('_')]
        glossary_block = '\n'.join(f'{k} → {v}' for k, v in pairs[:30])

    os.makedirs(args.out_dir, exist_ok=True)
    report = {'episode_file': args.ja, 'cues': len(cues), 'arms': {}}
    for name in args.arms.split(','):
        hint = ARMS.get(name, '')
        print(f'[ab] arm {name}: {len(cues)} cues', file=sys.stderr)
        texts = _translate_arm(cues, hint, glossary_block, chat, batch=args.batch)
        with open(os.path.join(args.out_dir, f'{name}.json'), 'w',
                  encoding='utf-8', newline='\n') as fh:
            json.dump(texts, fh, ensure_ascii=False, indent=1)
        metrics = _metrics(cues, texts)
        metrics['hint'] = hint
        report['arms'][name] = metrics
        print(f'  hits={metrics["hit_total"]} {metrics["hits"]} '
              f'qa={metrics["qa"]} len={metrics["mean_length_ratio"]} '
              f'names={metrics["names"]}', file=sys.stderr)

    with open(os.path.join(args.out_dir, 'report.json'), 'w',
              encoding='utf-8', newline='\n') as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write('\n')
    print('[ab] wrote', os.path.join(args.out_dir, 'report.json'), file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
