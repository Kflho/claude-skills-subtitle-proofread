# 字幕 Skill 技术路线 2026 修订方案

> **日期**：2026-10-18
>
> **范围**：日语动画字幕的 ASR、强制对齐、VAD、说话人分离、翻译、专名链路
>
> **结论先行**：把「Whisper + WebRTC VAD + 时间重叠匹配」换成「**Qwen3-ASR-1.7B + Qwen3-ForcedAligner-0.6B + FireRedVAD（只作提示）+ 全局文本对齐**」。Whisper 保留为 fallback；VibeVoice-ASR 作为可选的「说话人 / 热词」模式；云 API（MAI-Transcribe-2 / Scribe v2 / Gemini 3.5 Transcribe）只做难例兜底。**MMS FA 不适合日语字幕，不采用。**
>
> **证据等级**：本文标注 `[A]` 官方文档/代码、`[B]` 第三方公开基准、`[C]` 架构推断。所有公开基准都需要在我们的 193 集素材上复测，不能直接当结论。
>
> **2026-10-18 修订**：翻译改为 **LLM-only**（百度路径已删除、不再维护/测试）；
> **免费原则**：只考虑免费/本地技术，云 ASR、Qwen-MT、pyannote gated 等需 token/付费的
> 服务不纳入；**翻译是唯一 LLM 例外**（用户已配置 DeepSeek，`~/.claude/settings.json`）。
> 流程以 [`adr/010-asr-first-pipeline.md`](adr/010-asr-first-pipeline.md) 的 **ASR-first**
> 设计为准；所有候选技术的接入位与现状见
> [`adr/011-technology-integration-map.md`](adr/011-technology-integration-map.md)。
> 本文保留调研背景，冲突处以 ADR-010/011 和 `CONTEXT.md` 核心原则为准。

---

## 0. 一页结论

### 0.1 换什么

| 层 | 现在 | 目标 | 优先级 | 一句话理由 |
|---|---|---|---|---|
| ASR | `kotoba-whisper-v2.0 q5_0` | **Qwen3-ASR-1.7B** 默认；0.6B 做快速/草稿；`large-v3-turbo` 保留 fallback | **P0** | kotoba 是「朗读/新闻」域微调，动画自然对话公开 WER 0.534，同场 Qwen3 0.185 `[B]` |
| 强制对齐 | 无；`match_whisper_to_cues()` 时间重叠 + ±3s 宽窗 | **Qwen3-ForcedAligner-0.6B** + 全局文本对齐 | **P0** | Whisper 只有段级时间戳；要 cue/字符级边界必须做 FA。Qwen3-FA 日语 AAS 42.2ms `[A]` |
| VAD | `webrtcvad`（2010 年代 GMM） | **FireRedVAD**（语音/歌唱/音乐 AED） | **P1** | WebRTC 在 2026 多语基准 F1 52.3、漏检 64.15%；FireRed 97.57、漏检 3.62% `[B]` |
| 置信度 | Whisper 的 `avg_logprob` / `no_speech_prob` / `compression_ratio` | **对齐一致度 + 跨引擎一致性 + 语言/重复检测 + LLM 可读性** | **P1** | 这些指标是 Whisper 特有的，Qwen3 不暴露 logprob；不能把旧阈值硬套 |
| 人声分离 | demucs htdemucs，文档「推荐」 | **默认关**；只在病态 BGM 下试 BS-RoFormer / Mel-Band RoFormer | **P2** | 分离有伪影和延迟；Qwen3 本身对 BGM/歌曲更鲁棒；先验证再开 |
| 翻译 | 百度翻译 + LLM 双路径 | **LLM-only**（百度已删除，ADR-010）；Qwen-MT 只做批量草稿 | **P1** | 百度是通用 MT，无上下文/术语约束；动画台词的语气、敬称、专名需要 LLM |
| 专名 | 翻译前才生成词表 | **glossary-first**：先建词表，作为 ASR `context` / 热词，再进翻译 | **P1** | 在 ASR 阶段就把人名/术语喂进去，比事后替换更有效 |
| OP/ED | 固定 180s / 相似度 | **FireRedVAD AED + 跨集音频指纹 + 字幕重复度** | **P2** | 固定窗口已经踩过坑；AED 能直接给 speech/singing/music 时间线 |
| 切片重跑 | `whisper_spot_fix.py` | 改为「重新对齐 + 重新切段」；ASR 不必重跑 | **P2** | 根因是段级时间戳/长 cue 合并，不是模型没跑对那 9 秒 |

### 0.2 不换什么

- **3-Phase 编排**（SCAN → FIX → UNIFY/DELIVER）保留。问题在 FIX 内部的音频层，不在编排层。
- **JSON 数据契约、`report-as-database`、git CoW 写前备份、`never downgrade`** 保留。
- **Jamdict/JMdict 词典过滤** 保留；它没过时，仍适合做「普通词 vs 专名」的粗筛。
- **LLM 翻译 + 上下文** 保留；要改的是 prompt、术语注入和评测，不是推翻。
- **测试分层**（Tier 1 纯函数 / Tier 2 tempfile / Tier 3 mock 外部二进制）保留；新增 ASR/对齐模块继续按这个分层写。

### 0.3 预期收益与代价

| 指标 | 现状（公开基准/经验） | 迁移目标 | 代价 |
|---|---|---|---|
| 日语自然对话 CER/WER | kotoba 0.534；Qwen3 0.185 `[B]` | 自有金标上 CER 相对下降 **≥15%** | 1.7B 比 kotoba 慢约 2–4 倍 |
| cue 边界误差 | 时间重叠匹配，误差可达 0.5–3s | P50 **≤100ms**，P90 **≤300ms** | 需要 FA + 文本对齐新模块 |
| 人工审查条数 | 每集数十条 | 相对下降 **≥25%** | 需要更细的诊断/置信度 |
| 幻觉/重复 cue | Whisper 在音乐/噪声段高发 | 相对下降 **≥50%** | Qwen3 也会「空耳」，需上下文/双引擎 |
| 吞吐 | kotoba q5 约 36× 实时 | ~~1.7B 约 7–12× 实时~~ **实测 3.4–4.8× 实时（RTF 0.207–0.295）**；193 集约 **17–24h** | 比预估慢约 2×；0.6B 热态快 1.5×，整集数据反常（见 ADR-008） |
| 显存 | <2GB | 1.7B ASR + 0.6B 对齐约 5–6GB | 16GB 卡够用；VibeVoice 7B 另算 |
| 云成本 | 百度免费额度 | 兜底 $8–24 / 77h | 仅难例，默认本地 |

### 0.4 三个关键判断

1. **当前瓶颈不是「Whisper 不够大」，而是「VAD 门控 + 段级时间戳 + 时间重叠匹配」的误差级联。** 换更大的 Whisper 只能改善第一段，后面两段仍会把误差放大。
2. **动画对话和朗读新闻是两个域。** kotoba 在 JSUT/FLEURS 上还能看，在自然对话/动画上公开 WER 0.534；Qwen3-ASR 在同一组自然对话上 0.185 `[B]`。这解释了为什么「模型没坏，但修复效果不稳定」。
3. **对齐要用「文本-语音」模型，不要用 ASR 的时间戳凑。** Qwen3-FA 日语 AAS 42.2ms；MMS FA 的日语路径会被 uroman 把汉字当中文读，不适合；Gemini Pro 原生音频在长音频上的时间戳更差（日语 tcpWER 81.41 vs VibeVoice 15.41）`[B]`。

---

## 1. 旧栈审计

### 1.1 组件清单

| 组件 | 位置 | 当前实现 | 问题 | 证据 |
|---|---|---|---|---|
| ASR | `lib/whisper_backends.py`、`fix/whisper_pipeline.py` | whisper.cpp / faster-whisper / openai-whisper；默认 kotoba-v2.0 q5_0 | 域错配；段级时间戳；幻觉重复 | kotoba 自然对话 WER 0.534 `[B]` |
| VAD | `fix/whisper_pipeline.py:get_speech_timeline()` | `webrtcvad` 30ms 帧，GMM | 漏检高；音乐误判；对白垫 BGM 时不可靠 | WebRTC F1 52.30、miss 64.15% `[B]` |
| 修复区 | `build_fix_regions()` | VAD 语音段 → 统一 fix region | VAD 一错，region 就错；长语音段被跳过 | 代码 + 注释 |
| 时间匹配 | `match_whisper_to_cues()` | 区间重叠；±3s 宽窗重试；覆盖率 0.3/0.5 | 启发式；多句合并/拆分时错配；无字符级边界 | `whisper_pipeline.py:582` |
| 置信度 | `filter_low_confidence()` | `avg_logprob` / `no_speech_prob` / `compression_ratio` | 只适用于 Whisper；Qwen3 无这些输出 | `dev/technical-details.md` §6 |
| 人声分离 | `separate_vocals()` | demucs htdemucs | 2022 模型；伪影/延迟；默认推荐 | `whisper-pipeline.md` |
| 翻译 | `translate_srt.py` | LLM-only（百度路径已删除，ADR-010） | 原百度通用 MT；1 QPS；无上下文 | `lib/config.py` |
| 专名 | `nouns/*` | 翻译前扫描 → Jamdict → AI 审查 → 映射 | 事后补救；ASR 阶段没吃到词表 | `references/*` |
| OP/ED | `fix/oped_*.py` | 相似度/重复度 + 固定 180s 兜底 | 固定窗口已造成误覆盖 | `config.py` 注释 |
| 切片修复 | `whisper_spot_fix.py` | 对时间段重跑 Whisper | 根因是长 cue/时间戳，不是那几秒音频 | `SKILL.md` |

### 1.2 误差级联

