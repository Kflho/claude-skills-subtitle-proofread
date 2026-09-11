---
name: subtitle-proofread
description: >
  Subtitle proofreading — 3-phase pipeline (scan → triage → deliver). Use when the
  user wants to proofread, scan, or fix subtitles (SRT/ASS), run Whisper ASR
  correction, unify proper nouns, or apply batch fixes. Unfixable items get [???]
  markers for Aegisub review. Covers: 字幕, subtitle, SRT, ASS, proofread, 校对, Whisper, 专有名词, captions.
---

# Subtitle Proofread

3-phase pipeline：扫描（乱码 + VAD语音时间线）→ Whisper 统一修复 → 专名统一 + 交付。无法自动修复的条目写入 `[???]` 标记，在 Aegisub 中审查。

**资源驱动**：有什么用什么。有视频+Whisper→修复乱码+补全缺字幕；有参考字幕→注入 AI 校对上下文。缺资源也能残血运行——跳过缺失步骤，剩余步骤照常。

## 🔥 快速上手：专名校对

> 翻译完成后对中文 SRT 做专有名词一致性审查。**AI 驱动三步**，脚本只做 AI 决策的便利执行工具（不做决策本身）：

```bash
cd "<project-root>"
# 1. 提取：翻译时自动（translate_srt.py --extract-nouns），或对已翻译文件独立跑
python "<scripts-dir>/nouns/extractor.py" --ja-dir "<日文源>" --zh-dir "<中文翻译>" -o temp/nouns
# 2. 聚合：跨集增量合并 → running-map temp/noun_map.json
python "<scripts-dir>/nouns/aggregate.py" --map temp/noun_map.json --extracted-dir temp/nouns -o temp/noun_map.json
# 3. 应用：先 dry-run 预览 → 🤖 AI 审查 map → 再写入
python "<scripts-dir>/nouns/apply_map.py" temp/noun_map.json --target-dir "<中文翻译>" --dry-run
python "<scripts-dir>/nouns/apply_map.py" temp/noun_map.json --target-dir "<中文翻译>" --apply
```

**scope 语义**：`global`（主要角色 / episodes≥2）跨全集统一；`per_episode` 仅该实体出现的集；`auto` 按 episodes 数自动判定。

**AI 审查点（不可跳过）**：先审查 `temp/noun_map.json` 与 dry-run 输出（定标准名、scope，删误判变体），再 `--apply`。

→ 数据流与设计动机见下方「专名审查（AI 驱动）」一节。
→ 旧版 `auto_translate.py` 词典审查法已弃用，见「专名审查（旧版 auto_translate）」一节。

## 专名审查（AI 驱动 · 推荐）

> **为什么重设计**：旧词典扫描法（unified_scanner → build_glossary → noun_mappings.json）
> 从 8009 个词频里挑 367 个映射，噪声大（多为 Whisper 幻觉），且词典 seed 会产生幻觉标准名
> （如「天間→天间」，正确应为「天马博士」）。新流程让 AI 在翻译过程中直接从真实文本挑专名
> 实体，脚本只做 AI 决策的**便利执行工具**，不做决策本身。

**三步闭环**（共享核心 `extract_names(ja_cues, zh_cues)`，翻译流程内 + 独立 CLI 同一实现）：

```bash
cd "<project-root>"

# 1. 提取（翻译时自动，或独立跑已翻译文件）
#    翻译时：translate_srt.py --extract-nouns → temp/nouns/extracted_EP###.json
python "<scripts-dir>/nouns/extractor.py" \
  --ja-dir "<日文源>" --zh-dir "<中文翻译>" -e EP001-EP005 -o temp/nouns

# 2. 聚合 → running-map（增量合并，AI 判定 merge/new/ignore）
python "<scripts-dir>/nouns/aggregate.py" \
  --map temp/noun_map.json --extracted-dir temp/nouns -o temp/noun_map.json

# 3. 应用（先 dry-run 预览 → 🤖 AI 审查 map → --apply）
python "<scripts-dir>/nouns/apply_map.py" temp/noun_map.json \
  --target-dir "<中文翻译>" --dry-run
python "<scripts-dir>/nouns/apply_map.py" temp/noun_map.json \
  --target-dir "<中文翻译>" --apply
# 导出 ja→zh 幻觉控制表（供 translate_srt --mappings）
python "<scripts-dir>/nouns/apply_map.py" temp/noun_map.json \
  --target-dir "<中文翻译>" \
  --emit-mappings temp/noun_map_ja_to_zh.json --merge-existing temp/noun_mappings.json
```

