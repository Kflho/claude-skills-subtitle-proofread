#!/usr/bin/env python3
"""Find suspected proper nouns in SRT files — reusable for both source (ja)
and translation (zh) modes.

Core idea: segment text → filter against known glossaries → detect words that
look like proper nouns but aren't in any dictionary → group by similarity →
output JSON for AI review.

Usage:
  # Source mode (Japanese): find unrecognized names
  python find_suspect_nouns.py --input-dir 日文ai修复版/ \\
      --glossary reports/proper-nouns.md --lang ja --mode source

  # Translation mode (Chinese): find inconsistent translations
  python find_suspect_nouns.py --input-dir 中文AI翻译验证/ \\
      --mappings temp/noun_mappings.json --lang zh --mode translation

  # Translation mode with source cross-reference (enables clustering)
  python find_suspect_nouns.py --input-dir 中文AI翻译验证/ \\
      --source-dir 日文ai修复版/ \\
      --mappings temp/noun_mappings.json --lang zh --mode translation
"""

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

import lib._path  # noqa: F401
from lib.whisper_utils import parse_subtitles, extract_ep_number
from lib.japanese_utils import COMMON_KANJI, COMMON_KATAKANA, NON_WORD_RE
from lib.language_utils import get_lang_utils

# ── Tokenizer singletons (lazy init, same pattern as unified_scanner) ──
_janome_tokenizer = None
_jieba_available = None


def _get_janome():
    global _janome_tokenizer
    if _janome_tokenizer is None:
        try:
            from janome.tokenizer import Tokenizer
            _janome_tokenizer = Tokenizer()
        except ImportError:
            _janome_tokenizer = False
    return _janome_tokenizer if _janome_tokenizer is not False else None


def _get_jieba():
    global _jieba_available
    if _jieba_available is None:
        try:
            import jieba
            jieba.initialize()
            _jieba_available = jieba
        except ImportError:
            _jieba_available = False
    return _jieba_available if _jieba_available is not False else None


# ═══════════════════════════════════════════════════════════════
# Context patterns for proper noun detection (language-agnostic)
# ═══════════════════════════════════════════════════════════════

# ja: honorifics that follow a name
_JA_HONORIFIC_RE = re.compile(
    r'(さん|くん|ちゃん|様|殿|博士|警部|殿下|先生|総統|団長|伯爵|署長|所長|船長|部長|社長)$'
)
# ja: introduction patterns that PRECEDE a name
_JA_INTRO_BEFORE_RE = re.compile(
    r'(俺は|私は|僕は|わしは|それが|こちらが|この方が|紹介する|紹介します)'
)
# ja: introduction patterns that FOLLOW a name
_JA_INTRO_AFTER_RE = re.compile(
    r'(って言う|っていう|って呼ぶ|って呼ばれ|という|と言う|と呼ぶ|と呼ばれ|といいます|とは)'
)
# ja: calling patterns
_JA_CALLING_RE = re.compile(r'(おい|なあ|ねえ|もしもし|おーい)\s*([゠-ヿ]{2,6})')

# zh: title suffixes that follow a name
_ZH_TITLE_RE = re.compile(
    r'(先生|小姐|女士|老师|同学|博士|教授|局长|部长|社长|队长|团长|市长|县长|老板|总[统裁理监]|主席|书记|主任|经理|所长|处长|科长|警[官察长]|殿下|陛下)'
)
# zh: introduction patterns
_ZH_INTRO_RE = re.compile(
    r'(叫|名叫|叫作|叫做|就是|那就是|这位是|我是|我叫|人称|被称为|大家都叫[我他她])'
)
# zh: possessive/relationship before a name-like token
_ZH_POSSESSIVE_RE = re.compile(
    r'(我的|你的|他的|她的|我们的|你们的|他们的|小|老|大|阿)'
)

# ── Short token patterns (likely name fragments when in context) ──
# Single CJK char that's not a common particle/verb
_ZH_SINGLE_CHAR_BLACKLIST = frozenset(
    '的了是在有我他她你我它这和那个不一人大中上下来去到说看走过能把会对要以可为之日年月时所里出家前后左右东西南北'
    '很都也还就已经才刚正又将只被让给从比向同与及或没别但而因所以如果虽然然而因此于是因为'
)

