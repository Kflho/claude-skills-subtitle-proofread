---
name: subtitle-proofread
description: >
  Use when the user wants 字幕 handled end to end: Qwen3-ASR/ForcedAligner or
  legacy Whisper for timing, LLM translation to Chinese, proper-noun unification
  across episodes, or proofreading existing SRT/ASS. FireRedVAD/AED is currently
  diagnostic-only (no OP/ED decision consumer yet). Unfixable items get [???]
  markers for Aegisub review.
---

# Subtitle Proofread

3-phase pipeline：扫描（乱码 + VAD 语音时间线）→ 时间轴修复（默认 Whisper legacy；
可选 Qwen3-ASR + ForcedAligner）→ 专名统一 + 交付；可选 LLM 翻译。
无法自动修复的条目写入 `[???]` 标记，在 Aegisub 中审查。

**资源驱动**：有什么用什么。有视频+ASR→修时间轴/补缺字幕；有参考字幕→注入 AI 校对上下文。
缺资源也能残血运行——跳过缺失步骤，剩余步骤照常。

## 专名审查（AI 驱动）

> 旧词典扫描法噪声大，且会产生“幻觉标准名”（把「天間」定成「天间」，
> 正确应为「天马博士」）。新流程让 AI 从真实文本里挑专名实体，
> **脚本只做 AI 决策的执行工具，不做决策本身**。

共享核心 `extract_names(ja_cues, zh_cues)`；翻译流程内和独立 CLI 同一实现。

三步闭环：

```bash
cd "<project-root>"

# 1. 提取（翻译时自动：translate_srt.py --extract-nouns；或独立跑）
python "<scripts-dir>/nouns/extractor.py" \
  --ja-dir "<日文源>" --zh-dir "<中文翻译>" -e EP001-EP005 -o temp/nouns

# 2. 聚合 → running-map
python "<scripts-dir>/nouns/aggregate.py" \
  --map temp/noun_map.json --extracted-dir temp/nouns -o temp/noun_map.json

# 3. dry-run → 🤖 审查 map → --apply
python "<scripts-dir>/nouns/apply_map.py" temp/noun_map.json \
  --target-dir "<中文翻译>" --dry-run
python "<scripts-dir>/nouns/apply_map.py" temp/noun_map.json \
  --target-dir "<中文翻译>" --apply

# 导出 ja→zh 映射（供 translate_srt --mappings）
python "<scripts-dir>/nouns/apply_map.py" temp/noun_map.json \
  --target-dir "<中文翻译>" \
  --emit-mappings temp/noun_map_ja_to_zh.json --merge-existing temp/noun_mappings.json
```

**🤖 审查点（不可跳过）**：dry-run 之后、`--apply` 之前，审查 `temp/noun_map.json`：
定标准名、定 scope、删误判变体（如把标题「铁腕阿童木」并进「阿童木」变体）。

**scope**：`global` = 跨全集；`per_episode` = 仅该集；`auto` = episodes ≥ 2 取 global。

**续跑/失败**：大项目先 `--status` 确认无 ⚠ 再聚合；中断用 `--resume`。
超长请求导致空 content 时逐级降 `--chunk-size 60/40`。

## 专名审查（旧版 auto_translate · legacy）

> ⚠️ **已弃用**，仅存量项目参考。新项目一律走上方「专名审查（AI 驱动）」。
> 旧法的扫描 → API 批量分类 → 黑名单/映射表循环见
> [references/batch-review.md](references/batch-review.md)。

关键文件：`temp/scans/candidates.json`、`temp/noun_mappings.json`、
`temp/zh_common_blacklist.json`、`temp/scans/classified_terms.json`。

## ASS 格式项目

本 skill 同时支持 **SRT** 和 **ASS** 两种格式。所有工具通过 `read_subtitles()`/`write_subtitles()`（`lib.subtitle_io`）或 `parse_subtitles()`/`write_srt()`（`lib.whisper_utils`）自动检测格式，无需手动转换。

> **注意**：如果项目是 ASS 格式，`--input-dir` 指向包含 `.ass` 文件的目录即可。Pipeline 会像处理 SRT 一样处理 ASS，输出保持 ASS 格式。

## 行为分级：哪些默认做，哪些等指令

