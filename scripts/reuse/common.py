#!/usr/bin/env python3
"""参考字幕复用 —— 语料加载、折叠与相似度（align.py / apply.py 共用）。

场景：目标字幕是我们自己的机译，参考字幕是**同一部作品**的人工翻译。
两者之间若有一批 cue 说的是同一句话，就直接拿参考译文覆盖机译 ——
人的译文优于机译，专名也随之统一。典型触发是总集篇（recap）沿用原片素材。

相似度 = 折叠后的字符二元组 Dice：
  · 折叠 = 去标点空白（NORM）+ 把同一实体的不同写法归一（--variants）
  · 折叠后**完全相同**的两行 = 整句只差人名写法 —— 这正是要按参考定译改写的情形，
    不是噪声（所以 apply.py 的 R1/R3 长度判据对它让路）
"""

import glob
import json
import os
import re

import lib._path  # noqa: F401,E402
from lib.subtitle_io import iter_subtitle_files, read_subtitles  # noqa: E402
from lib.video_scan import episode_token  # noqa: E402

# ASS 覆盖块 {\...} 与 SRT 内联标签 <i>
TAGRE = re.compile(r'\{[^}]*\}|<[^>]*>')
BS = chr(92)
NL_RE = re.compile(re.escape(BS) + '[Nn]')       # \N / \n → 换行
# 折叠时只留拉丁字母数字 / 假名 / 汉字，其余（标点、空白、符号）全去
NORM = re.compile(r'[^0-9A-Za-z぀-ヿ一-鿿]+')

# 参考字幕里承载对白的 ASS 样式。OP/ED、屏字、职员表通常另起样式，
# 混进来会污染候选。SRT 无样式信息，一律收；--styles '' 关闭过滤。
DEFAULT_STYLES = 'Default,DefaultS'


def clean(text):
    """去 ASS/SRT 标签，把换行硬标记（\\N）换成真换行。"""
    s = TAGRE.sub('', text or '')
    s = NL_RE.sub('\n', s)
    return s.replace('\r', '').strip()


def norm_only(text):
    """去标点空白，但**不**做写法归一（apply.py 的 R2「只差标点」用）。"""
    return NORM.sub('', clean(text).replace('\n', ''))


def make_fold(groups=()):
    """groups（同一实体的各种写法）→ fold(text)。

    同一组的成员在折叠后等价（换成同一个占位符），于是「只差人名写法」的两行
    可以被识别成同一个句子。占位符取私用区单字符：**必须一个字符** ——
    多字符占位符会同时拉长折叠串并打乱二元组边界，相似度随之系统性偏低。
    """
    pats = [(re.compile('|'.join(re.escape(v) for v in sorted(grp, key=len, reverse=True))),
             chr(0xE000 + i))
            for i, grp in enumerate(groups) if grp]

    def fold(text):
        s = NORM.sub('', clean(text).replace('\n', ''))
        for pat, rep in pats:
            s = pat.sub(rep, s)
        return s

    return fold


def load_variants(path):
    """读 --variants JSON → (groups, never_swap)。

    {
      "groups":     {"女主": ["瓦尔古蕾", "瓦尔Q蕾", "小瓦尔"]},
      "never_swap": [["小瓦尔", "瓦尔古蕾"], ["小瓦尔", "瓦尔Q蕾"]]
    }

    groups 的成员在折叠时等价，并注入 align.py 的复核 prompt（「以下写法指同一个人」）；
    never_swap 是**互为误配**的两种写法（昵称 vs 形态名、简称 vs 全称），apply.py 的
    R5 见到就拒。
    """
    if not path:
        return [], []
    if not os.path.isfile(path):
        raise SystemExit(f'[reuse] --variants 文件不存在：{path}')
    with open(path, encoding='utf-8') as f:
        d = json.load(f)
    raw_groups = d.get('groups') or []
    groups = list(raw_groups.values()) if isinstance(raw_groups, dict) else list(raw_groups)
    pairs = [tuple(p) for p in (d.get('never_swap') or []) if len(p) == 2]
    return groups, pairs


