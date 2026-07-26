#!/usr/bin/env python3
"""API-based OP/ED boundary detection using LLM.

Replaces hardcoded OP_BOUNDARY_SEC / ED_BOUNDARY_SEC with semantic analysis
of cue patterns.  LLM understands where blank/dense-music regions end and
continuous dialogue begins — something heuristic rules cannot do reliably.

Shared module used by both oped_fixer.py (unifying existing text) and
oped_fill.py (filling blank cues).
"""

import json
import os
import re
import sys
import urllib.request

import lib._path  # noqa: F401 — ensure scripts/ on sys.path
from lib.config import LLM_API_KEY, LLM_MODEL, LLM_BASE_URL, LLM_MODEL_DEFAULT, LLM_BASE_URL_DEFAULT
from lib.subtitle_io import read_subtitles  # unified SRT + ASS parser


# ── Constants ────────────────────────────────────────────────────

# How many episodes to sample for boundary detection
DEFAULT_SAMPLE_COUNT = 5

# How many seconds from the start / end to send to the LLM
DEFAULT_SCAN_WINDOW = 240  # 4 minutes — covers cold opens, long OPs, previews

# Fallback boundaries when API is unavailable
FALLBACK_OP_SEC = 180
FALLBACK_ED_SEC = 180


# ── Cue extraction ───────────────────────────────────────────────

def _extract_cues_for_boundary(srt_dir: str, sample_count: int = DEFAULT_SAMPLE_COUNT):
    """Extract cue lists from sample episodes for boundary detection.

    Returns list of dicts: [{fname, total_duration_s, head_cues, tail_cues}]
    where head_cues/tail_cues are lists of (start_s, text) tuples.
    """
    sub_files = sorted([
        f for f in os.listdir(srt_dir)
        if f.lower().endswith(('.srt', '.ass'))
    ])
    if not sub_files:
        return []

    # Sample evenly
    if len(sub_files) <= sample_count:
        sampled = sub_files
    else:
        step = len(sub_files) / sample_count
        sampled = [sub_files[int(i * step)] for i in range(sample_count)]

    episodes = []
    for fname in sampled:
        fpath = os.path.join(srt_dir, fname)
        cues = list(read_subtitles(fpath, mark_garbled=False))
        if not cues:
            episodes.append({'fname': fname, 'total_duration_s': 0,
                             'head_cues': [], 'tail_cues': []})
            continue

        max_end = max(c['end_s'] for c in cues)
        head_start = max(0, max_end - DEFAULT_SCAN_WINDOW)

        head_cues = [
            (c['start'], c['text'].strip())
            for c in cues
            if c['start_s'] < DEFAULT_SCAN_WINDOW
        ]
        tail_cues = [
            (c['start'], c['text'].strip())
            for c in cues
            if c['start_s'] >= head_start
        ]

        episodes.append({
            'fname': fname,
            'total_duration_s': max_end,
            'head_cues': head_cues,
            'tail_cues': tail_cues,
        })

    return episodes


# ── Prompt building ──────────────────────────────────────────────

def _format_cue_list(cues: list) -> str:
    """Format a cue list for the LLM prompt."""
    if not cues:
        return "  (无字幕行)\n"
    lines = []
    for timestamp, text in cues:
        text_display = text if text else "「空白」"
        lines.append(f"  [{timestamp}] {text_display}")
    return '\n'.join(lines)