每个会**写盘**的行为都在 `lib/gates.py` 登记一个级别。判据只有一条：
**这一步会不会在用户没要求的情况下改变字幕内容？** 会 → 至少 L1。

| 级别 | 含义 | 包含 |
|---|---|---|
| **L0** | 默认必做，只读 | 扫描/编码探测、VAD 台词↔人声匹配（含「有人声无字幕」缺口清单）、交付校验 |
| **L1** | 默认只检测并标注，写盘要开关 | VAD 删「无人声」条（`--vad-clean-apply`）、missing 补条 |
| **L2** | 只在明确要求时执行 | OP/ED 预替换、专名预替换、TV 复用覆写、润色、繁→简、`fixes.json` 改写 |
| **L3** | 执行前必须确认 | 覆盖已交付字幕、覆盖既有 `.bak` |

默认取安全侧：VAD 删条只出清单，`missing` 只列不插。
新增会写盘的功能时，先在 `lib/gates.py` 加一条 `Gate(...)`，再写实现。

## 首次使用？

检查项目 `CLAUDE.md` 末尾是否有 `## SKILL INITIALIZED: true`。

**没有** → 首次使用。读取 `references/first-run.md`，跟随初始化向导完成配置后再继续。

**有** → 已初始化。从 CLAUDE.md 获取路径，直接进入 pipeline。

> 如需重新初始化（添加参考字幕、更换模型等），删除 CLAUDE.md 中的 `SKILL INITIALIZED: true` 行即可。

## 运行

**第一次用先看 [references/quickstart.md](references/quickstart.md)**：已有日文字幕 →
中文 / 整条 pipeline 的最短命令、输出、QA 字段和已知边界。

只有视频时，一键粗剪：

```bash
python scripts/video_to_zh.py \
  --video-dir "<视频目录>" --output-dir "<输出目录>" \
  --engine qwen3 --mappings glossaries/digimon.series.json
```

输出 `<输出目录>/ja/<EP>.srt`（Qwen3 转写）+ `<输出目录>/zh/<EP>.srt`（中文）+ QA。
注意：ASR 是句子级、raw text 有错；这是**A+C 快捷粗剪**，不是原有完整 A–D。
专名审查、`auto_translate`、`oped_fixer`、`polish_zh` 仍按
[references/workflows.md](references/workflows.md) 的完整流程走。

### 环境设置

首次使用 → [references/setup.md](references/setup.md)（环境变量、Python 依赖、API 密钥、git 备份铁律）

已验证过的项目跳过，直接从 CLAUDE.md export 环境变量即可。

### 可选模型资产（不随 skill 分发）

模型在 `models/`（gitignored）；本机绝对路径/版本由维护者本地保存，skill 不写死。速查：

| 模型 | 路径 | 调用 |
|---|---|---|
| FireRedVAD/AED | `models/FireRedVAD/{VAD,Stream-VAD,AED}` | `.venv-qwen` + `fireredvad`；`scripts/audio/detect_events.py` |
| Mel-Band RoFormer | `models/MelBandRoformer/*.safetensors` | 仅权重；运行时未接线 |
| Qwen3-ASR/FA | `models/Qwen3-ASR-*`, `models/Qwen3-ForcedAligner-0.6B` | `.venv-qwen` worker |

```powershell
# FireRedVAD/AED → AudioHints JSON（16k mono wav）
python scripts/audio/detect_events.py --wav temp/firered_ep001.wav `
  --out temp/scans/EP001.audio_hints.json --mode aed

# 主流程：ASR 路由 + hints（默认关；失败不影响修复）
python scripts/run_all.py --asr qwen3 --audio-hints
```

> **FireRedVAD 当前状态（2026-10）**：模型/CLI 可用，但 `--audio-hints` 是
> **write-only**：只写 `<EP>.audio_hints.json`，生产流程没有读取方；且只在
> `--asr qwen3` 下触发。token 过滤、VAD concat、AED 边界切块均未通过多集验证。
> 保留它只为后续 **完整集 OP/ED 翻译** 研究；不要当生产决策。
> 本机状态/缺口见本地开发仓库（不随 skill 分发）。

> Whisper 系按用户规则**不下载、不再测试**；本机只保留 whisper.cpp CUDA（路径由 `WHISPER_CLI` 指定）。

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

# 完整 pipeline（推荐）：扫描 + 时间轴修复（Whisper legacy / Qwen3 ASR）
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

# 2026 ASR 路线（P2）：Qwen3-ASR + ForcedAligner 对齐时间轴
python "<scripts-dir>/run_all.py" \
  --input-dir "<SUBTITLE_DIR>" \
  --video-dir "<VIDEO_DIR>" \
  --asr qwen3 \
  --asr-python "<venv>/Scripts/python.exe"
```