```
音频（对白+BGM，多人重叠）
   │
   ▼ WebRTC VAD（漏检 64%，音乐误判）
语音时间线 ──► 修复区（多算/少算）
   │
   ▼ Whisper 段级输出（长 cue 合并、音乐段幻觉）
ASR 段 ──► match_whisper_to_cues（时间重叠 ±3s）
   │
   ▼ 错配/漏配
cue 文本替换 ──► 翻译 ──► 人工审查
```

每一层都在「猜」下一层的边界。只要 VAD 漏一段、Whisper 合并一句，后面的时间匹配就无法恢复。**正确的顺序是：先拿到可靠的 token 级时间轴，再做文本对齐和修复。**

### 1.3 代码级证据

- `lib/config.py` 已写明：

  > 「本作（对白垫 BGM）实测：Silero 硬过滤会丢掉大量真实台词（单集 243/463），webrtcvad 又把音乐判成语音。任何一处拿它们当硬门（删条、定 region、定时间码）之前，先想清楚误杀怎么办。」

  这说明团队已经意识到 VAD 不可靠，但架构仍然让 VAD 决定修复区。**新方案要把 VAD 从「门控」降级为「提示」。**

- `whisper_pipeline.py:match_whisper_to_cues()` 的 ±3s 宽窗、`align_and_fix()` 的 0.3/0.5 覆盖率，都是没有 FA 时的补丁。它们能工作，但无法给出字符级边界，也无法区分「同时间段有两句」。
- `dev/technical-details.md` 把 `avg_logprob` / `no_speech_prob` / `compression_ratio` 当作置信度核心。这套指标在 Qwen3-ASR 上不存在；迁移时必须重建置信度体系，不能把阈值照搬。
- `whisper_spot_fix.py` 的「短窗口让 VAD 更准」是绕路：真正需要的是**全局对齐**，而不是对每个可疑片段再跑一次 ASR。

### 1.4 「过时」的准确定义

这里的「过时」不只是模型年龄，而是**架构错配**：

| 技术 | 年龄 | 过时原因 |
|---|---|---|
| WebRTC VAD | 2010 年代 | GMM/手工特征；对音乐、BGM、远场差；2026 基准漏检 64% |
| kotoba-whisper-v2.0 | 2024 | 日语朗读域微调；动画自然对话域外 |
| Whisper 段级时间戳 | 2022 | 只有 segment 级，天然不适合 cue 级字幕 |
| 时间重叠匹配 | 本项目自研 | 无文本对齐；多句/重叠时错配 |
| demucs htdemucs | 2022 | 社区已有更强的 RoFormer 系；且现代 ASR 更抗 BGM |
| 百度翻译 | — | 通用 MT，无上下文/术语/语气控制 |
| Whisper logprob 置信度 | 2022 | 模型特定；不可迁移 |
| 固定 180s OP/ED | 本项目自研 | 已被实际数据打脸；应使用 AED/指纹 |

### 1.5 之前调研的方法论问题

这次不是「模型旧」，而是**选型维度错了**：

1. **用朗读/新闻基准选动画模型。** kotoba 在 JSUT/FLEURS 上不差，但动画是情绪化、重叠、带 BGM 的自然对话；域错了，WER 再低也没用。
2. **看 VAD 误报率，不看漏检率。** WebRTC 误报 2.83% 很漂亮，但漏检 64.15%；在「VAD 决定修复区」的架构里，漏检比误报致命得多。
3. **把 ASR 段级时间戳当字幕时间轴。** Whisper 的 segment 不是 cue；用时间重叠去补，永远在猜边界。
4. **用单一 WER 做决策。** 没看时间戳能力、上下文/热词、许可、显存、Windows 部署；这些在字幕场景里和 WER 一样重要。
5. **先定模型，后补对齐/专名。** 正确顺序是「音频 → token 时间轴 → 文本对齐 → 修复/翻译」，不是「先跑 ASR，再打补丁」。

这也是为什么新方案把 **ASR、FA、cue 对齐** 作为三个独立可测的层，而不是继续在 Whisper 管线上加启发式。

---

## 2. 需求与约束

| 维度 | 约束 |
|---|---|
| 语言 | 日语为主；中文/俄语翻译为辅 |
| 素材 | 193 集，单集约 24 分钟；对白垫 BGM、多人争吵、OP/ED、音效 |
| 输入 | 已有 SRT/ASS（机翻/听译）+ 视频；也支持从零生成 |
| 输出 | cue 级日文字幕 + 中文翻译 + 问题报告 |
| 硬件 | RTX 3080 Ti Laptop 16GB；Windows；Python 3.12；CUDA 12.4 |
| 运行 | 本地优先、可离线；批量夜间跑；单集可交互 |
| 质量 | 对话准确性 > 时间轴 > 抗 BGM/重叠 > 专名 > 吞吐 > 成本 |
| 维护 | 模块可替换；不让某个模型的依赖污染主环境 |

**非目标**：不做实时字幕、不做专业字幕软件的波形编辑、不做多人协作。

---

## 3. ASR 调研

### 3.1 模型矩阵（日语视角）

| 模型 | 类型/许可 | 日语公开证据 | 时间戳 | 上下文/热词 | 说话人 | 16GB 本地 | 定位 |
|---|---|---|---|---|---|---|---|
| **Qwen3-ASR-1.7B** | 开源 LLM-ASR / Apache-2.0 | 自然对话 WER **0.185**（同场第一）`[B]`；朗读 CER 5.28/26.33/8.42 `[B]` | 需 FA；可一次返回 token 时间 | **`context` 系统提示** `[A]` | 否 | 是（约 3.9GB） | **默认** |
| Qwen3-ASR-0.6B | 开源 / Apache-2.0 | 朗读 CER 8.44/34.12/11.90 `[B]`；更快 | 同上 | 同上 | 否 | 是（约 1.5GB） | 快速/草稿/双引擎 |
| **Qwen3-ForcedAligner-0.6B** | 开源 NAR 对齐 / Apache-2.0 | 日语 AAS **42.2ms**；300s 拼接 81.3ms `[A]` | 字符/词级 | 文本-语音对 | 否 | 是 | **默认对齐** |
| **VibeVoice-ASR-7B** | 开源 LLM-ASR / MIT | MLC 日语 WER **14.69**、DER **0.82**、tcpWER **15.41** `[B]` | 内置 | **自定义 hotwords** | **内置 diarization** | 需量化/分段 | 可选「说话人/热词」模式 |
| Whisper large-v3-turbo | 开源 / MIT | 自然对话 WER **0.218**；朗读 CER 3.88/75.55/7.22（含一次幻觉异常）`[B]` | segment 级 | prompt | 否 | 是（约 1.7GB） | fallback |
| kotoba-whisper-v2.0 | 开源日语微调 / Apache-2.0 | 自然对话 WER **0.534**（同场末位）`[B]`；朗读 CER 5.88/26.62/7.36 | segment 级 | prompt | 否 | 是（约 1.6GB） | **不再默认** |
| ReazonSpeech nemo-v2 | 开源日语 / 见模型卡 | 自然对话 WER 0.348 `[B]` | 需另做 | 无 | 否 | 是 | 新闻域备选 |
| Parakeet TDT 0.6B JA | 开源 / 见模型卡 | 朗读 CER 5.56/21.53/6.60；自然对话 WER 0.344 `[B]` | 需另做 | 无 | 否 | 是 | 速度备选 |
| MAI-Transcribe-2 | 云 API / 专有 | 单条 5 分钟日语实测细节最佳；AA-WER **2.0%**（英文为主）`[B]` | 有 | 未见公开 | <15min | 否 | 云兜底 |
| ElevenLabs Scribe v2 | 云 API / 专有 | 日语强；AA-WER **2.2%** `[B]` | 有 | 未见公开 | 有 | 否 | 云兜底 |
| Gemini 3.5 Transcribe | 云 API / 专有 | AA-WER **2.6%**；日语细节略逊 `[B]` | verbatim 有 | 未见公开 | verbatim 有 | 否 | 云兜底（注意 smart 模式互斥） |
| gpt-4o-transcribe | 云 API / 专有 | JSUT CER **5.14%**；CommonVoice 26.25% `[B]` | 有限/需查 | prompt | 否 | 否 | 云备选 |

> 表中「朗读 CER」三个数字依次为 FLEURS / CommonVoice / JSUT，来自 HEROZ 2026-08 的 11 模型对比 `[B]`。自然对话 WER 来自 Neosophie 2026-04 的 20 条真实媒体音频（含动画/剧集/综艺）`[B]`。两组数据的域不同，不能横向拼成一个总分。

### 3.2 Qwen3-ASR：为什么是默认

**能力** `[A]`：

- Qwen3-ASR-0.6B / 1.7B，52 种语言/方言（30 语言 + 22 中文方言），日语在列。
- 语言识别 + ASR 一体化；离线/流式统一；支持长音频、歌唱、带 BGM 的歌曲。
- `transcribe(audio, context=..., language="Japanese", return_time_stamps=True)`：
  - `context` 作为 system prompt，可直接放人名、术语、作品名；
  - `return_time_stamps=True` 时内部接 Qwen3-ForcedAligner；
  - 无时间戳时按 1200s 分块，有时间戳时按 180s 分块（低能量边界），再把对齐结果按 offset 合并 `[A]`。
- Apache-2.0；`qwen-asr==0.0.6` 依赖 `transformers==4.57.6`，Python ≥3.9；无 vLLM 也可用 Transformers 后端 `[A]`。

**API 版 vs 本地版** `[A]`：DashScope 的 Qwen3-ASR-Flash 有 3 分钟请求限制，官方 Toolkit 用 VAD 切成约 120s 并发调用；本地开源 0.6B/1.7B 不受 3 分钟限制（无时间戳 1200s、有时间戳 180s 分块）。我们选本地，原因是 193 集批量、隐私、无按次成本；没有 GPU 的用户可走 API。