**提取续跑/恢复**（并发分片提取被 API 限速时，~17% 集的 chunk LLM 调用会静默失败导致漏实体）：
sidecar 记录 `chunks: {chunks_total, chunks_failed}`，失败与空结果分开记账。

```bash
# 健康度检查（不调 LLM）：✅ 健康 / ⚠ 失败/过少 / ⬜ 未提取
python "<scripts-dir>/nouns/extractor.py" \
  --ja-dir "<日文源>" --zh-dir "<中文翻译>" -o temp/nouns --status

# 中断后续跑：跳过健康 sidecar，只跑缺失/失败集（自动重试 chunk 失败）
python "<scripts-dir>/nouns/extractor.py" \
  --ja-dir "<日文源>" --zh-dir "<中文翻译>" -o temp/nouns --resume
```

> 旧 sidecar（无 `chunks` 字段）以实体数作失败代理（`--min-entities`，默认 2）；
> 全量提取后**先 `--status` 确认无 ⚠ 再进聚合**，避免漏实体污染 map。

**chunk 失败降级（DeepSeek 超长请求）**：大 chunk（默认 120 对 cue）下，LLM 对超长请求
可能返回 **HTTP 200 + 空 `message.content`** → `call_chat` 静默返回 `''` → chunk 判失败但
**无错误日志**（症状：集 chunk 大量失败、`--status` 成片 ⚠，且日志无报错）。
用 `--chunk-size 60` 再 `--chunk-size 40` 逐级收敛（本作实测 74→22→5→0 失败）。

**第 4 步：清单记录**（apply 后生成逐集 AI 审查记录，格式与人工清单一致）：

```bash
# --apply 时同跑：记录实际统一项 → 逐集清单
python "<scripts-dir>/nouns/apply_map.py" temp/noun_map.json \
  --target-dir "<中文翻译>" --apply --emit-checklist temp/ai_review_checklist.txt
```

清单行格式（人工清单同款）：
`- [x] NN集：规范名（变体、变体）、规范名（变体）` —— 只记实际发生替换的集，
`[x]` 表示 AI 已审查。生成后把行追加到项目清单（如 `字幕翻译.md`）的 `## ai 审查`
版块；`## 人工审查` 版块不动。

**数据流**：

| 产物 | 内容 | 用途 |
|------|------|------|
| `temp/nouns/extracted_EP###.json` | 每集专名实体 `{ja_forms(含幻觉), zh_forms(含变体), count, samples}` | 聚合输入 |
| `temp/noun_map.json` | running-map `{标准名: {ja_forms, zh_variants, episodes, scope}}` | AI 审查后应用 |
| `temp/noun_map_ja_to_zh.json` | ja→zh 幻觉控制表 | translate_srt `--mappings` |

**scope 语义**（apply_map 使用）：
- `global`（主要角色 / episodes≥2）→ 跨全集统一
- `per_episode` → 仅该标准名出现的集
- `auto` → episodes≥2 → global；=1 → per_episode

**`_build_repl` 双写防护（apply_map）**：替换正则按变体-标准名关系生成——
- 变体是标准名精确后缀（茶水博士→御茶水博士）：`(?<!前缀)变体`，防"御茶水博士博士"；
- 变体是标准名精确前缀（乌拉尔→乌拉尔号）：`变体(?!后缀)`，防"乌拉尔号号"；
- **变体是标准名近似**（多里安→多利安博士，同音异字，非精确前后缀）：走裸匹配分支，
  已补 `变体(?!标准名全部≥2字真后缀)`，防"多里安博士"→"多利安博士博士"。
> ⚠️ **审查时注意近似音译变体**：只防精确前后缀时，裸匹配会吞"变体+标准名尾部"产生双写。
> apply 后建议跑双写检测（匹配 `标准名+标准名尾2~3字`），确认 0 处 apply 引入。

