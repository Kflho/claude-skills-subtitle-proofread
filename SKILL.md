---
name: subtitle-proofread
description: >
  Use when the user wants 字幕 handled end to end: transcribe video with Whisper
  ASR, translate subtitles to Chinese, unify proper nouns across episodes, or
  proofread existing SRT/ASS. Unfixable items get [???] markers for Aegisub review.
---

# Subtitle Proofread

3-phase pipeline：扫描（乱码 + VAD语音时间线）→ Whisper 统一修复 → 专名统一 + 交付。无法自动修复的条目写入 `[???]` 标记，在 Aegisub 中审查。

**资源驱动**：有什么用什么。有视频+Whisper→修复乱码+补全缺字幕；有参考字幕→注入 AI 校对上下文。缺资源也能残血运行——跳过缺失步骤，剩余步骤照常。

## 专名审查（AI 驱动）

> **为什么用这个流程**：旧词典扫描法（`unified_scanner` → `build_glossary`）从词频里挑映射，
> 噪声大且会产生幻觉标准名（把「天間」定成「天间」，正确应为「天马博士」）。新流程让 AI
> 从真实文本里挑专名实体，**脚本只做 AI 决策的执行工具，不做决策本身**。

**三步闭环**（共享核心 `extract_names(ja_cues, zh_cues)`，翻译流程内 + 独立 CLI 同一实现）：

```bash
cd "<project-root>"

# 1. 提取（翻译时自动：translate_srt.py --extract-nouns；或对已翻译文件独立跑）
python "<scripts-dir>/nouns/extractor.py" \
  --ja-dir "<日文源>" --zh-dir "<中文翻译>" -e EP001-EP005 -o temp/nouns

# 2. 聚合 → running-map（跨集增量合并）
python "<scripts-dir>/nouns/aggregate.py" \
  --map temp/noun_map.json --extracted-dir temp/nouns -o temp/noun_map.json

# 3. 应用：先 dry-run 预览 → 🤖 审查 map → 再写入
python "<scripts-dir>/nouns/apply_map.py" temp/noun_map.json \
  --target-dir "<中文翻译>" --dry-run
python "<scripts-dir>/nouns/apply_map.py" temp/noun_map.json \
  --target-dir "<中文翻译>" --apply
# 导出 ja→zh 幻觉控制表（供 translate_srt --mappings）
python "<scripts-dir>/nouns/apply_map.py" temp/noun_map.json \
  --target-dir "<中文翻译>" \
  --emit-mappings temp/noun_map_ja_to_zh.json --merge-existing temp/noun_mappings.json
```

**🤖 审查点（不可跳过）**：dry-run 之后、`--apply` 之前，审查 `temp/noun_map.json` ——
定标准名、定 scope、删误判变体（例：提取可能把标题「铁腕阿童木」并进「阿童木」变体，删掉即可）。

**scope 语义**：`global` = 跨全集统一（主要角色 / episodes≥2）；`per_episode` = 仅该实体
出现的集；`auto` = episodes≥2 取 global，否则取 per_episode。

**提取续跑**（并发分片被 API 限速时 ~17% 集的 chunk LLM 调用会静默失败漏实体；sidecar 记
`chunks: {chunks_total, chunks_failed}`，失败与空结果分开记账）：

```bash
# 健康度 ✅ 健康 / ⚠ 失败或过少 / ⬜ 未提取（不调 LLM）
python "<scripts-dir>/nouns/extractor.py" \
  --ja-dir "<日文源>" --zh-dir "<中文翻译>" -o temp/nouns --status

# 中断后续跑：跳过健康 sidecar，只跑缺失/失败集（自动重试 chunk 失败）
python "<scripts-dir>/nouns/extractor.py" \
  --ja-dir "<日文源>" --zh-dir "<中文翻译>" -o temp/nouns --resume
```

> 全量提取后**先 `--status` 确认无 ⚠ 再进聚合**，避免漏实体污染 map。
> 旧 sidecar（无 `chunks` 字段）以实体数作失败代理（`--min-entities`，默认 2）。

**chunk 失败降级（DeepSeek 超长请求）**：大 chunk（默认 120 对 cue）下 LLM 对超长请求可能
返回 **HTTP 200 + 空 `message.content`** → `call_chat` 静默返回 `''` → chunk 判失败但
**日志无报错**（症状：`--status` 成片 ⚠ 而日志干净）。用 `--chunk-size 60` 再 `40`
逐级收敛（实测 74→22→5→0 失败）。

**清单记录**（`--emit-checklist` 与 `--apply` 同用 = 记录实际统一项）：

```bash
python "<scripts-dir>/nouns/apply_map.py" temp/noun_map.json \
  --target-dir "<中文翻译>" --apply --emit-checklist temp/ai_review_checklist.txt
```

行格式 `- [x] NN集：规范名（变体、变体）`，只记实际发生替换的集，`[x]` 表示 AI 已审查。
把行追加到项目清单的 `## ai 审查` 版块；`## 人工审查` 版块不动。

**`_build_repl` 双写防护**（apply_map 按变体-标准名关系生成替换正则）：
- 变体是标准名精确后缀（茶水博士→御茶水博士）→ `(?<!前缀)变体`，防「御茶水博士博士」
- 变体是标准名精确前缀（乌拉尔→乌拉尔号）→ `变体(?!后缀)`，防「乌拉尔号号」
- 变体与标准名是近似音译（多里安→多利安博士）→ 裸匹配 + `变体(?!标准名全部≥2字真后缀)`

> ⚠️ 近似音译变体是双写高发区：apply 后跑一次双写检测（匹配 `标准名+标准名尾2~3字`），
> 确认 apply 引入 0 处。