**日语表现** `[B]`：

- Neosophie（自然对话，20 条，含动画/剧集/综艺）：
  - Qwen3-ASR-1.7B：WER **0.185**、CER **0.140**、RTF 0.036；
  - Whisper large-v3-turbo：0.218 / 0.184 / 0.013；
  - kotoba-v2.0：**0.534 / 0.495 / 0.008**（它最快，但问题不是速度，是域错配）。
  - 评语：Qwen3「会补全成完整句子、按呼吸打标点、适当删语气词但保留助词」，20 条里只有 1 条 WER >0.5，稳定性好；缺点是俚语/专名会「空耳」。
- HEROZ（朗读/CommonVoice/JSUT，A100，逐条）：
  - Qwen3-ASR-1.7B：CER 5.28 / 26.33 / 8.42；RTF 0.10–0.17；VRAM 3,886MB；
  - Whisper large-v3：4.24 / 28.61 / 7.01；
  - Cohere Transcribe：2.89 / 20.22 / 8.59（朗读域很强，但自然对话仅 0.327，不是我们的域）；
  - gpt-4o-transcribe：2.26 / 26.25 / 5.14（朗读域强，API 且时间戳能力有限）。
  - 结论：**Qwen3 不是所有日语基准都第一**；它在自然对话/动画域优势最大，在干净朗读域只是中上。我们的主场景恰好是前者。

**适配我们的理由**：

1. 域匹配：动画对话、情绪、重叠、BGM，比朗读新闻更接近 Neosophie 的测试集。
2. 有时间戳路径：官方直接接 ForcedAligner，不需要自己拼 Whisper 段。
3. 有上下文入口：`context` 可以吃 glossary，正面解决专名。
4. 许可干净：Apache-2.0，可随 skill 分发。
5. 显存友好：1.7B ASR + 0.6B 对齐约 5–6GB，16GB 卡可同时加载。

**注意事项**：

- 它倾向「写成可读的句子」，不是逐字转写；对字幕是优点，对「严格对照原音」的 QA 要接受这一点。
- 俚语/专名会「空耳」；必须用 `context` + 原文保留 + 双引擎兜底，不能盲替换。
- 3 分钟对齐分块在边界可能抖动；实现时要做 3–5s 重叠或边界连续性检查。

### 3.3 VibeVoice-ASR：可选的「说话人/热词」模式

**能力** `[A]`：

- 7B，单次最多 60 分钟，50+ 语言；一次输出 **Who（说话人）+ When（时间戳）+ What（内容）**。
- 支持 **customized hotwords / context**；支持 vLLM、Transformers、BitNet CPU、流式。
- 项目 MIT；官方明确「不建议未经测试直接商用」。

**日语证据** `[B]`（MLC-Challenge，长音频多说话人）：

| 模型 | DER | cpWER | tcpWER | WER |
|---|---:|---:|---:|---:|
| Gemini-2.5-Pro | 20.40 | 30.41 | 37.36 | 16.58 |
| Gemini-3-Pro | 59.68 | 21.96 | **81.41** | 18.47 |
| **VibeVoice-ASR** | **0.82** | **15.33** | **15.41** | **14.69** |

> 论文为避免 Gemini 长音频不稳定，把 Gemini 输入切成 240s；VibeVoice 是单次 60min。即便给 Gemini 更小的块，它的 tcpWER 仍远高于 VibeVoice。

这张表有两个信息：

1. VibeVoice 的日语长音频「谁在说/什么时候说」明显强于 Gemini Pro 原生音频；
2. **Gemini Pro 不适合做长音频时间戳**（tcpWER 81.41），不要拿它给字幕定轴。

**为什么不做默认**：

- 7B 在 16GB 卡上要量化/分段；60 分钟单次上下文的 KV cache 也不小 `[C]`。
- 它的强项是会议/播客式多说话人；动画的夸张声线、重叠喊叫、BGM 是另一个域，DER 未必有 MLC 那么漂亮。
- 输出是结构化 rich transcription，接入现有 cue 模型需要额外解析。

**建议**：作为 `--diarize` / `--speaker-mode` 的可选引擎，先在一集上对比「角色归属」是否真的改善翻译（特别是人称/敬称），再决定是否纳入默认。

### 3.4 Whisper 系：保留，但别再当唯一

| 模型 | 优点 | 问题 | 用法 |
|---|---|---|---|
| large-v3-turbo | 快、VRAM 低、朗读域仍强；自然对话 WER 0.218 | 幻觉重复；段级时间戳；音乐段易编 | **fallback**，不是默认 |
| large-v3 | 比 turbo 稳一点 | 慢、重；同样段级时间戳 | 备选 |
| kotoba-v2.0 | 日语朗读域 CER 不错；速度极快 | 自然对话 WER 0.534；重复/漏词 | **从默认移除**；仅保留兼容 |

Whisper 的 `avg_logprob` / `no_speech_prob` / `compression_ratio` 仍然有价值，但只在 Whisper 路径内使用；新路径要用新置信度体系。

### 3.5 云 API：国内可选、国外不纳入（2026-10-18 修订）

> 成本原则：优先本地/免费；需要 token 只接受国内服务（DashScope/Qwen-ASR、讯飞、腾讯等），
> 且不默认启用；国外服务（MAI/Scribe/Gemini/gpt-4o）暂不配置。本地 Qwen3/Whisper
> 足够覆盖当前素材，没有云兜底。

| 服务 | 日语 | 时间戳/说话人 | 价格（参考） | 限制 | 适合 |
|---|---|---|---|---|---|
| **MAI-Transcribe-2** | 单条 5 分钟实测细节最佳；AA-WER 2.0% `[B]` | 有；diarization <15min | $0.10/h（2026 年底前导入价）`[B]` | 公开预览；2h/250MB；Azure | 难例、批量兜底 |
| **Scribe v2** | 强；AA-WER 2.2% `[B]` | 有；实体/音频事件 | $0.22/h `[B]` | 云；付费 | 难例、需要事件标签 |
| **Gemini 3.5 Transcribe** | 中上；AA-WER 2.6% `[B]` | verbatim 有词级时间戳/说话人；**smart 模式与二者互斥** | 约 $0.31/h `[B]` | 云；模式限制 | 兜底/语义整理 |
| gpt-4o-transcribe | 朗读域强（JSUT 5.14%）`[B]` | 时间戳能力有限 | 按 token | 云 | 朗读/清晰素材 |

**成本感**：193 集 × 24 分钟 ≈ 77 小时；MAI 约 $7.7，Scribe 约 $17，Gemini 约 $24。**云不贵，贵的是隐私和一致性**。默认本地，难例才走云；不要把 193 集全送云。

### 3.6 日语基准的正确读法

- **域 > 排名**：Neosophie 的自然对话（含动画）和 HEROZ 的朗读/CommonVoice 是两种任务。kotoba 在后者还能看，在前者垫底；Qwen3 反之更均衡。
- **样本小**：Neosophie 20 条、HEROZ 每个数据集约 10 分钟；1 个点以内的差距不要当真。
- **WER 对日语不友好**：需要 MeCab/Sudachi 分词；CER 更稳。看专名还要看 noun F1，不只看 CER。
- **时间戳是独立能力**：ASR WER 低不等于时间戳准。Gemini Pro 的 WER 不差，但 tcpWER 81.41 说明时间戳完全不能用于字幕。
- **云 API 的「最强」是综合分**：MAI 的日语细节最好是在一条 5 分钟、2 说话人、单评委的测试里；不能当普遍结论。

### 3.7 结论

- **默认**：Qwen3-ASR-1.7B（质量）+ Qwen3-ForcedAligner-0.6B（时间轴）。
- **快速/草稿**：Qwen3-ASR-0.6B；或 1.7B 与 0.6B 两个 ASR 引擎，只在分歧 cue 上跑第二遍。
- **可选**：VibeVoice-ASR-7B（说话人/热词），先在一集上验证。
- **兜底**：Whisper large-v3-turbo；云 MAI/Scribe/Gemini 3.5。
- **移除默认**：kotoba-v2.0、WebRTC VAD、百度翻译默认路径。

---

## 4. 强制对齐调研

### 4.1 为什么必须做 FA

字幕的最小单位是 **cue**，不是 Whisper 的 segment。没有 FA 时，我们只有：

- Whisper 的 segment 起止（通常 5–30s，可能合并多句）；
- `match_whisper_to_cues()` 的时间重叠（±1s/±3s）。

这导致：

1. **长 cue 合并**：多人争吵时 Whisper 输出一条长段，SRT 里却是 5 条短 cue；时间重叠无法拆。
2. **边界漂移**：Whisper 段边界不是句子边界；翻译后中文长度变化，时间轴更对不上。
3. **无法验证文本**：不知道 cue 的每个字对应音频的哪一段，就无法判断「这是错字还是错轴」。
4. **切片重跑是治标**：重跑只换一组段边界，根因没变。

FA 给出 token/字符级时间后，才能做「文本 ↔ 音频」的精确对齐、cue 切分、缺失检测和修复验证。

### 4.2 Qwen3-ForcedAligner-0.6B

**规格** `[A]`：

- 非自回归（NAR）文本-语音对齐模型，0.6B。
- 支持 11 种语言：中、英、粤、法、德、意、**日**、韩、葡、俄、西。
- 输入：音频 + 任意文本；输出：词/字符级 `text/start_time/end_time`。
- 官方标称单次 ≤5 分钟；参考实现按 **180s** 分块，以保证稳定。
- Apache-2.0。

