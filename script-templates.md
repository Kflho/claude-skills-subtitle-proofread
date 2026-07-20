# 字幕校对脚本配置参考

> 所有脚本都遵循 **检测 → Claude 审查 → apply_fixes.py 修复** 的三步流程。
> 本文件记录每个检测脚本的配置格式、输出示例和对应的 fixes.json 写法。

---

## 通用配置

所有检测脚本共用以下参数：

```
--target-dir <DIR>        目标 ASS 字幕目录（必需）
--config <CONFIG.json>    JSON 配置文件（可选，用于覆盖默认参数）
```

---

## 1. OP/ED 多样式对比（ass_repair.py --check oped）

### 检测
```bash
python scripts/ass_repair.py --target-dir ./target/ --check oped --oped-config oped_config.json
```

### 配置 (oped_config.json)
```json
{
  "source_style": "Opening Romaji",
  "ref_style": "Opening Rus",
  "tolerance_ms": 500
}
```

### 检测输出（片段）
```json
{
  "checks_run": ["oped"],
  "results": {
    "oped": {
      "findings": {
        "per_file_diffs": [
          {"file": "Episode 001.ass", "line": 342, "start_ms": 89200,
           "source_text": "Ai-yai-ya...", "ref_text": "啊呀呀...", "style": "Opening Romaji"}
        ]
      },
      "summary": {"total_files": 109, "total_text_diffs": 45}
    }
  }
}
```

### 对应 fixes.json
```json
[
  {"action": "replace_text", "file": "Episode 001.ass", "line": 342, "replacement": "啊呀呀... 我曾梦见"}
]
```

---

## 2. 统一字符扫描（替代旧版独立脚本）

> **旧版脚本已删除**：`bilingual_detect.py` / `source_lang_detect.py` / `source_char_detect.py` / `repeat_detect.py` / `issue_tracker.py`。
> 功能已全部合并到 `unified_scanner.py`，单次遍历完成所有字符层检测。

### 检测
```bash
python scripts/unified_scanner.py --target-dir ./target/ \
  --output-findings findings.json \
  --output-issues issues/ \
  --build-glossary
```

### 检测输出（findings.json 片段）
```json
{
  "garbled_cues": [
    {"file": "Ep 001.srt", "line": 15, "timecode": "0:01:23.45", "text": "走吧 Come on！", "has_kana": false, "has_kanji": true},
    {"file": "Ep 001.srt", "line": 42, "timecode": "0:03:10.20", "text": "I'm sorry.", "has_kana": false, "has_kanji": false}
  ],
  "repeats": [
    {"file": "Ep 006.srt", "line": 201, "timecode": "0:05:45.00", "repeat_seq": "红尘", "repeat_count": 12}
  ],
  "per_episode_issues": { "EP001": [...], "EP006": [...] },
  "summary": { "files_scanned": 192, "garbled_count": 1101, "repeat_count": 45 }
}
```

### 对应 fixes.json

Garbled cue → VAD + Whisper 重转录后替换；Repeat → Claude 审查后替换：
```json
[
  {"action": "replace_text", "file": "Ep 001.srt", "line": 15, "replacement": "走吧！"},
  {"action": "replace_text", "file": "Ep 001.srt", "line": 42, "replacement": "ごめんなさい。"},
  {"action": "replace_text", "file": "Ep 006.srt", "line": 201, "replacement": "正しいテキスト..."}
]
```

---

## 3. Name 字段映射（ass_repair.py --check names）

### 检测
```bash
python scripts/ass_repair.py --target-dir ./target/ --check names
```

### 检测输出（--lang-check 模式，片段）
```json
{
  "total_names": 180,
  "target_language_names": 16,
  "non_target_names": 164,
  "non_target_by_language": {
    "cyrillic": 140,
    "latin": 20,
    "japanese_kana": 4
  },
  "non_target_names_detail": [
    {
      "name": "Мама Осаму",
      "primary_language": "cyrillic",
      "non_target_languages": ["cyrillic"],
      "non_target_chars": ["а", "а", "М", "м", "О", "с"],
      "occurrences": 49,
      "files": ["Mahou Tsukai Sally 056.ass", ...],
      "file_count": 8
    }
  ]
}
```

### 对应 fixes.json

