#!/usr/bin/env python3
"""统一问题解决报告读写工具。

供所有脚本复用的核心模块。
报告格式: reports/问题解决报告.md — 按 5 层工作流分类的修复记录。
层号与 SKILL.md 的 5+1 层流水线一致。

用法:
  from utils.update_report import read_report, write_report, upsert_entries, update_entry_status

  # 读取
  data = read_report('reports/问题解决报告.md')

  # 批量更新层条目（按 集数+时间 去重）
  entries = [
      {'ep': 'EP002', 'time': '00:02:00.490', 'original': 'me', 'corrected': '', 'status': '⬜'},
  ]
  upsert_entries('reports/问题解决报告.md', step='2', entries=entries)

  # 单条状态更新
  update_entry_status('reports/问题解决报告.md', step='6', ep='EP002',
                      time='00:02:00.490', corrected='行くぞ', status='✅')
"""

import os
import re
import datetime
from collections import OrderedDict

# ═══════════════════════════════════════════════════════════════
# 层定义（5层工作流 + AI审查 + 人工交付，与 SKILL.md 一致）
# ═══════════════════════════════════════════════════════════════

LAYER_NAMES = OrderedDict([
    ('1',   '字符扫描'),
    ('2',   '错误修复'),
    ('2.5', 'AI短碎片补全'),
    ('3',   '专名统一'),
    ('3.5', 'AI专名审查'),
    ('4',   '批量修复'),
    ('5',   '格式修补 [ASS only]'),
    ('6',   '人工审查'),
])

# 旧步骤名 → 新层号映射（用于迁移旧报告数据）
STEP_TO_LAYER = {
    1:  '1',     # 卡死重复清理 → 字符扫描
    2:  '4',     # 繁体→简体 → 批量修复
    3:  '1',     # 双语混合清理 → 字符扫描
    4:  '1',     # 纯源语言行处理 → 字符扫描
    5:  '1',     # 多语言字符残留 → 字符扫描
    6:  '4',     # 感叹词残留 → 批量修复
    7:  '5',     # Name字段异常 → 格式修补
    8:  '5',     # Comment行残留 → 格式修补
    9:  '5',     # 样式异常清理 → 格式修补
    10: '5',     # 绘图指令误译 → 格式修补
    11: '4',     # 固定格式变体 → 批量修复
    12: '2',     # 机翻幻觉检测 → 语义修复
    13: '5',     # OP/ED异常 → 格式修补
    14: '3',     # 专有名词变体 → 专名统一
    15: '2',     # Whisper乱码修复 → 语义修复
    16: '6',     # 人工审查修正 → 人工交付
}

STATUS_MAP = {
    '✅': '已修复',
    '⬜': '待处理',
    '🗑️': '已删除',
}

# 层与项目特征的关联（用于动态筛选）
# None=始终适用，dict=需要这些特征才适用
_LAYER_REQUIRES = {
    '3.5': {'has_ai_review': True},      # AI审查：noun_checker unknown>0 时触发
    '5':   {'format': 'ass'},            # 格式修补：ASS only
}


def get_relevant_layers(target_lang='ja', fmt='srt', has_reference=False,
                        is_translation=False, has_ai_review=False):
    """根据项目特征返回适用的层列表。

    默认值对应本项目（日语原文 + SRT only + 无参考字幕）→ 5-6 层。

    Args:
        target_lang: 目标语言代码 ('ja'=日语, 'zh'=中文, ...)
        fmt: 字幕格式 ('srt' 或 'ass')
        has_reference: 是否有参考字幕
        is_translation: 是否为翻译项目（非原文转录）
        has_ai_review: 是否触发了 AI 专名审查

    Returns:
        OrderedDict: {layer_id: layer_name} 仅包含适用层
    """
    from collections import OrderedDict as _OD

    features = {
        'target_lang': target_lang,
        'format': fmt,
        'has_reference': has_reference,
        'is_translation': is_translation,
        'has_ai_review': has_ai_review,
    }

    relevant = _OD()
    for lid, name in LAYER_NAMES.items():
        req = _LAYER_REQUIRES.get(lid)
        if req is None:
            relevant[lid] = name
            continue
        match = all(features.get(k) == v for k, v in req.items())
        if match:
            relevant[lid] = name

    return relevant

# ═══════════════════════════════════════════════════════════════
# 报告头模板
# ═══════════════════════════════════════════════════════════════

REPORT_HEADER = """# 问题解决报告
> 最后更新: {date}
> 总览: {fixed}条已解决 / {pending}条待处理 / {deleted}条已删除
>
> 格式: 5层工作流（+ AI审查 + 人工交付）
"""

# ═══════════════════════════════════════════════════════════════
# 解析
# ═══════════════════════════════════════════════════════════════

