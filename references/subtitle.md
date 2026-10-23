# 字幕校对与翻译：操作手册

> 本文件是主文档 [SKILL.md](../SKILL.md) 的子文档：主文档讲**全流程与阶段路由**，
> 这里讲**字幕这一段怎么做**——专名审查、扫描与打轴、重打轴、模块化调用、
> 翻译与润色、语言限制、Flags 全表。
>
> 什么时候该翻到这里：已经拿到「日文（或其它源语言）字幕 + 视频」，
> 要把它修对、译好、交付。

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

**变体归一 + 闸门**（分批翻译会漂；词表注入解决大部分，剩下用确定性归一）：

```bash
# 预览 / 写盘：把人工确认过的 aliases 变体改回规范写法（.bak 备份）
python "<scripts-dir>/nouns/normalize_variants.py" \
  --zh-dir "<中文翻译>" --glossary "<项目>/glossaries/<series>.json" \
  --meta "<项目>/glossaries/<series>.meta.json" --dry-run
python "<scripts-dir>/nouns/normalize_variants.py" ... --apply

# 候选（只读）：哪些变体还没进表
python "<scripts-dir>/nouns/normalize_variants.py" ... \
  --discover --ja-dir "<日文参考>" --report temp/variants_candidates.json

# 闸门：kana / echo / 变体 / CPS 超阈值就退出 1
python "<scripts-dir>/eval/text_gate.py" --zh-dir "<中文翻译>" \
  --glossary "<项目>/glossaries/<series>.json" --meta "<...meta.json>" \
  --source-dir "<日文参考>" --max-kana 0 --max-variants 0
```

> 实测：EP051 无词表 2/15 规范写法，带词表 15/15；52 集副本上变体 197 → 0。
> 别名必须人工确认：`圣诞`（圣诞老人）这类曾被误改成「三太」，已进回归测试。
> **多源（英→中 + 日→中）**：英文形也进 `aliases`（如 `Sabu → 佐武`），匹配按
> 词边界 + 忽略大小写；扁平词表同时加 `Sabu → 佐武` 供 `--mappings` 预替换。
> 闸门加 `--max-latin 0` 检查英文残留；两条线必须落到同一个 canonical
> （佐武实测：拉丁残留 178 → 0，23 个共同角色写法一致）。
> 设计见 `references/design/glossary-variants.md`。

**语境审查 agent**（专名是否符合作品世界 + 语体）：

```bash
# 背景档案（年代/人物/固定用语）+ 近似词候选判定（promote/reject/uncertain）
python "<scripts-dir>/nouns/context_review.py" --project "<项目>" \
  --glossary "<项目>/glossaries/<series>.json" \
  --meta "<项目>/glossaries/<series>.meta.json" \
  --candidates "<项目>/temp/variants_candidates.json" --mode near --dry-run
# 批准后写 _meta.aliases（备份），再跑 normalize_variants
python "<scripts-dir>/nouns/context_review.py" ... --mode near --apply-approvals

# 语体审查：确定性检测 + 定向改写（只出 patch，不写字幕）
python "<scripts-dir>/nouns/context_review.py" ... --mode style --sample 3
python "<scripts-dir>/nouns/context_review.py" ... --mode style-fix
```

> 实测（EP051，300 条）：**在 system prompt 里加风格块没有可测收益**（加与不加
> 现代语/方言命中 9–10、语体标准差 0.096–0.102），所以没有 `--style` 开关；
> 有效路径是「检测 → 定向改写 → 约束校验」（52 集：85 条 → 接受 69、命中 70→0）。
> 设计见 `references/design/style-context.md`。

**OP/ED 与固定台词统一**（复用 `fix/oped_fixer.py`，不另造聚类）：