def bg(s):
    """字符二元组集合（长度 1 时退化为单字符）。"""
    if not s:
        return set()
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}


def dice(A, B):
    if not A or not B:
        return 0.0
    return 2 * len(A & B) / (len(A) + len(B))


# ═══════════════════════════════════════════════════════════════
# 语料加载
# ═══════════════════════════════════════════════════════════════

def reference_files(ref_dir, pattern=None):
    """参考目录里的字幕文件（--reference-glob 给出时按它筛，否则收全部 .srt/.ass）。"""
    if not os.path.isdir(ref_dir):
        return []
    if pattern:
        return sorted(glob.glob(os.path.join(ref_dir, pattern)))
    return sorted(p for _, p in iter_subtitle_files(ref_dir))


def load_reference(ref_dir, pattern=None, styles=DEFAULT_STYLES, fold=None):
    """读参考字幕 → cue 列表，每条带 ep token / start_s / text / bg / gi。

    参考字幕可能覆盖整部作品（目标只是其中一集或一个特别篇），ep 只用于报告与
    「续行必须同集」的判断。
    """
    fold = fold or make_fold()
    keep = {s.strip() for s in (styles or '').split(',') if s.strip()}
    files = reference_files(ref_dir, pattern)
    if not files:
        raise SystemExit(f'[reuse] 参考目录里没有字幕文件：{ref_dir}'
                         + (f'（--reference-glob {pattern}）' if pattern else ''))
    cues = []
    for path in files:
        ep = episode_token(path)
        for c in read_subtitles(path, mark_garbled=False):
            style = (c.get('_ass_dialogue') or {}).get('style')
            if keep and style is not None and style not in keep:
                continue
            t = clean(c['text'])
            if t:
                f = fold(t)
                cues.append({'ep': ep, 'start': c['start'], 'start_s': c['start_s'],
                             'text': t, 'f': f, 'bg': bg(f)})
    if not cues:
        raise SystemExit(
            f'[reuse] 参考字幕解析出 0 条 cue。检查 --styles：参考字幕若用别的样式名'
            f'（如「正文」），默认的 {DEFAULT_STYLES} 会把对白全部滤掉（--styles \'\' 关闭过滤）')
    for gi, c in enumerate(cues):
        c['gi'] = gi
    return cues


def resolve_tag_file(directory, tag):
    """在目录里按集号 token 找字幕文件（同名优先，再退回 episode_token 比对）。"""
    if not os.path.isdir(directory):
        return None
    files = [p for _, p in iter_subtitle_files(directory)]
    want = tag.upper()
    for p in files:
        if os.path.splitext(os.path.basename(p))[0].upper() == want:
            return p
    for p in files:
        if episode_token(p).upper() == want:
            return p
    return None


def load_target(source_dir, target_dir, tag, fold=None):
    """读目标（源语言转录 + 我们的译文）→ cue 列表。两条必须逐条对齐。"""
    fold = fold or make_fold()
    src_path = resolve_tag_file(source_dir, tag)
    tgt_path = resolve_tag_file(target_dir, tag)
    if not src_path or not tgt_path:
        raise SystemExit(f'[reuse] 找不到 {tag}：源 {src_path or source_dir} / '
                         f'译文 {tgt_path or target_dir}')
    src = read_subtitles(src_path, mark_garbled=False)
    tgt = read_subtitles(tgt_path, mark_garbled=False)
    if len(src) != len(tgt):
        raise SystemExit(f'[reuse] {tag} 源 {len(src)} 条 ≠ 译文 {len(tgt)} 条，'
                         f'无法逐条对齐（翻译步骤可能漏译/多译）')
    out = []
    for a, b in zip(src, tgt):
        f = fold(b['text'])
        out.append({'start': a['start'], 'src': a['text'].strip(),
                    'zh': b['text'].strip(), 'f': f, 'bg': bg(f)})
    return out


def group_hint(groups):
    """variants 的 groups → 复核 prompt 里的「同一人」提示串。"""
    if not groups:
        return ''
    return '（' + '；'.join(' / '.join(g) + ' 指同一个人' for g in groups if g) + '）'
