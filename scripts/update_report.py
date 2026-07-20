#!/usr/bin/env python3
"""统一问题解决报告读写工具。

供所有脚本复用的核心模块。
报告格式: reports/问题解决报告.md — 按 16 个处理步骤分类的修复记录。

用法:
  from update_report import read_report, write_report, upsert_entries, update_entry_status

  # 读取
  data = read_report('reports/问题解决报告.md')

  # 批量更新步骤条目（按 集数+时间 去重）
  entries = [
      {'ep': 'EP002', 'time': '00:02:00.490', 'original': 'me', 'corrected': '', 'status': '⬜'},
  ]
  upsert_entries('reports/问题解决报告.md', step=15, entries=entries)

  # 单条状态更新
  update_entry_status('reports/问题解决报告.md', step=16, ep='EP002',
                      time='00:02:00.490', corrected='行くぞ', status='✅')
"""

import os
import re
import datetime
from collections import OrderedDict

# ═══════════════════════════════════════════════════════════════
# 步骤定义（16步，与 SKILL.md 工作流一致）
# ═══════════════════════════════════════════════════════════════

STEP_NAMES = OrderedDict([
    (1,  '卡死重复清理'),
    (2,  '繁体→简体'),
    (3,  '双语混合清理'),
    (4,  '纯源语言行处理'),
    (5,  '多语言字符残留'),
    (6,  '感叹词残留'),
    (7,  'Name字段异常'),
    (8,  'Comment行残留'),
    (9,  '样式异常清理'),
    (10, '绘图指令误译'),
    (11, '固定格式变体'),
    (12, '机翻幻觉检测'),
    (13, 'OP/ED异常'),
    (14, '专有名词变体'),
    (15, 'Whisper乱码修复'),
    (16, '人工审查修正'),
])

STATUS_MAP = {
    '✅': '已修复',
    '⬜': '待处理',
    '🗑️': '已删除',
}

# 步骤与项目特征的关联（用于动态筛选）
# 每个步骤的适用条件：None=始终适用，dict=需要这些特征才适用
_STEP_REQUIRES = {
    2:  {'target_lang': 'zh'},          # 繁体→简体：中文目标语言
    3:  {'is_translation': True},        # 双语混合：翻译项目
    4:  {'is_translation': True},        # 纯源语言行：翻译项目
    6:  {'is_translation': True},        # 感叹词残留：翻译项目
    7:  {'format': 'ass'},              # Name字段：ASS only
    8:  {'format': 'ass'},              # Comment行：ASS only
    9:  {'format': 'ass'},              # 样式异常：ASS only
    10: {'format': 'ass'},              # 绘图指令：ASS only
    12: {'target_lang': 'zh'},          # 机翻幻觉：中文目标语言
    13: {'format': 'ass'},              # OP/ED异常：ASS + 多样式
    14: {'has_reference': True},         # 专有名词：有参考字幕
}


def get_relevant_steps(target_lang='ja', fmt='srt', has_reference=False,
                       is_translation=False):
    """根据项目特征返回适用的步骤列表。

    默认值对应本项目（日语原文 + SRT only + 无参考字幕）→ 仅 5-6 个步骤。

    Args:
        target_lang: 目标语言代码 ('ja'=日语, 'zh'=中文, ...)
        fmt: 字幕格式 ('srt' 或 'ass')
        has_reference: 是否有参考字幕
        is_translation: 是否为翻译项目（非原文转录）

    Returns:
        OrderedDict: {step_num: step_name} 仅包含适用步骤
    """
    from collections import OrderedDict as _OD

    features = {
        'target_lang': target_lang,
        'format': fmt,
        'has_reference': has_reference,
        'is_translation': is_translation,
    }

    relevant = _OD()
    for num, name in STEP_NAMES.items():
        req = _STEP_REQUIRES.get(num)
        if req is None:
            relevant[num] = name
            continue
        # 检查所有条件是否满足
        match = all(features.get(k) == v for k, v in req.items())
        if match:
            relevant[num] = name

    return relevant

# ═══════════════════════════════════════════════════════════════
# 报告头模板
# ═══════════════════════════════════════════════════════════════

REPORT_HEADER = """# 问题解决报告
> 最后更新: {date}
> 总览: {fixed}条已解决 / {pending}条待处理 / {deleted}条已删除
"""

