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

sys.path.insert(0, _root_dir)

from lib.whisper_utils import setup_windows_utf8, extract_ep_number

from lib.ass_utils import (
    parse_dialogue, build_dialogue_line,
    read_ass_file, write_ass_file, iter_ass_files
)

# 尝试导入 srt_utils
try:
    from lib import srt_utils
except ImportError:
    import lib.srt_utils as srt_utils


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
    parser.add_argument('--step', type=int, default=0, help='报告步骤编号（1-16），配合 --log-to-report 使用')
    args = parser.parse_args()

    # Step 0: 繁→简（--lang zh 自动启用）
    if args.lang == 'zh' or args.trad_to_simp:
        print('[trad→simp] 繁→简转换 ...')
        _run_trad_to_simp(args.target_dir, dry_run=args.dry_run)
        print('[degloss] 翻译腔去机械化 ...')
        _run_degloss(args.target_dir, dry_run=args.dry_run)

    # Load fixes from JSON or review checklist
    if args.review:
        print(f'[review] 解析审查清单: {args.review}')
        fixes = _parse_review_checklist(args.review)
        if not fixes:
            print('[review] 未找到修复条目。')
            return
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
        from workflow.update_report import upsert_entries as _upsert
        _upsert(args.log_to_report, step=args.step, entries=report_entries)
        print(f'\n📋 已记录 {len(report_entries)} 条到问题解决报告步骤{args.step}')

    print(f"\n{'[DRY-RUN] ' if args.dry_run else ''}共应用 {total_applied} 项修复，跳过 {total_skipped} 项")


# ═══════════════════════════════════════════════════════════════
# 繁→简 内置转换
# ═══════════════════════════════════════════════════════════════

# 繁→简映射表（合并自 trad_to_simp_detect.py）
_TRAD_TO_SIMP_MAP = str.maketrans({
    '體':'体','萬':'万','歐':'欧','沒':'没','過':'过','後':'后','臺':'台',
    '個':'个','關':'关','為':'为','與':'与','僅':'仅','該':'该','對':'对',
    '態':'态','門':'门','開':'开','氣':'气','幹':'干','兒':'儿','處':'处',
    '們':'们','時':'时','來':'来','現':'现','說':'说','間':'间','當':'当',
    '發':'发','經':'经','還':'还','從':'从','頭':'头','樣':'样','動':'动',
    '會':'会','係':'系','見':'见','長':'长','書':'书','國':'国','實':'实',
    '學':'学','東':'东','覺':'觉','愛':'爱','樂':'乐','車':'车','電':'电',
    '話':'话','讓':'让','給':'给','帶':'带','馬':'马','飛':'飞','魚':'鱼',
    '鳥':'鸟','龍':'龙','難':'难','變':'变','親':'亲','風':'风','場':'场',
    '錢':'钱','塊':'块','聲':'声','賣':'卖','買':'买','錯':'错','飯':'饭',
    '飽':'饱','養':'养','嗎':'吗','寫':'写','點':'点','熱':'热','漢':'汉',
    '禮':'礼','機':'机','視':'视','聽':'听','師':'师','樹':'树','殺':'杀',
    '遠':'远','進':'进','運':'运','傳':'传','業':'业','義':'义','達':'达',
    '號':'号','畫':'画','問':'问','題':'题','應':'应','戰':'战','戲':'戏',
    '報':'报','結':'结','統':'统','舊':'旧','節':'节','衛':'卫','護':'护',
    '領':'领','顯':'显','驚':'惊','鬥':'斗','齊':'齐','爾':'尔','亞':'亚',
    '擊':'击','權':'权','術':'术','蘇':'苏','蘭':'兰','靈':'灵','眾':'众',
    '優':'优','羅':'罗','離':'离','際':'际','葉':'叶','裝':'装','銀':'银',
    '陽':'阳','陰':'阴','險':'险','隨':'随','靜':'静','頁':'页','顧':'顾',
    '類':'类','願':'愿','館':'馆','驗':'验','髮':'发','鳳':'凤','麥':'麦',
    '黃':'黄','齒':'齿','龜':'龟','無':'无','絕':'绝','幾':'几','歲':'岁',
    '腦':'脑','腳':'脚','臉':'脸','準':'准','媽':'妈','輕':'轻','輪':'轮',
    '轉':'转','辦':'办','農':'农','連':'连','歡':'欢','鐵':'铁','錦':'锦',
    '錄':'录','雙':'双','雜':'杂','雲':'云','順':'顺','須':'须','鬆':'松',
    '鬧':'闹','鬱':'郁','壓':'压','舉':'举','虛':'虚','虧':'亏','蟲':'虫',
    '證':'证','譯':'译','讀':'读','貝':'贝','財':'财','責':'责','質':'质',
    '賴':'赖','賽':'赛','敗':'败','貨':'货','貼':'贴','費':'费','賀':'贺',
    '賓':'宾','賞':'赏','賢':'贤','贊':'赞','軍':'军','邊':'边','這':'这',
    '醫':'医','釋':'释','閉':'闭','閱':'阅','隊':'队','陸':'陆','標':'标',
    '奮':'奋','贈':'赠','訝':'讶','鈴':'铃','隻':'只','佈':'布','佔':'占',
    '併':'并','淚':'泪','煙':'烟','煉':'炼','煩':'烦','爭':'争','狀':'状',
    '獲':'获','環':'环','產':'产','盡':'尽','監':'监','盤':'盘','睜':'睁',
    '礙':'碍','穀':'谷','窮':'穷','讚':'赞','兇':'凶','曬':'晒','誌':'志',
    '餘':'余','確':'确','恆':'恒','啟':'启','嘆':'叹','團':'团','範':'范',
    '於':'于','著':'着','麼':'么','彆':'别','麵':'面','裡':'里',
    '噹':'当','繫':'系','溼':'湿','儘':'尽','癒':'愈','鑑':'鉴','鐘':'钟',
    '蠟':'蜡','鬱':'郁','巖':'岩','祕':'秘','籤':'签','豔':'艳','曬':'晒',
    '菸':'烟','慾':'欲',
})


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
            print(f'  {"[DRY-RUN]" if dry_run else ""} {fname}: {changed} 处', file=sys.stderr)
    print(f'[trad→simp] {"预览" if dry_run else "转换"}完成: {total} 处', file=sys.stderr)


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
            print(f'  {"[DRY-RUN]" if dry_run else ""} {fname}: {changed} 处', file=sys.stderr)
    print(f'[degloss] {"预览" if dry_run else "修正"}完成: {total} 处', file=sys.stderr)