Claude 审查后建立映射：
```json
[
  {"action": "replace_global", "original": ",Мама Осаму,", "replacement": ",修的母亲,", "note": "Name字段：俄→中"},
  {"action": "replace_global", "original": ",サリー,", "replacement": ",莎莉,", "note": "Name字段：日→中"},
  {"action": "replace_global", "original": ",Sally,", "replacement": ",莎莉,", "note": "Name字段：英→中"}
]
```

> ⚠️ Name 字段替换必须用前后逗号限定（`,Name,`），避免替换到对话文本中的同名普通词汇。建议优先使用 `replace_global`（而非逐行 `replace_name`），因为同一角色名会在多行多集中出现。

---

## 4. 感叹词/语气词替换

### 检测
```bash
python scripts/interjection_detect.py --target-dir ./target/ --config interj_config.json > interj_findings.json
```

### 配置 (interj_config.json)
```json
{
  "dialogue_styles": ["Default", "DefaultTop", "Episode"],
  "source_char_pattern": "[A-Za-zА-Яа-яЁё]",
  "min_len": 2,
  "max_len": 5
}
```

### 检测输出（片段）
```json
{
  "word_frequencies": {"Ну": 156, "Вот": 89, "Да": 72, "Ой": 45, "Что": 38},
  "findings": [
    {"file": "Ep 001.ass", "line": 23, "word": "Ну", "context": "Ну, пошли!", "timecode": "0:02:15.30", "style": "Default"}
  ],
  "total_findings": 450
}
```

### 对应 fixes.json

Claude 审查词频表，只替换真正需要替换的（跳过无问题的词）：
```json
[
  {"action": "replace_global", "original": "Ну", "replacement": "好啦", "note": "感叹词"},
  {"action": "replace_global", "original": "Вот", "replacement": "瞧", "note": "语气词"},
  {"action": "replace_global", "original": "Ой", "replacement": "哎哟", "note": "惊呼"}
]
```

> ⚠️ 不要盲目替换所有高频词。某些词可能在目标语言中已自然使用，需逐条对照参考字幕确认。

---

## 5. 专有名词统一

### 检测
```bash
python scripts/proper_noun_detect.py --target-dir ./target/ --ref-dir ./ref/ > proper_nouns.json
```

### 检测输出（片段）
```json
{
  "target_proper_nouns": [
    {"noun": "阿部", "count": 234, "samples": [...]},
    {"noun": "安倍晋三", "count": 15, "samples": [...]},
    {"noun": "古美", "count": 189, "samples": [...]},
    {"noun": "龟仙人", "count": 8, "samples": [...]}
  ],
  "target_has_ref": ["阿部", "古美"],
  "target_no_ref": ["安倍晋三", "龟仙人"],
  "ref_no_target": []
}
```

### 对应 fixes.json

`target_no_ref` 中的项大概率是幻觉 → 对照参考确认正确译名后：
```json
[
  {"action": "replace_global", "original": "安倍晋三", "replacement": "阿部", "note": "人名幻觉修正"},
  {"action": "replace_global", "original": "龟仙人", "replacement": "古美", "note": "角色名幻觉修正"}
]
```

---

## 6. 样式统计（ass_repair.py --check styles）

### 检测
```bash
python scripts/ass_repair.py --target-dir ./target/ --check styles
```

### 检测输出（片段）
```json
[
  {"style": "Default", "count": 8420, "sample_text": "你好，今天天气真好", "files": ["Ep 001.ass", "Ep 002.ass", ...]},
  {"style": "DefaultTop", "count": 1230, "sample_text": "（画外音）等等...", "files": [...]},
  {"style": "Roboto", "count": 109, "sample_text": "Перевод: xxx", "files": [...]},
  {"style": "Display", "count": 2180, "sample_text": "{\\k41}Pa-{\\k25}pa-", "files": [...]}
]
```

### 对应 fixes.json

Claude 审查后删除译者署名样式：
```json
[
  {"action": "delete_style", "style": "Roboto"},
  {"action": "delete_comment", "keyword": "Перевод:"},
  {"action": "delete_comment", "keyword": "Translated by"}
]
```

---

## 7. 绘图指令修复（ass_repair.py --check drawing）

### 检测
```bash
python scripts/ass_repair.py --target-dir ./target/ --check drawing
```

### 检测输出（片段）
```json
[
  {"file": "Ep 008.ass", "line": 156, "text": "{\\p1}男 0 0 女 100 100", "has_suspicious_chars": true, "suspicious_parts": ["男", "女"]}
]
```

