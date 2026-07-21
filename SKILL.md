---
name: subtitle-proofread
description: >
  Subtitle proofreading pipeline — 6-layer, one-pass. Use when the user wants to
  proofread, scan, or fix subtitles (SRT/ASS), run Whisper ASR correction, unify
  proper nouns, apply batch fixes, or generate human-review checklists. Covers:
  字幕, subtitle, SRT, ASS, proofread, 校对, Whisper, 专有名词, captions.
---

# Subtitle Proofread

## When invoked — do this

### Step 1: Read project config from CLAUDE.md

Read the project's `CLAUDE.md`. Extract these values from the tables and code
blocks:

| What | Where in CLAUDE.md | Variable |
|------|--------------------|----------|
| Video directory | `项目路径` table → row with `视频` in 说明 column | `VIDEO_DIR` |
| Whisper CLI path | `项目路径` table or env var block → `whisper-cli.exe` | `WHISPER_CLI` |
| Whisper main model | `项目路径` table or env var block → `ggml-kotoba-whisper` | `WHISPER_MODEL` |
| Whisper retry model | `项目路径` table or env var block → `ggml-large-v3` | `WHISPER_RETRY_MODEL` |

If any value can't be found, ask the user before running.

### Step 2: Set environment variables

```bash
export PYTHONPATH="C:/Users/54238/.claude/skills/subtitle-proofread/scripts"
export WHISPER_CLI='<value from CLAUDE.md>'
export WHISPER_MODEL='<value from CLAUDE.md>'
export WHISPER_RETRY_MODEL='<value from CLAUDE.md>'
```

`PYTHONPATH` is fixed — never change it. The other three come from CLAUDE.md.

### Step 3: Run the orchestrator

```bash
cd "<project-root>" && PYTHONIOENCODING=utf-8 python \
  "C:/Users/54238/.claude/skills/subtitle-proofread/scripts/run_all.py" \
  --lang ja --video-dir "<VIDEO_DIR>" [user's flags]
```

Always pass `--video-dir` for audio-mode projects (no reference subtitles).
Without it, Whisper can't extract audio and the fix layer prints nothing and
returns 0 fixes — a silent failure.

### Step 4: Add user's flags

> ⚠️ **Do NOT add `--apply-ai-review` or `--apply-checklist` to a full run.**
> These are standalone fast paths (see Step 6 below) — they skip the entire
> pipeline and only apply already-completed review results. Combining them
> with a full run causes the pipeline to exit immediately after apply + clean.

| User said | Add this flag |
|-----------|---------------|
| "试运行" / "dry run" / "preview" | `--dry-run` (scan only, no file changes) |
| "第N到M集" / "EP005-EP010" | `-e EP005-EP010` |
| "前N集" / "first N" | `--limit N` |
| "从第N集开始" | `--start-from EP0NN` |
| "跳过Whisper" | `--skip-whisper` |
| "断点续跑" | `--resume` |

### Step 5: Interpret the output

| Output | Meaning |
|--------|---------|
| `L4 ✅` | Episode fully processed — all layers passed |
| `[STILL]` entries | Cues Whisper couldn't fix → need L6 human review |
| `[ai-review]` entries | Cues too long for short-fragment but AI-fixable → L2.5 AI context completion |
| `Fixed: N` (N > 0) | Whisper successfully fixed N cues |
| `Done: 0 fixed, 0 AI review, 0 unfixable` + no `[whisper]` messages | **Whisper didn't run.** Check `--video-dir` points to a directory with `.mkv` files |
| `[whisper] EP00N: No video file found` | Video directory is wrong or empty |
| `Pipeline complete — all layers passed` | Orchestrator ran all layers without crashing |

### Step 5.5: Auto AI review (do NOT skip — part of the pipeline)

If the pipeline prints `[ai-review] N pending` entries (non-zero), Claude MUST
immediately handle them:

1. **Read** each `reports/manual-review/{EP}/ai_review.md`
2. **For each ⬜ item**, read the context (上文/下文) and the Whisper attempt,
   then infer the correct Japanese text. Fill in the `修正:` field.
   - Trust context over Whisper — Whisper often drops or garbles words.
   - If the original text looks correct except for a loanword (e.g. "OK"),
     keep it as-is — the scanner flags Latin but common loanwords are fine.
   - If truly uncertain, leave `修正:` blank and it stays ⬜ for L6 human.
3. **Run** `python run_all.py --lang <lang> --apply-ai-review` to apply.

This is the "Claude AI" in L2.5 — do it inline, don't wait for the user to ask.

