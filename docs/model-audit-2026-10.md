# 模型技术审计与下载清单（2026-10）

> **目的**：下载前先回答“这些技术真的新、真的领先吗”。
> **结论**：只有 **FireRedVAD** 是“新且明确领先、值得马上下载”的；
> **Qwen3-ASR-1.7B + FA 已在本机，仍是日语媒体 ASR/对齐第一梯队**，不用重下；
> **VibeVoice-ASR** 是唯一值得做单集实验的可选领先模型（13–17 GB，重）；
> 其余（Parakeet JA、ReazonSpeech、BuzzASR、WhisperX、pyannote、Sortformer v1、Demucs）
> 要么旧、要么不是日语领先、要么被许可/环境挡住。**Whisper 系按你的要求不下、不测。**
> 下载**尚未开始**，等你确认。

## 1. 判定标准

1. **发布时间/维护**：2025 年以前的基本判旧；2026 年发布优先。
2. **独立基准**：优先第三方日语/媒体实测，不只看模型卡自评。
3. **域匹配**：动画对话、重叠、BGM、专名；纯朗读/新闻基准只作参考。
4. **本地可行性**：16 GB VRAM、Windows、许可可随 skill 分发、无需国外 token。
5. **是否被更新模型超过**：有更新的同域模型且指标更好，就换。

## 2. 判定表

| 技术 | 发布/更新 | 关键证据 | 判定 | 动作 |
|---|---|---|---|---|
| **Qwen3-ASR-1.7B** | 2026-01-28 | 日语媒体实测 WER **0.185** / CER 0.140（9 模型第一）；HF 115 万下载 | **当前领先** | 已有，保留 |
| Qwen3-ASR-0.6B | 2026-01-28 | 无日语独立基准；本机试跑 10 分钟未完成 EP001（疑似循环） | 未证明、当前不可用 | 已有，暂不接 |
| **Qwen3-FA-0.6B** | 2026-01-28 | 日语 AAS **42.2 ms**（raw）、81.3 ms（300s）；NFA/WhisperX 无日语数 | **日语对齐领先** | 已有，保留 |
| **FireRedVAD + AED** | 2026-02/03 | FLEURS-VAD-102 F1 **97.57**，超过 Silero 95.95 / TEN 95.19 / FunASR 90.91 / WebRTC 52.30；支持 speech/singing/music | **新且领先（自评，需本机复测）** | **P0 下载** |
| VibeVoice-ASR-7B | 2026-01-21 | 60 分钟单次、Who/When/What、热词、50+ 语言、MIT；HF 68.7 万下载 | 新、差异化领先；**动画域未验证** | P1 可选实验 |
| Parakeet TDT 0.6B JA | 2024-05（2025-02 更新） | 被 2025-08 的 v3 取代；日文本体非领先 | **旧** | 不下 |
| Parakeet TDT 0.6B v3 | 2025-08 | 快（RTF 0.003），日语 WER **0.344**；模型卡语言列表无日语 | 速度型，非领先 | 不下 |
| ReazonSpeech nemo-v2 | 2024-01 | 日语 WER 0.348；两年未更新 | **旧** | 不下 |
| BuzzASR Japanese | 2026-09-08 | FLEURS+CV CER 21.08，但**基于 Whisper large-v3**、无动画域 | 新但违反 Whisper 约束 | 不下 |
| pyannote community-1 | 2025-04 | 5.2M 下载、成熟；需 HF 账号 token（gated） | 外国/gated，暂不配 | 不下 |
| Sortformer v1 | 2024-12 | 4spk 离线；CC-BY-NC | **旧 + NC** | 不下 |
| Sortformer v2.1 | 2025-10 | 流式 4spk；许可 "other"（NVIDIA） | 较新但 NC 风险 | 备选，不默认 |
| Demucs htdemucs | 2021/2022 | 音乐分离老基线；RoFormer 之后已非 SOTA | **旧** | 降级为 fallback |
| Mel-Band RoFormer | 2023 | 人声分离强、MIT GGUF 0.46GB | 比 Demucs 新，仍可用 | P2 可选 |
| BS-RoFormer | 2023 | 分离 SOTA 系；GGUF 4.9GB，许可不明 | 可选但重/许可待查 | 暂不下 |
| Whisper large-v3/turbo | 2023/2024 | 日语 WER 0.218，仍强；但你明确不下载/不测试 | 按用户规则排除 | 保留本机 CUDA，不下载 |
| WhisperX / MMS FA / MFA / stable-ts | 2022–2023 | Whisper 依赖 / NC / 专名差 / 非真 FA | 淘汰 | 不下 |
| 云 ASR（MAI/Scribe/Gemini/gpt-4o） | 2025–2026 | 付费/国外 token | 不纳入 | 不下 |
| FireRedASR2-LLM | 2026-02/03 | 中文/方言 SOTA，支持日语但无日语基准 | 观察名单 | 暂不下 |

## 3. 下载清单

### P0｜必下（小、明确领先）

**FireRedVAD**
- 权重：`FireRedTeam/FireRedVAD`（HF，0.01 GB，Apache-2.0）；
  国内镜像：`xukaituo/FireRedVAD`（ModelScope）。