**精度** `[A]`（AAS，平均绝对对齐误差，ms，越低越好）：

| 数据集 | Monotonic-Aligner | NFA | WhisperX | **Qwen3-FA** |
|---|---:|---:|---:|---:|
| 日语 Raw | — | — | — | **42.2** |
| 日语 Concat-300s | — | — | — | **81.3** |
| 全语言 Raw 平均 | 161.1 | 129.8 | 133.2 | **42.9** |
| 全语言 300s 平均 | 1742.4 | 246.7 | 2708.4 | **52.9** |

> 这张表是官方评测；WhisperX 在日语行没有数，不能理解为「WhisperX 日语一定差」，但至少说明 Qwen3-FA 的日语是官方覆盖并评测过的。

**用法** `[A]`：

```python
import torch
from qwen_asr import Qwen3ForcedAligner

aligner = Qwen3ForcedAligner.from_pretrained(
    "Qwen/Qwen3-ForcedAligner-0.6B",
    dtype=torch.bfloat16,
    device_map="cuda:0",
)
result = aligner.align(
    audio="scene.wav",
    text="お前、何してんだよ！",
    language="Japanese",
)
for item in result[0]:
    print(item.text, item.start_time, item.end_time)
```

**限制**：

- 不返回置信度/概率；只能从时间单调性、每字符时长、与 ASR 文本的一致性做启发式质量判断。
- 文本语言要与 `language` 参数一致；如果文本里混入大量拉丁乱码，对齐质量会下降。修复流程应先用 ASR 文本作为对齐输入，而不是直接对齐乱码文本。
- 单次 3 分钟分块；长集要处理边界连续性。**实测输入 >60s 时大量 token 塌缩为零时长**
  （60s 17.6% → 90s 34.7% → 120s 54.6%），所以 worker 自切 60s 再按 offset 合并，
  见 [ADR-008](adr/008-qwen3-long-audio-chunking.md)。

### 4.3 MMS FA：为什么不适合日语字幕

**规格** `[A]`：

- Torchaudio `MMS_FA`：wav2vec2 CTC，31K 小时、1,130 语言，2023。
- **CC-BY-NC 4.0**（非商用）——skill 本身 MIT，分发时会给用户叠加 NC 限制。
- 没有 word boundary token，后处理与普通 wav2vec2 不同。
- 输入文本需要 **romanize**（uroman）。

**日语的致命问题** `[A]`：

uroman 官方 README 明确写：

> “For Japanese, uroman currently romanizes hiragana and katakana as expected, but **kanji are interpreted as Chinese characters and romanized as such**.”

也就是说，`お前` 的假名能读，`漢字` 会被按中文读音转写。字幕里大量汉字，MMS FA 的对齐目标音素就会错。要用它，必须先做 **pykakasi/ Sudachi 读音转换 → 假名/罗马字**，再对齐；这层转换本身会引入同音/多音错误。

**结论**：MMS FA 只建议作为「无 GPU 或配额受限、文本已转假名」的降级路径；**不做默认，不做日语主路径**。CC-BY-NC 也不适合作为开源 skill 的默认依赖。

### 4.4 WhisperX / MFA / stable-ts

| 方案 | 日语支持 | 优点 | 问题 | 结论 |
|---|---|---|---|---|
| **WhisperX** | 有默认 `jonatasgrosman/wav2vec2-large-xlsr-53-japanese` | 批处理快；词级对齐；diarization 集成 | 对齐在 Whisper segment 内做，段错则对齐错；数字/符号/未知字符弱；重叠语音差；依赖第三方 wav2vec2 | 可用，但不是日语 cue 级首选 |
| **MFA** | 需要日语发音词典 | 词典级精度高 | 安装重；OOV/专名/动画造词差；需要 G2P | 不适合动画专名 |
| **stable-ts** | 依赖 Whisper | 比 Whisper 原生时间戳稳 | 不是真正的文本-语音 FA；仍受 Whisper 段/幻觉影响 | 不解决根因 |

**为什么 Qwen3-FA 更适合我们**：

- 官方日语评测 42.2ms；
- 对齐对象是「任意文本」，不依赖 ASR 段边界；
- Apache-2.0；
- 与 Qwen3-ASR 同一个包，模型可同时加载；
- 对专名/汉字原生支持，不需要 romanize。

### 4.5 对齐选型结论

| 方案 | 采用 | 角色 |
|---|---|---|
| Qwen3-ForcedAligner-0.6B | ✅ | 默认；GPU；日语 cue/字符级 |
| MMS FA | ⚠️ 仅降级 | 无 GPU + 文本已转假名；NC 许可需提示 |
| WhisperX | ⚠️ 备选 | 已有 Whisper 管线、想快速拿词级时间戳时 |
| MFA | ❌ | 动画专名/OOV 不合适 |
| stable-ts | ❌ | 不是真 FA，不解决根因 |

---

## 5. 其他组件

### 5.1 VAD / AED：从「门控」降级为「提示」

**2026 基准** `[B]`（FLEURS-VAD-102，102 语言，9,443 条）：

| 模型 | F1 ↑ | 误报率 ↓ | 漏检率 ↓ | 许可/体积 |
|---|---:|---:|---:|---|
| **FireRedVAD** | **97.57** | **2.69** | **3.62** | Apache-2.0；约 2.2MB |
| Silero-VAD | 95.95 | 9.41 | 3.95 | MIT；约 2MB |
| TEN-VAD | 95.19 | 15.47 | 2.95 | 见仓库 |
| FunASR-VAD | 90.91 | 44.03 | 0.42 | 见仓库 |
| **WebRTC-VAD** | **52.30** | 2.83 | **64.15** | 当前使用 |

WebRTC 的「低误报」是靠「几乎不判语音」换来的：漏检 64%。在 VAD 门控的架构里，这意味着大量台词根本进不了修复流程。**这不是调 aggressiveness 能解决的。**

FireRedVAD 还提供 **AED**（audio event detection）：

- `speech` / `singing` / `music` 三类时间线；
- 对 OP/ED、BGM、歌曲段落直接有用；
- 支持 Windows/NCNN，`pip install fireredvad`。

**建议**：

1. `VAD_ENGINE=firered` 默认；Silero 作为 fallback；WebRTC 删除。
2. VAD/AED **只输出提示**：speech timeline、music/singing 区间、疑似非对白段；不直接删 cue、不直接定修复区。
3. OP/ED 用 AED + 跨集指纹 + 字幕重复度，不用固定 180s。
4. 对白垫 BGM 的场景，AED 的 `speech` 可能仍被 music 淹没；所以 ASR-first 架构不依赖 VAD 切段。

### 5.2 说话人分离

| 方案 | 许可 | 特点 | 适用 |
|---|---|---|---|
| **VibeVoice-ASR-7B** | MIT | ASR + diarization + timestamp 一次出；日语 MLC DER 0.82 | 可选；7B 需量化/分段 |
| **pyannote community-1** | CC-BY-4.0 | 通用 diarization；WhisperX 已集成；需 HF token | 可选；单独跑 diarization |
| 3D-Speaker / Sortformer | 见仓库 | 备选 | 未评估 |

**动画场景的现实**：角色声线夸张、重叠喊叫、BGM、音效，DER 会比会议数据差很多。说话人分离的主要价值不是时间轴，而是：

- 翻译时的人称/敬称/口吻；
- 多角色场景的 cue 归属；
- 专名与角色绑定。

**建议**：先做「可选实验」，不要进默认链路。评估指标用「角色归属人工正确率」，不要只看 DER。

### 5.3 人声分离

- 当前 `demucs htdemucs`（2022）是默认推荐；分离会带来伪影、相位问题、额外 2s/30s 的耗时。
- 社区常用、通常强于 Demucs 的是 BS-RoFormer / Mel-Band RoFormer 系；但它们更重，且对 ASR 不一定必要（需在本项目素材上实测）。
- **Qwen3-ASR 官方明确支持「带 BGM 的歌曲」**，说明它对 BGM 有一定鲁棒性 `[C]`；先用原音频跑，不行再分离。

**建议**：

1. `--separate-vocals` 默认关；文档从「推荐」改为「最后手段」。
2. 如果必须分离，优先试 Mel-Band RoFormer / BS-RoFormer；保留 demucs 兼容。
3. 分离后必须对比「原音频 vs 分离音频」的 ASR 结果，不能默认分离更好。

### 5.4 翻译

**现状（2026-10-18 修订）**：百度路径已删除，翻译全部走 LLM（ADR-010/011）。
使用用户已配置的 LLM（`~/.claude/settings.json` 的 DeepSeek，已实测）；
Qwen-MT / 云翻译不纳入（付费/需 token）。以下是当时的调研记录，保留作为背景。

原状：百度 API + LLM 双路径；百度 1 QPS、200 万字符/月免费、通用 MT。

**问题**：

- 动画台词依赖上下文、角色语气、敬称、专名；通用 MT 容易直译/翻译腔。
- 百度没有术语表/上下文注入；专名一致性靠事后替换。
- 双路径增加维护面，且百度路径的输出质量明显低于 LLM。

**建议**：

1. **LLM 为唯一路径**：DeepSeek / Qwen / Gemini / GPT 均可，保持 OpenAI 兼容接口。
2. **Qwen-MT** 作为批量草稿/低成本选项（阿里云，多语言 API）`[A]`；再用 LLM 做语气/术语审校。
3. **Prompt 升级**：
   - 注入 glossary（人名/地名/术语/角色关系）；
   - 给前后各 2–3 条 cue 的日文上下文；
   - 明确角色口吻/敬称/性别/年龄；
   - 输出必须满足行长/CPS（每行 ≤ 指定字符，阅读速度 ≤ 指定 CPS）；
   - 禁止添加原文没有的信息；不确定时保留 `[???]`。
