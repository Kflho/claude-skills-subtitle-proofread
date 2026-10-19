# 流程删除审计（2026-10）

> 原则：先证明“没用”，再删。删除前必须满足：无消费者，或 step-off 在 EP001–003
> 无指标收益；删除后测试全绿、文档留证据。

## 1. 消费者审计（代码层）

| 步骤 | 输出去哪 | 消费者 | 结论 |
|---|---|---|---|
| `--audio-hints` / `attach_audio_hints` | `<EP>.audio_hints.json` | 仅 `cc_fa_align.py` 实验脚本读取；生产流程无读取 | **write-only**，先移出默认流程 |
| `future_context` | `_translate_batch` 的 ja_future | 仅 prompt | 已实测 chrF 43.33→36.25，**负收益** |
| `chunk_boundaries` | worker 切块 | 仅 `aed_chunk_transcribe.py` 实验 | AED 边界实测 P90 变差，**无生产消费者** |
| `--asr-global-offset` | 整集偏移 | opt-in CLI | 用户已确认自估偏移是自证，**不该用于默认** |
| `cc_fa_align` / `cc_fa_per_cue` | ASRResult JSON | 仅 benchmark 实验 | 覆盖/CER 好，P90/可靠差；**不是生产步骤** |
| `vad_concat_asr` | ASRResult JSON | 仅 benchmark 实验 | EP002/3 CER .503/.606，**不泛化** |
| `--repair` | 输出 SRT | 生产翻译链路 | 有用，但一遍可能不够（EP001 需 2 遍）；**保留待优化** |
| 60s ASR + token 映射 | 时间轴 | 生产 | 官方直出更差；**保留** |
| ID 协议 + 按批 glossary | 翻译输出 | 生产 | 修复错位/降 chrF 回归；**保留** |
| review worklist/converter | ai_review_fixes | 生产 review | 有用；保留 |

## 2. 删除候选

### A. 已有负证据 + 无消费者：可删

1. `--future-context`（prompt 模板、CLI、测试）：负收益，无其它消费者。
2. `cc_fa_align.py` / `cc_fa_per_cue.py`：只服务实验；结论已写进
   `docs/research-2026-10-model-usage.md`。
3. `vad_concat_asr.py`：EP001 单集假阳性，EP002/3 文本崩；只服务实验。
4. `aed_chunk_transcribe.py` + `chunk_boundaries` 传输：AED 边界拒绝；无生产消费者。
5. `--asr-global-offset`：自证偏移；删除注册/默认路径，保留文档说明。

### B. 先移出默认、保留独立入口

1. `--audio-hints`：生产只写文件、无读取；从 `run_all`/`fix_by_asr` 默认链移除，
   保留 `scripts/audio/detect_events.py` 独立实验入口。
   等 Phase 2 证明 OP/ED/speech 消费者有指标收益，再决定是否恢复。
2. ASR hotwords context：文本/覆盖有收益但时间轴变差；先只保留在
   `Fixer._transcribe_for_alignment` 的可选开关（默认关），做“时间/文本双路”实验；
   无端到端收益就删。
3. 旧 VAD/Whisper 步骤在 `--asr qwen3` 下：做 step-off（跳 VAD/Whisper）对照；
   无收益再删。

### C. 保留

- 60s chunk + ASR/FA（官方直出明显更差）；
- token 映射（CC→FA 边界更差）；
- 翻译 ID 协议、按批 glossary、QA→repair（但 repair 需测两遍）；
- review worklist → converter → apply；
- series glossary 验证器。

## 3. 删除前必做的测试

1. `--audio-hints` on/off：EP001–003 指标应完全不变（write-only）；确认后移出默认。
2. `future_context` on/off：已有负结果，做一次 3 集确认后删。
3. `--asr-global-offset`：不跑默认；确认无调用方后删。
4. CC→FA / VAD concat / AED：已有负结果，删除实验脚本；文档保留。
5. context：空 vs hotwords，对比时间轴/文本；如果只改善文本，评估“文本修复专用”
   是否值得保留，否则删。
6. 旧 VAD/Whisper：`--asr qwen3` 下跳/不跳，比较时间/文本/空洞；无收益删。

## 4. 删除后的验收

- 全量测试通过；
- 默认流程不引用被删除模块；
- 3 集指标不回退；
- 文档记录“为什么删、删前证据”。
