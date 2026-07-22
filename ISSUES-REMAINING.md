# 遗留问题（2026-07-22 重构后）

## 🔴 需要修复

### 1. Layer 2.5 报告条目未写入
`fix_by_whisper()` 中的 `upsert_entries(step='2.5', ai_fragments)` 实际运行时未产生条目。
现象：报告显示 L2.5 "暂无记录"，但 ai_fragments JSON 有 3 条数据。
可能原因：`step_apply_all()` 子进程重新读写报告时覆盖了 L2.5 条目，或 try/except 静默失败。
**排查方向**：在 `fix_by_whisper()` 的 try 块中加 print 确认 upsert 是否执行。

### 2. Layer 3（专名自动应用）未写报告
`_apply_classified_results()` 在 `run_all.py` 中将 ACCEPT 候选写入 `noun_accepted_fixes.json` 并 append glossary，但没有调用 `upsert_entries(step='3')`。
**修复点**：`run_all.py` 第 340-352 行，在 glossary append 后添加报告写入。

### 3. Layer 3.5（AI 专名审查）未写报告
`_apply_classified_results()` 将 NEEDS_AI 候选写入 `ai_review_candidates.json`，但没有调用 `upsert_entries(step='3.5')`。
**修复点**：`run_all.py` 第 359-369 行，在保存 candidates 后添加报告写入。

## 🟡 设计债

### 4. Feature Envy — Fixer 直接操作报告层
`apply_ai_fragments()` 在 fix_orchestrator.py 中硬编码 step label (`'2.5'`, `'6'`) 和 status emoji。
更好的设计：Fixer 只描述"发生了什么"，由 report 模块决定如何编码。

### 5. 报告写入分散在多处
当前报告由 `fix_orchestrator.py`（L2/L2.5）、`apply_fixes.py`（L4 子进程）分别写入。
如果顺序不对（后运行的覆盖前面），条目就丢了。
**建议**：内存中累积 → 最后一次性 `write_report()`。

### 6. 废弃 wrapper 未清理
`review_ai_from_fragments()` 和 `review_ai()` 保留为 deprecated wrapper。
确认无外部调用后可删除。

## 🟢 已验证通过

- ✅ Triage 在 SRT 写入前执行 — AI fragment 不污染 SRT
- ✅ ai_review.md → temp/scans/ai_fragments_{EP}.json
- ✅ auto_classify intro pattern 修复（移除裸 って）
- ✅ 问题解决报告 3-Phase 格式
- ✅ L2 Whisper 自动修复 → 报告 ✅
- ✅ L2.5 AI 补全 apply → 报告 ✅ + escalate L6