4. **两遍法**：高价值集先直译，再让 LLM 做「术语/语气/长度」审校；低价值集一遍过。
5. ~~百度降级~~：**已删除**；无 LLM key 时明确失败/保留原文，不回退机翻。

### 5.5 专名：从「事后替换」改成「源头注入」

**现状**：翻译前扫描词频 → Jamdict 过滤 → AI 审查 → `noun_mappings.json` → 翻译时替换。

**问题**：ASR 阶段已经把专名听错（空耳），翻译阶段再替换只能补救已经错掉的日文。

**建议**：

1. **glossary-first**：每部作品先建词表（角色、地点、组织、招式、作品名、读音）。
   - 来源：现有字幕词频 + JMnedict + LLM + 网络搜索 + 官方中文标题；
   - 字段：`ja` / `kana` / `zh` / `type` / `confidence` / `notes`。
2. **ASR 阶段注入**：把词表压成紧凑的 `context`（Qwen3-ASR）或 hotwords（VibeVoice），优先保证人名/术语听对。
3. **翻译阶段注入**：同一份词表作为术语约束。
4. **审查**：JMdict 过滤普通词；Sudachi/MeCab 提取复合名词；LLM 只审 `NEEDS_AI` 的少量候选，不读全表。
5. **一致性**：跨集用同一份 glossary；新增词先 diff，再人工/AI 确认。

---

## 6. 目标架构

### 6.1 总图

```
┌──────────────────────────────────────────────────────────────────┐
│ 输入：视频 + 现有 SRT/ASS（可选） + glossary                      │
└──────────────────────────────────────────────────────────────────┘
                              │
                    ffmpeg → 16k mono WAV
                              │
        ┌─────────────────────┴─────────────────────┐
        │ 可选：FireRedVAD / AED                     │
        │  - speech / singing / music 时间线         │
        │  - OP/ED 候选、非对白提示                  │
        └─────────────────────┬─────────────────────┘
                              │
                    ASR Router（可插拔）
        ┌─────────────────────┼─────────────────────┐
        │ 默认：Qwen3-ASR-1.7B │ 可选：VibeVoice-7B  │
        │ + context=glossary   │ + hotwords/diarize  │
        │ + return_time_stamps │                     │
        └─────────────────────┼─────────────────────┘
                              │
                    Qwen3-ForcedAligner-0.6B
                    （token/字符级时间，3min 分块）
                              │
                    ASRResult（text + tokens + segments）
                              │
        ┌─────────────────────┴─────────────────────┐
        │ 全局文本对齐（cue ↔ ASR token timeline）   │
        │  - 时间粗锚点 + SequenceMatcher/带状 DP    │
        │  - 每个 cue：覆盖率/相似度/边界误差/溢出   │
        └─────────────────────┬─────────────────────┘
                              │
                    CueDiagnostics（每 cue 诊断）
                              │
        ┌─────────────────────┼─────────────────────┐
        │ 自动修复             │ AI 审查             │ 人工审查
        │ 高置信度             │ 歧义/语义           │ 低置信度/无音频
        └─────────────────────┼─────────────────────┘
                              │
                    重新对齐 / 重新切段
                              │
                    LLM 翻译（glossary + 上下文）
                              │
                    QA：行长/CPS/重叠/报告
```

### 6.2 三种运行模式

| 模式 | 输入 | 流程 | 用途 |
|---|---|---|---|
| **Repair** | 视频 + 现有字幕 | ASR + FA → cue 对齐 → 诊断 → 修复 | 主要场景（机翻/听译校对） |
| **Generate** | 视频，无字幕 | ASR + FA → LLM 切段/翻译 → 生成 SRT | 冷门资源从零做字幕 |
| **Verify** | 视频 + 干净字幕 | 逐场景 FA → 边界/覆盖率校验 | 只校时间轴、不重写文本 |

### 6.3 数据契约

```python
from dataclasses import dataclass, field
from typing import Literal, Optional

@dataclass
class Token:
    text: str
    start: float          # 秒，绝对时间
    end: float
    speaker: Optional[str] = None

@dataclass
class ASRSegment:
    text: str
    start: float
    end: float
    tokens: list[Token] = field(default_factory=list)

@dataclass
class ASRResult:
    text: str
    language: str
    segments: list[ASRSegment]
    tokens: list[Token]           # 全局 token 时间轴（FA 输出）
    engine: str                   # qwen3 | vibevoice | whisper | mai | scribe | gemini
    model: str
    audio_duration: float
    context_hash: str             # glossary 版本
    meta: dict = field(default_factory=dict)

@dataclass
class CueAlignment:
    cue_index: int
    asr_span: tuple[int, int]     # tokens 下标 [i, j)
    matched_chars: int
    similarity: float             # 0–1
    start: float                  # 对齐后的 cue 起点
    end: float
    status: Literal[
        "match",          # 文本与音频一致
        "garbled",        # cue 文本乱码/错字，但音频有对应
        "missing_asr",    # cue 有文本，音频无对应（多译/幻觉）
        "extra_text",     # cue 文本比音频多（部分幻觉）
        "no_speech",      # cue 时间段无语音
    ]
    signals: dict = field(default_factory=dict)
```

### 6.4 核心算法：ASR-FA + 全局文本对齐

> **实现落点**：`lib/alignment/cue_mapper.py`；设计取舍见 [ADR-007](adr/007-cue-alignment.md)；
> 真实 cue 结构回归（405 cue，P50 ≈30ms）见 [`tech-route-progress.md`](tech-route-progress.md) §4。

**Step 1 — 音频归一化**

```bash
ffmpeg -i input.mkv -vn -ac 1 -ar 16000 -c:a pcm_s16le ep.wav
```

**Step 2 — ASR + 时间戳**

- 优先一次调用：`model.transcribe(audio=ep.wav, context=glossary_text, language="Japanese", return_time_stamps=True)`。
- 内部按 180s 分块、低能量边界、offset 合并；我们只需处理返回的 token 时间轴。
- 如果显存/速度紧张，用 0.6B；如果走 VibeVoice，单独适配 rich transcription。

**Step 3 — 全局文本对齐**

不要做全量 O(n²) DP。24 分钟约 1–2 万字符，全矩阵不可行。推荐两层：

1. **时间粗锚点**：用 ASR segment 与 cue 的时间重叠，把 cue 分到 1–3 个候选 ASR 窗口。
2. **窗口内细对齐**：对每个窗口，用 `difflib.SequenceMatcher`（或带状 DP）对「cue 文本串」和「ASR 文本串」做字符级匹配；匹配块映射到 token 时间。
3. **全局一致性**：检查 cue 顺序单调、时间不倒退、cue 之间不重叠；异常回退到相邻窗口重试。

输出每个 cue 的 `asr_span`、`matched_chars`、`similarity`、`start/end`。

**Step 4 — cue 诊断**

| 信号 | 计算 | 用途 |
|---|---|---|
| `speech_coverage` | cue 时间段内 AED speech 的比例 | 判断是否无人声/音乐段 |
| `text_similarity` | cue 文本 vs 对齐 ASR 文本的字符相似度 | 判断文本是否错/乱码 |
| `boundary_error_start/end` | cue 起止 vs 对齐 token 起止 | 判断轴是否偏 |
| `overflow` | 文本对应时间超出 cue 边界多少 | 判断需要扩轴/切段 |
| `gap` | cue 内语音但文本没覆盖的时长 | 判断漏句 |
| `repeat_score` | 连续重复 n-gram | 判断幻觉/循环 |
| `lang_score` | 日语字符比例/语言 ID | 判断拉丁污染/语言错 |

**Step 5 — 修复决策**

```
status == match 且 boundary_error < 150ms
  → 不动（保护干净 cue）

status == garbled 且 similarity < 0.6 且 ASR 文本可读
  → 替换文本；用对齐 token 时间校正边界

status == missing_asr / extra_text
  → 删除/截断候选；先给 AI 判断，不自动删

status == no_speech 且 cue 时长短
  → 删除候选（保留 VAD 提示，不硬删）

边界误差 > 300ms 且文本 match
  → 只调时间轴，不改文本

长 cue（> 7s 或 > 2 行）且内部有 > 300ms 停顿
  → 在停顿处切段；文本按标点/语义切
```

**Step 6 — 重新对齐/切段**

- 改过文本的 cue，用 Qwen3-FA 对该 cue 的音频窗口重新对齐，确保新文本的字符时间正确。
- 切段后的新 cue，用 token 时间作为边界，不再用固定 padding。
- 翻译后的中文不参与时间对齐；时间轴继承日文 cue。

**Step 7 — 翻译 + QA**

- LLM 按 glossary + 上下文翻译；输出行长/CPS 约束。
- QA：cue 不重叠、阅读速度、空行、残留假名/拉丁、报告状态。

### 6.5 置信度与决策树

Qwen3-ASR 不暴露 logprob，所以用「多信号投票」：

```
高置信度（自动）：
  similarity ≥ 0.85
  且 boundary_error ≤ 150ms
  且 lang_score 正常
  且 repeat_score 低
  且（可选）第二引擎文本相似度 ≥ 0.8

中置信度（AI 审查）：
  similarity 0.5–0.85
  或 boundary_error 150–500ms
  或 单引擎可读但另一引擎分歧

低置信度（人工）：
  similarity < 0.5
  或 无 ASR 对应
  或 音频质量差/重叠严重
  或 AI 也判不确定
```

**双引擎**只对中置信度 cue 跑：例如 Qwen3-ASR 与 Whisper turbo / VibeVoice 各出一版，比较字符相似度；一致则自动，分歧则送 AI。不要对全片跑两个 7B。

### 6.6 与现有 3-Phase 的映射