**AI 审查点（不可跳过）**：dry-run 输出的待改项、map 中的标准名与 scope。
先审查 `temp/noun_map.json`（删误判变体、修正标准名、定 scope），再 `--apply`。
> 例：提取可能把标题"铁腕阿童木"并入"阿童木"变体——审查时删掉即可。

## 专名审查（旧版 auto_translate · legacy）

> ⚠️ **已弃用**。旧法用 `auto_translate.py` 扫描候选 + 逐条审查，噪声大、把 AI 当搬运工。
> 功能保留供存量项目参考；**新项目一律走上方「专名审查（AI 驱动）」流程**。

**入口命令**：

```bash
python "<scripts-dir>/auto_translate.py" \
  --source-dir "<日文源>" --target-dir "<中文翻译>" \
  --mappings temp/noun_mappings.json
```

**检查点驱动，反复运行同一命令自动推进：**

| 阶段 | 输出 | 做什么 |
|------|------|--------|
| `review` | `temp/scans/candidates.json` (N条) | 开始AI审查 |
| `review_pending` | candidates 未归零 | 继续修复 + 重跑 |
| `done` ✅ | candidates 归零 | 完成 |

**AI 审查循环**（每条 candidate 有 `type` 字段）：

| type | 含义 | 操作 |
|------|------|------|
| `inconsistency` | 已知专名译法不一致（如「阿托姆」→应为「阿童木」） | 按 `zh_canonical_in_mappings` 修复 SRT |
| `unknown_suspect` | 未识别疑似专名 | 判断是否专名 → 是：补 `noun_mappings.json` + 统一 SRT；否：补 `temp/zh_common_blacklist.json` |

**大规模审查（100+ unknown_suspect）**：不要逐条硬查。
→ 用 `batch_classify.py` 批量分类（30条/批，API 成本 ~$0.30-0.50）
→ 普通词自动补黑名单，专名自动补映射表，重扫 → 迭代至归零

```bash
# 一键批量分类
python "<scripts-dir>/batch_classify.py"

# 先测试几批
python "<scripts-dir>/batch_classify.py" --limit 3
```

**完整迭代循环**：

```
scan → candidates.json (N条)
  │
  ├─ N > 50 unknown_suspect → API 批量分类
  │   ├─ common_word → temp/zh_common_blacklist.json
  │   └─ proper_noun → temp/noun_mappings.json (self-mapping)
  │
  ├─ N ≤ 50 → AI 手动审查每条 candidate
  │   ├─ inconsistency → 编辑 SRT 修复译法不一致
  │   ├─ unknown_suspect(专名) → 补 mappings + 统一 SRT
  │   └─ unknown_suspect(普通词) → 补黑名单
  │
  └─ 重跑 auto_translate.py → 自动检测 SRT 变更 → 重新扫描
       ↓
     candidates 归零 → done ✅
```

**旧法名词库生成**（unified_scanner → build_glossary，已被新流程提取替代）：

```bash
python "<scripts-dir>/scan/unified_scanner.py" --target-dir "<日文源>" \
  --build-glossary --glossary-output reports/proper-nouns.md --project-lang ja
python "<scripts-dir>/nouns/build_glossary.py" --findings temp/scans/findings.json \
  -o reports/proper-nouns.md --mappings-output temp/noun_mappings.json
```

**关键文件**：

| 文件 | 作用 |
|------|------|
| `temp/scans/candidates.json` | AI审查输入（扫描器输出） |
| `temp/noun_mappings.json` | ja→zh 专名映射（补专名用） |
| `temp/zh_common_blacklist.json` | 中文普通词黑名单（补普通词用） |
| `temp/scans/classified_terms.json` | API 批量分类结果 |

**黑名单机制**（v5.1）：`find_suspect_nouns.py` 支持 `--zh-blacklist <JSON>` 加载外部普通词列表。`auto_translate.py` 自动检测 `temp/zh_common_blacklist.json` 并传递。扫描器跳过黑名单中的词，从源头减少误报。

→ 完整流程见 [references/batch-review.md](references/batch-review.md) 和 [references/translation.md](references/translation.md)。

## ASS 格式项目

