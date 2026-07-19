#!/usr/bin/env python3
"""脚本: 机翻重复模式检测。

检测对话行中 2-4 字符序列连续重复 ≥8 次的模式（常见于机器翻译错误）。
排除拟声/尖叫等已知非错误模式（Pa-pa-pa, La-la-la 等）。
用法: python repeat_detect.py --target-dir <DIR> [--config <CONFIG.json>]
"""

import argparse
import json
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ass_utils import (
    strip_ass_tags, time_to_ms,
    read_ass_file, iter_ass_files, iter_dialogue_lines
)

# ── 排除模式 ─────────────────────────────────────────────────

# scat 拟声：Pa-pa-pa, La-la-la, Me-me-me, Ta-ta-ta
SCAT_SEQS = {'pa', 'la', 'me', 'ta'}

# 动物/拟声词（单字或双字重复序列常见于叫声描写，非翻译错误）
ONOMATOPOEIA_SEQS = {
    # 中文动物叫声
    '汪', '喵', '哞', '咩', '咯', '咕', '嘎', '呱', '吱', '啾',
    '嗷', '呜', '哼', '嘶', '喔', '啊', '哦', '嗯', '呃',
    # 英文动物叫声
    'woof', 'meow', 'moo', 'baa', 'quack', 'oink', 'cluck',
    'chirp', 'buzz', 'ribbit', 'neigh', 'roar', 'howl',
    'arf', 'bow', 'caw', 'coo', 'hoot', 'tweet',
    'wo', 'me', 'mu', 'ba', 'ha', 'he', 'ho', 'hi', 'hu',
}


def _is_scat(seq_lower: str) -> bool:
    """检查序列是否为 scat 拟声模式（Pa/La/Me/Ta 变体）。"""
    return seq_lower in SCAT_SEQS


def _is_onomatopoeia(seq_lower: str) -> bool:
    """检查序列是否为动物/拟声词。"""
    return seq_lower in ONOMATOPOEIA_SEQS


def _is_excluded(seq: str, full_match: str) -> bool:
    """综合判断是否应排除此重复序列。"""
    seq_lower = seq.lower()
    if _is_scat(seq_lower):
        return True
    if _is_onomatopoeia(seq_lower):
        return True
    # 如果整段文本几乎全为同一字符类型且含大量叹号/问号（情绪表达）
    stripped = re.sub(r'[!！?？\s\-~～]+', '', full_match)
    if len(stripped) > 0:
        unique_chars = set(stripped.lower())
        if len(unique_chars) <= 2:
            # 整段重复仅由 1-2 种字符构成，可能是情绪表达
            # 但仍需排除拟声词
            if all(c in 'aeiou' or _is_onomatopoeia(c) for c in unique_chars if c.isalpha()):
                return True
    return False


def _find_repeats(visible_text: str, min_repeats: int = 8) -> list[dict]:
    """在可见文本中查找 2-4 字符序列的连续重复。

    Args:
        visible_text: 去除 ASS 标签后的纯文本
        min_repeats: 最少重复次数

    Returns:
        找到的重复模式列表
    """
    results = []
    if not visible_text:
        return results

    for seq_len in [2, 3, 4]:
        if len(visible_text) < seq_len * min_repeats:
            continue
        i = 0
        while i <= len(visible_text) - seq_len * min_repeats:
            seq = visible_text[i:i + seq_len]
            # 跳过含空白/标点的序列（避免匹配分隔符模式）
            if re.search(r'[\s\-~～!！?？,，.。、；;：:]', seq):
                i += 1
                continue

            # 统计连续重复次数
            count = 1
            j = i + seq_len
            while j + seq_len <= len(visible_text) and visible_text[j:j + seq_len] == seq:
                count += 1
                j += seq_len

            if count >= min_repeats:
                full_match = visible_text[i:j]
                if not _is_excluded(seq, full_match):
                    results.append({
                        'repeat_seq': seq,
                        'repeat_count': count,
                        'full_match': full_match,
                    })
                i = j  # 跳到匹配区域之后
            else:
                i += 1

    return results


def main():
    parser = argparse.ArgumentParser(description='机翻重复模式检测')
    parser.add_argument('--target-dir', required=True, help='目标 ASS 字幕目录')
    parser.add_argument('--config', type=str, default=None,
                        help='JSON 配置文件（可选，用于指定 dialogue_styles 和 min_repeats）')
    args = parser.parse_args()

    # 默认配置
    dialogue_styles = {'Default', 'DefaultTop', 'Episode'}
    min_repeats = 8

    if args.config:
        with open(args.config, 'r', encoding='utf-8') as f:
            config = json.load(f)
        if 'dialogue_styles' in config:
            dialogue_styles = set(config['dialogue_styles'])
        if 'min_repeats' in config:
            min_repeats = config['min_repeats']

    findings = []

    for fname, fpath in iter_ass_files(args.target_dir):
        lines = read_ass_file(fpath)
        for line_idx, d in iter_dialogue_lines(lines, dialogue_styles):
            visible = strip_ass_tags(d['text'])
            if not visible.strip():
                continue

            repeats = _find_repeats(visible, min_repeats)
            for r in repeats:
                findings.append({
                    'file': fname,
                    'line': line_idx + 1,  # 1-based 行号
                    'start_ms': time_to_ms(d['start']),
                    'visible_text': visible,
                    'repeat_seq': r['repeat_seq'],
                    'repeat_count': r['repeat_count'],
                    'full_match': r['full_match'],
                })

    json.dump(findings, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write('\n')


if __name__ == '__main__':
    main()
