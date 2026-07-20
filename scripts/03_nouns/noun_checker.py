#!/usr/bin/env python3
"""Proper noun consistency checker — language-aware (ja/zh).

Scans SRT files against a known proper noun table (reports/proper-nouns.md),
detecting common ASR/Whisper errors with proper names.

Japanese (--lang ja):
  - Same reading, wrong kanji (天馬→店舗)
  - Katakana long vowel missing/extra (アトム→アトーム)
  - Small kana errors (キャ→キヤ)
  - Voiced/unvoiced consonant confusion (ガデム→カテム)

Chinese (--lang zh):
  - Homophone confusion (王→黄, 张→章)
  - Simplified/traditional variants (万→萬)
  - Pinyin tone errors
  - Character-splitting/merging errors

Usage:
  python noun_checker.py AI审查后/EP064.srt --lang ja \
    --noun-table reports/proper-nouns.md \
    --output temp/reviews/EP064_nouns.json

  python noun_checker.py AI审查后/ --lang zh \
    --noun-table reports/proper-nouns.md \
    --output temp/reviews/
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict, Counter
from difflib import SequenceMatcher


# ═══════════════════════════════════════════════════════════════
# 1. Language-aware normalizers
# ═══════════════════════════════════════════════════════════════

class JapaneseNormalizer:
    """Katakana normalization for fuzzy matching.

    Levels:
      'strict'  — long vowel collapse (カー→カ)
      'medium'  — + small kana → full size (ャ→ヤ)
      'lenient' — + voiced → unvoiced (ガ→カ)
    """

    _LONG_VOWEL_PAIRS = [
        ('アー','ア'),('イー','イ'),('ウー','ウ'),('エー','エ'),('オー','オ'),
        ('カー','カ'),('キー','キ'),('クー','ク'),('ケー','ケ'),('コー','コ'),
        ('サー','サ'),('シー','シ'),('スー','ス'),('セー','セ'),('ソー','ソ'),
        ('ター','タ'),('チー','チ'),('ツー','ツ'),('テー','テ'),('トー','ト'),
        ('ナー','ナ'),('ニー','ニ'),('ヌー','ヌ'),('ネー','ネ'),('ノー','ノ'),
        ('ハー','ハ'),('ヒー','ヒ'),('フー','フ'),('ヘー','ヘ'),('ホー','ホ'),
        ('マー','マ'),('ミー','ミ'),('ムー','ム'),('メー','メ'),('モー','モ'),
        ('ラー','ラ'),('リー','リ'),('ルー','ル'),('レー','レ'),('ロー','ロ'),
        ('ガー','ガ'),('ギー','ギ'),('グー','グ'),('ゲー','ゲ'),('ゴー','ゴ'),
        ('ザー','ザ'),('ジー','ジ'),('ズー','ズ'),('ゼー','ゼ'),('ゾー','ゾ'),
        ('ダー','ダ'),('バー','バ'),('ビー','ビ'),('ブー','ブ'),('ベー','ベ'),
        ('ボー','ボ'),('パー','パ'),('ピー','ピ'),('プー','プ'),('ペー','ペ'),
        ('ポー','ポ'),
    ]

    _SMALL_KANA = str.maketrans({
        'ァ':'ア','ィ':'イ','ゥ':'ウ','ェ':'エ','ォ':'オ',
        'ャ':'ヤ','ュ':'ユ','ョ':'ヨ','ッ':'ツ','ヵ':'カ','ヶ':'ケ',
    })

    _VOICED_TO_CLEAR = str.maketrans({
        'ガ':'カ','ギ':'キ','グ':'ク','ゲ':'ケ','ゴ':'コ',
        'ザ':'サ','ジ':'シ','ズ':'ス','ゼ':'セ','ゾ':'ソ',
        'ダ':'タ','ヂ':'チ','ヅ':'ツ','デ':'テ','ド':'ト',
        'バ':'ハ','ビ':'ヒ','ブ':'フ','ベ':'ヘ','ボ':'ホ',
        'パ':'ハ','ピ':'ヒ','プ':'フ','ペ':'ヘ','ポ':'ホ',
    })

    @classmethod
    def normalize(cls, text, level='strict'):
        result = text
        for pair in cls._LONG_VOWEL_PAIRS:
            result = result.replace(pair[0], pair[1])
        if level in ('medium', 'lenient'):
            result = result.translate(cls._SMALL_KANA)
        if level == 'lenient':
            result = result.translate(cls._VOICED_TO_CLEAR)
        return result


class ChineseNormalizer:
    """Chinese text normalization for fuzzy matching.

    Levels:
      'strict'  — simplified/traditional mapping
      'medium'  — + pinyin tone folding
      'lenient' — + erhua removal, common homophone groups
    """

    # Traditional → Simplified (same mapping as trad_to_simp_detect.py)
    _TRAD_TO_SIMP = str.maketrans({
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
        '於':'于',
    })

    # Pinyin tone marks → bare vowels
    _PINYIN_TONES = str.maketrans({
        'ā':'a','á':'a','ǎ':'a','à':'a',
        'ē':'e','é':'e','ě':'e','è':'e',
        'ī':'i','í':'i','ǐ':'i','ì':'i',
        'ō':'o','ó':'o','ǒ':'o','ò':'o',
        'ū':'u','ú':'u','ǔ':'u','ù':'u',
        'ǖ':'v','ǘ':'v','ǚ':'v','ǜ':'v',
    })

    @classmethod
    def normalize(cls, text, level='strict'):
        result = text
        if level in ('strict', 'medium', 'lenient'):
            result = result.translate(cls._TRAD_TO_SIMP)
        if level in ('medium', 'lenient'):
            result = result.translate(cls._PINYIN_TONES)
        if level == 'lenient':
            # Erhua removal
            result = re.sub(r'儿\b', '', result)
        return result


def get_normalizer(lang):
    """Factory: return the normalizer class for a language."""
    return {'ja': JapaneseNormalizer, 'zh': ChineseNormalizer}.get(lang, JapaneseNormalizer)


# ═══════════════════════════════════════════════════════════════
# 2. Language-aware candidate extractors
# ═══════════════════════════════════════════════════════════════

# ── Japanese patterns ──

_JA_COMMON_WORDS = frozenset({
    'ドア','テーブル','パック','バック','テスト','メモ','データ',
    'タイプ','レベル','モデル','システム','プログラム','サービス',
    'ケース','グループ','チーム','クラス','ルール','コード',
    'イメージ','デザイン','コピー','チェック','リスト','ファイル',
    'メッセージ','レポート','サポート','プロジェクト','マシン',
    'ライン','ポイント','ボタン','スイッチ','パネル','ケーブル',
    'エネルギー','スピード','バランス','コントロール','センター',
    'エリア','ゾーン','スペース','ホール','ルーム','ハウス',
    'カード','キー','ロック','ベル','サイン','マーク',
    'パパ','ママ','ボーイ','ガール','ベビー',
})

_JA_NAME_KANJI_SUFFIX = re.compile(
    r'([一-鿿]{1,4})'
    r'(博士|警部|殿下|先生|総統|団長|伯爵|署長|所長|船長|部長|社長)'
)
_JA_NAME_KATAKANA_SUFFIX = re.compile(
    r'([゠-ヿ]{2,6})'
    r'(さん|くん|ちゃん|様|殿)'
)
_JA_CALLING_PATTERN = re.compile(
    r'(?:おい[、\s]*|なあ[、\s]*|ねえ[、\s]*|もしもし[、\s]*)'
    r'([゠-ヿ]{2,6})'
    r'(?:[!！〜～\s]|$)'
)
_JA_INTRO_PATTERN = re.compile(
    r'([一-鿿぀-ゟ゠-ヿ]{2,8})'
    r'(?:って|という|と言う|と呼ぶ|って言う|といいます|って呼ばれ)'
)

# ── Chinese patterns ──

_ZH_COMMON_WORDS = frozenset({
    '我们','你们','他们','她们','这个','那个','什么','怎么','为什么',
    '可以','可能','应该','因为','所以','但是','虽然','如果','然后',
    '已经','没有','还是','或者','而且','不过','只是','一定','非常',
    '今天','明天','昨天','现在','以后','以前','已经','正在','一直',
})

_ZH_NAME_SUFFIX = re.compile(
    r'([一-鿿]{1,4})'
    r'(先生|小姐|女士|老师|同学|医生|大夫|队长|警长|校长|部长|经理|'
    r'总[监裁]?|董[事长]?|局长|处长|科长|主任|教授|师傅)'
)
_ZH_CALLING_PATTERN = re.compile(
    r'(?:喂[、\s,!！]*|诶[、\s,!！]*|嘿[、\s,!！]*)'
    r'([一-鿿]{1,4})'
    r'(?:[!！啊呀啦呢哦～~\s]|$)'
)
_ZH_INTRO_PATTERN = re.compile(
    r'(?:叫|名叫|叫作|叫做|就是|那就是|这位是)'
    r'([一-鿿A-Za-z]{1,8})'
)


class JapaneseExtractor:
    """Extract proper noun candidates from Japanese subtitle cues."""

    @staticmethod
    def extract(cue_text, known_names_set=None):
        if known_names_set is None:
            known_names_set = frozenset()
        candidates = []

        # Pattern 1a: Kanji name + formal suffix
        for m in _JA_NAME_KANJI_SUFFIX.finditer(cue_text):
            name, suffix = m.group(1), m.group(2)
            start = m.start()
            if start > 0 and 'ぁ' <= cue_text[start - 1] <= 'ゟ':
                continue
            candidates.append({
                'text': name + suffix, 'name_part': name,
                'start': start, 'type': 'name_kanji_suffix',
                'context': f'{name}＋{suffix} (敬称)',
            })

        # Pattern 1b: Katakana name + casual suffix
        for m in _JA_NAME_KATAKANA_SUFFIX.finditer(cue_text):
            name, suffix = m.group(1), m.group(2)
            if name not in _JA_COMMON_WORDS:
                candidates.append({
                    'text': name + suffix, 'name_part': name,
                    'start': m.start(), 'type': 'name_katakana_suffix',
                    'context': f'{name}＋{suffix} (敬称)',
                })

        # Pattern 2: Calling/interjection
        for m in _JA_CALLING_PATTERN.finditer(cue_text):
            name = m.group(1)
            if name not in _JA_COMMON_WORDS and len(name) >= 2:
                candidates.append({
                    'text': name, 'name_part': name,
                    'start': m.start(1), 'type': 'calling',
                    'context': f'呼びかけ: ...{cue_text[max(0,m.start()-3):m.end()+3]}...',
                })

        # Pattern 3: Introduction
        for m in _JA_INTRO_PATTERN.finditer(cue_text):
            name = m.group(1)
            if name not in _JA_COMMON_WORDS:
                candidates.append({
                    'text': name, 'name_part': name,
                    'start': m.start(1), 'type': 'introduction',
                    'context': f'紹介: ...{cue_text[max(0,m.start()-2):m.end()+2]}...',
                })

        # Pattern 4: Known katakana names
        if known_names_set:
            for m in re.finditer(r'[゠-ヿ]{2,}', cue_text):
                word = m.group()
                if word in known_names_set:
                    already_covered = any(
                        abs(c['start'] - m.start()) < len(word)
                        for c in candidates
                    )
                    if not already_covered:
                        candidates.append({
                            'text': word, 'name_part': word,
                            'start': m.start(), 'type': 'known_katakana',
                            'context': f'既知名詞: {word}',
                        })

        return candidates


class ChineseExtractor:
    """Extract proper noun candidates from Chinese subtitle cues."""

    @staticmethod
    def extract(cue_text, known_names_set=None):
        if known_names_set is None:
            known_names_set = frozenset()
        candidates = []

        # Pattern 1: Name + honorific/title suffix
        for m in _ZH_NAME_SUFFIX.finditer(cue_text):
            name, suffix = m.group(1), m.group(2)
            if name in _ZH_COMMON_WORDS:
                continue
            candidates.append({
                'text': name + suffix, 'name_part': name,
                'start': m.start(), 'type': 'name_suffix',
                'context': f'{name}＋{suffix} (称谓)',
            })

        # Pattern 2: Calling pattern (喂、〇〇！/ 〇〇啊！)
        for m in _ZH_CALLING_PATTERN.finditer(cue_text):
            name = m.group(1)
            if name not in _ZH_COMMON_WORDS and len(name) >= 1:
                candidates.append({
                    'text': name, 'name_part': name,
                    'start': m.start(1), 'type': 'calling',
                    'context': f'呼称: ...{cue_text[max(0,m.start()-2):m.end()+2]}...',
                })

        # Pattern 3: Introduction (叫〇〇 / 名叫〇〇)
        for m in _ZH_INTRO_PATTERN.finditer(cue_text):
            name = m.group(1)
            if name not in _ZH_COMMON_WORDS:
                candidates.append({
                    'text': name, 'name_part': name,
                    'start': m.start(1), 'type': 'introduction',
                    'context': f'介绍: ...{cue_text[max(0,m.start()-2):m.end()+2]}...',
                })

        # Pattern 4: 「」quoted names (Chinese also uses corner brackets)
        for m in re.finditer(r'[「「]([一-鿿]{1,6})[」」]', cue_text):
            name = m.group(1)
            already_covered = any(abs(c['start'] - m.start()) < 6 for c in candidates)
            if not already_covered:
                candidates.append({
                    'text': name, 'name_part': name,
                    'start': m.start(1), 'type': 'quoted',
                    'context': f'引用: 「{name}」',
                })

        # Pattern 5: Known Chinese names matching noun table
        if known_names_set:
            for m in re.finditer(r'[一-鿿]{2,4}', cue_text):
                word = m.group()
                if word in known_names_set:
                    already_covered = any(
                        abs(c['start'] - m.start()) < len(word)
                        for c in candidates
                    )
                    if not already_covered:
                        candidates.append({
                            'text': word, 'name_part': word,
                            'start': m.start(), 'type': 'known_hanzi',
                            'context': f'已知名词: {word}',
                        })

        return candidates


def get_extractor(lang):
    """Factory: return the extractor class for a language."""
    return {'ja': JapaneseExtractor, 'zh': ChineseExtractor}.get(lang, JapaneseExtractor)


# ═══════════════════════════════════════════════════════════════
# 3. Noun table parser
# ═══════════════════════════════════════════════════════════════

def parse_noun_table(path, lang='ja'):
    """Parse proper-nouns.md into structured list.

    Returns list of {name, reading, category, aliases, name_norm}.
    """
    nouns = []
    if not os.path.exists(path):
        print(f'WARNING: {path} not found.', file=sys.stderr)
        return nouns

    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Parse markdown tables: | 日语/中文 | 假名/读法 | 说明 |
    table_pattern = re.compile(
        r'^\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|',
        re.MULTILINE
    )

    # Language-specific header skip words
    skip_headers = {'日语', '中文', '---', '仮名', '假名', '拼音', '读法'}

    normalizer = get_normalizer(lang)

    for m in table_pattern.finditer(content):
        name = m.group(1).strip()
        reading = m.group(2).strip()
        desc = m.group(3).strip()

        if name in skip_headers:
            continue

        # Clean markdown formatting
        name = re.sub(r'\*\*|\[|\]|\([^)]*\)', '', name).strip()
        reading = re.sub(r'\*\*|\[|\]|\([^)]*\)', '', reading).strip()

        if name and len(name) >= 2:
            nouns.append({
                'name': name,
                'reading': reading,
                'description': desc,
                'name_norm': normalizer.normalize(name, 'lenient'),
            })

    return nouns


# ═══════════════════════════════════════════════════════════════
# 4. Matching engine (language-agnostic)
# ═══════════════════════════════════════════════════════════════

def text_similarity(a, b):
    """SequenceMatcher ratio of two strings."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def match_candidates(candidates, known_nouns, cue_text, normalizer, threshold=0.7):
    """Match candidates against known noun table.

    Returns list of {candidate, match_status, matched_noun, suggestion, similarity}.
    """
    results = []

    for cand in candidates:
        cand_text = cand['text']
        cand_norm = normalizer.normalize(cand_text, 'lenient')

        best_match = None
        best_score = 0
        best_level = 'none'

        for noun in known_nouns:
            name = noun['name']
            name_norm = noun.get('name_norm', normalizer.normalize(name, 'lenient'))

            # Exact match
            if cand_text == name:
                best_match = noun
                best_score = 1.0
                best_level = 'exact'
                break

            # Normalized match
            if cand_norm == name_norm:
                if best_score < 0.95:
                    best_match = noun
                    best_score = 0.95
                    best_level = 'normalized'
                continue

            # Similarity match
            sim = text_similarity(cand_norm, name_norm)
            if sim > best_score and sim >= threshold:
                best_match = noun
                best_score = sim
                best_level = 'fuzzy'

        status = 'match'
        suggestion = None

        if best_level == 'exact':
            status = 'exact'
        elif best_level == 'normalized':
            status = 'variant'
            suggestion = best_match['name'] if best_match else None
        elif best_level == 'fuzzy':
            status = 'mismatch'
            suggestion = best_match['name'] if best_match else None
        else:
            status = 'unknown'

        results.append({
            'candidate': cand_text,
            'position': cand.get('start', 0),
            'type': cand['type'],
            'status': status,
            'matched': best_match['name'] if best_match else None,
            'suggestion': suggestion,
            'similarity': round(best_score, 3),
            'context': cue_text[max(0, cand.get('start', 0) - 5):cand.get('start', 0) + len(cand_text) + 10],
        })

    return results


