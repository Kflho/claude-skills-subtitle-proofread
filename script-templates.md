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

## 1. OP/ED 时间码匹配

### 检测
```bash
python scripts/oped_detect.py --target-dir ./target/ --config oped_config.json > oped_findings.json
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
[
  {
    "file": "Episode 001.ass",
    "line": 342,
    "start_ms": 89200,
    "source_text": "Ai-yai-ya... yume wo miteita",
    "ref_text": "啊呀呀... 我曾梦见",
    "style": "Opening Romaji"
  }
]
```

### 对应 fixes.json
```json
[
  {"action": "replace_text", "file": "Episode 001.ass", "line": 342, "replacement": "啊呀呀... 我曾梦见"}
]
```

---

## 2. 双语混合检测

### 检测
```bash
python scripts/bilingual_detect.py --target-dir ./target/ --config bilingual_config.json > bilingual_findings.json
```

### 配置 (bilingual_config.json)
```json
{
  "dialogue_styles": ["Default", "DefaultTop", "Episode"],
  "source_lang_pattern": "[A-Za-z]"
}
```

### 检测输出（片段）
```json
[
  {"file": "Ep 001.ass", "line": 15, "type": "mixed", "text": "走吧 Come on！", "visible": "走吧 Come on！", "timecode": "0:01:23.45"},
  {"file": "Ep 001.ass", "line": 42, "type": "pure_source", "text": "I'm sorry.", "visible": "I'm sorry.", "timecode": "0:03:10.20"}
]
```

### 对应 fixes.json

`type: "mixed"` → Claude 判断删除源语言部分：
```json
[
  {"action": "replace_global_regex", "pattern": "走吧 Come on！", "replacement": "走吧！", "note": "删除英文残留"}
]
```

`type: "pure_source"` → Claude 翻译后：
```json
[
  {"action": "replace_text", "file": "Ep 001.ass", "line": 42, "replacement": "对不起。"}
]
```

---

## 3. Name 字段映射

### 检测
```bash
# 先扫描所有 Name 值
python scripts/names_detect.py --target-dir ./target/ --scan

# 或输出 JSON
python scripts/names_detect.py --target-dir ./target/ > names.json
```

### 检测输出（片段）
```json
{
  "names": ["Abe", "Gumi", "Poppy", "Sally"],
  "by_file": {"Episode 001.ass": ["Abe", "Sally"], "Episode 002.ass": ["Abe", "Gumi"]}
}
```

### 对应 fixes.json

Claude 对照参考字幕建立映射后：
```json
[
  {"action": "replace_global", "original": ",Abe,", "replacement": ",阿部,", "note": "Name字段：角色名"},
  {"action": "replace_global", "original": ",Gumi,", "replacement": ",古美,", "note": "Name字段：角色名"},
  {"action": "replace_global", "original": ",Poppy,", "replacement": ",波比,", "note": "Name字段：宠物名"}
]
```

> ⚠️ Name 字段替换建议用前后逗号限定（`,Name,`），避免替换到对话文本中的同名普通词汇。

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

## 6. 样式统计与删除

### 检测
```bash
python scripts/styles_detect.py --target-dir ./target/ > styles.json
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

## 7. 绘图指令修复

### 检测
```bash
python scripts/drawing_detect.py --target-dir ./target/ > drawing_findings.json
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

## 8. 纯源语言行处理

### 检测
```bash
python scripts/source_lang_detect.py --target-dir ./target/ --config source_config.json > pure_source.json
```

### 配置 (source_config.json)
```json
{
  "dialogue_styles": ["Default", "DefaultTop", "Episode"],
  "source_char_pattern": "[A-Za-zА-Яа-яЁё]",
  "target_lang": "cjk"
}
```

### 检测输出（片段）
```json
[
  {"file": "Ep 003.ass", "line": 78, "text": "I'm sorry.", "visible": "I'm sorry.", "timecode": "0:05:42.10", "style": "Default"}
]
```

### 对应 fixes.json

Claude 对照上下文集判断：
```json
[
  {"action": "replace_text", "file": "Ep 003.ass", "line": 78, "replacement": "对不起。"},
  {"action": "delete_line", "file": "Ep 003.ass", "line": 102}
]
```

---

## 9. 机翻卡死重复

### 检测
```bash
python scripts/repeat_detect.py --target-dir ./target/ --config repeat_config.json > repeat_findings.json
```

### 配置 (repeat_config.json)
```json
{
  "dialogue_styles": ["Default", "DefaultTop", "Episode"],
  "min_repeats": 8
}
```

### 检测输出（片段）
```json
[
  {"file": "Ep 006.ass", "line": 201, "start_ms": 345000, "visible_text": "红尘红尘红尘...", "repeat_seq": "红尘", "repeat_count": 12, "full_match": "红尘红尘红尘红尘红尘红尘红尘红尘红尘红尘红尘红尘"}
]
```

### 对应 fixes.json

Claude 对照参考字幕找到正确文本后：
```json
[
  {"action": "replace_text", "file": "Ep 006.ass", "line": 201, "replacement": "红粉，红粉，红粉，瞬间变变变..."}
]
```

---

## 10. 机翻幻觉/乱码

### 检测
```bash
python scripts/garbled_detect.py --target-dir ./target/ --config garbled_config.json > garbled_findings.json
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

## 12. 源语言字符残留

### 检测
```bash
python scripts/source_char_detect.py --target-dir ./target/ --config source_char_config.json > char_scan.json
```

### 配置 (source_char_config.json)
```json
{
  "dialogue_styles": ["Default", "DefaultTop", "Episode"],
  "source_char_pattern": "[А-Яа-яЁё]"
}
```

### 检测输出（片段）
```json
[
  {"file": "Ep 015.ass", "line": 89, "timecode": "0:06:30.10", "visible": "我们走吧Давай", "source_chars_found": ["Д", "а", "в", "а", "й"], "count": 5}
]
```

### 对应 fixes.json

Claude 判断是删除残留还是翻译：
```json
[
  {"action": "replace_global_regex", "pattern": "我们走吧Давай", "replacement": "我们走吧！", "note": "删除俄语残留"}
]
```

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

## 快速参考：什么时候用哪个 action

| 场景 | action | 示例 |
|------|--------|------|
| 同一文本在所有文件中都需替换 | `replace_global` | 人名统一、感叹词替换 |
| 同一正则模式在所有文件中替换 | `replace_global_regex` | 双语混合格式清理 |
| 某文件特定行需替换 text | `replace_text` | 单集偶发错误 |
| 某文件特定行需替换 Name 字段 | `replace_name` | Name 字段本地化 |
| 某文件特定行需删除 | `delete_line` | 上下文已有翻译的重复行 |
| 某样式所有行都删除 | `delete_style` | 译者署名样式 |
| 含关键词的 Comment 行删除 | `delete_comment` | 编辑注释行 |