本 skill 同时支持 **SRT** 和 **ASS** 两种格式。所有工具通过 `read_subtitles()`/`write_subtitles()`（`lib.subtitle_io`）或 `parse_subtitles()`/`write_srt()`（`lib.whisper_utils`）自动检测格式，无需手动转换。

> **注意**：如果项目是 ASS 格式，`--input-dir` 指向包含 `.ass` 文件的目录即可。Pipeline 会像处理 SRT 一样处理 ASS，输出保持 ASS 格式。

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
# 旧词典扫描审查（legacy，仅存量项目）
python "<scripts-dir>/auto_translate.py" \
  --source-dir "<日文源>" --target-dir "<中文翻译>" \
  --mappings temp/noun_mappings.json

# 无日文源 → 中文侧扫描
python "<scripts-dir>/auto_translate.py" \
  --target-dir "<中文翻译>" \
  --mappings temp/noun_mappings.json

# 仅扫描专名（不比对 SRT）
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
- **时间轴用用户的**：Whisper 自身时间戳只作内部参考，不输出。用户已经对好轴。
- **前后各 2 句**：给用户看到修复后的句子如何嵌入上下文。
- **发现幻觉 cue**（切片 Whisper 在同一时间段输出完全不同的文本）：在 JA 行标注 `⚠️ 原为幻觉，日文无对应`，ZH 给出切片参考翻译。
- **不要多余的话**：不写"建议修复为"、"核心问题"、"分析"等。用户只需要轴 + 参考文本。
- **不解释、不总结、不推荐方案**。用户拿到参考自己判断。

**Whisper 幻觉重复**（已知问题，翻译阶段处理）

Whisper 在音乐/噪声段会产生幻觉重复（连续多条 cue 文本高度相似甚至完全相同）。
> ⚠️ **切片重跑 Whisper 方案已验证无效**（`fix_repeated_cues.py` 已弃用）。
> 根因是音频本身触发幻觉，重跑只换一组幻觉，不会修复。

**正确做法**：在 AI 翻译阶段，system prompt 中告知 LLM：
> "连续多条字幕文本相同或高度相似是 Whisper 的 bug（幻觉重复），
> 照常翻译每条即可，不要尝试区分或合并它们。最终由人工审查决定保留哪条。"

> 此问题目前无法自动审查，只能等人工校对阶段处理。

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

> 🚨 **推理模型的思考 token 会静默毁掉翻译**（本 skill 已默认规避）
>
> `deepseek-flash` 等推理模型会返回 `reasoning_content`，且思考 token 计入
> `max_tokens` 预算。预算被思考吃光时，响应是 **HTTP 200 + `content` 空串 +
> `finish_reason='length'`** —— 调用方只看到空串，判整批失败，**不打印任何错误**。
> 症状：翻译"跑完了、exit 0"，但输出里成片 cue 仍是日文原文（实测某集 61% 未翻译）。
>
> 提高 `max_tokens` **治标不治本**：思考长度会跟着涨（`max_tokens=32768` 时
> `reasoning_tokens=13832`，62s 才收敛，比关思考贵 57 倍）。
>
> 正确做法是关掉思考，`lib/config.py` 已默认：
>
> | env | 默认 | 说明 |
> |---|---|---|
> | `LLM_REASONING_EFFORT` | `none` | 关闭思考。设**空串**则省略该参数（= API 默认，思考开启） |
> | `LLM_MAX_TOKENS` | `8192` | 单次响应 token 上限 |
>
> ```bash
> # 默认即可，无需显式设置
> export LLM_REASONING_EFFORT='none'
> # 若要重新开启思考，必须同时把预算提到 32768 以上，否则输出为空
> export LLM_REASONING_EFFORT='low' LLM_MAX_TOKENS=32768
> ```
>
> **质量影响**：同批 12 条对白实测，思考开/关译文质量相当（差异仅风格），
> 但耗时 1.7s vs 33.3s、completion token 106 vs 6061。思考并未减少译文里的
> 日文残留（未收录专名仍原样输出），故**没有理由开启**。
>
> 覆盖范围：`lib/llm.py:call_chat` 与 `translate_srt.py:_call_llm`。
> 两者在 `content` 为空且 `reasoning_content` 非空时会打印诊断，不再静默。

