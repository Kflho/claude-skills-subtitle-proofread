# ADR-011：技术接入总图（所有候选技术的统一接入位）

- 状态：已接受（2026-10-18，2026-10-18 修订：只考虑免费/本地）
- 关系：`ADR-010` 定义 ASR-first 的流程与 Review 阶梯；本文定义**所有技术**（已选、备选、
  未来可选、明确不采用）落在哪个接入位，避免“做完才发现还有没算进去的技术”。
- 原则：**技术只通过接缝（seam）接入，不旁路硬接。** 每个可选能力都有 null 实现，
  默认关；新增技术 = 实现一个 adapter/stage，不改流程。

> **成本原则（2026-10-18 用户决定，修订版）**：优先免费/本地；需要 token 的服务
> **只接受国内**（DeepSeek、阿里 DashScope/Qwen、讯飞、腾讯等），**国外服务暂不配置**
> （OpenAI / Anthropic / Gemini / Azure MAI / ElevenLabs / HF gated 等不纳入）。
> 当前唯一已配置并验证的是 **DeepSeek**（`~/.claude/settings.json` 的
> `LLM_API_KEY`/`LLM_BASE_URL`/`LLM_MODEL=deepseek-v4-pro`）。
>
> **翻译阶段**用已配置的 DeepSeek LLM（唯一确定使用 LLM 的阶段）；**翻译以外的阶段
> 默认不新增 LLM 调用**：Review L1/L2 由本 skill 的 agent 工作单完成，润色并入翻译 prompt。
> 如果以后要自动化 Review，只能选国内 token 服务，并单独走成本/隐私门。
>
> 本地/免费资产已核实：`whisper-cli.exe` + kotoba q5 + large-v3 q5 均在
> `D:/software/video/whisper-cublas-12.4.0-bin-x64/`，Whisper fallback/A-B 可用；
> Qwen3-ASR-1.7B/0.6B + FA 在 `models/`；HTDemucs 已缓存。FireRedVAD 等免费本地模型
> 缺资产时只能等模型，不用云服务替代。
>
> **2026-10 技术审计**（`docs/model-audit-2026-10.md`）：下载前先判“新 = 领先”。
> 结论：**P0 只下 FireRedVAD**；Qwen3-1.7B/FA 保留；VibeVoice/说话人已删除；
> Whisper 系不下载/不测试（用户规则）；Parakeet JA、ReazonSpeech、BuzzASR、
> WhisperX、pyannote、Sortformer v1 淘汰；Demucs 降级为 fallback。
>
> **接入前能力测试**（`docs/capability-tests.md`）：模型卡声称 ≠ 本机能力。
> FireRedVAD 只证明可做空洞过滤（6/30 speech 主导），OP/ED 未测；
> VibeVoice-BitNet 实测无 who/when；用户决定删除说话人，资产已清理；
> Mel-Band 无运行时/无 A/B。**没通过能力测试的接入 = 猜测。**

## 1. 为什么需要总图

`tech-route-2026.md` 里不止 Qwen3-ASR/FA：还有 FireRedVAD/AED、Silero/TEN-VAD、
WhisperX/MMS FA、Demucs/RoFormer、Qwen-MT、
云 ASR（MAI/Scribe/Gemini/gpt-4o）、OP/ED 指纹、切片重对齐、chunk 45/60/90、
双引擎一致性、glossary-first、LLM 可读性等。前面几轮只把 ASR/FA 接进了旧流程，
其余技术没有接入位，导致：

1. 每接一个新模型都要改编排/修流程；
2. 已经做出的修复可能被后来接入的 AED/双引擎方案推翻；
3. 评测只看边界，不看 review 比例、成本，无法判断该不该接。

## 2. 统一架构（层 + 接缝 + null 实现）

