#!/usr/bin/env python3
"""统一修复生成器 — 从 unified_scanner 输出生成 fixes.json。

合并两处重复的删除管线：
  - generate_romaji_fixes.py 的词典替换 + AI 噪声删除
  - whisper_transcribe.py 的 whisper_delete_candidates 生成

用法:
  # 从 unified_scanner 输出生成 fixes.json
  python romaji_fixer.py --findings findings.json --output fixes.json

  # 仅词典修复
  python romaji_fixer.py --findings findings.json --mode dict-only --output dict_fixes.json

  # 仅删除候选
  python romaji_fixer.py --findings findings.json --mode delete-only --output delete_fixes.json
"""

import argparse
import json
import re
import sys
import os

_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

from whisper_utils import setup_windows_utf8


# ═══════════════════════════════════════════════════════════════
# Romaji → Kana 词典（从 generate_romaji_fixes.py 迁移）
# ═══════════════════════════════════════════════════════════════

# 完整 Hepburn 罗马字 → 平假名表（~250 条目，按长度降序以便最长匹配）
ROMAJI_TO_KANA = {
    # ═══ 常用词汇/短语（优先最长匹配） ═══
    'sayounara': 'さようなら', 'konnichiwa': 'こんにちは',
    'arigatou': 'ありがとう', 'sumimasen': 'すみません',
    'ohayou': 'おはよう', 'konbanwa': 'こんばんは',
    'itadakimasu': 'いただきます', 'gochisousama': 'ごちそうさま',
    'otsukaresama': 'おつかれさま', 'yoroshiku': 'よろしく',
    'onegai': 'おねがい', 'daijoubu': 'だいじょうぶ',
    'ganbatte': 'がんばって', 'omedetou': 'おめでとう',
    'tadaima': 'ただいま', 'okaeri': 'おかえり',
    'itterasshai': 'いってらっしゃい', 'irasshai': 'いらっしゃい',
    'sayonara': 'さよなら', 'hontou': 'ほんとう',
    'sugoku': 'すごく', 'totemo': 'とても',
    'chotto': 'ちょっと', 'motto': 'もっと',
    'zutto': 'ずっと', 'kitto': 'きっと',
    'yappari': 'やっぱり', 'soshite': 'そして',
    'dakara': 'だから', 'keredo': 'けれど',
    'shikashi': 'しかし', 'tsumari': 'つまり',
    'tabun': 'たぶん', 'kanarazu': 'かならず',
    'hayaku': 'はやく', 'sukoshi': 'すこし',
    'sugu': 'すぐ', 'mou': 'もう',
    # 指示词
    'kore': 'これ', 'sore': 'それ', 'are': 'あれ',
    'koko': 'ここ', 'soko': 'そこ', 'asoko': 'あそこ',
    'kochira': 'こちら', 'sochira': 'そちら', 'achira': 'あちら',
    'konna': 'こんな', 'sonna': 'そんな', 'anna': 'あんな',
    'dore': 'どれ', 'doko': 'どこ', 'dochira': 'どちら',
    'donna': 'どんな', 'doushite': 'どうして', 'douzo': 'どうぞ',
    # 人称/事物
    'watashi': 'わたし', 'boku': 'ぼく', 'ore': 'おれ',
    'anata': 'あなた', 'kimi': 'きみ', 'omae': 'おまえ',
    'kare': 'かれ', 'kanojo': 'かのじょ',
    'minna': 'みんな', 'jibun': 'じぶん',
    'sensei': 'せんせい', 'tomodachi': 'ともだち',
    'otousan': 'おとうさん', 'okaasan': 'おかあさん',
    'oniisan': 'おにいさん', 'oneesan': 'おねえさん',
    'ojisan': 'おじさん', 'obasan': 'おばさん',
    'nani': 'なに', 'naze': 'なぜ', 'itsu': 'いつ',
    'dare': 'だれ',
    # 常用动词/形容词
    'suru': 'する', 'kuru': 'くる', 'aru': 'ある',
    'iru': 'いる', 'iku': 'いく', 'miru': 'みる',
    'kiku': 'きく', 'hanasu': 'はなす', 'taberu': 'たべる',
    'nomu': 'のむ', 'neru': 'ねる', 'okiru': 'おきる',
    'omou': 'おもう', 'wakaru': 'わかる', 'dekiru': 'できる',
    'tsukau': 'つかう', 'tsukuru': 'つくる',
    'motsu': 'もつ', 'toru': 'とる', 'ageru': 'あげる',
    'kureru': 'くれる', 'morau': 'もらう',
    'yoi': 'よい', 'warui': 'わるい',
    'ookii': 'おおきい', 'chiisai': 'ちいさい',
    'hayai': 'はやい', 'osoi': 'おそい',
    'atsui': 'あつい', 'samui': 'さむい',
    'takai': 'たかい', 'yasui': 'やすい',
    'tooi': 'とおい', 'chikai': 'ちかい',
    'atarashii': 'あたらしい', 'furui': 'ふるい',
    'tanoshii': 'たのしい', 'kanashii': 'かなしい',
    'ureshii': 'うれしい', 'sabishii': 'さびしい',
    'muzukashii': 'むずかしい', 'yasashii': 'やさしい',
    # 助词/副词
    'made': 'まで', 'kara': 'から', 'dake': 'だけ',
    'demo': 'でも', 'shika': 'しか', 'hodo': 'ほど',
    'nado': 'など', 'yori': 'より', 'koso': 'こそ',
    'sae': 'さえ', 'nagara': 'ながら',
    'mada': 'まだ', 'mata': 'また', 'sudeni': 'すでに',
    'ato': 'あと', 'mae': 'まえ', 'ushiro': 'うしろ',
    'naka': 'なか', 'soto': 'そと', 'ue': 'うえ',
    'shita': 'した', 'migi': 'みぎ', 'hidari': 'ひだり',
    'tonari': 'となり', 'chikaku': 'ちかく',
    'hoka': 'ほか', 'toki': 'とき', 'mono': 'もの',
    'koto': 'こと', 'tokoro': 'ところ',
    'tame': 'ため', 'hazu': 'はず', 'wake': 'わけ',
    'dame': 'だめ', 'dai': 'だい',

    # ═══ 拗音 (Yōon) — 清音 ═══
    'kya': 'きゃ', 'kyu': 'きゅ', 'kyo': 'きょ',
    'sha': 'しゃ', 'shu': 'しゅ', 'sho': 'しょ',
    'cha': 'ちゃ', 'chu': 'ちゅ', 'cho': 'ちょ',
    'nya': 'にゃ', 'nyu': 'にゅ', 'nyo': 'にょ',
    'hya': 'ひゃ', 'hyu': 'ひゅ', 'hyo': 'ひょ',
    'mya': 'みゃ', 'myu': 'みゅ', 'myo': 'みょ',
    'rya': 'りゃ', 'ryu': 'りゅ', 'ryo': 'りょ',
    # ═══ 拗音 — 浊音 ═══
    'gya': 'ぎゃ', 'gyu': 'ぎゅ', 'gyo': 'ぎょ',
    'ja': 'じゃ', 'ju': 'じゅ', 'jo': 'じょ',
    'bya': 'びゃ', 'byu': 'びゅ', 'byo': 'びょ',
    # ═══ 拗音 — 半浊音 ═══
    'pya': 'ぴゃ', 'pyu': 'ぴゅ', 'pyo': 'ぴょ',

    # ═══ 基本五十音（放在最后：长度短的优先被长键覆盖） ═══
    # 清音
    'a': 'あ', 'i': 'い', 'u': 'う', 'e': 'え', 'o': 'お',
    'ka': 'か', 'ki': 'き', 'ku': 'く', 'ke': 'け', 'ko': 'こ',
    'sa': 'さ', 'shi': 'し', 'su': 'す', 'se': 'せ', 'so': 'そ',
    'ta': 'た', 'chi': 'ち', 'tsu': 'つ', 'te': 'て', 'to': 'と',
    'na': 'な', 'ni': 'に', 'nu': 'ぬ', 'ne': 'ね', 'no': 'の',
    'ha': 'は', 'hi': 'ひ', 'fu': 'ふ', 'he': 'へ', 'ho': 'ほ',
    'ma': 'ま', 'mi': 'み', 'mu': 'む', 'me': 'め', 'mo': 'も',
    'ya': 'や', 'yu': 'ゆ', 'yo': 'よ',
    'ra': 'ら', 'ri': 'り', 'ru': 'る', 're': 'れ', 'ro': 'ろ',
    'wa': 'わ', 'wo': 'を', 'n': 'ん',
    # 浊音
    'ga': 'が', 'gi': 'ぎ', 'gu': 'ぐ', 'ge': 'げ', 'go': 'ご',
    'za': 'ざ', 'ji': 'じ', 'zu': 'ず', 'ze': 'ぜ', 'zo': 'ぞ',
    'da': 'だ', 'di': 'ぢ', 'du': 'づ', 'de': 'で', 'do': 'ど',
    'ba': 'ば', 'bi': 'び', 'bu': 'ぶ', 'be': 'べ', 'bo': 'ぼ',
    # 半浊音
    'pa': 'ぱ', 'pi': 'ぴ', 'pu': 'ぷ', 'pe': 'ぺ', 'po': 'ぽ',
    # 双元音/长音
    'ou': 'おう', 'ei': 'えい', 'aa': 'ああ',
    'ii': 'いい', 'uu': 'うう', 'ee': 'ええ', 'oo': 'おお',
}

