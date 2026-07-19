#!/usr/bin/env python3
"""ASS 字幕处理共享工具函数。

所有脚本共用的 ASS 解析、时间码转换、标签处理等基础功能。
"""

import os
import re
from typing import Optional


# ── 时间码处理 ──────────────────────────────────────────────

def time_to_ms(t: str) -> int:
    """将 ASS 时间码 (H:MM:SS.cc) 转为毫秒。

    Args:
        t: ASS 时间码字符串，如 "0:01:23.45"

    Returns:
        毫秒数

    Examples:
        >>> time_to_ms("0:01:23.45")
        83450
    """
    parts = t.split(':')
    h, m = int(parts[0]), int(parts[1])
    s_parts = parts[2].split('.')
    s = int(s_parts[0])
    cs = int(s_parts[1].ljust(2, '0')[:2])
    return ((h * 60 + m) * 60 + s) * 1000 + cs * 10


def ms_to_time(ms: int) -> str:
    """将毫秒转为 ASS 时间码格式。

    Args:
        ms: 毫秒数

    Returns:
        ASS 时间码字符串 "H:MM:SS.cc"
    """
    h = ms // 3600000
    ms %= 3600000
    m = ms // 60000
    ms %= 60000
    s = ms // 1000
    cs = (ms % 1000) // 10
    return f"{h}:{m:01d}:{s:02d}.{cs:02d}"


# ── ASS 标签处理 ─────────────────────────────────────────────

ASS_TAG_RE = re.compile(r'\{[^}]*\}')

def strip_ass_tags(text: str) -> str:
    """移除 ASS 内联标签 {...}。

    Args:
        text: 含 ASS 标签的文本

    Returns:
        去除标签后的纯文本
    """
    return ASS_TAG_RE.sub('', text)


# ── Dialogue 行解析 ──────────────────────────────────────────

def parse_dialogue(line: str) -> Optional[dict]:
    """解析 ASS Dialogue 行。

    ASS 格式: Dialogue: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
    split(',', 9) 后 parts[0] 包含 "Dialogue: {Layer}"。

    Args:
        line: ASS 文件中的一行

    Returns:
        包含各字段的字典，非 Dialogue 行返回 None
    """
    if not line.startswith('Dialogue:'):
        return None
    parts = line.strip().split(',', 9)
    if len(parts) < 10:
        return None
    # parts[0] = "Dialogue: 0" (Layer 嵌入在 format 前缀中)
    # parts[1] = Start, parts[2] = End, parts[3] = Style, parts[4] = Name
    # parts[5] = MarginL, parts[6] = MarginR, parts[7] = MarginV
    # parts[8] = Effect, parts[9] = Text
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
        '_raw_parts': parts,  # 保留原始 split 结果，用于重建行
    }


def build_dialogue_line(d: dict) -> str:
    """从 parse_dialogue 返回的字典重建 Dialogue 行字符串。

    Args:
        d: parse_dialogue 返回的字典

    Returns:
        完整的 Dialogue 行字符串（不含换行符）
    """
    parts = d.get('_raw_parts', [
        d['format'], d['layer'], d['start'], d['end'],
        d['style'], d['name'], d['margin_l'], d['margin_r'],
        d['margin_v'], d['text']
    ])
    parts[9] = d['text']  # 确保 text 是最新的
    return ','.join(parts)


# ── 文件 I/O ──────────────────────────────────────────────────

def read_ass_file(path: str) -> list[str]:
    """读取 ASS 文件，返回行列表（含换行符）。"""
    with open(path, 'r', encoding='utf-8') as f:
        return f.readlines()


def write_ass_file(path: str, lines: list[str]):
    """写入 ASS 文件。"""
    with open(path, 'w', encoding='utf-8') as f:
        f.writelines(lines)


# ── 文件迭代 ──────────────────────────────────────────────────

def iter_ass_files(target_dir: str):
    """遍历目录中的 .ass 文件（按文件名排序）。

    Yields:
        (filename, full_path) 元组
    """
    for fname in sorted(os.listdir(target_dir)):
        if fname.lower().endswith('.ass'):
            yield fname, os.path.join(target_dir, fname)


# ── 对话行迭代 ───────────────────────────────────────────────

def iter_dialogue_lines(lines: list[str], styles: Optional[set] = None):
    """遍历 ASS 文件行列表中的 Dialogue 行。

    Args:
        lines: ASS 文件行列表
        styles: 限定处理的样式名集合，None 表示不限定

    Yields:
        (line_index, parsed_dict) 元组
    """
    for i, line in enumerate(lines):
        d = parse_dialogue(line)
        if d is None:
            continue
        if styles is not None and d['style'] not in styles:
            continue
        yield i, d


# ── 名称收集工具 ─────────────────────────────────────────────

def collect_names(target_dir: str) -> set[str]:
    """扫描全部 ASS 文件中出现的 Name 字段值。

    Args:
        target_dir: 目标目录

    Returns:
        Name 字段值集合
    """
    names = set()
    for fname, fpath in iter_ass_files(target_dir):
        lines = read_ass_file(fpath)
        for _, d in iter_dialogue_lines(lines):
            if d['name']:
                names.add(d['name'])
    return names


def collect_texts(target_dir: str, styles: Optional[set] = None) -> list[str]:
    """收集全部 ASS 文件中的可见文本（已 strip 标签）。

    Args:
        target_dir: 目标目录
        styles: 限定样式集合

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


# ── 中文检测 ──────────────────────────────────────────────────

CJK_RE = re.compile(r'[一-鿿]')

def contains_cjk(text: str) -> bool:
    """检测文本是否含 CJK 字符（中日韩统一表意文字）。"""
    return bool(CJK_RE.search(text))
