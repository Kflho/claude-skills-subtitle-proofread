#!/usr/bin/env python3
"""脚本: Name 字段扫描与检测。

扫描所有 Dialogue 行的 Name 字段，收集唯一值并输出。
支持 --scan 模式直接打印名称列表（用于创建映射模板）。
用法:
  python names_detect.py --target-dir <DIR>            # JSON 输出
  python names_detect.py --target-dir <DIR> --scan     # 打印映射模板
"""

import argparse
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ass_utils import parse_dialogue, read_ass_file, iter_ass_files


def collect_all_names(target_dir: str) -> tuple[list[str], dict[str, list[str]]]:
    """收集所有 Name 字段值。

    Args:
        target_dir: 目标目录

    Returns:
        (sorted_unique_names, by_file_dict)
    """
    all_names = set()
    by_file = {}

    for fname, fpath in iter_ass_files(target_dir):
        lines = read_ass_file(fpath)
        file_names = []
        for line in lines:
            d = parse_dialogue(line)
            if d is None:
                continue
            name = d['name'].strip()
            if name:
                all_names.add(name)
                file_names.append(name)
        if file_names:
            # 去重但保序
            seen = set()
            unique_file_names = []
            for n in file_names:
                if n not in seen:
                    seen.add(n)
                    unique_file_names.append(n)
            by_file[fname] = unique_file_names
        else:
            by_file[fname] = []

    return sorted(all_names), by_file


def scan_names(target_dir: str):
    """扫描并输出所有 Name 字段值，方便创建映射表。"""
    names, by_file = collect_all_names(target_dir)
    print('=== Name 字段扫描结果 ===')
    print(f'共 {len(names)} 个不同的 Name 值\n')
    # 打印 JSON 映射模板
    print('{\n' + '\n'.join(f"    \"{n}\": \"\"," for n in names) + '\n}')


def main():
    parser = argparse.ArgumentParser(description='Name 字段扫描与检测')
    parser.add_argument('--target-dir', required=True, help='目标 ASS 字幕目录')
    parser.add_argument('--scan', action='store_true',
                        help='仅扫描并打印 Name 字段映射模板')
    args = parser.parse_args()

    if args.scan:
        scan_names(args.target_dir)
        return

    names, by_file = collect_all_names(args.target_dir)

    result = {
        'names': names,
        'by_file': by_file,
    }

    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write('\n')


if __name__ == '__main__':
    main()
