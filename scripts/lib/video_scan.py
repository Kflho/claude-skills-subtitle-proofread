#!/usr/bin/env python3
"""视频文件枚举与集号识别 —— 摆脱对 EP### 硬编码的依赖。

背景：whisper_batch_transcribe.py 原先用 `for ep_num in range(1, 194)` 合成 'EP064'
再交给 find_video() 匹配。这对 SP / OVA / 特别篇等非 EP### 命名完全失效——
例如 `[YYDM-11FANS]...[SP01]...[A3D3AE54].mp4` 既不带 EP 前缀，又含大量干扰数字
（960x720 / 10bit / A3D3AE54）。

本模块改为「直接枚举视频文件 → 从文件名推导集号 token」，任何命名都能跑，
且天然支持混合命名（同一目录里既有 EP### 又有 SP##）。
"""

import os
import re

VIDEO_EXTS = ('.mkv', '.mp4', '.avi', '.mov', '.ts', '.m2ts', '.wmv', '.flv')

# 显式前缀：EP064 / SP01 / OVA2。左边界不要字母，避免 "UFO" 里的 "FO"、
# "DVDRIP" 里的片段被误取。
_PREFIX_RE = re.compile(r'(?<![A-Za-z])(EP|SP|OVA|OAD)[\s._-]*(\d{1,3})(?!\d)', re.I)

# 裸集号：左右都必须是非字母数字。这样 [960x720] / 10bit / 2xAudio / A3D3AE54
# 里的数字全部落选，只剩 `- 064 -` 这种真集号。
_BARE_RE = re.compile(r'(?<![A-Za-z0-9])(\d{1,3})(?![A-Za-z0-9])')

_TOKEN_RE = re.compile(r'^(?:(EP|SP|OVA|OAD))?[\s._-]*(\d{1,3})$', re.I)

# EP 用 3 位（对齐既有 EP001-EP194），其他类型用 2 位（SP01/OVA02）
_PAD = {'EP': 3}


def _mk_token(kind, num):
    """('EP', 64) → 'EP064'；('SP', 1) → 'SP01'。"""
    kind = (kind or 'EP').upper()
    return f'{kind}{num:0{_PAD.get(kind, 2)}d}'


def _natural_key(name):
    """自然序：'ep2' < 'ep10'。"""
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r'(\d+)', name)]


def episode_token(path):
    """从视频文件名推导集号 token。

    '...- 064 ...mkv'                          → 'EP064'
    '...[SP01][DVDRIP][960x720]...[A3D3AE54]'  → 'SP01'
    '...[OVA02]...'                            → 'OVA02'
    认不出来 → 文件名 stem（保证仍可跑，不丢文件）
    """
    stem = os.path.splitext(os.path.basename(path))[0]
    m = _PREFIX_RE.search(stem)
    if m:
        return _mk_token(m.group(1), int(m.group(2)))
    nums = _BARE_RE.findall(stem)
    if nums:
        return _mk_token('EP', int(nums[-1]))
    return stem


def list_video_files(dirs):
    """按自然序枚举目录下的视频文件。dirs 可为单个路径或可迭代。

    跨目录去重（同一文件被多个候选目录覆盖时只留一份）。
    """
    if isinstance(dirs, str):
        dirs = [dirs]
    seen, out = set(), []
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for fname in os.listdir(d):
            if not fname.lower().endswith(VIDEO_EXTS):
                continue
            full = os.path.join(d, fname)
            real = os.path.realpath(full)
            if real in seen:
                continue
            seen.add(real)
            out.append(full)
    out.sort(key=lambda p: _natural_key(os.path.basename(p)))
    return out


def parse_episode_spec(spec):
    """解析集号选择串 → token 列表。

    支持：'EP001-EP010' / 'EP001,EP005' / '1-10' / '1,5' / 'SP01' / 'SP01-SP02'
    未写类型前缀时按 EP 处理（兼容旧 --episodes 1-10 语义）；
    区间内只要一端带类型，整段沿用该类型。
    """
    if not spec:
        return []
    out = []
    for part in str(spec).split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part.lstrip('-'):
            a, _, b = part.partition('-')
            ma, mb = _TOKEN_RE.match(a.strip()), _TOKEN_RE.match(b.strip())
            if not (ma and mb):
                continue
            kind = ma.group(1) or mb.group(1)
            lo, hi = int(ma.group(2)), int(mb.group(2))
            if lo > hi:
                lo, hi = hi, lo
            out.extend(_mk_token(kind, n) for n in range(lo, hi + 1))
        else:
            m = _TOKEN_RE.match(part)
            if m:
                out.append(_mk_token(m.group(1), int(m.group(2))))
    # 去重保序
    seen, uniq = set(), []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def select_videos(dirs, episodes=None, start_from=None, limit=0):
    """枚举视频 → (token, path) 列表，按 episodes / start_from / limit 裁剪。

    顺序即自然序；start_from 以「列表内定位」实现，避免 EP 与 SP 混排时
    字符串比较给出意外结果。
    """
    entries = [(episode_token(p), p) for p in list_video_files(dirs)]

    if episodes:
        want = set(parse_episode_spec(episodes))
        if want:
            entries = [e for e in entries if e[0] in want]

    if start_from:
        st = parse_episode_spec(str(start_from))
        if st:
            tgt = st[0]
            for i, (tok, _) in enumerate(entries):
                if tok == tgt:
                    entries = entries[i:]
                    break

    if limit and limit > 0:
        entries = entries[:limit]

    return entries