# ═══════════════════════════════════════════════════════════════
# 解析
# ═══════════════════════════════════════════════════════════════

def _parse_step_header(line):
    """解析 '## 步骤N: 名称' 返回 (N, name) 或 None。"""
    m = re.match(r'^##\s*步骤(\d+):\s*(.+)', line)
    if m:
        return int(m.group(1)), m.group(2).strip()
    return None


def _parse_table_row(line):
    """解析表格行 '| EP002 | 00:02:00.490 | me | 修正文本 | ✅ |'
    返回 dict 或 None。"""
    if not line.startswith('|'):
        return None
    # 跳过分隔行
    if re.match(r'^\|[\s\-:|]+\|$', line):
        return None
    # 跳过表头行
    if '原错误字幕' in line or '集数' in line:
        return None

    cells = [c.strip() for c in line.split('|')]
    # 表格行格式: '' 'EP002' '00:02:00.490' 'me' '修正' '✅' ''
    # cells[0] 和 cells[-1] 是空字符串（行首行尾的 |）
    valid = [c for c in cells if c]
    if len(valid) < 4:
        return None

    entry = {'ep': valid[0], 'time': valid[1], 'original': valid[2]}
    entry['corrected'] = valid[3] if len(valid) > 3 else ''
    entry['status'] = valid[4] if len(valid) > 4 else '⬜'
    return entry


def read_report(path):
    """读取统一报告，返回 {step_number: [entries]}。
    如果文件不存在，返回空 dict。
    """
    if not os.path.exists(path):
        return {}

    with open(path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    data = OrderedDict()
    current_step = None

    for line in lines:
        line = line.rstrip('\n')
        # 检测步骤标题
        step_match = _parse_step_header(line)
        if step_match:
            current_step = step_match[0]
            if current_step not in data:
                data[current_step] = []
            continue

        # 在步骤内解析表格行
        if current_step is not None:
            entry = _parse_table_row(line)
            if entry:
                data[current_step].append(entry)

    return data


# ═══════════════════════════════════════════════════════════════
# 写入
# ═══════════════════════════════════════════════════════════════

def _build_step_section(step_num, entries):
    """构建单个步骤的 markdown 段落。"""
    name = STEP_NAMES.get(step_num, f'步骤{step_num}')
    lines = [f'\n## 步骤{step_num}: {name}\n']

    if not entries:
        lines.append('*（暂无记录）*\n')
        return ''.join(lines)

    lines.append('| 集数 | 时间 | 原错误字幕 | 整改后字幕 | 状态 |\n')
    lines.append('|------|------|-----------|-----------|:---:|\n')
    for e in entries:
        ep = e.get('ep', '')
        time = e.get('time', '')
        orig = e.get('original', '').replace('|', '/').replace('\n', ' ')
        corr = e.get('corrected', '').replace('|', '/').replace('\n', ' ')
        status = e.get('status', '⬜')
        lines.append(f'| {ep} | {time} | {orig} | {corr} | {status} |\n')

    return ''.join(lines)


def _count_summary(data):
    """统计各状态条目数。"""
    fixed = pending = deleted = 0
    for entries in data.values():
        for e in entries:
            s = e.get('status', '⬜')
            if s == '✅':
                fixed += 1
            elif s == '🗑️':
                deleted += 1
            else:
                pending += 1
    return fixed, pending, deleted


def write_report(path, data):
    """将结构化数据写回 markdown 文件。"""
    fixed, pending, deleted = _count_summary(data)
    today = datetime.date.today().isoformat()

    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else '.', exist_ok=True)

    with open(path, 'w', encoding='utf-8') as f:
        f.write(REPORT_HEADER.format(date=today, fixed=fixed, pending=pending, deleted=deleted))

        # 按步骤顺序输出
        for step_num in STEP_NAMES:
            entries = data.get(step_num, [])
            f.write(_build_step_section(step_num, entries))

    return path


# ═══════════════════════════════════════════════════════════════
# 更新操作
# ═══════════════════════════════════════════════════════════════

def _entry_key(entry):
    """条目的去重键：(集数, 时间)。"""
    return (entry.get('ep', ''), entry.get('time', ''))