# Common Chinese 2-3 char words that are never proper nouns
_ZH_COMMON_BLACKLIST = frozenset({
    '这是', '那是', '什么', '怎么', '这么', '那么', '为什么', '怎么样',
    '可以', '已经', '没有', '还是', '只是', '但是', '因为', '所以',
    '如果', '虽然', '不过', '而且', '然后', '这个', '那个', '哪个',
    '我们', '你们', '他们', '她们', '自己', '大家', '别人', '一起',
    '知道', '觉得', '看到', '听到', '来到', '出来', '过来', '起来',
    '今天', '明天', '昨天', '现在', '然后', '可是', '一定', '一样',
    '突然', '终于', '马上', '立刻', '慢慢', '好好', '真的', '全部',
    '所有', '整个', '到处', '很多', '几乎', '至少', '竟然', '果然',
    '进行', '需要', '应该', '可能', '当然', '必须', '一直', '已经',
    '马戏团', '机器人', '科学', '世界', '人类', '地球', '日本',
    '歌舞伎町', '能量', '博士', '孩子', '爸爸', '妈妈', '爸爸',
    '东西', '事情', '问题', '办法', '地方', '时间', '力量',
    '哈哈哈', '嘿嘿嘿', '呵呵呵', '拜托', '谢谢', '对不起',
    # Common verbs/adjectives (not names)
    '不死', '不坏', '不在意', '长不大', '没变', '没长', '没用',
    '好吃', '好喝', '好看', '好听', '好闻',
    '去吧', '来吧', '走吧', '跑吧', '吃吧', '喝吧',
    '歌舞伎', '歌舞伎町', '宅邸', '梅干',
})



# ═══════════════════════════════════════════════════════════════
# Known term loading
# ═══════════════════════════════════════════════════════════════

