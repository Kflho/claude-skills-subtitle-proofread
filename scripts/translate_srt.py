#!/usr/bin/env python3
"""SRT/ASS batch translator — Japanese → Chinese via OpenAI-compatible API.

Features:
- OP/ED pre-translate: detect + translate once, apply to all episodes (--skip-oped to disable)
- Proper noun pre-replace: ja→zh mapping from glossary before translation
- Batch processing: N cues/batch with ±3 cue JA context window (pre-computed → fully parallel)
- Intra-episode parallelism: all batches submitted concurrently (ThreadPoolExecutor)

Usage:
  # Single file
  python translate_srt.py --input EP001.srt --output 中文/EP001.srt

  # Batch directory (with proper noun mappings, skip OP/ED)
  python translate_srt.py --input-dir AI审查后/ --output-dir 中文翻译后/ \
    --mappings noun_mappings.json --skip-oped

  # With glossary (proper nouns + OP/ED)
  python translate_srt.py --input-dir AI审查后/ --output-dir 中文翻译后/ \\
      --glossary reports/proper-nouns.md

  # Preview mode
  python translate_srt.py --input EP001.srt --dry-run

Setup:
  LLM_API_KEY env var (required)
  LLM_MODEL env var (optional, default deepseek-chat)
  LLM_BASE_URL env var (optional, default https://api.deepseek.com/v1)
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import lib._path  # noqa: F401
from lib.whisper_utils import parse_subtitles, write_subtitles, OP_BOUNDARY_SEC, ED_BOUNDARY_SEC
from lib.video_scan import episode_token, parse_episode_spec
from lib.config import (
    LLM_API_KEY, LLM_MODEL, LLM_BASE_URL,
    LLM_MODEL_DEFAULT, LLM_BASE_URL_DEFAULT,
    LLM_MAX_TOKENS, LLM_REASONING_EFFORT,
)

# ═══════════════════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════════════════

BATCH_SIZE = 10   # cues per API call
DELAY = 1.0       # seconds between batches

# ═══════════════════════════════════════════════════════════════
# Language detection & prompt building
# ═══════════════════════════════════════════════════════════════

# Character ranges for CJK / Cyrillic / Kana detection
import unicodedata


def _detect_source_lang(cues, sample_size=20):
    """Detect source language from cue text. Returns 'ja', 'ru', 'zh', or 'other'.

    Samples up to `sample_size` cues and counts characters in each script.
    """
    ja_count = 0
    ru_count = 0
    zh_count = 0
    total_chars = 0

    for cue in cues[:sample_size]:
        text = cue.get('text', '') if isinstance(cue, dict) else str(cue)
        for ch in text:
            cp = ord(ch)
            if 0x3040 <= cp <= 0x30FF:    # Hiragana + Katakana
                ja_count += 1
            elif 0x0400 <= cp <= 0x04FF:   # Cyrillic
                ru_count += 1
            elif 0x4E00 <= cp <= 0x9FFF:   # CJK Unified
                zh_count += 1
            total_chars += 1

    if total_chars == 0:
        return 'other'

    ja_ratio = ja_count / total_chars
    ru_ratio = ru_count / total_chars

    if ja_ratio >= 0.05:
        return 'ja'
    if ru_ratio >= 0.05:
        return 'ru'
    if zh_count / total_chars >= 0.3:
        return 'zh'
    return 'other'


def _build_system_prompt(source_lang):
    """Build system prompt with language-specific rules."""
    lang_name = {'ja': '日语', 'ru': '俄语', 'zh': '中文', 'other': '外语'}.get(source_lang, '外语')

    base = f"""你是动画字幕翻译专家。将{lang_name}字幕翻译为自然口语化的中文。

规则：
1. **准确翻译**：保持原意，不增不减
2. **口语化**：用自然中文口语表达，避免翻译腔
   - 书面词换口语词（"如何"→"怎么"、"迅速"→"快"）
3. **角色语言风格**：
   - 老年男性：用语稳重、简洁
   - 少年/儿童：口语化、直接
   - 女性：自然柔和（不要过度加"呢""哦"）
4. **保持专有名词**：角色名、地名、组织名、术语不改变原文
5. **字幕长度适中**：翻译后不应明显变长或变短"""

    # Japanese-specific rules
    if source_lang == 'ja':
        base += """
   - 省略冗余主语（日语主语常可省略）
   - 敬语适度（です/ます 不一定翻成"请"）"""

    base += f"""

