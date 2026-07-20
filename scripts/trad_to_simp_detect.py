#!/usr/bin/env python3
"""脚本: 繁体中文 → 简体中文 检测与转换。

扫描对话行中的繁体字（仅繁简异体，不含繁简同形字），输出 JSON 供 Claude 审查。
支持 --auto 模式直接应用内置映射表批量转换。

用法:
  python trad_to_simp_detect.py --target-dir <DIR>                  # JSON 检测输出
  python trad_to_simp_detect.py --target-dir <DIR> --auto           # 直接自动转换
  python trad_to_simp_detect.py --target-dir <DIR> --dry-run        # 预览模式
"""

import argparse
import json
import re
import sys
import os
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ass_utils import (
    strip_ass_tags, parse_dialogue, build_dialogue_line,
    read_ass_file, write_ass_file, iter_ass_files, iter_dialogue_lines
)

# ═══════════════════════════════════════════════════════════════
# 繁→简映射表（仅收录繁简异体字，不含同形字如「真」「黑」）
# ═══════════════════════════════════════════════════════════════

TRAD_TO_SIMP = {
    # 常用高频
    '著': '着', '麼': '么', '彆': '别', '麵': '面', '裡': '里', '體': '体', '萬': '万',
    '歐': '欧', '沒': '没', '過': '过', '後': '后', '臺': '台', '個': '个', '關': '关',
    '為': '为', '與': '与', '僅': '仅', '該': '该', '對': '对', '態': '态', '門': '门',
    '開': '开', '氣': '气', '幹': '干', '兒': '儿', '處': '处', '們': '们', '時': '时',
    '來': '来', '現': '现', '說': '说', '間': '间', '當': '当', '發': '发', '經': '经',
    '還': '还', '從': '从', '頭': '头', '樣': '样', '動': '动', '會': '会', '係': '系',
    # 偏旁类推
    '見': '见', '長': '长', '書': '书', '國': '国', '實': '实', '學': '学', '東': '东',
    '覺': '觉', '愛': '爱', '樂': '乐', '車': '车', '電': '电', '話': '话', '讓': '让',
    '給': '给', '帶': '带', '馬': '马', '飛': '飞', '魚': '鱼', '鳥': '鸟', '龍': '龙',
    '難': '难', '變': '变', '親': '亲', '風': '风', '場': '场', '錢': '钱', '塊': '块',
    '聲': '声', '賣': '卖', '買': '买', '錯': '错', '飯': '饭', '飽': '饱', '養': '养',
    '嗎': '吗', '寫': '写', '點': '点', '熱': '热', '漢': '汉', '禮': '礼', '機': '机',
    '視': '视', '聽': '听', '師': '师', '樹': '树', '殺': '杀', '遠': '远',
    '進': '进', '運': '运', '傳': '传', '業': '业', '義': '义', '達': '达', '號': '号',
    '畫': '画', '問': '问', '題': '题', '應': '应', '戰': '战', '戲': '戏',
    '報': '报', '結': '结', '統': '统', '舊': '旧', '節': '节',
    '衛': '卫', '護': '护', '領': '领', '顯': '显', '驚': '惊', '鬥': '斗',
    '齊': '齐', '爾': '尔', '亞': '亚', '擊': '击', '權': '权', '術': '术', '蘇': '苏',
    '蘭': '兰', '靈': '灵', '眾': '众', '優': '优', '羅': '罗', '離': '离', '際': '际',
    '葉': '叶', '裝': '装', '銀': '银', '陽': '阳', '陰': '阴', '險': '险', '隨': '随',
    '靜': '静', '頁': '页', '顧': '顾', '類': '类', '願': '愿', '館': '馆', '驗': '验',
    '髮': '发', '鬍': '胡', '鳳': '凤', '麥': '麦', '黃': '黄', '齒': '齿',
    '龜': '龟', '無': '无', '絕': '绝', '幾': '几', '歲': '岁', '腦': '脑',
    '腳': '脚', '臉': '脸', '準': '准', '媽': '妈', '輕': '轻', '輪': '轮',
    '轉': '转', '辦': '办', '農': '农', '連': '连', '歡': '欢', '鐵': '铁', '錦': '锦',
    '錄': '录', '雙': '双', '雜': '杂', '雲': '云', '順': '顺', '須': '须',
    '鬆': '松', '鬧': '闹', '鬱': '郁', '壓': '压',
    '溼': '湿', '儘': '尽', '癒': '愈', '鑑': '鉴', '鐘': '钟',
    '舉': '举', '虛': '虚', '虧': '亏', '蟲': '虫', '蠟': '蜡', '證': '证', '譯': '译',
    '讀': '读', '貝': '贝', '財': '财', '責': '责', '質': '质', '賴': '赖',
    '賽': '赛', '敗': '败', '貨': '货', '貧': '贫', '貼': '贴', '費': '费', '貿': '贸',
    '賀': '贺', '賓': '宾', '賞': '赏', '賢': '贤', '贊': '赞',
    '軍': '军', '邊': '边', '邏': '逻', '這': '这', '醫': '医',
    '釋': '释', '閉': '闭', '閱': '阅', '隊': '队',
    '陸': '陆', '標': '标', '奮': '奋', '贈': '赠', '訝': '讶', '鈴': '铃',
    # 补充：实际扫描中发现的
    '噹': '当', '繫': '系', '彆': '别', '麵': '面', '臺': '台',
    '隻': '只', '佈': '布', '佔': '占', '併': '并',
    '淚': '泪', '煙': '烟', '煉': '炼', '煩': '烦', '燙': '烫',
    '爭': '争', '狀': '状', '獲': '获', '環': '环', '產': '产',
    '盡': '尽', '監': '监', '盤': '盘', '睜': '睁', '礙': '碍',
    '祕': '秘', '穀': '谷', '窮': '穷', '籤': '签', '讚': '赞', '豔': '艳',
    '兇': '凶', '曬': '晒', '菸': '烟', '誌': '志', '餘': '余',
    '確': '确', '巖': '岩', '恆': '恒', '啟': '启', '嘆': '叹', '團': '团',
    '慾': '欲', '範': '范', '於': '于',
}


