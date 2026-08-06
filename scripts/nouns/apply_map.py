#!/usr/bin/env python3
"""专名统一应用 —— AI 决策的便利执行工具（重新设计的专名审查流程 · 应用层）。

脚本本身不做决策；它把 running-map（temp/noun_map.json，AI 审查定稿后的
标准译名表）机械地应用到中文字幕：

    1. 计算每条标准名的作用域：
         scope=global     → 全部集
         scope=per_episode → 仅该标准名出现的集（entry.episodes）
         scope=auto       → episodes ≥ 2 → global；= 1 → per_episode
    2. 把每条标准名的 zh_variants（非标准译法）替换成标准名：
         - 只在字幕文本行替换（不动序号/时间轴/空行）
         - 保留源文件 BOM + 换行符（CRLF/LF）原样
         - 防双替换：variant 是 canonical 后缀时加负向断言
           （茶水博士→御茶水博士 不会误伤已有的 御茶水博士）
    3. 输出替换报告（每条标准名：替换数 + 分布）

dry-run 先预览，AI 确认后再 --apply。脚本是"让 AI 便利快速替换"的工具。

Usage:
  # 预览（不改文件）
  python nouns/apply_map.py temp/noun_map.json --target-dir 260806 --dry-run

  # 应用
  python nouns/apply_map.py temp/noun_map.json --target-dir 260806 --apply

  # 导出 ja→zh 预替换表（供 translate_srt --mappings 幻觉控制）
  python nouns/apply_map.py temp/noun_map.json --target-dir 260806 \
      --emit-mappings temp/noun_map_ja_to_zh.json
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lib._path  # noqa: F401,E402

from lib.whisper_utils import extract_ep_number  # noqa: E402

import nouns.extractor as extractor  # noqa: E402  (resolve_episode_file 复用)


def _build_repl(variant, canonical):
    """构建替换正则。variant 是 canonical 后缀时加负向断言防双替换。"""
    if canonical.endswith(variant) and len(canonical) > len(variant):
        prefix = canonical[:-len(variant)]
        return re.compile(r'(?<!' + re.escape(prefix) + r')' + re.escape(variant))
    return re.compile(re.escape(variant))


def _index_files(target_dir):
    """target_dir 下所有字幕文件 → 集号索引 {EPNNN: path}。"""
    index = {}
    if not os.path.isdir(target_dir):
        return index
    for fname in os.listdir(target_dir):
        if not fname.lower().endswith(('.srt', '.ass')):
            continue
        path = os.path.join(target_dir, fname)
        ep = extract_ep_number(path)
        if ep == '???':
            m = re.search(r'-\s*(\d{1,3})(?:[vV]\d+)?\s*-', fname)
            ep = f'EP{int(m.group(1)):03d}' if m else '???'
        if ep != '???':
            index[ep] = path
    return index


def _effective_scope(entry):
    """scope 判定：auto → episodes≥2 global，=1 per_episode。"""
    scope = entry.get('scope') or 'auto'
    if scope == 'auto':
        return 'global' if len(entry.get('episodes', [])) >= 2 else 'per_episode'
    return scope


def _apply_to_text(text, repls):
    """对单条字幕文本依次应用替换。返回 (新文本, 替换次数)。"""
    changed = 0
    for regex in repls:
        new_text, n = regex.subn(lambda m: repls[regex], text)
        changed += n
        text = new_text
    return text, changed


def _process_srt(path, file_repls, apply):
    """处理单个文件。file_repls: {canonical: [(regex, canonical), ...]}。

    Returns: (file, 替换总数, {canonical: 次数})。
    """
    with open(path, 'rb') as f:
        raw = f.read()

    # 检测 BOM
    bom = raw.startswith(b'\xef\xbb\xbf')
    try:
        content = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        content = raw.decode('utf-8', errors='ignore')

    # 检测换行符
    eol = '\r\n' if '\r\n' in content else '\n'
    lines = content.splitlines(keepends=False)

    counts = {c: 0 for c in file_repls}
    out_lines = []
    for line in lines:
        stripped = line.strip()
        # 跳过序号行、时间轴行、空行 —— 只处理文本行
        is_index = re.fullmatch(r'\d+', stripped) is not None
        is_timecode = '-->' in line
        if is_index or is_timecode or not stripped:
            out_lines.append(line)
            continue
        new_line = line
        for canonical, repls in file_repls.items():
            for regex in repls:
                new_line, n = _apply_to_text(new_line, {regex: canonical})
                counts[canonical] += n
        out_lines.append(new_line)

    total = sum(counts.values())
    if apply and total > 0:
        new_content = eol.join(out_lines) + eol
        with open(path, 'wb') as f:
            f.write(b'\xef\xbb\xbf' if bom else b'')
            f.write(new_content.encode('utf-8'))
    return os.path.basename(path), total, counts


def run_map(noun_map, target_dir, dry_run=True):
    """把 map 应用到 target_dir。返回报告字符串。"""
    files = _index_files(target_dir)
    if not files:
        print(f'[apply_map] 警告：{target_dir} 无字幕文件', file=sys.stderr)
        return ''

    # 为每个文件预构建替换表
    file_repls = {}   # path → {canonical: [regex, ...]}
    scope_used = {}   # canonical → scope
    skipped = []
    for canonical, entry in noun_map.items():
        variants = [v for v in entry.get('zh_variants', []) if v and v != canonical]
        if not variants:
            continue
        scope = _effective_scope(entry)
        scope_used[canonical] = scope
        if scope == 'per_episode':
            ep_set = set(entry.get('episodes', []))
            target_files = [files[ep] for ep in ep_set if ep in files]
            if not target_files:
                skipped.append((canonical, '单集文件未找到'))
                continue
        else:
            target_files = list(files.values())
        for path in target_files:
            repls = file_repls.setdefault(path, {})
            repls[canonical] = [_build_repl(v, canonical) for v in variants]

    # 逐文件处理
    per_file = {}
    for path, repls in sorted(file_repls.items()):
        fname, total, counts = _process_srt(path, repls, apply=not dry_run)
        per_file[fname] = (total, counts)

    # 汇总报告
    report = []
    header = 'dry-run（预览，未改文件）' if dry_run else 'apply（已写入文件）'
    report.append(f'[apply_map] {header} · 目标 {target_dir}')
    report.append(f'[apply_map] 覆盖 {len(file_repls)}/{len(files)} 文件，'
                  f'含变体的标准名 {len(scope_used)} 条')
    for canonical, scope in sorted(scope_used.items(), key=lambda kv: -sum(
            c for f, (t, c) in per_file.items() if canonical in c)):
        # 统计该标准名总替换数
        total_c = sum(c[canonical] for f, (t, c) in per_file.items() if canonical in c)
        if total_c == 0:
            continue
        loc = ', '.join(f'{f}({c[canonical]})' for f, (t, c) in per_file.items()
                        if canonical in c and c[canonical] > 0)
        report.append(f'  {canonical} [{scope}] {total_c} 处 → {loc}')
    for canonical, why in skipped:
        report.append(f'  ⚠ {canonical}: {why}')

    return '\n'.join(report) + '\n'


def emit_ja_to_zh(noun_map, out_path, merge_with=None):
    """从 map 导出 ja_form → canonical 映射表。"""
    ja_to_zh = {}
    for canonical, entry in noun_map.items():
        for ja_form in entry.get('ja_forms', []):
            if ja_form:
                ja_to_zh[ja_form] = canonical
    if merge_with:
        with open(merge_with, 'r', encoding='utf-8') as f:
            existing = json.load(f)
        # 已存在的、map 未覆盖的条目保留
        for k, v in existing.items():
            ja_to_zh.setdefault(k, v)
    os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(ja_to_zh, f, ensure_ascii=False, indent=2)
    print(f'[apply_map] ja→zh 映射 {len(ja_to_zh)} 条 → {out_path}')


def main():
    ap = argparse.ArgumentParser(
        description='专名统一应用工具（AI 决策的便利执行器）')
    ap.add_argument('map_path', help='running-map JSON（temp/noun_map.json）')
    ap.add_argument('--target-dir', default='260806', help='中文字幕目录')
    ap.add_argument('--dry-run', action='store_true', help='预览，不改文件')
    ap.add_argument('--apply', action='store_true', help='实际写入文件')
    ap.add_argument('--emit-mappings', help='导出 ja→zh 预替换表到该路径')
    ap.add_argument('--merge-existing',
                    help='--emit-mappings 时合并已有的 ja→zh 映射（如 temp/noun_mappings.json）')
    args = ap.parse_args()

    with open(args.map_path, 'r', encoding='utf-8') as f:
        noun_map = json.load(f)

    if args.emit_mappings:
        emit_ja_to_zh(noun_map, args.emit_mappings,
                      merge_with=args.merge_existing)
        return

    print(run_map(noun_map, args.target_dir, dry_run=not args.apply))


if __name__ == '__main__':
    main()
