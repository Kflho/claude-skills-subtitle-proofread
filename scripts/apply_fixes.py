#!/usr/bin/env python3
"""统一修复脚本。

读取 Claude 审查后的 fixes.json，按 action 类型批量应用修复。

用法: python apply_fixes.py --target-dir <DIR> --fixes <FIXES.json> [--dry-run]

fixes.json 格式（Claude 审查后输出）:
[
  {
    "action": "replace_text",     // 替换指定行的 Dialogue text
    "file": "Episode 001.ass",
    "line": 42,
    "replacement": "新文本"
  },
  {
    "action": "replace_name",     // 替换指定行的 Name 字段
    "file": "Episode 001.ass",
    "line": 42,
    "replacement": "新名称"
  },
  {
    "action": "delete_line",      // 删除指定行
    "file": "Episode 001.ass",
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
    "action": "delete_style",     // 删除指定样式的所有行
    "style": "Roboto"
  },
  {
    "action": "delete_comment",    // 删除含关键词的 Comment 行
    "keyword": "Translated by"
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


def apply_replace_text(lines, fix):
    """替换指定行的 text 字段。"""
    i = fix['line'] - 1  # 转为 0-index
    if i < 0 or i >= len(lines):
        return False, f"行号 {fix['line']} 超出范围"
    d = parse_dialogue(lines[i])
    if d is None:
        return False, f"第 {fix['line']} 行不是 Dialogue 行"
    old = d['text']
    d['text'] = fix['replacement']
    lines[i] = build_dialogue_line(d) + '\n'
    return True, f"{old[:40]} → {fix['replacement'][:40]}"


def apply_replace_name(lines, fix):
    """替换指定行的 Name 字段。"""
    i = fix['line'] - 1
    if i < 0 or i >= len(lines):
        return False, f"行号 {fix['line']} 超出范围"
    d = parse_dialogue(lines[i])
    if d is None:
        return False, f"第 {fix['line']} 行不是 Dialogue 行"
    old = d['name']
    d['name'] = fix['replacement']
    lines[i] = build_dialogue_line(d) + '\n'
    return True, f"Name: {old} → {fix['replacement']}"


def apply_delete_line(lines, fix):
    """删除指定行。"""
    i = fix['line'] - 1
    if i < 0 or i >= len(lines):
        return False, f"行号 {fix['line']} 超出范围"
    old = lines[i].strip()[:60]
    lines[i] = ''  # 标记为空，稍后过滤
    return True, f"删除: {old}"


def apply_replace_global(fpath, fix):
    """全局文本替换（不区分文件/行）。"""
    with open(fpath, 'r', encoding='utf-8') as f:
        content = f.read()
    count = content.count(fix['original'])
    if count > 0:
        content = content.replace(fix['original'], fix['replacement'])
        with open(fpath, 'w', encoding='utf-8') as f:
            f.write(content)
    return count > 0, f"全局替换 {count} 处"


def apply_replace_global_regex(fpath, fix):
    """全局正则替换。"""
    with open(fpath, 'r', encoding='utf-8') as f:
        content = f.read()
    new_content, count = re.subn(fix['pattern'], fix['replacement'], content)
    if count > 0:
        with open(fpath, 'w', encoding='utf-8') as f:
            f.write(new_content)
    return count > 0, f"正则替换 {count} 处"


def apply_delete_style(lines, fix):
    """删除指定样式的所有 Dialogue 行。"""
    deleted = 0
    for i, line in enumerate(lines):
        d = parse_dialogue(line)
        if d and d['style'] == fix['style']:
            lines[i] = ''
            deleted += 1
    return deleted > 0, f"删除样式 '{fix['style']}' 共 {deleted} 行"


def apply_delete_comment(lines, fix):
    """删除含关键词的 Comment 行。"""
    deleted = 0
    for i, line in enumerate(lines):
        if line.startswith('Comment:'):
            parts = line.strip().split(',', 9)
            if len(parts) >= 10 and fix['keyword'] in parts[9]:
                lines[i] = ''
                deleted += 1
    return deleted > 0, f"删除 Comment 行 {deleted} 行"


def main():
    parser = argparse.ArgumentParser(description='统一修复脚本')
    parser.add_argument('--target-dir', required=True, help='目标 ASS 字幕目录')
    parser.add_argument('--fixes', required=True, help='Claude 审查后的 fixes.json')
    parser.add_argument('--dry-run', action='store_true', help='仅预览，不实际写入')
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

    # 2. 样式/注释级修复
    if style_fixes:
        print("\n=== 样式/注释修复 ===\n")
        for fix in style_fixes:
            applied = 0
            for fname, fpath in iter_ass_files(args.target_dir):
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
            file_fixes = sorted(per_file_fixes[fname], key=lambda f: f.get('line', 0))

            applied = 0
            for fix in file_fixes:
                action = fix['action']
                if action == 'replace_text':
                    ok, msg = apply_replace_text(lines, fix)
                elif action == 'replace_name':
                    ok, msg = apply_replace_name(lines, fix)
                elif action == 'delete_line':
                    ok, msg = apply_delete_line(lines, fix)
                else:
                    ok, msg = False, f"未知 action: {action}"

                if ok:
                    applied += 1
                else:
                    total_skipped += 1
                    print(f"  ✗ {fname}:{fix.get('line', '?')} - {msg}")

            if applied > 0:
                if not args.dry_run:
                    lines = [l for l in lines if l != '']
                    write_ass_file(fpath, lines)
                total_applied += applied
                print(f"  {fname}: {applied} 处修复")

    print(f"\n{'[DRY-RUN] ' if args.dry_run else ''}共应用 {total_applied} 项修复，跳过 {total_skipped} 项")


if __name__ == '__main__':
    main()