```bash
# 1) 现有工具出跨集候选（只读）
python "<scripts-dir>/fix/oped_fixer.py" "<中文目录>" --lang zh \
  -o temp/scans/oped_fixes.json --ai-review temp/scans/oped_ai_review.json

# 2) 填 canonical：_meta.fixed_lines 优先，其余走语境审查 agent
python "<scripts-dir>/nouns/context_review.py" --project "<项目>" \
  --glossary ... --meta ... --mode oped \
  --candidates "<项目>/temp/scans/oped_ai_review.json" \
  --report "<项目>/temp/oped_filled.json"

# 3) 完整统一走文本键控（写副本；按时间桶只统一 ~35/95，文本键控残留 0）
python "<scripts-dir>/nouns/context_review.py" ... --mode oped \
  --candidates "<项目>/temp/oped_filled.json" --apply-text-keyed \
  --out-dir "<项目>/temp/oped_textkeyed"
```

> 设计见 `references/design/oped-fixed-lines.md`。

**scope**：`global` = 跨全集；`per_episode` = 仅该集；`auto` = episodes ≥ 2 取 global。

**续跑/失败**：大项目先 `--status` 确认无 ⚠ 再聚合；中断用 `--resume`。
超长请求导致空 content 时逐级降 `--chunk-size 60/40`。

## 专名审查（旧版 auto_translate · legacy）

> ⚠️ **已弃用**，仅存量项目参考。新项目一律走上方「专名审查（AI 驱动）」。
> 旧法的扫描 → API 批量分类 → 黑名单/映射表循环见
> [references/batch-review.md](batch-review.md)。

关键文件：`temp/scans/candidates.json`、`temp/noun_mappings.json`、
`temp/zh_common_blacklist.json`、`temp/scans/classified_terms.json`。

## ASS 格式项目

本 skill 同时支持 **SRT** 和 **ASS** 两种格式。所有工具通过 `read_subtitles()`/`write_subtitles()`（`lib.subtitle_io`）或 `parse_subtitles()`/`write_srt()`（`lib.whisper_utils`）自动检测格式，无需手动转换。

> **注意**：如果项目是 ASS 格式，`--input-dir` 指向包含 `.ass` 文件的目录即可。Pipeline 会像处理 SRT 一样处理 ASS，输出保持 ASS 格式。

写回 ASS 时有四条已经踩过的坑，改 `lib/subtitle_io.py` 前先看 `tests/test_ass_writeback.py`：

- **样式/层/边距跟着 cue 走**：新文件模式不再把所有行写成 `Default`，否则标题、
  招牌、歌词轨的定位全丢（实测 443 条全掉到底部）；
- **时间码按位数判精度**：ASS 是百分秒（`44.54`），SRT 是毫秒（`44,540`），
  混着当毫秒读会把整集轴往前缩；
- **`{\fad}`/`{\pos}` 不进翻译**：翻译前拆出来，写盘前拼回去，模型看不到标签就不会删；
- **译文里的真换行折成 `\N`**：模型偶尔在 JSON 里写 `\n`，直接写进 `Dialogue:`
  会把一条拆成两行、后面那条变成孤立文本。

```bash
# 写完先扫结构：条数应与源一致，且没有以非 Dialogue 开头的游离行
python -c "
from lib.subtitle_io import read_subtitles
print(len(read_subtitles('out/EP001.ass', mark_garbled=False)))"
```

## 接手已有字幕：先量再决定

拿到别人做的字幕（机翻稿、上一版交付、别组转让）时，第一步不是润色，是量。
`eval/retranslate_decision.py` 给三个数：缺陷结构、修 vs 翻的调用量、墙钟时间，
并据此给 KEEP / PATCH / RETRANSLATE 三档结论。

```bash
python "<scripts>/eval/retranslate_decision.py" \
  --zh-dir "<已有字幕>" --source-dir "<源字幕>" \
  --report "data/reports/decision.json"
# 只算成本不判质量
python "<scripts>/eval/retranslate_decision.py" --zh-dir "<已有字幕>" --cost-only
# 折算成钱（元/百万 token）
python "<scripts>/eval/retranslate_decision.py" ... --price-in 1 --price-out 2
```

判据（`--rules <json>` 可覆盖）：

