#!/usr/bin/env python3
"""脚本: OP/ED 歌词轨全面检测（增强版）。

检测内容：
1. 比较两个 style 的歌词轨道文本（原有功能）
2. 检测行数不一致（Romaji vs Rus）
3. 检测多余/重复的时间码行
4. 检测遗漏的时间码行
5. 检测 Comment 行中残留的 OP 样式标记
6. 检测文本内容变体

用法:
  python oped_detect.py --target-dir <DIR> --config <CONFIG.json>
  python oped_detect.py --target-dir <DIR> --config <CONFIG.json> --summary  # 仅输出摘要

输出: JSON 到 stdout
  {
    "per_file_diffs": [...],      // 逐文件文本差异
    "count_mismatches": [...],    // 行数不一致
    "extra_timecodes": {...},     // 多余时间码
    "missing_timecodes": {...},   // 缺失时间码
    "comment_remnants": [...],    // Comment 行残留
    "text_variants": {...},       // 文本变体统计
    "summary": {...}              // 统计摘要
  }
"""

import argparse
import json
import sys
import os
import re
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ass_utils import (
    time_to_ms, parse_dialogue,
    read_ass_file, iter_ass_files
)


def detect_count_mismatch(fname, lines, source_style, ref_style):
    """检测两轨行数是否一致。"""
    s_count = sum(1 for l in lines if l.startswith('Dialogue:') and _get_style(l) == source_style)
    r_count = sum(1 for l in lines if l.startswith('Dialogue:') and _get_style(l) == ref_style)
    if s_count != r_count:
        return {'file': fname, 'source_count': s_count, 'ref_count': r_count,
                'diff': s_count - r_count}
    return None


def _get_style(line):
    """从 Dialogue 或 Comment 行提取 Style 字段。"""
    parts = line.strip().split(',', 9)
    if len(parts) >= 10:
        return parts[3].strip()
    return ''


def _get_text_and_start(line):
    """从行提取 (start, text)。"""
    parts = line.strip().split(',', 9)
    if len(parts) >= 10:
        return parts[1], parts[9]
    return '', ''


def detect_comment_remnants(fname, lines, source_style, ref_style):
    """检测 Comment 行中是否有 OP 样式残留。"""
    remnants = []
    for i, line in enumerate(lines):
        if line.startswith('Comment:'):
            style = _get_style(line)
            if style in (source_style, ref_style):
                _, text = _get_text_and_start(line)
                remnants.append({
                    'file': fname,
                    'line': i + 1,
                    'style': style,
                    'text_preview': text[:50] if text else '(空)',
                })
    return remnants


def detect_extra_missing_timecodes(fname, lines, source_style, ref_style,
                                    canonical_starts, tolerance_ms=500):
    """检测多余/缺失的时间码（相比规范时间码集）。"""
    if not canonical_starts:
        return None, None

    source_starts = set()
    for line in lines:
        if line.startswith('Dialogue:') and _get_style(line) == source_style:
            start, _ = _get_text_and_start(line)
            source_starts.add(time_to_ms(start))

    extra = source_starts - set(canonical_starts)
    missing = set(canonical_starts) - source_starts

    result_extra = None
    result_missing = None
    if extra:
        # 获取多余行内容
        extra_details = []
        for line in lines:
            if line.startswith('Dialogue:') and _get_style(line) == source_style:
                start, text = _get_text_and_start(line)
                ms = time_to_ms(start)
                if ms in extra:
                    extra_details.append({'start': start, 'text_preview': text[:60]})
        result_extra = {'file': fname, 'extra_timecodes': sorted(extra),
                        'details': extra_details}

    if missing:
        result_missing = {'file': fname, 'missing_timecodes': sorted(missing)}

    return result_extra, result_missing


def detect_text_variants(all_source_texts, canonical_starts_ms=None):
    """统计所有文件中相同时间码的文本变体。"""
    variants = {}
    # all_source_texts: {ep_name: {timecode_ms: text}}
    for start_ms in sorted(set().union(*[set(d.keys()) for d in all_source_texts.values()])):
        texts = Counter()
        for ep, text_map in all_source_texts.items():
            if start_ms in text_map:
                texts[text_map[start_ms]] += 1
        if len(texts) > 1:
            variants[start_ms] = [{'text': t, 'count': c} for t, c in texts.most_common()]
    return variants


