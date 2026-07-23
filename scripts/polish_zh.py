#!/usr/bin/env python3
"""中文字幕 AI 润色 — DeepSeek 批量去翻译腔。

读取修复后的中文 SRT，逐批送 DeepSeek 润色为自然口语，
同时保持专有名词（glossary）翻译一致。

Usage:
  # 单文件
  python polish_zh.py --input EP001.srt --output polished/EP001.srt

  # 批量目录
  python polish_zh.py --input-dir AI审查后/ --output-dir 中文润色后/
      --glossary reports/proper-nouns.md

  # 预览模式
  python polish_zh.py --input EP001.srt --dry-run

Setup:
  设置环境变量 DEEPSEEK_API_KEY（必需）
  可选 DEEPSEEK_MODEL（默认 deepseek-chat）
  可选 DEEPSEEK_BASE_URL（默认 https://api.deepseek.com/v1）
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request

import lib._path  # noqa: F401

from lib.whisper_utils import parse_srt, write_srt

# ═══════════════════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════════════════

DEFAULT_MODEL = 'deepseek-chat'
DEFAULT_BASE_URL = 'https://api.deepseek.com/v1'
BATCH_SIZE = 10   # cues per API call (tuned for ~$3.38 / 193 episodes)
DELAY = 1.0       # seconds between batches (rate limit safety)

SYSTEM_PROMPT = """你是动画字幕润色专家。将机翻中文字幕改写为自然口语，遵守：

1. **去翻译腔**：删除冗余主语（"我"、"你"在中文常可省略）、简化"是...的"结构、
   敬语过度的"请..."改成口语表达
2. **口语化**：书面词换口语词（"如何"→"怎么"、"是否"→"吗"、"迅速"→"快"）
3. **保持专有名词**：角色名、地名、组织名、术语不改变
4. **保持原意**：不改台词含义，只改表达方式
5. **保持字幕长度**：润色后不应明显变长或变短

输入是 10 条字幕（含上下文），输出每条润色后的字幕。
输出必须是严格的 JSON 数组，每个元素是润色后的字幕字符串。
不要输出任何 JSON 之外的内容。"""

USER_TEMPLATE = """上下文（前 3 条）：
{context_before}

本次要润色的字幕（10 条）：
{targets}

上下文（后 3 条）：
{context_after}

专有名词参考（不要改）：
{glossary}

