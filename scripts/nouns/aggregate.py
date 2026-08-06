#!/usr/bin/env python3
"""跨集专名聚合 —— 增量 running-map 合并（重新设计的专名审查流程 · 聚合层）。

把新一集的专名实体（extractor.py 产物）增量合并进 running-map：

    noun_map.json = {
      "标准译名": {
        "ja_forms": ["アトム", "アットム"],        # 日文全变体（含幻觉）
        "zh_variants": ["阿托姆"],                  # 非标准中文译法
        "episodes": ["EP001", "EP002"],             # 出现的集
        "scope": "auto",                            # auto|global|per_episode
        "source": "extract"                         # seed=来自旧映射表
      }, ...
    }

合并策略（脚本只做机械合并，决策由 AI 通过聚合调用做出）：
  1. 机械预合并 —— 新实体与某标准名共享任一 ja_form/zh_form → 直接并入
  2. LLM 聚合 —— 剩余无重叠实体，交给 LLM 判定 merge(并入某标准名) /
     new(新建标准名) / ignore(不是专名)。merge 还能识别"异形但同义"的
     跨集变体（如 お茶の水 与 御茶ノ水 实为同一实体）。

scope 语义（apply_map 使用）：
  auto  → episodes ≥ 2 → global；= 1 → per_episode
  global → 应用时跨全部分集统一
  per_episode → 仅应用在该实体出现的集

Usage:
  # 初始化 map（可选：从旧 ja→zh 映射表 seed 已知标准名）
  python nouns/aggregate.py --map temp/noun_map.json --seed temp/noun_mappings.json --init

  # 增量合并一集
  python nouns/aggregate.py --map temp/noun_map.json \
      --extracted temp/nouns/extracted_EP001.json -o temp/noun_map.json

  # 批量：合并 temp/nouns/ 下所有 extracted_*.json（每次调用按集顺序增量并入）
  python nouns/aggregate.py --map temp/noun_map.json \
      --extracted-dir temp/nouns -o temp/noun_map.json
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lib._path  # noqa: F401,E402

from lib.llm import call_chat, extract_json_array  # noqa: E402
from lib.config import LLM_API_KEY, LLM_MODEL, LLM_BASE_URL  # noqa: E402

AGGREGATE_SYSTEM_PROMPT = (
    '你是动画字幕专名归一专家。系统给出现有"标准译名表"（已确认的专名规范译法）'
    '以及一集新提取的专名实体。你的任务：把每个新实体归类。\n'
    '\n'
    '对每个新实体，选择一种处理：\n'
    '1. merge —— 该实体与现有某个标准名是同一实体（即使书写形式不同，如'
    ' お茶の水 与 御茶ノ水），应并入该标准名。输出该标准名的编号。\n'
    '2. new —— 该实体是新专名，尚未收录。为它指定一个标准译名'
    '（必须最符合中文通用译法/音译贴近度，可参考它的 zh_forms 中最好的一种）。\n'
    '3. ignore —— 该实体不是专有名词（是普通词、语气词、翻译错误等），忽略。\n'
    '\n'
    '判断要点：\n'
    '- 同一实体跨集出现时书写形式可能变化，需按语义/发音判断是否同一。\n'
    '- 标准译名要统一、规范；zh_forms 中不一致的译法会在应用阶段统一成标准译名。\n'
    '- 宁缺毋滥：普通词绝不收为专名。\n'
    '\n'
    '返回严格 JSON 数组，每项对应一个新实体（按序）：\n'
    '{"e":"A","action":"merge","canonical":1}    # canonical=现有标准名编号\n'
    '{"e":"B","action":"new","canonical":"标准译名"}\n'
    '{"e":"C","action":"ignore"}\n'
    '如果某实体无法判断，default 为 new（不要丢数据）。\n'
)


def _render_map(noun_map, max_entries=400):
    """把 running-map 渲染成紧凑列表供 LLM 阅读。返回 (lines, index→canonical)。"""
    if not noun_map:
        return [], {}
    # 按 episodes 数量降序，保证主要角色靠前
    ordered = sorted(noun_map.items(),
                     key=lambda kv: len(kv[1].get('episodes', [])),
                     reverse=True)
    if max_entries and len(ordered) > max_entries:
        ordered = ordered[:max_entries]
    lines = []
    idx_to_canonical = {}
    for i, (canonical, ent) in enumerate(ordered, 1):
        ja = '、'.join(ent.get('ja_forms', [])[:6])
        zh = '、'.join(ent.get('zh_variants', [])[:4])
        scope = ent.get('scope', 'auto')
        n_ep = len(ent.get('episodes', []))
        desc = f'{i}. {canonical}'
        if scope == 'global':
            desc += ' [global]'
        elif scope == 'per_episode':
            desc += ' [单集]'
        else:
            desc += f' [{n_ep}集]'
        if ja:
            desc += f' | 日文: {ja}'
        if zh:
            desc += f' | 中文变体: {zh}'
        lines.append(desc)
        idx_to_canonical[i] = canonical
    return lines, idx_to_canonical


def _render_entities(entities):
    lines = []
    letters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    for i, ent in enumerate(entities):
        letter = letters[i] if i < len(letters) else f'X{i}'
        ja = '、'.join(ent.get('ja_forms', []))
        zh = '、'.join(ent.get('zh_forms', []))
        sample = (ent.get('samples') or [''])[0]
        desc = f'{letter}. ja:[{ja}] zh:[{zh}]'
        if sample:
            desc += f' 样例:{sample[:80]}'
        lines.append(desc)
    return lines


def _decide_with_llm(noun_map, episode_id, entities, api_key, model, base_url):
    """对无机械重叠的实体调用 LLM 判定。返回 decisions: {letter: {...}}。"""
    map_lines, idx_to_canonical = _render_map(noun_map)
    ent_lines = _render_entities(entities)

    map_block = '\n'.join(map_lines) if map_lines else '(空)'
    ent_block = '\n'.join(ent_lines) if ent_lines else '(空)'

    user_msg = (
        f'现有标准译名表（{len(map_lines)} 条）：\n{map_block}\n\n'
        f'新集 {episode_id} 提取的专名实体：\n{ent_block}\n\n'
        '请逐条归类。'
    )
    messages = [
        {'role': 'system', 'content': AGGREGATE_SYSTEM_PROMPT},
        {'role': 'user', 'content': user_msg},
    ]
    response = call_chat(messages, api_key=api_key, model=model, base_url=base_url)
    if not response:
        return None
    arr = extract_json_array(response)
    if not isinstance(arr, list):
        return None
    # 规范化：key 统一为字母
    decisions = {}
    for item in arr:
        if not isinstance(item, dict):
            continue
        e = str(item.get('e') or '').strip()
        action = str(item.get('action') or '').strip()
        if not e or not action:
            continue
        decisions[e] = {
            'action': action,
            'canonical': item.get('canonical'),
            'scope': item.get('scope'),
        }
    return decisions


def _merge_mechanical(noun_map, episode_id, entities):
    """机械预合并：共享任一 form 的实体直接并入。返回 (noun_map, remaining)。"""
    remaining = []
    for ent in entities:
        ja_set = set(ent.get('ja_forms', []))
        zh_set = set(ent.get('zh_forms', []))
        target = None
        for canonical, entry in noun_map.items():
            entry_ja = set(entry.get('ja_forms', []))
            entry_zh = set(entry.get('zh_variants', []))
            if canonical in zh_set:
                target = canonical
                break
            if (entry_ja & ja_set) or (entry_zh & zh_set):
                target = canonical
                break
        if target is None:
            remaining.append(ent)
        else:
            _apply_merge(noun_map[target], ent, episode_id, canonical=target)
    return noun_map, remaining


def _apply_merge(entry, ent, episode_id, canonical=''):
    """把一个实体并入已有 entry（canonical 为标准名，本身不入 variants）。"""
    for f in ent.get('ja_forms', []):
        if f not in entry['ja_forms']:
            entry['ja_forms'].append(f)
    for f in ent.get('zh_forms', []):
        if f != canonical and f not in entry['zh_variants']:
            entry['zh_variants'].append(f)
    if episode_id not in entry['episodes']:
        entry['episodes'].append(episode_id)
    entry['count'] = entry.get('count', 0) + ent.get('count', 0)


def _apply_new(noun_map, canonical, ent, episode_id, scope=None):
    """新建标准名条目。"""
    canonical = str(canonical).strip()
    if not canonical or canonical in noun_map:
        return
    variants = [f for f in ent.get('zh_forms', []) if f != canonical]
    noun_map[canonical] = {
        'ja_forms': list(ent.get('ja_forms', [])),
        'zh_variants': variants,
        'episodes': [episode_id],
        'scope': scope or 'auto',
        'source': 'extract',
        'count': ent.get('count', 0),
    }


def _apply_decisions(noun_map, episode_id, entities, decisions):
    """按 LLM decisions 机械应用 merge/new/ignore。未覆盖的实体默认 new。"""
    letters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    ent_by_letter = {}
    for i, ent in enumerate(entities):
        letter = letters[i] if i < len(letters) else f'X{i}'
        ent_by_letter[letter] = ent

    for letter, ent in ent_by_letter.items():
        dec = (decisions or {}).get(letter, {})
        action = dec.get('action', 'new')

        if action == 'ignore':
            continue
        if action == 'merge':
            # canonical 字段 = 标准名编号（int）或标准名（str）
            c = dec.get('canonical')
            target = None
            if isinstance(c, int):
                # 需要重建编号→标准名映射：这里用当前 map 顺序渲染
                lines, idx_to_canonical = _render_map(noun_map, max_entries=None)
                target = idx_to_canonical.get(c)
            elif isinstance(c, str) and c in noun_map:
                target = c
            if target:
                _apply_merge(noun_map[target], ent, episode_id, canonical=target)
            else:
                # 编号解析失败 → 降级为 new
                canonical = str(c or '') or _default_canonical(ent)
                _apply_new(noun_map, canonical, ent, episode_id)
        else:  # new / 未识别
            canonical = str(dec.get('canonical') or '') or _default_canonical(ent)
            _apply_new(noun_map, canonical, ent, episode_id, scope=dec.get('scope'))


def _default_canonical(ent):
    """无 canonical 时的机械兜底：取最长的 zh_form 为标准名。"""
    zhs = ent.get('zh_forms', [])
    if not zhs:
        return '(未命名)'
    return max(zhs, key=len)


def merge_into_map(noun_map, episode_id, entities, api_key=None, model=None,
                   base_url=None):
    """把一集的实体增量合并进 running-map。返回更新后的 map（原地修改并返回）。

    Args:
        noun_map: 现有 running-map（dict）。可为空 {}。
        episode_id: 'EP001' 形式。
        entities: extractor.extract_names 的返回（实体列表）。
    """
    # 清理可能混入的 meta（预留字段，当前无用）
    _meta = noun_map.pop('_meta', None) if '_meta' in noun_map else None

    if not entities:
        if _meta is not None:
            noun_map['_meta'] = _meta
        return noun_map

    # 1. 机械预合并
    noun_map, remaining = _merge_mechanical(noun_map, episode_id, entities)

    # 2. LLM 聚合（无重叠实体）
    if remaining:
        decisions = _decide_with_llm(noun_map, episode_id, remaining,
                                     api_key, model, base_url)
        if decisions is not None:
            _apply_decisions(noun_map, episode_id, remaining, decisions)
        else:
            # LLM 失败 → 兜底全部按 new 收编，不丢数据
            for ent in remaining:
                _apply_new(noun_map, _default_canonical(ent), ent, episode_id)

    # 3. 规范化：zh_variants 去重、episodes 排序、移除 canonical 同名字段
    for canonical, entry in list(noun_map.items()):
        entry['_canonical'] = canonical
        entry['zh_variants'] = sorted(set(entry.get('zh_variants', [])))
        entry['episodes'] = sorted(set(entry.get('episodes', [])))
        entry['ja_forms'] = sorted(set(entry.get('ja_forms', [])), key=len, reverse=True)
        if entry.get('scope') == 'auto':
            pass  # apply_map 时计算
        entry.pop('_canonical', None)

    if _meta is not None:
        noun_map['_meta'] = _meta
    return noun_map


# ═══════════════════════════════════════════════════════════════
# Seed：从旧 ja→zh 映射表初始化已知标准名
# ═══════════════════════════════════════════════════════════════

def seed_from_mapping(noun_map, mapping_path):
    """把旧 noun_mappings.json（ja→zh）作为 seed 合并进 map。

    按 zh 目标值分组，反推出标准名条目（ja_forms=所有指向该 zh 的 ja 形式）。
    episodes 留空，等聚合填充；scope 用 auto。
    """
    with open(mapping_path, 'r', encoding='utf-8') as f:
        mapping = json.load(f)
    for ja_form, zh in mapping.items():
        if not ja_form or not zh:
            continue
        if zh not in noun_map:
            noun_map[zh] = {
                'ja_forms': [],
                'zh_variants': [],
                'episodes': [],
                'scope': 'auto',
                'source': 'seed',
                'count': 0,
            }
        if ja_form not in noun_map[zh]['ja_forms']:
            noun_map[zh]['ja_forms'].append(ja_form)
    return noun_map


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def _extract_ep_from_sidecar(path):
    """从 sidecar 文件名或内容提取集号。"""
    base = os.path.basename(path)
    m = re.search(r'EP(\d{1,3})', base)
    if m:
        return f'EP{int(m.group(1)):03d}'
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f).get('episode', '???')
    except Exception:
        return '???'


def main():
    ap = argparse.ArgumentParser(description='跨集专名聚合（增量 running-map）')
    ap.add_argument('--map', default='temp/noun_map.json',
                    help='running-map 路径（读写）')
    ap.add_argument('--extracted', help='单集 sidecar JSON（extractor 产物）')
    ap.add_argument('--extracted-dir', help='批量：目录下所有 extracted_*.json')
    ap.add_argument('--seed', help='用旧 ja→zh 映射表初始化已知标准名')
    ap.add_argument('--init', action='store_true',
                    help='先初始化 map（--seed 必须给出）')
    ap.add_argument('-o', '--output', help='写出的 map 路径（默认覆盖 --map）')
    ap.add_argument('--api-key', default=LLM_API_KEY)
    ap.add_argument('--model', default=LLM_MODEL)
    ap.add_argument('--base-url', default=LLM_BASE_URL)
    args = ap.parse_args()

    out_path = args.output or args.map
    if os.path.dirname(out_path):
        os.makedirs(os.path.dirname(out_path), exist_ok=True)

    noun_map = {}
    if os.path.exists(args.map):
        with open(args.map, 'r', encoding='utf-8') as f:
            noun_map = json.load(f)

    if args.init:
        if not args.seed:
            print('--init 需要 --seed', file=sys.stderr)
            sys.exit(1)
        noun_map = seed_from_mapping(noun_map, args.seed)
        print(f'[aggregate] seed 完成：{len(noun_map)} 条标准名', file=sys.stderr)

    if args.extracted:
        jobs = [args.extracted]
    elif args.extracted_dir:
        jobs = sorted(
            os.path.join(args.extracted_dir, f)
            for f in os.listdir(args.extracted_dir)
            if f.startswith('extracted_') and f.endswith('.json')
        )
    else:
        jobs = []

    if not jobs:
        print('[aggregate] 无 --extracted/--extracted-dir 输入，仅初始化/更新 map',
              file=sys.stderr)

    for job in jobs:
        with open(job, 'r', encoding='utf-8') as f:
            data = json.load(f)
        episode_id = data.get('episode') or _extract_ep_from_sidecar(job)
        entities = data.get('entities', [])
        before = len(noun_map)
        merge_into_map(noun_map, episode_id, entities,
                       api_key=args.api_key or None, model=args.model or None,
                       base_url=args.base_url or None)
        after = len(noun_map)
        print(f'[aggregate] {episode_id}: 实体 {len(entities)} 个 → '
              f'标准名 {before} → {after}', file=sys.stderr)

    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(noun_map, f, ensure_ascii=False, indent=2)
    print(f'[aggregate] map → {os.path.relpath(out_path)} '
          f'（共 {len(noun_map)} 条）', file=sys.stderr)


if __name__ == '__main__':
    main()
