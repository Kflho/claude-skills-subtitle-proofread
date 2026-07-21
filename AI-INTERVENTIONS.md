# AI Intervention Points — Subtitle Proofread

每个 🤖 点的触发条件、操作流程、判断标准。

---

## L2.5: AI Fragment Completion

**触发**: 管线输出 `[ai-review] N pending`

**数据**: `reports/manual-review/{EP}/ai_review.md`

**流程**:
1. 读取每个 EP 的 `ai_review.md`
2. 对每个 ⬜ 条目，读上文/下文和 Whisper 尝试文本
3. 推断正确日语，填入 `修正:` 字段
4. 无法确定 → 留空，留给 L6 人工

**判断原则**:
- 信任上下文，不信任 Whisper（Whisper 常丢词/乱码）
- 原文正确只是含拉丁字母（如 "OK"）→ 保留不改
- 不确定 → 留空

**应用**:
```bash
python run_all.py --lang ja --apply-ai-review
```

---

## L3.1: Auto-clean Glossary

**触发**: L1 扫描重建了 `reports/proper-nouns.md` 后自动运行

**流程**:
```bash
# 1. 试运行
cd "<project>" && PYTHONPATH="<scripts>" python \
  "<scripts>/nouns/auto_clean_glossary.py" \
  --glossary reports/proper-nouns.md --lang ja

# 2. 如果有 >0 条建议 → 应用 + 重建
cd "<project>" && PYTHONPATH="<scripts>" python \
  "<scripts>/nouns/auto_clean_glossary.py" \
  --glossary reports/proper-nouns.md --lang ja --apply --yes

cd "<project>" && PYTHONPATH="<scripts>" python \
  "<scripts>/nouns/build_glossary.py" \
  --findings temp/scans/findings.json -o reports/proper-nouns.md --lang ja
```

**auto_clean 会过滤什么**:
- 片假名：拟声词（ワンワン）、笑声（ハッハー）、日常词（パパママ）、碎片（ネルギー）
- 汉字：动词词干、时间/数字片段、修饰语片段、代词片段
- 保留：真实人名（アーサー）、地名、敬称、已知动画术语

---

## L3.2: AI Glossary Review

**触发**: auto_clean 之后，检查 glossary 中边界条目

**流程**:
1. 读取 `reports/proper-nouns.md` 全部三栏
2. 扫描低频/可疑条目：
   - 片假名：是真角色名还是拟声/日常词？
   - 汉字：是专有名词还是动词片段？
3. 判断为普通词 → 加入 `lib/japanese_utils.py` 的 COMMON_KATAKANA 或 COMMON_KANJI
4. 重建 glossary

**判断资源**:
- 鉄腕アトム (1963) 角色知识
- 日语常见人名模式（姓氏+名、敬称）
- 词是否合理作为名字 vs. 普通名词

**应用**（同 L3.1）: 编辑 `japanese_utils.py` → 重新 `build_glossary.py`

---

## L3.5: AI Proper Noun Judgment

**触发**: 管线输出 `AI REVIEW NEEDED: N proper noun candidates`
（auto_classify 处理不了的边界情况）

**数据**: `temp/scans/ai_review_candidates.json`

**流程**:
1. 读取候选项列表
2. 判断每个候选项是否为专有名词
3. 是 → 给出规范形式
4. 否 → 标记排除
5. 输出到 `temp/scans/ai_review_fixes.json`:
   ```json
   [{"action":"replace_global","original":"候補","replacement":"規範形"}, ...]
   ```
6. 重新运行: `python run_all.py --lang ja --resume`

**如果 auto_classify 处理了全部候选项（Needs AI: 0）→ 跳过此步。**

---

## L6.5: AI Pre-review (人工审查前 AI 先修)

**触发**: L6 deliver 生成了 `reports/manual-review/{EP}/checklist.md`

**流程**:
1. 检查 `reports/manual-review/` 下所有 `EP*/checklist.md`
2. 读取每个 checklist
3. 对每个 ⬜ 条目：
   - 读上文/下文
   - **不需要看视频就能推断** → 填入 `修正:`
   - **必须听音频/看视频才能判断** → 留空给人工
4. 应用可修复条目:
   ```bash
   python run_all.py --lang ja --apply-checklist --video-dir "<VIDEO_DIR>"
   ```
5. 报告：AI 修了 X 条，剩 Y 条需人工

**AI 可修的判断标准**:
- 上下文清楚显示乱码原文应是什么
- 当前 SRT 文本（`残留`）已经是正确日语 → 确认即可
- 常见日语短语，乱码模式明显
- **留给人工**: 两个同音词都可能、涉及你无法验证的人名、音频是关键判断依据

---

## AI Web Search for Proper Nouns (可选)

**触发**: 用户说 "网上搜索专有名词" / "search for character names"

**流程**:
1. 搜索 "鉄腕アトム 1963 キャラクター 一覧"
2. 提取角色名、地名、组织名
3. 保存到 `temp/scans/ai_nouns.json`:
   ```json
   {"characters": ["アトム", "ウラン", ...], "places": [...], "organizations": [...]}
   ```
4. 带 AI 数据重建 glossary:
   ```bash
   cd "<project>" && PYTHONPATH="<scripts>" python \
     "<scripts>/nouns/build_glossary.py" \
     --findings temp/scans/findings.json -o reports/proper-nouns.md --lang ja \
     --ai-nouns temp/scans/ai_nouns.json
   ```
   AI 来源的名词绕过 min_freq=3 阈值，标记 `[AI]`。
