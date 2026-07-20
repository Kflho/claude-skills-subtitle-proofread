#!/usr/bin/env python3
"""统一修复脚本 — 支持 ASS 和 SRT 格式。

读取 Claude 审查后的 fixes.json，按 action 类型批量应用修复。

用法: python apply_fixes.py --target-dir <DIR> --fixes <FIXES.json> [--dry-run]

fixes.json 格式（Claude 审查后输出）:
[
  {
    "action": "replace_text",     // 替换指定行的条目 text
    "file": "Episode 001.srt",
    "line": 42,                   // 该 cue 块内任一行号均可定位
    "replacement": "新文本"
  },
  {
    "action": "replace_name",     // 替换指定行的 Name 字段 (仅 ASS)
    "file": "Episode 001.ass",
    "line": 42,
    "replacement": "新名称"
  },
  {
    "action": "delete_line",      // 删除指定行（SRT: 删除整个 cue 块）
    "file": "Episode 001.srt",
    "line": 42
  },
  {
    "action": "replace_global",   // 全局文本替换（不区分文件/行）
    "original": "旧文本",
    "replacement": "新文本"
  },
  {
    "action": "replace_global_regex",  // 全局正则替换
    "pattern": "正则模式",
    "replacement": "替换文本"
  },
  {
    "action": "delete_style",     // 删除指定样式的所有行 (仅 ASS)
    "style": "Roboto"
  },
  {
    "action": "delete_comment",    // 删除含关键词的 Comment 行 (仅 ASS)
    "keyword": "Translated by"
  },
  {
    "action": "merge_cues",       // 合并相邻 SRT cues (SRT 专用)
    "file": "Episode 001.srt",
    "line": 42,                   // 起始 cue 的任一行号
    "count": 3,                   // 要合并的连续 cue 数量
    "note": "合并碎片行"
  }
]
"""

import argparse
import json
import re
import sys
import os
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ass_utils import (
    parse_dialogue, build_dialogue_line,
    read_ass_file, write_ass_file, iter_ass_files
)

# 尝试导入 srt_utils
try:
    from . import srt_utils
except ImportError:
    import srt_utils


# ═══════════════════════════════════════════════════════════════
# SRT 辅助函数
# ═══════════════════════════════════════════════════════════════

def _is_srt(path: str) -> bool:
    return path.lower().endswith('.srt')


def _parse_srt_cues(lines: list[str]) -> list[dict]:
    """将 SRT 文件行列表解析为 cue 列表。

    每个 cue 包含: cue_dict (同 parse_srt_cue) + _start_line / _end_line
    """
    cues = []
    idx = 0
    while idx < len(lines):
        start_idx = idx
        cue, idx = srt_utils.parse_srt_cue(lines, idx)
        if cue is None:
            idx += 1
            continue
        cue['_start_line'] = start_idx
        cue['_end_line'] = idx - 1  # 最后一个有效行的索引
        cues.append(cue)
    return cues


def _find_srt_cue_by_line(cues: list[dict], line_num: int) -> dict | None:
    """通过 1-based 行号查找包含该行的 SRT cue。"""
    line_idx = line_num - 1  # 转为 0-based
    for cue in cues:
        if cue['_start_line'] <= line_idx <= cue['_end_line']:
            return cue
    return None


def _rebuild_srt_lines(cues: list[dict]) -> list[str]:
    """从 cue 列表重建 SRT 文件行列表。"""
    lines = []
    for i, cue in enumerate(cues):
        lines.extend(srt_utils.build_srt_cue_lines(cue))
    return lines


def _find_srt_cues_range(cues: list[dict], start_line: int, count: int) -> list[dict]:
    """从起始行号找到连续的 count 个 SRT cues。"""
    start_idx = None
    for i, cue in enumerate(cues):
        line_idx = start_line - 1
        if cue['_start_line'] <= line_idx <= cue['_end_line']:
            start_idx = i
            break
    if start_idx is None:
        return []
    end_idx = min(start_idx + count, len(cues))
    return cues[start_idx:end_idx]


