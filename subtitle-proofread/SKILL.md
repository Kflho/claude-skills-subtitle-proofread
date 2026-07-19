---
name: subtitle-proofread
description: 对照参考字幕，校对机翻 ASS 字幕。适用于任意语言机器翻译字幕的批量校对。
argument-hint: [目标目录] [参考字幕目录]
---

# 字幕校对 Skill

对照高质量参考字幕，系统性校对机器翻译的 `.ass` 字幕。基于 109 集番剧校对实战经验总结。

## 前置条件

- 目标 `.ass` 字幕目录（机翻，需校对）
- 参考 `.ass` 字幕目录（人工翻译，作为基准）
- 两者文件名对应、时间码基本一致

## 工具架构：检测 → 审查 → 修复

所有修复操作遵循统一的三步流程：

```
检测脚本 (输出 JSON) → Claude 审查 (判断 + 补充修正) → apply_fixes.py (批量写入)
```

### 为什么要这样设计？

机翻错误**无法穷举**，不能硬编码在脚本中。正确做法：
1. **脚本负责「找到可疑之处」** — 模式匹配、差异对比、统计扫描，输出结构化 JSON
2. **Claude 负责「判断如何修正」** — 对照参考字幕，确认是否真的需要修改，确定正确文本
3. **统一修复脚本负责「写入」** — 读取审查后的 fixes.json，批量应用到全部文件

### 可用脚本一览

所有脚本位于 `scripts/` 目录，共享工具函数 `scripts/ass_utils.py`。

| # | 检测脚本 | 用途 | 输出 |
|---|---------|------|------|
| 1 | `oped_detect.py` | 对比 OP/ED 轨，检测时间码匹配差异 | `[{"file","line","source_text","ref_text"}]` |
| 2 | `bilingual_detect.py` | 检测中英/多语混合行、纯源语言行 | `[{"file","line","type":"mixed"\|"pure_source"}]` |
| 3 | `names_detect.py` | 扫描全部 Name 字段值 | `{"names":[...], "by_file":{...}}` |
| 4 | `interjection_detect.py` | 检测源语言短感叹词残留 | `{"word_frequencies":{...}, "findings":[...]}` |
| 5 | `proper_noun_detect.py` | 检测专名变体、对比参考字幕译名 | `{"target_proper_nouns":[...], "source_names":[...]}` |
| 6 | `styles_detect.py` | 统计各样式使用情况 | `[{"style","count","sample_text","files"}]` |
| 7 | `drawing_detect.py` | 检测矢量绘图指令被误译 | `[{"file","line","suspicious_parts"}]` |
| 8 | `source_lang_detect.py` | 检测无目标语言的纯源语言行 | `[{"file","line","visible","timecode"}]` |
| 9 | `repeat_detect.py` | 检测 MT 卡死重复序列 (≥8次) | `[{"file","line","repeat_seq","repeat_count"}]` |
| 10 | `garbled_detect.py` | 检测机翻幻觉（人名/角色名/脏话等） | `{"by_category":{...}, "summary":{...}}` |
| 11 | `format_detect.py` | 检测预告标题/结尾语等格式变体 | `{"variant_stats":{...}, "findings":{...}}` |
| 12 | `source_char_detect.py` | 扫描对话中残留的源语言字符 | `[{"file","line","source_chars_found"}]` |

| 修复脚本 | 用途 |
|----------|------|
| `apply_fixes.py` | 读取 `fixes.json`，批量应用所有修复项 |

### 三步操作流程

以「感叹词残留」为例：

```bash
# 第一步：检测
python scripts/interjection_detect.py --target-dir ./target/ --config config.json > findings.json

# 第二步：Claude 审查 findings.json
#   → 逐条判断：这个"Ну"确实应该替换为"好啦"
#   → 输出 fixes.json:
#   [
#     {"action": "replace_global", "original": "Ну", "replacement": "好啦", "note": "感叹词"},
#     {"action": "replace_global", "original": "Вот", "replacement": "瞧", "note": "语气词"},
#     ...
#   ]

# 第三步：修复（先 dry-run 预览）
python scripts/apply_fixes.py --target-dir ./target/ --fixes fixes.json --dry-run
python scripts/apply_fixes.py --target-dir ./target/ --fixes fixes.json
```

### fixes.json 格式

