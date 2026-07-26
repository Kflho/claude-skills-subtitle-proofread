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

> 翻译完成后对中文SRT做专有名词一致性审查。最常用的入口点。

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

→ 完整流程见 [references/batch-review.md](references/batch-review.md)

### ASS 格式项目

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

**Phase 3：专名统一**（扫描 + 交互审查）

```bash
# 有日文源 → 交叉比对
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
```

> 适用：没有任何字幕文件，或已有字幕质量太差不值得修复。
> 输出：Whisper 自带 VAD 分段，直接生成完整 SRT。

**Phase 4：AI 润色**（--lang zh 项目可选）

```bash
python "<scripts-dir>/polish_zh.py" --input-dir "<SUBTITLE_DIR>"
```

### LLM API 配置

翻译（`translate_srt.py`）、润色（`polish_zh.py`）、fix_tail_wo 翻译步骤共用 `LLM_API_KEY`。

> ⚠️ **模型名称**：DeepSeek API 当前只支持 `deepseek-v4-pro` 和 `deepseek-v4-flash`。
> `deepseek-chat` 已失效（返回 HTTP 400）。
>
> ```bash
> export LLM_API_KEY='sk-...'
> export LLM_MODEL='deepseek-v4-pro'       # 默认值，对应 config.py LLM_MODEL_DEFAULT
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
> 翻译后对中文输出执行专名校对即可（`auto_translate.py --target-dir`），见下方「专名统一审查」。
> ```bash
> python "<scripts>/translate_srt.py" --input-dir "<源字幕>" --output-dir "<中文输出>"
> # 源语言自动检测，system prompt 动态适配
> ```

翻译项目**必须先准备名词库**，否则专名翻译不一致。

→ 完整流程见 [references/translation.md](references/translation.md)

简短版：
```bash
# 1. 扫描生成词表
python "<scripts>/scan/unified_scanner.py" --target-dir "<日文源>" \
  --build-glossary --glossary-output reports/proper-nouns.md --project-lang ja
python "<scripts>/nouns/build_glossary.py" --findings temp/scans/findings.json \
  -o reports/proper-nouns.md --mappings-output temp/noun_mappings.json

# 2. 🤖 AI 审查词表 → 编辑 temp/noun_mappings.json
#    ⚠️ 确保每个专名的所有书写形式（汉字/片假名/平假名）都有映射！

# 2.5. 🚨 映射完整性检查 — 翻译前必做
#    确认日语源中实际出现的书写形式都在 mappings 中有对应条目
#    反面案例：mapping 有「トビラ→飞雄」但没有「扉→飞雄」→ 翻译崩坏

# 3. 翻译
python "<scripts>/translate_srt.py" --input-dir "<日文源>" --output-dir "<输出>" \
  --mappings temp/noun_mappings.json

# 4. 🚨 翻译后验证 — 必须执行（不靠 exit 0 判断成功）
#    a. grep 日语残留（零容忍）
#    b. grep 已知错误专名
#    c. 发现残留 → 手工修复或标 [???]，错误专名 → 回到步骤 2.5 补全映射
```

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

> **mj** = meaningful Japanese character count。mj < 2 = noise。
> AI 审查只读小 JSON 文件（ai_fragments_{EP}.json, ai_review_candidates.json），不读词表全文。

## 暂停点 → Action

Pipeline 不会自动暂停。输出中看到以下关键字时，**停下来处理再继续**。

### 疑似专名搜索

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
| `HTTP Error 400: Bad Request` + `invalid_request_error` | 模型名不兼容。检查 API 返回的 supported model names，更新 `lib/config.py` 中 `LLM_MODEL_DEFAULT`（当前 `deepseek-v4-pro`）。也可通过 `LLM_MODEL` env 或 `--model` CLI 参数覆盖 |

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
| `LLM_MODEL` (env) | Override default model. Current default: `deepseek-v4-pro`. Use `--model` for per-run override. |
| `--source-lang <LANG>` | translate_srt.py: force source language (ja/ru/zh). Default: auto-detect. |
| `--mappings <JSON>` | translate_srt.py: path to noun_mappings.json (preferred over --glossary) |
| `--detect-boundaries` | oped_fixer/oped_fill: use API (LLM) to detect OP/ED boundaries from cue patterns |
| `--skip-step1` | oped_fill: skip API boundary detection, use --op-boundary/--ed-boundary defaults |

> `--apply-ai-review` 是后处理快速路径，不能和 full run 一起用。
> 翻译工具完整参数见 [references/translation.md](references/translation.md)。

### 专名统一审查（独立工具）

翻译完成后（或已有翻译文件），用 `auto_translate.py` 做专名校对：

```bash
cd "<project-root>"

# 有日文源 → 交叉比对（最佳）
python "<scripts-dir>/auto_translate.py" \
  --source-dir "<日文源>" \
  --target-dir "<中文翻译>" \
  --mappings temp/noun_mappings.json

# 无日文源 → 中文侧扫描（降级）
python "<scripts-dir>/auto_translate.py" \
  --target-dir "<中文翻译>" \
  --mappings temp/noun_mappings.json
```

**完整迭代循环**（反复运行同一命令自动推进）：

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

**关键文件**：

| 文件 | 作用 |
|------|------|
| `temp/scans/candidates.json` | AI审查输入（扫描器输出） |
| `temp/noun_mappings.json` | ja→zh 专名映射（补专名用） |
| `temp/zh_common_blacklist.json` | 中文普通词黑名单（补普通词用） |
| `temp/scans/classified_terms.json` | API 批量分类结果 |

**黑名单机制**（v5.1）：`find_suspect_nouns.py` 支持 `--zh-blacklist <JSON>` 加载外部普通词列表。`auto_translate.py` 自动检测 `temp/zh_common_blacklist.json` 并传递。扫描器跳过黑名单中的词，从源头减少误报。

> 和 `run_all.py` Phase 3 共用同一审查引擎，结果质量一致。
> 详细用法见 [references/translation.md](references/translation.md) 和 [references/batch-review.md](references/batch-review.md)。
