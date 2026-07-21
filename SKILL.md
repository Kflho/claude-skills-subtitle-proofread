---
name: subtitle-proofread
description: >
  Subtitle proofreading pipeline — 6-layer, one-pass. Use when the user wants to
  proofread, scan, or fix subtitles (SRT/ASS), run Whisper ASR correction, unify
  proper nouns, apply batch fixes, or generate human-review checklists. Covers:
  字幕, subtitle, SRT, ASS, proofread, 校对, Whisper, 专有名词, captions.
---

# Subtitle Proofread

## Pipeline flow (AI intervention points marked)

```
L1   scan            → findings.json + proper-nouns.md
L2   fix (Whisper)   → SRT repaired
L2.5 🤖 AI fragment  → context-based completion (Step 5.5)
L3   noun check      → OP/ED + noun_table → candidates
L3.1 script auto_clean → prune obvious non-nouns from glossary
L3.2 🤖 AI glossary review → judge borderline entries (Step 5.7)
L3.5 🤖 AI noun judgment   → decide unknown candidates (Step 5.9)
L4   apply           → batch-apply all fixes
L5   ASS repair      → (SRT: skipped)
L6   deliver         → human checklist + video clips
L6.5 🤖 AI pre-review → try to fix L6 items before human (Step 5.11)
```

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
| `[AI review] N candidates → auto_classify handled all` | L3 noun candidates processed by script — check if any need AI |

### Step 5.5: 🤖 L2.5 AI fragment completion (do NOT skip — part of the pipeline)

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

### Step 5.6: 🤖 L3.1 Auto-clean glossary (auto-triggered after scan)

After L1 scan rebuilds `reports/proper-nouns.md`, the pipeline runs L3 noun
check. Before that, Claude MUST run auto_clean to prune the newly-built glossary:

```bash
cd "<project-root>" && PYTHONPATH="<scripts>" python \
  "<scripts>/nouns/auto_clean_glossary.py" \
  --glossary reports/proper-nouns.md --lang ja
```

If it finds >0 suggestions, apply with `--apply --yes`:

```bash
# Apply + rebuild clean glossary in one go:
cd "<project-root>" && PYTHONPATH="<scripts>" python \
  "<scripts>/nouns/auto_clean_glossary.py" \
  --glossary reports/proper-nouns.md --lang ja --apply --yes
```

Then rebuild the glossary from findings:
```bash
cd "<project-root>" && PYTHONPATH="<scripts>" python \
  "<scripts>/nouns/build_glossary.py" \
  --findings temp/scans/findings.json -o reports/proper-nouns.md --lang ja
```

This ensures the glossary is clean before L3 noun_checker runs against it.

### Step 5.7: 🤖 L3.2 AI glossary review (auto-triggered after auto_clean)

After auto_clean, Claude MUST review the glossary for borderline entries that
the script couldn't decide:

1. **Read** `reports/proper-nouns.md`
2. **Scan** all three sections (角色名, 汉字复合词, 其他片假名术语)
3. **Judge** entries that look borderline:
   - Katakana: is it a real character name or a sound effect/common word?
   - Kanji: is it a real proper noun or a verb fragment?
4. **If borderline entries found**: add them to `COMMON_KATAKANA` or
   `COMMON_KANJI` in `lib/japanese_utils.py`, then re-run build_glossary.
5. **If glossary looks clean**: note "glossary clean" and move on.

Context you can use for judgment:
- Astro Boy (1963) character lists from memory
- Common Japanese name patterns (surname + given name, honorifics)
- Whether a word makes sense as a name vs. a common noun

### Step 5.8: 🤖 AI web search for proper nouns (optional, user-triggered)

If the user says "网上搜索专有名词" or "search for character names":

1. **Search** for "鉄腕アトム 1963 キャラクター 一覧" or similar
2. **Extract** character names, place names, organization names
3. **Save** to `temp/scans/ai_nouns.json`:
   ```json
   {"characters": ["アトム", "ウラン", ...], "places": [...], "organizations": [...]}
   ```
4. **Re-run** glossary build with `--ai-nouns temp/scans/ai_nouns.json`:
   ```bash
   cd "<project-root>" && PYTHONPATH="<scripts>" python \
     "<scripts>/nouns/build_glossary.py" \
     --findings temp/scans/findings.json -o reports/proper-nouns.md --lang ja \
     --ai-nouns temp/scans/ai_nouns.json
   ```
   AI-sourced names bypass the min_freq=3 threshold and are marked `[AI]`.