# ═══════════════════════════════════════════════════════════════
# 5. Main check function
# ═══════════════════════════════════════════════════════════════

def check_srt(srt_path, noun_table_path, lang='ja'):
    """Check one SRT file against the noun table."""
    # Parse SRT
    cues = []
    with open(srt_path, 'r', encoding='utf-8-sig') as f:
        content = f.read()

    cue_pattern = re.compile(
        r'(\d+)\s*\n'
        r'(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*\n'
        r'((?:.+\n?)+?)(?=\n\d+\n|\n*\Z)',
        re.MULTILINE
    )

    for m in cue_pattern.finditer(content):
        cues.append({
            'index': int(m.group(1)),
            'start': m.group(2),
            'end': m.group(3),
            'text': m.group(4).strip(),
        })

    # Parse noun table
    known_nouns = parse_noun_table(noun_table_path, lang=lang)
    if not known_nouns:
        return {'error': 'No known nouns found in table'}

    # Build known names set for quick lookup
    known_names_set = frozenset(n['name'] for n in known_nouns)

    # Get language-specific components
    extractor = get_extractor(lang)
    normalizer = get_normalizer(lang)

    # Check each cue
    all_results = []
    stats = defaultdict(int)

    for cue in cues:
        candidates = extractor.extract(cue['text'], known_names_set)
        if not candidates:
            continue

        matches = match_candidates(candidates, known_nouns, cue['text'], normalizer, threshold=0.7)

        for r in matches:
            r['cue_start'] = cue['start']
            r['cue_end'] = cue['end']
            r['cue_text'] = cue['text']
            stats[r['status']] += 1
            all_results.append(r)

    return {
        'srt_file': srt_path,
        'lang': lang,
        'known_nouns_count': len(known_nouns),
        'cues_scanned': len(cues),
        'findings': len(all_results),
        'stats': dict(stats),
        'results': sorted(all_results, key=lambda r: (r['status'], r['cue_start'])),
    }


