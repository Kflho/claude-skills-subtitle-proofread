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
  → Output: findings.json + proper-nouns.md (cleaned)
  → Does NOT write to 问题解决报告（scan is read-only, no fixes applied）

Phase 2: Triage
  → VAD → Whisper → classify each garbled cue:
      ├─ readable JP           → write SRT ✅ (→ 报告: Whisper 自动修复)
      ├─ noise (mj < 2)        → auto-cut 🗑️
      └─ JP + Latin corruption → ai_fragments_{EP}.json (🤖 AI补全)
          ├─ AI fills correction → --apply-ai-review (→ 报告: AI 短碎片补全)
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
| `[ai-review] N pending` | Read `temp/scans/ai_fragments_EP*.json`, fill `"correction"` field per fragment, then `--apply-ai-review` | All fragments have `"correction"` filled or intentionally blank |
| `AI REVIEW NEEDED: N` | Read `temp/scans/ai_review_candidates.json`, judge each candidate, write `ai_review_fixes.json`, re-run `--resume` | `--resume` completes without error |
| `Pipeline complete` + checklists exist | Read `reports/manual-review/{EP}/checklist.md`, fill `修正:` for entries you can fix from video context, leave audio-dependent ones ⬜, then `--apply-checklist` | `--apply-checklist` reports applied count |
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