**数据流**：

| 产物 | 内容 | 用途 |
|------|------|------|
| `temp/nouns/extracted_EP###.json` | 每集专名实体 `{ja_forms(含幻觉), zh_forms(含变体), count, samples}` | 聚合输入 |
| `temp/noun_map.json` | running-map `{标准名: {ja_forms, zh_variants, episodes, scope}}` | AI 审查后应用 |
| `temp/noun_map_ja_to_zh.json` | ja→zh 幻觉控制表 | translate_srt `--mappings` |

## 专名审查（旧版 auto_translate · legacy）

> ⚠️ **已弃用**，仅存量项目参考。旧法用 `auto_translate.py` 扫候选 + 逐条审查（噪声大，
> 且把 AI 当搬运工）。**新项目一律走上方「专名审查（AI 驱动）」**；完整迭代循环
> （扫描 → API 批量分类 → 黑名单 / 映射表 → 重跑至归零）见
> [references/batch-review.md](references/batch-review.md)。

```bash
python "<scripts-dir>/auto_translate.py" \
  --source-dir "<日文源>" --target-dir "<中文翻译>" \
  --mappings temp/noun_mappings.json
```

**关键文件**：`temp/scans/candidates.json`（审查输入）、`temp/noun_mappings.json`（ja→zh 映射）、
`temp/zh_common_blacklist.json`（中文普通词黑名单）、`temp/scans/classified_terms.json`
（API 分类结果）。黑名单经 `--zh-blacklist` 传给 `find_suspect_nouns.py`，从源头减少误报。

## ASS 格式项目

本 skill 同时支持 **SRT** 和 **ASS** 两种格式。所有工具通过 `read_subtitles()`/`write_subtitles()`（`lib.subtitle_io`）或 `parse_subtitles()`/`write_srt()`（`lib.whisper_utils`）自动检测格式，无需手动转换。

> **注意**：如果项目是 ASS 格式，`--input-dir` 指向包含 `.ass` 文件的目录即可。Pipeline 会像处理 SRT 一样处理 ASS，输出保持 ASS 格式。

## 行为分级：哪些默认做，哪些等指令

每个会**写盘**的行为都在 `lib/gates.py` 登记一个级别。判据只有一条：
**这一步会不会在用户没要求的情况下改变字幕内容？** 会 → 至少 L1。

| 级别 | 含义 | 包含 |
|---|---|---|
| **L0** | 默认必做，只读 | 扫描/编码探测、VAD 台词↔人声匹配（含「有人声无字幕」缺口清单）、交付校验 |
| **L1** | 默认只检测并标注，写盘要开关 | VAD 删「无人声」条（`--vad-clean-apply`） |
| **L2** | 只在明确要求时执行 | OP/ED 预替换、专名预替换、TV 复用覆写、润色、繁→简、`fixes.json` 改写 |
| **L3** | 执行前必须确认 | 覆盖已交付字幕、覆盖既有 `.bak` |

```bash
# 看当前分级（run_all.py 每次启动也会打印）
python -c "import sys; sys.path.insert(0,'<scripts-dir>'); from lib.gates import format_table; print(format_table())"
```

**为什么 VAD 删条是 L1 而不是 L0**：本作对白垫 BGM，「对白+BGM」会被判非语音 ——
Silero 硬过滤单集丢过 243/463 条真实台词，`はい` 这类 2 假名真词也在删除名单里。
所以默认只出清单，**VAD 只作提示，不作硬判据**。库级默认也取安全侧：
`vad_delete_nonspeech(apply=False)`、`fix_by_whisper(vad_clean_apply=False)` ——
漏传参数的调用方正是踩过坑的那一类。

新增会写盘的功能时，先在这里加一条 `Gate(...)`，再写实现。

## 首次使用？

检查项目 `CLAUDE.md` 末尾是否有 `## SKILL INITIALIZED: true`。

**没有** → 首次使用。读取 `references/first-run.md`，跟随初始化向导完成配置后再继续。

**有** → 已初始化。从 CLAUDE.md 获取路径，直接进入 pipeline。

> 如需重新初始化（添加参考字幕、更换模型等），删除 CLAUDE.md 中的 `SKILL INITIALIZED: true` 行即可。

## 运行

### 环境设置

首次使用 → [references/setup.md](references/setup.md)（环境变量、Python 依赖、API 密钥、git 备份铁律）

已验证过的项目跳过，直接从 CLAUDE.md export 环境变量即可。

### 跑 pipeline

**⚠️ 破坏性改动前必须 git 备份。** Pipeline 的 Phase 2/3 会直接修改 SRT 文件（原地覆写），
没有撤销按钮。跑 pipeline 前：

```bash
cd "<project-root>"
git add -A && git commit -m "备份：pipeline前 — $(date +%Y-%m-%d)"
```

> 如果项目目录还不是 git repo，SKILL.md 加载后第一时间 `git init` + `git add -A` + `git commit`。
> 开发者模式下修改 skill 文件前后也需要 git 备份（skill 目录和项目目录各一份）。

```bash
cd "<project-root>"

# 完整 pipeline（推荐）：扫描 + VAD 语音检测 + Whisper 统一修复
python "<scripts-dir>/run_all.py" \
  --input-dir "<SUBTITLE_DIR>" \
  --video-dir "<VIDEO_DIR>"

# 残血运行（无视频/Whisper）：仅字符扫描 + 专名统一
python "<scripts-dir>/run_all.py" \
  --input-dir "<SUBTITLE_DIR>" \
  --skip-whisper

# 仅扫描预演（不改文件）
python "<scripts-dir>/run_all.py" \
  --input-dir "<SUBTITLE_DIR>" \
  --video-dir "<VIDEO_DIR>" \
  --dry-run

# 指定集数范围
python "<scripts-dir>/run_all.py" \
  --input-dir "<SUBTITLE_DIR>" \
  --video-dir "<VIDEO_DIR>" \
  --limit 5
```