| 信号 | 阈值 | 为什么致命 |
|---|---|---|
| 源语言残留（西里尔/假名） | > 0 | 有整条没翻或半句没翻 |
| 原文回显 | > 0.5% | 模型照抄源文，逐条修不如重译 |
| 条数漂移 | > 5% | 丢条/合并过，逐条修救不回来 |
| 标点缺陷（空格断句 + 长句无标点） | > 25% | 断句逻辑整体崩了 |
| 其余缺陷 5%–25% | | 判 PATCH：只重写问题条 |
| 其余缺陷 < 5% | | 判 KEEP：按 QA 清单人工过 |

读结论时注意三件事：

- **PATCH 用定点修，不要全局润色**：把问题条挑出来逐条重写（`--repair` 或
  `translate_srt` 的 QA 修复），整体润色会动到本来正确的句子；
- **成本要跟效果一起看**：修 1 万条和重翻 4 万条的调用量可能只差一倍，
  但修完还是旧底子（译名、语体都已定型）；
- **判定依赖源语言**：ru→zh 的残留看西里尔，ja→zh 看假名；工具按源文自动认。

## 运行

**第一次用先看 [references/quickstart.md](quickstart.md)**：已有日文字幕 →
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
[references/workflows.md](workflows.md) 的完整流程走。

### 环境设置

首次使用 → [references/setup.md](setup.md)（环境变量、Python 依赖、API 密钥、git 备份铁律）

已验证过的项目跳过，直接从 CLAUDE.md export 环境变量即可。

### 可选模型资产（不随 skill 分发）

模型不在仓库里；路径来自 harness 副本的 `local.json`（`scripts/harness/init_local.py` 写入，`paths.models_dir`）。下表是目录结构示例：

| 模型 | 路径 | 调用 |
|---|---|---|
| FireRedVAD/AED | `<models-dir>/FireRedVAD/{VAD,Stream-VAD,AED}` | `<qwen-python>` + `fireredvad`；`scripts/audio/detect_events.py` |
| Mel-Band RoFormer | `<models-dir>/MelBandRoformer/*.safetensors` | 仅权重；运行时未接线 |
| Qwen3-ASR/FA | `<models-dir>/Qwen3-ASR-*`, `<models-dir>/Qwen3-ForcedAligner-0.6B` | `<qwen-python>` worker |

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

### Qwen3-ASR 单集转写：先测吞吐，再铺全量

52 集 × 25 分钟 = 21 小时音频，参数选错就是十几小时差价。本机（3080 Ti Laptop）
实测，`qwen3_transcribe.py` 三个旋钮：

| 旋钮 | 建议 | 原因 |
|---|---|---|
| `--chunk-sec` | **20** | 60 秒块在旧片源上会出现 50 秒长 cue、对齐塌到块尾；20 秒 ≈ 16 条/集 0 时长，最长 cue 16 秒 |
| `--max-batch` | **8** | worker 旧版把 `max_inference_batch_size` 写死成 1，GPU 只有 24% 利用率 |
| token 预算（自动） | 块长×14 | 一块 20 秒给 ~280 token；不设上限时脏块会一直生成到 1024，EP002 单集从 4 分钟变 30+ 分钟 |

- `--max-batch` 是每次 `generate()` 的块数，8 块 × 20 秒 ≈ 160 秒音频一批；显存 16 GB 上占 ~13 GB，想更稳降到 4；
- **不要并行跑两个 worker**：显存顶到 16 GB、GPU 87 °C 降频，两集合计比串行还慢；
- 串行实测：对话密集集 3.5–6 分钟/集，片头唱段多的集会慢一些（幻觉块撞预算）；
- 全套 52 集约 4 小时，中途中断直接重跑同一命令即可（已有 SRT 会跳过）。

> 片头/片尾唱段：ASR 对唱词基本是乱写（`超大な鼓動へ` 这类），且 ForcedAligner 不给
> 时间戳（0 时长 cue 集中在 OP/ED）。合成片要发之前，OP/ED 用 `_meta.fixed_lines`
> 或 oped_fixer 统一，别指望 ASR 出对歌词。

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
> 架构/评测说明在本地开发仓库；用户用法见 [references/translation.md](translation.md)。

### 场景：文字正确、时间轴错位（重新打轴）

