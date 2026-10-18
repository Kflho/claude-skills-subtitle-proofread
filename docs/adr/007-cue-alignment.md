# ADR-007: cue 对齐用字符时间轴 + 窗口匹配

- **Date**: 2026-10-18
- **Status**: 🟡 Accepted（P2 核心落地；未接默认管线）
- **Context**: 旧链路用「ASR 段级时间戳 + 时间重叠 ±3s」把 ASR 文本贴回 cue。它无法处理三类真实情况：
  1. 长 ASR 段合并多句 cue（多人争吵）；
  2. 时间轴整体漂移（不同剪辑、字幕来源）；
  3. 重复台词（OP 歌词、口头禅）——重叠匹配会贴错位置。
  另外 Qwen3 给 token、Whisper 给 segment，两种粒度都要走同一条下游。

## Decision

1. **统一的字符时间轴**（`lib/alignment/timeline.py`）：把每个 ASR 单元（token 或 segment）的文本归一化后，在本单元 start/end 内均分到字符，得到单调的 `chars/starts/ends`。token 优先，segment 兜底；没有时间戳就返回空轴，由 mapper 报 `no_asr`，绝不编造时间。

2. **时间窗而不是字符窗**（`cue_mapper._select_window`）：每个 cue 只取 `[cue_start-search, cue_end+search]`（默认 ±6s）内的字符。不再「凑够 N 个字符」——稀疏音频里那会跨越几分钟，把远处重复台词吸进来。窗口内用单调 cursor 防止 cue 互相抢字符；**允许合法重叠**的 cue 从自己的锚点重新开始，避免第二条被饿死。

3. **窗口内字符匹配 + 最佳簇**：`difflib.SequenceMatcher(autojunk=False)`。`autojunk` 必须关：日语常用字在长序列里会被当垃圾元素忽略，导致大面积漏配。匹配块按「两个字符串里间隔都 ≤4 字符」聚簇，取覆盖字符最多的簇作为音频跨度；散落的单字匹配不会把边界拉长。

4. **精确串优先 + 就近选择**：窗口里若存在 cue 文本的**精确子串**，在所有这些出现位置中选离 cue 中点最近的那个。`SequenceMatcher` 只返回一条单调对齐，天然落在最早出现处；重复台词必须靠这一步纠正。

5. **状态词表做加法**：在方案原有 `match/garbled/missing_asr/extra_text/no_speech` 之上补 `empty_text`（cue 无文本）和 `no_asr`（没有任何时间轴）。状态由相似度 + 语音覆盖率 + 尾部溢出共同判定，见 `_map_one`。

6. **置信度是多信号投票，不是模型分数**：Qwen3 不暴露 logprob，所以用 `similarity + 边界误差 + 重复度 + 语种占比 + 内部停顿`。1–2 字的 cue 永远不给 `high`——它们在日语里和半个剧本都能匹配上。

7. **修复分级**（`fix/align_repair.py`）：
   - 文本 match 且边界误差 ≤150ms → 不动（`clean_boundary`）。
   - 文本 match、150ms < 误差 ≤2s、且**联动求解后**不与非移动 cue 撞车 → 自动改时间码。
     相邻 cue 若也在移动，可以占用对方的旧窗口（见 Consequences 的联求解）。
   - 误差 >2s → 转 review，不自动改（`max_time_shift`）。
   - 文本替换只在 `allow_text_replace=True` 且 cue 可判定为 garbled 时执行；默认只出提案。
   - `no_speech` / `missing_asr` / `extra_text` **永不自动删除**，只进审查清单。

8. **对齐器走 registry**：不新增 `lib/alignment/qwen3_fa.py`；P1 已有 `KIND='aligner'` 插件（`lib/asr/adapters/qwen3_aligner.py`）。`lib/alignment/aligner.py` 只是薄服务，`ALIGNER_ENGINE=none` 可整体关闭。ADR-006 的「新模型只加 adapter」继续成立。

9. **不改默认管线**：P2 以独立 CLI 交付（`scripts/asr/align_cues.py`），默认 dry-run。`run_all`/`fix_orchestrator` 的接入等真实金标验收后再做（方案 §8.1 P2 验收）。

## Consequences