> `--input-dir` 指定字幕子目录（默认 `AI审查后`）。`--lang` 自动检测。
> `--video-dir` 启用 VAD 语音检测 + Whisper 统一修复（乱码、部分重叠、缺字幕一次处理）。无视频时加 `--skip-whisper` 残血运行。
> `--limit` 只限 Phase 2 修复集数，扫描覆盖全部文件。
> **VAD 删条默认不写盘**（分级 L1）：先看清单，确认无误再 `--vad-clean-apply`。

### 模块化调用

`run_all.py` 适合一键跑完，但每个 Phase 的底层脚本也可独立调用——调试/定制流程/单步骤重跑时不用从头来。

**Phase 1：扫描**（只读，不改文件）

```bash
# 全量扫描：乱码 + 词频 + 词表生成
python "<scripts-dir>/scan/unified_scanner.py" \
  --target-dir "<SUBTITLE_DIR>" \
  --output-findings temp/scans/findings.json \
  --build-glossary --project-lang zh

# 从 findings 生成专名表
python "<scripts-dir>/nouns/build_glossary.py" \
  --findings temp/scans/findings.json \
  -o reports/proper-nouns.md \
  --mappings-output temp/noun_mappings.json
```

**Phase 2：修复**（改 SRT，逐集运行）

```bash
# 单集修复（audio 模式：VAD + Whisper）
python "<scripts-dir>/fix/episode_workflow.py" EP001 \
  --mode audio --project-dir "<PROJECT_DIR>"

# 单集修复（text 模式：参考字幕对比）
python "<scripts-dir>/fix/episode_workflow.py" EP001 \
  --mode text --project-dir "<PROJECT_DIR>"

# 仅预览（不改文件）
python "<scripts-dir>/fix/episode_workflow.py" EP001 --dry-run

# 单步骤拆分
python "<scripts-dir>/fix/episode_workflow.py" EP001 --step audio      # VAD + Whisper 转录
python "<scripts-dir>/fix/episode_workflow.py" EP001 --step translate  # 翻译参考字幕
python "<scripts-dir>/fix/episode_workflow.py" EP001 --step compare    # 对比 Whisper vs 参考
python "<scripts-dir>/fix/episode_workflow.py" EP001 --step apply      # 应用修复
python "<scripts-dir>/fix/episode_workflow.py" EP001 --step ai-review  # AI 审查碎片
```

**Phase 3：专名统一**（⚠️ 旧词典法，已弃用 → 新项目用「专名审查（AI 驱动）」流程）

```bash
# legacy：日文源 + 中文翻译对照审查
python "<scripts-dir>/auto_translate.py" \
  --source-dir "<日文源>" --target-dir "<中文翻译>" \
  --mappings temp/noun_mappings.json

# legacy：无日文源时只扫中文侧专名（不比对 SRT）
python "<scripts-dir>/nouns/find_suspect_nouns.py" \
  --target-dir "<中文翻译>" --project-lang zh
```

**OP/ED 专项工具**（独立于 pipeline，可单独调用）

```bash
# oped_fixer: 跨集文本聚类 + 统一已有 OP/ED 文本
# --detect-boundaries 用 API 检测边界（替代硬编码），--auto-only 仅清理器乐幻觉
python "<scripts-dir>/fix/oped_fixer.py" "<SUBTITLE_DIR>" \
  --lang zh --detect-boundaries --auto-only -o temp/scans/oped_fixes.json

# oped_fill: 三步全API空白行填充（需 --video-dir 提取音频）
# Step1: API边界检测 → Step2: API器乐/人声分类 → Step3: API翻译+模板填充
python "<scripts-dir>/fix/oped_fill.py" "<SUBTITLE_DIR>" \
  --video-dir "<VIDEO_DIR>" --lang zh --dry-run

# API 边界检测（独立测试）
python "<scripts-dir>/lib/oped_detect.py" "<SUBTITLE_DIR>" --lang zh --dry-run
```

**Whisper 批量转录**（从视频生成 SRT，不依赖已有字幕）

```bash
# 从视频直接 Whisper 转录 — 无需任何已有字幕文件
python "<scripts-dir>/whisper_batch_transcribe.py" \
  --video-dir "<VIDEO_DIR>" \
  --output-dir "<OUTPUT_DIR>" \
  --lang ja

# 限制前 N 集测试
python "<scripts-dir>/whisper_batch_transcribe.py" \
  --video-dir "<VIDEO_DIR>" \
  --output-dir "<OUTPUT_DIR>" \
  --lang ja --limit 3

# 只转录指定集（支持 EP001-EP010 / SP01,SP02 / 1-3）
python "<scripts-dir>/whisper_batch_transcribe.py" \
  --video-dir "<VIDEO_DIR>" \
  --output-dir "<OUTPUT_DIR>" \
  --lang ja --episodes SP01,SP02

# 只列出会选中哪些视频，不真跑
python "<scripts-dir>/whisper_batch_transcribe.py" \
  --video-dir "<VIDEO_DIR>" --output-dir "<OUTPUT_DIR>" --dry-run
```