先抽查几句：文本与台词一致，只是起点/漂移/重叠不对。**不要重译或重转写文本**，
只把现有 cue 对到音频上。

```bash
# 先诊断，不写盘
python "<scripts-dir>/asr/align_cues.py" \
  --srt "<EN.srt>" --video "<video>" \
  --language English --engine qwen3 --python "<venv>/Scripts/python.exe"

# 确认 summary/plan 合理后，只写时间码
python "<scripts-dir>/asr/align_cues.py" \
  --srt "<EN.srt>" --asr-json temp/asr/<EP>.json --write
```

- 默认 time-only：`--write` 只改起止时间，文本不动；
- `--fix-text` 只用于乱码/错词，**本场景不要加**；
- 已有 ASR JSON 时用 `--asr-json` 跳过模型；
- 字幕文本与音频语言不一致（例如日语配音 + 英文字幕）时，英文 cue 无法直接匹配
  日文 ASR：先对齐**源语言字幕/转写**，再按句序映射译文，或提供同语言的 ASR；
- ASS 暂不支持写回，先转 SRT 或只用报告。

**语言不同（英字译文 + 日语音轨）**：`align_cues` 会给出 0 条干净匹配，
改用跨语言路线——转写原音，LLM 按语义给 cue→ASR 锚点，再分段 warp 时间码：

```bash
python "<scripts-dir>/asr/retime_crosslingual.py" \
  --source-srt "<EN.srt>" --asr-srt "<ja-asr.srt>" \
  --target-srt "<zh.srt>" --output "<zh.retimed.srt>" --report temp/retime/<EP>.json
```

> 先确认**集号对得上**：发行版集号可能和视频集号错位（实测《佐武与市捕物控》
> 英版 E01/E02 与 DVD P1/P2 对调）。症状是片头固定台词能对上、之后整体跑偏；
> `coverage` 明显偏低就是信号。集号错位只能换文件，重打轴救不了。
> 报告里 `coverage` 是硬门槛（< 0.35 直接退出 4），`holdout_error` 看真实精度。

详见 [references/retime-existing-subs.md](retime-existing-subs.md)。

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
| 重新打轴（文字不改） | `asr/align_cues.py` |
| 跨语言重打轴（译文 vs 原音） | `asr/retime_crosslingual.py` |
| 切段修复 | `whisper_spot_fix.py` |
| 接手旧稿：质量评估 + 修/翻成本 | `eval/retranslate_decision.py` |
| 交付前文本闸门 | `eval/text_gate.py` |

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
> 只能留到人工校对阶段。详见 [references/workflows.md](workflows.md) 的「已知问题」。

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
| 质量闸门 | ✅ 假名/回显 | ✅ 假名/回显 | ✅ **西里尔残留**/空格断句 | — |

> `translate_srt.py` **自动检测源语言**（ja/ru/zh），动态切换 system prompt，无需手动指定。
> 日语检测到假名时启用专用规则（敬语、主语省略等），非日语跳过。
> `--lang zh` 时使用 jieba 分词 + 词典查询对标 jamdict。jieba 不可用时退回 n-gram + 启发式规则。
> **翻译全部走 LLM**（ADR-010/011）：百度/传统 MT 路径已删除，不再维护或测试；
> 翻译是**必须调用 LLM 的阶段**，用用户已配置的国内 key（`~/.claude/settings.json`
> 的 `LLM_API_KEY`/`LLM_BASE_URL`/`LLM_MODEL`；DeepSeek，Claude Code 自动注入）。
> 成本策略：优先免费/本地；需要 token 时只接受国内服务，国外服务暂不配置。
> **字幕复核缺口在管线内自填**（ADR-012）：分两类、按失败类型处置。
> ① **QA 判失败**（`japanese_residual` 残留假名 / `echo` 未翻译 / `empty` /
> `glossary_miss` / `too_long` / `cps`）：自动 repair 没清掉的条目写进
> `temp/scans/<EP>.zh_pack.json`，AI 译成中文并逐条校验（不得含假名），
> 默认允许改写；EP001–03 注入残留实验 28/30/29 条全清、0 误伤。
> ② **ASR 与原文分歧**：只有带可比证据的文本/删条候选会问模型，时间轴不进模型；
> 默认 `REVIEW_ACTIONS=keep` 只分类（实测给权会把正确字幕改坏）。
> 详见 [references/design/review-ai-fill.md](design/review-ai-fill.md)（开发文档）。
> Review 之外的专名/润色不新增 LLM 调用（走 agent 工作单或并入翻译 prompt）；
> 无 key 时明确失败/保留原文，不回退机翻。
> AI 润色为**可选**：Pipeline 末尾交互提问。需要 `LLM_API_KEY` 环境变量。无 key 时降级为 AI 助理自行润色（⚠️ 高 token 消耗，7.5 万 cue）。
> ⚠️ **translate_srt.py 必须要有 LLM_API_KEY**：无 key 时脚本无法运行。不要静默降级为 AI 自行翻译——量级太大（193 集 × 200 条 = 不可行）。正确做法：告知用户 key 为空，请用户设置后重试。详见 [references/translation.md](translation.md)。