| 现有阶段 | 现有模块 | 新架构 |
|---|---|---|
| Phase 1 SCAN | `unified_scanner.py`；VAD timeline；garbled 检测 | 保留字符扫描；新增 ASR/FA 产出的 `CueDiagnostics`；VAD 改为 FireRedVAD 提示 |
| Phase 2 FIX | `whisper_fixer.py` / `whisper_pipeline.py` | 新增 `align_repair.py`；`Fixer` 按 `ASR_ENGINE` 路由；Whisper 路径保留 |
| Phase 2.5 AI 碎片 | `fragment_processor.py` | 保留；输入从 Whisper 段改为对齐后的 ASR 文本 + token 时间 |
| Phase 3 UNIFY | `nouns/*` | 保留；glossary 前移，翻译注入 |
| DELIVER | `run_all.py:step_deliver` | 保留；报告新增诊断字段 |

### 6.7 性能与成本估算

> **2026-10-18 实测修正**（莎莉 EP001，25.2 分钟，3080 Ti Laptop 16GB；详见 [ADR-008](adr/008-qwen3-long-audio-chunking.md)）：
> 内部 180s 分块 RTF 0.83 且时间戳塌缩；自切 60s 后 RTF **0.207–0.295**、零时长 token 27%（补零后 8.4%），
> cue 覆盖均值 0.717。下表为方案估计，带 *实测* 的行以实测为准。

| 项 | 估算 | 说明 |
|---|---|---|
| Qwen3-ASR-1.7B RTF | ~~0.08–0.15~~ **实测 0.207–0.295（整集）/ 0.183（热态 120s）** | 公开数据 A100 0.10–0.17；5090 0.036；笔记本更慢，且受散热影响 |
| 单集 24min ASR | ~~2–4 分钟~~ **实测 5.2–7.4 分钟**（60s 分块；含加载） | 内部 180s 分块需 20.9 分钟 |
| 193 集 ASR | ~~6–12 小时~~ **按时测 ≈ 17–24 小时** | 可夜间批处理，但需按热降频留余量 |
| Qwen3-FA | 含在 ASR 耗时内 | 60s chunk 后对齐开销可接受 |
| 显存 | 实测 4.9–10.2GB（1.7B + 对齐） | 16GB 充足；Whisper large-v3 q5 另约 3GB |
| 云兜底 | $8–24 / 77h | MAI/Scribe/Gemini |
| 磁盘 | 模型约 5GB + 缓存 | 每集 ASR JSON 约 1–5MB |
| Whisper large-v3 q5（对照） | 实测 RTF 0.184；193 集约 15h | 段级时间戳，不能直接定 cue 轴 |

---

## 7. 代码落点

### 7.0 稳定核心与插件

**硬约束**：核心不追模型。换模型只加 `lib/asr/adapters/<name>.py`，不改 `router.py`、CLI、评测脚本、修复管线。

| 层 | 文件 | 变更频率 | 规则 |
|---|---|---|---|
| 契约 | `lib/asr/base.py` | 极低 | `ASRResult` / `Token` / `Segment` / `ASRConfig` / `BackendInfo` / `ASRBackend` / `Aligner`；JSON 带 `schema_version` |
| 注册器 | `lib/asr/registry.py` | 极低 | 自动 import `adapters/*.py`，读 `NAME/KIND/INFO/build` |
| 路由 | `lib/asr/router.py` | 极低 | 只依赖 registry + 契约，不 import 模型 |
| 适配器 | `lib/asr/adapters/<name>.py` | 高 | 每个模型一个文件；`build(config, **kwargs)` 返回 backend |
| Worker | `scripts/asr/qwen3_worker.py` | 中 | 引擎自己的进程；协议固定 `ping/load/transcribe/align/shutdown` |
| CLI/评测 | `scripts/asr/qwen3_transcribe.py`、`scripts/eval/asr_eval.py` | 低 | `--engine` 从 registry 动态取值，不写死模型名 |

新增模型 5 步：

1. 复制 `templates/asr_adapter.py` → `scripts/lib/asr/adapters/<name>.py`；
2. 填 `NAME/KIND/INFO/build`，实现 `available()/transcribe()`；
3. 需要 token 时间戳的，另写 `KIND='aligner'` 适配器，或后端暴露 `align()`；
4. 加契约测试（`validate_backend` + fake 调用，不下载模型）；
5. `python scripts/asr/qwen3_transcribe.py --list-engines` 应自动看到新引擎。

**不要做**：在 router/CLI 里加 `if engine == 'xxx'`；让模型返回私有 JSON；为每个模型复制一套评测。模型特有参数放 `ASRConfig.options` 或 adapter 自己的 `build(**kwargs)`。详见 [ADR-006](adr/006-stable-asr-core.md)。

**文档分层**（2026-10-18 整理）：

| 位置 | 用途 | 是否随 skill |
|---|---|---|
| `SKILL.md` | 运行时入口/操作说明 | ✅ |
| `references/` | 运行时命令/工作流/初始化参考 | ✅ |
| `dev/` | 开发者实现/根因/算法笔记 | ❌（本地，多数被 .gitignore） |
| `docs/` | ADR、技术路线、实施进度 | 仓库跟踪，不参与 skill 运行 |

### 7.1 新增模块

```
scripts/
├── lib/
│   └── asr/
│       ├── base.py            # 稳定契约（Token/Segment/ASRResult/ASRConfig/Protocol）
│       ├── registry.py        # 自动发现 adapters/*
│       ├── router.py          # 选择/降级；不 import 模型
│       └── adapters/
│           ├── qwen3.py       # Qwen3-ASR worker 客户端
│           ├── whisper.py     # 现有 Whisper 包装
│           └── <new_model>.py # 新模型只加这里（复制 templates/asr_adapter.py）
├── asr/
│   ├── qwen3_worker.py        # 长驻 worker；stdin/stdout JSON lines
│   ├── qwen3_transcribe.py    # 单文件 CLI：JSON + 可选 SRT
│   ├── setup_qwen3.ps1        # 隔离 venv / 安装 / 检查
│   └── README.md              # P1 运行说明
├── eval/
│   └── asr_eval.py            # CER / 边界诊断 / RTF
└── ...
```

对齐/修复层（P2）继续按契约加：

```
├── lib/alignment/
│   ├── base.py                # CueAlignment/AlignmentConfig/归一化/状态词表
│   ├── timeline.py            # ASRResult → 字符时间轴（token 优先、segment 兜底）
│   ├── cue_mapper.py          # 时间窗锚点 + 最佳匹配簇 + 精确串就近
│   ├── diagnostics.py         # 语音覆盖/重复度/置信度/边界统计
│   └── aligner.py             # registry 对齐服务；ALIGNER_ENGINE=none 可关闭
├── fix/align_repair.py        # 修复计划 + 时间码应用 + 单 cue 窗口重对齐
└── scripts/asr/align_cues.py  # P2 端到端 CLI（默认 dry-run，--write 才落盘）
```

> 原方案里的 `lib/alignment/qwen3_fa.py` 不再单独存在：P1 已把 Qwen3-FA 做成
> `lib/asr/adapters/qwen3_aligner.py`（`KIND='aligner'`），`aligner.py` 只是
> registry 之上的薄服务，换对齐模型仍然只加 adapter。详见 [ADR-007](adr/007-cue-alignment.md)。

### 7.2 修改文件

| 文件 | 改动 | 状态 |
|---|---|---|
| `lib/config.py` | 增加 `ASR_ENGINE`、`ASR_MODEL`、`ALIGNER_ENGINE`、`QWEN_PYTHON`、`ASR_CONTEXT_FILE` 等 | ✅ P1 |
| `lib/asr/base.py` | 契约 + `ASRConfig` + `BackendInfo` + `validate_backend` | ✅ P1 |
| `lib/asr/registry.py` | 自动发现 adapters | ✅ P1 |
| `lib/asr/router.py` | 按 registry 选择/fallback | ✅ P1 |
| `lib/asr/adapters/qwen3.py` | Qwen3 worker 客户端 + 适配器 | ✅ P1 |
| `lib/asr/adapters/whisper.py` | 旧 Whisper 包装成同一契约 | ✅ P1 |
| `scripts/asr/qwen3_worker.py` | 长驻 worker | ✅ P1 |
| `scripts/asr/qwen3_transcribe.py` | 单文件 CLI | ✅ P1 |
| `scripts/asr/setup_qwen3.ps1` / `README.md` | 隔离环境/运行说明 | ✅ P1 |
| `scripts/eval/asr_eval.py` / `eval/README.md` | P0 评测 | ✅ P1 |
| `templates/asr_adapter.py` | 新模型骨架 | ✅ P1 |
| `lib/alignment/base.py` | 对齐契约 + 文本归一化 + 状态/动作词表 | ✅ P2 |
| `lib/alignment/timeline.py` | ASRResult → 字符时间轴 | ✅ P2 |
| `lib/alignment/cue_mapper.py` | 时间窗 + 字符匹配 + 精确串就近 | ✅ P2 |
| `lib/alignment/diagnostics.py` | 信号/置信度/边界统计 | ✅ P2 |
| `lib/alignment/aligner.py` | registry 对齐服务（`none` 可关） | ✅ P2 |
| `fix/align_repair.py` | 修复计划 + 时间应用 + 窗口重对齐 | ✅ P2 |
| `scripts/asr/align_cues.py` | P2 端到端 CLI（默认 dry-run） | ✅ P2 |
| `scripts/eval/alignment_bench.py` | P2 结构回归（合成 ASR，无需音频） | ✅ P2 |
| `fix/fix_orchestrator.py` | `fix_by_asr()`；按引擎路由 | ✅ P2（flag 控制） |
| `fix/whisper_pipeline.py` | `match_whisper_to_cues` / `align_and_fix` 标记 legacy | ⬜ P2 尾项 |
| `scan/unified_scanner.py` | VAD 切到 `lib/audio/vad.py` | ⬜ P3 |
| `translate_srt.py` | 百度路径已删除；翻译全部走 LLM（ADR-010） | ✅ P4 前半 |
| `run_all.py` | `--asr` / `--asr-python` / `--asr-fix-text`（`--aligner`/`--vad-engine`/`--hotwords` 待 P3/P4） | ✅ P2（flag 控制） |
| `SKILL.md` / `README.md` | 更新技术栈、安装向导、后端说明 | 🟡 文档已拆分；技术栈更新待 P6 |