- 代码：`github.com/FireRedTeam/FireRedVAD`（DFSMN，0.6M 参数）+ PyPI `fireredvad` 0.0.2。
- 目标：`models/FireRedVAD/`；依赖装进独立 `.venv-audio`（torch 不污染主环境）。
- 验证：`vad.py`/`aed.py` 跑一段动画音频，检查 speech/singing/music 时间线；
  再对 EP001 与现有对齐空洞对比。
- 为什么是它：2026-03 发布，唯一同时覆盖 VAD + AED、且公开 F1 超过 Silero/TEN/FunASR；
  0.6M 参数、CPU 可跑，适合当“音频事件提示”，不参与门控。

### P1｜可选实验（先单集验证收益，再决定）

**VibeVoice-ASR-BitNet**（或 fp16）
- 权重：`microsoft/VibeVoice-ASR-BitNet`（12.96 GB，MIT）或 `microsoft/VibeVoice-ASR`（17.35 GB）。
- 目标：`models/VibeVoice-ASR-BitNet/`；16 GB VRAM 需顺序加载，不能与 Qwen3 同时常驻。
- 验证：EP001 单集，比较“谁在说”对翻译人称/敬称的改善；不看 CER 单项。
- 风险：7B、BitNet/量化运行时未定；动画重叠喊叫域未验证；不设默认。
- 若 OOM/收益不明显：不进入主链路，保留 Qwen3 + 字幕边界。

### P2｜可选（有明确需求再下）

**Mel-Band RoFormer（人声分离）**
- `Kijai/MelBandRoformer_comfy`（1.37 GB，许可待确认）或 MIT GGUF 0.46 GB；
  用 `audio-separator` 之类的推理封装，先做“原音频 vs 分离音频”的 ASR A/B。
- Demucs 仅保留为已有 fallback，不再投入。

### 不下（明确淘汰/排除）

- **Whisper 全系**（large-v3/turbo、kotoba、faster-whisper、WhisperX）——按你的要求，
  只保留本机 whisper.cpp CUDA，不下载、不再测试。
- Parakeet JA、ReazonSpeech v2——2024 年模型，日语非领先。
- BuzzASR——基于 Whisper，违反约束。
- pyannote community-1——gated/外国 token，暂不配置。
- Sortformer v1、MMS FA、MFA、stable-ts——旧/NC/不适合。
- 云 ASR、Qwen-MT、百度——付费/已删。
- Qwen3-ASR-0.6B——本机已有，但没有日语领先证据且当前生成异常，不下载、不接。

## 4. 关键证据（可复核）

- Qwen3-ASR 日语实测（2026-05 更新）：
  [Neosophie benchmark](https://neosophie.com/en/blog/20260226-japanese-asr-benchmark)
  — Qwen3-1.7B WER 0.185 / CER 0.140 / RTF 0.036，9 模型中第一；
  Whisper 0.218、Voxtral 0.239、Granite 4.1 0.281、Parakeet v3 0.344、Reazon 0.348、kotoba 0.534。
- Qwen3-ASR / FA 模型卡与发布时间：
  [Qwen3-ASR-1.7B](https://huggingface.co/Qwen/Qwen3-ASR-1.7B)、
  [Qwen3-ForcedAligner-0.6B](https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B)。
- FireRedVAD 指标与发布：
  [模型卡](https://huggingface.co/FireRedTeam/FireRedVAD)、
  [FireRedASR2S 论文](https://arxiv.org/abs/2603.10420)。
- VibeVoice：
  [模型卡](https://huggingface.co/microsoft/VibeVoice-ASR)（2026-01，MIT，60min，Who/When/What）。
- Parakeet/Reazon 时间：
  [parakeet-tdt_ctc-0.6b-ja](https://huggingface.co/nvidia/parakeet-tdt_ctc-0.6b-ja)（2024-05）、
  [reazonspeech-nemo-v2](https://huggingface.co/reazon-research/reazonspeech-nemo-v2)（2024-01）。
- BuzzASR 基于 Whisper：
  [BuzzASR/japanese](https://huggingface.co/BuzzASR/japanese)（base_model: openai/whisper-large-v3）。
- pyannote / Sortformer：
  [community-1](https://huggingface.co/pyannote/speaker-diarization-community-1)、
  [Sortformer v2.1](https://huggingface.co/nvidia/diar_streaming_sortformer_4spk-v2.1)。

## 5. 对现有方案的影响

- **保留**：Qwen3-ASR-1.7B + Qwen3-FA；Whisper 本机 CUDA 只作已有 fallback，不再下载/测试。
- **新增**：FireRedVAD/AED → `AudioHints`（OP/ED、空洞诊断、翻译场景）。
- **降级**：0.6B 草稿、Demucs、Sortformer v1、Parakeet/Reazon 从计划中移除。
- **可选**：VibeVoice（说话人/热词）、Mel-Band RoFormer（分离），都必须先单集 A/B。
- **观察**：FireRedASR2-LLM（2026-02，中文强；日语无基准）、Voxtral Mini 4B（更新但更差）。

## 6. 下一步

1. 你确认后，先下 **P0 FireRedVAD**（权重 + 代码 + `.venv-audio`），跑 EP001 验证。
2. 若同意，再下 **P1 VibeVoice-BitNet** 做单集“角色归属是否改善翻译”实验。
3. 分离（P2）暂缓：等 ASR/翻译主链路稳定，再决定是否值得。
4. 下载完成后更新 `ADR-011` 台账与 `docs/tech-route-progress.md`，把“已下载/已验证/已淘汰”分开记。
