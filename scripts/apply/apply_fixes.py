#!/usr/bin/env python3
"""统一修复脚本 — 支持 ASS 和 SRT 格式。

读取 Claude 审查后的 fixes.json，按 action 类型批量应用修复。
支持三种输入模式：
  --fixes fixes.json       JSON 修复列表（主模式）
  --review checklist.md    人工审查清单（markdown 格式）
  --trad-to-simp           内置繁→简转换（--lang zh 自动启用）

用法: python apply_fixes.py --target-dir <DIR> --fixes <FIXES.json> [--dry-run]
      python apply_fixes.py --target-dir <DIR> --review checklist.md
      python apply_fixes.py --target-dir <DIR> --trad-to-simp --lang zh

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

import lib._path  # noqa: F401

from lib.whisper_utils import setup_windows_utf8, extract_ep_number

from lib.ass_utils import (
    parse_dialogue, build_dialogue_line,
    read_ass_file, write_ass_file, iter_ass_files
)

from lib.subtitle_io import (
    read_subtitles, write_subtitles, apply_fixes_to_cues,
    _find_cue as _find_cue_by_timecode,
)


# ═══════════════════════════════════════════════════════════════
# SRT 辅助函数 — 使用 cue 模型（subtitle_io）
# ═══════════════════════════════════════════════════════════════

def _is_srt(path: str) -> bool:
    return path.lower().endswith('.srt')


def _load_srt_cues(fpath: str) -> list[dict]:
    """Load SRT file as cue dicts via subtitle_io."""
    return read_subtitles(fpath, mark_garbled=False)


def _save_srt_cues(fpath: str, cues: list[dict]):
    """Write cue dicts back to SRT file via subtitle_io."""
    write_subtitles(fpath, cues)


def _find_srt_cue_by_line(cues: list[dict], line_num: int) -> dict | None:
    """Find cue by 1-based line number (fallback, kept for backward compat)."""
    line_idx = line_num - 1
    for cue in cues:
        sl = cue.get('_start_line', -1)
        if sl <= line_idx <= sl + 4:  # SRT blocks are ~4 lines
            return cue
    return None


def _find_srt_cue(cues: list[dict], fix: dict) -> dict | None:
    """Find cue by 'start' timecode (primary) or 'line' number (fallback)."""
    return _find_cue_by_timecode(cues, fix)


def apply_replace_text(cues, fix):
    """Replace text of a single cue (cue model)."""
    cue = _find_srt_cue(cues, fix)
    if cue is None:
        return False, f"未找到 cue: start={fix.get('start', '?')}, line={fix.get('line', '?')}"
    old = cue['text']
    if old == fix['replacement']:
        return True, f"already correct: {old[:40]}"
    cue['text'] = fix['replacement']
    return True, f"{old[:40]} → {fix['replacement'][:40]}"


def apply_delete_line(cues, fix):
    """Delete a single cue from the list."""
    cue = _find_srt_cue(cues, fix)
    if cue is None:
        return True, f"already deleted: line {fix.get('line', '?')}"
    old_text = cue['text'][:40]
    cues.remove(cue)
    return True, f"删除 SRT cue: {old_text}"


def apply_merge_cues(cues, fix):
    """Merge consecutive cues in the cue list."""
    count = fix.get('count', 2)
    target = _find_srt_cue(cues, fix)
    if target is None:
        return False, f"未找到起始 cue"

    idx = cues.index(target)
    if idx + count > len(cues):
        return False, f"起始 cue 后不足 {count} 个 cues（仅 {len(cues) - idx}）"

    target_cues = cues[idx:idx + count]
    merged_parts = [c['text'].strip() for c in target_cues if c['text'].strip()]
    merged_text = '\n'.join(merged_parts) if fix.get('multiline', True) else ' '.join(merged_parts)

    target['text'] = merged_text
    target['end'] = target_cues[-1]['end']
    target['end_s'] = target_cues[-1]['end_s']

    for c in target_cues[1:]:
        cues.remove(c)
    return True, f"合并 {count} 个 cues: {merged_text[:60]}..."


def apply_replace_global(fpath, fix):
    """Global text replacement — operates on cues, not raw file. Safer, no cross-line risk."""
    cues = _load_srt_cues(fpath)
    old = fix['original']
    new = fix['replacement']
    count = 0
    for cue in cues:
        if old in cue['text']:
            cue['text'] = cue['text'].replace(old, new)
            count += 1
    if count > 0:
        _save_srt_cues(fpath, cues)
        return True, f"全局替换 {count} 处"
    return True, f"already correct: {old[:40]}"


def apply_replace_global_regex(fpath, fix):
    """Global regex replacement — operates on cues, not raw file."""
    cues = _load_srt_cues(fpath)
    pat = fix['pattern']
    repl = fix['replacement']
    total = 0
    for cue in cues:
        new_text, n = re.subn(pat, repl, cue['text'])
        if n > 0:
            cue['text'] = new_text
            total += n
    if total > 0:
        _save_srt_cues(fpath, cues)
        return True, f"正则替换 {total} 处"
    return True, f"already correct (regex): {fix['pattern'][:40]}"


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
    setup_windows_utf8()
    parser = argparse.ArgumentParser(description='统一修复脚本 — 支持 ASS 和 SRT')
    parser.add_argument('--target-dir', required=True, help='目标字幕目录')
    parser.add_argument('--fixes', help='Claude 审查后的 fixes.json')
    parser.add_argument('--review', help='人工审查清单 markdown 文件')
    parser.add_argument('--trad-to-simp', action='store_true', help='内置繁→简转换')
    parser.add_argument('--lang', default='ja', choices=['ja', 'zh'],
                        help='目标语言 (默认: ja)。zh 自动启用繁→简')
    parser.add_argument('--dry-run', action='store_true', help='仅预览，不实际写入')
    parser.add_argument('--log-to-report', help='统一问题解决报告路径（追加修复记录）')
    parser.add_argument('--step', type=str, default='0', help='报告层号（1/2/3/3.5/4/5/6），配合 --log-to-report 使用')
    args = parser.parse_args()

    # Step 0: 繁→简（--lang zh 自动启用）
    # NOTE: degloss (翻译腔去机械化) removed from zh pipeline — replaced
    # by DeepSeek LLM batch polish (polish_zh.py) for full coverage.
    if args.lang == 'zh' or args.trad_to_simp:
        print('[trad→simp] 繁→简转换 ...')
        _run_trad_to_simp(args.target_dir, dry_run=args.dry_run)

    # Load fixes from JSON or review checklist
    if args.review:
        print(f'[review] --review is deprecated.', file=sys.stderr)
        print(f'  [???] markers are written directly to SRT. Review in Aegisub.', file=sys.stderr)
        sys.exit(1)
    elif args.fixes:
        with open(args.fixes, 'r', encoding='utf-8') as f:
            fixes = json.load(f)
    else:
        if args.trad_to_simp or args.lang == 'zh':
            return  # 只做内置变换（繁→简 + 翻译腔去机械化），不需要 fixes
        print('ERROR: 需要 --fixes 或 --review 或 --trad-to-simp', file=sys.stderr)
        sys.exit(1)

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
    total_already = 0
    total_skipped = 0
    report_entries = []  # 用于 --log-to-report

    # 辅助：从文件名提取集数
    def _extract_ep(fname):
        return extract_ep_number(fname)

    # 1. 全局替换（先执行，不依赖行号）
    if global_fixes:
        print("=== 全局替换 ===\n")
        for fix in global_fixes:
            applied = 0
            already = 0
            for fname, fpath in iter_ass_files(args.target_dir):
                if fix['action'] == 'replace_global':
                    ok, msg = apply_replace_global(fpath, fix)
                else:
                    ok, msg = apply_replace_global_regex(fpath, fix)
                if ok and 'already correct' in msg:
                    already += 1
                elif ok:
                    applied += 1
            if applied > 0:
                total_applied += 1
                print(f"  [OK] {fix.get('note', fix['action'])} -> 影响 {applied} 个文件")
            elif already > 0:
                total_already += 1

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
                print(f"  [OK] {fix.get('note', fix['action'])} -> 影响 {applied} 个文件")

    # 3. 逐文件逐行修复（使用 cue 模型）
    if per_file_fixes:
        print("\n=== 逐行修复 ===\n")
        for fname, fpath in iter_ass_files(args.target_dir):
            if fname not in per_file_fixes:
                continue

            file_fixes = per_file_fixes[fname]
            ep_tag = _extract_ep(fname)

            if _is_srt(fpath):
                # ── SRT: load cues once, apply all fixes, save once ──
                cues = _load_srt_cues(fpath)
                applied = 0

                for fix in file_fixes:
                    action = fix['action']

                    # Record pre-fix state for report
                    pre_time = fix.get('start', '')
                    pre_text = ''
                    cue = _find_srt_cue(cues, fix)
                    if cue:
                        pre_time = cue.get('start', pre_time)
                        pre_text = cue.get('text', '')[:120]

                    if action == 'replace_text':
                        ok, msg = apply_replace_text(cues, fix)
                    elif action == 'delete_line':
                        ok, msg = apply_delete_line(cues, fix)
                    elif action == 'merge_cues':
                        ok, msg = apply_merge_cues(cues, fix)
                    elif action == 'replace_name':
                        ok, msg = False, "SRT 文件不支持 Name 字段替换"
                    else:
                        ok, msg = False, f"未知 action: {action}"

                    if ok:
                        is_already = 'already correct' in msg or 'already deleted' in msg
                        if is_already:
                            total_already += 1
                        else:
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
                    else:
                        total_skipped += 1
                        print(f"  ✗ {fname}:{fix.get('line', fix.get('start', '?'))} - {msg}")

                if applied > 0:
                    if not args.dry_run:
                        _save_srt_cues(fpath, cues)
                    total_applied += applied
                    print(f"  {fname}: {applied} applied")

            else:
                # ── ASS: use existing line-based processing ──
                lines = read_ass_file(fpath)
                file_fixes_sorted = sorted(file_fixes, key=lambda f: f.get('line', 0), reverse=True)
                applied = 0

                for fix in file_fixes_sorted:
                    action = fix['action']
                    i = fix.get('line', 0) - 1
                    pre_time = ''
                    pre_text = ''

                    if action == 'replace_text':
                        if i < 0 or i >= len(lines):
                            ok, msg = False, f"行号 {fix['line']} 超出范围"
                        else:
                            d = parse_dialogue(lines[i])
                            if d is None:
                                ok, msg = False, f"第 {fix['line']} 行不是 Dialogue 行"
                            else:
                                old = d['text']
                                pre_text = old[:120]
                                pre_time = d['start']
                                if old == fix['replacement']:
                                    ok, msg = True, f"already correct: {old[:40]}"
                                else:
                                    d['text'] = fix['replacement']
                                    lines[i] = build_dialogue_line(d) + '\n'
                                    ok, msg = True, f"{old[:40]} → {fix['replacement'][:40]}"
                    elif action == 'replace_name':
                        if i < 0 or i >= len(lines):
                            ok, msg = False, f"行号 {fix['line']} 超出范围"
                        else:
                            d = parse_dialogue(lines[i])
                            if d is None:
                                ok, msg = False, f"第 {fix['line']} 行不是 Dialogue 行"
                            else:
                                old = d['name']
                                d['name'] = fix['replacement']
                                lines[i] = build_dialogue_line(d) + '\n'
                                ok, msg = True, f"Name: {old} → {fix['replacement']}"
                    elif action == 'delete_line':
                        if i < 0 or i >= len(lines):
                            ok, msg = False, f"行号 {fix['line']} 超出范围"
                        else:
                            pre_text = lines[i].strip()[:60]
                            lines[i] = ''
                            ok, msg = True, f"删除: {pre_text}"
                    else:
                        ok, msg = False, f"ASS 不支持 action: {action}"

                    if ok:
                        is_already = 'already correct' in msg or 'already deleted' in msg
                        if is_already:
                            total_already += 1
                        else:
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
                    else:
                        total_skipped += 1
                        print(f"  ✗ {fname}:{fix.get('line', '?')} - {msg}")

                if applied > 0:
                    if not args.dry_run:
                        lines = [l for l in lines if l != '']
                        write_ass_file(fpath, lines)
                    total_applied += applied
                    print(f"  {fname}: {applied} applied")

    # ── 报告日志 ──
    if args.log_to_report and args.step and report_entries:
        from utils.update_report import upsert_entries as _upsert
        _upsert(args.log_to_report, step=args.step, entries=report_entries)
        print(f'\n[apply] {len(report_entries)} entries logged to report layer {args.step}')

    print(f"\n{'[DRY-RUN] ' if args.dry_run else ''}"
          f"{total_applied} applied, {total_already} already correct, "
          f"{total_skipped} skipped")


# ═══════════════════════════════════════════════════════════════
# 繁→简 内置转换
# ═══════════════════════════════════════════════════════════════

from lib.chinese_utils import TRAD_TO_SIMP_MAP as _TRAD_TO_SIMP_MAP


def _run_trad_to_simp(target_dir, dry_run=False):
    """内置换繁→简转换。遍历所有字幕文件，将繁体字替换为简体。"""
    total = 0
    for fname, fpath in iter_ass_files(target_dir):
        lines = read_ass_file(fpath)
        changed = 0
        for i in range(len(lines)):
            if lines[i].startswith('Dialogue:') or not lines[i].startswith('Comment:'):
                old = lines[i]
                # Only convert dialogue text, not style/metadata
                parts = lines[i].split(',', 9)
                if len(parts) >= 10:
                    new_text = parts[9].translate(_TRAD_TO_SIMP_MAP)
                    if new_text != parts[9]:
                        parts[9] = new_text
                        lines[i] = ','.join(parts)
                        changed += 1
        if changed > 0:
            total += changed
            if not dry_run:
                write_ass_file(fpath, lines)
            print(f'  {"[DRY-RUN]" if dry_run else ""} {fname}: {changed} changed', file=sys.stderr)
    print(f'[trad→simp] {"preview" if dry_run else "done"}: {total} changed', file=sys.stderr)


# ═══════════════════════════════════════════════════════════════
# EN→ZH 翻译腔去机械化（degloss）
# ═══════════════════════════════════════════════════════════════

# 格式: (pattern, replacement, note)
# pattern 用 re.sub 执行，均为安全替换（低误报）
_TRANSLATIONESE_FIXES = [
    # ── 动词冗余 ──
    (r'进行了([一-鿿]{1,4})', r'\1了', '进行了X→X了'),
    (r'给予([一-鿿]{1,4})', r'给\1', '给予→给'),
    (r'有着([一-鿿]{2,6})', r'有\1', '有着→有'),

    # ── 代词冗余（EN his/her 强制，ZH 省略）──
    (r'他的(妈妈|爸爸|哥哥|姐姐|弟弟|妹妹|朋友|老师|同学|邻居|同事|老板)',
     r'他\1', '他的X→他X'),
    (r'她的(妈妈|爸爸|哥哥|姐姐|弟弟|妹妹|朋友|老师|同学|邻居|同事|老板)',
     r'她\1', '她的X→她X'),

    # ── 介词/连词自然化 ──
    (r'对于([你我他她])来说', r'对\1来说', '对于→对'),
    (r'当([你我他她])的时候', r'\1...的时候', '当→省略'),

    # ── 口语化 ──
    (r'是否(可以|能够|需要|愿意|知道|明白|清楚|确定)',
     r'\1吗', '是否X→X吗'),
    (r'如何(办|做|说|写|处理|解决|解释|描述)',
     r'怎么\1', '如何→怎么'),

    # ── 副词简化 ──
    (r'迅速地', r'很快', '迅速地→很快'),
    (r'非常地', r'非常', '非常地→非常'),
    (r'显得格外', r'特别', '显得格外→特别'),

    # ── 进行时简体化 ──
    (r'正在([一-鿿])着', r'在\1', '正在X着→在X'),

    # ── 冗余"性"/"度" ──
    (r'的重要性', r'很重要', '的重要性→很重要'),
    (r'具有([一-鿿]{2,6})性', r'很\1', '具有X性→很X'),
]


def _run_degloss(target_dir, dry_run=False):
    """EN→ZH 翻译腔去机械化。遍历所有字幕文件，应用翻译腔修正模式。"""
    total = 0
    for fname, fpath in iter_ass_files(target_dir):
        with open(fpath, 'r', encoding='utf-8-sig' if _is_srt(fpath) else 'utf-8') as f:
            content = f.read()

        changed = 0
        for pattern, replacement, note in _TRANSLATIONESE_FIXES:
            new_content, n = re.subn(pattern, replacement, content)
            if n > 0:
                content = new_content
                changed += n

        if changed > 0:
            total += changed
            if not dry_run:
                with open(fpath, 'w', encoding='utf-8') as f:
                    f.write(content)
            print(f'  {"[DRY-RUN]" if dry_run else ""} {fname}: {changed} changed', file=sys.stderr)
    print(f'[degloss] {"preview" if dry_run else "done"}: {total} changed', file=sys.stderr)


if __name__ == '__main__':
    main()