> 🚨 **`noun_mappings.json` 里不能放 `_` 开头的元数据键**
>
> `load_mappings` 会把**所有值**拼成 `glossary_str` 塞进 prompt 的「固定译名参考
> （必须使用）」。若文件里有 `"_comment": "…整段说明文字…"`，那段中文说明会当成
> 译名混进去，把 prompt 撑长且语义错乱 —— 实测可让模型**放弃翻译、整批原样回显
> 日文**（某集 #160-169 十连回显，prompt 里是 `1. どうか` … `10. さあ早くいらっしゃい`）。
>
> 已修：`load_mappings` 跳过 `_` 前缀键。但**说明文字请写进 CLAUDE.md**，
> 不要指望它待在 JSON 里 —— 其他消费方（`apply_map.py --merge-existing`）未必过滤。

> 🚨 **回显 ≠ 翻译成功：接受判据必须是「剥离编号后是否仍等于原文」**
>
> `targets` 是 `"1. …\n2. …"` 格式。模型整批回显时返回的是**带编号的原文**，
> 于是 `translated_text != cue['text']` 判为「翻译成功」（文本确实不同 ——
> 多了编号），回显被当成功写进输出，译文里留下一整段日文。
>
> `translate_srt.py` 现在用 `is_untranslated()`：剥掉 `^\s*\d+\s*[.、)）]\s*`
> 前缀再比。`_translate_one` 对回显率 > 50% 的批次重试一次；
> 接受循环把回显条目计入 `failed`，不再写进输出。

> 🚨 **OP/ED 预替换不能按时间窗无脑覆写 —— 窗口比 OP 长时吞掉正片对白**
>
> `apply_oped_pre_replace` 早先按固定窗口（`OP_BOUNDARY_SEC` = `ED_BOUNDARY_SEC`
> = 180s）取「窗口内所有 cue」，**一律**改写成预译的那一句歌词中文。窗口是照
> 「OP 最长 180s」定的，OP 只有 82s 的作品里，窗口一路吃进正片：
> 某 SP 实测 45 条对白被覆写 —— #1–#23 全成「和Freeze王子接吻」、
> #442–#463 全成「买买买买买」，**真正的对白无声消失**，且因为文本非空、
> 非日文残留，Step 4 的验证项**一个都不报警**。
>
> 已修：`_is_lyric()` 三信号判定，只覆写真歌词 —— 等于该窗口 canonical /
> 同集窗口内重复 ≥2 次（音乐段幻觉重复）/ 跨集出现 ≥2 次（主题歌）。
> 只出现一次、别集也没有的文本不再覆写，留给正常翻译。
>
> **排查手法**：预替换后立刻统计「同一句中文在窗口内重复次数」，
> 单句占比过高即是窗口过宽的信号 —— 歌词本就该重复，但正片对白不该。

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
4. 0 条**真正待处理**的 ⬜ → 完成

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
  → VAD clean: 删除非人声 cue（[音楽][拍手] 等）
  → build_fix_regions(): VAD 人声段落 → 统一检测三种 fix region
     ├─ garbled:      人声覆盖乱码 cue → 清空重录
     ├─ partial_overlap: 人声部分覆盖 cue + 延伸到 uncovered → 清空重录
     └─ missing:      人声完全无 cue → 插入新 cue
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

> ⚠️ 旧词典法暂停点（legacy）。新流程无此暂停点——提取/聚合自动完成，AI 只在「专名审查（AI 驱动）」的 dry-run 后审查 `temp/noun_map.json`。

**触发**: `[review] N candidate(s)` 或 `[suspect-nouns] N entries → report layer 3`

**审查流程**（按候选数量选择策略）：

**≤ 50 条** → 手动审查
1. 读 `temp/scans/candidates.json`（统一格式）
2. 每条 candidate 有 `type` 字段：
   - `inconsistency` → 已知专名译法不一致（如「阿托姆」→ 应为「阿童木」），按 `zh_canonical_in_mappings` 编辑 SRT
   - `unknown_suspect` → 未识别专名，判断是否专名 → 是：补 `noun_mappings.json` + 统一 SRT → 否：跳过
3. 修完重新运行 → candidates 归零 → 完成

