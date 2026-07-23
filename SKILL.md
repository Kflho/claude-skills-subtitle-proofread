---
name: subtitle-proofread
description: >
  Subtitle proofreading — 3-phase pipeline (scan → triage → deliver). Use when the
  user wants to proofread, scan, or fix subtitles (SRT/ASS), run Whisper ASR
  correction, unify proper nouns, or apply batch fixes. Unfixable items get [???]
  markers for Aegisub review. Covers: 字幕, subtitle, SRT, ASS, proofread, 校对, Whisper, 专有名词, captions.
---

# Subtitle Proofread

3-phase pipeline：扫描（乱码 + VAD无字幕检测）→ Whisper 修复 + 缺字幕补全 → 专名统一 + 交付。无法自动修复的条目写入 `[???]` 标记，在 Aegisub 中审查。

**资源驱动**：有什么用什么。有视频+Whisper→修复乱码+补全缺字幕；有参考字幕→注入 AI 校对上下文。缺资源也能残血运行——跳过缺失步骤，剩余步骤照常。

> v5.0: Phase 1 新增 VAD 有人声无字幕检测（需 `--video-dir`），作为第一类错误与乱码并列。详见 [references/architecture.md](references/architecture.md)。

### ASS 格式项目

本 skill 同时支持 **SRT** 和 **ASS** 两种格式。所有工具通过 `parse_subtitles()`/`write_subtitles()` 自动检测格式，无需手动转换。

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

# 完整 pipeline（推荐）：扫描 + VAD 无字幕检测 + Whisper 修复
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
> `--video-dir` 启用 VAD 有人声无字幕检测 + Whisper 修复。无视频时加 `--skip-whisper` 残血运行。
> `--limit` 只限 Phase 2 修复集数，扫描覆盖全部文件。

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

| 功能 | ja（日语） | zh（中文） | 其他 |
|------|:---:|:---:|:---:|
| 乱码扫描 | ✅ | ✅ | ✅ |
| Whisper 修复 | ✅ (kotoba) | ⚠️ 需中文模型 | ⚠️ 需对应模型 |
| Baidu 翻译层 | ❌ (日语目标无需) | ✅ (Whisper 输出 ja→zh) | ❌ |
| 词典过滤 | ✅ (jamdict/JMdict) | ✅ (jieba/498K 词) | ❌ |
| 专名分类 | ✅ (jamdict) | ✅ (jieba + 规则) | ❌ |
| Glossary 清洗 | ✅ (JMdict + 规则) | ✅ (jieba 词典 + 规则) | ❌ |
| AI 润色（去翻译腔） | ❌ (日语原文无需) | ✅ (OpenAI 兼容 API) | ❌ |

> `--lang zh` 时使用 jieba 分词 + 词典查询对标 jamdict。jieba 不可用时退回 n-gram + 启发式规则。
> Baidu 翻译为**可选**：未配置时自动降级，日语原文保留在 AI fragments 中由 AI 自行翻译。
> AI 润色为**可选**：Pipeline 末尾交互提问。需要 `LLM_API_KEY` 环境变量。无 key 时降级为 AI 助理自行润色（⚠️ 高 token 消耗，7.5 万 cue）。
> ⚠️ **translate_srt.py 必须要有 LLM_API_KEY**：无 key 时脚本无法运行。不要静默降级为 AI 自行翻译——量级太大（193 集 × 200 条 = 不可行）。正确做法：告知用户 key 为空，请用户设置后重试。详见 [references/translation.md](references/translation.md)。

## 名词库准备 + 翻译

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
  → VAD 有人声无字幕检测（需 --video-dir，无视频自动跳过）
  → build_glossary → proper-nouns.md
  → glossary AI review: AI reads full glossary, manages whitelist/blacklist directly (🤖)
  → Output: findings.json + proper-nouns.md
  → Does NOT write to 问题解决报告（scan is read-only）

Phase 2: Triage
  → 若有参考字幕 → 注入 reference_text 到 AI fragments（原文，不翻译）
  → VAD clean + Whisper garbled fix (复用 Phase 1 VAD 缓存)
  → (v5.0) Missing subtitle fill: gap 音频 → Whisper → 插入新 cue / [???]
  → classify + triage → auto-keep ✅ / ai_fragments 🤖 / auto-cut 🗑️
  → Baidu 翻译 (--lang zh): Whisper 输出 ja→zh（无凭证时降级 AI 翻译）

Phase 3: Unify
  ├─ Suspect noun search: segmentation-based detection of unrecognized names (new!)
  ├─ OP/ED fixer: cross-episode clustering → instrumental auto-clean / vocal AI review
  ├─ Noun variant detection → AI review all candidates (≤50, one pass)
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

**触发**: `[suspect-nouns] N entries → report layer 3` 或 `[SUSPECT GROUPS]`

1. 读 `temp/scans/suspect_nouns.json`
2. 逐条判断：专有名词 or 普通词？
3. 专名 → 确认译名 → 更新 `noun_mappings.json`；普通词 → 忽略

→ 详细用法见 [references/translation.md](references/translation.md)

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

**触发**: `[oped] AI review candidates` + `vocal_clusters > 0`

1. 读 `temp/scans/oped_ai_review.json`
2. 填每个 candidate 的 `canonical`（`__INSTRUMENTAL__` = 器乐）
3. 运行：`python run_all.py --apply-ai-review --video-dir "<VIDEO_DIR>"`

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

## AI 介入点

→ [references/interventions.md](references/interventions.md) — 每个 🤖 点：触发条件、操作流程、判断规则。

## 参考

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
| `--video-dir <DIR>` | Video directory — enables VAD + Whisper (v5.0: VAD missing-sub detection) |
| `-e EP005-EP010` | Specific episode range |
| `--limit 5` | First N episodes only |
| `--skip-whisper` | Skip audio processing (残血模式) |
| `--resume` | Resume after AI noun review (Phase 3 only) |
| `--force-rescan` | Re-scan even if cache fresh |
| `LLM_API_KEY` (env) | LLM API key for polish (optional) + translate_srt.py (**required**). Separate from Claude Code's. |
| `--mappings <JSON>` | translate_srt.py: path to noun_mappings.json (preferred over --glossary) |

> `--apply-ai-review` 是后处理快速路径，不能和 full run 一起用。
> 翻译工具完整参数见 [references/translation.md](references/translation.md)。