ROMAJI_CORRECTIONS = {
    # 阿童木专有名词
    'atom': 'アトム', 'atomu': 'アトム',
    'wan': 'ワン',
    # 常见片假名词汇（外来语）
    'robot': 'ロボット', 'roboto': 'ロボット',
    'enerugi': 'エネルギー', 'enerugii': 'エネルギー',
    'scien': 'サイエン', 'kagaku': '科学',
    'uchuu': '宇宙', 'chikyuu': '地球',
    'ningen': '人間', 'ningenzo': '人間像',
}

ENGLISH_OK = {
    'ok', 'okay', 'yeah', 'hey', 'oh', 'wow', 'bye', 'hi',
    'yes', 'no', 'go', 'stop', 'hello', 'good',
}

AI_NOISE = {
    'wh', 'th', 'dj', 'w',
}


def is_romaji_only(text: str) -> bool:
    """Check if text contains only Latin letters and spaces."""
    return bool(re.fullmatch(r'[a-zA-Z\s\-\']+', text.strip()))


# ═══════════════════════════════════════════════════════════════
# 修复生成
# ═══════════════════════════════════════════════════════════════

def generate_dict_fixes(findings_by_type, project_dict=None):
    """从 pure_romaji 和 mixed_romaji 发现中生成词典修复。

    Args:
        findings_by_type: unified_scanner 输出的 findings dict (keyed by type)
        project_dict: 项目词典 dict（含 auto 和 pending），可选

    Returns:
        list of fix dicts (兼容 apply_fixes.py)
    """
    fixes = []
    seen_global = set()
    seen_per_file = set()

    # ── 合并词典：内置 + 项目词典 ──
    merged_kana = dict(ROMAJI_TO_KANA)
    merged_corrections = dict(ROMAJI_CORRECTIONS)

    if project_dict:
        # auto 条目：已确认的替换
        for word, info in project_dict.get('auto', {}).items():
            if isinstance(info, dict) and 'replacement' in info:
                merged_corrections[word] = info['replacement']
            elif isinstance(info, str):
                merged_corrections[word] = info

        # pending 条目中已填写的
        for word, info in project_dict.get('pending', {}).items():
            repl = info.get('replacement', '') if isinstance(info, dict) else ''
            if repl:
                merged_corrections[word] = repl

    # ── pure_romaji: 整行罗马字 → 词典修复或标记待 Whisper ──
    pure_items = findings_by_type.get('pure_romaji', [])
    for item in pure_items:
        text = item['text'].strip()
        lower = text.lower()

        # 跳过太长的（大概率不是词典能覆盖的）
        if len(lower) > 30:
            continue

        # 已知英语（OK）
        if lower in ENGLISH_OK:
            continue

        # 单字符（太危险，不全局替换）
        if len(lower) <= 1:
            continue

        # 词典匹配 → 逐行 replace_text
        if lower in merged_kana:
            kana = merged_kana[lower]
            key = (item['file'], item['line'], 'dict_kana')
            if key not in seen_per_file:
                seen_per_file.add(key)
                fixes.append({
                    'action': 'replace_text',
                    'file': item['file'],
                    'line': item['line'],
                    'replacement': kana,
                    'note': f'Romaji→Kana: "{text}"→"{kana}"',
                })
            continue

        if lower in merged_corrections:
            katakana = merged_corrections[lower]
            key = (item['file'], item['line'], 'dict_katakana')
            if key not in seen_per_file:
                seen_per_file.add(key)
                fixes.append({
                    'action': 'replace_text',
                    'file': item['file'],
                    'line': item['line'],
                    'replacement': katakana,
                    'note': f'Romaji→カタカナ: "{text}"→"{katakana}"',
                })
            continue

    # ── mixed_romaji: 罗马字+日语混合 → 尝试提取并替换罗马字部分 ──
    mixed_items = findings_by_type.get('mixed_romaji', [])
    for item in mixed_items:
        text = item['text'].strip()
        # 提取拉丁单词
        romaji_words = re.findall(r'[a-zA-Z]{2,}', text)
        if not romaji_words:
            continue

        new_text = text
        changed = False
        for word in romaji_words:
            lower = word.lower()
            if lower in ENGLISH_OK:
                continue
            if lower in merged_kana:
                new_text = new_text.replace(word, merged_kana[lower])
                changed = True
            elif lower in merged_corrections:
                new_text = new_text.replace(word, merged_corrections[lower])
                changed = True

        if changed and new_text != text:
            key = (item['file'], item['line'])
            if key not in seen_per_file:
                seen_per_file.add(key)
                fixes.append({
                    'action': 'replace_text',
                    'file': item['file'],
                    'line': item['line'],
                    'replacement': new_text,
                    'note': f'Romaji修正: "{text[:40]}"→"{new_text[:40]}"',
                })

    return fixes


