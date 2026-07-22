---
name: subtitle-proofread
description: >
  Subtitle proofreading — 3-phase pipeline (scan → triage → deliver). Use when the
  user wants to proofread, scan, or fix subtitles (SRT/ASS), run Whisper ASR
  correction, unify proper nouns, apply batch fixes, or generate human-review
  checklists. Covers: 字幕, subtitle, SRT, ASS, proofread, 校对, Whisper, 专有名词, captions.
---

# Subtitle Proofread

3-phase pipeline. Run once; handle each 🤖 intervention as it fires.

## End-to-end run（AI 必须执行的完整序列）

```
Step 0: 读 CLAUDE.md → 获取路径、密钥、项目配置
Step 1: python run_all.py --lang ja --video-dir "..." --limit N --force-rescan
           │
           ├─ 语法错误/编码错误 → 修完 git commit，重跑 Step 1
           ├─ Pipeline complete + AI REVIEW NEEDED: N
           │    → 跳到 Step 3（专名审查）
           └─ Pipeline complete + all phases passed
                → 跳到 Step 2（碎片补全，如果有 ai_fragments_*.json）
Step 2: 读 ai_fragments_{EP}.json → **直接 Edit JSON** 填 correction → `--apply-ai-review --video-dir "..."`（必须带 --video-dir）
Step 3: 读 ai_review_candidates.json → 判断真伪 → 写 ai_review_fixes.json
        + 把拒绝项加入 lib/japanese_utils.py COMMON_KANJI
        → --resume
        → 重复 Step 3 直到 Needs AI = 0
Step 4: 检查 reports/问题解决报告.md — 不应有残留 ⬜（除非人工审查）
Step 5: git commit 两端（skill + project）
```

> ⚠️ **每步失败必须立刻修，不要跳过。** 缓存不完整（如 auto_clean 失败但 findings.json 已写入）会导致后续跳过关键步骤。

## Run

```bash
# Paths & env vars → read from CLAUDE.md 「密钥与路径」+ 「项目路径」
export PYTHONPATH="<scripts-dir>"
export WHISPER_CLI='<path>'; export WHISPER_MODEL='<path>'; export WHISPER_RETRY_MODEL='<path>'

cd "<project-root>" && python run_all.py --lang ja --video-dir "<VIDEO_DIR>" [--limit N | -e EP001-EP010]
```

> ⚠️ `--video-dir` mandatory for audio-mode projects. Without it Whisper silently produces 0 fixes.

## Pipeline

```
Phase 1: Scan
  → unified_scanner: garbled chars, repeat patterns, term frequency
  → build_glossary: corpus frequency → proper-nouns.md
  → auto_clean: prune common words, rebuild clean glossary (automatic)
  → glossary AI review: borderline entries printed inline (🤖, ≤20 entries)
  → Output: findings.json + proper-nouns.md (cleaned)
  → Does NOT write to 问题解决报告（scan is read-only, no fixes applied）

Phase 2: Triage
  → VAD → Whisper → classify each garbled cue:
      ├─ noise (mj < 2)        → auto-cut 🗑️
      ├─ readable JP + 原文无语义 (mj_orig < 2) → auto-keep ✅  噪声→Whisper即改善
      ├─ readable JP + 长度正常 (ratio ≤ 3x)    → auto-keep ✅
      ├─ readable JP + 长度异常 (ratio > 3x)     → ai_fragments (🤖 配对审查)
      │     └─ fragment 带 paired_cues，AI 可改邻居或 __DELETE__
      └─ JP + Latin corruption → ai_fragments_{EP}.json (🤖 AI补全)
          ├─ AI fills correction → --apply-ai-review (→ 报告: AI 短碎片补全)
          │     ├─ 原文 mj < 2 的 fragment → auto-cut（不升级人工）
          │     └─ paired mode: AI 可 __DELETE__ 邻居 cue
          └─ AI can't fix      → VAD check
              ├─ no speech     → auto-cut 🗑️
              └─ has speech    → checklist.md (👤 人工 → 报告: 人工审查)

Phase 3: Unify
  ├─ Noun variant detection: noun_checker scans SRTs against glossary
  ├─ Auto-classify: deterministic accept (→ 报告: 专名自动应用)
  │                 / reject / needs_ai (🤖 → 报告: AI 专名审查)
  ├─ AI noun judgment: Claude reads ai_review_candidates.json (≤20 entries)
  │                    → writes ai_review_fixes.json → --resume (Phase 3 only)
  └─ Deliver: apply all fixes + human checklist + video clips

Report: reports/问题解决报告.md（自动生成，按 Phase 分组）
```

> **mj** = meaningful Japanese character count. mj < 2 = noise.
> AI fragments → `temp/scans/ai_fragments_{EP}.json` (machine-readable), NOT markdown.
> Glossary maintenance is automatic: build → auto_clean → rebuild runs in Phase 1.
> **Token efficiency**: AI never reads `proper-nouns.md` (200+ lines). It only reads small JSON files (ai_fragments_{EP}.json, ai_review_candidates.json).

## Output → action

| Pipeline prints | What to do | Done when |
|-----------------|------------|-----------|
| `SyntaxError` / `UnicodeEncodeError` | **立即修代码**。emoji→ASCII, 括号不匹配→补全。修完 git commit，重跑 | 该步骤成功 |
| `[ai-review] N pending` | **直接编辑** `temp/scans/ai_fragments_EP*.json`，每个 fragment 填 `correction`。**配对模式** (`mode:paired`)：必须同时填 `fragment.correction` 和 `paired_cues[*].correction`（target cue）。邻居填 `__DELETE__` 删除。留空=保持不变。→ `--apply-ai-review --video-dir "..."`（**必须带 --video-dir**，否则无法提取视频片段） | 所有可判断的 fragment 已填 |
| `AI REVIEW NEEDED: N` | 读 `temp/scans/ai_review_candidates.json`，判断每个候选是否专名。**拒绝的必须加入 `lib/japanese_utils.py` COMMON_KANJI**。接受的写 `ai_review_fixes.json`。→ `--resume`。**可能需多轮**：12→6→3→0 是正常收敛过程 | `--resume` 输出 `Needs AI: 0` |
| `Pipeline complete — all phases passed` | 检查 `reports/问题解决报告.md`：专名自动应用有条目（非"暂无记录"），AI专名审查无残留⬜ | 报告无异常 |
| `Pipeline complete` + checklists exist | 读 `reports/manual-review/{EP}/checklist.md`，填 `修正:`。看视频片段判断 → `--apply-checklist` | `--apply-checklist` 报告 applied count |
| `Done: 0 fixed` + no `[whisper]` | `--video-dir` is missing or wrong — verify path in CLAUDE.md | Whisper runs and produces output |

## AI intervention points

→ [AI-INTERVENTIONS.md](AI-INTERVENTIONS.md) — each 🤖 point: trigger, procedure, judgment rules.

## Reference & debugging

→ [LAYERS.md](LAYERS.md) — standalone commands, glossary maintenance cycle.

## Flags

| Flag | When |
|------|------|
| `--dry-run` | Preview, no file changes |
| `-e EP005-EP010` | Specific episode range |
| `--limit 5` | First N episodes only |
| `--skip-whisper` | Skip audio processing |
| `--resume` | Resume after AI noun review (Phase 3 only — skips scan + audio) |
| `--force-rescan` | Re-scan even if cache fresh |

> `--apply-ai-review` and `--apply-checklist` are standalone fast paths — never combine with a full run.