## 名词库准备 + 翻译

> **非日语源（ru/en/其他）**：跳过名词库准备（jamdict/jieba 不适用），直接翻译。
> 翻译后对中文输出执行专名校对即可，见上方「专名审查（AI 驱动）」。
> ```bash
> python "<scripts>/translate_srt.py" --input-dir "<源字幕>" --output-dir "<中文输出>"
> # 源语言自动检测，system prompt 动态适配
> ```

### 俄语源（ru→zh）项目

`translate_srt.py` 对 ru 自动换提示词：专名按「固定译名参考」音译、禁止把西里尔字母
留在译文里、中文全角标点、保留原文的 `\N` 分行。前两条要落到 `--mappings` 才稳：
先给一份 `ru→zh` 扁平表，同一角色的每种俄语写法各写一条，否则一集里会出现两三个名字。

```bash
# 1. 重译（保留 ASS 样式和时间轴，条数不动）
python "<scripts>/translate_srt.py" \
  --input-dir "references/俄语参考字幕/TV" --output-dir "data/zh" \
  --mappings "data/glossaries/<series>.ru-zh.json" \
  --max-chars 20 --max-cps 12

# 2. 闸门：西里尔残留和「空格断句」都必须是 0
python "<scripts>/eval/text_gate.py" --zh-dir "data/zh" \
  --source-dir "references/俄语参考字幕/TV" \
  --max-cyrillic 0 --max-cjk-space 0 --max-kana 0
```

**OP/ED 匹配**：有 ASS 样式名（`Opening*` / `Ending*` / `Lyrics`）就按样式认歌词段，
没有才退回固定时间窗——180s 的窗口会把开篇对白当成 OP（实测 EP001 正片 1:44 就开场）。
整部作品只翻一次，按原文逐句复用；同一时间点有罗马音和译本两轨时，只翻源语言那条，
另一轨复用同一句中文。逐句表存在时，表里没命中的句子留给正常翻译，
**不要**退回「窗口里出现最多的那句」兜底——那会把整段歌词换成一句（实测 ED 六行全变成
同一句台词）。canonical 兜底只用于完全没有逐句表的旧数据。

**旧稿要不要重翻**：先量再决定。

```bash
python "<scripts>/eval/retranslate_decision.py" \
  --zh-dir "<旧译文>" --source-dir "<源字幕>" --report "data/reports/decision.json"
```

判定标准与三档结论见上方「接手已有字幕：先量再决定」，这里只换成 ru 的源目录。
ru 项目的残留信号看西里尔；旧稿若同时有 20% 以上的空格断句，基本可以直接判重译。

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

→ 完整流程见 [references/translation.md](translation.md)

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
[references/batch-review.md](batch-review.md)。修完重跑至 candidates 归零。

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

→ 详细规则见 [references/interventions.md](interventions.md)

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

→ 详细说明见 [references/translation.md](translation.md)

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
> 翻译工具完整参数见 [references/translation.md](translation.md)。
