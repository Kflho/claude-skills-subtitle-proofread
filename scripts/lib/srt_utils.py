#!/usr/bin/env python3
"""SRT 字幕处理共享工具函数。

提供与 ass_utils.py 相同接口的 SRT 解析、时间码转换、标签处理等功能。
使现有检测脚本无需修改即可处理 .srt 文件。
"""

import os
import re
from typing import Optional


# ── 时间码处理 ──────────────────────────────────────────────

def time_to_ms(t: str) -> int:
    """将 SRT 时间码 (HH:MM:SS,mmm) 转为毫秒。

    Args:
        t: SRT 时间码字符串，如 "00:01:23,456"

    Returns:
        毫秒数

    Examples:
        >>> time_to_ms("00:01:23,456")
        83456
    """
    t = t.strip()
    # SRT 使用逗号分隔毫秒
    if ',' in t:
        time_part, ms_part = t.split(',')
    else:
        # 兼容 ASS 格式 (点号)
        time_part, ms_part = t.split('.')
    h, m, s = map(int, time_part.split(':'))
    ms = int(ms_part.ljust(3, '0')[:3])
    return ((h * 60 + m) * 60 + s) * 1000 + ms


def ms_to_time(ms: int) -> str:
    """将毫秒转为 SRT 时间码格式。

    Args:
        ms: 毫秒数

    Returns:
        SRT 时间码字符串 "HH:MM:SS,mmm"
    """
    h = ms // 3600000
    ms %= 3600000
    m = ms // 60000
    ms %= 60000
    s = ms // 1000
    milli = ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"


# ── SRT 标签处理 ─────────────────────────────────────────────

# SRT 中的 HTML 标签: <i>, <b>, <u>, <font color="...">, </i>, </b>, 等
SRT_TAG_RE = re.compile(r'<[^>]*>')

def strip_srt_tags(text: str) -> str:
    """移除 SRT HTML 标签。

    Args:
        text: 含 HTML 标签的文本

    Returns:
        去除标签后的纯文本
    """
    return SRT_TAG_RE.sub('', text)


# ── SRT 字幕块解析 ──────────────────────────────────────────

# SRT 索引行: 纯数字
SRT_INDEX_RE = re.compile(r'^\d{1,6}\s*$')

# SRT 时间码行: "00:00:00,000 --> 00:00:00,000"
SRT_TIMECODE_RE = re.compile(
    r'(\d{1,2}:\d{2}:\d{2}[,.]\d{2,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{2,3})'
)


def parse_srt_cue(lines: list[str], idx: int) -> tuple[Optional[dict], int]:
    """从行列表的 idx 位置解析一个 SRT 字幕块。

    一个 SRT 块的结构：
        索引号 (纯数字)
        时间码行 (start --> end)
        文本 (一行或多行)
        空行 (分隔符)

    Args:
        lines: SRT 文件行列表（含换行符）
        idx: 开始解析的位置

    Returns:
        (cue_dict, next_idx)，如果解析失败返回 (None, idx)
        cue_dict 字段与 ASS parse_dialogue 兼容:
            format, layer, start, end, style, name,
            margin_l, margin_r, margin_v, effect, text, _raw_parts
    """
    if idx >= len(lines):
        return None, idx

    # 跳过空行，找到索引号
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    if idx >= len(lines):
        return None, idx

    # 检查是否是索引号行
    index_line = lines[idx].strip()
    if not SRT_INDEX_RE.match(index_line):
        return None, idx

    cue_index = int(index_line)
    idx += 1

    # 时间码行
    if idx >= len(lines):
        return None, idx
    tc_match = SRT_TIMECODE_RE.match(lines[idx].strip())
    if not tc_match:
        return None, idx

    start_time = tc_match.group(1).replace(',', '.')  # 统一为 . 便于兼容 ASS
    end_time = tc_match.group(2).replace(',', '.')
    idx += 1

    # 文本行（可能多行）
    text_lines = []
    while idx < len(lines) and lines[idx].strip():
        text_lines.append(lines[idx].strip())
        idx += 1

    text = '\n'.join(text_lines) if text_lines else ''

    # 跳过尾随空行
    while idx < len(lines) and not lines[idx].strip():
        idx += 1

    # 构建与 ASS parse_dialogue 兼容的字典
    # ASS format: Dialogue: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
    # split(',', 9) → parts[0]="Dialogue: 0", parts[1]=Start, ..., parts[9]=Text
    cue_dict = {
        'format': 'Dialogue: 0',
        'layer': '0',
        'start': start_time,
        'end': end_time,
        'style': 'Default',
        'name': '',
        'margin_l': '0',
        'margin_r': '0',
        'margin_v': '0',
        'effect': '',
        'text': text,
        '_raw_parts': [
            'Dialogue: 0',
            start_time,
            end_time,
            'Default',
            '',
            '0',
            '0',
            '0',
            '',
            text,
        ],
        '_srt_index': cue_index,
        '_srt_text_lines': text_lines,
        '_format': 'srt',
    }

    return cue_dict, idx


