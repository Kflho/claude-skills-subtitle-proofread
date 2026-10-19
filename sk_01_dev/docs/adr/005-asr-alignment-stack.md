# ADR-005: 2026 字幕技术栈 — ASR-first + 强制对齐

- **Date**: 2026-10-18
- **Status**: 🟡 Proposed
- **Context**: 现行音频链路是 `WebRTC VAD → Whisper(kotoba-v2.0) → 时间重叠匹配`。在 193 集动画素材上表现为：VAD 漏检/音乐误判、长 cue 合并、时间重叠错配、Whisper 音乐段幻觉。公开基准显示 kotoba 在自然对话上 WER 0.534，同场 Qwen3-ASR-1.7B 为 0.185；WebRTC VAD 2026 基准 F1 52.30、漏检 64.15%。根因不是模型不够大，而是「VAD 门控 + 段级时间戳 + 启发式匹配」的误差级联。

## Decision

1. **默认 ASR**：`Qwen3-ASR-1.7B`（Apache-2.0，`context` 注入 glossary，`return_time_stamps=True`）；`0.6B` 用于快速/草稿；`Whisper large-v3-turbo` 保留 fallback。
2. **默认对齐**：`Qwen3-ForcedAligner-0.6B`（Apache-2.0，日语 AAS 42.2ms，字符/词级时间）。
3. **VAD**：`FireRedVAD`（Apache-2.0，F1 97.57，AED 支持 speech/singing/music）；VAD/AED **只作提示**，不删条、不定修复区；WebRTC 废弃。
4. **Cue 映射**：用 ASR token 时间轴 + 全局文本对齐（时间粗锚点 + SequenceMatcher/带状 DP）替代 `match_whisper_to_cues()` 的区间重叠/±3s 宽窗。
5. **可选**：`VibeVoice-ASR-7B`（MIT，60min，hotwords + diarization）做说话人/热词模式；云 `MAI-Transcribe-2` / `Scribe v2` / `Gemini 3.5 Transcribe` 只做难例兜底。
6. **翻译**：LLM 为主，Qwen-MT 可做批量草稿；百度翻译降级为可选 fallback。
7. **专名**：glossary-first，ASR 阶段通过 `context`/hotwords 注入，再进翻译。

## Consequences

- ✅ 误差级联被拆开：ASR、时间轴、文本对齐各自可测/可替换
- ✅ 时间轴从「段级」提升到「字符级」，长 cue 可切、边界可校
- ✅ 专名在 ASR 阶段就被约束，减少事后替换
- ✅ 依赖隔离在独立 venv/worker，主环境保持纯 Python 测试
- ⚠️ Qwen3 1.7B 比 kotoba 慢约 2–4 倍；193 集约 6–12h，需夜间批处理
- ⚠️ Qwen3 不暴露 logprob；置信度必须重建（对齐一致度 + 双引擎 + LLM）
- ⚠️ 3 分钟对齐分块有边界抖动风险，需要重叠/连续性检查
- ⚠️ VibeVoice 7B 在 16GB 卡上需量化/分段，暂不做默认

## Alternatives rejected

| 方案 | 拒绝原因 |
|---|---|
| 继续用 kotoba-v2.0 | 朗读域微调；自然对话 WER 0.534，域错配 |
| 换更大 Whisper | 仍只有段级时间戳；幻觉/长 cue 根因不变 |
| MMS FA 做日语 | uroman 把汉字按中文读音转写；CC-BY-NC 4.0 |
| WhisperX 做默认对齐 | 段内对齐，段错则对齐错；日语依赖第三方 wav2vec2 |
| Gemini Pro 原生音频定轴 | 日语长音频 tcpWER 81.41，时间戳不可用 |
| 百度翻译默认 | 通用 MT，无上下文/术语/语气控制 |
| demucs 默认 | 伪影/延迟；Qwen3 对 BGM 更鲁棒，先验证原音频 |

## Follow-up

- P0：12 集金标 + 基线指标。
- P1：`qwen3_worker.py` + `--asr qwen3`；A/B CER。
- P2：`qwen3_fa.py` + `cue_mapper.py` + `align_repair.py`；边界 P50/P90。
- P3：`FireRedVAD` 适配；VAD 降级为提示。
- P4：glossary-first + 翻译 prompt；百度默认下线。
- 详细方案见 [`docs/tech-route-2026.md`](../tech-route-2026.md)。