> 适用：没有任何字幕文件，或已有字幕质量太差不值得修复。
> 输出：Whisper 自带 VAD 分段，直接生成完整 SRT。
> **集号识别**：`lib/video_scan.py` 直接枚举视频文件再推导集号 token（`episode_token()`），
> 不再硬编码 EP001-EP194 —— **EP### / SP## / OVA## / 特别篇等任意命名都能跑**，
> 同目录混排也没问题。裸数字集号要求左右均为非字母数字，故
> `[960x720]` / `10bit` / `A3D3AE54` 里的数字不会被误取；输出文件名即推导出的
> token（`SP01.srt`、`EP064.srt`）。`--episodes` / `--start-from` 同样走 token
> （`translate_srt.py` 的 `-e` / `--start-from` 亦然）。
> **时间戳自愈**：`-p`（processors）> 1 时 whisper.cpp 的 `whisper_full_parallel`
> 在某些 build 上不对第 2..N 块施加时间偏移，表现为分块边界之后时间戳集体塌成 0。
> 脚本检测到此类损坏会自动改用 `-p 1` 重跑一次并打印告警，无需人工干预。

**参考字幕复用**（同一部作品的现成译文 → 覆盖我们的机译）

参考字幕若与目标是**同一部作品**且共用素材（总集篇、重制版、特别篇拿原片片段），
其中整句的人工译文可直接覆盖机译。**先看命中分布判断有没有素材，再动手。**

```bash
# 1. 配对（LLM 拿源语言原句逐对复核；结果有缓存，重跑只补新对）
python "<scripts-dir>/reuse/align.py" -e SP01,SP02 \
  --source-dir "<源语言转录>" --target-dir "<我们的译文>" \
  --reference-dir "<参考字幕目录>" --reference-glob '*.SC.ass' \
  --variants temp/reuse_variants.json --report temp/reuse/report.txt

# 2. 🤖 看报告：命中是连续成段（真复用）还是均匀散布（巧合）

# 3. 改写：先 dry-run 看判据，再 --apply（留 <集号>.srt.bak）
python "<scripts-dir>/reuse/apply.py" -e SP01 \
  --source-dir "<源语言转录>" --target-dir "<我们的译文>" \
  --reference-dir "<参考字幕目录>" --variants temp/reuse_variants.json
python "<scripts-dir>/reuse/apply.py" -e SP01 ... --apply
```

→ 分布判定、采纳判据 R1–R6、`--variants` 写法归一 config 见
[references/reuse-reference-subs.md](references/reuse-reference-subs.md)

**Whisper 切段修复**（全量翻译后补 `[???]` 标记的 cue）

全片 Whisper 在多人争吵/对话密集场景会把多句合并成一条长 cue，导致 VAD 分段失准、输出乱码。LLM 无力翻译乱码时标 `[???]`。
`whisper_spot_fix.py` 对指定时间轴切段单独重跑 Whisper + 翻译，输出干净的中文参考：

```bash
# 单段
python "<scripts-dir>/whisper_spot_fix.py" EP001 --start 24:35 --end 24:44

# 多段
python "<scripts-dir>/whisper_spot_fix.py" EP042 --spots "12:10-12:18,18:30-18:42"

# 仅日文（不调翻译 API，手动判断）
python "<scripts-dir>/whisper_spot_fix.py" EP001 --start 24:35 --end 24:44 --no-translate
```

> 原理：短音频窗口让 Whisper 内部 VAD 分段更准确，避免全片模式下长 cue 合并导致的乱码。
> 输出 JA（Whisper 干净日文）+ ZH（LLM 翻译），供人工校对参考。
> 使用 `--spots` 可一次指定多段，`--padding` 控制切段时间轴外扩秒数（默认 3s）。
> `--model`/`--base-url` 覆盖翻译 API，默认使用 `LLM_MODEL`/`LLM_BASE_URL`。

### 用户发字幕轴 → Claude 校对流程

用户发字幕时间轴（SRT 或 ASS 格式）时，表示要求对该段进行精确校对。

**立即执行的步骤：**

1. **确定集号** — grep 用户提供的文本找到对应 EP
2. **运行切片 Whisper** — 自动覆盖用户提供的时间段（外扩 padding）：
   ```bash
   python "<scripts-dir>/whisper_spot_fix.py" EP### --start <最早时间> --end <最晚时间> --no-translate
   ```
3. **读取上下文** — 读中日 SRT 文件，取需要校对的 cue **前后各 2 句**

**输出格式（严格遵守）：**

仅输出以下内容，不要加任何解释、分析、结论：

```
前 [时间轴]
  JA: <日文原文>
  ZH: <现有中文翻译>

  [用户提供的时间轴]  ← 使用用户提供的时间轴，不要用 Whisper 时间戳
  JA: <切片 Whisper 日文参考>
  ZH: <建议中文修复>     ← 综合日文原文 + Whisper 参考给出

  [用户提供的时间轴]  ← 同上
  JA: ...
  ZH: ...

后 [时间轴]
  JA: <日文原文>
  ZH: <现有中文翻译>
```

**关键规则：**
- **时间轴用用户的**：Whisper 自身时间戳只作内部参考。用户已经对好轴。
- **前后各 2 句**：让用户看到修复后的句子如何嵌入上下文。
- **发现幻觉 cue**（切片 Whisper 在同一时间段输出完全不同的文本）：JA 行标注 `⚠️ 原为幻觉，日文无对应`，ZH 给出切片参考翻译。
- **只输出这份对照**：轴 + JA + ZH，到此为止 —— 用户拿到参考自己判断。

**Whisper 幻觉重复**（已知问题，翻译阶段处理）

在 AI 翻译的 system prompt 中告知 LLM：
> "连续多条字幕文本相同或高度相似是 Whisper 的 bug（幻觉重复），
> 照常翻译每条即可，不要尝试区分或合并它们。最终由人工审查决定保留哪条。"

> 切片重跑 Whisper 已验证无效（根因在音频本身，重跑只换一组幻觉），自动审查同样不可行，
> 只能留到人工校对阶段。详见 [references/workflows.md](references/workflows.md) 的「已知问题」。

