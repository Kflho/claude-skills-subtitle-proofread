#!/usr/bin/env python3
"""专名实体提取 —— 共享核心模块（重新设计的专名审查流程 · 提取层）。

从对齐的 日文原文 / 中文翻译 cue 对中提取专名实体，包含
Whisper 幻觉变体（ja 侧）与中文译法变体（zh 侧），供跨集聚合与统一。

小接口，深实现：
    extract_names(ja_cues, zh_cues) -> [实体, ...]

两个入口共用同一实现（"单独拿出来就能用"）：
  1. 翻译流程内  —— translate_srt.py --extract-nouns，每集翻译后自动调用
  2. 独立 CLI     —— python nouns/extractor.py ... 对已翻译文件单独运行

实体格式：
    {
      "ja_forms": ["アトム", "アットム"],      # 日文全变体（含幻觉）
      "zh_forms": ["阿童木", "阿托姆"],        # 中文全译法（含不一致）
      "count": 12,                             # 本集近似出现次数
      "samples": ["アトム、来てくれ！| 阿童木，过来！"]
    }

sidecar 含提取健康度（供续跑/恢复）：
    {
      "episode": "EP001",
      "entities": [...],
      "chunks": {"chunks_total": 3, "chunks_failed": 0}
    }
    chunks_failed > 0 表示该集有 chunk LLM 调用失败（Whisper 幻觉压制下
    可能漏实体），--resume 会重跑这些集；chunk 空结果（成功但无实体）不算失败。

续跑/恢复（防止并发限速导致 ~17% 集静默漏提取）：
  python nouns/extractor.py --ja-dir ... --zh-dir ... -o temp/nouns --resume
      # 跳过健康 sidecar，只跑缺失/失败集（上次中断后接着跑）
  python nouns/extractor.py --ja-dir ... --zh-dir ... -o temp/nouns --status
      # 只打印健康度报告（✅健康 / ⚠失败 / ⬜未提取），不调 LLM

Usage (standalone):
  python nouns/extractor.py --ja-dir 日语参考字幕 --zh-dir 260806 \
      -e EP001-EP005 -o temp/nouns
  python nouns/extractor.py --ja-dir 日语参考字幕 --zh-dir 260806 \
      --episodes EP151 --output temp/nouns
  python nouns/extractor.py --ja-dir 日语参考字幕 --zh-dir 260806 \
      -o temp/nouns --resume
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lib._path  # noqa: F401,E402

from lib.llm import call_chat  # noqa: E402
from lib.config import LLM_API_KEY, LLM_MODEL, LLM_BASE_URL  # noqa: E402
from lib.whisper_utils import parse_subtitles, extract_ep_number  # noqa: E402

DEFAULT_CHUNK_SIZE = 120   # 每批 cue 对数量（一次 LLM 调用）

EXTRACT_SYSTEM_PROMPT = (
    '你是字幕专有名词提取专家。下面是一集动画的「日文原文 | 中文翻译」字幕，'
    '按顺序逐条对齐。请提取这一集中出现的所有专有名词实体。\n'
    '\n'
    '专有名词实体包括：人名、地名、组织/机构名、机器人/机械/飞船名、星球名、'
    '作品/标题名、专有称谓（如"御茶水博士""弗兰肯"这类特定称呼）。\n'
    '不包括：普通名词、动词、语气词、通用称谓（单独的"博士""博士"不算，'
    '但作为专名一部分的"XX博士"算）。\n'
    '\n'
    '对每个实体输出：\n'
    '- ja_forms: 该实体的所有日文书写形式。必须包含反复出现的所有变体'
    '（含 Whisper 听写错误，如 アトム/アットム/オトム）。\n'
    '- zh_forms: 该实体的所有中文译法，含不一致变体（如 阿童木/阿托姆）。\n'
    '- count: 本批字幕中出现的大致次数（整数）。\n'
    '- sample: 1 条包含该实体的原文样例，格式"日文|中文"。\n'
    '\n'
    '规则：\n'
    '1. 同一实体只输出一个条目，所有变体合并进 ja_forms/zh_forms。\n'
    '2. 只提取专有名词，普通词宁缺毋滥（漏报优于误报）。\n'
    '3. 返回严格 JSON 数组，不要 Markdown，不要额外文字。\n'
)


def _time_to_seconds(ts):
    """'00:01:30,500' 或 '00:01:30.500' → 90.5。解析失败返回 None。"""
    t = str(ts).strip().replace(',', '.')
    try:
        h, m, rest = t.split(':')
        sec, _, ms = rest.partition('.')
        return int(h) * 3600 + int(m) * 60 + float(sec) + (float(ms or 0) / 1000)
    except (ValueError, AttributeError):
        return None


def _align_pairs(ja_cues, zh_cues):
    """把 ja/zh cue 对齐成 (ja_text, zh_text) 对。

    - cue 数相等 → 索引对齐（翻译流程 fused 模式是 1:1 映射，最准确）
    - cue 数不等 → 时间戳对齐（人工编辑过导致增删 cue 时，索引会漂移）
    """
    if len(ja_cues) == len(zh_cues):
        pairs = []
        for ja, zh in zip(ja_cues, zh_cues):
            jt = (ja.get('text') or '').strip()
            zt = (zh.get('text') or '').strip()
            if jt and zt:
                pairs.append((jt, zt))
        return pairs

    print(f'  [extractor] ja/zh cue 数不一致，改用时间戳对齐 '
          f'({len(ja_cues)} vs {len(zh_cues)})', file=sys.stderr)
    # 收集 ja 侧 (start_s, text)，按时间排序（通常已有序，保险排一次）
    ja_items = []
    for c in ja_cues:
        t = _time_to_seconds(c.get('start'))
        if t is not None and (c.get('text') or '').strip():
            ja_items.append((t, c['text'].strip()))
    ja_items.sort(key=lambda x: x[0])
    ja_starts = [x[0] for x in ja_items]

    import bisect
    pairs = []
    for zh in zh_cues:
        zt = _time_to_seconds(zh.get('start'))
        ztext = (zh.get('text') or '').strip()
        if zt is None or not ztext:
            continue
        # 二分找最接近的 ja start
        idx = bisect.bisect_left(ja_starts, zt)
        cands = []
        if idx > 0:
            cands.append(idx - 1)
        if idx < len(ja_items):
            cands.append(idx)
        best = None
        best_d = 1.5  # 容差 1.5 秒
        for ci in cands:
            d = abs(ja_items[ci][0] - zt)
            if d < best_d:
                best_d = d
                best = ja_items[ci][1]
        if best:
            pairs.append((best, ztext))
    return pairs


def _render_pairs(pairs):
    """把 cue 对渲染成 '序号. 日文|中文' 文本块。"""
    lines = []
    for i, (ja, zh) in enumerate(pairs, 1):
        lines.append(f'{i}. {ja} | {zh}')
    return '\n'.join(lines)


def _normalize_forms(forms):
    """去空、去首尾空白、去重、按原序保留。"""
    seen = set()
    out = []
    for f in forms or []:
        if not f:
            continue
        f = str(f).strip()
        if f and f not in seen:
            seen.add(f)
            out.append(f)
    return out


def _extract_chunk(pairs, api_key, model, base_url, chunk_no, chunk_total):
    """对一批 cue 对调用 LLM。

    Returns: (实体列表, 是否成功)。LLM 调用失败 → ([], False)；
    成功但无实体 → ([], True)。两者语义不同，供恢复流程区分。
    """
    user_msg = _render_pairs(pairs)
    messages = [
        {'role': 'system', 'content': EXTRACT_SYSTEM_PROMPT},
        {'role': 'user',
         'content': f'（第 {chunk_no}/{chunk_total} 批）\n\n{user_msg}'},
    ]
    response = call_chat(messages, api_key=api_key, model=model, base_url=base_url)
    if not response:
        return [], False
    arr = _extract_entities_from_response(response)
    return arr, True


def _extract_entities_from_response(response):
    """从 LLM 响应解析实体数组（容错），并做字段规范化。"""
    from lib.llm import extract_json_array
    arr = extract_json_array(response)
    if not isinstance(arr, list):
        return []
    entities = []
    for item in arr:
        if not isinstance(item, dict):
            continue
        ja = _normalize_forms(item.get('ja_forms'))
        zh = _normalize_forms(item.get('zh_forms'))
        if not ja and not zh:
            continue
        sample = item.get('sample') or item.get('samples') or ''
        if isinstance(sample, list):
            sample = sample[0] if sample else ''
        try:
            count = int(item.get('count') or 0)
        except (TypeError, ValueError):
            count = 0
        entities.append({
            'ja_forms': ja,
            'zh_forms': zh,
            'count': count,
            'samples': [str(sample)] if sample else [],
        })
    return entities


def _merge_entities(entities):
    """机械合并同批/跨批实体：共享任一 ja_form 或 zh_form 即视为同一实体。"""
    merged = []  # [{...sets...}]
    for ent in entities:
        ja = set(ent['ja_forms'])
        zh = set(ent['zh_forms'])
        target = None
        for m in merged:
            if (m['ja'] & ja) or (m['zh'] & zh):
                target = m
                break
        if target is None:
            merged.append({
                'ja': ja, 'zh': zh,
                'count': ent['count'],
                'samples': list(ent.get('samples', [])),
            })
        else:
            target['ja'] |= ja
            target['zh'] |= zh
            target['count'] += ent['count']
            if ent.get('samples'):
                target['samples'].append(ent['samples'][0])
    # 转回 JSON 友好结构
    out = []
    for m in merged:
        samples = m['samples']
        if len(samples) > 2:
            samples = samples[:2]
        out.append({
            'ja_forms': sorted(m['ja'], key=len, reverse=True),
            'zh_forms': sorted(m['zh'], key=len, reverse=True),
            'count': m['count'],
            'samples': samples,
        })
    return out


def extract_names(ja_cues, zh_cues, api_key=None, model=None, base_url=None,
                  chunk_size=DEFAULT_CHUNK_SIZE, quiet=False, stats=None):
    """从对齐的日文/中文 cue 列表提取专名实体。

    Args:
        ja_cues: 日文 cue 列表（parse_subtitles 输出，每项含 'text'）。
        zh_cues: 中文 cue 列表，与 ja_cues 按索引对齐。
        chunk_size: 每批 cue 对数量。
        stats: 可选 dict。非 None 时填入 {'chunks_total', 'chunks_failed'}，
            供调用方记录提取健康度（--resume 恢复依据）。

    Returns:
        实体列表（见模块 docstring）。LLM 全部失败时返回空列表。
    """
    pairs = _align_pairs(ja_cues, zh_cues)

    if not pairs:
        if stats is not None:
            stats.update({'chunks_total': 0, 'chunks_failed': 0})
        return []

    chunks = [pairs[i:i + chunk_size] for i in range(0, len(pairs), chunk_size)]
    chunk_total = len(chunks)
    all_entities = []
    chunks_failed = 0
    for ci, chunk in enumerate(chunks, 1):
        ents, ok = _extract_chunk(chunk, api_key, model, base_url,
                                  ci, chunk_total)
        if not ok:
            chunks_failed += 1
        all_entities.extend(ents)
        if not quiet:
            print(f'  [extractor] chunk {ci}/{chunk_total}: '
                  f'{len(ents)} 实体', file=sys.stderr)

    if stats is not None:
        stats.update({'chunks_total': chunk_total, 'chunks_failed': chunks_failed})
    return _merge_entities(all_entities)


# ═══════════════════════════════════════════════════════════════
# 集号 → 文件解析（ja/zh 目录可能用不同命名风格）
# ═══════════════════════════════════════════════════════════════

def resolve_episode_file(directory, ep):
    """按集号在目录中解析字幕文件。

    支持 EP001.srt 与 DVD 原名（- 001v2 - ...）两种风格。
    Returns: 文件绝对路径 或 None。
    """
    num = int(re.sub(r'\D', '', str(ep)))
    target = f'EP{num:03d}'
    if not os.path.isdir(directory):
        return None
    for fname in os.listdir(directory):
        if not fname.lower().endswith(('.srt', '.ass')):
            continue
        base = os.path.splitext(fname)[0]
        if base.upper() == target.upper():
            return os.path.join(directory, fname)
    # DVD 原名风格：- 001v2 - / - 001 - （extract_ep_number 对 v 后缀会漏）
    for fname in os.listdir(directory):
        if not fname.lower().endswith(('.srt', '.ass')):
            continue
        m = re.search(r'-\s*(\d{1,3})(?:[vV]\d+)?\s*-', fname)
        if m and int(m.group(1)) == num:
            return os.path.join(directory, fname)
    # DVD 原名风格：用 extract_ep_number 匹配
    for fname in os.listdir(directory):
        if not fname.lower().endswith(('.srt', '.ass')):
            continue
        if extract_ep_number(os.path.join(directory, fname)).upper() == target:
            return os.path.join(directory, fname)
    return None


def resolve_episode_pair(ja_dir, zh_dir, ep):
    """同时解析 ja/zh 两个目录的集文件。

    Returns: (ja_path, zh_path) 或 (None, None)。
    """
    ja_path = resolve_episode_file(ja_dir, ep)
    zh_path = resolve_episode_file(zh_dir, ep)
    return ja_path, zh_path


def extract_episode(ja_dir, zh_dir, ep, output_dir=None, api_key=None,
                    model=None, base_url=None, chunk_size=DEFAULT_CHUNK_SIZE):
    """独立入口：对单个集执行提取并可选写 sidecar JSON。

    Returns: (ep_id, entities)。ep_id 形如 'EP001'。
    """
    ja_path, zh_path = resolve_episode_pair(ja_dir, zh_dir, ep)
    if not ja_path or not zh_path:
        print(f'  [extractor] {ep}: 无法匹配 ja/zh 文件'
              f'（ja={bool(ja_path)}, zh={bool(zh_path)}）', file=sys.stderr)
        return None, []

    ep_id = extract_ep_number(ja_path)
    if ep_id == '???':
        ep_id = f'EP{int(re.sub(r"\\D", "", str(ep))):03d}'
    ja_cues = parse_subtitles(ja_path, mark_garbled=False)
    zh_cues = parse_subtitles(zh_path, mark_garbled=False)
    stats = {}
    entities = extract_names(ja_cues, zh_cues, api_key=api_key, model=model,
                             base_url=base_url, chunk_size=chunk_size,
                             stats=stats)

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        out_path = os.path.join(output_dir, f'extracted_{ep_id}.json')
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump({'episode': ep_id, 'entities': entities,
                       'chunks': stats},
                      f, ensure_ascii=False, indent=2)
        n_fail = stats.get('chunks_failed', 0)
        status = '⚠ 部分失败' if n_fail else 'OK'
        print(f'  [extractor] {ep_id}: {len(entities)} 实体 '
              f'({status}, chunk {n_fail}/{stats.get("chunks_total", 0)} 失败) → '
              f'{os.path.relpath(out_path)}', file=sys.stderr)

    return ep_id, entities


def _sidecar_health(path, min_entities=2):
    """判断现有 sidecar 是否健康（--resume / --status 依据）。

    Returns:
        'skip'   —— 健康，无需重跑
        'failed' —— 需要重跑（有 chunk 失败，或旧格式实体过少）
        None     —— sidecar 不存在（未提取）
    """
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            d = json.load(f)
    except Exception:
        return 'failed'
    chunks = d.get('chunks')
    if chunks is not None:
        return 'skip' if chunks.get('chunks_failed', 0) == 0 else 'failed'
    # 旧格式（升级前无 chunks 字段）：实体数作失败代理
    entities = d.get('entities', [])
    return 'skip' if len(entities) > min_entities else 'failed'


def _print_status(args, episodes):
    """--status：扫描 sidecar 打印健康度报告（不调 LLM）。"""
    done_healthy, done_failed, missing = [], [], []
    for ep in episodes:
        path = os.path.join(args.output, f'extracted_{ep}.json')
        health = _sidecar_health(path, min_entities=args.min_entities)
        if health == 'skip':
            done_healthy.append(ep)
        elif health == 'failed':
            done_failed.append(ep)
        else:
            missing.append(ep)
    total = len(episodes)
    print(f'[extractor] --status：共 {total} 集', file=sys.stderr)
    print(f'  ✅ 已提取健康：{len(done_healthy)}', file=sys.stderr)
    print(f'  ⚠ 已提取但失败/过少：{len(done_failed)}'
          + (f'（{",".join(done_failed)}）' if done_failed else ''),
          file=sys.stderr)
    print(f'  ⬜ 未提取：{len(missing)}'
          + (f'（{",".join(missing[:40])}{"…" if len(missing) > 40 else ""}）'
             if missing else ''), file=sys.stderr)


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def _parse_episodes(arg):
    """'EP001-EP005' / 'EP001,EP005' / '1-10' → ['EP001', ...]。"""
    if not arg:
        return None
    episodes = []
    for part in str(arg).split(','):
        part = part.strip()
        if '-' in part:
            a, b = part.split('-', 1)
            a = int(re.sub(r'\D', '', a))
            b = int(re.sub(r'\D', '', b))
            episodes.extend(f'EP{i:03d}' for i in range(a, b + 1))
        else:
            num = int(re.sub(r'\D', '', part))
            episodes.append(f'EP{num:03d}')
    return sorted(set(episodes))


def main():
    ap = argparse.ArgumentParser(
        description='专名实体提取（独立入口，与翻译流程内共用同一核心）')
    ap.add_argument('--ja-dir', required=True, help='日文源字幕目录')
    ap.add_argument('--zh-dir', required=True, help='中文翻译字幕目录')
    ap.add_argument('-e', '--episodes', help='集数范围，如 EP001-EP005 或 1,3,7')
    ap.add_argument('-o', '--output', default='temp/nouns',
                    help='sidecar 输出目录（默认 temp/nouns）')
    ap.add_argument('--chunk-size', type=int, default=DEFAULT_CHUNK_SIZE)
    ap.add_argument('--resume', action='store_true',
                    help='跳过健康 sidecar，只跑缺失/失败集（中断后续跑）')
    ap.add_argument('--status', action='store_true',
                    help='只打印健康度报告（✅健康/⚠失败/⬜未提取），不调 LLM')
    ap.add_argument('--min-entities', type=int, default=2,
                    help='旧 sidecar（无 chunks 字段）实体数≤此值视为失败')
    ap.add_argument('--api-key', default=LLM_API_KEY)
    ap.add_argument('--model', default=LLM_MODEL)
    ap.add_argument('--base-url', default=LLM_BASE_URL)
    args = ap.parse_args()

    episodes = _parse_episodes(args.episodes)
    if not episodes:
        # 全目录：扫描两目录交集的所有集号
        seen = {}
        for d in (args.ja_dir, args.zh_dir):
            for fname in os.listdir(d):
                if fname.lower().endswith(('.srt', '.ass')):
                    ep = extract_ep_number(os.path.join(d, fname))
                    if ep != '???':
                        seen[ep] = True
        episodes = sorted(seen)

    if args.status:
        _print_status(args, episodes)
        return

    if args.resume:
        skip, todo = [], []
        for ep in episodes:
            path = os.path.join(args.output, f'extracted_{ep}.json')
            if _sidecar_health(path, min_entities=args.min_entities) == 'skip':
                skip.append(ep)
            else:
                todo.append(ep)
        episodes = todo
        print(f'[extractor] --resume：跳过健康 {len(skip)} 集，'
              f'待跑 {len(episodes)} 集', file=sys.stderr)
        if not episodes:
            print('[extractor] 全部健康，无需提取', file=sys.stderr)
            return

    print(f'[extractor] 提取 {len(episodes)} 集', file=sys.stderr)

    ok = 0
    failed = []
    for ep in episodes:
        _, entities = extract_episode(
            args.ja_dir, args.zh_dir, ep, output_dir=args.output,
            api_key=args.api_key or None, model=args.model or None,
            base_url=args.base_url or None, chunk_size=args.chunk_size)
        if entities:
            ok += 1
        path = os.path.join(args.output, f'extracted_{ep}.json')
        if _sidecar_health(path, min_entities=args.min_entities) != 'skip':
            failed.append(ep)
    print(f'[extractor] 完成：{ok}/{len(episodes)} 集有提取结果', file=sys.stderr)
    if failed:
        print(f'[extractor] ⚠ 仍有失败 {len(failed)} 集'
              f'（重跑 --resume 可重试）：{",".join(failed)}',
              file=sys.stderr)


if __name__ == '__main__':
    main()
