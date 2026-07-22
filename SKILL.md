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
Phase 1: Scan       → findings.json + proper-nouns.md
Phase 2: Triage     → VAD → Whisper → classify:
                       ├─ readable JP           → write SRT ✅
                       ├─ noise (mj < 2)        → auto-cut 🗑️
                       └─ JP + Latin corruption → ai_fragments_{EP}.json (🤖 AI补全)
                           ├─ AI fills correction → --apply-ai-review
                           └─ AI can't fix      → VAD check
                               ├─ no speech     → auto-cut 🗑️
                               └─ has speech    → checklist.md (👤 人工)
Phase 3: Unify      → proper nouns (collect → classify → apply)
                    → deliver: human checklist + video clips

Report: reports/问题解决报告.md（Phase分组，自动生成）
```

> **mj** = meaningful Japanese character count (kana/kanji minus exclamation kana like あっ！えーっ！). mj < 2 = noise.
> AI fragments write to `temp/scans/ai_fragments_{EP}.json` (machine-readable), NOT markdown.

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
| `--resume` | Resume after AI noun review |
| `--force-rescan` | Re-scan even if cache fresh |

> `--apply-ai-review` and `--apply-checklist` are standalone fast paths — never combine with a full run.
