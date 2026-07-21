#!/usr/bin/env python3
"""字幕处理共享工具函数 — 支持 ASS 和 SRT 格式。

所有脚本共用的字幕解析、时间码转换、标签处理等基础功能。
自动根据文件扩展名检测格式并委托给对应实现。

内部委托：
  - ASS 文件 → 本模块内置的 ASS 解析
  - SRT 文件 → srt_utils.py
"""

import os
import re
from typing import Optional

# 尝试导入 srt_utils（同级目录）
try:
    from lib import srt_utils
except ImportError:
    import lib.srt_utils as srt_utils

CJK_RE = srt_utils.CJK_RE  # single source of truth


# ── 格式检测辅助 ──────────────────────────────────────────────

def _is_srt_path(path: str) -> bool:
    """判断路径是否为 SRT 文件。"""
    return path.lower().endswith('.srt')


def _is_ass_path(path: str) -> bool:
    """判断路径是否为 ASS 文件。"""
    return path.lower().endswith('.ass')


# ═══════════════════════════════════════════════════════════════
# ASS 时间码处理
# ═══════════════════════════════════════════════════════════════

def time_to_ms(t: str) -> int:
    """将时间码转为毫秒。自动识别 ASS (H:MM:SS.cc) 和 SRT (HH:MM:SS,mmm) 格式。

    Args:
        t: 时间码字符串

    Returns:
        毫秒数

    Examples:
        >>> time_to_ms("0:01:23.45")    # ASS: 83450
        >>> time_to_ms("00:01:23,456")  # SRT: 83456
    """
    t = t.strip()
    parts = t.split(':')
    h, m = int(parts[0]), int(parts[1])
    s_part = parts[2]
    # 检测分隔符：逗号 → SRT，点号 → ASS
    if ',' in s_part:
        s, ms = s_part.split(',')
        s = int(s)
        ms = int(ms.ljust(3, '0')[:3])
        return ((h * 60 + m) * 60 + s) * 1000 + ms
    else:
        s, cs = s_part.split('.')
        s = int(s)
        cs = int(cs.ljust(2, '0')[:2])
        return ((h * 60 + m) * 60 + s) * 1000 + cs * 10


def ms_to_time(ms: int, format: str = 'ass') -> str:
    """将毫秒转为时间码格式。

    Args:
        ms: 毫秒数
        format: 'ass' → "H:MM:SS.cc", 'srt' → "HH:MM:SS,mmm"

    Returns:
        时间码字符串
    """
    h = ms // 3600000
    ms %= 3600000
    m = ms // 60000
    ms %= 60000
    s = ms // 1000
    if format == 'srt':
        milli = ms % 1000
        return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"
    else:
        cs = (ms % 1000) // 10
        return f"{h}:{m:01d}:{s:02d}.{cs:02d}"


# ═══════════════════════════════════════════════════════════════
# 标签处理
# ═══════════════════════════════════════════════════════════════

ASS_TAG_RE = re.compile(r'\{[^}]*\}')
SRT_TAG_RE = re.compile(r'<[^>]*>')

def strip_ass_tags(text: str) -> str:
    """移除字幕标签。支持 ASS {...} 和 SRT HTML <...> 标签。

    先移除 ASS 标签，再移除 HTML 标签。

    Args:
        text: 含标签的文本

    Returns:
        去除标签后的纯文本
    """
    text = ASS_TAG_RE.sub('', text)
    text = SRT_TAG_RE.sub('', text)
    return text


# ═══════════════════════════════════════════════════════════════
# ASS Dialogue 行解析
# ═══════════════════════════════════════════════════════════════

def parse_dialogue(line: str) -> Optional[dict]:
    """解析 ASS Dialogue 行。

    ASS 格式: Dialogue: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
    split(',', 9) 后 parts[0] 包含 "Dialogue: {Layer}"。

    Args:
        line: ASS 文件中的一行

    Returns:
        包含各字段的字典，非 Dialogue 行返回 None

    Note:
        此函数仅处理 ASS 行。SRT 解析使用 srt_utils.parse_srt_cue()。
    """
    if not line.startswith('Dialogue:'):
        return None
    parts = line.strip().split(',', 9)
    if len(parts) < 10:
        return None
    return {
        'format': parts[0],
        'layer': parts[0].split(': ', 1)[1] if ': ' in parts[0] else '',
        'start': parts[1],
        'end': parts[2],
        'style': parts[3],
        'name': parts[4],
        'margin_l': parts[5],
        'margin_r': parts[6],
        'margin_v': parts[7],
        'effect': parts[8],
        'text': parts[9],
        '_raw_parts': parts,
        '_format': 'ass',
    }