# ═══════════════════════════════════════════════════════════════
# 审查清单解析（合并自 apply_review_fixes.py）
# ═══════════════════════════════════════════════════════════════

def _parse_review_checklist(path):
    """解析人工审查清单 markdown，生成 fixes 列表。

    清单格式:
      EP019 | 00:08:10.569 ~ 00:08:42.810 | EP019_00-08-10_to_00-08-42.mp4
      残留: 原始错误文本
      修正:
      修正后文本第一行
      修正后文本第二行
    """
    fixes = []
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    header_pattern = re.compile(
        r'^(EP)?(\d+)\s*\|\s*([\d:,.-]+)\s*~\s*([\d:,.-]+)\s*\|\s*(.+\.mp4)')
    raw_lines = content.split('\n')

    for i, line in enumerate(raw_lines):
        m = header_pattern.match(line.strip())
        if not m:
            continue
        ep = f'EP{m.group(2)}'
        start = m.group(3).replace('.', ',').replace('-', ':')
        end = m.group(4).replace('.', ',').replace('-', ':')

        # Collect correction lines
        corrected_lines = []
        in_correction = False
        j = i + 1
        while j < len(raw_lines):
            s = raw_lines[j].strip()
            if s.startswith('修正:'):
                in_correction = True
                # Check if correction is on same line
                text = s[2:].strip()
                if text:
                    corrected_lines.append(text)
            elif in_correction:
                if s.startswith('残留:') or header_pattern.match(s) or s == '---':
                    break
                if s:
                    corrected_lines.append(s)
                else:
                    break
            j += 1

        if corrected_lines:
            replacement = '\n'.join(corrected_lines)
            fixes.append({
                'action': 'replace_text',
                'file': f'{ep}.srt',
                'start': start, 'end': end,
                'replacement': replacement,
                'note': f'清单修正 ({len(corrected_lines)} 行)',
            })

    return fixes


if __name__ == '__main__':
    main()