请润色中间 10 条字幕，输出 JSON 数组："""


# ═══════════════════════════════════════════════════════════════
# Glossary loading
# ═══════════════════════════════════════════════════════════════

def load_glossary(path):
    """从 proper-nouns.md 提取专有名词列表。"""
    if not path or not os.path.exists(path):
        return ''
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    # 提取表格中第一列（日语/中文术语）
    terms = set()
    for line in content.split('\n'):
        line = line.strip()
        if line.startswith('|') and not line.startswith('|--') and not line.startswith('| 日语'):
            cells = [c.strip() for c in line.split('|')]
            if len(cells) >= 2 and cells[1]:
                term = cells[1]
                if len(term) >= 2:
                    terms.add(term)
    if not terms:
        return ''
    return ', '.join(sorted(terms))


# ═══════════════════════════════════════════════════════════════
# DeepSeek API
# ═══════════════════════════════════════════════════════════════

def _call_deepseek(messages, api_key, model, base_url):
    """Call DeepSeek chat API, return response text."""
    url = f'{base_url}/chat/completions'
    body = {
        'model': model,
        'messages': messages,
        'temperature': 0.3,
        'max_tokens': 2048,
    }
    data = json.dumps(body).encode('utf-8')

    req = urllib.request.Request(url, data=data)
    req.add_header('Content-Type', 'application/json')
    req.add_header('Authorization', f'Bearer {api_key}')

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            return result['choices'][0]['message']['content']
    except Exception as e:
        print(f'  [polish] API error: {e}', file=sys.stderr)
        return None


def polish_batch(cues, glossary_str, api_key, model, base_url):
    """Polish one batch of 10 cues. Returns list of polished text or None."""
    if len(cues) != BATCH_SIZE:
        # Pad with empty strings if needed
        cues = list(cues) + [''] * (BATCH_SIZE - len(cues))

    # Build context from cue texts
    texts = [c['text'] if isinstance(c, dict) else str(c) for c in cues]
    context_before = '\n'.join(f'{i+1}. {t}' for i, t in enumerate(texts[:3]) if t)
    context_after = '\n'.join(f'{i+1}. {t}' for i, t in enumerate(texts[7:]) if t)
    targets = '\n'.join(f'{i+1}. {t}' for i, t in enumerate(texts[3:7]) if t)

    user_msg = USER_TEMPLATE.format(
        context_before=context_before or '(无)',
        context_after=context_after or '(无)',
        targets=targets,
        glossary=glossary_str or '(无)',
    )

    messages = [
        {'role': 'system', 'content': SYSTEM_PROMPT},
        {'role': 'user', 'content': user_msg},
    ]

    response = _call_deepseek(messages, api_key, model, base_url)
    if not response:
        return None

    # Parse JSON array from response
    try:
        # Strip markdown code blocks if present
        cleaned = response.strip()
        if cleaned.startswith('```'):
            cleaned = re.sub(r'^```\w*\n', '', cleaned)
            cleaned = re.sub(r'\n```$', '', cleaned)
        result = json.loads(cleaned)
        if isinstance(result, list) and all(isinstance(s, str) for s in result):
            return result
    except (json.JSONDecodeError, TypeError):
        pass

    print(f'  [polish] Failed to parse JSON from response: {response[:200]}',
          file=sys.stderr)
    return None


# ═══════════════════════════════════════════════════════════════
# Main batch processing
# ═══════════════════════════════════════════════════════════════

def polish_srt(input_path, output_path, glossary_str,
               api_key, model, base_url, dry_run=False):
    """Polish a single SRT file. Returns (total, polished, failed)."""
    cues = parse_srt(input_path, mark_garbled=False)
    total = len(cues)
    polished = 0
    failed = 0

    print(f'  {os.path.basename(input_path)}: {total} cues', file=sys.stderr)

    # Group into batches of BATCH_SIZE, preserving order
    result_cues = list(cues)
    batch_indices = []

    for i in range(0, total, BATCH_SIZE):
        batch = cues[i:i + BATCH_SIZE]
        batch_indices.append((i, batch))

    for batch_idx, (start_idx, batch) in enumerate(batch_indices):
        if len(batch) < 2:  # skip single-cue batches (no context)
            continue

        if dry_run:
            continue

        result = polish_batch(batch, glossary_str, api_key, model, base_url)
        if result:
            for j, polished_text in enumerate(result):
                if j < len(batch) and polished_text and polished_text != batch[j]['text']:
                    result_cues[start_idx + j]['text'] = polished_text
                    polished += 1
        else:
            failed += len(batch)

        # Progress
        done = (batch_idx + 1) * BATCH_SIZE
        pct = min(done, total) * 100 // total
        print(f'\r    [{min(done, total)}/{total}] {pct}%',
              end='', file=sys.stderr)

        if done < total:
            time.sleep(DELAY)

    print(file=sys.stderr)

    if not dry_run and polished > 0:
        os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
        write_srt(output_path, result_cues)

    return total, polished, failed


def polish_dir(input_dir, output_dir, glossary_str,
               api_key, model, base_url, dry_run=False):
    """Polish all SRT files in a directory."""
    if not os.path.isdir(input_dir):
        print(f'ERROR: {input_dir} not found', file=sys.stderr)
        sys.exit(1)

    srt_files = sorted(f for f in os.listdir(input_dir)
                       if f.endswith('.srt') and not f.startswith('._'))

    os.makedirs(output_dir, exist_ok=True)

    grand_total = grand_polished = grand_failed = 0
    for fname in srt_files:
        input_path = os.path.join(input_dir, fname)
        output_path = os.path.join(output_dir, fname)
        t, p, f = polish_srt(input_path, output_path, glossary_str,
                             api_key, model, base_url, dry_run)
        grand_total += t
        grand_polished += p
        grand_failed += f

    print(f'\n[polish] {grand_polished}/{grand_total} polished, '
          f'{grand_failed} failed', file=sys.stderr)

    # Cost estimate (DeepSeek: $0.27/M input, $1.10/M output)
    if grand_total > 0:
        batches = grand_total // BATCH_SIZE + 1
        est_input_tokens = batches * 800   # ~800 input tokens per batch
        est_output_tokens = batches * 200  # ~200 output tokens per batch
        est_cost = (est_input_tokens / 1e6 * 0.27 +
                    est_output_tokens / 1e6 * 1.10)
        print(f'[polish] Est. cost: ~${est_cost:.2f} '
              f'({batches} batches × {BATCH_SIZE} cues)', file=sys.stderr)

    return grand_polished > 0


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description='中文字幕 AI 润色 — DeepSeek 批量去翻译腔',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument('--input', help='Single SRT file to polish')
    src.add_argument('--input-dir', help='Directory of SRT files to polish')
    parser.add_argument('--output', help='Output SRT path (with --input)')
    parser.add_argument('--output-dir', default='中文润色后',
                        help='Output directory (with --input-dir, default: 中文润色后/)')
    parser.add_argument('--glossary', help='proper-nouns.md path for term consistency')
    parser.add_argument('--model', default=DEFAULT_MODEL,
                        help=f'DeepSeek model (default: {DEFAULT_MODEL})')
    parser.add_argument('--base-url', default=DEFAULT_BASE_URL,
                        help='API base URL')
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview only, no API calls')
    parser.add_argument('--batch', type=int, default=BATCH_SIZE,
                        help=f'Cues per batch (default: {BATCH_SIZE})')
    args = parser.parse_args()

    # API key
    api_key = os.environ.get('DEEPSEEK_API_KEY', '')
    if not api_key and not args.dry_run:
        print('ERROR: DEEPSEEK_API_KEY not set.', file=sys.stderr)
        print('Set it via environment variable or pass --dry-run to preview.',
              file=sys.stderr)
        sys.exit(1)

    # Glossary
    glossary_str = load_glossary(args.glossary) if args.glossary else ''

    # Single file mode
    if args.input:
        if not args.output:
            args.output = os.path.splitext(args.input)[0] + '_polished.srt'
        polish_srt(args.input, args.output, glossary_str,
                   api_key, args.model, args.base_url, args.dry_run)
    # Directory mode
    else:
        polish_dir(args.input_dir, args.output_dir, glossary_str,
                   api_key, args.model, args.base_url, args.dry_run)


if __name__ == '__main__':
    main()
