# ADR-010：ASR-first 流程重设计 + Review 阶梯 + LLM-only 翻译

- 状态：已接受（2026-10-18）
- 取代/修订：ADR-007 的“VAD 门控 + 时间重叠匹配”默认路径、`tech-route-2026.md` §5.4 的百度 fallback
- 相关：ADR-006（稳定 ASR 核心）、ADR-008（Qwen3 长音频切块）、ADR-009（参考=考卷）

## 背景

旧流程是围绕 `WebRTC VAD → kotoba Whisper 段级时间戳 → 时间重叠匹配` 写的工程实现：

1. 用 VAD 决定“哪里要修”，VAD 漏检就直接丢台词；
2. 用时间重叠把 Whisper 段贴到 cue，段级时间戳决定边界；
3. 修不动的条目走“AI fragments / AI review”清单，由 AI 或人工补；
4. 翻译是翻译前扫描出来的副产物，百度 + LLM 双路径。

2026 技术栈换成了 `Qwen3-ASR + Qwen3-ForcedAligner`（后续 FireRedVAD/AED、LLM 翻译）。
继续把新模型硬塞进旧流程，会同时继承旧流程的误差级联和人工负担：实机冒烟一集 296 条
CC，ASR 路由只自动修 13 条，剩下 **272 条 review**——其中大部分本该由自动规则或 AI 判掉。

**旧 3-Phase 只是编排骨架，不是流程规范。** 本 ADR 按新技术的强项重新设计内部流程；
骨架（SCAN → FIX → UNIFY/DELIVER）、JSON 契约、报告库、CoW 备份、never-downgrade 保留。

## 决策 1：ASR-first 的数据流

音频内容的“真相”由 ASR + FA 提供，VAD/AED 只做提示，不参与门控。

```text
Phase 0  摄取 / 文本扫描（不碰音频决策）
         - 编码/乱码/重复/空行扫描（纯文本）
         - glossary-first：先从项目字幕/前集建专名表（ASR context + 翻译用）

Phase 1  音频理解（ASR-first）
         - 整集音频 → Qwen3-ASR（60s 自切）→ ASRResult（缓存）
         - Qwen3-FA → token/字符时间轴
         - 可选 AED 提示：speech / singing / music 时间线（FireRedVAD；只提示）

Phase 2  对齐 / 修复
         - 字符时间轴 → cue mapper（引擎无关）
         - 修复计划：时间、文本、删条候选、拆条候选
         - 阶梯执行（见决策 2），写 SRT + 报告 + AI 工作单

Phase 3  统一 / 交付
         - 专名一致性检查
         - LLM 翻译（原文 + glossary + 场景）→ 目标语言
         - LLM 润色（目标语言，可选）
         - 交付校验：时间轴/假名残留/专名/回显/数量
```

明确**不再**做的事：

- 不用 VAD 决定修复区（`build_fix_regions` 不再是音频修复的入口）；
- 不用时间重叠把段贴到 cue（`match_whisper_to_cues` 只留在 Whisper 兼容路径）；
- 不用固定 180s 判 OP/ED（有 AED 用 AED，没有则用重复度/指纹）；
- 不把百度/传统 MT 当翻译路径（见决策 3）；
- 缺字幕不再默认靠“AI 从上下文编”（ASR 已有音频文本；无音频的 text-only 模式才保留 fragments）。

三种运行模式：

| 模式 | 输入 | 行为 |
|---|---|---|
| `asr-first`（目标默认） | 音频 + 字幕 | 全量 ASR + FA + 对齐修复 + LLM 翻译 |
| `text-only`（残血） | 只有字幕 | 文本扫描 + glossary + AI review；不编造音频时间 |
| `evaluation` | 音频 + 参考字幕 | 只读基准（CC 是边界测试标准，不回流） |

## 决策 2：Review 阶梯（复用旧流程已有的替代人工机制）

旧流程不是没有“替代人工”，而是这些机制没有接到 ASR 路由上：

| 机制 | 旧流程位置 | ASR-first 里的用法 |
|---|---|---|
| 置信度分级自动保留/自动修 | `whisper_pipeline` triage（high/retry/none） | `fix_time` / 高置信 `replace_text` 直接 L0 自动 |
| AI fragments 清单 | `ai_fragments_<EP>.json` + `Fixer.apply_ai_fragments()` | 每条非自动修复一份工作单，AI 填 `decision`，批量回写 |
| AI review 候选/修复 | `ai_review_candidates.json` → `ai_review_fixes.json` → `apply_fixes` | ASR 的 review_ai / 删条 / 拆条候选走同一套 |
| 专名 AI 分类 | `auto_classify` / `auto_clean_glossary` | glossary-first + 候选 AI 审查，不占人工 |
| OP/ED 预替换 | `oped_pre_replace` | AED + 指纹 + 重复度；歌词预替换 |
| 复用参考 | `reuse/apply.py` | 有可信参考时覆写译文；评测参考不回流 |
| 润色 | `polish_zh` | LLM 润色，不新增人工环节 |