```text
Ingest
  audio_extract ── Separator（null | demucs | RoFormer）──► wav
AudioEvents（可选，全部 null 默认）
  VAD/AED（null | FireRedVAD | Silero | TEN）──► AudioHints(speech/music/singing)
Audio Understanding
  ASR Router（registry: qwen3_1.7b | qwen3_0.6b | whisper；云 ASR ⛔ 排除）
    └─ context/hotwords、chunk_sec、diarize、ensemble 都是 ASRConfig.options
  Aligner Router（registry: qwen3_fa | whisperx | mms_fa）
  Ensemble（null | second local engine only for medium-confidence cues）
Alignment / Repair
  CharTimeline → CueMapper → CueDiagnostics(Evidence) → RepairPlan → ReviewLadder
  SpotReAlign（null | 短窗 FA 重对齐，带音频证据）
Confidence / Review
  Evidence（多信号 + 跨引擎 + 可读性 + 音频质量）→ L0 自动 / L1 AI / L2 复核 / L3 人工
  ReviewWorklist（asr_review_*.json / ai_fragments_*.json / ai_review_fixes.json）
Text / Deliver
  Glossary（glossary-first：ASR context + 翻译 prompt）
  Translation（已配置 LLM（DeepSeek，翻译例外）| agent 工作单；其它付费服务 ⛔ 排除）
  Polish / QA / Report
Orchestration
  modes: asr-first | text-only | evaluation
  cost/privacy policy（全本地，无云兜底）
Evaluation
  CC 边界标准 + auto/AI/human 计数 + RTF/成本 + 跨引擎 A/B
```

**Null-stage 规则**：上面的可选层默认返回空对象（`AudioHints()` / `None`），
主链路在“全部可选技术关闭”时仍能完整跑通。加技术不改流程，只加实现 + 配置 + 证据门。

**接入位规则**：

- 新 ASR/对齐模型：只写 `lib/asr/adapters/<name>.py`（`KIND='asr'|'aligner'`），
  router/CLI/修复管线不改；
- 新音频事件源（VAD/AED/分离）：实现 `lib/audio/base.py` 的 provider 契约；
- 新翻译模型：实现 `TranslationBackend`（LLM/Qwen-MT 都是 backend）；
- 核心层（mapper/planner/review）只读契约，不 import 模型。

## 3. 需要现在就定义的契约

这些字段不在今天实现，但必须先占位，否则接入时要改数据流：

| 契约 | 关键字段 | 谁消费 |
|---|---|---|
| `AudioHints` | `speech[]`、`music[]`、`singing[]`、`source`、`confidence` | OP/ED 判定、空洞诊断、翻译场景、review 证据 |
| `Evidence` | `similarity`、`char_coverage`、`boundary_*`、`repeat`、`lang`、`cross_engine`、`audio_quality`、`aligner_quality`、`sources` | Review 阶梯、gate |
| `ReviewItem` | `cue`、`action`、`evidence`、`decision`、`replacement`、`new_time`、`source` | L1/L2 工作单、apply |
| `TranslationRequest` | `cues`、`glossary`、`context_window`、`target_lang`、`max_chars/cps`、`draft_model` | LLM/Qwen-MT 翻译 |
| `ASRConfig.options` | `chunk_sec`、`context/hotwords`、`diarize`、`ensemble`、`model_role`（default/draft/fallback） | router/worker/评测 |

说话人（diarization）已由用户删除（2026-10）；不再保留生产者/接入计划。

## 4. 全量技术台账（现在差什么）

图例：✅ 已接并测过；🟡 部分接入/未测；⬜ 未接入；⛔ 明确不采用；🚫 环境阻塞。

### 4.1 音频理解