def _load_known_terms(glossary_path=None, mappings_path=None, lang='ja'):
    """Load all known terms to exclude from suspect detection.

    Returns: (known_ja_terms, known_zh_terms, ja_to_zh)
    """
    known_ja = set()
    known_zh = set()
    ja_to_zh = {}

    # From glossary markdown table
    if glossary_path and os.path.exists(glossary_path):
        with open(glossary_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line.startswith('|') or line.startswith('|--'):
                    continue
                cells = [c.strip() for c in line.split('|')]
                if len(cells) >= 2 and cells[1]:
                    term = cells[1]
                    if len(term) >= 2:
                        known_ja.add(term)

    # From mappings JSON
    if mappings_path and os.path.exists(mappings_path):
        with open(mappings_path, 'r', encoding='utf-8') as f:
            mappings = json.load(f)
        for ja_term, zh_term in mappings.items():
            if ja_term and len(ja_term) >= 2:
                known_ja.add(ja_term)
            if zh_term and len(zh_term) >= 1:
                known_zh.add(zh_term)
            if ja_term and zh_term:
                ja_to_zh[ja_term] = zh_term

    # Add language built-in common words
    if lang == 'ja':
        known_ja.update(COMMON_KANJI)
        known_ja.update(COMMON_KATAKANA)
    elif lang == 'zh':
        # jieba high-freq words as filter
        jieba_mod = _get_jieba()
        if jieba_mod:
            # Words with freq > 100 are almost certainly common
            for word, freq in jieba_mod.dt.FREQ.items():
                if freq > 100 and len(word) >= 1:
                    known_zh.add(word)
        # Also add the Japanese known terms as they can appear in Chinese output
        known_zh.update({w for w in COMMON_KANJI if len(w) >= 1})
        known_zh.update({w for w in COMMON_KATAKANA if len(w) >= 1})

    return known_ja, known_zh, ja_to_zh


# ═══════════════════════════════════════════════════════════════
# Segmentation
# ═══════════════════════════════════════════════════════════════

def _segment_ja(text):
    """Segment Japanese text into word tokens using Janome.

    Uses relaxed POS filtering compared to unified_scanner — includes
    verbs and other POS since Japanese names can derive from any word class
    (e.g. 飛び can be a verb or a name depending on context).

    Single kanji are kept (many Japanese names are single characters: 扉, 鉄, etc.)
    """
    tokenizer = _get_janome()
    if not tokenizer:
        return []
    tokens = []
    try:
        for tok in tokenizer.tokenize(text):
            surface = tok.surface
            # Single kanji → keep (many names: 扉, 鉄, 岩)
            if len(surface) < 1:
                continue
            # Skip hiragana-only
            if not re.search(r'[一-鿿゠-ヿ]', surface):
                continue
            # Single kana → skip (particles, fragments)
            if len(surface) == 1 and not re.search(r'[一-鿿]', surface):
                continue
            # Relaxed POS: skip particles, aux verbs, symbols, conjunctions, interjections
            pos = tok.part_of_speech.split(',')[0]
            if pos in ('助詞', '助動詞', '記号', '接続詞', '感動詞', '連体詞', 'フィラー'):
                continue
            tokens.append(surface)
    except Exception:
        pass
    return tokens


def _segment_zh(text):
    """Segment Chinese text into word tokens using jieba."""
    jieba_mod = _get_jieba()
    if not jieba_mod:
        # Fallback: character n-grams
        tokens = []
        for i in range(len(text)):
            if '一' <= text[i] <= '鿿':
                if i + 1 < len(text) and '一' <= text[i + 1] <= '鿿':
                    tokens.append(text[i:i + 2])
                if i + 2 < len(text) and '一' <= text[i + 2] <= '鿿':
                    tokens.append(text[i:i + 3])
        return list(set(tokens))

    tokens = list(jieba_mod.cut(text))
    # Keep only CJK-bearing tokens >= 1 char
    return [t for t in tokens if re.search(r'[一-鿿]', t) and len(t) >= 1]


def _is_in_name_context(cue_text, token, lang='ja'):
    """Check if token appears in a name-like context (honorific, intro, etc.)."""
    if lang == 'ja':
        # Check if token is followed by an honorific
        pattern = re.compile(re.escape(token) + r'\s*(さん|くん|ちゃん|様|殿|博士)')
        if pattern.search(cue_text):
            return True, 'honorific_suffix'
        # Check if token follows an introduction
        if _JA_INTRO_BEFORE_RE.search(cue_text):
            return True, 'intro_before'
        if _JA_INTRO_AFTER_RE.search(cue_text):
            return True, 'intro_after'
        # Check calling pattern
        if re.search(r'(おい|なあ|ねえ)\s*' + re.escape(token), cue_text):
            return True, 'calling'
    elif lang == 'zh':
        if _ZH_TITLE_RE.search(cue_text):
            return True, 'title_suffix'
        if _ZH_INTRO_RE.search(cue_text):
            return True, 'intro_pattern'
        if _ZH_POSSESSIVE_RE.search(cue_text):
            return True, 'possessive'

    return False, ''


# ═══════════════════════════════════════════════════════════════
# Main detection logic
# ═══════════════════════════════════════════════════════════════

def find_suspect_nouns(input_dir, lang='ja', mode='source',
                       glossary_path=None, mappings_path=None,
                       source_dir=None, limit=None):
    """Scan SRT files for suspected proper nouns.

    Args:
        input_dir: Directory of SRT files to scan
        lang: 'ja' or 'zh'
        mode: 'source' (find unrecognized names) or 'translation' (find inconsistent)
        glossary_path: Path to proper-nouns.md
        mappings_path: Path to noun_mappings.json
        source_dir: (translation mode) Japanese source SRTs for cross-reference
        limit: Max files to scan (for testing)

    Returns:
        dict with 'groups' and 'singletons' lists
    """
    known_ja, known_zh, ja_to_zh = _load_known_terms(
        glossary_path, mappings_path, lang
    )

    # Collect SRT files
    srt_files = sorted([
        f for f in os.listdir(input_dir)
        if f.endswith(('.srt', '.ass'))
    ])
    if limit:
        srt_files = srt_files[:limit]

    # ── Track per-term metadata ──
    # {term: {'count': N, 'eps': set(), 'contexts': [...]}}
    term_info = defaultdict(lambda: {'count': 0, 'eps': set(), 'contexts': []})

    segment_fn = _segment_ja if lang == 'ja' else _segment_zh
    known_set = known_ja if lang == 'ja' else known_zh

    for filename in srt_files:
        filepath = os.path.join(input_dir, filename)
        ep = extract_ep_number(filename) or filename
        try:
            cues = parse_subtitles(filepath)
        except Exception as e:
            print(f'  [WARN] {filename}: parse error ({e})', file=sys.stderr)
            continue

        for cue in cues:
            text = cue.get('text', '')
            if not text or len(text) < 2:
                continue

            tokens = segment_fn(text)
            for token in tokens:
                # Skip known terms
                if token in known_set:
                    continue
                # Skip non-word patterns
                if NON_WORD_RE.match(token):
                    continue
                # Skip digits/punctuation-only
                if not re.search(r'[一-鿿゠-ヿ]', token):
                    continue

                # ── Apply heuristic rules ──
                keep = False
                reason = ''

                if lang == 'ja':
                    # Rule D: katakana 2-6 chars
                    if re.match(r'^[゠-ヿ]{2,6}$', token):
                        keep = True
                        reason = 'katakana_name_len'
                    # Rule E: 1-3 char kanji, not common word
                    elif re.match(r'^[一-鿿]{1,3}$', token):
                        if token not in COMMON_KANJI:
                            keep = True
                            reason = 'short_kanji'
                elif lang == 'zh':
                    # Skip common words
                    if token in _ZH_COMMON_BLACKLIST:
                        continue
                    # Rule E: 1-3 char hanzi
                    if re.match(r'^[一-鿿]{1,3}$', token):
                        # Single char must pass blacklist
                        if len(token) == 1 and token in _ZH_SINGLE_CHAR_BLACKLIST:
                            continue
                        keep = True
                        reason = 'short_hanzi'

                # Rule F/G: name context check
                in_context, ctx_reason = _is_in_name_context(text, token, lang)
                if in_context:
                    keep = True
                    reason = (reason + '+' + ctx_reason) if reason else ctx_reason

                if not keep:
                    continue

                info = term_info[token]
                info['count'] += 1
                info['eps'].add(ep)
                if len(info['contexts']) < 5:
                    info['contexts'].append({
                        'ep': ep,
                        'cue_start': cue['start'],
                        'cue_text': text[:80],
                    })

    # ── Filter: Rule H (cross-episode) and minimum quality ──
    # Threshold adapts to scan size — single files have lower bar
    n_files = len(srt_files)
    min_eps = 1 if n_files <= 3 else 2
    min_count = 1 if n_files <= 3 else 3
    suspects = []
    for term, info in term_info.items():
        n_eps = len(info['eps'])
        if n_eps >= min_eps and info['count'] >= min_count:
            suspects.append({
                'text': term,
                'count': info['count'],
                'eps': sorted(info['eps']),
                'contexts': info['contexts'][:3],
            })

    suspects.sort(key=lambda s: -s['count'])

    # ── Clustering (translation mode with source) ──
    groups = []
    singletons = []

    if mode == 'translation' and source_dir:
        groups = _cluster_by_source(suspects, input_dir, source_dir,
                                    ja_to_zh, known_ja)

    # Remaining unclustered suspects become singletons
    clustered_texts = set()
    for g in groups:
        for v in g.get('variants', []):
            clustered_texts.add(v['text'])
    for s in suspects:
        if s['text'] not in clustered_texts:
            singletons.append({
                'text': s['text'],
                'count': s['count'],
                'eps': s['eps'],
                'reason': 'unknown_suspect',
                'contexts': s['contexts'],
            })

    return {
        'groups': groups,
        'singletons': singletons[:50],  # cap for AI review
    }


def _norm_ja_reading(term):
    """Normalize a Japanese term to its katakana reading for variant matching.

    Uses Janome's reading feature. Falls back to heuristic normalization.
    """
    tokenizer = _get_janome()
    if tokenizer:
        try:
            for tok in tokenizer.tokenize(term):
                reading = tok.reading if hasattr(tok, 'reading') else ''
                if reading:
                    # Normalize: remove long vowels, small kana
                    r = reading
                    r = r.replace('ー', '')
                    for s, l in [('ァ','ア'),('ィ','イ'),('ゥ','ウ'),('ェ','エ'),('ォ','オ'),
                                 ('ャ','ヤ'),('ュ','ユ'),('ョ','ヨ'),('ッ','ツ')]:
                        r = r.replace(s, l)
                    return r
        except Exception:
            pass
    # Fallback: just clean the term itself
    result = term
    result = result.replace('ー', '')
    for s, l in [('ァ','ア'),('ィ','イ'),('ゥ','ウ'),('ェ','エ'),('ォ','オ'),
                 ('ャ','ヤ'),('ュ','ユ'),('ョ','ヨ'),('ッ','ツ')]:
        result = result.replace(s, l)
    return result


def _cluster_by_source(suspects, target_dir, source_dir, ja_to_zh, known_ja=None):
    """Group suspects that map to the same Japanese source term.

    Uses katakana reading normalization so that 扉/飛び/トビ all match.
    """
    if known_ja is None:
        known_ja = set()

    # Build reverse index: Japanese source term (normalized) → set of Chinese translations
    ja_norm_to_zh_variants = defaultdict(set)
    # Also: normalized ja → original ja surface forms
    ja_norm_to_surfaces = defaultdict(set)

    # Also build: Chinese term → Japanese source term norm
    zh_to_ja_norms = defaultdict(set)

    target_files = sorted([f for f in os.listdir(target_dir) if f.endswith('.srt')])
    source_files = sorted([f for f in os.listdir(source_dir) if f.endswith('.srt')])

    # Match files by episode number
    target_by_ep = {}
    for f in target_files:
        ep = extract_ep_number(f)
        if ep:
            target_by_ep[ep] = f

    source_by_ep = {}
    for f in source_files:
        ep = extract_ep_number(f)
        if ep:
            source_by_ep[ep] = f

    suspect_set = {s['text'] for s in suspects}

    for ep, target_file in sorted(target_by_ep.items())[:20]:  # sample first 20 EPs
        if ep not in source_by_ep:
            continue

        target_path = os.path.join(target_dir, target_file)
        source_path = os.path.join(source_dir, source_by_ep[ep])

        try:
            target_cues = parse_subtitles(target_path)
            source_cues = parse_subtitles(source_path)
        except Exception:
            continue

        # Align by cue index (both files should have same structure)
        for i, (tc, sc) in enumerate(zip(target_cues, source_cues)):
            # Segment source (ja)
            ja_tokens = _segment_ja(sc.get('text', ''))
            # Segment target (zh)
            zh_tokens = _segment_zh(tc.get('text', ''))

            # Find overlaps: zh tokens that are suspects
            zh_suspects_in_cue = [t for t in zh_tokens if t in suspect_set]
            for zh_term in zh_suspects_in_cue:
                for ja_term in ja_tokens:
                    ja_norm = _norm_ja_reading(ja_term)
                    if not ja_norm or len(ja_norm) < 2:
                        continue
                    # Link if: JA norm matches a known mapping key,
                    # OR surface is in known_ja,
                    # OR norm is a substring of a known mapping key
                    # (e.g. トビ ⊂ トビラ → both refer to Tobio/飞雄)
                    is_mapped = ja_norm in ja_to_zh or ja_term in known_ja
                    if not is_mapped:
                        for known_key in ja_to_zh:
                            if (len(ja_norm) >= 2 and len(known_key) >= 2
                                    and (ja_norm in known_key
                                         or known_key in ja_norm)):
                                is_mapped = True
                                break
                    if not is_mapped:
                        continue
                    ja_norm = _norm_ja_reading(ja_term)
                    if not ja_norm or len(ja_norm) < 2:
                        continue
                    ja_norm_to_surfaces[ja_norm].add(ja_term)
                    ja_norm_to_zh_variants[ja_norm].add(zh_term)
                    zh_to_ja_norms[zh_term].add(ja_norm)

    # Build ALL ja_norm → zh_variant pairs first (even singletons)
    all_groups = []
    for ja_norm, zh_variants in sorted(ja_norm_to_zh_variants.items()):
        surfaces = ja_norm_to_surfaces.get(ja_norm, {ja_norm})
        # Find known translation
        known_zh = ''
        for surf in surfaces:
            if surf in ja_to_zh:
                known_zh = ja_to_zh[surf]
                break

        all_groups.append({
            'suspected_canonical': known_zh or f'[未知: {ja_norm}]',
            'source_ja': ', '.join(sorted(surfaces)[:5]),
            'source_ja_norm': ja_norm,
            'reason': f'日语"{ja_norm}"',
            'variants': [
                {'text': v, 'eps': []} for v in sorted(zh_variants)
            ],
        })

    # Merge related groups (substring overlap in normalized readings)
    groups = _merge_related_groups(all_groups)

    # Filter: only keep groups with >= 2 variants after merge
    groups = [g for g in groups if len(g.get('variants', [])) >= 2]

    # Update seen_zh to avoid duplicates in singletons
    seen_zh = set()
    for g in groups:
        for v in g.get('variants', []):
            seen_zh.add(v['text'])

    # ── Post-process: merge groups with overlapping normalized readings ──
    groups = _merge_related_groups(groups)

    return groups


def _merge_related_groups(groups):
    """Merge groups whose normalized Japanese readings are related.

    Two groups are merged if one reading is a substring of the other
    (e.g. トビ ⊂ トビラ, トビ ⊂ トビウオ).
    """
    if len(groups) <= 1:
        return groups

    merged = []
    used = set()

    for i, g1 in enumerate(groups):
        if i in used:
            continue
        norm1 = g1.get('source_ja_norm', '')
        for j, g2 in enumerate(groups):
            if j <= i or j in used:
                continue
            norm2 = g2.get('source_ja_norm', '')
            # Check substring relationship
            if (len(norm1) >= 2 and len(norm2) >= 2 and
                    (norm1 in norm2 or norm2 in norm1)):
                # Merge g2 into g1
                used.add(j)
                g1['variants'].extend(g2['variants'])
                g1['source_ja'] += ', ' + g2['source_ja']
                g1['reason'] = g1['reason'].replace('同源异译',
                                                    f'同源异译(模糊匹配: {norm1}≈{norm2})')
                if not g1['suspected_canonical'].startswith('['):
                    pass  # keep known canonical
                elif not g2['suspected_canonical'].startswith('['):
                    g1['suspected_canonical'] = g2['suspected_canonical']

        merged.append(g1)

    # Remove groups that were merged into others (now have only 1 variant)
    return [g for g in merged if len(g.get('variants', [])) >= 2]


# ═══════════════════════════════════════════════════════════════
# Search by corrections (user-provided error examples)
# ═══════════════════════════════════════════════════════════════

def search_corrections(input_dir, corrections, source_dir=None, lang='zh',
                       output_path=None, apply_fixes=False, limit=None):
    """Batch search for user-provided error patterns across all episodes.

    Args:
        input_dir: Directory of SRT files to scan
        corrections: List of correction dicts, each with:
            - wrong: list of incorrect Chinese terms
            - correct: the correct Chinese term
            - source_ja: (optional) Japanese source term for confirmation
        source_dir: Japanese source SRT directory for cross-reference
        lang: Language for tokenization
        output_path: Where to write the hits JSON
        apply_fixes: If True, actually modify SRT files
        limit: Max files to scan

    Returns:
        dict with per-correction hit lists
    """
    srt_files = sorted([
        f for f in os.listdir(input_dir)
        if f.endswith(('.srt', '.ass'))
    ])
    if limit:
        srt_files = srt_files[:limit]

    segment_fn = _segment_zh if lang == 'zh' else _segment_ja

    # Build source file index if cross-referencing
    source_by_ep = {}
    if source_dir and os.path.isdir(source_dir):
        source_files = sorted([f for f in os.listdir(source_dir)
                               if f.endswith('.srt')])
        for f in source_files:
            ep = extract_ep_number(f)
            if ep:
                source_by_ep[ep] = os.path.join(source_dir, f)

    # Build wrong-term → correction lookup
    wrong_to_correction = {}
    for corr in corrections:
        for w in corr.get('wrong', []):
            if w not in wrong_to_correction:
                wrong_to_correction[w] = []
            wrong_to_correction[w].append(corr)

    # Per-correction results
    results = []
    for corr in corrections:
        results.append({
            'correction': corr,
            'hits': [],
            'total_hits': 0,
            'eps_affected': set(),
        })

    all_wrong_terms = set(wrong_to_correction.keys())

    # Scan all files
    for filename in srt_files:
        filepath = os.path.join(input_dir, filename)
        ep = extract_ep_number(filename) or filename
        try:
            cues = parse_subtitles(filepath)
        except Exception as e:
            print(f'  [WARN] {filename}: parse error ({e})', file=sys.stderr)
            continue

        # Load source cues if available
        source_cues = None
        if ep in source_by_ep:
            try:
                source_cues = parse_subtitles(source_by_ep[ep])
            except Exception:
                pass

        for ci, cue in enumerate(cues):
            text = cue.get('text', '')
            if not text:
                continue

            tokens = segment_fn(text)
            matched_tokens = [t for t in tokens if t in all_wrong_terms]

            if not matched_tokens:
                continue

            # Get aligned source text if available
            source_text = ''
            source_ja_matched = ''
            if source_cues and ci < len(source_cues):
                source_text = source_cues[ci].get('text', '')

            for token in matched_tokens:
                for corr_info in wrong_to_correction.get(token, []):
                    corr_idx = corrections.index(corr_info)
                    correct = corr_info.get('correct', '')
                    source_ja_target = corr_info.get('source_ja', '')

                    # Cross-reference with source if available
                    source_confirmed = False
                    if source_text and source_ja_target:
                        ja_tokens = _segment_ja(source_text)
                        for ja_term in ja_tokens:
                            ja_norm = _norm_ja_reading(ja_term)
                            target_norm = _norm_ja_reading(source_ja_target)
                            if (ja_norm == target_norm or
                                (len(ja_norm) >= 2 and len(target_norm) >= 2 and
                                 (ja_norm in target_norm or target_norm in ja_norm))):
                                source_confirmed = True
                                source_ja_matched = ja_term
                                break

                    # Build suggested fix
                    suggested_text = text.replace(token, correct)

                    hit = {
                        'ep': ep,
                        'cue_index': ci,
                        'cue_start': cue.get('start', ''),
                        'original_text': text,
                        'matched_token': token,
                        'suggested_text': suggested_text,
                        'source_ja_text': source_text[:80] if source_text else '',
                        'source_ja_matched': source_ja_matched,
                        'source_confirmed': source_confirmed,
                    }
                    results[corr_idx]['hits'].append(hit)
                    results[corr_idx]['total_hits'] += 1
                    results[corr_idx]['eps_affected'].add(ep)

    # Convert sets to sorted lists
    for r in results:
        r['eps_affected'] = sorted(r['eps_affected'])
        r['n_eps'] = len(r['eps_affected'])

    # ── Apply fixes if requested ──
    if apply_fixes:
        _apply_correction_fixes(input_dir, results)

    # Write output
    if output_path:
        os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f'→ {output_path}', file=sys.stderr)

    # Summary
    for r in results:
        corr = r['correction']
        print(f'  {corr["correct"]} ← {corr["wrong"][:5]}: '
              f'{r["total_hits"]} hits in {r["n_eps"]} episodes',
              file=sys.stderr)

    return results