**> 50 条** → API 批量分类（见 [references/batch-review.md](references/batch-review.md)）
1. 读 `candidates.json` → 提取所有 `unknown_suspect`
2. 写脚本用 `LLM_API_KEY` 批量分类（30条/批），判断每个词是 proper_noun 还是 common_word
3. 普通词 → 补 `temp/zh_common_blacklist.json`（JSON 数组）
4. 专名 → 补 `noun_mappings.json`（self-mapping 即可：`"专名": "专名"`）
5. 重跑 auto_translate.py → 自动使用 `--zh-blacklist` 加载黑名单 → 候选数大幅下降
6. 迭代至归零

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

> ⚠️ 旧词典法暂停点（legacy）。新流程的审查点在「专名审查（AI 驱动）」：dry-run 后审查 `temp/noun_map.json`（标准名、scope、误判变体）再 `--apply`。

**Step 1** — `[scan] 🤖 AI Glossary Review — N entries`：读 `reports/proper-nouns.md` → 逐条判专名/普通词 → 编辑 utils 白名单/黑名单 → 重跑 build_glossary

**Step 2** — `AI REVIEW NEEDED: N`：读 `ai_review_candidates.json` → 判专名/普通词 → 写 `ai_review_fixes.json` → `--resume`

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
| 参考字幕乱码（西里尔/中文变 `?`） | v2 已自动检测编码（UTF-8/CP1251/KOI8-R/Shift-JIS/GBK） |
| `[translate] Baidu credentials not found` | 正常降级。配置 `BAIDU_APPID` + `BAIDU_SECRET` 或接受 AI 自行翻译 |
| `[polish] LLM_API_KEY not set` | 正常降级。设置环境变量或选 `n` 跳过润色。不要复用 Claude Code 内部 key |
| `[translate_srt] LLM_API_KEY not set` | **不要降级为 AI 自行翻译。**告知用户 key 为空，请用户设置后重跑。≤5 集且用户明确同意时才可手工翻译 |
| `HTTP Error 400: Bad Request` + `invalid_request_error` | 模型名不兼容。检查 API 返回的 supported model names，更新 `lib/config.py` 中 `LLM_MODEL_DEFAULT`（当前 `deepseek-flash`）。也可通过 `LLM_MODEL` env 或 `--model` CLI 参数覆盖 |
| 翻译 exit 0 但成片 cue 仍是日文原文 | 推理模型思考 token 吃光 `max_tokens`（HTTP 200 但 `content` 空）。确认 `LLM_REASONING_EFFORT='none'`；若为空串则思考是开着的，需同时把 `LLM_MAX_TOKENS` 提到 32768+ |
| 译文里成片出现 `1. 原文` `2. 原文`（带编号） | prompt 被 glossary 污染 → 模型整批回显。检查 `noun_mappings.json` 有无 `_comment` 之类的元数据键；已自动过滤，若仍复现则缩短 glossary |
| 接受循环把回显记成成功 | 判据写成了 `out != src`。回显文本带编号，"不同"但没翻译。用 `is_untranslated()` 判 |
| 片头/片尾一大段对白变成同一句歌词中文 | OP/ED 预替换按固定 180s 窗口无脑覆写，窗口宽于实际 OP。`_is_lyric()` 已改为只覆写真歌词；统计窗口内单句重复率即可确认 |
| OP/ED 预替换后对白凭空少了一截 | 同上。被覆写的 cue 文本非空、非日文，**Step 4 各验证项都不会报警** —— 必须与日文源逐条对数量 |

## AI 介入点

→ [references/interventions.md](references/interventions.md) — 每个 🤖 点：触发条件、操作流程、判断规则。

## 参考

→ [references/workflows.md](references/workflows.md) — 典型工作流场景（从视频生成、翻译、修复、完整流程）。**设计工作流前先看这个，避免重复造轮子。**

→ [references/phase1-scan.md](references/phase1-scan.md) — Phase 1 扫描命令参考。
→ [references/phase2-triage.md](references/phase2-triage.md) — Phase 2 Whisper 修复命令参考。
→ [references/phase3-unify.md](references/phase3-unify.md) — Phase 3 专名统一 + 交付命令参考。
→ [references/full-mode.md](references/full-mode.md) — 有参考字幕时的完整工作流。
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