def build_srt_cue(d: dict) -> str:
    """从 parse_srt_cue 返回的字典重建 SRT 字幕块。

    Args:
        d: parse_srt_cue 返回的字典

    Returns:
        完整的 SRT 字幕块字符串（含索引、时间码、文本）
    """
    index = d.get('_srt_index', 0)
    start = d['start'].replace('.', ',')  # SRT 使用逗号
    end = d['end'].replace('.', ',')
    text = d['text']
    return f"{index}\n{start} --> {end}\n{text}\n"


def build_srt_cue_lines(d: dict) -> list[str]:
    """从 parse_srt_cue 返回的字典重建 SRT 字幕块行列表。

    Returns:
        每行以 \n 结尾的列表（如 ['1\n', '00:00:00,000 --> 00:00:01,000\n', 'text\n', '\n']）
    """
    index = d.get('_srt_index', 0)
    start = d['start'].replace('.', ',')
    end = d['end'].replace('.', ',')
    text = d['text']
    lines_out = [
        f"{index}\n",
        f"{start} --> {end}\n",
    ]
    # 文本可能包含多行
    for text_line in text.split('\n'):
        lines_out.append(f"{text_line}\n")
    lines_out.append('\n')
    return lines_out


# ── 文件 I/O ──────────────────────────────────────────────────

# Encoding detection chain for subtitle files.
# ASS/SRT files may be saved in non-UTF-8 encodings (CP1251 for Cyrillic,
# Shift-JIS for Japanese, GBK for Chinese).  Try common encodings in order;
# the first that decodes without error wins.
_ENCODING_CHAIN = ['utf-8-sig', 'utf-8', 'cp1251', 'koi8-r', 'shift-jis', 'gbk']


def _detect_encoding(raw_bytes: bytes) -> str:
    """Return the first encoding in the chain that decodes without error."""
    for enc in _ENCODING_CHAIN:
        try:
            raw_bytes.decode(enc)
            return enc
        except (UnicodeDecodeError, UnicodeError):
            continue
    return 'utf-8'  # fallback


def read_srt_file(path: str) -> list[str]:
    """读取 SRT/ASS 文件，返回行列表（含换行符）。自动检测编码。"""
    with open(path, 'rb') as f:
        raw = f.read()
    encoding = _detect_encoding(raw)
    return raw.decode(encoding).splitlines(True)


def write_srt_file(path: str, lines: list[str]):
    """写入 SRT 文件。"""
    with open(path, 'w', encoding='utf-8') as f:
        f.writelines(lines)


# ── 文件迭代 ──────────────────────────────────────────────────

def iter_srt_files(target_dir: str):
    """遍历目录中的 .srt 文件（按文件名排序）。

    Yields:
        (filename, full_path) 元组
    """
    for fname in sorted(os.listdir(target_dir)):
        if fname.lower().endswith('.srt'):
            yield fname, os.path.join(target_dir, fname)


# ── 对话行迭代 ───────────────────────────────────────────────

def iter_dialogue_lines(lines: list[str], styles: Optional[set] = None):
    """遍历 SRT 文件行列表中的字幕条目。

    Args:
        lines: SRT 文件行列表
        styles: 忽略（SRT 无样式概念，保留参数以兼容 ASS API）

    Yields:
        (line_index, parsed_dict) 元组
        line_index 是字幕块第一行（索引行）的行号 (0-based)
    """
    idx = 0
    while idx < len(lines):
        start_idx = idx
        cue, idx = parse_srt_cue(lines, idx)
        if cue is None:
            idx += 1
            continue
        yield start_idx, cue


# ── 名称收集工具 ─────────────────────────────────────────────

def collect_names(target_dir: str) -> set[str]:
    """SRT 没有 Name 字段，始终返回空集合。"""
    return set()


def collect_texts(target_dir: str, styles: Optional[set] = None) -> list[str]:
    """收集全部 SRT 文件中的可见文本（已 strip 标签）。

    Args:
        target_dir: 目标目录
        styles: 忽略（SRT 无样式概念）

    Returns:
        可见文本列表
    """
    texts = []
    for fname, fpath in iter_srt_files(target_dir):
        lines = read_srt_file(fpath)
        for _, d in iter_dialogue_lines(lines):
            visible = strip_srt_tags(d['text'])
            if visible.strip():
                texts.append(visible)
    return texts


# ── 中文检测 ──────────────────────────────────────────────────

CJK_RE = re.compile(r'[一-鿿]')

def contains_cjk(text: str) -> bool:
    """检测文本是否含 CJK 字符（中日韩统一表意文字）。"""
    return bool(CJK_RE.search(text))
