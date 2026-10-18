# 能力测试台账（接入前）

> **规则**：先测模型“能做什么、在我们数据上做得怎样”，再决定接哪一步。
> 没有通过能力测试的模型，只保留资产，不接主流程；接入后必须有回退和指标。
> 最后更新：2026-10-19。

## 1. 状态总表

| 模型 | 声称能力 | 已做测试 | 结论 | 允许接入 |
|---|---|---|---|---|
| Qwen3-ASR-1.7B + FA | 日语 ASR + 字符级对齐 | 3 集 CC 基准、端到端、signed/空洞 | **有强证据**，日语媒体第一梯队 | ✅ 主 ASR/对齐 |
| FireRedVAD + AED | VAD + speech/singing/music | README 样例、EP001–003 整集、空洞归因、OP/ED 窗口 | 能分事件；singing 在 OP/ED 是正文 ~6 倍（三集一致）；**空洞只 12/53 为 speech 主导，只支持过滤** | 🟡 已接 `--audio-hints` 写证据；决策未接 |
| VibeVoice-ASR-BitNet | 60min Who/When/What + 热词 | 60s 日语动画片段（text/json 两种 prompt） | **没有 who/when；专名更差；用户决定删除说话人** | ⛔ 已删资产，不接 |
| Mel-Band RoFormer | 人声分离 | 无（只有权重，运行时未接） | 未测 | ⛔ 暂不接 |
| LLM 翻译（DeepSeek） | 日→中翻译 | 1 句短句 API 验证 | 能调用；**无成集质量证据** | 🟡 只做 20 cue pilot |
| Review 阶梯 | L0-L3 减人工 | 单元测试（工作单/set_time） | 机制可用；**无 pilot 收益证据** | 🟡 先做 EP001 小样本 pilot |

## 2. FireRedVAD/AED：能力与边界

### 已测

- 官方 `hello_zh.wav` / `event.wav`：输出与 README 一致；EP001 主窗口 1145s：
  VAD speech 666.7s/290 段；AED speech 558.8s/309 段、singing 101.8s/55 段、music 893.8s/78 段。
- `AudioHints` provider/CLI 已接线；EP001 生成 442 事件（`temp/scans/EP001.audio_hints.json`）。

### 空洞归因实验（EP001–003，现有 FA 时间轴）

用 `lost_spans` 找空洞，与整集 AED 的 speech/music/singing 求重叠（主窗口对齐后）：

| EP | 空洞数 | 总时长 | speech≥50%（真对白候选） | 其中 speech 秒 |
|---|---:|---:|---:|---:|
| EP001 | 30 | 375.2s | 6 | 125.9s |
| EP002 | 9 | 139.0s | 2 | 48.4s |
| EP003 | 14 | 186.0s | 4 | 47.0s |
| **合计** | **53** | **700.2s** | **12（23%）** | **221.3s** |

EP001 细分：speech 20–50% 15 个/192.5s；speech <20% 9 个/105.6s；
music ≥80% 20 个/268.7s；singing ≥30% 4 个/59.6s。

**结论**：FireRedVAD 能把“空洞”分成“可能丢对白 / 可能是音乐”两类，**适合做证据过滤**；
三集里只有 12/53（23%）是 speech 主导，说明“空洞=FA 塌缩”这个假设过强，
直接拿空洞数当质量指标会高估问题。

### 未测（不能宣称）

- 阈值敏感性：`speech_threshold=0.4` 等默认值未扫；不同 BGM 强度下可能漂移。
- 与 CC 边界的相关性：AED speech onset 是否比 ASR 更接近 cue 边界，未测。
- 候选规则的 precision/recall：还没在 3 集上跑“自动选 OP/ED 窗口”的规则，只验证了信号。

### 2.5 整集 OP/ED 实验（EP001–003，2026-10-19）

用整集音频（约 1386s/集）重跑 AED，按 CC 主窗口切 OP/正文/ED：