**Phase 4：AI 润色**（--lang zh 项目可选）

```bash
python "<scripts-dir>/polish_zh.py" --input-dir "<SUBTITLE_DIR>"
```

### 翻译并行机制（v2）

`translate_srt.py` 支持**集内 batch 并行**：
- 每个 batch 的上下文用**日文原文**（前 3 条 cue），无需等翻译结果
- 所有 batch 通过 `ThreadPoolExecutor` 同时发出（max_workers=min(batches, 16)）
- 每集从 ~40s（串行）降至 ~3s（并行），193 集约 10 分钟

> 上下文用日文原文优于中文翻译结果——LLM 本身做日→中翻译，看原文更能理解语流。

### LLM API 配置

翻译（`translate_srt.py`）、润色（`polish_zh.py`）、fix_tail_wo 翻译步骤共用 `LLM_API_KEY`。

> ⚠️ **模型名称**：用 `deepseek-flash`（`config.py` 的 `LLM_MODEL_DEFAULT`）。
> 旧名 `deepseek-v4-flash` / `deepseek-v4-pro` 仍可调用，但对应模型已下线，
> 请求由 DeepSeek-V4.1-Flash 提供并按 Flash 计费——**直接用新名**。
> `deepseek-chat` 已失效（返回 HTTP 400）。
>
> ```bash
> export LLM_API_KEY='sk-...'
> export LLM_MODEL='deepseek-flash'         # 默认值，对应 config.py LLM_MODEL_DEFAULT
> export LLM_BASE_URL='https://api.deepseek.com/v1'
> ```
>
> 推荐写入 `~/.claude/settings.json` 的 `env` 字段持久化，不污染项目 CLAUDE.md。

### 4. 验证

**必须**执行，不靠 "Pipeline complete" 判断成功：

1. 读 `reports/问题解决报告.md`
   - **文件存在** → 搜索 `⬜`
   - **文件不存在**（单文件/残血模式常见）→ 读 `temp/scans/findings.json`，检查 `garbled_cues` 和 `per_episode_issues`