输入是一组{lang_name}字幕，输出每条对应的中文翻译。
输出必须是严格的 JSON 数组，每个元素是翻译后的中文字幕字符串。
不要输出任何 JSON 之外的内容。"""
    return base


USER_TEMPLATE = """上下文（前几条日文原文，帮助理解语流）：
{context_before}

本次要翻译的字幕（日文）：
{targets}

固定译名参考（必须使用）：
{glossary}

请翻译以上每条字幕，输出 JSON 数组："""


# ═══════════════════════════════════════════════════════════════
# Glossary loading (ja→zh mapping)
# ═══════════════════════════════════════════════════════════════

def load_glossary(path):
    """从 proper-nouns.md 提取专有名词列表（用于 prompt 注入）。

    同时尝试构建 ja→zh 映射（如果表格有日语和中文两列）。
    返回 (glossary_str, ja_to_zh_dict)。
    """
    if not path or not os.path.exists(path):
        return '', {}

    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    terms = set()
    ja_to_zh = {}

    for line in content.split('\n'):
        line = line.strip()
        if not line.startswith('|') or line.startswith('|--'):
            continue
        cells = [c.strip() for c in line.split('|')]
        # 跳过标题行
        if any(h in cells[0].lower() for h in ('日语', '术语', '原文', '---')):
            continue

        # 尝试提取 ja → zh 映射（单元格1=日语, 单元格2=中文）
        if len(cells) >= 3 and cells[1] and cells[2]:
            ja_term = cells[1]
            zh_term = cells[2]
            if len(ja_term) >= 2 and len(zh_term) >= 1:
                terms.add(zh_term)
                ja_to_zh[ja_term] = zh_term
        elif len(cells) >= 2 and cells[1]:
            term = cells[1]
            if len(term) >= 2:
                terms.add(term)

    glossary_str = ', '.join(sorted(terms)) if terms else ''
    return glossary_str, ja_to_zh


def load_mappings(path):
    """从 JSON 文件加载 ja→zh 映射（供 translate_srt.py 使用）。

    格式: {"ja_term": "zh_translation", ...}
    zh 值为空的条目会被过滤掉（AI 尚未审查）。
    `_` 开头的键视为说明性元数据（`_comment` 等），不参与译名表与预替换。
    返回 (glossary_str, ja_to_zh_dict)，与 load_glossary() 签名一致。
    """
    if not path or not os.path.exists(path):
        return '', {}

    with open(path, 'r', encoding='utf-8') as f:
        raw = json.load(f)

    # 只保留 AI 已填入中文译名的条目
    # 注意：允许单字条目（如汉字专名「扉」），但跳过空值
    # ⚠️ 必须滤掉 `_comment` 之类的元数据键：它们的值是整段说明文字，
    # 混进 glossary_str 后会顶进 prompt 的「固定译名参考（必须使用）」，
    # 把 prompt 撑长且语义混乱 —— 实测可让模型放弃翻译、整批原样回显日文。
    ja_to_zh = {k: v for k, v in raw.items()
                if v and not str(k).startswith('_')}
    glossary_str = ', '.join(sorted(set(ja_to_zh.values()))) if ja_to_zh else ''
    return glossary_str, ja_to_zh


# ═══════════════════════════════════════════════════════════════
# OP/ED detection + pre-translation
# ═══════════════════════════════════════════════════════════════

def _time_to_seconds(ts):
    """Convert SRT timestamp 'HH:MM:SS,mmm' to float seconds."""
    m = re.match(r'(\d+):(\d+):(\d+)[,.](\d+)', str(ts))
    if not m:
        return 0.0
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4)) / 1000


def _is_oped_region(cue_start_s, cue_end_s, total_duration_s):
    """Check if a cue falls in OP (first N s) or ED (last N s) region."""
    if cue_start_s <= OP_BOUNDARY_SEC:
        return 'OP'
    if total_duration_s > 0 and cue_end_s >= total_duration_s - ED_BOUNDARY_SEC:
        return 'ED'
    return None


def _pick_oped_canonical(variants):
    """Pick the canonical form from variants dict {text: count}."""
    if not variants:
        return None
    # Most frequent variant is canonical
    return max(variants, key=variants.get)


def collect_oped_across_episodes(input_dir, api_key, model, base_url, dry_run=False):
    """Scan all episodes to collect OP/ED text across episodes.

    Returns dict:
        op_zh, ed_zh: str|None — canonical Chinese OP/ED text
        op_texts, ed_texts: {ep_name: [(cue_idx, ja_text)]} — 各集窗口内 cue 位置
        op_ja, ed_ja: str|None — canonical Japanese text
        op_variants, ed_variants: Counter — 跨集文本频次（判歌词用）
    """
    srt_files = sorted([
        f for f in os.listdir(input_dir)
        if f.lower().endswith(('.srt', '.ass'))
    ])

    if not srt_files:
        return {'op_zh': None, 'ed_zh': None, 'op_texts': {}, 'ed_texts': {},
                'op_ja': None, 'ed_ja': None,
                'op_variants': Counter(), 'ed_variants': Counter()}

    # Collect OP/ED text from each episode
    op_variants = Counter()
    ed_variants = Counter()
    op_texts = {}
    ed_texts = {}

    for srt_file in srt_files:
        path = os.path.join(input_dir, srt_file)
        cues = parse_subtitles(path, mark_garbled=False)

        if not cues:
            continue

        # Determine total duration from last cue
        last_end = _time_to_seconds(cues[-1].get('end', cues[-1].get('start', '00:00:00')))
        total_duration = last_end + 10  # add padding

        ep_op = []
        ep_ed = []

        for i, cue in enumerate(cues):
            start_s = _time_to_seconds(cue.get('start', '00:00:00'))
            end_s = _time_to_seconds(cue.get('end', cue.get('start', '00:00:00')))
            region = _is_oped_region(start_s, end_s, total_duration)
            text = cue.get('text', '').strip()

            if not text or len(text) < 2:
                continue

            if region == 'OP':
                op_variants[text] += 1
                ep_op.append((i, text))
            elif region == 'ED':
                ed_variants[text] += 1
                ep_ed.append((i, text))

        if ep_op:
            op_texts[srt_file] = ep_op
        if ep_ed:
            ed_texts[srt_file] = ep_ed

    # Pick canonical Japanese form (most frequent)
    op_ja = _pick_oped_canonical(op_variants)
    ed_ja = _pick_oped_canonical(ed_variants)

    # Translate canonical OP/ED once
    op_zh = None
    ed_zh = None

    if op_ja and not dry_run:
        print(f'  [oped] Translating OP canonical ({len(op_variants)} variants across {len(op_texts)} eps)...',
              file=sys.stderr)
        result = _translate_batch([{'text': op_ja}], api_key, model, base_url, glossary_str='')
        if result and len(result) > 0:
            op_zh = result[0]
            print(f'  [oped] OP: {op_ja[:40]}... → {op_zh[:40]}...', file=sys.stderr)

    if ed_ja and not dry_run:
        print(f'  [oped] Translating ED canonical ({len(ed_variants)} variants across {len(ed_texts)} eps)...',
              file=sys.stderr)
        result = _translate_batch([{'text': ed_ja}], api_key, model, base_url, glossary_str='')
        if result and len(result) > 0:
            ed_zh = result[0]
            print(f'  [oped] ED: {ed_ja[:40]}... → {ed_zh[:40]}...', file=sys.stderr)

    return {'op_zh': op_zh, 'ed_zh': ed_zh,
            'op_texts': op_texts, 'ed_texts': ed_texts,
            'op_ja': op_ja, 'ed_ja': ed_ja,
            'op_variants': op_variants, 'ed_variants': ed_variants}


def _is_lyric(text, canonical_ja, within_file, cross_episodes):
    """这句文本是否算「歌词」，可以整条覆写成预译的 OP/ED 中文。

    三个信号，命中任一即是：
      · 等于 canonical（该窗口里出现最多的那句）
      · 同一集窗口内重复 ≥2 次 —— Whisper 在音乐段的幻觉重复
      · 跨集出现 ≥2 次 —— 主题歌歌词（每集都会唱）

    单独一句、只出现一次、别的集也没有的文本 → **不是歌词**，多半是
    恰好落在窗口里的正片对白，必须留给正常翻译。
    """
    t = (text or '').strip()
    if not t:
        return False
    if canonical_ja and t == canonical_ja.strip():
        return True
    if within_file.get(t, 0) >= 2:
        return True
    return cross_episodes.get(t, 0) >= 2


def apply_oped_pre_replace(cues, srt_file, oped):
    """Pre-replace OP/ED **lyric** cues with pre-translated Chinese.

    ⚠️ 只覆盖判定为歌词的 cue（`_is_lyric`）。早先的实现把 OP/ED 时间窗
    内的 cue **无条件全部覆写**——窗口是固定 180s 的常量，OP 只有 90s 的
    作品里，窗口会一路吃进正片对白，把整段对白替换成同一句歌词中文。
    """
    replaced = 0

    for region, key_zh, key_texts, key_ja, key_var in (
            ('OP', 'op_zh', 'op_texts', 'op_ja', 'op_variants'),
            ('ED', 'ed_zh', 'ed_texts', 'ed_ja', 'ed_variants')):
        zh = oped.get(key_zh)
        entries = oped.get(key_texts) or {}
        if not zh or srt_file not in entries:
            continue
        canonical = oped.get(key_ja)
        cross = oped.get(key_var) or Counter()
        within = Counter(t for _, t in entries[srt_file])
        for idx, ja_text in entries[srt_file]:
            if idx >= len(cues):
                continue
            if cues[idx].get('text', '').strip() != ja_text:
                continue  # 预替换过的（专名）或已改动，跳过
            if not _is_lyric(ja_text, canonical, within, cross):
                continue
            cues[idx]['text'] = zh
            replaced += 1

    return replaced


# ═══════════════════════════════════════════════════════════════
# Proper noun pre-replace
# ═══════════════════════════════════════════════════════════════

def apply_noun_pre_replace(cues, ja_to_zh):
    """Pre-replace Japanese proper nouns with Chinese equivalents in cue text.

    Replaces full-word matches only (surrounded by word boundaries or
    Japanese punctuation).
    """
    if not ja_to_zh:
        return 0

    replaced = 0
    for cue in cues:
        text = cue.get('text', '')
        if not text:
            continue
        for ja_term, zh_term in ja_to_zh.items():
            if ja_term in text:
                text = text.replace(ja_term, zh_term)
                replaced += 1
        cue['text'] = text

    return replaced


# ═══════════════════════════════════════════════════════════════
# LLM API
# ═══════════════════════════════════════════════════════════════

def _call_llm(messages, api_key, model, base_url):
    """Call OpenAI-compatible chat API, return response text.

    ⚠️ 推理模型（deepseek-flash / deepseek-v4-pro）默认关闭思考。
    不关的话思考 token 会吃光 max_tokens 预算 → content 为空串 →
    `_translate_batch` 判整批失败 → 该批 10 条 cue 全部保留日文原文，
    且**不打印任何错误**（表现为 mojibake 式的"翻译没生效"）。
    """
    url = f'{base_url}/chat/completions'
    body = {
        'model': model,
        'messages': messages,
        'temperature': 0.3,
        'max_tokens': LLM_MAX_TOKENS,
    }
    if LLM_REASONING_EFFORT:
        body['reasoning_effort'] = LLM_REASONING_EFFORT
    data = json.dumps(body).encode('utf-8')

    req = urllib.request.Request(url, data=data)
    req.add_header('Content-Type', 'application/json')
    req.add_header('Authorization', f'Bearer {api_key}')

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            choice = result['choices'][0]
            content = choice['message'].get('content') or ''
            if not content:
                reasoning = choice['message'].get('reasoning_content') or ''
                print(f'  [translate] 空 content：finish_reason='
                      f'{choice.get("finish_reason")!r}, '
                      f'reasoning_content={len(reasoning)} 字'
                      + ('（思考 token 吃光 max_tokens，'
                         '把 LLM_REASONING_EFFORT 设为 none）'
                         if reasoning else ''),
                      file=sys.stderr)
            return content
    except Exception as e:
        print(f'  [translate] API error: {e}', file=sys.stderr)
        return None


def _parse_json_response(response):
    """Parse JSON array from LLM response, handling markdown code blocks
    and common JSON formatting errors."""
    if not response:
        return None

    strategies = []

    # Strategy 1: direct parse after stripping markdown fences
    cleaned = response.strip()
    if cleaned.startswith('```'):
        # Remove opening fence (```json or just ```)
        cleaned = re.sub(r'^```[\w-]*\s*', '', cleaned)
        # Remove closing fence
        cleaned = re.sub(r'\s*```\s*$', '', cleaned)
    strategies.append(cleaned)

    # Strategy 2: extract first JSON array via regex
    m = re.search(r'\[.*\]', response, re.DOTALL)
    if m:
        strategies.append(m.group(0))

    for s in strategies:
        try:
            result = json.loads(s)
            if isinstance(result, list):
                # Format 1: array of strings ["trans1", "trans2"]
                if all(isinstance(x, str) for x in result):
                    return result
                # Format 2: array of objects [{"index":1, "translation":"..."}]
                if all(isinstance(x, dict) for x in result):
                    texts = []
                    for obj in result:
                        t = obj.get('translation') or obj.get('text') or obj.get('zh') or ''
                        texts.append(str(t))
                    if any(texts):
                        return texts
        except (json.JSONDecodeError, TypeError):
            continue

    # Strategy 3: try to fix trailing commas before ] or }
    for s in strategies:
        try:
            fixed = re.sub(r',\s*([}\]])', r'\1', s)
            result = json.loads(fixed)
            if isinstance(result, list) and all(isinstance(x, str) for x in result):
                return result
        except (json.JSONDecodeError, TypeError):
            continue

    # Strategy 4: handle truncated JSON (missing closing bracket)
    for s in strategies:
        if s.rstrip().endswith('"') and '[' in s and ']' not in s[s.rfind('['):]:
            try:
                result = json.loads(s + ']')
                if isinstance(result, list) and all(isinstance(x, str) for x in result):
                    return result
            except (json.JSONDecodeError, TypeError):
                continue
        # Also handle trailing comma before truncation
        if s.rstrip().endswith(',') and '[' in s:
            try:
                result = json.loads(s.rstrip().rstrip(',') + ']')
                if isinstance(result, list) and all(isinstance(x, str) for x in result):
                    return result
            except (json.JSONDecodeError, TypeError):
                continue

    print(f'  [translate] Failed to parse JSON (len={len(response)}): {response[:500]}', file=sys.stderr)
    # Debug: check for common issues
    if not response.strip().startswith('['):
        print(f'  [translate] Response does not start with "[", starts with: {repr(response[:50])}', file=sys.stderr)
    if not response.strip().endswith(']'):
        print(f'  [translate] Response does not end with "]", ends with: {repr(response[-50:])}', file=sys.stderr)
    return None


def _translate_batch(cues, api_key, model, base_url, glossary_str, ja_context=None, source_lang='ja'):
    """Translate one batch of cues. Returns list of translated text or None.

    Args:
        cues: list of cue dicts to translate
        ja_context: optional list of JA original cue dicts from preceding batch (pre-computed)
        source_lang: detected source language ('ja', 'ru', 'zh', 'other')
    """
    texts = [c['text'] if isinstance(c, dict) else str(c) for c in cues]

    # Context: up to 3 JA original cues from previous batch (pre-computed, no dependency)
    context_before = ''
    if ja_context:
        recent = ja_context[-3:]
        context_before = '\n'.join(f'{i+1}. {c["text"]}' for i, c in enumerate(recent) if c.get('text'))

    targets = '\n'.join(f'{i+1}. {t}' for i, t in enumerate(texts) if t)

    user_msg = USER_TEMPLATE.format(
        context_before=context_before or '(无)',
        targets=targets,
        glossary=glossary_str or '(无)',
    )

    messages = [
        {'role': 'system', 'content': _build_system_prompt(source_lang)},
        {'role': 'user', 'content': user_msg},
    ]

    response = _call_llm(messages, api_key, model, base_url)
    if not response:
        return None

    return _parse_json_response(response)


# 模型照抄 prompt 时会把编号一起带回来（targets 是 "1. …\n2. …" 格式）
_ECHO_PREFIX_RE = re.compile(r'^\s*\d+\s*[.、)）]\s*')


def is_untranslated(src, out):
    """译文是否只是原文回显（含照抄带编号的 prompt 行）。

    不判这个的话，「1. どうか」会被 `out != src` 判成翻译成功——
    文本确实不同（多了编号），人看到却是整片日文。
    """
    if not out or not str(out).strip():
        return True
    s = _ECHO_PREFIX_RE.sub('', str(src).strip()).strip()
    o = _ECHO_PREFIX_RE.sub('', str(out).strip()).strip()
    return o == s


def bad_ratio(batch_cues, result):
    """一批里未翻译/回显条目的占比。result 为 None 时视为全失败。"""
    if not result:
        return 1.0
    n = len(batch_cues)
    if n == 0:
        return 0.0
    bad = sum(1 for j, c in enumerate(batch_cues)
              if is_untranslated(c.get('text', ''),
                                 result[j] if j < len(result) else None))
    return bad / n


# ═══════════════════════════════════════════════════════════════
# Single file translation
# ═══════════════════════════════════════════════════════════════

def translate_file(input_path, output_path, glossary_str, ja_to_zh,
                   api_key, model, base_url,
                   oped=None,
                   dry_run=False, source_lang=None,
                   extract_nouns=False, extract_dir=None):
    """Translate a single subtitle file (SRT or ASS).

    Args:
        extract_nouns: 翻译后调用专名提取（nouns.extractor），写 sidecar JSON。
        extract_dir: sidecar 输出目录（默认 temp/nouns）。

    Returns (total, translated, failed).
    """
    cues = parse_subtitles(input_path, mark_garbled=False)
    total = len(cues)
    fname = os.path.basename(input_path)

    # 快照原始日文（apply_noun_pre_replace 会原地改 cues，需在预替换前保存）
    ja_snapshot = [{'text': c.get('text', '')} for c in cues]

    if total == 0:
        print(f'  {fname}: 0 cues (empty)', file=sys.stderr)
        return 0, 0, 0

    # Auto-detect source language if not specified
    if source_lang is None:
        source_lang = _detect_source_lang(cues)
        print(f'  {fname}: detected source language = {source_lang}', file=sys.stderr)

    # Step 1: OP/ED pre-replace（只覆盖歌词，见 apply_oped_pre_replace）
    oped_replaced = 0
    if oped and (oped.get('op_zh') or oped.get('ed_zh')):
        oped_replaced = apply_oped_pre_replace(cues, fname, oped)
        if oped_replaced:
            print(f'  {fname}: OP/ED pre-replaced {oped_replaced} cues', file=sys.stderr)

    # Step 2: Proper noun pre-replace
    noun_replaced = apply_noun_pre_replace(cues, ja_to_zh)
    if noun_replaced:
        print(f'  {fname}: noun pre-replaced {noun_replaced} occurrences', file=sys.stderr)

    # Step 3: Batch translate remaining cues (intra-episode parallel)
    translated = 0
    failed = 0
    result_cues = list(cues)

    if dry_run:
        print(f'  {fname}: {total} cues (dry-run, no API calls)', file=sys.stderr)
        return total, 0, 0

    print(f'  {fname}: {total} cues (OP/ED: {oped_replaced}, nouns: {noun_replaced})',
          file=sys.stderr)

    # Build batch list with pre-computed JA context (no dependency on translation order)
    batches = []
    for i in range(0, total, BATCH_SIZE):
        batch_cues = cues[i:i + BATCH_SIZE]
        if len(batch_cues) < 1:
            continue
        # JA context: up to 3 cues immediately before this batch (original Japanese)
        ja_ctx = cues[max(0, i - 3):i]
        batches.append((i, batch_cues, ja_ctx))

    # Submit all batches in parallel
    def _translate_one(idx_batch):
        """Translate a single batch with retry. Returns (batch_idx, result_list_or_None)."""
        batch_idx, batch_cues, ja_ctx = idx_batch
        result = _translate_batch(batch_cues, api_key, model, base_url, glossary_str,
                                  ja_context=ja_ctx, source_lang=source_lang)
        if result is None:
            time.sleep(1)
            result = _translate_batch(batch_cues, api_key, model, base_url, glossary_str,
                                      ja_context=ja_ctx, source_lang=source_lang)
        # 整批回显/未翻译也重试一次：prompt 过长或语义混乱时，模型会直接
        # 把带编号的原文照抄回来。这类结果非 None、且与原文"不同"（多了编号），
        # 光看 None 判不出来。
        elif bad_ratio(batch_cues, result) > 0.5:
            time.sleep(1)
            retry = _translate_batch(batch_cues, api_key, model, base_url, glossary_str,
                                     ja_context=ja_ctx, source_lang=source_lang)
            if retry is not None and bad_ratio(batch_cues, retry) < bad_ratio(batch_cues, result):
                result = retry
        return batch_idx, batch_cues, result

    print(f'    [{len(batches)} batches in parallel]', end='', file=sys.stderr)

    with ThreadPoolExecutor(max_workers=min(len(batches), 16)) as executor:
        futures = {executor.submit(_translate_one, b): b[0] for b in batches}
        for future in as_completed(futures):
            batch_idx, batch_cues, result = future.result()
            for j, cue in enumerate(batch_cues):
                translated_text = result[j] if result and j < len(result) else None
                # 判据是「不是原文回显」，不是「和原文不同」——整批照抄成
                # "1. どうか" 也算「不同」，但那是失败，不能记成功写进输出。
                if not is_untranslated(cue['text'], translated_text):
                    result_cues[batch_idx + j]['text'] = translated_text
                    translated += 1
                else:
                    failed += 1
            # Progress: count completed futures
            done = len([f for f in futures if f.done()])
            pct = done * 100 // len(batches)
            print(f'\r    [{done}/{len(batches)} batches] {pct}%', end='', file=sys.stderr)

    print(file=sys.stderr)

    # Write output
    if not dry_run and translated > 0:
        os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
        write_subtitles(output_path, result_cues, template_path=input_path)

    # 专名提取（与翻译共用同一核心：nouns/extractor.extract_names）
    if extract_nouns and not dry_run and translated > 0:
        try:
            import nouns.extractor as _extractor
            stats = {}
            entities = _extractor.extract_names(
                ja_snapshot, result_cues, api_key=api_key, model=model,
                base_url=base_url, quiet=True, stats=stats)
            if extract_dir:
                os.makedirs(extract_dir, exist_ok=True)
                m = re.search(r'(\d{1,3})', fname)
                ep_id = f'EP{int(m.group(1)):03d}' if m else 'EP'
                out_path = os.path.join(extract_dir, f'extracted_{ep_id}.json')
                with open(out_path, 'w', encoding='utf-8') as f:
                    json.dump({'episode': ep_id, 'entities': entities,
                               'chunks': stats},
                              f, ensure_ascii=False, indent=2)
                print(f'  {fname}: 专名提取 {len(entities)} 实体 → '
                      f'{os.path.relpath(out_path)}', file=sys.stderr)
        except Exception as e:
            print(f'  {fname}: 专名提取失败（不影响翻译结果）: {e}',
                  file=sys.stderr)

    return total, translated + oped_replaced, failed


# ═══════════════════════════════════════════════════════════════
# Episode filtering (same logic as run_all.py)
# ═══════════════════════════════════════════════════════════════

def _parse_episodes(arg):
    """Parse --episodes argument into a list of episode tokens.

    Supports: 'EP001-EP010', 'EP001,EP005', '1-10', '1,5', 'SP01,SP02', None (all)
    """
    if not arg:
        return None
    return parse_episode_spec(arg) or None


def _filter_by_start(files, start_from):
    """Only keep files whose episode token is >= start_from.

    Token 精确命中时按列表位置截取；否则退回同类 token 的字符串比较
    （token 零填充，同类型内字典序即数值序）。
    """
    if not start_from:
        return files
    st = parse_episode_spec(str(start_from))
    if not st:
        return files
    start_ep = st[0]
    exact = [i for i, f in enumerate(files) if episode_token(f) == start_ep]
    if exact:
        return files[exact[0]:]
    kind = re.match(r'[A-Za-z]+', start_ep).group(0)
    return [f for f in files
            if episode_token(f).startswith(kind) and episode_token(f) >= start_ep]


# ═══════════════════════════════════════════════════════════════
# Directory batch processing
# ═══════════════════════════════════════════════════════════════

def translate_dir(input_dir, output_dir, glossary_str, ja_to_zh,
                  api_key, model, base_url, dry_run=False, source_lang=None,
                  skip_oped=False, episodes=None, start_from=None,
                  extract_nouns=False, extract_dir=None):
    """Translate all SRT/ASS files in a directory."""
    if not os.path.isdir(input_dir):
        print(f'ERROR: {input_dir} not found', file=sys.stderr)
        sys.exit(1)

    srt_files = sorted([
        f for f in os.listdir(input_dir)
        if f.lower().endswith(('.srt', '.ass'))
    ])

    if not srt_files:
        print(f'ERROR: No SRT/ASS files found in {input_dir}', file=sys.stderr)
        sys.exit(1)

    print(f'{len(srt_files)} files found', file=sys.stderr)

    # Episode filter — token-based, so EP### and SP## both work
    if episodes:
        ep_set = set(episodes)
        srt_files = [f for f in srt_files if episode_token(f) in ep_set]
    if start_from:
        srt_files = _filter_by_start(srt_files, start_from)

    if not srt_files:
        print('No files to translate after filtering.', file=sys.stderr)
        return

    print(f'{len(srt_files)} files to translate', file=sys.stderr)

    # Phase 0: Collect & pre-translate OP/ED across all episodes
    oped = None
    if not skip_oped:
        print('[oped] Scanning OP/ED across episodes...', file=sys.stderr)
        oped = collect_oped_across_episodes(
            input_dir, api_key, model, base_url, dry_run
        )
        if oped.get('op_zh'):
            print(f'  [oped] OP pre-translated: '
                  f'{len(oped["op_texts"])} episodes', file=sys.stderr)
        if oped.get('ed_zh'):
            print(f'  [oped] ED pre-translated: '
                  f'{len(oped["ed_texts"])} episodes', file=sys.stderr)
    else:
        print('[oped] Skipped (--skip-oped)', file=sys.stderr)

    # Phase 1: Translate each file
    grand_total = grand_translated = grand_failed = 0

    for srt_file in srt_files:
        input_path = os.path.join(input_dir, srt_file)
        output_path = os.path.join(output_dir, srt_file)

        total, translated, failed = translate_file(
            input_path, output_path, glossary_str, ja_to_zh,
            api_key, model, base_url,
            oped=oped,
            dry_run=dry_run, source_lang=source_lang,
            extract_nouns=extract_nouns, extract_dir=extract_dir,
        )

        grand_total += total
        grand_translated += translated
        grand_failed += failed

    # Summary
    print(f'\n{"[DRY RUN] " if dry_run else ""}'
          f'Done: {grand_translated}/{grand_total} cues translated '
          f'({grand_failed} failed) across {len(srt_files)} files',
          file=sys.stderr)


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def main():
    global BATCH_SIZE

    parser = argparse.ArgumentParser(
        description='SRT/ASS batch translator via OpenAI-compatible API '
                    '(auto-detects source language: ja/ru/zh)')
    parser.add_argument('--input', help='Single SRT/ASS file to translate')
    parser.add_argument('--output', help='Output file path (single file mode)')
    parser.add_argument('--input-dir', help='Directory of SRT/ASS files to translate')
    parser.add_argument('--output-dir', default='中文翻译后',
                        help='Output directory (default: 中文翻译后/)')
    parser.add_argument('--glossary', help='Path to proper-nouns.md (ja→zh mapping, legacy)')
    parser.add_argument('--mappings', help='Path to noun_mappings.json (ja→zh, preferred)')
    parser.add_argument('--model', default=LLM_MODEL_DEFAULT,
                        help=f'LLM model (default: {LLM_MODEL_DEFAULT})')
    parser.add_argument('--base-url', default=LLM_BASE_URL_DEFAULT,
                        help='API base URL')
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview only, no API calls')
    parser.add_argument('--batch', type=int, default=BATCH_SIZE,
                        help=f'Cues per batch (default: {BATCH_SIZE})')
    parser.add_argument('--source-lang', choices=['ja', 'ru', 'zh', 'other'],
                        default=None,
                        help='Source language (default: auto-detect from cues)')
    parser.add_argument('--skip-oped', action='store_true',
                        help='Skip OP/ED detection and pre-translation')
    parser.add_argument('--episodes', '-e', default=None,
                        help='Episodes to translate: EP001-EP010, EP001,EP005, 1-10, 1,5')
    parser.add_argument('--start-from', default=None,
                        help='Start from this episode (e.g., EP050 or 50)')
    parser.add_argument('--extract-nouns', action='store_true',
                        help='翻译后提取专名实体（nouns/extractor.py 核心），写 sidecar JSON')
    parser.add_argument('--extract-dir', default='temp/nouns',
                        help='专名提取 sidecar 输出目录（默认 temp/nouns）')
    args = parser.parse_args()

    # API key（LLM_API_KEY 优先，回退到 POLISH_API_KEY）
    api_key = LLM_API_KEY
    if not api_key and not args.dry_run:
        print('ERROR: LLM_API_KEY not set (also tried POLISH_API_KEY).', file=sys.stderr)
        print('  export LLM_API_KEY="sk-..."', file=sys.stderr)
        print('  Or use --dry-run to preview.', file=sys.stderr)
        sys.exit(1)

    # Model and base URL from env vars (with fallback to CLI defaults)
    model = args.model or LLM_MODEL or LLM_MODEL_DEFAULT
    base_url = args.base_url or LLM_BASE_URL or LLM_BASE_URL_DEFAULT

    # Load glossary/mappings (--mappings preferred, --glossary as fallback)
    glossary_str = ''
    ja_to_zh = {}
    if args.mappings:
        glossary_str, ja_to_zh = load_mappings(args.mappings)
        if ja_to_zh:
            print(f'  [mappings] {len(ja_to_zh)} ja→zh mappings loaded', file=sys.stderr)
    elif args.glossary:
        glossary_str, ja_to_zh = load_glossary(args.glossary)
        if ja_to_zh:
            print(f'  [glossary] {len(ja_to_zh)} ja→zh mappings loaded', file=sys.stderr)

    # Single file mode
    if args.input:
        if not args.output:
            base = os.path.splitext(os.path.basename(args.input))[0]
            args.output = os.path.join(args.output_dir, f'{base}.srt')
        translate_file(args.input, args.output, glossary_str, ja_to_zh,
                       api_key, model, base_url, dry_run=args.dry_run,
                       source_lang=args.source_lang,
                       extract_nouns=args.extract_nouns,
                       extract_dir=args.extract_dir)
    # Directory mode
    elif args.input_dir:
        episodes = _parse_episodes(args.episodes) if args.episodes else None
        translate_dir(args.input_dir, args.output_dir, glossary_str, ja_to_zh,
                      api_key, model, base_url, dry_run=args.dry_run,
                      source_lang=args.source_lang,
                      skip_oped=args.skip_oped,
                      episodes=episodes,
                      start_from=args.start_from,
                      extract_nouns=args.extract_nouns,
                      extract_dir=args.extract_dir)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
