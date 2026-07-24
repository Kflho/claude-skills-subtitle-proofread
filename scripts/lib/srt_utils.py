#!/usr/bin/env python3
"""SRT 字幕处理共享工具函数。（向后兼容 — 委托到 subtitle_io）

提供与 ass_utils.py 相同接口的 SRT 解析、时间码转换、标签处理等功能。
新代码应直接使用 lib.subtitle_io。
"""

import os
import re
from typing import Optional

# ── Re-export from subtitle_io for backward compat ──
from lib.subtitle_io import (
    time_to_ms, ms_to_time,
    strip_srt_tags, SRT_TAG_RE,
    SRT_INDEX_RE, SRT_TIMECODE_RE,
    _parse_srt_cue as parse_srt_cue,
    _format_srt_cue_lines as build_srt_cue_lines,
    _read_raw_lines as read_srt_file,
    _write_raw_lines as write_srt_file,
    iter_subtitle_files as iter_srt_files,
    contains_cjk, CJK_RE,
)


# ── Legacy helpers (not in subtitle_io — thin, kept for compat) ──

def build_srt_cue(d: dict) -> str:
    """从 parse_srt_cue 返回的字典重建 SRT 字幕块字符串。"""
    index = d.get('_srt_index', 0)
    start = d['start'].replace('.', ',')
    end = d['end'].replace('.', ',')
    text = d['text']
    return f"{index}\n{start} --> {end}\n{text}\n"


def iter_dialogue_lines(lines: list[str], styles: Optional[set] = None):
    """遍历 SRT 文件行列表中的字幕条目。

    Yields: (line_index, parsed_dict) — line_index is 0-based.
    """
    idx = 0
    while idx < len(lines):
        start_idx = idx
        cue, idx = parse_srt_cue(lines, idx)
        if cue is None:
            idx += 1
            continue
        yield start_idx, cue


def collect_names(target_dir: str) -> set[str]:
    """SRT 没有 Name 字段，始终返回空集合。（兼容 ASS API）"""
    return set()


def collect_texts(target_dir: str, styles: Optional[set] = None) -> list[str]:
    """收集全部 SRT 文件中的可见文本（已 strip 标签）。"""
    texts = []
    for fname, fpath in iter_srt_files(target_dir):
        lines = read_srt_file(fpath)
        for _, d in iter_dialogue_lines(lines):
            visible = strip_srt_tags(d['text'])
            if visible.strip():
                texts.append(visible)
    return texts
