# AI Intervention Points — Subtitle Proofread

Each 🤖 point: trigger condition, procedure, judgment rules.

---

## Phase 2: AI Fragment Completion

**Trigger**: Pipeline output `[ai-review] N pending`

**Data**: `reports/manual-review/{EP}/ai_review.md`

**Flow**:
1. Read each EP's `ai_review.md`
2. For each ⬜ entry, read surrounding context and Whisper attempt text
3. Infer correct Japanese, fill `修正:` field
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

## L3.1: Auto-clean Glossary

**Trigger**: L1 scan rebuilt `reports/proper-nouns.md` — runs automatically

**Flow**:
```bash
# 1. Dry run
cd "<project>" && PYTHONPATH="<scripts>" python \
  "<scripts>/nouns/auto_clean_glossary.py" \
  --glossary reports/proper-nouns.md --lang ja

# 2. If >0 suggestions → apply + rebuild
cd "<project>" && PYTHONPATH="<scripts>" python \
  "<scripts>/nouns/auto_clean_glossary.py" \
  --glossary reports/proper-nouns.md --lang ja --apply --yes

cd "<project>" && PYTHONPATH="<scripts>" python \
  "<scripts>/nouns/build_glossary.py" \
  --findings temp/scans/findings.json -o reports/proper-nouns.md --lang ja
```

**auto_clean filters**:
- Katakana: onomatopoeia (ワンワン), laughter (ハッハー), daily words (パパママ), fragments (ネルギー)
- Kanji: verb stems, time/number fragments, modifier fragments, pronoun fragments
- Keeps: real names (アーサー), places, honorifics, known anime terms

---

## L3.2: AI Glossary Review

**Trigger**: After auto_clean — review borderline glossary entries

**Flow**:
1. Read `reports/proper-nouns.md` all three columns
2. Scan low-frequency/suspicious entries:
   - Katakana: real character name or onomatopoeia/daily word?
   - Kanji: proper noun or verb fragment?
3. Judge as common word → add to `lib/japanese_utils.py` COMMON_KATAKANA or COMMON_KANJI
4. Rebuild glossary

**Reference**: 鉄腕アトム (1963) character knowledge, Japanese name patterns (surname+given, honorifics), whether a word makes sense as a name vs. common noun.

**Apply** (same as L3.1): Edit `japanese_utils.py` → re-run `build_glossary.py`

---

## L3.5: AI Proper Noun Judgment

**Trigger**: Pipeline output `AI REVIEW NEEDED: N proper noun candidates`
(auto_classify handled the rest; these are the borderline cases)

**Data**: `temp/scans/ai_review_candidates.json`

**Flow**:
1. Read candidate list
2. Judge each: proper noun? yes/no
3. Yes → give canonical form
4. No → mark excluded
5. Output to `temp/scans/ai_review_fixes.json`:
   ```json
   [{"action":"replace_global","original":"候補","replacement":"規範形"}, ...]
   ```
6. Re-run: `python run_all.py --lang ja --resume`

**If auto_classify handled everything (Needs AI: 0) → skip this step.**

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