def generate_delete_fixes(findings_by_type, delete_candidates=None):
    """生成删除修复。

    来源：
      1. unified_scanner 的 delete_candidates 列表（ai_noise + hallucination）
      2. pure_romaji 中 ≤2 词的极短片段（whisper 也无法修复）

    Args:
        findings_by_type: unified_scanner 输出的 findings dict
        delete_candidates: unified_scanner 的 delete_candidates 列表（可选）

    Returns:
        list of fix dicts
    """
    fixes = []
    seen = set()

    # ── 来源 1: unified_scanner 自动标记的删除候选 ──
    if delete_candidates:
        for dc in delete_candidates:
            key = (dc['file'], dc['line'], 'delete')
            if key not in seen:
                seen.add(key)
                fixes.append(dc)

    # ── 来源 2: pure_romaji 中极短片段（未被 delete_candidates 覆盖的） ──
    for item in findings_by_type.get('pure_romaji', []):
        text = item['text'].strip()
        words = text.split()
        if len(words) <= 2 and not item.get('has_kana') and not item.get('has_kanji'):
            key = (item['file'], item['line'], 'delete_short')
            if key not in seen:
                seen.add(key)
                fixes.append({
                    'action': 'delete_line',
                    'file': item['file'],
                    'line': item['line'],
                    'note': f'噪声: 短罗马字碎片 — "{text[:40]}"',
                })

    return fixes