阶梯定义（每条修复必须落在一层）：

- **L0 自动**：高置信时间修复；高置信文本替换（ASR 文本合理、相似度/语言/重复检查通过）；
  干净 cue 直接保留。写盘前备份，报告记 ✅。
- **L1 AI 批审**：L0 判不了的（低置信、歧义、删条/拆条候选）写成机器可读工作单，
  由 AI（LLM 或本 skill 的 agent）批量裁决；产出 `ai_review_fixes.json` /
  `ai_fragments_<EP>.json`，复用现有 apply 路径。**这一步不是人工。**
- **L2 AI 复核**：L1 的裁决再过一遍一致性检查（glossary、重复台词、时间合理性、
  相邻 cue 联动）；冲突或证据不足的降级。
- **L3 人工**：只留 L2 仍不敢判的（例如重复台词歧义、无音频证据、AI 冲突），
  附音频片段、候选和理由；目标是每集个位数。

量化目标（每集，按 CC 条数）：

| 指标 | 现状（冒烟） | 目标 |
|---|---:|---:|
| L0 自动 | 13/285 | ≥60% |
| L1+L2 AI | 0（没工作单） | 剩余中的 ≥90% |
| L3 人工 | 272（全堆在报告） | ≤5% |

实现要求：

1. `fix_by_asr` 必须为每条非自动修复生成 `asr_review_<EP>.json`（cue、时间、原文、
   ASR 文本、相似度、置信度、建议 action、证据、空的 `decision`）。
2. `apply_fixes` 增加 `set_time`（SRT），让 AI 裁决能同时改时间和文本。
3. `run_all` 的 AI review 步把 ASR 工作单和专名/AI fragments 一起列出，一套指令回填。
4. 评测加 `auto/AI/human` 三档计数，防止“把 review 从报告挪到另一个文件”冒充优化。

## 决策 3：翻译全部走 LLM，移除传统 MT

- 删除百度翻译路径（`fix/translate_srt.py`、`BAIDU_*` 配置、相关测试/文档）；
  不再维护、不再测试、不再作为 fallback。
- 翻译是 **Phase 3 的一等公民**：原文 + glossary + 场景/角色上下文 → LLM；
  批量、缓存、失败重试；输出统一 SRT。
- `translate_srt.py`（LLM）保留为翻译入口；`_translate_whisper_replacements` 改走
  `lib.llm.call_chat`，无 key 时保留原文并交给翻译阶段，不再静默调用百度。
- 专名一致性靠 glossary-first（ASR context + 翻译 prompt），不靠事后替换兜底。

## 后果

- ✅ 流程与模型能力对齐：ASR 覆盖全音频，FA 给字符级时间，不再被 VAD 漏检/段级时间戳限制。
- ✅ review 负担从“报告里 272 条”变成“L0 自动 + L1/L2 AI + 少量 L3 人工”，并可用数字验收。
- ✅ 翻译只剩一条 LLM 路径，维护面减半。
- ⚠️ 迁移期保留 Whisper 兼容路径（`ASR_ENGINE=whisper`）作为回滚；不删旧代码，只降级。
- ⚠️ FireRedVAD/AED、LLM key 在当前环境不可用：AED 相关步骤先留接口/降级，
  翻译的 live 测试等 key；不影响 ASR-first 主链路。
- ⚠️ L1/L2 依赖 AI 工作单质量与 apply 路径；工作单 schema、`set_time` 和计数指标要补测试。

## 迁移步骤

1. 本 ADR + `CONTEXT.md` 原则落库（本次）。
2. 删百度路径，翻译改 LLM（本次）。
3. `fix_by_asr` 生成 AI 工作单；`apply_fixes` 加 `set_time`；run_all 汇总工作单（下一步）。
4. 用三集基准量 L0/L1/L2/L3 比例；把 L0 比例和 L3 绝对数写进 release gate。
5. ASR-first 编排收口：`--asr qwen3` 跳过 VAD 门控/Whisper 修复区，Phase 3 接 LLM 翻译。
6. FireRedVAD/AED 可用后接提示时间线；OP/ED 改 AED + 指纹。