### 对应 fixes.json
```json
[
  {"action": "replace_global", "original": "男", "replacement": "m", "note": "绘图指令 m 被译为'男'"},
  {"action": "replace_global", "original": "女", "replacement": "n", "note": "绘图指令 n 被译为'女'"}
]
```

---

## 8. 纯源语言行 + 卡死重复 → 已合并到 unified_scanner

> ⚠️ 旧脚本 `source_lang_detect.py` 和 `repeat_detect.py` 已删除。
> 纯源语言行检测和卡死重复检测已合并到 `unified_scanner.py`（见上方 ## 2）。
> 纯源语言行 → `garbled_cues` 中 `has_kana: false, has_kanji: false` 的条目。
> 卡死重复 → `repeats` 数组。

---

## 10. 机翻幻觉/乱码（语义层）

### 检测
```bash
# --lang 加载对应语言的预设模式（zh=中文, en=英语, ja=日语空模式）
python scripts/garbled_detect.py --target-dir ./target/ --lang zh > garbled_findings.json

# 追加自定义模式
python scripts/garbled_detect.py --target-dir ./target/ --lang zh --config garbled_config.json
```

### 配置 (garbled_config.json) — 可选，添加自定义模式
```json
{
  "dialogue_styles": ["Default", "DefaultTop", "Episode"],
  "custom_patterns": [
    ["须藤", "角色幻觉", "日语人名 Sudou 被译为中文"],
    ["本田", "人名幻觉", "日语人名 Honda 被译为汽车品牌"]
  ]
}
```

### 检测输出（片段）
```json
{
  "summary": {
    "安倍晋三": {"count": 15, "category": "人名幻觉", "desc": "普通人名被译为日本前首相"},
    "去你妈的": {"count": 8, "category": "脏话误译", "desc": "可能为责备用语的过度翻译"}
  },
  "by_category": {
    "人名幻觉": [
      {"file": "Ep 012.ass", "line": 56, "pattern": "安倍晋三", "text": "安倍晋三，你在哪？", "timecode": "0:04:12.30"}
    ]
  },
  "total_findings": 45
}
```

### 对应 fixes.json

Claude 逐条对照参考字幕确认后：
```json
[
  {"action": "replace_global", "original": "安倍晋三", "replacement": "阿部", "note": "人名幻觉：Abe→阿部，非安倍晋三"},
  {"action": "replace_global", "original": "去你妈的", "replacement": "活该", "note": "脏话误译：Serves you right→活该"}
]
```

---

## 11. 固定格式统一

### 检测
```bash
python scripts/format_detect.py --target-dir ./target/ --config format_config.json > format_findings.json
```

### 配置 (format_config.json) — 可选
```json
{
  "dialogue_styles": ["Default", "DefaultTop", "Episode"],
  "patterns": {
    "预告标题": ["下集预告", "下一集", "下集，预告", "在下一集中", "下一个系列", "次回予告"],
    "结尾语": ["敬请期待", "敬请收看", "千万不要错过", "不要错过", "请欣赏"]
  }
}
```

### 检测输出（片段）
```json
{
  "variant_stats": {
    "预告标题": {"下集预告": 45, "下一集": 23, "在下一集中": 18, "下一个系列": 12, "次回予告": 8},
    "结尾语": {"敬请收看": 30, "敬请期待": 25, "千万不要错过": 15, "请欣赏": 12}
  }
}
```

### 对应 fixes.json

Claude 选定标准后统一：
```json
[
  {"action": "replace_global", "original": "下一集", "replacement": "下集预告", "note": "预告标题统一"},
  {"action": "replace_global", "original": "在下一集中", "replacement": "下集预告", "note": "预告标题统一"},
  {"action": "replace_global", "original": "下一个系列", "replacement": "下集预告", "note": "预告标题统一"},
  {"action": "replace_global", "original": "敬请收看", "replacement": "敬请期待", "note": "结尾语统一"},
  {"action": "replace_global", "original": "千万不要错过", "replacement": "敬请期待", "note": "结尾语统一"}
]
```

---

## 12. 源语言字符残留 → 已合并到 unified_scanner

> ⚠️ 旧脚本 `source_char_detect.py` 已删除。
> 字符层检测（按语言分类的字符残留扫描）已合并到 `unified_scanner.py`（见上方 ## 2）。
> `unified_scanner` 的 `garbled_cues` 输出包含 `has_kana`/`has_kanji` 标记，可区分不同类型的字符残留。