### 7.3 配置与 CLI

```python
# lib/config.py（新增）
ASR_ENGINE        = os.environ.get('ASR_ENGINE', 'qwen3')      # qwen3|whisper|vibevoice|mai|scribe|gemini
ASR_MODEL         = os.environ.get('ASR_MODEL', 'Qwen/Qwen3-ASR-1.7B')
ASR_DEVICE        = os.environ.get('ASR_DEVICE', 'cuda:0')
ASR_CONTEXT_FILE  = os.environ.get('ASR_CONTEXT_FILE', '')      # glossary 文本
QWEN_PYTHON       = os.environ.get('QWEN_PYTHON', '')           # 隔离 venv 的 python.exe
ALIGNER_ENGINE    = os.environ.get('ALIGNER_ENGINE', 'qwen3')  # qwen3|mms|none
ALIGNER_MODEL     = os.environ.get('ALIGNER_MODEL', 'Qwen/Qwen3-ForcedAligner-0.6B')
VAD_ENGINE        = os.environ.get('VAD_ENGINE', 'firered')    # firered|silero|silencedetect
ASR_FALLBACK      = os.environ.get('ASR_FALLBACK', 'whisper')  # whisper|none
```

```bash
python run_all.py --lang ja --asr qwen3 --aligner qwen3 --vad-engine firered --hotwords glossary.txt
```

> 新模型不需要新增配置开关：`--engine <name>` 直接选 registry 里的适配器。只有默认引擎/auto 策略才需要改 config。

### 7.4 Worker 协议（解决 Windows 依赖冲突）

**为什么用 worker**：

- `qwen-asr` 固定 `transformers==4.57.6`，并会拉 gradio/flask；直接 import 主进程会污染 skill 环境。
- 模型加载一次很贵；193 集不能每集重载。
- 主进程保持纯 Python/轻依赖，测试仍然 0.5s。

**Windows 注意**：vLLM 官方不支持 Windows；用 `qwen-asr` 的 Transformers 后端（`Qwen3ASRModel.from_pretrained`），不要依赖 vLLM。FlashAttention 在 Windows 上编译麻烦，先不启用；用默认 SDPA + bf16。

**启动**：

```powershell
.venv-qwen\Scripts\python.exe scripts\asr\qwen3_worker.py --asr Qwen/Qwen3-ASR-1.7B --aligner Qwen/Qwen3-ForcedAligner-0.6B --device cuda:0 --dtype bfloat16 --chunk-sec 60
```

> `--chunk-sec`（默认 60；0 = 交给 qwen_asr 内部 180s 分块）：worker 按低能量边界自切，
> 逐块转写+对齐后加 offset 合并；合并后把连续零时长的 token 在前后的已知时间之间补齐
> （仅限 ≤3s 或 0.6s/token 的间隔）。原因和实测见 [ADR-008](adr/008-qwen3-long-audio-chunking.md)。

**协议**（JSON lines）：

```json
{"cmd":"ping"}
{"cmd":"transcribe","id":"EP001","audio":"C:/tmp/EP001.wav","language":"Japanese","context":"...","timestamps":true}
{"cmd":"align","id":"EP001-cue-12","audio":"C:/tmp/EP001_0012.wav","text":"お前、何してんだよ！","language":"Japanese"}
```

```json
{"ok":true,"id":"EP001","result":{"text":"...","language":"Japanese","tokens":[{"text":"お前","start":12.34,"end":12.61}],"segments":[...],"engine":"qwen3"}}
```

**失败策略**：

- worker 启动失败 → 检查 `QWEN_PYTHON`/显存/模型路径；自动降级 `ASR_FALLBACK=whisper`，并在报告标记。
- worker 崩溃 → 重启一次；仍失败则该集走 fallback。
- 不允许静默返回空结果；必须有 `ok:false` + `error` 字段。

### 7.5 缓存与幂等

```
cache/
├── asr/
│   └── EP001.<audio_sha1>.<model>.<context_sha1>.json
├── align/
│   └── EP001.<audio_sha1>.<aligner>.json
└── vad/
    └── EP001.<audio_sha1>.<engine>.json
```

- key 包含音频 hash、模型名/版本、glossary 版本；任一变化则重跑。
- ASR/对齐是只读缓存；修复写入仍走 git CoW。
- 支持 `--no-cache` 和 `--refresh-asr`。

### 7.6 测试

| 测试 | 内容 |
|---|---|
| `tests/test_cue_mapper.py` | 构造 ASR token + cue 文本，验证 span/边界/异常 |
| `tests/test_cue_diagnostics.py` | 覆盖率/相似度/溢出/重复信号 |
| `tests/test_asr_router.py` | 引擎选择、fallback、缓存 key |
| `tests/test_worker_protocol.py` | JSON lines 解析、错误传播（fake worker） |
| `tests/test_vad_backends.py` | FireRed/Silero/silencedetect 统一接口（mock） |
| 集成（可选） | 30s fixture + 小模型；无模型时 skip |

---

## 8. 迁移计划

### 8.1 P0–P6

| 阶段 | 目标 | 交付 | 验收 | 工时 | 回滚 |
|---|---|---|---|---|---|
| **P0 基线** | 固定对照 | 12 集金标；当前管线 CER/边界/人工条数 | 基线报告可复现 | 1–2d | 无 |
| **P1 ASR 适配** | Qwen3 跑通 | `lib/asr/qwen3.py` + worker + `--asr qwen3` | 12 集 CER 对比；无崩溃 | 2–3d | flag 关掉即回 Whisper |
| **P2 对齐** | cue 级时间轴 | `qwen3_fa.py` + `cue_mapper.py` + `align_repair.py` | 边界 P50/P90；干净 cue 不被破坏 | 3–5d | `ALIGNER_ENGINE=none` |
| **P3 VAD/AED** | 提示而非门控 | `lib/audio/vad.py` + FireRedVAD | 语音时间线对比；OP/ED 候选 | 1–2d | `VAD_ENGINE=silencedetect` |
| **P4 专名/翻译** | 源头注入 | glossary schema + ASR context + 翻译 prompt | 专名 F1；翻译人工评分 | 1–2d | 保留旧 glossary 路径 |
| **P5 可选** | 说话人/云兜底 | VibeVoice adapter；MAI/Scribe/Gemini client | 单集实验；成本/隐私评估 | 2–4d | 默认关 |
| **P6 清理** | 去旧 | 文档/README/SKILL 更新；deprecated 标记 | 测试全绿；安装向导可跑 | 1d | git revert |

**MVP（P0–P2）约 1 周**；完整迁移约 2–3 周（单人）。

### 8.2 本周可做

1. 建 `.venv-qwen`（`--system-site-packages` 复用 torch 2.6.0+cu124），装 `qwen-asr==0.0.6`。
2. 下载 `Qwen3-ASR-1.7B` + `Qwen3-ForcedAligner-0.6B`，用 30s 日语音频跑通 `transcribe(return_time_stamps=True)`。
3. 写 `scripts/asr/qwen3_worker.py` 最小版（ping/transcribe/align）。
4. 选 3 集：一集干净对话、一集多人争吵、一集 BGM/OP-ED 重；跑 Qwen3 vs 当前 kotoba，记录 CER 和 20 条 cue 边界。
5. 写 `FireRedVAD` 对比脚本，输出 speech/music/singing 时间线，和 WebRTC 对比漏检。
6. 把结果填进第 9 节模板；决定是否进 P2。

### 8.3 完成定义（DoD）

- 12 集金标上：CER 相对下降 ≥15%；边界 P50 ≤100ms、P90 ≤300ms；干净 cue 破坏率 <1%；人工审查条数相对下降 ≥25%。
- 193 集批量：无崩溃；单集 ASR+对齐 ≤5 分钟；显存 <8GB；缓存可复用。
- `pytest -q` 全绿；无新增外部依赖进入主环境。
- `SKILL.md`/`README.md`/`dev/technical-details.md` 已更新；`kotoba`/WebRTC/百度默认路径已标记 deprecated。
- 回滚开关有效：`ASR_ENGINE=whisper`、`ALIGNER_ENGINE=none` 可回到旧行为。

---

## 9. 评测方案

### 9.1 金标集

选 **12 集**，每类 4 集：

| 类别 | 特征 | 目的 |
|---|---|---|
| A 干净对话 | 单人/双人、BGM 少 | 防止「为了难例牺牲简单例」 |
| B 密集/重叠 | 争吵、多人抢话、语速快 | 当前长 cue 合并的重灾区 |
| C BGM/OP-ED | 对白垫 BGM、歌曲、音效多 | 幻觉/音乐误判 |

每集人工标注：

- 前 5 分钟逐句日文（或使用可信的现有日文字幕，人工校正）；
- 100 条 cue 的起止边界（人工听音校准）；
- 20 条专名的正确写法/读音。

### 9.2 指标