### Step 6: Post-pipeline fast paths (standalone — do NOT combine with full run)

These flags run a **single action** and exit. They do NOT scan, run Whisper,
or process anything new. Use them only AFTER a full pipeline run + human/AI review.

| Flag | What it does | When to use |
|------|-------------|-------------|
| `--apply-ai-review` | Apply JSON fixes + `reports/manual-review/{EP}/ai_review.md` → SRT + clean | After Claude fills AI fragment completions |
| `--apply-checklist` | Apply `reports/manual-review/{EP}/checklist.md` → SRT | After human fills in corrections in checklist |

```bash
# After Claude fills AI review checklists:
python run_all.py --lang ja --apply-ai-review

# After human fills checklist corrections:
python run_all.py --lang ja --apply-checklist --video-dir "<VIDEO_DIR>"
```

## Layers (reference — read only if debugging)

| Layer | What it does |
|-------|-------------|
| L1 `scan/unified_scanner.py` | One-pass scan: garbled chars, repeats, term harvest |
| L2 `fix/fix_orchestrator.py` | Cascading fix: reference → Whisper → triage |
| L2.5 `run_all.py:step_ai_review` | AI context completion for fragments with Japanese semantics + Latin garbled |
| L3 `nouns/noun_checker.py` + `auto_classify.py` | Proper noun unification |
| L3.5 (Claude) | AI judgment on borderline proper nouns |
| L4 `apply/apply_fixes.py` | Batch apply all accumulated fixes |
| L5 (skipped for SRT) | ASS format repair |
| L6 (human) | Review checklist with video clips + VAD alignment |

## Single-layer debugging

Run individual layers when a single step fails. All scripts live under
`C:/Users/54238/.claude/skills/subtitle-proofread/scripts/`.

```bash
PROJ="<project-root>"
SCRIPTS="C:/Users/54238/.claude/skills/subtitle-proofread/scripts"

# L1: Scan all SRTs
cd "$PROJ" && python "$SCRIPTS/scan/unified_scanner.py" \
  --target-dir AI审查后/ --output-findings temp/scans/findings.json --project-lang ja

# L2: Whisper-fix one episode
cd "$PROJ" && python "$SCRIPTS/fix/fix_orchestrator.py" EP005 --step whisper

# L2: Check if episode is clean
cd "$PROJ" && python "$SCRIPTS/fix/fix_orchestrator.py" EP005 --step check

# L2.5: Generate AI review checklist (context-only, no video)
cd "$PROJ" && python -c "
from fix.fix_orchestrator import Fixer
Fixer('EP005', '$PROJ').review_ai()
"

# L6: Generate human review checklist
cd "$PROJ" && python "$SCRIPTS/fix/fix_orchestrator.py" EP005 --step review

# L3: Build proper noun glossary
cd "$PROJ" && python "$SCRIPTS/nouns/build_glossary.py" \
  --findings temp/scans/findings.json -o reports/proper-nouns.md --lang ja

# L3.1: Auto-clean glossary (dry-run first, then --apply)
cd "$PROJ" && python "$SCRIPTS/nouns/auto_clean_glossary.py" \
  --glossary reports/proper-nouns.md
cd "$PROJ" && python "$SCRIPTS/nouns/auto_clean_glossary.py" \
  --glossary reports/proper-nouns.md --apply

# L3.3: Scan SRTs for proper noun variants
cd "$PROJ" && python "$SCRIPTS/nouns/noun_checker.py" AI审查后/ --lang ja --oped

# L4: Apply accumulated fixes
cd "$PROJ" && python "$SCRIPTS/apply/apply_fixes.py" \
  --target-dir AI审查后/ --fixes temp/scans/all_fixes.json --lang ja

# Report summary
cd "$PROJ" && python "$SCRIPTS/utils/update_report.py" \
  reports/问题解决报告.md --summary
```

## L3 glossary maintenance

The proper-noun glossary is built once, AI-reviewed once, then reused.

```
L3.0  build_glossary.py      → aggressive JMdict filter → raw glossary
L3.1  auto_clean_glossary.py → heuristic prune
L3.2  Claude AI review       → semantic judgment on low-frequency survivors
```

**L3.2 trigger**: user says "审查专名表". Claude reads `reports/proper-nouns.md`,
judges kanji compounds with freq ≤ 8, appends common nouns to `COMMON_KANJI`.

## Git guardrail

The orchestrator commits before modifying SRTs. When running a single-layer
script by hand:

```bash
cd "<project-root>" && git add -A && git commit -m "备份：<what>"
```
