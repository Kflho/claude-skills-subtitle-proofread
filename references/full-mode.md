# 完整模式（有参考字幕）

> ⚠️ **适用条件**：有参考字幕。SRT-only 项目或日语原文项目部分脚本不适用。
> 加载条件：用户提供了参考字幕目录（路径 #2）。
>
> **2026-07 更新**：字符层扫描（双语混合/源语言/字符残留）已由 `unified_scanner.py` 单次遍历替代。旧脚本 `bilingual_detect.py` / `source_lang_detect.py` / `source_char_detect.py` 已删除。

此模式可验证翻译正确性、统一专有名词、修复 OP/ED 歌词。所有检测脚本按项目特征自动选用。

## 参考字幕的用法

参考字幕用于**按时间码定位对照**，不是直接替换。核心原则：
- 逐行对比同一时间码的目标文本与参考文本
- 发现不一致时，以参考字幕的语义为准修正目标文本
- 参考字幕中的专有名词（人名、地名、称谓）统一采纳为全剧标准

---

## 阶段一：规则批量修复

按以下顺序执行。每步流程：运行检测脚本 → Claude 审查输出 → 生成 fixes.json → 运行 apply_fixes.py。

1. **统一扫描（替代旧版独立脚本）** — `unified_scanner.py --output-findings ... --output-issues ... --build-glossary`
2. **ASS 格式修补** 📎 — `ass_repair.py --check all`（ASS only，涵盖 names/styles/drawing/comment/oped）
3. **繁体中文→简体** — `trad_to_simp_detect.py --auto`（仅中文目标语言，日文自动空操作）
4. **感叹词替换** — `interjection_detect.py`（翻译项目）
5. **专有名词统一** 📎 — `proper_noun_detect.py --ref-dir <REF_DIR>`
6. **机翻幻觉检测** — `garbled_detect.py --lang <LANG>`（中文目标语言）
7. **固定格式统一** — `format_detect.py --lang <LANG>`（翻译项目）

## 阶段二：批量精读审查 📎

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

目标文件（机翻）: {目标目录}/{文件名前缀} {集号范围}.{格式}
参考文件（人工翻译）: {参考目录}/{文件名前缀} {集号范围}.{格式}

任务：
1. 逐行对比同一时间码的目标文本和参考文本
2. 找出所有翻译错误、用词不当、机翻幻觉
3. 以 OLD → NEW 格式输出修复列表
4. 标注每条修复的原因

输出格式（每行一条）：
文件名 | 行号 | OLD文本 | NEW文本 | 原因
```

## 阶段三：机翻语气润色（抽样精读）📎

> 此阶段与错误修复不同：目标不是翻译错误，而是修正"读起来像机翻"的不自然表达。

**方法**：抽样精读 → 识别普遍模式 → 全局修正。

1. **抽选样本**：从全部剧集中抽 3-5 集代表性样本
2. **逐句精读样本**：对照参考字幕，标记所有带"机翻感"的表述
3. **判定扩散范围**：对每个标记项判断：
   - 🌐 **普遍模式** → 生成 `replace_global` 规则
   - 📍 **单集偶发** → 用 `replace_text` 逐行修复
4. **执行修复**：生成 fixes.json，运行 `apply_fixes.py`

## 阶段四：残留检查

重新运行关键检测脚本，确保无遗漏：

```bash
# 字符层（所有项目）
python scripts/unified_scanner.py --target-dir <DIR> --output-findings temp/scans/check.json

# 中文项目
python scripts/trad_to_simp_detect.py --target-dir <DIR> > check_trad.json
python scripts/garbled_detect.py --target-dir <DIR> --lang zh > check_garbled.json

# 翻译项目
python scripts/interjection_detect.py --target-dir <DIR> --config interj_config.json > check_interj.json
python scripts/format_detect.py --target-dir <DIR> > check_format.json

# ASS only
python scripts/ass_repair.py --target-dir <DIR> --check names,comment
```

## 阶段五：反馈迭代（每次必做）

校对报告完成后：

### 第一步：Claude 输出迭代提案

| # | 类型 | 发现 | 建议更新 | 更新目标 |
|---|------|------|----------|----------|
| 1 | 新错误模式 | xxx | 补充到错误清单 | SKILL.md |
| 2 | 脚本增强 | xxx 被漏检 | 增强 detect 脚本 | scripts/xxx.py |

**约束**：每条提案必须有数据支撑（如「影响 60/109 集」），禁止凭空建议。

### 第二步：人工审核后写入

1. 用户审查提案表，逐条确认/拒绝/修改
2. 仅对**用户明确批准**的项执行更新
3. 在报告文件夹的 `skill-changelog.md` 追加条目，并同步到 skill 根目录的 `ITERATION_LOG.md`

> ⚠️ Claude 不得在用户审核前自行修改 skill 文件。提案是建议，不是许可。

---

## 完整模式专属错误类型

以下错误类型仅在完整模式下可检测（需要参考字幕对照）：

### 音译代替意译（极高频）📎

源语言感叹词/拟声词被音译而非翻译。对照参考字幕同一时间码确定正确语义。

### 机翻幻觉 / 假朋友（高频）📎

MT 将专有名词误译为字面意思。运行 `garbled_detect.py --lang <LANG>`（内置常见幻觉模式），对照参考字幕逐条确认。

| 类型 | 示例 |
|------|------|
| 人名→名人幻觉 | `Abe`→`安倍晋三`（应为角色名） |
| 假朋友 | `sister`→`修女` 而非 `姐姐` |
| 脏话误译 | "Serves you right!"→`去你妈的` |

### OP/ED 歌词异常（每集固定出现）📎

运行 `ass_repair.py --check oped`。常见问题：行数不一致、Comment 行残留、文本变体。
> ⚠️ ASS only。

### 节目名/角色名/术语不统一（高频）📎

运行 `proper_noun_detect.py`，关注 "仅在机翻中出现" 的专名（可能是幻觉）。

### 源语言文本损坏（反转/乱码）📎

部分参考字幕存在字符级反转或编码损坏。对照参考字幕恢复后翻译。
