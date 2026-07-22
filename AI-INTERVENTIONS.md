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

**立即修**。通常是 frozenset/list/dict 括号不匹配。修完重跑。

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

**Context fields** (v2 — multi-layer context for better AI inference):

```json
{
  "episode": "EP031",
  "episode_title": "黒い宇宙線",
  "fragments": [{
    "original": "ip で起こったものであります",
    "whisper_attempt": "今度も東日本街で...",
    "context_before": ["6 cues, including garbled neighbors"],
    "context_after":  ["6 cues, including garbled neighbors"],
    "whisper_context": [
      {"start_s": 605.1, "text": "Whisper transcript ±30s window"}
    ],
    "reference_text": "これはIPで起こった…",
    "correction": ""
  }]
}
```

| 字段 | 来源 | 用途 |
|------|------|------|
| `context_before/after` (6 cues) | SRT 相邻 cue | 对话流上下文，即使邻居也有乱码仍提供部分信息 |
| `whisper_context` (±30s) | Whisper Tier 2 全转录 | **即使 `whisper_attempt` 为 null**，周围 Whisper 段仍提供声学参考 |
| `reference_text` | 参考字幕/ (原文，不翻译) | **AI 直接读原文**，结合上下文翻译+纠错一步到位。无需机器翻译 |
| `episode_title` | 视频文件名 | 故事场景线索（e.g. "黒い宇宙線" → 宇宙/科学相关台词） |

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
- **有 `reference_text`** → 参考字幕原文（不翻译），AI 读原文 + 上下文 → 直接写日文 correction。参考字幕可能有错，需结合上下文判断
- Original is correct but contains Latin letters (e.g. "OK") → keep as-is
- **原文纯拉丁/单音节（mj < 2）且 Whisper 输出可读** → 直接填 Whisper 输出（噪声→改善）
- **邻居是明显碎片**（如 `の手紙なんだ` 是前句的尾巴）→ `__DELETE__`
- **`whisper_attempt` 为 null 但 `whisper_context` 有内容** → 用 `whisper_context` 的周围段推断：看 Whisper 在该时段说了什么，结合 `episode_title` 的场景线索，综合判断正确台词
- **`episode_title` 线索** → 帮助缩小语义范围（e.g. 标题含"宇宙"时，"星"更可能是"宇宙"而非"明星"）
- Uncertain → leave blank

**Apply**:
```bash
python run_all.py --apply-ai-review --video-dir "<VIDEO_DIR>"
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
6. Re-run: `python run_all.py --resume`
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

## Phase 3: OP/ED Lyric Unification (🤖 triggered, vocal OP/ED only)

**Resource priority**（匹配整体字幕逻辑）：

```
Tier 1: 有源语言字幕 → --reference <dir> → 自动填入 canonical，AI 验证
Tier 2: 有音频(Whisper) → 跨集对比 → AI 审查变体
Tier 3: 什么都没有 → 跨集对比 → AI 审查（当前默认）
底限:   人工审查
```

> OP/ED **不需要百度翻译**。任务是文本统一（找规范形式），不是翻译。
> AI 对比跨集变体 + 引用字幕即可判断规范形式。

**Trigger**: Pipeline prints `[oped] AI review candidates → temp/scans/oped_ai_review.json`
with `vocal_clusters > 0`.

**Not triggered when**: Project has instrumental-only OP/ED (like 鉄腕アトム 1963).
The auto-clean path handles that silently — no AI intervention needed.

**With reference subtitles** (`--reference <dir>`):
```
[oped] Reference: 183 cues → 51 unique time positions from <dir>
```
- `canonical` 自动从引用填入（`auto_canonical_from_reference: N`）
- AI 只需验证：引用文本是否正确？不对则覆盖 canonical
- 支持 ASS/SRT 格式，任何语言（俄语/日语/中文均可）

**Data**: `temp/scans/oped_ai_review.json`

```json
{
  "description": "OP/ED AI Review Candidates — vocal OP/ED lyric variants across episodes.",
  "total_groups": 5,
  "op_groups": 3,
  "ed_groups": 2,
  "candidates": [{
    "region": "OP",
    "time_position_s": 6.1,
    "episode_count": 50,
    "variants": {"歌詞の一部": 30, "歌詞が違う": 12},
    "noise_variants": {"me": 8},
    "suggested_canonical": "歌詞の一部",
    "suggested_confidence": 0.6,
    "canonical": "",
    "sample_times": [{"ep": "...", "start": "00:00:06.100", "text": "歌詞の一部"}]
  }]
}
```

**Flow**:
1. Pipeline auto-detects vocal OP/ED via cross-episode text similarity
2. Instrumental-only regions are auto-cleaned (→ [音楽]) — no AI needed
3. For vocal regions with text variants, `oped_ai_review.json` is generated
4. Read the file, fill `"canonical"` for each candidate group
5. Set `"canonical": "__INSTRUMENTAL__"` if AI determines it's actually instrumental
6. Leave `"canonical": ""` to skip (no fix applied)

**Judgment rules**:
- `suggested_canonical` is majority-vote — override if wrong
- Reference `variants` (meaningful JP) vs `noise_variants` (Whisper hallucinations)
- If all variants look like noise → set `"__INSTRUMENTAL__"`
- If uncertain → leave blank (keeps current text)
- External knowledge of the song lyrics is valid reference
- The original SRT text and Whisper output can both be referenced

**How to fill canonical（重要 — 避免 0 fixes 陷阱）**:

⚠️ **不要手工重写整个 JSON 文件。** 人工重写极易丢失 `sample_times` 字段，
导致 `--apply-ai-review` 生成 0 条修复。

正确做法：只修改每个 candidate 的 `"canonical"` 字段：

```python
# 方式1：用 Python 脚本填（推荐）
python -c "
import json
with open('temp/scans/oped_ai_review.json', 'r+', encoding='utf-8') as f:
    data = json.load(f)
    for c in data['candidates']:
        t = c['time_position_s']
        # AI 判断逻辑在这里...
        if t == 0.0: c['canonical'] = '正しい歌詞'
        elif t == 3.2: c['canonical'] = '正しい歌詞2'
        # 不确定 → 留空或跳过
    f.seek(0); f.truncate()
    json.dump(data, f, ensure_ascii=False, indent=2)
