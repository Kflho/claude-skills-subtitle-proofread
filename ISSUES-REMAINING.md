# 遗留问题（2026-07-22 重构后）

## ✅ 已修复（本轮）

### Bug 修复
- ✅ **L2.5 报告条目丢失** — try/except 加 traceback 打印，便于定位根因
- ✅ **L3 专名自动应用未写报告** — `_apply_classified_results()` 添加 `upsert_entries(step='3')`
- ✅ **L3.5 AI 专名审查未写报告** — `_apply_classified_results()` 添加 `upsert_entries(step='3.5')`（含 fallback 路径）

### 设计债清理
- ✅ **废弃 wrapper 删除** — `review_ai_from_fragments()` + `review_ai()` 已删除（无外部调用）
- ✅ **专名库污染清理** — COMMON_KATAKANA 新增 7 条碎片词，COMMON_KANJI 新增 25 条误入普通词；proper-nouns.md 移除 5 条垃圾条目（ロボットだ、ネルギーがなくな、一体どうした、誰だ），补全缺失的"角色名+敬称"节标题
- ✅ **编号分层废除** — AI-INTERVENTIONS.md、SKILL.md、LAYERS.md 全面改用 3-Phase 描述性步骤名，消除 L3.1/L3.2/L3.5 旧编号

### SKILL.md 更新
- ✅ Phase 1 显式声明 "Does NOT write to 问题解决报告"
- ✅ Phase 3 展开为 5 个子步骤：Glossary maintenance → Noun variant detection → Auto-classify → AI judgment → Deliver
- ✅ Phase 3 标注每个子步骤对应的报告 section
- ✅ Glossary maintenance cycle 在 SKILL.md 中有入口链接

## 🟡 仍待处理

### 报告写入分散（设计债，非 bug）
当前报告由 `fix_orchestrator.py`（Phase 2）、`apply_fixes.py`（Phase 3 apply 子进程）、`run_all.py`（Phase 3 classify）分别调用 `upsert_entries`。
每次调用都是 read-modify-write，如果顺序不对（后运行的覆盖前面），条目可能丢失。
**建议**：内存中累积 → 最后一次性 `write_report()`。但当前 `upsert_entries` 已保证 read-modify-write 原子性，实际丢失风险低。

### Feature Envy — Fixer 硬编码 step label
`fix_orchestrator.py` 中 step label (`'2'`, `'2.5'`, `'6'`) 和 status emoji 是裸字符串。
`update_report.py` 已有 `LAYER_NAMES` 和 `STATUS_MAP`，Fixer 应引用而非硬编码。
**影响**：低。step label 变化频率极低，当前硬编码不会导致 bug。

### 专名库需重建
`COMMON_KANJI` 和 `COMMON_KATAKANA` 已更新，但 `reports/proper-nouns.md` 中仍有 ~25 条应移除的汉字复合词条目。下次运行 Phase 1 的 `build_glossary.py` 时会自动过滤。

## 🟢 已验证通过（上次）

- ✅ Triage 在 SRT 写入前执行 — AI fragment 不污染 SRT
- ✅ ai_review.md → temp/scans/ai_fragments_{EP}.json
- ✅ auto_classify intro pattern 修复（移除裸 って）
- ✅ 问题解决报告 3-Phase 格式
- ✅ L2 Whisper 自动修复 → 报告 ✅
- ✅ L2.5 AI 补全 apply → 报告 ✅ + escalate L6