> `--input-dir` 指定字幕子目录（默认 `AI审查后`）。`--lang` 自动检测。
> `--video-dir` 启用旧 VAD + Whisper 修复（乱码、部分重叠、缺字幕一次处理）；`--asr qwen3` 会改走 Qwen3-ASR/FA 时间轴路线。无视频时加 `--skip-whisper` 残血运行。
> `--limit` 只限 Phase 2 修复集数，扫描覆盖全部文件。
> **VAD 删条默认不写盘**（分级 L1）：先看清单，确认无误再 `--vad-clean-apply`。
> `--asr qwen3` 用 2026 路线替代 Phase 2 的 Whisper：**默认只改时间码**，文本不动、不删条；
> 需要文本替换再加 `--asr-fix-text`。诊断明细写到 `temp/scans/<EP>.alignment.json`。
> 整集偏移**默认不应用**（按 CC 标准，0.37–0.40 s 是当前对齐的起点缺陷，不是参考偏移；
> 用参考自估偏移平移输出属于自证）；只有拿到独立证据（换剪辑/帧率）才加
> `--asr-global-offset`，估计值始终写进 alignment.json。
> ASS 的 `--asr` 写回暂不支持（只出报告，不静默改文件）。
> `--translate`：修复完成后把源字幕翻成中文，输出 `translated/zh/<EP>.srt` +
> `<EP>.qa.json`；QA 只修问题条（`--repair`），默认关，失败不阻断交付。
> 架构/评测说明在本地开发仓库；用户用法见 [references/translation.md](references/translation.md)。

### 模块化调用

`run_all.py` 适合一键跑完；单步调试/定制用下列脚本。完整命令见
`references/phase1-scan.md`、`phase2-triage.md`、`phase3-unify.md`。

| 目的 | 入口 |
|---|---|
| Phase 1 扫描 | `scan/unified_scanner.py`（+ `nouns/build_glossary.py`） |
| Phase 2 单集 audio/text | `fix/episode_workflow.py EP001 --mode audio/text` |
| Phase 3 专名/交付 | `run_all.py --resume` / `apply/apply_fixes.py` |
| OP/ED | `fix/oped_fixer.py`、`fix/oped_fill.py` |
| 批量转录 | `whisper_batch_transcribe.py` |
| 参考字幕复用 | `reuse/align.py` + `reuse/apply.py` |
| 切段修复 | `whisper_spot_fix.py` |

**常用单步**：

```bash
# 扫描（只读）
python "<scripts-dir>/scan/unified_scanner.py" --target-dir "<SUBTITLE_DIR>" \
  --output-findings temp/scans/findings.json --build-glossary --project-lang zh

# 单集修复
python "<scripts-dir>/fix/episode_workflow.py" EP001 --mode audio --project-dir "<PROJECT_DIR>"
python "<scripts-dir>/fix/episode_workflow.py" EP001 --mode text --project-dir "<PROJECT_DIR>"

# 从视频批量转录（无字幕时）
python "<scripts-dir>/whisper_batch_transcribe.py" \
  --video-dir "<VIDEO_DIR>" --output-dir "<OUTPUT_DIR>" --lang ja --limit 3

# 参考字幕复用：先配对看报告，再 dry-run/--apply
python "<scripts-dir>/reuse/align.py" -e SP01,SP02 \
  --source-dir "<源语言转录>" --target-dir "<我们的译文>" \
  --reference-dir "<参考字幕目录>" --report temp/reuse/report.txt
python "<scripts-dir>/reuse/apply.py" -e SP01 ...   # 先 dry-run；确认后 --apply

# 切段修复（多人争吵/长 cue 合并）
python "<scripts-dir>/whisper_spot_fix.py" EP001 --start 24:35 --end 24:44
```

> OP/ED、复用判据、逐 Phase 细节见对应 `references/*.md`。

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

### LLM API 配置

