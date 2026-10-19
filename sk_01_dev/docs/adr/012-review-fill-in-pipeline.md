# ADR-012：复核缺口由管线内 LLM 填充（默认只分类）

- 状态：已接受（2026-10-19）
- 取代/修订：ADR-010 决策 2 的「L1/L2 由 agent 填工作单、人工只留个位数」
  （实现形态变了：不再有工作单交接，也没有人工队列）
- 相关：ADR-003（报告=只读导出）、ADR-011（技术接入总图，Evaluation/Review 接缝）

## 背景

旧的「人工复核 AI 方案」是：报告/checklist 里填「修正:」→ 脚本回写。
ADR-003 把报告降级成 JSON 的只读导出后，这条路断了；`apply_review_fixes.py`
与 `ai_review_batch.py` 也已删除。迁移到 ASR-first 后，缺口写成
`<EP>.asr_review.json`，但要人跑 `asr_review_to_fixes.py` 再 `--resume`，
仍然是「交接 + 人工动作」。

用户要求：**不要新接口、不要人工审查，按旧思路在管线里把缺口填好**，
中间步骤和产出文件重新设计。

## 决策

1. **一个包**：`temp/scans/<EP>.ai_pack.json`（schema v2）承载全部缺口，
   字段包含原文、ASR 文本、窗口语音（`asr_window`）、相似度、边界误差、
   上下文、建议值，以及 `route`（这条值不值得问模型）。
2. **一套动作词**：`keep` / `set_time` / `replace_text` / `delete_line`，
   与 `apply_fixes` 完全一致，取消中间转换层。
3. **管线内闭环**：`run_all` 在 fix 之后直接跑 review 阶段
   （填充 → 校验 → 应用 → 逐条回滚 → 审计日志 `<EP>.ai_applied.json`），
   不新增 CLI，不再需要人跑转换脚本或 `--resume` 交接。
4. **默认只分类**：`REVIEW_ACTIONS=keep`。`set_time` / `replace_text` /
   `delete_line` 必须显式开启（`keep,delete` / `keep,delete,rewrite`）。
   依据见 `docs/review-ai-fill.md` §6：给改写权时 EP001 干净输入 48 条改动
   47 条变差（P90 0 → 1692 ms），给删条权时删掉 8–9 条真实短台词。
5. **报告只记真实改动**：keep 不进 `问题解决报告.md`，进包统计；
   L3「人工残留」= `kept` 计数，不是待办列表。

## 后果

- ✅ 不再有「待人工复核」队列；缺口的去向在包里有据可查。
- ✅ 路由把 EP001 的 275 条缺口压到 100 条可判（-64%），质量零变化。
- ✅ 任何越权/越界/坏结果的决策被逐条回滚，不会毁整份字幕。
- ⚠️ 现证据下 AI 复核**不产生改动**：结论是「不乱动」，不是「改对更多」。
  真正的缺口在音频侧证据（VAD/AED 边界）与对齐起点偏差（−0.37 s）。
- ⚠️ `ai_review_fixes.json` 仍保留给专名/OP-ED 路径（`step_apply_all`），
  下一轮再统一；ASR 工作单不再经过它。