def main():
    parser = argparse.ArgumentParser(description='OP/ED 歌词轨全面检测')
    parser.add_argument('--target-dir', required=True, help='目标 ASS 字幕目录')
    parser.add_argument('--config', required=True, help='JSON 配置文件')
    parser.add_argument('--summary', action='store_true', help='仅输出摘要')
    parser.add_argument('--canonical-timecodes', help='规范时间码列表 (JSON 文件, ms 数组)')
    args = parser.parse_args()

    with open(args.config, 'r', encoding='utf-8') as f:
        config = json.load(f)

    source_style = config['source_style']
    ref_style = config['ref_style']
    tolerance_ms = config.get('tolerance_ms', 500)

    # 加载规范时间码（如果提供）
    canonical_starts = None
    if args.canonical_timecodes:
        with open(args.canonical_timecodes, 'r', encoding='utf-8') as f:
            canonical_starts = json.load(f)

    # ── 收集所有数据 ──
    per_file_diffs = []
    count_mismatches = []
    extra_timecodes = []
    missing_timecodes = []
    comment_remnants = []
    all_source_texts = {}  # {ep_name: {ms: text}}

    for fname, fpath in iter_ass_files(args.target_dir):
        lines = read_ass_file(fpath)

        # 匹配 ep 编号
        ep_match = re.search(r'(\d+)', fname)
        ep_name = ep_match.group(1) if ep_match else fname

        # 1. 文本差异检测（原有功能）
        ref_map = {}
        for i, line in enumerate(lines):
            d = parse_dialogue(line)
            if d and d['style'] == ref_style:
                ref_map[time_to_ms(d['start'])] = d['text']

        source_map = {}
        for i, line in enumerate(lines):
            d = parse_dialogue(line)
            if d and d['style'] == source_style:
                ms = time_to_ms(d['start'])
                source_map[ms] = d['text']
                all_source_texts.setdefault(ep_name, {})[ms] = d['text']

        if ref_map:
            for start_ms, source_text in source_map.items():
                best_ref_ms = None
                best_distance = float('inf')
                for ref_ms in ref_map:
                    dist = abs(start_ms - ref_ms)
                    if dist <= tolerance_ms and dist < best_distance:
                        best_ref_ms = ref_ms
                        best_distance = dist

                if best_ref_ms is not None and ref_map[best_ref_ms] != source_text:
                    line_no = None
                    for i, line in enumerate(lines):
                        d = parse_dialogue(line)
                        if d and d['style'] == source_style and time_to_ms(d['start']) == start_ms:
                            line_no = i + 1
                            break

                    per_file_diffs.append({
                        'file': fname,
                        'line': line_no,
                        'start_ms': start_ms,
                        'source_text': source_text,
                        'ref_text': ref_map[best_ref_ms],
                        'style': source_style,
                    })

        # 2. 行数不一致检测
        cm = detect_count_mismatch(fname, lines, source_style, ref_style)
        if cm:
            count_mismatches.append(cm)

        # 3. Comment 残留检测
        cr = detect_comment_remnants(fname, lines, source_style, ref_style)
        comment_remnants.extend(cr)

        # 4. 多余/缺失时间码检测
        if canonical_starts:
            extra, missing = detect_extra_missing_timecodes(
                fname, lines, source_style, ref_style, canonical_starts, tolerance_ms)
            if extra:
                extra_timecodes.append(extra)
            if missing:
                missing_timecodes.append(missing)

    # 5. 文本变体统计
    text_variants = detect_text_variants(all_source_texts)

    # ── 构建摘要 ──
    total_source_lines = sum(len(m) for m in all_source_texts.values())
    files_with_diffs = len(set(d['file'] for d in per_file_diffs))
    files_with_mismatches = len(count_mismatches)
    files_with_extra = len(extra_timecodes)
    files_with_missing = len(missing_timecodes)
    files_with_remnants = len(set(c['file'] for c in comment_remnants))

    summary = {
        'total_files': len(all_source_texts),
        'total_source_lines': total_source_lines,
        'files_with_text_diffs': files_with_diffs,
        'files_with_count_mismatch': files_with_mismatches,
        'files_with_extra_timecodes': files_with_extra,
        'files_with_missing_timecodes': files_with_missing,
        'files_with_comment_remnants': files_with_remnants,
        'total_text_diffs': len(per_file_diffs),
        'total_comment_remnants': len(comment_remnants),
        'text_variant_count': len(text_variants),
    }

    # 常见 source_style 行数
    line_counts = Counter()
    for ep, m in all_source_texts.items():
        line_counts[len(m)] += 1
    summary['line_count_distribution'] = dict(line_counts.most_common())

    output = {
        'summary': summary,
    }

    if not args.summary:
        output['count_mismatches'] = count_mismatches
        output['comment_remnants'] = comment_remnants
        output['extra_timecodes'] = extra_timecodes
        output['missing_timecodes'] = missing_timecodes
        output['per_file_diffs'] = per_file_diffs
        output['text_variants'] = {str(k): v for k, v in text_variants.items()}

    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write('\n')


if __name__ == '__main__':
    main()
