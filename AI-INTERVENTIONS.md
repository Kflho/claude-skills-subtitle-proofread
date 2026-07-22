# AI Intervention Points — Subtitle Proofread

Each 🤖 point: trigger condition, procedure, judgment rules.
Organized by the 3-Phase pipeline.

---

## First Run Troubleshooting

初次运行或代码变更后可能出现的问题，**必须立即修复，不要跳过**。

### SyntaxError（Python 语法错误）

```
SyntaxError: closing parenthesis ')' does not match opening parenthesis '{'
```

**立即修**。通常是 frozenset/list/dict 括号不匹配。修完 `git commit`，重跑。

### UnicodeEncodeError（GBK 编码）

```
UnicodeEncodeError: 'gbk' codec can't encode character '✅'
```

**立即修**。把 emoji（✅⚠️✨⟳）替换为 ASCII（`[OK]` `[WARN]` `[*]`）。Windows 控制台默认 GBK 不支持 emoji。

### 缓存污染

如果某步骤失败但已写入了中间文件（如 `findings.json`），后续重跑会跳过该步骤。
→ 清空 `temp/` + `reports/`，加 `--force-rescan` 重跑。

---

## Phase 2: AI Fragment Completion

**Trigger**: Pipeline output `[ai-review] N pending`

**Data**: `temp/scans/ai_fragments_{EP}.json`

**Flow**:
1. Read each EP's fragment JSON
2. For each fragment, read surrounding context and Whisper attempt
3. Infer correct Japanese, fill `"correction"` field
4. Can't determine → leave blank (→ VAD escalation)

**Paired mode** (`"mode": "paired"`):
Fragment 带有 `paired_cues` 数组，包含目标 cue 和相邻 cue。
AI **可以修改任意一句**（或两者）以使整体通顺：

- 填 `fragment.correction` → **必须与 target cue 的 `paired_cues[*].correction` 一致**
- 填 `paired_cues[*].correction`（target cue）→ **必须填，且与 `fragment.correction` 相同**
- 填 `paired_cues[*].correction`（neighbor cue）→ 修改邻居 cue
- 填 `"__DELETE__"` → 删除该 cue
- 留空 → 保持不变
- **规则**：target cue 的 correction 必须同时出现在 `fragment.correction` 和 `paired_cues[target].correction` 两处

**示例**（EP022）：
```json
{
  "mode": "paired",
  "paired_cues": [
    {"start": "00:05:43.130", "text": "の手紙なんだ", "role": "neighbor", "correction": "__DELETE__"},
    {"start": "00:05:48.360", "text": "ハーフィーが回転に...", "role": "target", "correction": "入ってるんだ"}
  ],
  "correction": "入ってるんだ"
}
```
→ 删除碎片邻居，修复目标 cue。apply 时输出 `2 corrections applied`。

**Judgment rules**:
- Trust context, don't trust Whisper (Whisper often drops/corrupts words)
- Original is correct but contains Latin letters (e.g. "OK") → keep as-is
- **原文纯拉丁/单音节（mj < 2）且 Whisper 输出可读** → 直接填 Whisper 输出（噪声→改善）
- **邻居是明显碎片**（如 `の手紙なんだ` 是前句的尾巴）→ `__DELETE__`
- Uncertain → leave blank

**Apply**:
```bash
python run_all.py --lang ja --apply-ai-review --video-dir "<VIDEO_DIR>"
```
> ⚠️ 必须带 `--video-dir`，否则无法为人工审查项提取视频片段。

**After apply — VAD escalation**:
AI 无法修复的 fragment 进入 VAD 检查：
- **原文 mj < 2**（纯拉丁噪声）→ 直接 auto-cut，**不升级人工**
- 无语音 → auto-cut
- 有语音 → 人工审查 checklist

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

### Proper Noun AI Judgment (🤖 triggered, iterative)

**Trigger**: Pipeline output `AI REVIEW NEEDED: N proper noun candidates`
(auto_classify handled the rest; these are the borderline cases)

**Data**: `temp/scans/ai_review_candidates.json` (small file, typically <20 entries)

**⚠️ 这是迭代过程——可能需要多轮。12→6→3→0 是正常收敛。**

**Flow（每轮）**:
1. Read `ai_review_candidates.json` — ONLY this small file, NOT the full glossary
2. Judge each candidate: proper noun? yes/no
3. **Yes** → 提供规范形式，写入 `ai_review_fixes.json`
4. **No** → **立即加入 `lib/japanese_utils.py` COMMON_KANJI**（或 COMMON_KATAKANA，如果候选是片假名）
   - 在 `COMMON_KANJI = frozenset({` 的最后一个条目后追加
   - 格式：`'候補詞',` + 注释
5. Write `ai_review_fixes.json`（仅包含 Yes 的候補）：
   ```json
   [{"action":"replace_global","original":"候補","replacement":"規範形"}, ...]
   ```
   如果全部 No → 写入 `[]`
6. Re-run: `python run_all.py --lang ja --resume`
7. **检查输出**：如果仍有 `AI REVIEW NEEDED: N`（N > 0）→ 回到 Step 1
8. 循环直到 `Needs AI: 0` 或 `auto_classify handled all`

**为什么必须加 COMMON_KANJI**：
`--resume` 重跑 Phase 3 时 noun_checker 重新扫描全部 SRT。
只写 `ai_review_fixes.json` 不够——下一轮会再次检测到同一个候选。
必须把拒绝词加入 COMMON_KANJI 才能从根源消除。

**示例（EP001 试运行，3 轮收敛）**：
```
Round 1: AI REVIEW NEEDED: 12
  → 判断 12 个全是动词碎片 → 加入 COMMON_KANJI → --resume
Round 2: AI REVIEW NEEDED: 6
  → 判断 6 个都是语法碎片 → 加入 COMMON_KANJI → --resume
Round 3: AI REVIEW NEEDED: 3
  → 判断 3 个都是动词词干 → 加入 COMMON_KANJI → --resume
Round 4: [AI review] 6607 candidates → auto_classify handled all. Nothing needs AI review.
  → ✅ 收敛完成
```

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