2. 搜索 `⬜` → 逐个分析每条 ⬜：
   - 同一 (EP, 时间) 在「Whisper自动修复」section 已有 ✅ → **false alarm**，忽略
   - 同一 (EP, 时间) 在 AI fragment section 有 correction 非空但仍是 ⬜ → SRT 已修，报告未同步，忽略
   - 其余 → 回到对应[暂停点](#暂停点--action)处理
3. 确认 Phase 3「疑似专名搜索」非空（非"暂无记录"）
4. **与日文源逐条对数量** —— 条数对齐，且逐条时间轴对齐。文本非空、非日文的破坏
   （OP/ED 窗口吞掉对白、cue 被覆写）**不会**在 ⬜ 里露头，只有对数量才发现。
5. 0 条**真正待处理**的 ⬜ 且第 4 步对齐 → 完成

> 脚本 exit 0 ≠ 成功。同一个 cue 可能出现在报告多个 section，一边 ✅ 就算干净。
>
> **残血模式**（无视频/Whisper）：Phase 2 跳过，garbled cues 流入 `问题解决报告.md` 的「未修复乱码」section。需手动/AI 逐条处理，对照参考字幕修复后删除 ⬜。

## 语言限制

| 功能 | ja（日语） | zh（中文） | ru（俄语） | 其他 |
|------|:---:|:---:|:---:|:---:|
| 乱码扫描 | ✅ | ✅ | ✅ | ✅ |
| LLM 翻译 | ✅ (ja→zh) | — | ✅ (ru→zh) | ✅ (任意→zh) |
| Whisper 修复 | ✅ (kotoba) | ⚠️ 需中文模型 | ❌ | ⚠️ 需对应模型 |
| Baidu 翻译层 | ❌ (日语目标无需) | ✅ (Whisper 输出 ja→zh) | ❌ | ❌ |
| 词典过滤 | ✅ (jamdict/JMdict) | ✅ (jieba/498K 词) | ❌ | ❌ |
| 专名分类 | ✅ (jamdict) | ✅ (jieba + 规则) | ❌ | ❌ |
| Glossary 清洗 | ✅ (JMdict + 规则) | ✅ (jieba 词典 + 规则) | ❌ | ❌ |
| AI 润色（去翻译腔） | ❌ (日语原文无需) | ✅ (OpenAI 兼容 API) | ✅ (同 zh) | ✅ (同 zh) |

> `translate_srt.py` **自动检测源语言**（ja/ru/zh），动态切换 system prompt，无需手动指定。
> 日语检测到假名时启用专用规则（敬语、主语省略等），非日语跳过。
> `--lang zh` 时使用 jieba 分词 + 词典查询对标 jamdict。jieba 不可用时退回 n-gram + 启发式规则。
> Baidu 翻译为**可选**：未配置时自动降级，日语原文保留在 AI fragments 中由 AI 自行翻译。
> AI 润色为**可选**：Pipeline 末尾交互提问。需要 `LLM_API_KEY` 环境变量。无 key 时降级为 AI 助理自行润色（⚠️ 高 token 消耗，7.5 万 cue）。
> ⚠️ **translate_srt.py 必须要有 LLM_API_KEY**：无 key 时脚本无法运行。不要静默降级为 AI 自行翻译——量级太大（193 集 × 200 条 = 不可行）。正确做法：告知用户 key 为空，请用户设置后重试。详见 [references/translation.md](references/translation.md)。

## 名词库准备 + 翻译

> **非日语源（ru/en/其他）**：跳过名词库准备（jamdict/jieba 不适用），直接翻译。
> 翻译后对中文输出执行专名校对即可，见上方「专名审查（AI 驱动）」。
> ```bash
> python "<scripts>/translate_srt.py" --input-dir "<源字幕>" --output-dir "<中文输出>"
> # 源语言自动检测，system prompt 动态适配
> ```

翻译项目**必须先准备 `temp/noun_mappings.json`**（ja→zh 幻觉控制表），否则专名翻译不一致。

生成方式（任选其一）：
- **推荐**：新专名流程导出 —— `apply_map.py --emit-mappings <out> --merge-existing temp/noun_mappings.json`
  （见上方「专名审查（AI 驱动）」；逐集翻译→提取→聚合→应用→导出，映射随语料自动累积）
- 或 AI 直接编写/增补该 JSON

🚨 **映射完整性检查 — 翻译前必做**：确认日语源中实际出现的书写形式都在 mappings 中有对应条目。
反面案例：mapping 有「トビラ→飞雄」但没有「扉→飞雄」→ 翻译崩坏。

```bash
# 翻译（--extract-nouns 自动提取本集专名实体，供新流程聚合）
python "<scripts>/translate_srt.py" --input-dir "<日文源>" --output-dir "<输出>" \
  --mappings temp/noun_mappings.json --extract-nouns

# 翻译后验证 — 必须执行（不靠 exit 0 判断成功）
#    a. grep 日语残留（零容忍）
#    b. grep 已知错误专名
#    c. 发现残留 → 手工修复或标 [???]，错误专名 → 回到映射完整性检查
```

→ 完整流程见 [references/translation.md](references/translation.md)

## Pipeline

```
Phase 1: Scan
  → unified_scanner: garbled chars, repeat patterns, term frequency
  → VAD 语音时间线提取（需 --video-dir，无视频自动跳过）
  → build_glossary → proper-nouns.md
  → glossary AI review: AI reads full glossary, manages whitelist/blacklist directly (🤖)
  → Output: findings.json + proper-nouns.md + {EP}_vad.json 缓存
  → Does NOT write to 问题解决报告（scan is read-only）

Phase 2: Triage
  → 若有参考字幕 → 注入 reference_text 到 AI fragments（原文，不翻译）
  → VAD clean: 列出非人声 cue（[音楽][拍手] 等）—— 分级 L1，默认只列不删，
     要真删加 --vad-clean-apply
  → build_fix_regions(): VAD 人声段落 → 统一检测 fix region
     ├─ garbled:      人声覆盖乱码 cue → 清空重录
     ├─ partial_overlap: 人声部分覆盖 cue + 延伸到 uncovered → 清空重录
     └─ missing:      人声完全无 cue → **只出清单，不插新 cue**
                      （凭空多出来的内容要人来点头；见 lib/gates.py）
  → 转 cluster 格式 → Tier 1/2 Whisper → match back → triage
  → classify + triage → auto-keep ✅ / ai_fragments 🤖 / auto-cut 🗑️
  → Baidu 翻译 (--lang zh): Whisper 输出 ja→zh（无凭证时降级 AI 翻译）

Phase 3: Unify
  ├─ Suspect noun search: full-scan (no cap), jieba pre-primed with known names
  ├─ OP/ED fixer (oped_fixer.py): cross-episode text clustering → instrumental auto-clean / vocal AI review
  ├─ OP/ED filler (oped_fill.py): 3-step API pipeline (boundary detect → classify instrumental/vocal → translate + fill blank cues)
  ├─ Noun variant detection → unified candidates.json (全量，AI 逐条审查)
  └─ Deliver: apply all fixes → [???] markers written to SRT for Aegisub review

Phase 4: Polish (--lang zh only, optional)
  └─ 交互提问 → LLM 批量润色（10句/批，OpenAI 兼容 API）
       ├─ 有 LLM_API_KEY → polish_zh.py 自动润色
       └─ 无 key → AI 助理自行润色（⚠️ 高耗费，7.5万 cue）

Report: reports/问题解决报告.md（自动生成，按 Phase 分组）
```

> **专名统一的新流程**：run_all.py 的 Phase 3 是旧词典法（legacy）。新项目用独立的
> extract→aggregate→apply_map 三流程，见「专名审查（AI 驱动）」。

> **mj** = meaningful Japanese character count。mj < 2 = noise。
> AI 审查只读小 JSON 文件（ai_fragments_{EP}.json, ai_review_candidates.json），不读词表全文。

## 暂停点 → Action

Pipeline 不会自动暂停。输出中看到以下关键字时，**停下来处理再继续**。

### 疑似专名搜索

> ⚠️ 旧词典法的暂停点，仅存量项目。新流程无此暂停点 —— 提取/聚合自动完成，AI 只在
> 「专名审查（AI 驱动）」dry-run 后审查 `temp/noun_map.json`。

**触发**: `[review] N candidate(s)` 或 `[suspect-nouns] N entries → report layer 3`

读 `temp/scans/candidates.json`，按每条 candidate 的 `type` 字段处理：`inconsistency`
（已知译法不一致，如「阿托姆」应为「阿童木」）按 `zh_canonical_in_mappings` 编辑 SRT；
`unknown_suspect` 判专名 / 普通词 —— 专名补 `noun_mappings.json` + 统一 SRT，普通词补
`temp/zh_common_blacklist.json`。候选 > 50 条时走 API 批量分类（30 条/批），完整流程见
[references/batch-review.md](references/batch-review.md)。修完重跑至 candidates 归零。

### AI 碎片补全

**触发**: `[ai-review] N pending`（N > 0）或 `Layer 2.5: N entries (N⬜)`

**流程**：

1. 读 `temp/scans/ai_fragments_EP*.json`
2. 对每个 fragment，参考 `original`（原文）、`whisper_attempt`（Whisper 猜测）、`context_before/after`（上下文），判断 `correction`：
   - 能从上下文推断 → 写日语修正
   - 纯噪声 → `__DELETE__`
3. 写回 JSON
4. 运行：`python run_all.py --apply-ai-review --video-dir "<VIDEO_DIR>"`
5. 验证：报告 Layer 2.5 全部 ✅

### 专有名词审查

> ⚠️ 旧词典法暂停点，仅存量项目。新流程的审查点在「专名审查（AI 驱动）」：
> dry-run 后审查 `temp/noun_map.json`（标准名、scope、误判变体）再 `--apply`。

**触发** `[scan] 🤖 AI Glossary Review — N entries`：读 `reports/proper-nouns.md`，逐条判
专名 / 普通词 → 编辑白名单 / 黑名单 → 重跑 `build_glossary`。
**触发** `AI REVIEW NEEDED: N`：读 `ai_review_candidates.json` 判定 → 写
`ai_review_fixes.json` → `--resume`。

→ 详细规则见 [references/interventions.md](references/interventions.md)

### OP/ED 审查

两个互补工具，处理 OP/ED 区域的不同问题：

| 工具 | 处理对象 | 数据来源 | 输出 |
|------|---------|---------|------|
| `oped_fixer.py` | **已有文本的 cue**（幻觉、变体） | SRT 文本跨集聚类 | 统一为 canonical / 器乐幻觉→[音楽] |
| `oped_fill.py` | **空白行 cue**（无文本） | 视频音频→Whisper→LLM | 填入歌词翻译 / 保持空白（器乐） |

**oped_fixer 触发**: `[oped] AI review candidates` + `vocal_clusters > 0`

1. 读 `temp/scans/oped_ai_review.json`
2. 填每个 candidate 的 `canonical`（`__INSTRUMENTAL__` = 器乐）
3. 运行：`python run_all.py --apply-ai-review --video-dir "<VIDEO_DIR>"`

**oped_fill 用法**（三步全 API，不需人工介入）：

```bash
# dry-run 预览（不改文件，不调 Whisper）
python "<scripts-dir>/fix/oped_fill.py" "<SUBTITLE_DIR>" \
  --video-dir "<VIDEO_DIR>" --lang zh --dry-run

# 跳过 API 边界检测（用默认值 180s）
python "<scripts-dir>/fix/oped_fill.py" "<SUBTITLE_DIR>" \
  --video-dir "<VIDEO_DIR>" --lang zh --skip-step1
```

**执行顺序**：先 `oped_fill` 填空白 → 再 `oped_fixer` 统一文本变体。
**边界检测**：两个工具都支持 `--detect-boundaries`（API 语义分析 cue 列表）和手动 `--op-boundary`/`--ed-boundary`。

### AI 润色（--lang zh）

**触发**: Pipeline 末尾交互提问 `是否对最终字幕进行 AI 润色？(y/n)`

→ 详细说明见 [references/translation.md](references/translation.md)

## 错误恢复

| 输出 | 操作 |
|------|------|
| `SyntaxError` / `UnicodeEncodeError` | emoji→ASCII、括号补全，修完重跑 |
| `Done: 0 fixed` + 无 `[whisper]` 输出 | `--video-dir` 缺失或路径错 — 验证 CLAUDE.md 路径 |
| 某步骤失败但已写中间文件 | 清空 `temp/` + `reports/`，加 `--force-rescan` 重跑 |
| 参考字幕乱码（西里尔 / 中文变 `?`）或**解析出 0 条 cue 却不报错** | 编码探测失准。`_detect_encoding()` 先嗅 BOM，无 BOM 再按 NUL 字节奇偶分布测 UTF-16；**单字节编码（cp1251/koi8-r/latin-1）必须留在试探链末尾** —— 它们对任意字节串都能解码成功，排在 CJK 多字节编码前会让后者成死代码。ASS 惯例编码是 UTF-16LE+BOM |
| `[translate] Baidu credentials not found` | 正常降级。配置 `BAIDU_APPID` + `BAIDU_SECRET` 或接受 AI 自行翻译 |
| `[polish] LLM_API_KEY not set` | 正常降级。设置环境变量或选 `n` 跳过润色。不要复用 Claude Code 内部 key |
| `[translate_srt] LLM_API_KEY not set` | **不要降级为 AI 自行翻译。**告知用户 key 为空，请用户设置后重跑。≤5 集且用户明确同意时才可手工翻译 |
| `HTTP Error 400: Bad Request` + `invalid_request_error` | 模型名不兼容。检查 API 返回的 supported model names，更新 `lib/config.py` 中 `LLM_MODEL_DEFAULT`（当前 `deepseek-flash`）。也可通过 `LLM_MODEL` env 或 `--model` CLI 参数覆盖 |
| 翻译 exit 0 但成片 cue 仍是日文原文 | 推理模型思考 token 吃光 `max_tokens`（HTTP 200 + `content` 空 + `finish_reason='length'`，无报错）。确认 `LLM_REASONING_EFFORT='none'`；若为空串则思考是开着的，需同时把 `LLM_MAX_TOKENS` 提到 32768+。实测思考开/关译文质量相当（仅风格差异），耗时 1.7s vs 33.3s —— **没有理由开启**，别靠加大预算解决 |
| 译文里成片出现 `1. 原文` `2. 原文`（带编号） | prompt 被 glossary 污染 → 模型整批回显。`load_mappings` 会把**所有值**拼进「固定译名参考」，`_comment` 之类的元数据键会混入。已自动跳过 `_` 前缀键；**说明文字写进 CLAUDE.md，不要留在 JSON 里**（`apply_map.py --merge-existing` 等消费方未必过滤） |
| 接受循环把回显记成成功 | 判据写成了 `out != src`。回显返回的是**带编号的原文**，文本确实"不同"但没翻译。用 `is_untranslated()` 剥掉 `^\s*\d+\s*[.、)）]\s*` 前缀再比 |
| 片头/片尾一大段对白变成同一句歌词中文 | OP/ED 预替换按固定 180s 窗口无脑覆写，窗口宽于实际 OP。`_is_lyric()` 已改为只覆写真歌词（等于窗口 canonical / 同集窗口内重复 ≥2 次 / 跨集出现 ≥2 次）。统计窗口内单句重复率即可确认 |
| OP/ED 预替换后对白凭空少了一截 | 同上。被覆写的 cue 文本非空、非日文，**Step 4 各验证项都不会报警** —— 必须与日文源逐条对数量 |
| 生成的 ASS 内容全是模板那一集的台词 | 新译文误走了就地编辑分支（`_start_line` 对不上、又不报错）。输出文件须**事先不存在**才会走新文件模式（模板只提供 header/styles） |
| ASS 写出来变成 UTF-8 | `write_ass_file(..., template_path=)` 没传，编码没沿用。ASS 交付要 UTF-16LE + BOM |
| ASS 行的字段整体错位一格 | `build_dialogue_line` 兜底分支把 `format`(`Dialogue: {layer}`) 和 `layer` 并列输出。Events 是 10 字段，Layer 内嵌在首字段里 |

## AI 介入点

→ [references/interventions.md](references/interventions.md) — 每个 🤖 点：触发条件、操作流程、判断规则。

## 参考

→ [references/workflows.md](references/workflows.md) — 典型工作流场景（从视频生成、翻译、修复、完整流程）。**设计工作流前先看这个，避免重复造轮子。**

→ [references/phase1-scan.md](references/phase1-scan.md) — Phase 1 扫描命令参考。
→ [references/phase2-triage.md](references/phase2-triage.md) — Phase 2 Whisper 修复命令参考。
→ [references/phase3-unify.md](references/phase3-unify.md) — Phase 3 专名统一 + 交付命令参考。
→ [references/full-mode.md](references/full-mode.md) — 有参考字幕时的完整工作流。
→ [references/reuse-reference-subs.md](references/reuse-reference-subs.md) — 参考字幕复用（总集篇/同一作品）的判定与判据。
→ [references/architecture.md](references/architecture.md) — 脚本架构与数据流（调试时查阅）。

## Flags

| Flag | When |
|------|------|
| `--dry-run` | Preview, no file changes |
| `--input-dir <DIR>` | Subtitle subdirectory (default: `AI审查后`). Use `.` for direct path |
| `--target-dir <DIR>` | Project root (default: CWD) |
| `--video-dir <DIR>` | Video directory — enables VAD speech detection + Whisper unified fix |
| `-e EP005-EP010` | Specific episode range |
| `--limit 5` | First N episodes only |
| `--skip-whisper` | Skip audio processing (残血模式) |
| `--vad-clean-apply` | **真正**按 VAD 删「无人声」条。默认只列清单（分级 L1，理由见上） |
| `--resume` | Resume after AI noun review (Phase 3 only) |
| `--force-rescan` | Re-scan even if cache fresh |
| `LLM_API_KEY` (env) | LLM API key for polish (optional) + translate_srt.py (**required**). Separate from Claude Code's. |
| `LLM_MODEL` (env) | Override default model. Current default: `deepseek-flash`. Use `--model` for per-run override. |
| `LLM_MAX_TOKENS` (env) | 单次响应 token 上限，默认 `8192`。推理模型需连同 `LLM_REASONING_EFFORT` 一起调。 |
| `LLM_REASONING_EFFORT` (env) | 默认 `none`（关闭思考）。设空串 = 省略参数 = API 默认（思考开）。 |
| `--source-lang <LANG>` | translate_srt.py: force source language (ja/ru/zh). Default: auto-detect. |
| `--mappings <JSON>` | translate_srt.py: path to noun_mappings.json (preferred over --glossary) |
| `--skip-oped` | translate_srt.py: skip OP/ED detection and pre-translation (use for shows without OP/ED) |
| `--batch N` | translate_srt.py: cues per batch (default: 10). Larger = less API calls but slower parallelism |
| `--detect-boundaries` | oped_fixer/oped_fill: use API (LLM) to detect OP/ED boundaries from cue patterns |
| `--skip-step1` | oped_fill: skip API boundary detection, use --op-boundary/--ed-boundary defaults |
| `--extract-nouns` | translate_srt.py: 翻译后提取专名实体 → temp/nouns/extracted_EP###.json |
| `--extract-dir <DIR>` | translate_srt.py / extractor.py: sidecar 输出目录（默认 temp/nouns） |
| `--resume` | extractor.py: 跳过健康 sidecar，只跑缺失/失败集（中断后续跑） |
| `--status` | extractor.py: 只打印健康度报告（✅/⚠/⬜），不调 LLM |
| `--min-entities N` | extractor.py: 旧 sidecar（无 chunks 字段）实体数≤N 视为失败（默认 2） |
| `--emit-mappings` | apply_map.py: 导出 ja→zh 幻觉控制表（`--merge-existing` 并入旧表） |
| `--emit-checklist <PATH>` | apply_map.py: 生成逐集 AI 审查清单（`- [x] NN集：规范名（变体）`；与 `--apply` 同用=记录实际统一项） |

> `--apply-ai-review` 是后处理快速路径，不能和 full run 一起用。
> 翻译工具完整参数见 [references/translation.md](references/translation.md)。