- ✅ 无 O(n²) 全矩阵：405 cue / 24 分钟 → 约 0.23s/集（193 集约 45s），纯 stdlib。
- ✅ token 与 segment 同一路径；Whisper 兜底也能诊断，只是精度受段级插值限制。
- ✅ 重复台词、时间漂移、长段合并不再必然错配；实测见 `docs/tech-route-progress.md` §4。
- ✅ 默认不写盘、不删除、不换文本，"干净 cue 破坏率" 的风险面被压到只改时间码。
- ✅ 机制回归可复现：`scripts/eval/alignment_bench.py --srt <字幕>` 用真实 cue 反推合成 ASR，不需要音频。
- ⚠️ 段级 ASR（无 token）的 cue 边界 P50 只有 ~300ms，达不到 100ms 目标；Qwen3-FA 的 token 时间轴是必要前提。
- ⚠️ 全局漂移超过 `search_seconds`（默认 6s）时整集失配，只会报 `no_speech`/`missing_asr`；需要先做整集偏移估计（已实现，见下）。
- ⚠️ 同一时刻完全重复的 cue（OP 双行）仍可能映射到相邻的同一句，误差在 ±3s 内会被标成 review 而不是自动改。
- ✅ **整集统一偏移已实现**：`estimate_global_offset` 只在「≥20 条高相似 match + MAD ≤150ms」时
  认定整集漂移，然后把所有 cue 一起平移。EP001 的 +0.5s 平移测试里 367/370 恢复原位。
- ✅ **相邻 cue 联动求解已实现**（`_resolve_joint_time_fixes`）：候选时间窗先全部提出，
  再按「移动者用新窗口、不动者用旧窗口」做冲突消解；两两提议重叠且原本不重叠 → 双方转 review，
  重复直到稳定。EP001 +0.5s 回放（370 cue）：无整集偏移时自动应用 **89 → 262**，
  50ms 内还原 **70 → 247**；有整集偏移时 365 → 364（唯一差异是一条 24.5s 长 cue 的
  163ms 边界修正），精度没有实质退化。
- ⚠️ 对齐依赖 cue 文本可读；纯图形/矢量（ASS `\p1`）cue 无文本，天然是 `empty_text`/`no_speech`。
- ⚠️ ASS 写回目前只更新文本、不更新时间码，`align_cues.py --write` 对 `.ass` 直接拒绝（退出码 4），避免「成功但什么都没改」。

## Alternatives rejected

| 方案 | 原因 |
|---|---|
| 全量 O(n²) DP（cue 文本 × ASR 文本） | 2 万字符 × 400 cue 不可行；窗口 + 精确串已覆盖主场景 |
| 继续用时间重叠 ±3s | 无法拆长段、无法处理重复台词；P2 的根因就在这 |
| 只支持 token 级 ASR | Whisper 兜底、云 API 只有 segment；没有统一路径就无法 A/B |
| 强制 cue 按时间排序后再对齐 | 真实 ASS 文件存在乱序行（莎莉 EP001 有 12 处）；必须在 mapper 内排序再还原索引 |
| 自动删除 `no_speech` cue | 误杀 OP/ED、旁白、静音表演的风险高于收益；删除必须人工/AI 决策 |
| 把置信度写成 logprob 阈值 | 模型特定、不可迁移；ADR-006 的换模型目标会被破坏 |

## Follow-up

- ✅ 已接 `run_all --asr qwen3` / `fix_orchestrator.fix_by_asr()`（默认仍 Whisper）。
- ✅ 整集偏移估计已落地（`estimate_global_offset` + `shift_cues`）。
- ✅ 相邻 cue 联动的逐条求解已落地：`_prepare_time_fix` 只做单条合法性检查，
  `_resolve_joint_time_fixes` 做集合级冲突消解；回归测试见 `tests/test_align_repair.py`
  （密集相邻 cue 同时移动、冲突提议双方 review、静止邻居仍拦截、既有重叠容忍）。
- P3 FireRedVAD 的 speech 时间线替换 `speech_coverage` 的 ASR 近似。
- 用用户提供的金标素材（完整字幕 + 视频）跑 §9 的 A/B，填 progress §4。莎莉/阿童木字幕质量差，不作验收素材。
