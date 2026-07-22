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

```bash
# 路径和环境变量从 CLAUDE.md 获取
export PYTHONPATH="<scripts-dir>"           # skill 的 scripts/ 目录
export WHISPER_CLI='<path>'                  # whisper.cpp 可执行文件
export WHISPER_MODEL='<path>'                # 主模型
export WHISPER_RETRY_MODEL='<path>'          # 备用模型

cd "<project-root>"
python "<scripts-dir>/run_all.py" --video-dir "<VIDEO_DIR>" [--limit N | -e EP001-EP010]
```

> ⚠️ `--video-dir` 必须传，否则 Whisper 静默失败。`--lang` 自动检测，无需手动传。

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

## Output → Action

| Pipeline prints | What to do | Done when |
|-----------------|------------|-----------|
| `SyntaxError` / `UnicodeEncodeError` | 修代码（emoji→ASCII, 括号补全），重跑 | 该步骤成功 |
| `[ai-review] N pending` | 编辑 `temp/scans/ai_fragments_EP*.json`，填 `correction`。配对模式需同时填 `fragment.correction` 和 `paired_cues[*].correction` → `--apply-ai-review --video-dir "..."` | 所有 fragment 已填 |
| `AI REVIEW NEEDED: N` | 读 `ai_review_candidates.json`，判断专名。拒绝的加入 `lib/japanese_utils.py` COMMON_KANJI。接受的写 `ai_review_fixes.json` → `--resume`。**可能多轮**（12→6→3→0） | `Needs AI: 0` |
| `[oped] AI review candidates` | 读 `oped_ai_review.json`，填 `canonical`（`__INSTRUMENTAL__` = 器乐）→ `--apply-ai-review` | 所有候选已判断 |
| `Pipeline complete` + all phases passed | 检查 `问题解决报告.md`：专名自动应用有条目，AI审查无残留⬜ | 报告无异常 |
| `Pipeline complete` + checklists exist | 读 `reports/manual-review/{EP}/checklist.md`，填 `修正:` → `--apply-checklist` | applied count > 0 |
| `Done: 0 fixed` + no `[whisper]` | `--video-dir` 缺失或错误 — 验证 CLAUDE.md 中的路径 | Whisper 有输出 |

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