def build_trad_pattern():
    """构建繁体字匹配正则（字符类）。"""
    chars = ''.join(TRAD_TO_SIMP.keys())
    return re.compile(f'[{re.escape(chars)}]')


def detect(target_dir: str, config: dict = None) -> dict:
    """扫描目标目录，检测繁体字。

    Args:
        target_dir: 目标 ASS 字幕目录
        config: 可选配置 dict，支持:
            - dialogue_styles: 限定样式集合（默认 Default/DefaultTop/Episode）
            - skip_styles: 跳过样式集合（默认 Display/Title 1/Title 2/Opening Romaji/Opening Rus）

    Returns:
        {
            "findings": [...],
            "char_stats": {...},   # 每个繁体字的出现次数
            "affected_files": [...],  # 受影响的文件名列表
            "total_occurrences": N
        }
    """
    if config is None:
        config = {}

    dialogue_styles = set(config.get('dialogue_styles', {'Default', 'DefaultTop', 'Episode'}))
    skip_styles = set(config.get('skip_styles', {'Display', 'Title 1', 'Title 2',
                                                   'Opening Romaji', 'Opening Rus', 'Roboto'}))

    trad_re = build_trad_pattern()
    char_counter = Counter()
    findings = []
    affected_files = set()

    for fname, fpath in iter_ass_files(target_dir):
        lines = read_ass_file(fpath)
        file_has_trad = False

        for line_idx, d in iter_dialogue_lines(lines):
            style = d['style']

            # 跳过特效层
            if style in skip_styles:
                continue
            # 如果指定了对话样式，只处理这些
            if dialogue_styles and style not in dialogue_styles:
                continue

            visible = strip_ass_tags(d['text'])
            if not visible.strip():
                continue

            # 查找繁体字
            matches = trad_re.findall(visible)
            if matches:
                file_has_trad = True
                for ch in set(matches):
                    char_counter[ch] += 1

                findings.append({
                    'file': fname,
                    'line': line_idx + 1,  # 1-based
                    'timecode': d['start'],
                    'style': style,
                    'visible': visible,
                    'trad_chars': matches,
                    'suggested_fixes': {ch: TRAD_TO_SIMP[ch] for ch in set(matches)},
                })

        if file_has_trad:
            affected_files.add(fname)

    return {
        'findings': findings,
        'char_stats': dict(char_counter.most_common()),
        'affected_files': sorted(affected_files),
        'total_occurrences': sum(char_counter.values()),
    }