def apply_replace_text(lines, fix):
    """替换指定行的 text 字段。支持 ASS 和 SRT。"""
    i = fix['line'] - 1  # 转为 0-index
    if i < 0 or i >= len(lines):
        return False, f"行号 {fix['line']} 超出范围"

    # SRT 格式：需要找到对应的 cue 块
    if _is_srt(fix.get('file', '')):
        cues = _parse_srt_cues(lines)
        cue = _find_srt_cue_by_line(cues, fix['line'])
        if cue is None:
            return False, f"第 {fix['line']} 行不在任何 SRT cue 中"
        old = cue['text']
        cue['text'] = fix['replacement']
        # 重建文件行
        new_lines = _rebuild_srt_lines(cues)
        lines.clear()
        lines.extend(new_lines)
        return True, f"{old[:40]} → {fix['replacement'][:40]}"

    # ASS 格式
    d = parse_dialogue(lines[i])
    if d is None:
        return False, f"第 {fix['line']} 行不是 Dialogue 行"
    old = d['text']
    d['text'] = fix['replacement']
    lines[i] = build_dialogue_line(d) + '\n'
    return True, f"{old[:40]} → {fix['replacement'][:40]}"


def apply_replace_name(lines, fix):
    """替换指定行的 Name 字段（仅 ASS）。"""
    i = fix['line'] - 1
    if i < 0 or i >= len(lines):
        return False, f"行号 {fix['line']} 超出范围"
    # SRT 无 Name 字段
    if _is_srt(fix.get('file', '')):
        return False, "SRT 文件不支持 Name 字段替换"
    d = parse_dialogue(lines[i])
    if d is None:
        return False, f"第 {fix['line']} 行不是 Dialogue 行"
    old = d['name']
    d['name'] = fix['replacement']
    lines[i] = build_dialogue_line(d) + '\n'
    return True, f"Name: {old} → {fix['replacement']}"


def apply_delete_line(lines, fix):
    """删除指定行。SRT 格式会删除整个 cue 块。"""
    i = fix['line'] - 1
    if i < 0 or i >= len(lines):
        return False, f"行号 {fix['line']} 超出范围"

    # SRT 格式：删除整个 cue 块
    if _is_srt(fix.get('file', '')):
        cues = _parse_srt_cues(lines)
        cue = _find_srt_cue_by_line(cues, fix['line'])
        if cue is None:
            return False, f"第 {fix['line']} 行不在任何 SRT cue 中"
        old_text = cue['text'][:40]
        cues.remove(cue)
        new_lines = _rebuild_srt_lines(cues)
        lines.clear()
        lines.extend(new_lines)
        return True, f"删除 SRT cue: {old_text}"

    # ASS 格式
    old = lines[i].strip()[:60]
    lines[i] = ''  # 标记为空，稍后过滤
    return True, f"删除: {old}"


def apply_merge_cues(lines, fix):
    """合并相邻的 SRT cues（SRT 专用）。"""
    if not _is_srt(fix.get('file', '')):
        return False, "merge_cues 仅支持 SRT 格式"

    count = fix.get('count', 2)
    cues = _parse_srt_cues(lines)
    target_cues = _find_srt_cues_range(cues, fix['line'], count)

    if len(target_cues) < 2:
        return False, f"起始行 {fix['line']} 附近未找到足够的 cues（需 {count}，找到 {len(target_cues)}）"

    # 合并 cues
    merged_text_parts = []
    for c in target_cues:
        if c['text'].strip():
            merged_text_parts.append(c['text'].strip())

    merged_text = '\n'.join(merged_text_parts) if fix.get('multiline', True) else ' '.join(merged_text_parts)

    # 使用第一个 cue 的时间范围
    target_cues[0]['text'] = merged_text
    target_cues[0]['end'] = target_cues[-1]['end']
    target_cues[0]['_end_line'] = target_cues[-1]['_end_line']

    # 移除后续 cues
    for c in target_cues[1:]:
        cues.remove(c)

    # 重建文件行
    new_lines = _rebuild_srt_lines(cues)
    lines.clear()
    lines.extend(new_lines)
    return True, f"合并 {count} 个 cues: {merged_text[:60]}..."


