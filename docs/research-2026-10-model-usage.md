# 新模型用法调研：从“接上”到“用对”

> 结论：当前不是模型不够新，而是**用法只用了官方能力的一半**。
> 最大的遗漏：Qwen3-ForcedAligner 被当成“ASR 之后出 token 时间戳”的工具，
> 没有用它做**字幕文本 ↔ 音频的直接强制对齐**。

来源：
[Qwen3-ASR model card](https://huggingface.co/Qwen/Qwen3-ASR-1.7B) ·
[Qwen3-ForcedAligner](https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B) ·
[FireRedVAD](https://github.com/FireRedTeam/FireRedVAD) ·
[Mel-Band RoFormer infer](https://pypi.org/project/melband-roformer-infer/) ·
[gpt-subtrans](https://github.com/machinewrapped/gpt-subtrans) ·
[VideoLingo](https://github.com/Huanshere/VideoLingo)。

## 1. Qwen3-ASR / Qwen3-ForcedAligner

来源：本地官方 model card `models/Qwen3-ASR-1.7B/README.md`（Apache-2.0）。

官方能力：

- Qwen3-ASR-1.7B：52 语言/方言，离线+流式，**支持 Speech、Singing Voice、Songs with BGM**；
- Qwen3-ForcedAligner-0.6B：**任意文本单元**的 timestamp，**最多 5 分钟语音**，11 语言；
- `transcribe(context=...)` 的 context 是 system message，可用于**热词/上下文**；
- vLLM 已加 Qwen3-ASR hotwords 支持；Qwen3-ASR-1.7B-hf 也更新了 custom hotwords 模板；
- `Qwen3ForcedAligner.align(audio, text, language)` 可直接吃“文本+音频”，返回字符/词时间。

我们现在的用法：

1. Qwen3-ASR 转写整段音频 → 得到 token 时间；
2. 把日文 CC 文本映射到 ASR token（自写 mapper）；
3. 用映射结果修 cue 时间。

问题：

- ASR 文本错/漏，会直接导致映射失败（CER 0.26–0.36，coverage 0.42–0.46）；
- 我们绕了一圈：**CC 文本本来就是可信文本，为什么不用它直接对齐音频？**
- 官方 aligner 支持任意文本、5 分钟；完全可以把“CC 文本 + 音频”喂给 FA，
  直接得到每个 cue 的边界，不需要 ASR 文本当桥。

**最值得做的实验（P0）**：CC 文本 → Qwen3-FA 直接对齐。

- 按 60s/5min 窗口切音频；
- 每个窗口拼接对应的 CC cue 文本，调用 `align(audio_window, cc_text)`；
- 用字符时间戳回填每个 cue 的 start/end；
- 对比现有 ASR-token 映射：P50/P90、可靠 P90、coverage、空洞；
- 这是“把新模型用对”，不是再写 mapper。

### 1.1 已做的小探针（EP001 前 60s，12 条 CC）

| 输入 | 第一个 token | CC 第一条 start | 结论 |
|---|---:|---:|---|
| 原始 60s 音频 → FA | 0.88s | 4.47s | 音乐/前奏把文本拉早 3.6s |
| FireRedVAD speech 遮罩 → FA | **4.40s** | 4.47s | 误差 ~70ms |

这说明：

- **不是 FA 不行，是喂了带 BGM 的音频**；
- 正确用法是 **FireRedVAD VAD 先取 speech 区，再让 Qwen3-FA 对齐 CC 文本**；
- 现有 ASR-token 映射的第一 token 也是 0.88s，说明它同样被音乐带偏；
- 这是一条真正的“新模型组合用法”，不是代码端小修。

注意：最后两条（武之内空/石田ヤマト）仍有提前，说明 speech 遮罩/文本拼接还要
按 cue 边界处理；全窗口只是探针，不能直接宣布解决。

### 1.2 全 EP001 结果：覆盖/CER 变好，但边界没变好

| 方案 | CER | P50 | P90 | 可靠 P90 | coverage | zero tokens | collapse gap |
|---|---:|---:|---:|---:|---:|---:|---:|
| 60s ASR→token 映射 | 0.3705 | **129ms** | **1897ms** | **424ms** | 0.418 | 9.4% | 229s |
| CC→FA 60s 分组 | **0.0366** | 200ms | 3179ms | 626ms | 0.667 | 22.3% | 196s |
| CC→FA 单 cue ±2s | **0.0366** | 836ms | 2000ms | 1840ms | **0.961** | 23.4% | 117s |

结论：

- 直接 FA 把 **coverage 从 .418 提到 .667/.961**，CER 降到 .0366，说明“CC 文本直接对齐”
  在文本/覆盖上是对的；
- 但 **P50/P90/可靠 P90 没有变好**，单 cue 甚至更差；Qwen3-FA 在日语 CC/短窗上
  的边界精度不足以替代现有 ASR-token 映射；
- **不接入默认流程**。下一步若要继续，应研究“FA 只做候选/置信度，不单独决定边界”，
  或换更合适的对齐窗口/文本规范化，而不是硬上。

## 2. Qwen3-ASR 的 context / hotwords 没用

官方 `transcribe(context=...)` 是 system message；vLLM 有 hotwords 支持。
我们的 `ASR_CONTEXT_FILE` 仍是死配置，ASR context 基本为空。

正确用法：

- 把 series glossary 的日文词（アグモン/タケル/デジモン…）作为 hotwords 放进 context；
- 对比空 context 与 glossary context 的 CER、专名正确率；
- 这比在翻译后 repair 名字更前置，属于“用模型自带能力”。

### 2.1 60s 探针（EP001 开头）

| context | 输出专名 |
|---|---|
| 空 | 柳対一、竹ノ内空、石田山と |
| series glossary | **八神太一**、竹ノ内空、**石田ヤマト** |

3 个专名修好 2 个；`竹ノ内空` 仍错（模型把它读成竹ノ内）。已把 context 接进
`Fixer._transcribe_for_alignment`；全 EP001 的 CER/专名 A/B 已跑：

| context | CER | coverage | P50 | P90 | 可靠 P90 |
|---|---:|---:|---:|---:|---:|
| 空 | 0.3705 | 0.418 | **129ms** | **1897ms** | **424ms** |
| series glossary | **0.3436** | **0.441** | 148.5ms | 2616ms | 687ms |

结论：hotwords **改善文本/覆盖**，但 **P50/P90/可靠 P90 变差**；不能用于时间轴默认。
可考虑“无 context 出时间 + context 出文本”的双路，但成本翻倍，暂不接。

## 3. FireRedVAD/AED：官方用法是事件检测，不是切块

来源：`models/FireRedVAD/README.md`（Apache-2.0）。

- VAD F1 97.57，优于 Silero/TEN/FunASR/WebRTC；
- AED 直接输出：
  `event2timestamps: {speech, singing, music}`、`event2ratio`；
- 阈值：speech 0.4、singing 0.5、music 0.5；
- 官方推荐 16k mono PCM。

我们现在的用法：

- `--audio-hints` 只写 JSON，没有消费者；
- 试过 AED speech 边界切块：CER/coverage 变好，但 P90/可靠 P90 变差，已拒绝。

正确用法：

- **OP/ED 候选**：singing 富集 + 头尾位置 → 标记歌词，不按对白翻译；
- **speech 区/空洞分类**：speech 主导空洞才进修复，不门控删条；
- 不要再用它强行改 chunk 边界（已实测负收益）。

## 4. Mel-Band RoFormer：官方有现成推理包，不用自己写

来源：PyPI `melband-roformer-infer`（MIT，inference-only，CLI+API）。

- 默认模型 `melband-roformer-kim-vocals`，输出 vocals/instrumental；
- 支持 `--input_folder`、`--store_dir`；
- 本地已有单文件 safetensors，但官方包用 `.ckpt`+config；优先用包，不重写 STFT/模型。

先验证必要性：

- Qwen3-ASR 官方支持 **Songs with BGM**；
- 先测“原始音频 vs 分离人声”对 OP/ED/正文 CER、coverage 的影响；
- 只有提升才接；否则 Mel-Band 只作为可选预处理，不进默认。

## 5. 翻译：成熟方案已经给了 prompt 结构

来源：[gpt-subtrans](https://github.com/machinewrapped/gpt-subtrans)、
[VideoLingo](https://github.com/Huanshere/VideoLingo)。

成熟结构：

- 逐条 Original/Translation 成对 + 禁止并条；
- `<summary>` / `<scene>` 批间上下文；
- `term::canonical` 术语块；
- retry 指令处理漏条/并条；
- VideoLingo：直译 → reflection → free translation。

我们已做：

- `ja→zh` 术语对（EP003 名字段 0 违规）；
- 54 集 series glossary（18 条，14,605 对齐对）；
- `--repair` 只修问题条；
- 10 条 MQM 抽检 10/10 可接受。

不要继续做：

- 裸 future context（已实测 chrF 43.33→36.25，负）；
- 无证据的 prompt 花活；
- 自己重写翻译批处理/重试/解析（成熟项目已有）。

下一步：

- 用 batch `<summary>`/scene 做小样本 A/B；
- 扩 series glossary，不做复杂 memory 框架；
- 继续 MQM，不用 chrF 当唯一目标。

## 6. 修正后的优先级（只做“用对模型”）

1. **P0：CC 文本 → Qwen3-FA 直接对齐**（最大时间轴杠杆）。
2. **P0：Qwen3-ASR context/hotwords**（series glossary 前置到 ASR）。
3. **P1：FireRedVAD AED 做 OP/ED/空洞消费**（不切块）。
4. **P1：Mel-Band A/B**（用 `melband-roformer-infer`，先验证必要性）。
5. **P2：翻译 summary/series memory/MQM**（不重写翻译器）。