def build_dialogue_line(d: dict) -> str:
    """从解析后的字典重建字幕行字符串。自动检测格式（ASS/SRT）。

    Args:
        d: parse_dialogue 或 parse_srt_cue 返回的字典

    Returns:
        完整的行字符串或 SRT 块字符串（不含尾随换行符）
    """
    if d.get('_format') == 'srt':
        return srt_utils.build_srt_cue(d)

    # ASS 格式
    parts = d.get('_raw_parts', [
        d.get('format', 'Dialogue: 0'),
        d.get('layer', '0'),
        d['start'], d['end'],
        d.get('style', 'Default'),
        d.get('name', ''),
        d.get('margin_l', '0'),
        d.get('margin_r', '0'),
        d.get('margin_v', '0'),
        d['text'],
    ])
    # 确保 parts 有正确的元素
    if len(parts) < 10:
        parts = [
            d.get('format', 'Dialogue: 0'),
            d.get('layer', '0'),
            d['start'], d['end'],
            d.get('style', 'Default'),
            d.get('name', ''),
            d.get('margin_l', '0'),
            d.get('margin_r', '0'),
            d.get('margin_v', '0'),
            d.get('effect', ''),
            d['text'],
        ]
    parts[9] = d['text']  # 确保 text 是最新的
    return ','.join(str(p) for p in parts)


# ═══════════════════════════════════════════════════════════════
# 文件 I/O
# ═══════════════════════════════════════════════════════════════

def read_ass_file(path: str) -> list[str]:
    """读取字幕文件，返回行列表（含换行符）。自动处理 ASS 和 SRT。

    Args:
        path: 文件路径

    Returns:
        包含换行符的行列表
    """
    if _is_srt_path(path):
        return srt_utils.read_srt_file(path)
    with open(path, 'r', encoding='utf-8') as f:
        return f.readlines()


def write_ass_file(path: str, lines: list[str]):
    """写入字幕文件。自动处理 ASS 和 SRT。

    Args:
        path: 文件路径
        lines: 要写入的行列表
    """
    if _is_srt_path(path):
        srt_utils.write_srt_file(path, lines)
    else:
        with open(path, 'w', encoding='utf-8') as f:
            f.writelines(lines)


# ═══════════════════════════════════════════════════════════════
# 文件迭代
# ═══════════════════════════════════════════════════════════════

def iter_ass_files(target_dir: str):
    """遍历目录中的字幕文件（.ass 和 .srt），按文件名排序。

    Yields:
        (filename, full_path) 元组
    """
    for fname in sorted(os.listdir(target_dir)):
        lower = fname.lower()
        if lower.endswith('.ass') or lower.endswith('.srt'):
            yield fname, os.path.join(target_dir, fname)


def iter_ass_only(target_dir: str):
    """遍历目录中仅 .ass 文件（向后兼容）。

    Yields:
        (filename, full_path) 元组
    """
    for fname in sorted(os.listdir(target_dir)):
        if fname.lower().endswith('.ass'):
            yield fname, os.path.join(target_dir, fname)


# ═══════════════════════════════════════════════════════════════
# 对话行迭代（格式感知）
# ═══════════════════════════════════════════════════════════════

def iter_dialogue_lines(lines: list[str], styles: Optional[set] = None):
    """遍历字幕文件行列表中的对话条目。自动检测 ASS/SRT 格式。

    ASS:
        逐行扫描以 "Dialogue:" 开头的行。
    SRT:
        调用 srt_utils.parse_srt_cue 解析整个块。

    Args:
        lines: 字幕文件行列表（含换行符）
        styles: 限定处理的样式名集合，None 表示不限定（SRT 忽略此参数）

    Yields:
        (line_index, parsed_dict) 元组
        line_index 是条目的第一行（ASS: Dialogue 行，SRT: 索引号行）
    """
    # 检测格式
    is_srt = _detect_srt_format(lines)

    if is_srt:
        idx = 0
        while idx < len(lines):
            start_idx = idx
            cue, idx = srt_utils.parse_srt_cue(lines, idx)
            if cue is None:
                idx += 1
                continue
            yield start_idx, cue
    else:
        # ASS 格式
        for i, line in enumerate(lines):
            d = parse_dialogue(line)
            if d is None:
                continue
            if styles is not None and d['style'] not in styles:
                continue
            yield i, d