def apply_replace_global(fpath, fix):
    """全局文本替换（不区分文件/行）。"""
    with open(fpath, 'r', encoding='utf-8-sig' if _is_srt(fpath) else 'utf-8') as f:
        content = f.read()
    count = content.count(fix['original'])
    if count > 0:
        content = content.replace(fix['original'], fix['replacement'])
        with open(fpath, 'w', encoding='utf-8') as f:
            f.write(content)
    return count > 0, f"全局替换 {count} 处"


def apply_replace_global_regex(fpath, fix):
    """全局正则替换。"""
    with open(fpath, 'r', encoding='utf-8-sig' if _is_srt(fpath) else 'utf-8') as f:
        content = f.read()
    new_content, count = re.subn(fix['pattern'], fix['replacement'], content)
    if count > 0:
        with open(fpath, 'w', encoding='utf-8') as f:
            f.write(new_content)
    return count > 0, f"正则替换 {count} 处"


def apply_delete_style(lines, fix):
    """删除指定样式的所有 Dialogue 行（仅 ASS）。"""
    deleted = 0
    for i, line in enumerate(lines):
        d = parse_dialogue(line)
        if d and d['style'] == fix['style']:
            lines[i] = ''
            deleted += 1
    return deleted > 0, f"删除样式 '{fix['style']}' 共 {deleted} 行"


def apply_delete_comment(lines, fix):
    """删除含关键词的 Comment 行（仅 ASS）。"""
    deleted = 0
    for i, line in enumerate(lines):
        if line.startswith('Comment:'):
            parts = line.strip().split(',', 9)
            if len(parts) >= 10 and fix['keyword'] in parts[9]:
                lines[i] = ''
                deleted += 1
    return deleted > 0, f"删除 Comment 行 {deleted} 行"


