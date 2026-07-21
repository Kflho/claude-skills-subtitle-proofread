# Layer Reference & Debugging — Subtitle Proofread

所有脚本位于 `C:/Users/54238/.claude/skills/subtitle-proofread/scripts/`。

环境变量前缀：
```bash
PROJ="<project-root>"
SCRIPTS="C:/Users/54238/.claude/skills/subtitle-proofread/scripts"
export PYTHONPATH="$SCRIPTS"
```

---

## Layer 1: Character Scan

`scan/unified_scanner.py` — 单次扫描全部 SRT：乱码字符、重复模式、术语频率。

```bash
cd "$PROJ" && python "$SCRIPTS/scan/unified_scanner.py" \
  --target-dir AI审查后/ --output-findings temp/scans/findings.json --project-lang ja
```

输出：
- `temp/scans/findings.json` — 全量扫描结果
- `temp/scans/issues/` — 每集问题详情
- `reports/proper-nouns.md` — 术语表（如果带 --build-glossary）

---

## Layer 2: Error Fix (Whisper)

`fix/fix_orchestrator.py` — 级联修复：参考字幕 → Whisper → 人工分流。

```bash
# Whisper 修复单集
cd "$PROJ" && python "$SCRIPTS/fix/fix_orchestrator.py" EP005 --step whisper

# 检查单集是否干净
cd "$PROJ" && python "$SCRIPTS/fix/fix_orchestrator.py" EP005 --step check
```

需要环境变量：`WHISPER_CLI`, `WHISPER_MODEL`, `WHISPER_RETRY_MODEL`。

---

## Layer 2.5: AI Fragment Completion

`run_all.py:step_ai_review` — 带日语语义但含拉丁乱码的碎片，无视频。

```bash
# 生成 AI review checklist
cd "$PROJ" && python -c "
from fix.fix_orchestrator import Fixer
Fixer('EP005', '$PROJ').review_ai()
"
```

AI 填完 → `python run_all.py --lang ja --apply-ai-review`

---

## Layer 3: Proper Noun Unification

### L3.0: Build Glossary

`nouns/build_glossary.py` — 从语料词频构建专名表。

```bash
cd "$PROJ" && python "$SCRIPTS/nouns/build_glossary.py" \
  --findings temp/scans/findings.json -o reports/proper-nouns.md --lang ja
```

可选：`--ai-nouns temp/scans/ai_nouns.json` 合并 AI 网上搜索的名词。

### L3.1: Auto-clean Glossary

`nouns/auto_clean_glossary.py` — 启发式剪枝，去除明显非专名。

```bash
# 试运行
cd "$PROJ" && python "$SCRIPTS/nouns/auto_clean_glossary.py" \
  --glossary reports/proper-nouns.md

# 应用（编辑 japanese_utils.py）
cd "$PROJ" && python "$SCRIPTS/nouns/auto_clean_glossary.py" \
  --glossary reports/proper-nouns.md --apply --yes
```

### L3.3: Noun Checker

`nouns/noun_checker.py` — 扫描 SRT 查找专名变体。

```bash
# OP/ED 跨集一致性
cd "$PROJ" && python "$SCRIPTS/nouns/noun_checker.py" AI审查后/ --lang ja --oped

# 对照专名表检查（带 --noun-table）
cd "$PROJ" && python "$SCRIPTS/nouns/noun_checker.py" AI审查后/ --lang ja \
  --noun-table reports/proper-nouns.md -o temp/scans/nouns/
```

### L3.4: Auto Classify

`nouns/auto_classify.py` — Jamdict + 规则预分类，减少 AI 审查量。

```bash
cd "$PROJ" && python "$SCRIPTS/nouns/auto_classify.py" \
  --candidates temp/scans/noun_candidates.json --lang ja \
  --output temp/scans/noun_classified.json
```

---

## Layer 4: Apply Fixes

`apply/apply_fixes.py` — 收集所有来源的修复，批量写入 SRT。

```bash
cd "$PROJ" && python "$SCRIPTS/apply/apply_fixes.py" \
  --target-dir AI审查后/ --fixes temp/scans/all_fixes.json --lang ja
```

---

## Layer 5: ASS Format Repair

`ass/ass_repair.py` — ASS 格式修复（SRT 项目跳过）。

```bash
cd "$PROJ" && python "$SCRIPTS/ass/ass_repair.py" \
  --target-dir AI审查后/ --check all
```

---

## Layer 6: Human Review Delivery

`fix/fix_orchestrator.py:review` — 生成人工审查清单 + 视频片段。

```bash
cd "$PROJ" && python "$SCRIPTS/fix/fix_orchestrator.py" EP005 --step review
```

在 `reports/manual-review/EP005/` 输出 `checklist.md` + `.mp4` 片段。

填写 `修正:` 字段后 → `python run_all.py --lang ja --apply-checklist --video-dir "<VIDEO_DIR>"`

---

## Report Summary

```bash
cd "$PROJ" && python "$SCRIPTS/utils/update_report.py" \
  reports/问题解决报告.md --summary
```

## Glossary Maintenance Cycle

```
L3.0  build_glossary.py      → aggressive JMdict filter → raw glossary
L3.1  auto_clean_glossary.py → heuristic prune (all 3 sections)
L3.2  Claude AI review       → semantic judgment on borderline survivors
       ↓
       Rebuild: python build_glossary.py ... → clean glossary
       ↓
L3.3  noun_checker.py        → scan SRTs for variants
L3.4  auto_classify.py       → deterministic accept/reject/needs_ai
L3.5  Claude AI judgment     → decide remaining unknowns
```