def _apply_correction_fixes(input_dir, results):
    """Apply correction replacements to SRT files in-place.

    Backs up original files to <filename>.bak before modifying.
    """
    from lib.whisper_utils import write_subtitles

    # Collect all fixes per file
    file_fixes = defaultdict(list)  # {filepath: [(cue_index, old_text, new_text)]}

    for r in results:
        for hit in r['hits']:
            ep = hit['ep']
            # Find the file
            for f in os.listdir(input_dir):
                if extract_ep_number(f) == ep:
                    filepath = os.path.join(input_dir, f)
                    file_fixes[filepath].append(
                        (hit['cue_index'], hit['original_text'],
                         hit['suggested_text'])
                    )
                    break

    for filepath, fixes in file_fixes.items():
        try:
            cues = parse_subtitles(filepath)
        except Exception:
            continue

        # Apply fixes by cue index
        fix_map = {ci: (old, new) for ci, old, new in fixes}
        applied = 0
        for i, cue in enumerate(cues):
            if i in fix_map:
                old, new = fix_map[i]
                if cue.get('text', '') == old:
                    cue['text'] = new
                    applied += 1

        if applied > 0:
            # Backup original
            bak_path = filepath + '.bak'
            if not os.path.exists(bak_path):
                import shutil
                shutil.copy2(filepath, bak_path)

            write_subtitles(cues, filepath)
            print(f'  [apply] {os.path.basename(filepath)}: '
                  f'{applied} fixes applied', file=sys.stderr)


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description='Find suspected proper nouns in SRT files'
    )
    parser.add_argument('--input-dir', required=True,
                        help='Directory of SRT files to scan')
    parser.add_argument('--lang', default='ja', choices=['ja', 'zh'],
                        help='Language for tokenization (default: ja)')
    parser.add_argument('--mode', default='source',
                        choices=['source', 'translation', 'search'],
                        help='source=find unrecognized names, translation=find inconsistent, '
                             'search=batch find user-provided error patterns')
    parser.add_argument('--glossary', help='Path to proper-nouns.md (known terms)')
    parser.add_argument('--mappings', help='Path to noun_mappings.json (known ja→zh)')
    parser.add_argument('--source-dir',
                        help='Japanese source SRTs for cross-reference')
    parser.add_argument('--corrections',
                        help='(search mode) JSON file with correction patterns')
    parser.add_argument('--apply-corrections', action='store_true',
                        help='(search mode) Actually apply fixes to SRT files')
    parser.add_argument('--output', default='temp/scans/suspect_nouns.json',
                        help='Output JSON path')
    parser.add_argument('--limit', type=int, default=0,
                        help='Max files to scan (for testing)')
    args = parser.parse_args()

    if not os.path.isdir(args.input_dir):
        print(f'ERROR: input-dir not found: {args.input_dir}', file=sys.stderr)
        sys.exit(1)

    # ── Search mode: user-provided corrections ──
    if args.mode == 'search':
        if not args.corrections:
            print('ERROR: --mode search requires --corrections <JSON>',
                  file=sys.stderr)
            sys.exit(1)
        with open(args.corrections, 'r', encoding='utf-8') as f:
            corr_data = json.load(f)
        corrections = corr_data.get('corrections', [])
        if not corrections:
            print('ERROR: no "corrections" array found in JSON', file=sys.stderr)
            sys.exit(1)

        search_corrections(
            input_dir=args.input_dir,
            corrections=corrections,
            source_dir=args.source_dir,
            lang=args.lang,
            output_path=args.output,
            apply_fixes=args.apply_corrections,
            limit=args.limit or None,
        )
        return

    # ── Auto-discovery mode (source / translation) ──
    result = find_suspect_nouns(
        input_dir=args.input_dir,
        lang=args.lang,
        mode=args.mode,
        glossary_path=args.glossary,
        mappings_path=args.mappings,
        source_dir=args.source_dir,
        limit=args.limit or None,
    )

    # Write output
    os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    n_groups = len(result['groups'])
    n_single = len(result['singletons'])
    total_group_variants = sum(len(g.get('variants', [])) for g in result['groups'])
    print(f'→ {args.output}', file=sys.stderr)
    print(f'  Groups: {n_groups} ({total_group_variants} variants) | '
          f'Singletons: {n_single}', file=sys.stderr)

    # Quick summary for AI
    if result['groups']:
        print('\n[SUSPECT GROUPS]', file=sys.stderr)
        for g in result['groups'][:10]:
            variants_str = ', '.join(
                v['text'] for v in g.get('variants', [])[:5]
            )
            print(f'  {g["suspected_canonical"]} ← [{variants_str}]',
                  file=sys.stderr)


if __name__ == '__main__':
    main()