def main():
    parser = argparse.ArgumentParser(description='统一修复脚本 — 支持 ASS 和 SRT')
    parser.add_argument('--target-dir', required=True, help='目标字幕目录')
    parser.add_argument('--fixes', required=True, help='Claude 审查后的 fixes.json')
    parser.add_argument('--dry-run', action='store_true', help='仅预览，不实际写入')
    parser.add_argument('--log-to-report', help='统一问题解决报告路径（追加修复记录）')
    parser.add_argument('--step', type=int, default=0, help='报告步骤编号（1-16），配合 --log-to-report 使用')
    args = parser.parse_args()

    with open(args.fixes, 'r', encoding='utf-8') as f:
        fixes = json.load(f)

    # 分类修复项
    per_file_fixes = defaultdict(list)  # file → [fixes]
    global_fixes = []
    style_fixes = []

    for fix in fixes:
        action = fix['action']
        if action in ('replace_global', 'replace_global_regex'):
            global_fixes.append(fix)
        elif action in ('delete_style', 'delete_comment'):
            style_fixes.append(fix)
        elif 'file' in fix:
            per_file_fixes[fix['file']].append(fix)
        else:
            print(f"⚠ 跳过无效修复项: {fix}")

    total_applied = 0
    total_skipped = 0
    report_entries = []  # 用于 --log-to-report

    # 辅助：从文件名提取集数
    def _extract_ep(fname):
        m = re.search(r'(?:EP)?(\d{3})', fname)
        return f'EP{m.group(1)}' if m else fname[:20]

    # 1. 全局替换（先执行，不依赖行号）
    if global_fixes:
        print("=== 全局替换 ===\n")
        for fix in global_fixes:
            applied = 0
            for fname, fpath in iter_ass_files(args.target_dir):
                if fix['action'] == 'replace_global':
                    ok, msg = apply_replace_global(fpath, fix)
                else:
                    ok, msg = apply_replace_global_regex(fpath, fix)
                if ok:
                    applied += 1
            if applied > 0:
                total_applied += 1
                print(f"  ✓ {fix.get('note', fix['action'])} → 影响 {applied} 个文件")

    # 2. 样式/注释级修复（仅 ASS）
    if style_fixes:
        print("\n=== 样式/注释修复 ===\n")
        for fix in style_fixes:
            applied = 0
            for fname, fpath in iter_ass_files(args.target_dir):
                if fname.lower().endswith('.srt'):
                    continue  # SRT 不支持样式/注释
                lines = read_ass_file(fpath)
                if fix['action'] == 'delete_style':
                    ok, msg = apply_delete_style(lines, fix)
                else:
                    ok, msg = apply_delete_comment(lines, fix)
                if ok:
                    if not args.dry_run:
                        lines = [l for l in lines if l != '']
                        write_ass_file(fpath, lines)
                    applied += 1
            if applied > 0:
                total_applied += 1
                print(f"  ✓ {fix.get('note', fix['action'])} → 影响 {applied} 个文件")

    # 3. 逐文件逐行修复
    if per_file_fixes:
        print("\n=== 逐行修复 ===\n")
        for fname, fpath in iter_ass_files(args.target_dir):
            if fname not in per_file_fixes:
                continue
            lines = read_ass_file(fpath)
            # 按行号降序排列，避免 delete_line 导致后续修复行号偏移
            file_fixes = sorted(per_file_fixes[fname], key=lambda f: f.get('line', 0), reverse=True)

            # 预解析 SRT cues（用于获取时间码）
            srt_cues_cache = None
            if _is_srt(fpath):
                srt_cues_cache = _parse_srt_cues(lines)

            applied = 0
            ep_tag = _extract_ep(fname)
            for fix in file_fixes:
                # 记录修复前信息（用于报告日志）
                pre_time = ''
                pre_text = ''
                if srt_cues_cache:
                    cue = _find_srt_cue_by_line(srt_cues_cache, fix.get('line', 0))
                    if cue:
                        pre_time = cue.get('start', '')
                        pre_text = cue.get('text', '')[:120]

                action = fix['action']
                if action == 'replace_text':
                    ok, msg = apply_replace_text(lines, fix)
                elif action == 'replace_name':
                    ok, msg = apply_replace_name(lines, fix)
                elif action == 'delete_line':
                    ok, msg = apply_delete_line(lines, fix)
                elif action == 'merge_cues':
                    ok, msg = apply_merge_cues(lines, fix)
                else:
                    ok, msg = False, f"未知 action: {action}"

                if ok:
                    applied += 1
                    if args.log_to_report and args.step:
                        if action == 'replace_text':
                            report_entries.append({
                                'ep': ep_tag, 'time': pre_time,
                                'original': pre_text,
                                'corrected': fix.get('replacement', '')[:120],
                                'status': '✅',
                            })
                        elif action == 'delete_line':
                            report_entries.append({
                                'ep': ep_tag, 'time': pre_time,
                                'original': pre_text,
                                'corrected': '(已删除)',
                                'status': '🗑️',
                            })
                        elif action == 'merge_cues':
                            report_entries.append({
                                'ep': ep_tag, 'time': pre_time,
                                'original': pre_text,
                                'corrected': f'合并 {fix.get("count", 2)} 个 cues',
                                'status': '✅',
                            })
                        elif action == 'replace_name':
                            report_entries.append({
                                'ep': ep_tag, 'time': pre_time,
                                'original': f'Name: {pre_text}',
                                'corrected': f'Name: {fix.get("replacement", "")}',
                                'status': '✅',
                            })
                else:
                    total_skipped += 1
                    print(f"  ✗ {fname}:{fix.get('line', '?')} - {msg}")

            if applied > 0:
                if not args.dry_run:
                    if _is_srt(fpath):
                        # SRT: _rebuild_srt_lines 已更新 lines 内容，直接写入
                        write_ass_file(fpath, lines)
                    else:
                        lines = [l for l in lines if l != '']
                        write_ass_file(fpath, lines)
                total_applied += applied
                print(f"  {fname}: {applied} 处修复")

    # ── 报告日志 ──
    if args.log_to_report and args.step and report_entries:
        from update_report import upsert_entries as _upsert
        _upsert(args.log_to_report, step=args.step, entries=report_entries)
        print(f'\n📋 已记录 {len(report_entries)} 条到问题解决报告步骤{args.step}')

    print(f"\n{'[DRY-RUN] ' if args.dry_run else ''}共应用 {total_applied} 项修复，跳过 {total_skipped} 项")


if __name__ == '__main__':
    main()