翻译（`translate_srt.py`）、润色（`polish_zh.py`）共用 `LLM_API_KEY`。
默认模型 `deepseek-flash`（`lib/config.py`）。

```bash
export LLM_API_KEY='sk-...'
export LLM_MODEL='deepseek-flash'
export LLM_BASE_URL='https://api.deepseek.com/v1'
```

> 旧名 `deepseek-v4-flash` / `deepseek-v4-pro` 已下线；`deepseek-chat` 失效（HTTP 400）。
> 无 key 时 `translate_srt.py` 不降级为 AI 自行翻译；告知用户设置后重跑。

### 验证

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
| 词典过滤 | ✅ (jamdict/JMdict) | ✅ (jieba/498K 词) | ❌ | ❌ |
| 专名分类 | ✅ (jamdict) | ✅ (jieba + 规则) | ❌ | ❌ |
| Glossary 清洗 | ✅ (JMdict + 规则) | ✅ (jieba 词典 + 规则) | ❌ | ❌ |
| AI 润色（去翻译腔） | ❌ (日语原文无需) | ✅ (OpenAI 兼容 API) | ✅ (同 zh) | ✅ (同 zh) |

> `translate_srt.py` **自动检测源语言**（ja/ru/zh），动态切换 system prompt，无需手动指定。
> 日语检测到假名时启用专用规则（敬语、主语省略等），非日语跳过。
> `--lang zh` 时使用 jieba 分词 + 词典查询对标 jamdict。jieba 不可用时退回 n-gram + 启发式规则。
> **翻译全部走 LLM**（ADR-010/011）：百度/传统 MT 路径已删除，不再维护或测试；
> 翻译是**必须调用 LLM 的阶段**，用用户已配置的国内 key（`~/.claude/settings.json`
> 的 `LLM_API_KEY`/`LLM_BASE_URL`/`LLM_MODEL`；DeepSeek，Claude Code 自动注入）。
> 成本策略：优先免费/本地；需要 token 时只接受国内服务，国外服务暂不配置。
> **字幕复核缺口在管线内自填**（ADR-012）：`fix_by_asr` 把自动修不掉的 cue 写成
> `temp/scans/<EP>.ai_pack.json`，review 阶段按 15 条一批问 LLM 并应用结果；
> **默认 `REVIEW_ACTIONS=keep` 只分类**（不改时间/文本、不删条——EP001 实测改写会稳定改坏），
> 需要写权时显式 `REVIEW_ACTIONS=keep,delete,rewrite`。详见
> [sk_01_dev/docs/review-ai-fill.md](sk_01_dev/docs/review-ai-fill.md)（开发文档）。
> Review 之外的专名/润色不新增 LLM 调用（走 agent 工作单或并入翻译 prompt）；
> 无 key 时明确失败/保留原文，不回退机翻。
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

## Pipeline 速览

Phase 1 扫描（只读）→ Phase 2 修复（VAD 统一路径；删条 L1、missing 只列）→
Phase 3 专名/OP-ED/交付 → Phase 4 润色（`--lang zh` 可选）。

逐 Phase 命令见 `references/phase1-scan.md` / `phase2-triage.md` / `phase3-unify.md`。
架构/数据流属于开发文档，不在 SKILL.md 展开。

**mj** = meaningful Japanese character count；mj < 2 = noise。
AI 审查只读小 JSON（`ai_fragments_*.json`、`ai_review_candidates.json`），不读词表全文。

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

# 跳过 API 边界检测，改用 --op-boundary/--ed-boundary
python "<scripts-dir>/fix/oped_fill.py" "<SUBTITLE_DIR>" \
  --video-dir "<VIDEO_DIR>" --lang zh --skip-step1