| EP | OP singing | 正文 singing | ED singing | 正文 music |
|---|---:|---:|---:|---:|
| EP001 | 56.1% | 9.0% | 50.4% | 78.0% |
| EP002 | 56.0% | 8.0% | 51.4% | 69.0% |
| EP003 | 56.0% | 8.5% | 51.3% | 59.6% |

（窗口：OP `[0,95]`、正文 `[95,1240]`、ED `[1240,end]`；三集 singing 在 OP/ED 均为正文的 6 倍左右。）

- 三集 OP singing 段几乎逐秒一致（18.2–80.9s），是同一段 OP 素材，说明检测可复现；
- ED singing 段约 1263–1348s，三集也基本一致；
- **singing 是可用的 OP/ED 信号；music 在正文仍占 59.6–78%，单独不可用**；
- 下一步：用“头尾 singing 富集 + music/CC 边界”写候选规则，在 3 集上与 CC 歌词比对
  precision/recall，通过后再接主流程。


## 3. VibeVoice-ASR-BitNet：能力测试（负面，资产已删除）

60s 日语动画片段（主窗口起点后 60s，CPU 8 线程，greedy）：

| 项 | 结果 |
|---|---|
| RTF | 0.435（26.1s 处理 60s） |
| Who / When | **没有**；`--prompt-format text` 和 `json` 都只输出纯文本 |
| 专名 | 明显更差：`地球全体`→`子宮全体`、`八神太一`→`山見大知`、`記録的な冷夏`→`既読的な霊歌` |
| 结论 | 这个 **BitNet/1.5B CPU 版不是 7B 的 Who/When/What 能力**；只能当“另一个文本 ASR”，且更差 |

**因此**：原计划“用 VibeVoice 做说话人归属/翻译人称”**没有依据，不能接**。
用户已决定：**说话人没有用途，删除 VibeVoice 与 diarization**；资产已清理，
不再考虑 7B fp16 或专用 diarizer。

## 4. Mel-Band：未测

- 只有 ComfyUI 格式权重（fp16 456MB / fp32 913MB），没有运行时/适配器。
- 未做“原音频 vs 分离音频”的 ASR A/B；**不能接**。
- 要接先补：分离运行时 → 60–120s A/B（CER/边界/耗时）→ 有收益才默认关接入。

## 5. Qwen3-ASR/FA：已有强证据

3 集 CC 标准：Qwen3 test P50 400ms / P90 1196ms / 可靠 P90 510ms；CER 0.2633；
FA 日语 AAS 42.2ms（官方）。这是目前唯一“可以直接接主链路”的模型。

## 6. Review / 翻译：机制有测试，价值没证据

- Review：单元测试覆盖工作单、`set_time`、AI review 列表；**没有 pilot 证明能减少人工/不降质量**。
- 翻译：只有 1 句 API 验证；**没有成集术语一致性/漏译/长度证据**。
- 接入前各做一个小 pilot（EP001 的 20 条），通过再推广。

## 7. 接入闸门（每个模型都必须过）

1. **能力清单**：模型实际输出什么（文本/时间/说话人/事件），不是模型卡声称什么。
2. **最小实验**：1–3 集或 60–120s 片段，有可量化指标（CER/边界/事件覆盖率/人工抽检）。
3. **收益对比**：接与不接的差异；没有收益就不接。
4. **回退**：null provider / 开关关掉即回旧行为。
5. **记录**：本页 + `ADR-011` 台账 + `docs/tech-route-progress.md`。

**没有能力测试的接入 = 猜测；没有收益对比的接入 = 装饰。**

## 8. 下一步实验（按顺序）

1. FireRedVAD 整集音频 AED → OP/ED 候选（用 CC 歌词/人工抽检验 precision）。
2. FireRedVAD 空洞归因扩到 EP002/003（验证 6/30 是否稳定）。
3. Review pilot：EP001 272 条里抽 20 条，AI 填 decision → apply → 对比 review 数与质量。
4. 翻译 pilot：20 cue + glossary，测术语一致性/漏译/长度。
5. ~~VibeVoice/说话人~~：已删除（用户决定），不再做。
6. Mel-Band：先补运行时，再做 60–120s A/B。