---

## 13. 批量并行精读框架

此模板不生成脚本，而是定义子代理调度流程。

### 流程

1. **分批**：将全部文件按 ~18 集/批分成 N 批
2. **并行启动**：每批一个子代理，同时运行，对照参考字幕逐行精读
3. **统一审查**：所有子代理完成后，主进程合并结果

### 子代理 Prompt 模板

```
你是字幕校对专家。请对照参考字幕逐行精读以下剧集：

目标文件（机翻中文）: {目标目录}/Mahou Tsukai Sally {集号范围}.ass
参考文件（人工翻译）: {参考目录}/Mahou Tsukai Sally {集号范围}.ass

任务：
1. 逐行对比同一时间码的中文和参考字幕
2. 找出所有翻译错误、用词不当、机翻幻觉
3. 以 OLD → NEW 格式输出修复列表
4. 标注每条修复的原因

输出格式（每行一条）：
文件名 | 行号 | OLD文本 | NEW文本 | 原因
```

### 主进程合并步骤

1. 收集所有批次报告，解析 `|` 分隔格式
2. **去重**：同 OLD+NEW 组合只保留一条
3. **冲突检测**：同一 OLD 有不同 NEW → 人工裁定
4. **分类**：
   - ≥3 集出现 → `replace_global`（统一修改）
   - 1-2 集出现 → `replace_text`（单集特定）
5. 生成 fixes.json，运行 `apply_fixes.py`

### 冲突检测脚本

```python
# 简易冲突检测
fixes_by_old = {}
for f in all_fixes:
    fixes_by_old.setdefault(f['old'], []).append(f)
conflicts = {
    old: list(set(x['new'] for x in fixes))
    for old, fixes in fixes_by_old.items()
    if len(set(x['new'] for x in fixes)) > 1
}
if conflicts:
    for old, versions in conflicts.items():
        print(f"⚠ {old} → {versions}")
```

---

## 14. 繁体中文 → 简体中文 检测与转换

### 检测
```bash
# 检测模式（输出 JSON 供 Claude 审查）
python scripts/trad_to_simp_detect.py --target-dir ./target/ > trad_findings.json

# 自动转换（推荐 — 繁→简映射为确定性规则，无需逐条审查）
python scripts/trad_to_simp_detect.py --target-dir ./target/ --auto

# 预览自动转换（不写入）
python scripts/trad_to_simp_detect.py --target-dir ./target/ --auto --dry-run
```

### 检测输出（片段）
```json
{
  "findings": [
    {
      "file": "Ep 003.ass",
      "line": 325,
      "timecode": "0:13:24.32",
      "style": "Default",
      "visible": "哦，對不起！给你",
      "trad_chars": ["對"],
      "suggested_fixes": {"對": "对"}
    }
  ],
  "char_stats": {"對": 45, "沒": 38, "會": 30, "說": 25, "時": 20},
  "affected_files": ["Ep 003.ass", "Ep 014.ass", ...],
  "total_occurrences": 3358
}
```

### 对应 fixes.json（检测模式）

繁→简为确定性映射，通常直接 `--auto` 自动转换。如需保留检测步骤：

```json
[
  {"action": "replace_global", "original": "對", "replacement": "对", "note": "繁体→简体"},
  {"action": "replace_global", "original": "沒", "replacement": "没", "note": "繁体→简体"},
  {"action": "replace_global", "original": "會", "replacement": "会", "note": "繁体→简体"}
]
```

> ⚠️ **重要**：`--auto` 模式仅在对话样式行中执行转换，自动跳过 Display、Title、Opening 等特效层。如果某个繁体字恰好也是某个专有名词的标准写法（极少见），需手动检查 `char_stats` 后决定是否保留。

| 场景 | action | 示例 |
|------|--------|------|
| 同一文本在所有文件中都需替换 | `replace_global` | 人名统一、感叹词替换 |
| 同一正则模式在所有文件中替换 | `replace_global_regex` | 双语混合格式清理 |
| 某文件特定行需替换 text | `replace_text` | 单集偶发错误 |
| 某文件特定行需替换 Name 字段 | `replace_name` | Name 字段本地化 |
| 某文件特定行需删除 | `delete_line` | 上下文已有翻译的重复行 |
| 某样式所有行都删除 | `delete_style` | 译者署名样式 |
| 含关键词的 Comment 行删除 | `delete_comment` | 编辑注释行 |