```json
[
  {"action": "replace_global", "original": "旧文本", "replacement": "新文本", "note": "说明"},
  {"action": "replace_global_regex", "pattern": "正则", "replacement": "替换", "note": "说明"},
  {"action": "replace_text", "file": "xxx.ass", "line": 42, "replacement": "新文本"},
  {"action": "replace_name", "file": "xxx.ass", "line": 42, "replacement": "新名称"},
  {"action": "delete_line", "file": "xxx.ass", "line": 42},
  {"action": "delete_style", "style": "Roboto"},
  {"action": "delete_comment", "keyword": "Translated by"}
]
```

**建议**：优先使用 `replace_global`（全局替换），效率最高；仅当同一原文在不同语境需不同翻译时才用按行 `replace_text`。

---

## 参考字幕的用法

参考字幕用于**按时间码定位对照**，不是直接替换。核心原则：
- 逐行对比同一时间码的目标文本与参考文本
- 发现不一致时，以参考字幕的语义为准修正目标文本
- 参考字幕中的专有名词（人名、地名、称谓）统一采纳为全剧标准

---

## 机翻常见错误清单

以下错误按发现频率从高到低排列。

### 1. 音译代替意译（极高频）

源语言感叹词/拟声词被音译为目标语言拼音或罗马字母，而非翻译。

| 错误模式 | 正确处理 |
|----------|----------|
| 感叹词被转写为罗马拼音 | → 目标语言对应的感叹词 |
| 拟声词被转写为罗马拼音 | → 目标语言对应的拟声词 |
| 语气词被保留为源语言发音 | → 目标语言对应的语气词 |

**检测方法**：运行 `interjection_detect.py`，配置源语言字符模式。对照参考字幕同一时间码确定正确语义。

### 2. 机翻幻觉 / 假朋友（高频）

MT 将专有名词误译为字面意思，或将普通词汇错误联想为不相关内容。

**子类型**：

| 类型 | 特征 | 示例 |
|------|------|------|
| 人名→名人幻觉 | 普通人名被译为同音知名人物 | 如 `Abe`→`安倍晋三` |
| 人名→角色幻觉 | 普通人名被译为同音虚构角色 | 如 `Gumi`→`龟仙人` |
| 人名→物品名词 | 含普通词汇含义的名字被按字面翻译 | 如某角色名在源语言中恰好与"驾驶室"同形 |
| 假朋友（False Friend） | 同一词在源语言和目标语言中均存在但含义不同 | 如 `sister` 被译为"修女"而非"姐姐" |
| 动物名→花名/物名 | 宠物名在源语言中恰好是某种植物的名称 | 如 `Poppy`→`罂粟` 而非 `波比` |
| 惯用语字面直译 | 固定搭配/俗语被逐字翻译 | 如 "I give up" 被译为字面无关的内容 |
| 脏话误译 | 责备用语被译为侮辱性脏话 | 如 "Serves you right!"→`去你妈的`（应为`活该`） |

**检测方法**：运行 `garbled_detect.py`（内置常见幻觉模式），补充自定义可疑词。对照参考字幕逐条确认。

### 3. 机翻卡死重复（中频）

MT 程序卡住，将同一词/短语机械重复数十次。

**识别特征**：
- 同一 2-4 字序列重复 ≥8 次
- 常见模式：`某某，某某，某某...` / `词A词A词A词A...`

**检测方法**：运行 `repeat_detect.py`。

**注意排除**：歌曲歌词中的 scat 唱词（如 `Pa-pa-pa...`、`La-la-la...`）、动物叫声（如 `Me-me-me...`咩咩叫）、卡拉OK 特效层（Display 样式）。

### 4. OP/ED 歌词乱码（每集固定出现）

OP/ED 的歌词轨为严重机翻乱码，通常每集 30-40 行。

**检测方法**：
1. 先运行 `styles_detect.py` 了解文件中有哪些样式
2. 确认乱码轨和正确翻译轨的样式名
3. 运行 `oped_detect.py --config oped_config.json` 对比两轨

### 5. 双语混合残留（高频）

机翻遗留大量源语言文本与目标语言混排。

**模式**：
- 目标语言后紧跟同义源语言：`桌子，椅子。So, table, chairs.`
- 目标语言+源语言直译：`来吧 Come on！`
- 纯源语言行无目标语言翻译：`I'm sorry.` / `Come on！`