```

**执行顺序**：先 `oped_fill` 填空白 → 再 `oped_fixer` 统一文本变体。
**边界检测**：两个工具都支持 `--detect-boundaries`（API 语义分析 cue 列表）和手动 `--op-boundary`/`--ed-boundary`。

### AI 润色（--lang zh）

**触发**: Pipeline 末尾交互提问 `是否对最终字幕进行 AI 润色？(y/n)`

→ 详细说明见 [references/translation.md](references/translation.md)

## 错误恢复

| 输出/症状 | 操作 |
|---|---|
| `SyntaxError` / `UnicodeEncodeError` | emoji→ASCII、括号补全，修完重跑 |
| `Done: 0 fixed` + 无 `[whisper]` 输出 | `--video-dir` 缺失或路径错 |
| 某步骤失败但已写中间文件 | 清空 `temp/` + `reports/`，`--force-rescan` 重跑 |
| 参考字幕乱码 / 解析 0 cue | 编码探测失准；检查 BOM/转码；ASS 惯例 UTF-16LE+BOM |
| `LLM_API_KEY not set`（翻译/润色） | 明确失败或保留原文；不要降级到传统机翻（已移除） |
| `HTTP 400 invalid model` | 用 `deepseek-flash`，或更新 `lib/config.py:LLM_MODEL_DEFAULT` |
| 翻译 exit 0 但成片仍是日文 | 推理 token 吃光预算：`LLM_REASONING_EFFORT=none`，必要时提高 `LLM_MAX_TOKENS` |
| 译文带编号原文（`1. 原文`） | glossary 回显；说明文字写 CLAUDE.md，不要写 JSON |
| 接受循环把回显记成功 | 用 `is_untranslated()` 判断，不要只比 `out != src` |
| OP/ED 对白被歌词覆写/变少 | 检查 OP/ED 窗口与歌词判定；与日文源逐条对数量 |
| ASS 内容/编码/字段异常 | 用 `ass_repair.py --check all`；ASS 写 UTF-16LE+BOM |

## AI 介入点

→ [references/interventions.md](references/interventions.md) — 每个 🤖 点：触发条件、操作流程、判断规则。

## 参考

→ [references/workflows.md](references/workflows.md) — 典型工作流场景（从视频生成、翻译、修复、完整流程）。**设计工作流前先看这个，避免重复造轮子。**

→ [references/phase1-scan.md](references/phase1-scan.md) — Phase 1 扫描命令参考。
→ [references/phase2-triage.md](references/phase2-triage.md) — Phase 2 Whisper 修复命令参考。
→ [references/phase3-unify.md](references/phase3-unify.md) — Phase 3 专名统一 + 交付命令参考。
→ [references/full-mode.md](references/full-mode.md) — 有参考字幕时的完整工作流。
→ [references/reuse-reference-subs.md](references/reuse-reference-subs.md) — 参考字幕复用（总集篇/同一作品）的判定与判据。

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
| `--batch N` | translate_srt.py: cues per batch (default: 20; 20 beat 5/10/30/50 on EP001–003) |
| `--context-window N` | translate_srt.py: preceding JA cues (default: 0; `-1` = all past; experimental) |
| `--noun-pre-replace` | translate_srt.py: replace ja proper nouns in source before translation (default off; not validated) |
| `--qa-report` / `--repair` | translate_srt.py: write QA JSON / repair only flagged cues with per-cue rollback |
| `--detect-boundaries` | oped_fixer/oped_fill: use API (LLM) to detect OP/ED boundaries from cue patterns |
| `--skip-step1` | oped_fill: skip API boundary detection, use --op-boundary/--ed-boundary defaults |
| `--extract-nouns` | translate_srt.py: 翻译后提取专名实体 → temp/nouns/extracted_EP###.json |
| `--extract-dir <DIR>` | translate_srt.py / extractor.py: sidecar 输出目录（默认 temp/nouns） |
| `--resume` | extractor.py: 跳过健康 sidecar，只跑缺失/失败集（中断后续跑） |
| `--status` | extractor.py: 只打印健康度报告（✅/⚠/⬜），不调 LLM |
| `--min-entities N` | extractor.py: 旧 sidecar（无 chunks 字段）实体数≤N 视为失败（默认 2） |
| `--emit-mappings` | apply_map.py: 导出 ja→zh 幻觉控制表（`--merge-existing` 并入旧表） |
| `--emit-checklist <PATH>` | apply_map.py: 生成逐集 AI 审查清单（`- [x] NN集：规范名（变体）`；与 `--apply` 同用=记录实际统一项） |

> `--apply-ai-review` 是后处理快速路径，不能和 full run 一起用；它会先跑 review 阶段
> （填充 + 应用 `temp/scans/*.ai_pack.json`），再应用专名/OP-ED 的 `ai_review_fixes.json`。
> 翻译工具完整参数见 [references/translation.md](references/translation.md)。

