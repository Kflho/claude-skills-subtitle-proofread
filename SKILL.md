---
name: subtitle-proofread
description: >
  Subtitle proofreading — 3-phase pipeline (scan → triage → deliver). Use when the
  user wants to proofread, scan, or fix subtitles (SRT/ASS), run Whisper ASR
  correction, unify proper nouns, apply batch fixes, or generate human-review
  checklists. Covers: 字幕, subtitle, SRT, ASS, proofread, 校对, Whisper, 专有名词, captions.
---

# Subtitle Proofread

3-phase pipeline：扫描乱码 → Whisper 修复 → 专名统一 + 交付。

**资源驱动**：有什么用什么。有视频+Whisper→修复乱码；有参考字幕→注入 AI 校对上下文。缺资源也能残血运行——跳过缺失步骤，剩余步骤照常。

### ASS 格式项目

本 skill 同时支持 **SRT** 和 **ASS** 两种格式。所有工具通过 `parse_subtitles()`/`write_subtitles()` 自动检测格式，无需手动转换。

> **注意**：如果项目是 ASS 格式，`--input-dir` 指向包含 `.ass` 文件的目录即可。Pipeline 会像处理 SRT 一样处理 ASS，输出保持 ASS 格式。

## 首次使用？

检查项目 `CLAUDE.md` 末尾是否有 `## SKILL INITIALIZED: true`。

**没有** → 首次使用。读取 `references/first-run.md`，跟随初始化向导完成配置后再继续。

**有** → 已初始化。从 CLAUDE.md 获取路径，直接进入 pipeline。

> 如需重新初始化（添加参考字幕、更换模型等），删除 CLAUDE.md 中的 `SKILL INITIALIZED: true` 行即可。

## 运行

### 1. 导出环境变量

从项目 CLAUDE.md「密钥与路径」段逐条 `export`。**不要跳过** — 缺 env var 会导致 Whisper 静默跳过。

```bash
export PYTHONIOENCODING=utf-8   # 防 Windows GBK 乱码
```

### 2. 验证关键路径

```bash
test -f "$WHISPER_CLI" && echo "[OK] whisper-cli" || echo "[MISSING] whisper-cli"
test -f "$WHISPER_MODEL" && echo "[OK] model" || echo "[MISSING] model"
test -d "<VIDEO_DIR>" && echo "[OK] video" || echo "[MISSING] video"
```

有 `[MISSING]` → 告知用户。Whisper 缺失可残血运行（跳过音频修复）。

### 2.5. 验证 Python 依赖

```bash
python --version           # 需要 Python 3.12+
# 日语项目 — jamdict（JMdict 词典，专名分类）+ Janome（形态素解析，词汇提取）
python -c "from jamdict import Jamdict; Jamdict(); print('[OK] jamdict')" 2>/dev/null \
  || { echo "[INSTALL] jamdict..."; pip install jamdict; }
python -c "from janome.tokenizer import Tokenizer; Tokenizer().tokenize('テスト'); print('[OK] janome')" 2>/dev/null \
  || { echo "[INSTALL] janome..."; pip install janome; }
# 中文项目 — jieba（分词 + 词典过滤，对标 jamdict）
python -c "import jieba; jieba.initialize(); print('[OK] jieba', len(jieba.dt.FREQ), 'words')" 2>/dev/null \
  || { echo "[INSTALL] jieba..."; pip install jieba; }
```

`[OK]` → 继续。`[INSTALL]` → 自动安装后继续。安装失败 → 残血运行（退回规则分类）。

- **jamdict** 不可用 → Phase 3 退回规则分类
- **Janome** 不可用 → Phase 1 退回 n-gram 切分（~40% 碎片率）
- **jieba** 不可用 → Phase 1 退回 n-gram 切分，Phase 3 退回规则分类

### 2.6. AI 润色密钥（--lang zh 可选）

```bash
# OpenAI 兼容 API（批量润色中文字幕，10句/批）
# 支持 DeepSeek、OpenAI、Gemini 等任何 /chat/completions 端点
export POLISH_API_KEY="sk-..."
# 可选：覆盖默认模型和端点
export POLISH_MODEL="gpt-4o-mini"          # 默认 deepseek-chat
export POLISH_BASE_URL="https://api.openai.com/v1"  # 默认 DeepSeek
```