**检测方法**：运行 `bilingual_detect.py`（自动分类 mixed / pure_source）。

### 6. Name 字段保留源语言（每集）

ASS 格式的 Name 字段（逗号分隔第 4 项）保留源语言原名。

**检测方法**：运行 `names_detect.py --scan` 列出所有 Name 值，Claude 对照参考字幕建立映射表。

### 7. 源语言感叹词残留（高频）

对话文本中遗留源语言单词：常见为短感叹词、语气词、连接词。

**检测方法**：运行 `interjection_detect.py`，配置 2-5 字符的源语言词检测。

### 8. 节目名/角色名/术语不统一（高频）

同一角色/节目/术语在不同集有不同译名。

**检测方法**：运行 `proper_noun_detect.py --target-dir ./target/ --ref-dir ./ref/`，关注 "仅在机翻中出现" 的专名（可能是幻觉）。

### 9. 译者署名残留

每集末尾的译者/发布者信息行 → 全部删除。

**检测方法**：运行 `styles_detect.py` 找出非对话样式，Claude 确认后通过 `apply_fixes.py` 的 `delete_style` action 删除。

### 10. 矢量绘图指令乱码

ASS 绘图命令被误译为字面意思。

**检测方法**：运行 `drawing_detect.py` 扫描 `\p1` 标签后的文本。

### 11. 源语言文本损坏（反转/乱码）

部分参考字幕存在字符级反转或编码损坏。

**识别特征**：机翻输出为无意义音节堆砌，对照参考字幕如发现反转/乱码文本，先恢复再翻译。

### 12. 固定格式不统一

- 下集预告/结尾等固定用语有多种变体 → 全剧统一
- 标题、转场提示等格式不一致 → 全剧统一

**检测方法**：运行 `format_detect.py`，Claude 审查变体列表，确定统一标准后生成 `replace_global` fixes。

### 13. 源语言编辑注释

删除译者在字幕中留下的注释标签、定位标签、议论文字、Comment 行等。

**检测方法**：运行 `styles_detect.py` 并手动搜索 Comment 行中的可疑关键词。

---

## 校对工作流

### 阶段一：规则批量修复

按以下顺序执行。每步流程：运行检测脚本 → Claude 审查输出 → 生成 fixes.json → 运行 apply_fixes.py。

1. **OP/ED 修复** — `oped_detect.py`（见错误#4）
2. **双语混合清理** — `bilingual_detect.py`（见错误#5）
3. **Name 字段本地化** + 感叹词替换 — `names_detect.py` + `interjection_detect.py`（见错误#6、#7）
4. **专有名词统一** — `proper_noun_detect.py`（见错误#8）
5. **删除署名** + 绘图指令修复 — `styles_detect.py` + `drawing_detect.py`（见错误#9、#10）
6. **纯源语言行处理** — `source_lang_detect.py`（见错误#5第三类）
7. **固定格式统一** — `format_detect.py`（见错误#12）

### 阶段二：批量精读审查

1. 将文件分为 N 批（每批 ~18 集）
2. 每批启动一个子代理，对照参考字幕逐行精读
3. 子代理输出 `OLD → NEW` 修复列表（`|` 分隔格式）
4. 主进程收集所有报告，**统一审查**：
   - 消除相互矛盾的建议
   - 合并重叠修复项
   - 确认译名一致性
5. 生成 fixes.json，运行 `apply_fixes.py` 一次性应用到全部文件

**子代理 Prompt 模板**：

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

### 阶段三：机翻语气润色（抽样精读）

> 此阶段与错误修复不同：目标不是翻译错误，而是修正"读起来像机翻"的不自然表达。

**问题**：逐句通读全部剧集成本太高。

**方法**：抽样精读 → 识别普遍模式 → 全局修正。

1. **抽选样本**：从全部剧集中抽 3-5 集代表性样本
   - 建议选开头、中间、结尾各 1-2 集（如第 1、20、50、80、109 集）
   - 若番剧分篇章，每篇章至少抽 1 集
2. **逐句精读样本**：对照参考字幕，标记所有带"机翻感"的表述：
   - 生硬的直译句式（如 "What's wrong?" → `什么是错的？` 而非 `怎么了？`）
   - 不符合口语习惯的表达（如 `我表示歉意` 而非 `对不起`）
   - 过度书面化的对话用语（如 `我们应当前往` 而非 `我们走吧`）
   - 奇怪的量词/代词选择
   - 不自然的语序
