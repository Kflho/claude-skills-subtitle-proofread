# ADR-009: 参考字幕是 held-out 考卷，不是调参教材

- **Date**: 2026-10-18
- **Status**: ✅ Accepted
- **Context**: EP01–03 视频与日文 CC 都已到位，终于可以用参考字幕量化 CER / 边界误差。
  但真实工作流不一定有日语参考字幕；如果拿参考字幕反过来调阈值、改 prompt、
  挑 chunk 长度，模型只是记住了这三集，换一部片就失效。此前代码里已经有一批
  「为了让 EP01 数字好看」的固定秒数/比例（零时长补全、质量阈值等），正是这个风险。

## Decision

1. **参考字幕只用于打分**：`eval/` 下的评测脚本只读，不写回 manifest、ASR JSON、
   `lib/config.py` 或任何参数文件。参数来源必须是音频/ASR 自身证据或明确 prior。
2. **dev/test 冻结**：EP001 = dev（早期已看过指标，只做冒烟）；EP002/003 = test。
   test 只在改动冻结后跑一次；单 split 的 before/after 原样记录。
3. **评测器分层**：
   - `diagnose_timeline.py`：无参考，检查 token/segment 时间轴健康度；
   - `episode_benchmark.py`：主故事窗口的 CER + cue 对齐 + 计划修复安全性，只读；
   - `asr_eval.py`：P0 全文件 CER/边界，保留作回归。
4. **验收看四组数**：CER（macro/micro）、起点 P50/P90、±100ms、自动修复中的
   「结尾缩短」数。短 cue（≤2 字）与低置信匹配单独统计，不混进主 P90。
5. **允许的证据**：音频能量/VAD、局部语音速率、token 自身分布、既有 ADR 实测、
   人工标注的约定（如字幕结尾拖尾）。**禁止**：按集特判、用 test 指标反推常数、
   把参考字幕文本喂回 ASR/对齐/翻译输入。
6. **负结果要留档**：机制在 dev 上有效但 test 无提升，就保留为 opt-in 或回退，
   并把失败记录写进进度文档。

## Consequences

- ✅ 三集有了可复现的冻结基线：`temp/reports/digimon_qwen3_raw.{json,md}`、
  `digimon_whisper_raw.{json,md}`；冻结摘要 `eval/reports/digimon_benchmark_2026-10-18.md`。
- ✅ 证明了真实收益：字幕结尾 display linger 保留后，raw 下三集原本 106 条自动修复会缩短结尾，
  现在 0 条；起点 P50/P90 不变。Whisper large-v3 q5 A/B 也已跑：文本更好、打轴更差。
- ❌ **整集偏移被否**：用户人工复核确认视频/CC 无偏移；`estimate_global_offset` 的
  -0.37/-0.40 s 是在吸收 ASR 起点与 CC 显示起点的系统差。把 offset-corrected 的
  P50 57.5 ms / ±100 ms 68.3% 当优化会让字幕整体提前。`align_cues` 已改为 opt-in。
- ❌ 局部偏移原型同样会吸收该偏差，不采纳。
- ✅ 也留下了负结果：segment-start overlong trim 只在 EP01 提升、EP02/03 中性，保持 opt-in；
  cursor 回看增加 match 但 P90/±100 ms 变差，不采纳。
- ⚠️ raw test 的可靠匹配 P90 510 ms（目标 ≤300 ms）、CER 0.263；剩余误差主要来自
  短语气词/重复台词的歧义匹配与 FA 零时长，需要 P3 VAD 或分段重对齐，不能靠调阈值。
- ⚠️ 参考 CC 本身含字幕卡/写法差异，CER 只能作相对比较；结尾边界是显示时间，
  不应作为语音边界误差的主指标。

## Follow-up

- 用同一协议跑 P0 Whisper vs Qwen3 A/B（EP01–03，dev/test 分开）。
- P3 FireRedVAD 上线后，把「无语音证据不补时间」升级为 VAD 引导的分段重对齐。
- 翻译/润色链路（P4）沿用同一原则：prompt 用原文+glossary+场景，不塞参考字幕答案。
