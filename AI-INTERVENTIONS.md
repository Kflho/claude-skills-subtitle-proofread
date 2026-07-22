# AI Intervention Points — Subtitle Proofread

Each 🤖 point: trigger condition, procedure, judgment rules.
Organized by the 3-Phase pipeline.

---

## Phase 2: AI Fragment Completion

**Trigger**: Pipeline output `[ai-review] N pending`

**Data**: `temp/scans/ai_fragments_{EP}.json`

**Flow**:
1. Read each EP's fragment JSON
2. For each fragment, read surrounding context and Whisper attempt text
3. Infer correct Japanese, fill `"correction"` field
4. Can't determine → leave blank

**Judgment rules**:
- Trust context, don't trust Whisper (Whisper often drops/corrupts words)
- Original is correct but contains Latin letters (e.g. "OK") → keep as-is
- Uncertain → leave blank

**Apply**:
```bash
python run_all.py --lang ja --apply-ai-review
```

**After apply — VAD escalation**:
Entries AI couldn't fix are checked against VAD speech data:
- No speech detected → auto-cut (delete from SRT)
- Speech detected → escalate to human review checklist

No manual operation needed. Run `--apply-checklist` after filling human checklists.

---

## Phase 3: Proper Noun Unification

### Glossary Auto-Clean (automatic)

Runs automatically at the end of Phase 1 scan — no manual trigger needed.
`auto_clean_glossary.py --apply --yes` prunes common words from the glossary
and rebuilds it with updated COMMON_KANJI/COMMON_KATAKANA filters.

**What it catches**:
- Katakana: onomatopoeia, laughter, daily words, fragments
- Kanji: verb stems, time/number fragments, modifier fragments, pronoun fragments
- JMdict entries (common dictionary words that aren't proper nouns)
- Keeps: real names, places, honorifics, known Astro Boy terms (see `_ANIME_WHITELIST`)

---

### Glossary AI Review (🤖 triggered, low-token)

**Trigger**: Phase 1 scan prints `[scan] 🤖 Glossary AI Review candidates`

The pipeline saves low-frequency entries (≤5 occurrences) that survived
auto_clean to `temp/scans/glossary_borderline.json`. These are borderline
cases — auto_clean couldn't determine if they're proper nouns or not.

**Token efficiency**: Candidates are printed inline to stderr during the run.
Claude sees them directly — no file read needed. Typical count: 5-20 entries.

**Flow**:
1. Pipeline prints candidates inline (see them in the scan output)
2. Judge each: proper noun or common word?
3. Common word → add to `lib/japanese_utils.py` COMMON_KANJI or COMMON_KATAKANA
4. Proper noun → leave as-is
5. Next `--force-rescan` will rebuild with updated filters

**Judgment rules**:
- Katakana: real character name? or onomatopoeia/daily word?
- Kanji: surname/place/org? or verb fragment/common compound?
- Reference: 鉄腕アトム (1963) character knowledge, Japanese name patterns

**Do NOT read `reports/proper-nouns.md`** — it's 200+ lines. The candidates
are already printed inline by the pipeline.

---

### Noun Variant Detection + Auto-Classify (automatic)

Runs automatically in Phase 3. `noun_checker.py` scans SRTs for proper noun
spelling variants, then `auto_classify.py` uses Jamdict + rules to pre-classify
candidates into:
- **Accepted** → applied automatically (report section: 专名自动应用)
- **Rejected** → logged only
- **Needs AI** → triggers Proper Noun AI Judgment below

---

### Proper Noun AI Judgment (🤖 triggered)

**Trigger**: Pipeline output `AI REVIEW NEEDED: N proper noun candidates`
(auto_classify handled the rest; these are the borderline cases)

**Data**: `temp/scans/ai_review_candidates.json` (small file, typically <20 entries)

**Flow**:
1. Read `ai_review_candidates.json` — ONLY this small file, NOT the full glossary
2. Judge each candidate: proper noun? yes/no
3. Yes → give canonical form
4. No → mark excluded
5. Output to `temp/scans/ai_review_fixes.json`:
   ```json
   [{"action":"replace_global","original":"候補","replacement":"規範形"}, ...]
   ```
6. Re-run: `python run_all.py --lang ja --resume`
   (--resume skips Phase 1+2, only re-runs Phase 3: nouns → apply → deliver)

**If auto_classify handled everything (Needs AI: 0) → skip this step.**

**Token efficiency**: `ai_review_candidates.json` is typically 5-20 entries.
Never read `reports/proper-nouns.md` directly — it's 200+ lines and auto_clean
already handles it automatically.

---

## AI Web Search for Proper Nouns (optional)

**Trigger**: User says "网上搜索专有名词" / "search for character names"

**Flow**:
1. Search "鉄腕アトム 1963 キャラクター 一覧"
2. Extract character names, places, organizations
3. Save to `temp/scans/ai_nouns.json`:
   ```json
   {"characters": ["アトム", "ウラン", ...], "places": [...], "organizations": [...]}
   ```
4. Rebuild glossary with AI data:
   ```bash
   cd "<project>" && PYTHONPATH="<scripts>" python \
     "<scripts>/nouns/build_glossary.py" \
     --findings temp/scans/findings.json -o reports/proper-nouns.md --lang ja \
     --ai-nouns temp/scans/ai_nouns.json
   ```
   AI-sourced nouns bypass min_freq=3 threshold, marked `[AI]`.