# ═══════════════════════════════════════════════════════════════
# 6. Output formatting
# ═══════════════════════════════════════════════════════════════

def print_summary(report):
    """Print human-readable summary."""
    stats = report.get('stats', {})
    lang = report.get('lang', 'ja')
    lang_name = {'ja': '日语', 'zh': '中文'}.get(lang, lang)

    print(f'\n=== Noun Check ({lang_name}): {os.path.basename(report["srt_file"])} ===')
    print(f'  Known nouns: {report["known_nouns_count"]}')
    print(f'  Cues scanned: {report["cues_scanned"]}')
    print(f'  Candidates found: {report["findings"]}')
    print()
    for status, count in sorted(stats.items()):
        label = {
            'exact': '✅ Exact match',
            'variant': '☑️  Variant (normalized, ok)',
            'mismatch': '❌ MISMATCH — needs fix',
            'unknown': '⬜ Unknown — review',
        }.get(status, status)
        print(f'  {label}: {count}')

    # Show mismatches
    mismatches = [r for r in report['results'] if r['status'] == 'mismatch']
    unknown = [r for r in report['results'] if r['status'] == 'unknown']
    needs_attention = mismatches + unknown

    if needs_attention:
        print(f'\n--- {len(needs_attention)} items need attention ---')
        for r in needs_attention[:20]:
            tag = '[FIX]' if r['status'] == 'mismatch' else '[REVIEW]'
            sugg = f' → {r["suggestion"]}' if r['suggestion'] else ''
            print(f'  {tag} {r["cue_start"]} | {r["candidate"]}{sugg}')
            print(f'         context: ...{r["context"]}...')
        if len(needs_attention) > 20:
            print(f'  ... and {len(needs_attention) - 20} more')