def _build_boundary_prompt(episodes: list, lang: str = 'auto') -> str:
    """Build the LLM prompt for boundary detection."""
    lang_hint = ''
    if lang == 'zh':
        lang_hint = '（字幕为中文翻译）'
    elif lang == 'ja':
        lang_hint = '（字幕为日语）'

    parts = []
    parts.append(
        "你是一个动画字幕分析助手。以下是同一部动画若干集的字幕时间轴片段。\n"
        f"{lang_hint}\n"
        "「空白」表示该时间点有字幕行但没有文本内容。\n"
        "(无字幕行) 表示该时间段完全没有字幕行。\n"
        "\n"
        "请分析每集的开头和结尾，判断：\n"
        "1. OP（片头曲）的开始和结束时间（精确到秒）\n"
        "2. ED（片尾曲）的开始和结束时间（精确到秒）\n"
        "3. 下集预告的开始和结束时间（如果存在）\n"
        "4. 任何异常（某集无OP、ED被剪短、特殊回等）\n"
        "\n"
        "判断方法：\n"
        "- 连续空白行区域 → 可能是器乐段落（无对白无歌词）\n"
        "- 零散短文本 → 可能是歌词语音碎片\n"
        "- 连续有意义的对话出现 → 正片开始\n"
        "- 结尾：歌词/空白区结束后出现「次回」「下集」「予告」等 → 预告\n"
        "- 跨集对比：同一时间位置的文本是否相似 → 歌词\n"
    )

    # Per-episode data
    for ep in episodes:
        parts.append(f"\n{'='*60}")
        parts.append(f"=== {ep['fname']} (总时长 {ep['total_duration_s']:.0f}s) ===\n")
        parts.append(f"--- 开头 (前{DEFAULT_SCAN_WINDOW}秒) ---")
        parts.append(_format_cue_list(ep['head_cues']))
        parts.append(f"\n--- 结尾 (后{DEFAULT_SCAN_WINDOW}秒) ---")
        parts.append(_format_cue_list(ep['tail_cues']))

    parts.append(f"\n{'='*60}")
    parts.append("\n请以 JSON 格式输出分析结果：")
    parts.append("""```json
{
  "op": {
    "start_s": 0,
    "end_s": 90,
    "confidence": "high"
  },
  "ed": {
    "start_s": 1380,
    "end_s": 1440,
    "confidence": "high"
  },
  "preview": {
    "start_s": 1440,
    "end_s": 1500,
    "exists": true
  },
  "per_episode_notes": {
    "EP001.srt": "正常",
    "EP050.srt": "无OP，直接进入正片"
  },
  "warnings": [
    "EP100之后ED被剪短，只有30秒"
  ],
  "overall_confidence": "high"
}
```

规则：
- 时间以秒为单位（浮点数）
- confidence: high/medium/low
- preview.exists: false 表示没有下集预告
- per_episode_notes: 只标注异常的集，正常的可省略
- warnings: 全局注意事项
- op.start_s 如果不是从 0 开始（冷开场），标注实际开始时间
- ed.end_s 应该是预告结束的时间（如果预告存在），否则是 ED 结束时间
- 保守估计：宁可边界偏小（少覆盖）也不要偏大（误伤正片对白）
""")

    return '\n'.join(parts)


# ── API call ─────────────────────────────────────────────────────

def _call_llm(messages: list, api_key: str, model: str, base_url: str) -> str | None:
    """Call OpenAI-compatible chat API, return response text."""
    url = f'{base_url}/chat/completions'
    body = {
        'model': model,
        'messages': messages,
        'temperature': 0.1,
        'max_tokens': 4096,
    }
    data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(url, data=data)
    req.add_header('Content-Type', 'application/json')
    req.add_header('Authorization', f'Bearer {api_key}')

    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            return result['choices'][0]['message']['content']
    except Exception as e:
        print(f'  [oped-detect] API error: {e}', file=sys.stderr)
        return None


def _parse_json_response(response: str) -> dict | None:
    """Parse JSON from LLM response, handling markdown code blocks."""
    if not response:
        return None

    # Strategy 1: strip markdown fences
    cleaned = response.strip()
    if cleaned.startswith('```'):
        cleaned = re.sub(r'^```[\w-]*\s*', '', cleaned)
        cleaned = re.sub(r'\s*```\s*$', '', cleaned)

    # Strategy 2: extract first JSON object via regex
    strategies = [cleaned]
    m = re.search(r'\{.*\}', response, re.DOTALL)
    if m:
        strategies.append(m.group(0))

    for s in strategies:
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            continue

    print('  [oped-detect] Could not parse JSON from LLM response', file=sys.stderr)
    return None


# ── Main API ─────────────────────────────────────────────────────