> ⚠️ 不要复用 Claude Code 的 key。创建独立的 API key 用于润色脚本。
> 未设置时降级：Pipeline 末尾交互提问时选 `y` → AI 助理逐句润色（~7.5 万 cue，高 token 消耗）。
> 选 `n` → 跳过润色，直接交付。

### 3. 运行

```bash
cd "<project-root>"
python "<scripts-dir>/run_all.py" \
  --input-dir "<SUBTITLE_DIR>" \
  [--video-dir "<VIDEO_DIR>"] \
  [--skip-whisper] [--limit N | -e EP001-EP010]
```

> `--input-dir` 指定字幕子目录（默认 `AI审查后`）。无视频时加 `--skip-whisper` 残血运行。
> `--lang` 自动检测。`--limit` 只限 Phase 2 修复集数，扫描覆盖全部文件。

### 4. 验证

**必须**执行，不靠 "Pipeline complete" 判断成功：

1. 读 `reports/问题解决报告.md`
   - **文件存在** → 搜索 `⬜`
   - **文件不存在**（单文件/残血模式常见）→ 读 `temp/scans/findings.json`，检查 `garbled_cues` 和 `per_episode_issues`
2. 搜索 `⬜` → 逐个分析每条 ⬜：
   - 同一 (EP, 时间) 在「Whisper自动修复」section 已有 ✅ → **false alarm**，忽略
   - 同一 (EP, 时间) 在 AI fragment section 有 correction 非空但仍是 ⬜ → SRT 已修，报告未同步，忽略
   - 其余 → 回到对应[暂停点](#暂停点--action)处理
3. 确认 Phase 3「专名自动应用」非空（非"暂无记录"）
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
> AI 润色为**可选**：Pipeline 末尾交互提问。需要 `POLISH_API_KEY` 环境变量。无 key 时降级为 AI 助理自行润色（⚠️ 高 token 消耗，7.5 万 cue）。

## Pipeline

```
Phase 1: Scan
  → unified_scanner: garbled chars, repeat patterns, term frequency
      ja: Janome 形态素解析（名词提取），zh: jieba 分词，en: n-gram
  → build_glossary: jamdict/jieba 词典过滤 + frozenset → proper-nouns.md
  → auto_clean: prune common words, rebuild clean glossary (automatic)
  → glossary AI review: borderline entries printed inline (🤖, ≤20 entries)
  → Output: findings.json + proper-nouns.md (cleaned)
  → Does NOT write to 问题解决报告（scan is read-only）

Phase 2: Triage
  → 若有参考字幕 → 注入 reference_text 到 AI fragments（原文，不翻译）
  → VAD → Whisper → Baidu 翻译 (zh only) → classify each garbled cue:
      ├─ noise (mj < 2)        → auto-cut 🗑️
      ├─ readable JP + 原文无语义 → auto-keep ✅
      ├─ readable JP + 长度正常 → auto-keep ✅
      ├─ readable JP + 长度异常 → ai_fragments (🤖 配对审查)
      └─ JP + Latin corruption → ai_fragments_{EP}.json (🤖 AI补全)
          ├─ AI fills correction → --apply-ai-review
          └─ AI can't fix → VAD check → auto-cut or 人工审查
  → Baidu 翻译 (--lang zh 项目): Whisper 输出 ja→zh 翻译后进入分类
      无凭证时降级: 日语原文 → AI fragments (AI 自行翻译)

Phase 3: Unify
  ├─ OP/ED fixer: cross-episode clustering → instrumental auto-clean / vocal AI review
  ├─ Noun variant detection → auto-classify → AI judgment (iterative, ≤20 entries)
  └─ Deliver: apply all fixes + human checklist + video clips

Phase 4: Polish (--lang zh only, optional)
  └─ 交互提问 → LLM 批量润色（10句/批，OpenAI 兼容 API）
       ├─ 有 POLISH_API_KEY → polish_zh.py 自动润色
       └─ 无 key → AI 助理自行润色（⚠️ 高耗费，7.5万 cue）

Report: reports/问题解决报告.md（自动生成，按 Phase 分组）
```

> **mj** = meaningful Japanese character count。mj < 2 = noise。
> AI 审查只读小 JSON 文件（ai_fragments_{EP}.json, ai_review_candidates.json），不读词表全文。

## 暂停点 → Action

Pipeline 不会自动暂停。输出中看到以下关键字时，**停下来处理再继续**。

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

**触发**: `AI REVIEW NEEDED: N`（N > 0）

1. 读 `temp/scans/ai_review_candidates.json`（小文件，≤20条）
2. 判断每条是专名还是普通词
3. 专名 → 写 `ai_review_fixes.json`；普通词 → 加入对应语言 utils 的 COMMON_KANJI（ja: `japanese_utils.py`，zh: `chinese_utils.py`）
4. 运行：`python run_all.py --resume`
5. **迭代**直到 `Needs AI: 0`（12→6→3→0 是正常收敛）
6. **收敛后**：向用户展示最终专有名词表（`reports/proper-nouns.md`），询问是否采用当前词表。用户确认后再进入 Phase 3 交付步骤

> 详细规则 → [references/interventions.md](references/interventions.md)

### OP/ED 审查

**触发**: `[oped] AI review candidates` + `vocal_clusters > 0`

1. 读 `temp/scans/oped_ai_review.json`
2. 填每个 candidate 的 `canonical`（`__INSTRUMENTAL__` = 器乐）
3. 运行：`python run_all.py --apply-ai-review --video-dir "<VIDEO_DIR>"`

### 人工审查

**触发**: `Human review pending: N`（N > 0）或 checklist 文件存在

1. 读 `reports/manual-review/{EP}/checklist.md`
2. 每项填 `修正:` 字段
3. 运行：`python run_all.py --apply-checklist`

### AI 润色（--lang zh）

**触发**: Pipeline 末尾交互提问 `是否对最终字幕进行 AI 润色？(y/n)`

- **y** + 已设 `POLISH_API_KEY` → 自动调用 `polish_zh.py`（支持 SRT/ASS），输出到 `中文润色后/`
- **y** + 无 key → AI 助理自行润色：
  1. 读 `AI审查后/` 下所有字幕文件（SRT 或 ASS）
  2. 逐文件、逐句润色对白文本（去翻译腔、口语化）
  3. 保留专有名词（参考 `reports/proper-nouns.md` 如有）
  4. 写回原目录或 `中文润色后/`
  5. **仅适合 ≤5 集的样本项目**。全集项目（>10集）→ 建议配置 API key
- **n** → 跳过，直接交付 `AI审查后/`

## 错误恢复

| 输出 | 操作 |
|------|------|
| `SyntaxError` / `UnicodeEncodeError` | emoji→ASCII、括号补全，修完重跑 |
| `Done: 0 fixed` + 无 `[whisper]` 输出 | `--video-dir` 缺失或路径错 — 验证 CLAUDE.md 路径 |
| 某步骤失败但已写中间文件 | 清空 `temp/` + `reports/`，加 `--force-rescan` 重跑 |
| 参考字幕乱码（西里尔/中文变 `?`） | v2 已自动检测编码（UTF-8/CP1251/KOI8-R/Shift-JIS/GBK） |
| `[translate] Baidu credentials not found` | 正常降级。配置 `BAIDU_APPID` + `BAIDU_SECRET` 或接受 AI 自行翻译 |
| `[polish] POLISH_API_KEY not set` | 正常降级。设置环境变量或选 `n` 跳过润色。不要复用 Claude Code 内部 key |

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
| `-e EP005-EP010` | Specific episode range |
| `--limit 5` | First N episodes only |
| `--skip-whisper` | Skip audio processing (残血模式) |
| `--resume` | Resume after AI noun review (Phase 3 only) |
| `--force-rescan` | Re-scan even if cache fresh |
| `POLISH_API_KEY` (env) | Chinese AI polish (optional). Separate key from Claude Code's. |

> `--apply-ai-review` 和 `--apply-checklist` 是后处理快速路径，不能和 full run 一起用。