| 维度 | 指标 | 说明 |
|---|---|---|
| ASR | CER | NFKC、去标点、字符级；主指标 |
| ASR | WER | MeCab/Sudachi 分词后；辅助 |
| ASR | 专名 F1 | Sudachi 名词/专名；按 glossary 对齐 |
| ASR | 幻觉率 | 非语音段插入词数 / 分钟 |
| 对齐 | 边界 MAE（起/止） | 与人工边界比；主指标 |
| 对齐 | P50/P90 | 容忍长尾 |
| 对齐 | ±100/200/500ms 命中率 | 业务可读性 |
| 修复 | 乱码 cue 精确率/召回 | 与人工标记比 |
| 修复 | 干净 cue 破坏率 | 必须 <1% |
| 翻译 | COMET-22 / COMETKiwi | ja→zh；自动 |
| 翻译 | 术语准确率 | glossary 命中 |
| 翻译 | 人工 5 分制 | 50 条/类别；盲评 |
| 运营 | RTF / 显存 / 单集耗时 | 3080 Ti Laptop |
| 运营 | 人工审查条数 | 每集 |
| 成本 | 本地电费/云费用 | 按 193 集 |

### 9.3 A/B 流程

1. 同一集、同一视频、同一 glossary，分别跑“旧管线”和“新管线”。
2. 输出到不同目录；人工审查时随机顺序、隐藏引擎名。
3. 先看自动指标，再做 20 条错误分析（ASR 错/对齐错/翻译错/报告错）。
4. 统计用 bootstrap 95% CI；样本小的时候不要宣称“显著”。
5. 决策规则：
   - CER 相对下降 ≥15% 且边界改善 ≥30% → 进默认；
   - 只有一项达标 → 保留 flag，继续调；
   - 干净 cue 破坏率 >1% → 回滚。

### 9.4 报告模板

```markdown
## 2026-10 技术路线 A/B

| 指标 | 旧（kotoba+WebRTC+overlap） | 新（Qwen3+FA+DP） | Δ |
|---|---:|---:|---:|
| CER | | | |
| 边界 MAE (ms) | | | |
| P90 (ms) | | | |
| 乱码召回 | | | |
| 干净 cue 破坏率 | | | |
| 人工审查/集 | | | |
| 单集耗时 | | | |
| 显存峰值 | | | |

错误分析 Top 5：
1. ...
```

---

## 10. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| Qwen3 对俚语/专名「空耳」 | 中高 | 高 | glossary context；双引擎；保留原文；只在高置信时替换 |
| 3 分钟对齐分块边界抖动 | 中 | 中 | 5s 重叠/边界连续性检查；按停顿切；跨块单调性校验 |
| 长音频 FA 时间戳塌缩（>60s chunk） | **高（已实测）** | 高 | worker 自切 60s 低能量边界 + 补零时长；见 [ADR-008](adr/008-qwen3-long-audio-chunking.md) |
| 整季吞吐低估（实测 24h 而非 6–12h） | 高 | 中 | 按最差 RTF 排夜间批；0.6B 需复测；必要时云兜底/换卡 |
| Windows 依赖冲突 | 中 | 高 | 独立 venv + worker；pin 版本；fallback Whisper |
| 16GB 显存不够（VibeVoice） | 中 | 中 | 量化/分段/云；默认不启用 |
| 云 API 隐私/成本 | 中 | 中 | 默认本地；显式 opt-in；脱敏；成本上限 |
| MMS FA 许可/日语问题 | 高 | 中 | 不默认；只在降级路径并提示 NC |
| 模型更新/下载失败 | 中 | 中 | pin revision；本地缓存；离线模式 |
| 干净 cue 被误改 | 中 | 高 | `never downgrade`；高置信门槛；diff 报告；金标回归 |
| 吞吐变慢 | 中 | 中 | 0.6B 草稿；并行集；夜间批；缓存 |
| 报告/数据模型膨胀 | 中 | 低 | schema 版本；旧字段兼容；只加不改 |

---

## 11. 决策清单

| 决策 | 结论 | 理由 |
|---|---|---|
| Qwen3-ASR-1.7B 默认 | ✅ Adopt | 自然对话日语最强公开证据；Apache；context；时间戳路径 |
| Qwen3-ForcedAligner-0.6B 默认 | ✅ Adopt | 日语 AAS 42.2ms；任意文本；同包 |
| FireRedVAD 默认 | ✅ Adopt（仅提示） | F1 97.57；AED 对 OP/ED/BGM 有用；Apache |
| 全局文本对齐替代时间重叠 | ✅ Adopt | 解决长 cue/错配/边界 |
| 长音频自切 60s + 补零时长 | ✅ Adopt（实测后新增） | 内部 180s 分块会塌缩；RTF 0.83→0.295，零时长 48%→8.9% |
| VibeVoice-ASR-7B | 🟡 Trial | 说话人/热词很强；7B 部署需验证 |
| MAI-Transcribe-2 | 🟡 Trial（云兜底） | 日语细节好；预览/隐私/成本 |
| Scribe v2 / Gemini 3.5 | 🟡 Trial（云兜底） | 可用；不是默认 |
| Whisper large-v3-turbo | ✅ Keep（fallback） | 稳定、快；比 kotoba 更适合对话 |
| kotoba-v2.0 | ❌ Reject（默认） | 域错配；自然对话 WER 0.534 |
| WebRTC VAD | ❌ Reject | 漏检 64%；不配做门控 |
| MMS FA | ❌ Reject（日语主路径） | uroman 汉字按中文读；CC-BY-NC |
| WhisperX | 🟡 Hold | 日语可用，但段内对齐；Qwen3-FA 更合适 |
| MFA | ❌ Reject | 专名/OOV 不适合动画 |
| 百度翻译默认 | ❌ Reject（默认） | 通用 MT；无上下文/术语 |
| demucs 默认 | ❌ Reject（默认） | 伪影/延迟；先验证 Qwen3 原音频 |
| 固定 180s OP/ED | ❌ Reject | 已被实际数据打脸；用 AED/指纹 |

**现在不要做**：

1. 不要为了「新」而重写 3-Phase 编排层。
2. 不要同时换 ASR、对齐、VAD、翻译；按 P0–P4 逐步来。
3. 不要把 VibeVoice 7B 设为默认；先在 16GB 卡上跑 3 分钟片段。
4. 不要用 Gemini Pro 原生音频给字幕定轴。
5. 不要在没有金标的情况下宣布「CER 降低 X%」。

---

## 附录 A：参考链接

**Qwen3-ASR / 对齐**

- GitHub: <https://github.com/QwenLM/Qwen3-ASR>
- 技术报告: <https://arxiv.org/abs/2601.21337>
- ForcedAligner 模型卡: <https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B>
- ASR 工具包（API 长音频分块）: <https://github.com/QwenLM/Qwen3-ASR-Toolkit>
- 推理代码（分块/上下文/时间戳）: <https://github.com/QwenLM/Qwen3-ASR/blob/main/qwen_asr/inference/utils.py>

**VibeVoice**

- GitHub: <https://github.com/microsoft/VibeVoice>
- ASR 文档: <https://github.com/microsoft/VibeVoice/blob/main/docs/vibevoice-asr.md>
- 技术报告: <https://arxiv.org/abs/2601.18184>

**日语基准**

- Neosophie（自然对话，2026-04）: <https://neosophie.com/ja/blog/20260226-japanese-asr-benchmark>
- HEROZ（朗读/CommonVoice/JSUT，2026-08）: <https://techblog.heroz.jp/entry/2026/08/18/120000>
- Zenn（Scribe v2 / Gemini 3.5 / MAI-Transcribe-2，2026-09）: <https://zenn.dev/minedia/articles/c23bcd82102c8e>

**对齐 / VAD**

- Torchaudio MMS_FA: <https://docs.pytorch.org/audio/stable/generated/torchaudio.pipelines.MMS_FA.html>
- uroman（日语汉字限制）: <https://github.com/isi-nlp/uroman>
- WhisperX: <https://github.com/m-bain/whisperX>
- FireRedVAD: <https://github.com/FireRedTeam/FireRedVAD>
- FireRedVAD 论文: <https://arxiv.org/abs/2603.10420>
- Silero VAD: <https://github.com/snakers4/silero-vad>
- pyannote community-1: <https://www.pyannote.ai/blog/community-1>

**云 / 翻译**

- MAI-Transcribe-2: <https://microsoft.ai/news/mai-transcribe-2-is-the-fastest-most-accurate-and-cheapest-speech-recognition-model-in-the-world/>
- Gemini 3.5 Transcribe: <https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-5-transcribe/>
- Scribe v2: <https://elevenlabs.io/blog/introducing-scribe-v2>
- Qwen-MT: <https://www.alibabacloud.com/help/en/model-studio/machine-translation>

**Whisper 系**

- Whisper: <https://github.com/openai/whisper>
- kotoba-whisper-v2.0: <https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0>
- ReazonSpeech: <https://huggingface.co/reazon-research/reazonspeech-nemo-v2>
- Parakeet JA: <https://huggingface.co/nvidia/parakeet-tdt_ctc-0.6b-ja>

## 附录 B：术语

| 术语 | 含义 |
|---|---|
| ASR | 自动语音识别 |
| FA | Forced Alignment，强制对齐：把已知文本对齐到音频，得到 token 时间 |
| AAS | Average Alignment Shift/Error，平均对齐误差（ms，越低越好） |
| CER / WER | 字符/词错误率 |
| tcpWER | 时间约束的最小排列 WER，同时评估谁/何时/说什么 |
| DER | 说话人分离错误率 |
| AED | Audio Event Detection，音频事件检测（speech/singing/music） |
| RTF | Real-Time Factor，处理时间/音频时长，越低越快 |
| CPS | Characters Per Second，字幕阅读速度 |
| VAD | Voice Activity Detection，语音活动检测 |