def parse_comment_line(line: str) -> Optional[dict]:
    """解析 ASS Comment 行。

    Comment 行的格式与 Dialogue 相同，只是前缀为 "Comment:"。
    此函数专门处理 Comment 行（parse_dialogue 只处理 Dialogue 行）。

    Args:
        line: ASS 文件中的一行

    Returns:
        包含各字段的字典，非 Comment 行返回 None
    """
    if not line.startswith('Comment:'):
        return None
    parts = line.strip().split(',', 9)
    if len(parts) < 10:
        return {
            'layer': '0', 'start': '', 'end': '', 'style': '',
            'name': '', 'text': '', 'is_empty': True,
            '_format': 'ass',
        }
    return {
        'format': parts[0],
        'layer': parts[0].split(': ', 1)[1] if ': ' in parts[0] else '0',
        'start': parts[1], 'end': parts[2],
        'style': parts[3], 'name': parts[4],
        'margin_l': parts[5], 'margin_r': parts[6], 'margin_v': parts[7],
        'effect': parts[8], 'text': parts[9],
        'is_empty': False,
        '_raw_parts': parts,
        '_format': 'ass',
    }


def iter_comment_lines(lines: list[str]):
    """遍历 ASS 文件的 Comment 行。

    注意：对话检测脚本（iter_dialogue_lines）只扫描 Dialogue: 行，
    此函数专门用于扫描 Comment: 行中的外语残留。

    Args:
        lines: ASS 文件行列表（含换行符）

    Yields:
        (line_index, parsed_dict) 元组
    """
    for i, line in enumerate(lines):
        c = parse_comment_line(line)
        if c is not None:
            yield i, c


def _detect_srt_format(lines: list[str]) -> bool:
    """检测行列表是 SRT 还是 ASS 格式。

    启发式方法：
    1. 如果前几行有 "Dialogue:" → ASS
    2. 如果前几行有 SRT 时间码模式 "HH:MM:SS,mmm --> HH:MM:SS,mmm" → SRT
    3. 如果文件扩展名已知 → 由调用方指定
    """
    # 检查前 20 行
    for line in lines[:20]:
        if line.startswith('Dialogue:') or line.startswith('[Script Info]') or line.startswith('[V4'):
            return False  # ASS
        if '-->' in line and (',' in line or '.' in line):
            return True   # SRT
    return False  # 默认按 ASS 处理


# ═══════════════════════════════════════════════════════════════
# 名称收集工具
# ═══════════════════════════════════════════════════════════════

def collect_names(target_dir: str) -> set[str]:
    """扫描全部字幕文件中出现的 Name 字段值。

    SRT 文件没有 Name 字段，始终返回空集合。

    Args:
        target_dir: 目标目录

    Returns:
        Name 字段值集合
    """
    names = set()
    for fname, fpath in iter_ass_files(target_dir):
        if _is_srt_path(fpath):
            continue  # SRT 没有 Name 字段
        lines = read_ass_file(fpath)
        for _, d in iter_dialogue_lines(lines):
            if d.get('name'):
                names.add(d['name'])
    return names


def collect_texts(target_dir: str, styles: Optional[set] = None) -> list[str]:
    """收集全部字幕文件中的可见文本（已 strip 标签）。

    Args:
        target_dir: 目标目录
        styles: 限定样式集合（SRT 忽略此参数）

    Returns:
        可见文本列表
    """
    texts = []
    for fname, fpath in iter_ass_files(target_dir):
        lines = read_ass_file(fpath)
        for _, d in iter_dialogue_lines(lines, styles):
            visible = strip_ass_tags(d['text'])
            if visible.strip():
                texts.append(visible)
    return texts


# ═══════════════════════════════════════════════════════════════
# 中文检测
# ═══════════════════════════════════════════════════════════════

def contains_cjk(text: str) -> bool:
    """检测文本是否含 CJK 字符（中日韩统一表意文字）。"""
    return bool(CJK_RE.search(text))