def auto_fix(target_dir: str, config: dict = None, dry_run: bool = False) -> dict:
    """直接应用繁→简映射，批量转换（无需 Claude 审查）。

    Args:
        target_dir: 目标目录
        config: 可选配置
        dry_run: True 时仅预览不写入

    Returns:
        {"fixed_files": N, "total_conversions": N, "dry_run": bool}
    """
    if config is None:
        config = {}

    skip_styles = set(config.get('skip_styles', {'Display', 'Title 1', 'Title 2',
                                                   'Opening Romaji', 'Opening Rus', 'Roboto'}))

    fixed_files = 0
    total_conversions = 0

    for fname, fpath in iter_ass_files(target_dir):
        lines = read_ass_file(fpath)
        file_changed = False
        file_conversions = 0

        for line_idx, d in iter_dialogue_lines(lines):
            if d['style'] in skip_styles:
                continue

            old_text = d['text']
            new_text = old_text
            for trad, simp in TRAD_TO_SIMP.items():
                if trad in new_text:
                    count = new_text.count(trad)
                    new_text = new_text.replace(trad, simp)
                    file_conversions += count

            if new_text != old_text:
                d['text'] = new_text
                lines[line_idx] = build_dialogue_line(d) + '\n'
                file_changed = True

        if file_changed:
            fixed_files += 1
            total_conversions += file_conversions
            if not dry_run:
                write_ass_file(fpath, lines)
            print(f"  {'[DRY-RUN] ' if dry_run else ''}{fname}: {file_conversions} 处转换",
                  file=sys.stderr)

    return {
        'fixed_files': fixed_files,
        'total_conversions': total_conversions,
        'dry_run': dry_run,
    }


def main():
    parser = argparse.ArgumentParser(
        description='繁体中文→简体中文 检测与转换',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 检测模式（输出 JSON 供 Claude 审查）
  python trad_to_simp_detect.py --target-dir ./target/

  # 预览自动转换
  python trad_to_simp_detect.py --target-dir ./target/ --auto --dry-run

  # 执行自动转换
  python trad_to_simp_detect.py --target-dir ./target/ --auto
        """
    )
    parser.add_argument('--target-dir', required=True, help='目标 ASS 字幕目录')
    parser.add_argument('--auto', action='store_true',
                        help='自动应用繁→简映射（跳过 Claude 审查步骤）')
    parser.add_argument('--dry-run', action='store_true',
                        help='预览模式，不实际写入（需配合 --auto）')
    parser.add_argument('--config', default=None, help='JSON 配置文件（可选）')
    args = parser.parse_args()

    config = {}
    if args.config:
        with open(args.config, 'r', encoding='utf-8') as f:
            config = json.load(f)

    if args.auto:
        result = auto_fix(args.target_dir, config, dry_run=args.dry_run)
        action = '预览' if args.dry_run else '转换'
        print(f"\n{action}完成: {result['fixed_files']} 个文件, {result['total_conversions']} 处转换",
              file=sys.stderr)
    else:
        result = detect(args.target_dir, config)
        print(f"发现 {result['total_occurrences']} 处繁体字（{len(result['char_stats'])} 种字符），"
              f"涉及 {len(result['affected_files'])} 个文件", file=sys.stderr)
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write('\n')


if __name__ == '__main__':
    main()
