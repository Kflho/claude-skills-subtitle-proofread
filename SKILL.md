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

## 首次使用？

检查项目 `CLAUDE.md` 末尾是否有 `## SKILL INITIALIZED: true`。

**没有** → 首次使用。读取 `user/init-wizard.md`，跟随初始化向导完成配置后再继续。

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

### 3. 运行

```bash
cd "<project-root>"
python "<scripts-dir>/run_all.py" --video-dir "<VIDEO_DIR>" [--limit N | -e EP001-EP010]
```

> `--video-dir` 必须传。`--lang` 自动检测。`--limit` 只限 Phase 2 修复集数，扫描覆盖全部文件。

### 4. 验证

**必须**执行，不靠 "Pipeline complete" 判断成功：

1. 读 `reports/问题解决报告.md`
2. 搜索 `⬜` → 逐个分析每条 ⬜：
   - 同一 (EP, 时间) 在「Whisper自动修复」section 已有 ✅ → **false alarm**，忽略
   - 同一 (EP, 时间) 在 AI fragment section 有 correction 非空但仍是 ⬜ → SRT 已修，报告未同步，忽略
   - 其余 → 回到对应[暂停点](#暂停点--action)处理
3. 确认 Phase 3「专名自动应用」非空（非"暂无记录"）
4. 0 条**真正待处理**的 ⬜ → 完成

> 脚本 exit 0 ≠ 成功。同一个 cue 可能出现在报告多个 section，一边 ✅ 就算干净。

## Pipeline

```
Phase 1: Scan
  → unified_scanner: garbled chars, repeat patterns, term frequency
  → build_glossary: corpus frequency → proper-nouns.md
  → auto_clean: prune common words, rebuild clean glossary (automatic)
  → glossary AI review: borderline entries printed inline (🤖, ≤20 entries)
  → Output: findings.json + proper-nouns.md (cleaned)
  → Does NOT write to 问题解决报告（scan is read-only）

Phase 2: Triage
  → 若有参考字幕 → 注入 reference_text 到 AI fragments（原文，不翻译）
  → VAD → Whisper → classify each garbled cue:
      ├─ noise (mj < 2)        → auto-cut 🗑️
      ├─ readable JP + 原文无语义 → auto-keep ✅
      ├─ readable JP + 长度正常 → auto-keep ✅
      ├─ readable JP + 长度异常 → ai_fragments (🤖 配对审查)
      └─ JP + Latin corruption → ai_fragments_{EP}.json (🤖 AI补全)
          ├─ AI fills correction → --apply-ai-review
          └─ AI can't fix → VAD check → auto-cut or 人工审查

Phase 3: Unify
  ├─ OP/ED fixer: cross-episode clustering → instrumental auto-clean / vocal AI review
  ├─ Noun variant detection → auto-classify → AI judgment (iterative, ≤20 entries)
  └─ Deliver: apply all fixes + human checklist + video clips

Report: reports/问题解决报告.md（自动生成，按 Phase 分组）
```

> **mj** = meaningful Japanese character count。mj < 2 = noise。
> AI 审查只读小 JSON 文件（ai_fragments_{EP}.json, ai_review_candidates.json），不读词表全文。

## 暂停点 → Action

Pipeline 不会自动暂停。输出中看到以下关键字时，**停下来处理再继续**。

### AI 碎片补全

**触发**: `[ai-review] N pending`（N > 0）

1. 读 `temp/scans/ai_fragments_EP*.json`
2. 填每个 fragment 的 `correction` 字段（判断规则 → [AI-INTERVENTIONS.md](AI-INTERVENTIONS.md)）
3. 运行：`python run_all.py --apply-ai-review --video-dir "<VIDEO_DIR>"`
4. 重跑后检查报告，确认该 EP 无 ⬜

### 专有名词审查

**触发**: `AI REVIEW NEEDED: N`（N > 0）

1. 读 `temp/scans/ai_review_candidates.json`（小文件，≤20条）
2. 判断每条是专名还是普通词
3. 专名 → 写 `ai_review_fixes.json`；普通词 → 加入 `japanese_utils.py` COMMON_KANJI/KATAKANA
4. 运行：`python run_all.py --resume`
5. **迭代**直到 `Needs AI: 0`（12→6→3→0 是正常收敛）

> 详细规则 → [AI-INTERVENTIONS.md § Phase 3](AI-INTERVENTIONS.md)

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

## 错误恢复

| 输出 | 操作 |
|------|------|
| `SyntaxError` / `UnicodeEncodeError` | emoji→ASCII、括号补全，修完重跑 |
| `Done: 0 fixed` + 无 `[whisper]` 输出 | `--video-dir` 缺失或路径错 — 验证 CLAUDE.md 路径 |
| 某步骤失败但已写中间文件 | 清空 `temp/` + `reports/`，加 `--force-rescan` 重跑 |

## AI 介入点

→ [AI-INTERVENTIONS.md](AI-INTERVENTIONS.md) — 每个 🤖 点：触发条件、操作流程、判断规则。

## 参考

→ [user/run-reference.md](user/run-reference.md) — 独立命令、环境验证、调试指南。
→ [user/full-mode.md](user/full-mode.md) — 有参考字幕时的完整工作流。

## Flags

| Flag | When |
|------|------|
| `--dry-run` | Preview, no file changes |
| `-e EP005-EP010` | Specific episode range |
| `--limit 5` | First N episodes only |
| `--skip-whisper` | Skip audio processing |
| `--resume` | Resume after AI noun review (Phase 3 only) |
| `--force-rescan` | Re-scan even if cache fresh |

> `--apply-ai-review` 和 `--apply-checklist` 是后处理快速路径，不能和 full run 一起用。