def generate_fixes(findings_data, mode='all', project_dict=None):
    """从 unified_scanner 输出生成完整 fixes.json。

    Args:
        findings_data: unified_scanner.py 输出的完整 dict 或 findings JSON 文件路径
        mode: 'dict-only' | 'delete-only' | 'all'
        project_dict: 项目词典 dict（含 auto 和 pending），可选

    Returns:
        list of fix dicts (兼容 apply_fixes.py)
    """
    # 支持直接传 dict 或文件路径
    if isinstance(findings_data, str):
        with open(findings_data, 'r', encoding='utf-8') as f:
            findings_data = json.load(f)

    # 兼容顶层 key
    findings_by_type = findings_data.get('findings', findings_data)
    delete_candidates = findings_data.get('delete_candidates', None)

    fixes = []

    if mode in ('dict-only', 'all'):
        dict_fixes = generate_dict_fixes(findings_by_type, project_dict=project_dict)
        fixes.extend(dict_fixes)
        print(f'  词典修复: {len(dict_fixes)} 条', file=sys.stderr)

    if mode in ('delete-only', 'all'):
        delete_fixes = generate_delete_fixes(findings_by_type, delete_candidates)
        fixes.extend(delete_fixes)
        print(f'  删除候选: {len(delete_fixes)} 条', file=sys.stderr)

    return fixes


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def main():
    setup_windows_utf8()
    parser = argparse.ArgumentParser(
        description='统一修复生成器 — 从 unified_scanner 输出生成 fixes.json',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 生成全部修复
  python romaji_fixer.py --findings findings.json --output fixes.json

  # 仅词典修复（安全，不删任何行）
  python romaji_fixer.py --findings findings.json --mode dict-only --output dict_fixes.json

  # 仅删除候选
  python romaji_fixer.py --findings findings.json --mode delete-only --output delete_fixes.json
        """
    )
    parser.add_argument('--findings', required=True,
                        help='unified_scanner.py 输出的 findings JSON')
    parser.add_argument('--output', required=True,
                        help='输出的 fixes.json 路径')
    parser.add_argument('--mode', choices=['dict-only', 'delete-only', 'all'],
                        default='all',
                        help='修复模式 (default: all)')
    parser.add_argument('--project-dict',
                        help='项目词典 JSON（build_project_dict.py 输出），'
                             'auto 条目和已填写 pending 条目会合并到内置词典')
    args = parser.parse_args()

    if not os.path.exists(args.findings):
        print(f'错误: 文件不存在: {args.findings}', file=sys.stderr)
        sys.exit(1)

    # 加载项目词典
    project_dict = None
    if args.project_dict:
        if os.path.exists(args.project_dict):
            with open(args.project_dict, 'r', encoding='utf-8') as f:
                project_dict = json.load(f)
            auto_count = len(project_dict.get('auto', {}))
            pending_filled = sum(
                1 for v in project_dict.get('pending', {}).values()
                if isinstance(v, dict) and v.get('replacement', '').strip()
            )
            print(f'项目词典: {args.project_dict} '
                  f'(auto={auto_count}, pending已填写={pending_filled})', file=sys.stderr)
        else:
            print(f'警告: 项目词典不存在: {args.project_dict}', file=sys.stderr)

    print(f'输入: {args.findings} | 模式: {args.mode}', file=sys.stderr)
    fixes = generate_fixes(args.findings, mode=args.mode, project_dict=project_dict)

    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(fixes, f, ensure_ascii=False, indent=2)

    total = len(fixes)
    dict_count = sum(1 for f in fixes if 'Romaji' in f.get('note', ''))
    del_count = sum(1 for f in fixes if f['action'] == 'delete_line')
    print(f'\n→ {args.output}: {total} 条修复 '
          f'(词典: {dict_count}, 删除: {del_count})', file=sys.stderr)


if __name__ == '__main__':
    main()