# ═══════════════════════════════════════════════════════════════
# 7. OP/ED 跨集一致性检查
# ═══════════════════════════════════════════════════════════════

def _is_valid_text(text, lang):
    """Check if text looks like real language content, not noise/scat.

    For ja: must have kana or kanji, not all-latin, min 2 chars
    For zh: must have CJK, not all-latin, min 2 chars
    """
    text = text.strip()
    if len(text) < 2:
        return False
    has_cjk = bool(re.search(r'[一-鿿]', text))
    has_kana = bool(re.search(r'[぀-ヿ]', text))
    has_latin = bool(re.search(r'[a-zA-Z]', text))
    latin_ratio = sum(1 for c in text if c.isascii() and c.isalpha()) / max(len(text), 1)

    if lang == 'ja':
        # Must have kana or kanji, not be mostly latin
        if not (has_kana or has_cjk):
            return False
        if latin_ratio > 0.5:
            return False
    else:  # zh
        if not has_cjk:
            return False
        if latin_ratio > 0.5:
            return False

    return True


def check_oped_consistency(target_dir, op_boundary=95, ed_boundary=120, lang='ja'):
    """跨集 OP/ED 文本一致性检查。

    收集所有剧集的 OP/ED 区间 cue，按时码分桶分组，
    发现变体，选最高频为规范形式，生成 fixes。

    Returns: {fixes: [...], summary: {...}}
    """
    all_episodes = {}  # {filename: {op_cues: [...], ed_cues: [...]}}

    # Collect all OP/ED cues
    for fname in sorted(os.listdir(target_dir)):
        if not fname.endswith('.srt'):
            continue
        fpath = os.path.join(target_dir, fname)
        content = ''
        with open(fpath, 'r', encoding='utf-8-sig') as f:
            content = f.read()

        cue_pattern = re.compile(
            r'(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*\n((?:.+\n?)+?)(?=\n\d+\n|\n*\Z)',
            re.MULTILINE
        )

        cues = []
        for m in cue_pattern.finditer(content):
            start = m.group(1)
            end = m.group(2)
            text = m.group(3).strip().replace('\n', ' ')
            # Convert to seconds
            parts_s = start.replace(',', '.').split(':')
            parts_e = end.replace(',', '.').split(':')
            start_s = int(parts_s[0])*3600 + int(parts_s[1])*60 + float(parts_s[2])
            end_s = int(parts_e[0])*3600 + int(parts_e[1])*60 + float(parts_e[2])
            cues.append({'start': start, 'end': end, 'text': text,
                         'start_s': start_s, 'end_s': end_s})

        if not cues:
            continue

        max_end_s = max(c['end_s'] for c in cues)
        op_cues = [c for c in cues if c['start_s'] < op_boundary]
        ed_cues = [c for c in cues if c['start_s'] > max_end_s - ed_boundary]

        all_episodes[fname] = {'op_cues': op_cues, 'ed_cues': ed_cues}

    if len(all_episodes) < 2:
        return {'fixes': [], 'summary': {'error': 'Need at least 2 episodes for cross-episode comparison'}}

    normalizer = get_normalizer(lang)
    fixes = []
    stats = {'op_groups': 0, 'ed_groups': 0, 'op_variants': 0, 'ed_variants': 0}

    # ── Process OP and ED separately ──
    for region_name, cue_key in [('OP', 'op_cues'), ('ED', 'ed_cues')]:
        region_lower = region_name.lower()  # 'op' or 'ed'
        # Collect all cues by timecode bucket (±2s tolerance)
        time_buckets = defaultdict(list)

        for fname, ep_data in all_episodes.items():
            for cue in ep_data[cue_key]:
                bucket_found = False
                for bucket_start in list(time_buckets.keys()):
                    if abs(cue['start_s'] - bucket_start) <= 2.0:
                        time_buckets[bucket_start].append({
                            'fname': fname, 'text': cue['text'],
                            'start': cue['start'], 'start_s': cue['start_s'],
                        })
                        bucket_found = True
                        break
                if not bucket_found:
                    time_buckets[cue['start_s']].append({
                        'fname': fname, 'text': cue['text'],
                        'start': cue['start'], 'start_s': cue['start_s'],
                    })

        # For each bucket, find variants and pick canonical
        for bucket_start, entries in sorted(time_buckets.items()):
            if len(entries) < 2:
                continue

            # Group by text, count frequencies
            text_counts = Counter(e['text'] for e in entries)
            if len(text_counts) == 1:
                continue

            canonical_text = text_counts.most_common(1)[0][0]
            canonical_count = text_counts.most_common(1)[0][1]

            # 过滤：规范文本必须是真正的目标语言内容
            # 跳过纯拉丁/数字/拟声词（mememe, ani, uwa 等）
            if not _is_valid_text(canonical_text, lang):
                continue

            stats[f'{region_lower}_groups'] += 1

            # Flag non-canonical entries
            for entry in entries:
                if entry['text'] != canonical_text:
                    entry_norm = normalizer.normalize(entry['text'], 'lenient')
                    canon_norm = normalizer.normalize(canonical_text, 'lenient')

                    if entry_norm == canon_norm or text_similarity(entry_norm, canon_norm) >= 0.7:
                        fixes.append({
                            'action': 'replace_text',
                            'file': entry['fname'],
                            'start': entry['start'],
                            'original': entry['text'],
                            'replacement': canonical_text,
                            'note': (f'{region_name} 歌词统一: {canonical_text} '
                                     f'({canonical_count}/{len(entries)}集)'),
                        })
                        stats[f'{region_lower}_variants'] += 1

    return {
        'fixes': fixes,
        'summary': {
            'episodes': len(all_episodes),
            'op_groups': stats['op_groups'],
            'ed_groups': stats['ed_groups'],
            'op_variants_found': stats['op_variants'],
            'ed_variants_found': stats['ed_variants'],
            'total_fixes': len(fixes),
        },
    }