def detect_boundaries(srt_dir: str, *,
                      api_key: str = None,
                      model: str = None,
                      base_url: str = None,
                      sample_count: int = DEFAULT_SAMPLE_COUNT,
                      lang: str = 'auto',
                      dry_run: bool = False) -> dict:
    """Detect OP/ED/preview boundaries using LLM analysis of cue patterns.

    Args:
        srt_dir: directory containing SRT/ASS files
        api_key: LLM API key (defaults to LLM_API_KEY env var)
        model: LLM model (defaults to LLM_MODEL env var or LLM_MODEL_DEFAULT)
        base_url: LLM API base URL (defaults to LLM_BASE_URL env var or default)
        sample_count: number of episodes to sample (default 5)
        lang: target language hint (auto/zh/ja)
        dry_run: if True, print prompt but don't call API

    Returns:
        {
            'op_start_s': float,
            'op_end_s': float,
            'ed_start_s': float,
            'ed_end_s': float,
            'preview_end_s': float or None,
            'confidence': 'high'|'medium'|'low'|'fallback',
            'per_episode_notes': {...},
            'warnings': [...],
        }
    """
    episodes = _extract_cues_for_boundary(srt_dir, sample_count)
    if not episodes:
        return _fallback_boundaries('No SRT files found')

    if dry_run:
        prompt = _build_boundary_prompt(episodes, lang)
        print(f'[oped-detect] DRY RUN — would send {len(prompt)} chars to LLM',
              file=sys.stderr)
        return _fallback_boundaries('dry-run')

    # Resolve API params
    _api_key = api_key or LLM_API_KEY
    _model = model or LLM_MODEL or LLM_MODEL_DEFAULT
    _base_url = base_url or LLM_BASE_URL or LLM_BASE_URL_DEFAULT

    if not _api_key:
        print('[oped-detect] LLM_API_KEY not set — using fallback boundaries',
              file=sys.stderr)
        return _fallback_boundaries('No API key')

    prompt = _build_boundary_prompt(episodes, lang)
    messages = [{'role': 'user', 'content': prompt}]

    print(f'[oped-detect] Sending {len(prompt)} chars to LLM ({_model})...',
          file=sys.stderr)
    response = _call_llm(messages, _api_key, _model, _base_url)

    if not response:
        return _fallback_boundaries('API call failed')

    result = _parse_json_response(response)
    if not result:
        return _fallback_boundaries('Failed to parse LLM response')

    # Normalize to standard format
    return _normalize_result(result)


def _fallback_boundaries(reason: str = '') -> dict:
    """Return conservative fallback boundaries when API is unavailable."""
    if reason:
        print(f'[oped-detect] Fallback ({reason}): OP={FALLBACK_OP_SEC}s, ED={FALLBACK_ED_SEC}s',
              file=sys.stderr)
    return {
        'op_start_s': 0.0,
        'op_end_s': float(FALLBACK_OP_SEC),
        'ed_start_s': None,  # unknown without file duration
        'ed_end_s': None,
        'preview_end_s': None,
        'confidence': 'fallback',
        'per_episode_notes': {},
        'warnings': [f'Using fallback boundaries: {reason}'],
    }


def _normalize_result(raw: dict) -> dict:
    """Normalize LLM output to standard format, filling in defaults."""
    op = raw.get('op', {})
    ed = raw.get('ed', {})
    preview = raw.get('preview', {})

    return {
        'op_start_s': float(op.get('start_s', 0)),
        'op_end_s': float(op.get('end_s', FALLBACK_OP_SEC)),
        'ed_start_s': float(ed['start_s']) if ed.get('start_s') is not None else None,
        'ed_end_s': float(ed['end_s']) if ed.get('end_s') is not None else None,
        'preview_end_s': (float(preview['end_s'])
                          if preview.get('exists') and preview.get('end_s') is not None
                          else None),
        'confidence': raw.get('overall_confidence', 'medium'),
        'per_episode_notes': raw.get('per_episode_notes', {}),
        'warnings': raw.get('warnings', []),
    }


# ── CLI (for standalone testing) ─────────────────────────────────

if __name__ == '__main__':
    import argparse

    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        description='API-based OP/ED boundary detection')
    parser.add_argument('srt_dir', help='Directory containing SRT/ASS files')
    parser.add_argument('--sample', type=int, default=DEFAULT_SAMPLE_COUNT,
                        help=f'Number of episodes to sample (default: {DEFAULT_SAMPLE_COUNT})')
    parser.add_argument('--lang', default='auto',
                        help='Target language hint (auto/zh/ja)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print prompt without calling API')
    parser.add_argument('--output', '-o', help='Save result to JSON file')
    args = parser.parse_args()

    result = detect_boundaries(
        args.srt_dir,
        sample_count=args.sample,
        lang=args.lang,
        dry_run=args.dry_run,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f'\n[oped-detect] → {args.output}', file=sys.stderr)