"
```

```bash
# 方式2：用 Edit 工具只改 canonical 行（保留其他字段不动）
```

**Apply**:
```bash
python oped_fixer.py AI审查后/ -o temp/scans/oped_fixes.json \
    --apply-ai-review temp/scans/oped_ai_review.json
```
Then re-run `--apply-ai-review` in the full pipeline to apply all fixes.

**Token efficiency**: Candidates file is small (typically 3-10 groups for vocal OP/ED).
Each group has only variant texts (short lyric lines), not full SRT content.

**Known limitations**:
- SRT only（ASS 需先转换，见下方）
- `--min-episodes` 默认 3，测试时可用 `--min-episodes 2`
- 中文项目可用 `--lang zh` 手动覆盖（自动检测默认正确）

### ASS → SRT 快速转换（OP/ED 审查用）

```bash
# 从 ASS 提取 OP/ED 行生成临时 SRT（仅用于 oped_fixer 审查）
python -c "
import re, os
os.makedirs('temp/oped_srt', exist_ok=True)
for fname in os.listdir('中文字幕范例/'):
    if not fname.endswith('.ass'): continue
    with open(f'中文字幕范例/{fname}', 'r', encoding='utf-8') as f:
        lines = [l for l in f if l.startswith('Dialogue:') and 'Opening' in l]
    ep = re.search(r'(\d+)', fname).group(1)
    with open(f'temp/oped_srt/EP{ep}.srt', 'w', encoding='utf-8') as out:
        for i, line in enumerate(lines, 1):
            p = line.split(',', 9)
            start, end, text = p[1], p[2], re.sub(r'\{[^}]*\}', '', p[9]).replace('\\\\N', ' ').strip()
            def tc(t):  # ASS centiseconds → SRT milliseconds
                h,m,s = t.split(':'); sec,cs = s.split('.')
                return f'{int(h):02d}:{int(m):02d}:{int(sec):02d},{int(cs)*10:03d}'
            out.write(f'{i}\n{tc(start)} --> {tc(end)}\n{text}\n\n')
    print(f'{fname} -> temp/oped_srt/EP{ep}.srt')
"
# 然后跑 oped_fixer
python fix/oped_fixer.py temp/oped_srt/ -o temp/scans/oped_fixes.json \
    --ai-review temp/scans/oped_ai_review.json --min-episodes 2
```

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
     --findings temp/scans/findings.json -o reports/proper-nouns.md \
     --ai-nouns temp/scans/ai_nouns.json
   ```
   AI-sourced nouns bypass min_freq=3 threshold, marked `[AI]`.