def upsert_entries(path, step, entries):
    """批量插入/更新某步骤的条目。按 (集数, 时间) 去重，后到的覆盖先到的。

    Args:
        path: 报告文件路径
        step: 步骤编号 (1-16)
        entries: [{'ep': 'EP002', 'time': '00:02:00', 'original': 'me',
                    'corrected': '', 'status': '⬜'}, ...]
    """
    data = read_report(path)
    if step not in data:
        data[step] = []

    # 构建现有条目索引
    existing = {_entry_key(e): i for i, e in enumerate(data[step])}

    for entry in entries:
        key = _entry_key(entry)
        if key in existing:
            # 覆盖更新
            data[step][existing[key]] = entry
        else:
            data[step].append(entry)
            existing[key] = len(data[step]) - 1

    write_report(path, data)


def update_entry_status(path, step, ep, time, corrected=None, status=None):
    """更新单条目的整改文本和/或状态。

    Args:
        path: 报告文件路径
        step: 步骤编号
        ep: 集数标识，如 'EP002'
        time: 时间码，如 '00:02:00.490'
        corrected: 整改后字幕（None=不修改）
        status: 状态标记（None=不修改）

    Returns:
        True 如果找到并更新了条目，False 如果未找到。
    """
    data = read_report(path)
    if step not in data:
        return False

    for entry in data[step]:
        if entry.get('ep') == ep and entry.get('time') == time:
            if corrected is not None:
                entry['corrected'] = corrected
            if status is not None:
                entry['status'] = status
            write_report(path, data)
            return True

    return False


def get_step_summary(data):
    """返回每个步骤的统计: {step: {'fixed': N, 'pending': N, 'deleted': N, 'total': N}}。"""
    summary = {}
    for step_num in STEP_NAMES:
        entries = data.get(step_num, [])
        f = sum(1 for e in entries if e.get('status') == '✅')
        p = sum(1 for e in entries if e.get('status') == '⬜')
        d = sum(1 for e in entries if e.get('status') == '🗑️')
        summary[step_num] = {'fixed': f, 'pending': p, 'deleted': d, 'total': len(entries)}
    return summary


# ═══════════════════════════════════════════════════════════════
# CLI（用于手动检查/调试）
# ═══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print('用法: python update_report.py <报告路径> [--summary] [选项]')
        print('      python update_report.py <报告路径> --init')
        print()
        print('--summary 选项（按项目特征过滤步骤）:')
        print('  --target-lang ja|zh     目标语言 (default: ja)')
        print('  --format srt|ass        字幕格式 (default: srt)')
        print('  --has-reference         有参考字幕')
        print('  --is-translation        翻译项目（非原文转录）')
        sys.exit(1)

    path = sys.argv[1]

    if '--init' in sys.argv:
        write_report(path, {})
        print(f'已初始化空报告: {path}')
    elif '--summary' in sys.argv:
        # 解析项目特征参数
        target_lang = 'ja'
        fmt = 'srt'
        has_reference = False
        is_translation = False
        for i, arg in enumerate(sys.argv):
            if arg == '--target-lang' and i + 1 < len(sys.argv):
                target_lang = sys.argv[i + 1]
            elif arg == '--format' and i + 1 < len(sys.argv):
                fmt = sys.argv[i + 1]
            elif arg == '--has-reference':
                has_reference = True
            elif arg == '--is-translation':
                is_translation = True

        data = read_report(path)
        relevant_steps = get_relevant_steps(
            target_lang=target_lang, fmt=fmt,
            has_reference=has_reference, is_translation=is_translation
        )
        summary = get_step_summary(data)
        total_f = total_p = total_d = total_all = 0
        for step_num, s in summary.items():
            if step_num not in relevant_steps:
                continue  # 跳过不适用步骤
            name = relevant_steps[step_num]
            marker = '' if s['total'] > 0 else ' (空)'
            print(f'步骤{step_num} {name}: {s["fixed"]}✅ {s["pending"]}⬜ {s["deleted"]}🗑️ (共{s["total"]}条){marker}')
            total_f += s['fixed']; total_p += s['pending']; total_d += s['deleted']; total_all += s['total']
        print(f'\n总计（仅适用步骤）: {total_f}✅ {total_p}⬜ {total_d}🗑️ (共{total_all}条)')
        print(f'已过滤: {len(STEP_NAMES) - len(relevant_steps)} 个不适用步骤')
    else:
        data = read_report(path)
        print(f'步骤数: {len(data)}')
        for step, entries in data.items():
            print(f'  步骤{step}: {len(entries)}条')