| 技术 | 定位 | 现状 | 接入位 | 阻塞/触发 |
|---|---|---|---|---|
| Qwen3-ASR-1.7B | 默认 ASR | ✅ opt-in | ASR adapter | — |
| Qwen3-ASR-0.6B | 快速/草稿/双引擎 | 🟡 无日语独立基准；试跑 10min 未完成 EP001 | ASR adapter + `model_role=draft` | 审计：非领先；不下载/暂不接，先加 guard |
| Whisper large-v3/turbo | fallback | ✅ 本机 whisper.cpp CUDA 已有；按用户规则**不下载、不再测试** | ASR adapter | 只保留现有资产，不投入 |
| kotoba-v2.0 | 旧默认 | ⛔ 仅兼容 | legacy | — |
| VibeVoice-ASR-7B | 说话人/热词 | ⛔ 已删除（用户决定：说话人无用；BitNet 也无 who/when） | — | 不再考虑 |
| ReazonSpeech / Parakeet JA | 备选 ASR | ⛔ 2024 年模型，日语 WER 0.344/0.348，非领先 | — | 审计淘汰 |
| 国内云 ASR（DashScope/Qwen-ASR、讯飞、腾讯） | 难例兜底 | ⬜ 允许（国内 token），未配置 | ASR adapter + 路由门 | 需要 key/成本/隐私门；不默认 |
| 国外云 ASR（MAI/Scribe/Gemini/gpt-4o） | 难例兜底 | ⛔ 暂不配置 | — | 用户暂无配置时间 |
| Qwen3-FA-0.6B | 默认对齐 | ✅ | Aligner adapter | — |
| WhisperX / MMS FA | 对齐备选/降级 | ⬜ | Aligner adapter | WhisperX 仅无 Qwen3-FA；MMS 仅无 GPU 且文本转假名（NC 许可） |
| MFA / stable-ts | — | ⛔ 不采用 | — | 动画专名/OOV、非真 FA |
| FireRedVAD + AED | VAD/AED 提示 | ✅ 已下 + provider/CLI；空洞归因：仅 6/30 为 speech 主导，只作证据过滤 | `lib/audio` provider → `AudioHints` | OP/ED 需整集音频测试；不做门控 |
| Silero-VAD / TEN-VAD | VAD fallback | ⬜ | 同上 | FireRed 不可用/对比 |
| WebRTC VAD | 旧门控 | ⛔ 降级为 legacy | legacy | — |
| Demucs htdemucs | 人声分离 | 🟡 2021/22 老模型；仅 Whisper 路径 | Ingest separator | 审计：降级为 fallback，不再投入 |
| BS-RoFormer / Mel-Band | 更强分离 | ⚠️ Mel-Band 权重已下，但无运行时/无 A/B → 未测，不接 | Ingest separator | 先补运行时 + 60–120s A/B |
| 跨集音频指纹 | OP/ED | ⬜ | AudioEvents/OPED stage | 与 AED 一起 |

### 4.2 对齐 / 修复 / 置信度

| 技术 | 现状 | 接入位 |
|---|---|---|
| 字符时间轴 + cue mapper + quality | ✅ | `lib/alignment/*` |
| 对齐置信度（相似度/边界/重复/语言） | ✅ | `Evidence` |
| 跨引擎一致性（双引擎只跑中置信 cue） | ⬜ | `ASRConfig.options.ensemble` + Evidence |
| LLM 可读性/语法置信 | ⬜ | Evidence（可选项，不阻塞） |
| 音频质量/重叠证据 | ⬜ | Evidence（来自 AudioHints） |
| Review 阶梯（L0-L3） | ⬜ 设计完成、未实现 | `ReviewItem` + worklist + apply |
| 切片重对齐（短窗 FA，带证据） | ⬜ 旧 spot fix 仍重跑 Whisper | SpotReAlign stage |
| chunk 45/60/90 | 🟡 worker 支持 `--chunk-sec=60`，CLI/评测未暴露 | ASRConfig.options |
| 整集偏移 | ⛔ 默认关（自证） | — |

### 4.3 文本 / 翻译 / 专名

| 技术 | 现状 | 接入位 |
|---|---|---|
| glossary-first 词表 | 🟡 生成已有；ASR context 未注入（`ASR_CONTEXT_FILE` 死配置） | Phase 0 → ASR context / 翻译 prompt |
| LLM 翻译 | 🟡 key/base/model 已核实（settings）；工具在，未进 run_all | TranslationStage（LLM backend，翻译唯一例外） |
| Qwen-MT 草稿 + LLM 审校 | ⬜ 国内可选，未接（当前 DeepSeek 翻译已够） | TranslationBackend（draft_model） |
| Baidu/传统 MT | ⛔ 已删除 | — |
| 专名 AI 分类/清洗 | ✅（旧链路） | Phase 0/3 |
| LLM 润色 | 🟡 并入翻译 prompt（不单独调用） | Phase 3 |
| OP/ED 歌词预替换 | ✅ 旧链路 | Phase 2/3（保留） |

### 4.4 评测 / 编排 / 运维

| 技术 | 现状 | 接入位 |
|---|---|---|
| CC 边界标准 + 三集基准 | ✅ | `episode_benchmark` |
| release gate（目标 + 回归） | ✅ | `release_gate.py` |
| auto/AI/human 三档计数 | ⬜ | gate 指标 |
| 跨引擎 A/B（逐 cue） | 🟡 只有整集报告 | gate/评测 |
| 成本/RTF/显存回归 | 🟡 有 RTF，无成本/显存 | gate |
| 三模式编排（asr-first/text-only/evaluation） | 🟡 只有 flag，未分模式 | run_all |
| 云兜底策略 | ⛔ 不默认；国内云只作难例候选 | router policy |
| 缓存/幂等（context_hash 已有） | ✅ 契约；ensemble/AED 缓存待补 | cache |

