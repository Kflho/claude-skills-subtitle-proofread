---
name: subtitle-proofread
description: >
  Subtitle proofreading pipeline — 6-layer, one-pass. Use when the user wants to
  proofread, scan, or fix subtitles (SRT/ASS), run Whisper ASR correction, unify
  proper nouns, apply batch fixes, or generate human-review checklists. Covers:
  字幕, subtitle, SRT, ASS, proofread, 校对, Whisper, 专有名词, captions.
---

# Subtitle Proofread

## Quick run

```bash
export PYTHONPATH="C:/Users/54238/.claude/skills/subtitle-proofread/scripts"
export WHISPER_CLI='D:/software/video/whisper-cublas-12.4.0-bin-x64/whisper-cli.exe'
export WHISPER_MODEL='D:/software/video/whisper-cublas-12.4.0-bin-x64/models/ggml-kotoba-whisper-v2.0-q5_0.bin'
export WHISPER_RETRY_MODEL='D:/software/video/whisper-cublas-12.4.0-bin-x64/models/ggml-large-v3-q5_0.bin'

cd "<project-root>" && PYTHONIOENCODING=utf-8 python \
  "C:/Users/54238/.claude/skills/subtitle-proofread/scripts/run_all.py" \
  --lang ja --video-dir "<VIDEO_DIR>" [flags]
```

> ⚠️ Always pass `--video-dir` for audio-mode projects. Without it, Whisper
> silently fails (no error, 0 fixes).

## Common flags

| Flag | When |
|------|------|
| `--dry-run` | Preview only, no file changes |
| `-e EP005-EP010` | Specific episode range |
| `--limit 5` | First N episodes |
| `--skip-whisper` | Skip audio processing |
| `--resume` | Resume after AI review |
| `--force-rescan` | Force re-scan even if cache fresh |

> ⚠️ `--apply-ai-review` and `--apply-checklist` are standalone fast paths.
> Do NOT combine with a full run — they skip the entire pipeline.

## Pipeline flow (🤖 = AI intervention)

```
L1   scan            → findings.json + proper-nouns.md
L2   fix (Whisper)   → SRT repaired
L2.5 🤖 AI fragment  → context completion (→ AI-INTERVENTIONS.md #L2.5)
L3.1 script clean    → auto_clean_glossary prune
L3.2 🤖 glossary     → judge borderline entries (→ AI-INTERVENTIONS.md #L3.2)
L3.3 noun check      → OP/ED + noun_table → candidates
L3.5 🤖 noun judgment → decide unknowns (→ AI-INTERVENTIONS.md #L3.5)
L4   apply           → batch-apply all fixes
L5   ASS repair      → (SRT: skipped)
L6   deliver         → human checklist + video clips
L6.5 🤖 pre-review   → AI fix before human (→ AI-INTERVENTIONS.md #L6.5)
```

## Key output signals

| Pipeline says | Action |
|---------------|--------|
| `[ai-review] N pending` | → L2.5: read ai_review.md, fill 修正:, apply |
| `AI REVIEW NEEDED: N` | → L3.5: read ai_review_candidates.json, judge, resume |
| `Pipeline complete` | → L6.5: check checklists, pre-review, apply fixable |
| `Done: 0 fixed` + no `[whisper]` | Whisper didn't run — check `--video-dir` |

## AI介入全部步骤

详见 → [AI-INTERVENTIONS.md](AI-INTERVENTIONS.md)

## 各层参考与调试

详见 → [LAYERS.md](LAYERS.md)

## Git guardrail

```bash
cd "<project-root>" && git add -A && git commit -m "备份：<what>"
```