def _parse_layer_header(line):
    """解析 '## 第N层: 名称' 或旧格式 '## 步骤N: 名称'，返回 (layer_id, name) 或 None。"""
    # 新格式: 第N层 或 第N.M层
    m = re.match(r'^##\s*第([\d.]+)层:\s*(.+)', line)
    if m:
        return m.group(1), m.group(2).strip()
    # 兼容旧格式: 步骤N
    m = re.match(r'^##\s*步骤(\d+):\s*(.+)', line)
    if m:
        old_step = int(m.group(1))
        layer_id = STEP_TO_LAYER.get(old_step, str(old_step))
        return layer_id, m.group(2).strip()
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
    """读取统一报告，返回 {layer_id: [entries]}。
    layer_id 为字符串 ('1', '2', '3', '3.5', '4', '5', '6')。
    兼容旧格式（步骤N → 自动映射到新层号）。
    如果文件不存在，返回空 dict。
    """
    if not os.path.exists(path):
        return {}

    with open(path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    data = OrderedDict()
    current_layer = None

    for line in lines:
        line = line.rstrip('\n')
        # 检测层标题
        layer_match = _parse_layer_header(line)
        if layer_match:
            current_layer = layer_match[0]
            if current_layer not in data:
                data[current_layer] = []
            continue

        # 在层内解析表格行
        if current_layer is not None:
            entry = _parse_table_row(line)
            if entry:
                data[current_layer].append(entry)

    return data


# ═══════════════════════════════════════════════════════════════
# 写入
# ═══════════════════════════════════════════════════════════════

def _build_layer_section(layer_id, entries):
    """构建单个层的 markdown 段落。"""
    name = LAYER_NAMES.get(layer_id, f'第{layer_id}层')
    header_num = f'第{layer_id}层'
    lines = [f'\n## {header_num}: {name}\n']

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

        # 按层顺序输出
        for layer_id in LAYER_NAMES:
            entries = data.get(layer_id, [])
            f.write(_build_layer_section(layer_id, entries))

    return path


# ═══════════════════════════════════════════════════════════════
# 更新操作
# ═══════════════════════════════════════════════════════════════

def _entry_key(entry):
    """条目的去重键：(集数, 时间)。"""
    return (entry.get('ep', ''), entry.get('time', ''))


def upsert_entries(path, step, entries):
    """批量插入/更新某层的条目。按 (集数, 时间) 去重，后到的覆盖先到的。

    Args:
        path: 报告文件路径
        step: 层号字符串 ('1', '2', '3', '3.5', '4', '5', '6')
        entries: [{'ep': 'EP002', 'time': '00:02:00', 'original': 'me',
                    'corrected': '', 'status': '⬜'}, ...]
    """
    data = read_report(path)
    step = str(step)  # normalize to string
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
        step: 层号字符串 ('1'-'6')
        ep: 集数标识，如 'EP002'
        time: 时间码，如 '00:02:00.490'
        corrected: 整改后字幕（None=不修改）
        status: 状态标记（None=不修改）

    Returns:
        True 如果找到并更新了条目，False 如果未找到。
    """
    data = read_report(path)
    step = str(step)
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


def get_layer_summary(data):
    """返回每个层的统计: {layer_id: {'fixed': N, 'pending': N, 'deleted': N, 'total': N}}。"""
    summary = {}
    for layer_id in LAYER_NAMES:
        entries = data.get(layer_id, [])
        f = sum(1 for e in entries if e.get('status') == '✅')
        p = sum(1 for e in entries if e.get('status') == '⬜')
        d = sum(1 for e in entries if e.get('status') == '🗑️')
        summary[layer_id] = {'fixed': f, 'pending': p, 'deleted': d, 'total': len(entries)}
    return summary


# ═══════════════════════════════════════════════════════════════
# CLI（用于手动检查/调试）
# ═══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import sys
    # Ensure UTF-8 output on Windows (fixes GBK emoji encoding errors)
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass

    if len(sys.argv) < 2:
        print('用法: python update_report.py <报告路径> [--summary] [选项]')
        print('      python update_report.py <报告路径> --init')
        print()
        print('--summary 选项（按项目特征过滤层）:')
        print('  --target-lang ja|zh     目标语言 (default: ja)')
        print('  --format srt|ass        字幕格式 (default: srt)')
        print('  --has-reference         有参考字幕')
        print('  --is-translation        翻译项目（非原文转录）')
        print('  --has-ai-review         启用AI专名审查 (层3.5)')
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
        has_ai_review = False
        for i, arg in enumerate(sys.argv):
            if arg == '--target-lang' and i + 1 < len(sys.argv):
                target_lang = sys.argv[i + 1]
            elif arg == '--format' and i + 1 < len(sys.argv):
                fmt = sys.argv[i + 1]
            elif arg == '--has-reference':
                has_reference = True
            elif arg == '--is-translation':
                is_translation = True
            elif arg == '--has-ai-review':
                has_ai_review = True

        data = read_report(path)
        relevant_layers = get_relevant_layers(
            target_lang=target_lang, fmt=fmt,
            has_reference=has_reference, is_translation=is_translation,
            has_ai_review=has_ai_review
        )
        summary = get_layer_summary(data)
        total_f = total_p = total_d = total_all = 0
        for layer_id, s in summary.items():
            if layer_id not in relevant_layers:
                continue  # 跳过不适用层
            name = relevant_layers[layer_id]
            marker = '' if s['total'] > 0 else ' (空)'
            print(f'第{layer_id}层 {name}: {s["fixed"]}✅ {s["pending"]}⬜ {s["deleted"]}🗑️ (共{s["total"]}条){marker}')
            total_f += s['fixed']; total_p += s['pending']; total_d += s['deleted']; total_all += s['total']
        print(f'\n总计（仅适用层）: {total_f}✅ {total_p}⬜ {total_d}🗑️ (共{total_all}条)')
        print(f'已过滤: {len(LAYER_NAMES) - len(relevant_layers)} 个不适用层')
    else:
        data = read_report(path)
        print(f'层数: {len(data)}')
        for layer_id, entries in data.items():
            name = LAYER_NAMES.get(layer_id, f'第{layer_id}层')
            print(f'  第{layer_id}层 {name}: {len(entries)}条')
