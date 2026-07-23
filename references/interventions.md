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

**Judgment rules** — 按优先级，能用上层就不走下層：

**Tier 1：AI 直接推断（0 开销，优先使用）**

满足以下任一条件，AI 直接从上下文推断 correction，不跑 Whisper：

- **原文可读部分 ≥ 50%**（mj ≥ 3）且乱码只是短前缀/后缀（如 `です書 ii 江戸前は連れてきたかよ` → 去掉前缀即可）
- **Whisper pipeline 已有输出且可读**（whisper_attempt 非 null、非空）→ 直接采纳或微调
- **邻居是明显碎片**（如 `の手紙なんだ` 是前句的尾巴）→ `__DELETE__`
- **原文纯拉丁/单音节**（mj < 2）且 Whisper pipeline 输出可读 → 直接用 Whisper 输出
- **上下文语义清晰**（context_before/after 干净且主题连贯）→ AI 能可靠推断
- 有 `reference_text` → 参考字幕原文 + 上下文 → 直接写日文 correction

**Tier 2：Whisper 逐条重试（~1.5s/条，仅 Tier 1 无法确定时）**

触发条件：Tier 1 走完后仍有 fragment 的 correction 为空，且：
- whisper_attempt 为 null（pipeline 完全失败）
- 或原文大部分不可读（mj < 3）
- 或原文不构成可理解的日语句子

**逐条跑，不合并**（合并音频需复杂 ffmpeg filter，AI 容易写错，不值得为省几秒引入 bug）：
```bash
# 每条 fragment 单独提取+转录（~1.5s/条，模型加载 1.1s + 推理 0.4s）
ffmpeg -y -i "视频.mkv" -ss {start} -to {end} -vn -ac 1 -ar 16000 temp/frag_{N}.wav
"$WHISPER_CLI" -m "$WHISPER_MODEL" -l ja -f temp/frag_{N}.wav --no-timestamps
# 不确定时换备用模型再跑一次
"$WHISPER_CLI" -m "$WHISPER_RETRY_MODEL" -l ja -f temp/frag_{N}.wav --no-timestamps
```

> 典型 episode 3-6 条 fragment → 5-9 秒。Tier 1 已经过滤掉可推断的，到 Tier 2 的通常是真正需要 Whisper 的少数。
> 两个模型都跑不出可读结果 → 升级 Tier 3。

**Tier 3：升级人工（Tier 1+2 均无法确定）**

- Whisper 输出为空或与上下文矛盾
- 对话关键剧情节点，错误代价高
- 多个合理解读无法取舍

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
3. Common word → add to the language-specific utils file:
   - ja: `lib/japanese_utils.py` COMMON_KANJI or COMMON_KATAKANA
   - zh: `lib/chinese_utils.py` COMMON_KANJI
4. Proper noun → leave as-is (or add to PROPER_NOUNS_WHITELIST for jieba false positives)
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
   - **对 --lang zh 项目**：还需确定中文译名。优先使用已知权威译名；不确定时用 WebSearch 搜索 `「{日语专名} 铁臂阿童木 译名」` 或 `「{日语专名} Astro Boy character name」` 找公认译名
   - 找到权威译名 → 填入 `replacement` 字段
   - 搜索无结果 → 根据角色特征自行翻译，标注 `[AI译]`
4. **No** → 加入对应语言的 COMMON_KANJI：
   - ja: 编辑 `lib/japanese_utils.py`（片假名候选 → COMMON_KATAKANA）
   - zh: 编辑 `lib/chinese_utils.py` COMMON_KANJI
   - 在 `frozenset({` 的最后一个条目后追加：`'候補詞',  # 注释`
5. Write `ai_review_fixes.json`（仅包含 Yes 的候補）：
   ```json
   [{"action":"replace_global","original":"候補","replacement":"規範形"}, ...]
   ```
   如果全部 No → 写入 `[]`
6. Re-run: `python run_all.py --resume`
7. **检查输出**：如果仍有 `AI REVIEW NEEDED: N`（N > 0）→ 回到 Step 1
8. 循环直到 `Needs AI: 0` 或 `auto_classify handled all`
9. **收敛后 → 用户确认 + 补充**：向用户展示最终词表（`reports/proper-nouns.md`），列出将被应用的专名条目。逐项询问：
   - **是否采用当前词表？** 确认所有条目无误
   - **有无补充？** 例如：
     - 脚本未检测到的专名（冷门角色、地名、组织名、关键道具）
     - 别名/变体（同一角色的不同称呼、昵称、简称、笔画差异）
     - 译名偏好（如希望"お茶の水博士"译为"茶水博士"而非"御茶之水博士"）
   - 用户补充后 → 手动更新 `proper-nouns.md` 或写入 `ai_review_fixes.json`，再跑 `--resume` 应用
   - 用户确认无补充后 → 进入交付步骤

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
  → 📋 向用户展示最终 proper-nouns.md，询问是否采用。用户确认 → 继续交付
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

**Format support**: `oped_fixer.py` 原生支持 SRT 和 ASS 格式（通过 `parse_subtitles()` 自动检测）。
直接指向包含 `.srt` 或 `.ass` 文件的目录即可，无需手动转换。

**Known limitations**:
- `--min-episodes` 默认 3，测试时可用 `--min-episodes 2`
- 中文项目可用 `--lang zh` 手动覆盖（自动检测默认正确）

---

## Phase 4: AI Polish (--lang zh only, optional)

**Trigger**: Pipeline 末尾交互提问 `是否对最终字幕进行 AI 润色？(y/n)`

**Two paths**:

### Path A: API 自动润色（推荐，适合 >5 集项目）

需要 `LLM_API_KEY` 环境变量。调用 `polish_zh.py`（支持 SRT/ASS），10句/批送 LLM 润色。

```bash
export LLM_API_KEY="sk-..."
python scripts/polish_zh.py --input-dir AI审查后/ --output-dir 中文润色后/ \
    --glossary reports/proper-nouns.md
```

### Path B: AI 助理自行润色（无 API key，适合 ≤5 集样本）

**流程**：
1. 读取 `AI审查后/` 下的字幕文件（SRT 或 ASS，`parse_subtitles()` 自动检测）
2. 逐文件读取所有对白文本（仅 Default 风格，跳过 OP/ED/Title 等非对白行）
3. 批量润色：去翻译腔、去英文/俄文残留、口语化、修正标点
4. 保留专有名词（参考 `reports/proper-nouns.md` 如有）
5. 写回原格式（SRT→SRT，ASS→ASS，通过 `write_subtitles()` 自动检测）
6. 验证：搜索残留外文（英文/俄文）确认清零

> ⚠️ 仅适合 ≤5 集的样本项目。全集项目（>10集）强烈建议配置 `LLM_API_KEY`。

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