## 5. 接入顺序（按依赖，不按新鲜度）

**A. 现在（设计落地，防返工）**
1. `AudioHints` / `Evidence` / `ReviewItem` / `TranslationRequest` 契约 + null 实现；
2. ✅ Review 阶梯第一段：`<EP>.asr_review.json` 工作单、`apply_fixes.set_time`、
   run_all 的 AI review 步列出工作单；
3. 剩余：自动分级、`ai_review_fixes` 汇总联动、gate 加 auto/AI/human 计数。

**B. 本地文本价值（无新模型）**
4. glossary-first → ASR `context`（`ASR_CONTEXT_FILE` 接上或 run_all 传入）；
5. run_all 接翻译阶段（已配置 LLM：glossary + 场景 + 长度约束；润色并入同一 prompt）；
6. `--asr-model` / `model_role`（1.7B 默认、0.6B 草稿加护栏）、`--chunk-sec` 暴露；
7. 双引擎一致性只对中置信 cue（Whisper 运行时已核实，可直接 A/B）。

**C. 音频事件（拿到模型就接）**
8. FireRedVAD/AED → `AudioHints`：OP/ED 候选、空洞诊断、翻译场景；
9. 跨集音频指纹；Silero/TEN 作为 VAD fallback；
10. 短窗 FA 重对齐（SpotReAlign，带音频证据，不整集平移）。

**D. 可选重技术（有明确收益才开）**
11. ~~VibeVoice/说话人~~：已删除，不再做；
12. Demucs/RoFormer：默认关，先 A/B 原音频 vs 分离音频的 ASR 结果；
13. WhisperX/MMS FA：只在 Qwen3-FA 不可用/无 GPU 时降级。

## 6. 明确不采用 / 暂不接入

| 技术 | 结论 | 触发复核 |
|---|---|---|
| 云 ASR（MAI/Scribe/Gemini/gpt-4o） | ⛔ 付费/token，不纳入 | 无；用户要求才能重新评估 |
| Qwen-MT / 传统 MT / 百度 | ⛔ 付费或已删除 | 无 |
| pyannote community-1 | ⛔ 需 HF token（gated） | 有免费本地替代时不考虑 |
| Whisper 全系（large-v3/turbo、kotoba、faster-whisper、WhisperX） | ⛔ 用户规则：只保留本机 CUDA，不下载/不再测试 | 无 |
| Parakeet JA / ReazonSpeech v2 | ⛔ 2024 年模型，日语非领先（WER 0.344/0.348） | 无 |
| BuzzASR Japanese | ⛔ 基于 Whisper large-v3，违反约束 | 无 |
| Demucs 默认 | ⛔ 2021/22 老模型；只作 fallback | 不投入；有分离需求用 Mel-Band A/B |
| Qwen3-ASR-0.6B 草稿 | ⛔ 无日语领先证据，当前生成异常 | 修好 guard + 有 CER 数据再评估 |
| MFA | 不采用（动画专名/OOV） | 无 |
| stable-ts | 不采用（非真 FA） | 无 |
| MMS FA | 仅无 GPU 降级；NC 许可需提示 | 有用户要求无 GPU 部署 |
| FireRedASR2-LLM | 观察名单；日语无基准 | 有日语独立评测再说 |
| 固定 180s OP/ED | 不采用 | AED/指纹可用后替换 |

## 7. 验收（防“接了很多但没用”）

每接一项技术，必须同时给出：

1. 能力指标：CER/边界/覆盖率/专名 F1/翻译评分（按技术相关）；
2. 负担指标：auto/AI/human 三档条数、耗时、显存、云成本；
3. 回退：null 实现/开关关掉即回旧行为；
4. 证据：在 dev 上验证，test 只跑 release candidate（`eval/protocol.md`）；
5. 记录：本 ADR 台账状态 + `docs/tech-route-progress.md`。

**没有接缝的技术不接；没有指标的接入不算完成。**