3. **判定扩散范围**：对每个标记项判断：
   - 🌐 **普遍模式**：同一种表达方式可能在多集中反复出现 → 生成 `replace_global` 规则
   - 📍 **单集偶发**：仅该场景特有的生硬表达 → 用 `replace_text` 逐行修复
4. **执行修复**：生成 fixes.json，运行 `apply_fixes.py`
5. **追加到报告**：所有修正记入「统一修改」或「单集特定错误」（按 ≥3 集标准判定）

**常见机翻语气模式**（参考）：

| 机翻语气 | 自然表达 | 说明 |
|----------|----------|------|
| 过度完整的"的"字结构 | 口语省略 | 如 `他是很重要的` → `他很重要` |
| 直译的英语从句结构 | 中文流水句 | 如 `这是那个我昨天见过的男人` → `我昨天见过这个人` |
| 被动语态直译 | 中文主动表达 | 如 `我被告诉了` → `有人告诉我` |
| 生硬的敬语/谦辞 | 日常口语 | 如 `非常感谢您` → `太谢谢了` |
| 书面化的连接词 | 口语连接 | 如 `因此`/`然而` → `所以`/`不过` |

### 阶段四：残留检查

重新运行关键检测脚本，确保无遗漏：

```bash
python scripts/bilingual_detect.py --target-dir ./target/ --config config.json > check_bilingual.json
python scripts/source_lang_detect.py --target-dir ./target/ --config config.json > check_source.json
python scripts/repeat_detect.py --target-dir ./target/ > check_repeat.json
python scripts/source_char_detect.py --target-dir ./target/ --config config.json > check_chars.json
python scripts/interjection_detect.py --target-dir ./target/ --config config.json > check_interjections.json
```

---

## 输出文档格式

校对结束后，**必须**输出一份 `字幕校对报告.md`，包含以下结构：

```markdown
# [番组名] 字幕校对报告

## 总体统计
| 指标 | 数值 |
|------|------|
| 处理集数 | N |
| 总修复行数 | ~N |
| 参考字幕 | N 个文件 |
| 校对日期 | YYYY-MM-DD |

## 统一修改

> 以下修改应用于全部 N 集

### [类别名]（行数）
| 原文 | 修复为 | 原因 |
|------|--------|------|
| ... | ... | ... |

## 单集特定错误

### 第 N 集
| 行号 | 原文 | 修复为 | 原因 |
|------|------|--------|------|
| ... | ... | ... | ... |

## 技术要点
- ASS 解析注意事项
- 正则经验
- 遇到的坑和解决方案

## 已知遗留问题
- 列出因信息不足未修复的项目
```

### 统一修改 vs 单集特定错误 的判定标准

- **统一修改**：相同修复模式出现在 ≥3 集 → 归入统一修改（使用 `replace_global`）
- **单集特定错误**：仅 1-2 集出现，或修复内容完全不同 → 归入单集特定（使用 `replace_text` 指定行号）

### 机翻语气润色 的记录方式

在「统一修改」表中新增一行标注抽样范围：
```
> 以下修改基于第 X、Y、Z 集抽样精读，判定为跨集普遍模式后全局应用
```
单集偶发的生硬表达直接记入「单集特定错误」，标注为「机翻语气」。

---

## 技术备忘

### ASS 文件处理
- **解析**：使用 `split(',', 9)` 防止文本中逗号破坏解析
- **编码**：始终使用 UTF-8
- **样式过滤**：只修改 Default/Episode 等对话样式，跳过 Display（卡拉OK 特效层）
- **内联标签**：`{...}` 阻断正则匹配，需先 strip 标签再检测
- **时间码匹配**：使用 500ms 容差处理参考字幕与目标字幕的微小时间差

### 正则避坑
- **方括号路径**：`glob.glob` 将 `[tag]` 目录名视为字符类 → 用 `os.listdir` 替代
- **单引号 raw string**：`r'...\'...'` 中 `\'` 被解析为转义引号 → 用双引号 raw string
- **嵌套字符类**：`[一-鿿.。!！?？...]` 在较新 Python 版本触发 FutureWarning
