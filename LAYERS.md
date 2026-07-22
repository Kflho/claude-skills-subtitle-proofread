# Reference & Debugging — Subtitle Proofread

All scripts under `C:/Users/54238/.claude/skills/subtitle-proofread/scripts/`.

Env prefix:
```bash
PROJ="<project-root>"
SCRIPTS="C:/Users/54238/.claude/skills/subtitle-proofread/scripts"
export PYTHONPATH="$SCRIPTS"
```

---

## Phase 1: Scan

`scan/unified_scanner.py` — single scan of all SRTs: garbled chars, repeat patterns, term frequency.

```bash
cd "$PROJ" && python "$SCRIPTS/scan/unified_scanner.py" \
  --target-dir AI审查后/ --output-findings temp/scans/findings.json --project-lang ja
```

Output:
- `temp/scans/findings.json` — full scan results
- `temp/scans/issues/` — per-episode issue details
- `reports/proper-nouns.md` — glossary (with `--build-glossary`)

Scan is read-only. No fixes applied, no report entries written.

---

## Phase 2: Triage (Whisper → Classify → Fix)

`fix/fix_orchestrator.py` — cascade: VAD → Whisper → triage → human.

### Triage logic

Post-Whisper, each fix is classified by `fix_by_whisper()`:

```
eval_text = Whisper output (or original if Whisper failed)

① meaningful_jp_count(eval_text) < 2  → auto-cut (pure Latin, bare exclamations)
② looks_like_plausible_japanese()      → auto-keep (write SRT ✅)
③ rest (has JP + Latin corruption)     → AI fragment completion
```

AI-unfixable entries go through VAD check:
- No speech → auto-cut
- Has speech → escalate to human review

### Standalone commands

```bash
# Whisper fix for one episode
cd "$PROJ" && python "$SCRIPTS/fix/fix_orchestrator.py" EP005 --step whisper

# Check if episode is clean
cd "$PROJ" && python "$SCRIPTS/fix/fix_orchestrator.py" EP005 --step check
```

Requires env vars: `WHISPER_CLI`, `WHISPER_MODEL`, `WHISPER_RETRY_MODEL`.

---

## Phase 3: Proper Noun Unification

### OP/ED Fixer

`fix/oped_fixer.py` — cross-episode OP/ED detection, classification, and fix generation.

```bash
# Auto-clean instrumental OP/ED (Whisper hallucinations → [音楽])
cd "$PROJ" && python "$SCRIPTS/fix/oped_fixer.py" AI审查后/ --lang ja \
  --auto-only -o temp/scans/oped_fixes.json

# Full: auto-clean + generate AI review for vocal OP/ED lyrics
cd "$PROJ" && python "$SCRIPTS/fix/oped_fixer.py" AI审查后/ --lang ja \
  -o temp/scans/oped_fixes.json --ai-review temp/scans/oped_ai_review.json

# Apply AI-reviewed OP/ED fixes
cd "$PROJ" && python "$SCRIPTS/fix/oped_fixer.py" AI审查后/ --lang ja \
  -o temp/scans/oped_fixes.json --apply-ai-review temp/scans/oped_ai_review.json
```

How it works:
1. Collects cues in OP (0-95s) and ED (last 120s) regions from all episodes
2. Clusters by time position (±2s tolerance) across episodes
3. Classifies each cluster:
   - **Instrumental**: ≥80% noise text → auto-clean (replace with [音楽])
   - **Vocal**: meaningful JP with variants → AI review candidates
   - **Dialogue**: unique text per episode → skip (false-positive prevention)
4. For vocal OP/ED: generates `oped_ai_review.json` → AI fills canonical → apply

### Glossary Maintenance Cycle

```
build_glossary.py      → aggressive JMdict filter → raw glossary
auto_clean_glossary.py → heuristic prune (all 3 sections)
Claude AI review       → semantic judgment on borderline survivors
       ↓
       Rebuild: python build_glossary.py ... → clean glossary
       ↓
noun_checker.py        → scan SRTs for variants
auto_classify.py       → deterministic accept/reject/needs_ai
Claude AI judgment     → decide remaining unknowns → ai_review_fixes.json
```

### Build Glossary

`nouns/build_glossary.py` — corpus frequency → proper noun table.

```bash
cd "$PROJ" && python "$SCRIPTS/nouns/build_glossary.py" \
  --findings temp/scans/findings.json -o reports/proper-nouns.md --lang ja
```

Optional: `--ai-nouns temp/scans/ai_nouns.json` to merge AI-searched nouns.

### Auto-clean Glossary

`nouns/auto_clean_glossary.py` — heuristic pruning, removes obvious non-proper-nouns.

```bash
# Dry run
cd "$PROJ" && python "$SCRIPTS/nouns/auto_clean_glossary.py" \
  --glossary reports/proper-nouns.md

# Apply (edits japanese_utils.py)
cd "$PROJ" && python "$SCRIPTS/nouns/auto_clean_glossary.py" \
  --glossary reports/proper-nouns.md --apply --yes
```

### Noun Checker

`nouns/noun_checker.py` — scan SRTs for proper noun variants.

```bash
# Against noun table
cd "$PROJ" && python "$SCRIPTS/nouns/noun_checker.py" AI审查后/ --lang ja \
  --noun-table reports/proper-nouns.md -o temp/scans/nouns/
```

### Auto Classify

`nouns/auto_classify.py` — Jamdict + rules pre-classification, reduces AI review volume.

```bash
cd "$PROJ" && python "$SCRIPTS/nouns/auto_classify.py" \
  --candidates temp/scans/noun_candidates.json --lang ja \
  --output temp/scans/noun_classified.json
```

### Apply Fixes

`apply/apply_fixes.py` — collect all fix sources, batch-write SRT.

```bash
cd "$PROJ" && python "$SCRIPTS/apply/apply_fixes.py" \
  --target-dir AI审查后/ --fixes temp/scans/all_fixes.json --lang ja
```

### Human Review Delivery

```bash
cd "$PROJ" && python "$SCRIPTS/fix/fix_orchestrator.py" EP005 --step review
```

Output: `reports/manual-review/EP005/checklist.md` + `.mp4` clips.

Fill `修正:` → `python run_all.py --lang ja --apply-checklist --video-dir "<VIDEO_DIR>"`

---

## ASS Format Repair

`ass/ass_repair.py` — ASS-only (SRT projects skip).

```bash
cd "$PROJ" && python "$SCRIPTS/ass/ass_repair.py" \
  --target-dir AI审查后/ --check all
```

---

## Report Summary

```bash
cd "$PROJ" && python "$SCRIPTS/utils/update_report.py" \
  reports/问题解决报告.md --summary
```