### Step 5.9: 🤖 L3.5 AI proper noun judgment (auto-triggered after noun_checker)

After L3 noun_checker runs, the pipeline prints an AI review notice with counts.
If it says "AI REVIEW NEEDED: N proper noun candidates":

1. **Read** `temp/scans/ai_review_candidates.json`
2. **For each candidate**, judge whether it's a proper noun:
   - Is it a character name? Place name? Organization? Anime-specific term?
   - Is it a common word/phrase/fragment that should be rejected?
3. **Output** decisions to `temp/scans/ai_review_fixes.json`:
   ```json
   [
     {"action": "replace_global", "original": "候補", "replacement": "規範形"},
     ...
   ]
   ```
4. **Re-run** `python run_all.py --lang ja --resume` to apply.

If auto_classify handled all candidates (Needs AI: 0), skip this step.

### Step 5.10: 🤖 L6 AI pre-review (auto-triggered after deliver)

After L6 delivers human review checklists, Claude MUST try to resolve items
before asking the human:

1. **Check** `reports/manual-review/` for any `EP*/checklist.md` files
2. **Read** each checklist
3. **For each ⬜ item**:
   - Read the 上文/下文 context from the checklist
   - Can you infer the correct text WITHOUT watching the video?
   - If YES → fill in `修正:` field
   - If NO (truly need audio/video to decide) → leave blank for human
4. **Apply** fixable items: `python run_all.py --lang ja --apply-checklist --video-dir "<VIDEO_DIR>"`
5. **Report** how many items were AI-fixed vs. still need human review

**Decision criteria for "AI can fix":**
- Context (上文+下文) clearly shows what the garbled text should be
- The current SRT text (`残留`) is already correct Japanese (just confirm it)
- Common Japanese phrases where garbling is obvious from context
- **Leave for human if**: the audio is essential (e.g., two similar-sounding
  words both make sense, or it's a name you can't verify)

### Step 6: Post-pipeline fast paths (standalone — do NOT combine with full run)

These flags run a **single action** and exit. They do NOT scan, run Whisper,
or process anything new. Use them only AFTER a full pipeline run + human/AI review.

| Flag | What it does | When to use |
|------|-------------|-------------|
| `--apply-ai-review` | Apply JSON fixes + `reports/manual-review/{EP}/ai_review.md` → SRT + clean | After Claude fills AI fragment completions |
| `--apply-checklist` | Apply `reports/manual-review/{EP}/checklist.md` → SRT | After Claude/human fills in corrections in checklist |

```bash
# After Claude fills AI review checklists:
python run_all.py --lang ja --apply-ai-review

# After Claude/human fills checklist corrections:
python run_all.py --lang ja --apply-checklist --video-dir "<VIDEO_DIR>"
```

## Layers (reference — read only if debugging)

| Layer | What it does | AI介入 |
|-------|-------------|--------|
| L1 `scan/unified_scanner.py` | One-pass scan: garbled chars, repeats, term harvest | — |
| L2 `fix/fix_orchestrator.py` | Cascading fix: reference → Whisper → triage | — |
| L2.5 `run_all.py:step_ai_review` | AI context completion for fragments | 🤖 Step 5.5 |
| L3.0 `nouns/build_glossary.py` | Build proper-noun glossary from corpus | — |
| L3.1 `nouns/auto_clean_glossary.py` | Heuristic prune of non-nouns | 🤖 Step 5.6 |
| L3.2 Claude glossary review | Judge borderline glossary entries | 🤖 Step 5.7 |
| L3.3 `nouns/noun_checker.py` | Scan SRTs for proper noun variants | — |
| L3.4 `nouns/auto_classify.py` | Deterministic pre-filter before AI | — |
| L3.5 Claude noun judgment | Judge unknown proper noun candidates | 🤖 Step 5.9 |
| L4 `apply/apply_fixes.py` | Batch apply all accumulated fixes | — |
| L5 (skipped for SRT) | ASS format repair | — |
| L6 `fix/fix_orchestrator.py:review` | Human review checklist + video clips | 🤖 Step 5.10 (pre-review) |

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

## Git guardrail

The orchestrator commits before modifying SRTs. When running a single-layer
script by hand:

```bash
cd "<project-root>" && git add -A && git commit -m "备份：<what>"
```