# ═══════════════════════════════════════════════════════════════
# 8. CLI
# ═══════════════════════════════════════════════════════════════

def main():
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        description='Proper noun consistency checker (ja/zh)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python noun_checker.py target/EP064.srt --lang ja --noun-table reports/proper-nouns.md
  python noun_checker.py target/ --lang zh --noun-table reports/proper-nouns.md
        """
    )
    parser.add_argument('target', help='SRT file or directory')
    parser.add_argument('--lang', default='ja', choices=['ja', 'zh'],
                        help='Target language (default: ja)')
    parser.add_argument('--noun-table',
                        help='Path to proper-nouns.md (not needed for --oped)')
    parser.add_argument('--output', '-o',
                        help='Output JSON path (or directory for batch mode)')
    parser.add_argument('--threshold', '-t', type=float, default=0.7,
                        help='Fuzzy match threshold (default: 0.7)')
    parser.add_argument('--oped', action='store_true',
                        help='Cross-episode OP/ED lyric consistency check. '
                             'Collects all OP/ED cues, finds variants, generates fixes.')
    parser.add_argument('--op-boundary', type=float, default=95,
                        help='OP boundary in seconds (default: 95)')
    parser.add_argument('--ed-boundary', type=float, default=120,
                        help='ED boundary in seconds (default: 120)')
    args = parser.parse_args()

    # ── OP/ED 模式 ──
    if args.oped:
        if not os.path.isdir(args.target):
            print('ERROR: --oped requires a directory (cross-episode comparison)',
                  file=sys.stderr)
            sys.exit(1)
        print(f'[oped] 跨集 OP/ED 一致性检查 (lang={args.lang}) ...', file=sys.stderr)
        result = check_oped_consistency(args.target,
                                        op_boundary=args.op_boundary,
                                        ed_boundary=args.ed_boundary,
                                        lang=args.lang)
        s = result['summary']
        print(f'\n=== OP/ED Check ===')
        print(f'  Episodes: {s.get("episodes", 0)}')
        print(f'  OP groups: {s.get("op_groups", 0)}, variants: {s.get("op_variants_found", 0)}')
        print(f'  ED groups: {s.get("ed_groups", 0)}, variants: {s.get("ed_variants_found", 0)}')
        print(f'  Total fixes: {s.get("total_fixes", 0)}')

        fixes = result.get('fixes', [])
        if fixes:
            print(f'\n--- {len(fixes)} OP/ED fixes ---')
            for f in fixes[:15]:
                print(f'  [{f["file"]}] {f["start"]}')
                print(f'    {f["original"][:60]}')
                print(f'    → {f["replacement"][:60]}')
            if len(fixes) > 15:
                print(f'  ... and {len(fixes) - 15} more')

        if args.output:
            with open(args.output, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print(f'\n→ {args.output}', file=sys.stderr)
        return

    # ── 常规名词审查模式 ──
    if not args.noun_table:
        print('ERROR: --noun-table required (or use --oped for OP/ED check)',
              file=sys.stderr)
        sys.exit(1)

    # Determine single file vs directory
    if os.path.isfile(args.target):
        srt_files = [args.target]
    elif os.path.isdir(args.target):
        srt_files = sorted([
            os.path.join(args.target, f)
            for f in os.listdir(args.target)
            if f.endswith('.srt')
        ])
    else:
        print(f'ERROR: {args.target} not found.', file=sys.stderr)
        sys.exit(1)

    # Process each file
    for srt_path in srt_files:
        print(f'Checking ({args.lang}): {os.path.basename(srt_path)} ...', file=sys.stderr)
        report = check_srt(srt_path, args.noun_table, lang=args.lang)
        print_summary(report)

        # Write output
        if args.output:
            if os.path.isdir(args.output) or len(srt_files) > 1:
                out_dir = args.output if os.path.isdir(args.output) else os.path.dirname(args.output)
                os.makedirs(out_dir, exist_ok=True)
                base = os.path.splitext(os.path.basename(srt_path))[0]
                out_path = os.path.join(out_dir, f'{base}_nouns.json')
            else:
                out_path = args.output
                os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)

            with open(out_path, 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            print(f'→ {out_path}', file=sys.stderr)


if __name__ == '__main__':
    main()
