# 技术调研：时间轴与翻译提示词（2026-10）

> 目的：在写代码前确认“这些做法有成熟方案支撑”，避免拍脑袋。
> 结论先行：**翻译提示词最该补的是“源→目标术语对照 + 场景上下文”**；
> **时间轴最该做的是 VAD/语音边界切块 + 定向短窗重对齐**，不是再换 ASR 模型。

## 1. 翻译提示词：成熟项目怎么做

### 1.1 gpt-subtrans / llm-subtrans

来源：<https://github.com/machinewrapped/gpt-subtrans>，
`instructions/instructions (english to chinese).txt`、`docs/translation-flow.md`。

它的 prompt 结构：

- **逐条编号 + Original/Translation 成对输出**，不是裸 JSON 字符串数组；
- 明确“每条必须单独翻译，合并会导致时间轴错位”；
- 每批输出 `<summary>`（本批摘要）和 `<scene>`（场景摘要），供下一批使用；
- 提供 `terminology` 块，格式 `term::canonical form`，要求“术语表里的形式必须一致”；
- retry 指令专门处理“漏条/并条”；
- 名字表按用户偏好拼写；不审查脏话；不即兴加戏。

### 1.2 VideoLingo

来源：<https://github.com/Huanshere/VideoLingo>，`core/prompts.py`、`core/_4_2_translate.py`。

它的 prompt 结构：

- Role / Task / Context Information / Content Summary / Points to Note / Style；
- `<previous_content>` + `<subsequent_content>` + 整段摘要 + 术语；
- **两阶段**：先 “faithful direct translation”，再 “reflect + free translation”；
- 输出 JSON dict，保留 `origin`/`direct`/`reflect`/`free`，便于对齐与校验；
- 明确“不空行、不加解释、不给观众看的注释”；
- 术语提取 ≤15 条，避免 prompt 膨胀。

### 1.3 学术证据

- SMART / Breaking Babel（arXiv:2609.38660）：长片字幕需要 series-level memory、
  术语/风格一致、约束校验、judge-refiner；SubMQM 七维 19 类错误。
- IWSLT 2026 Subtitling Track：字幕系统普遍做长度/压缩约束。

### 1.4 对我们的结论

| 做法 | 证据 | 我们现状 | 决定 |
|---|---|---|---|
| 术语用 **ja→zh 对照**，不是只给中文名 | gpt-subtrans terminology；EP003 实证 | 原 prompt 只给中文名，`タケル` 被译成「太一」 | **已修**：`load_mappings/load_glossary` 输出 `ja→zh` |
| 逐条成对输出/禁止并条 | gpt-subtrans | 用 JSON 数组，靠顺序；无显式“禁止并条” | 30 条 A/B：禁止并条规则导致 1 条行长违规、chrF 34.92 vs 纯 pairs 36.69 → **暂不启用**，等 MQM |
| 场景摘要跨批传递 | gpt-subtrans `<scene>`；VideoLingo summary | 只有前 3 条原文 | 下一步：批间 summary（不是裸 future 2 条；裸 future 消融是负的） |
| 两阶段直译+反思 | VideoLingo | 只有 QA 后 repair | 下一步：仅对 MQM 问题条做 reflect，不全量 |
| 术语提取/一致性 | SMART、VideoLingo | glossary 有，跨集 memory 无 | 下一步：series memory |
| 未来上下文裸塞 | 我们的消融 | 43.33→36.25，负 | **不做** |

> 2026-10 复核：整集上下文窗口 0/3/10/full 重测，默认 3 无稳定收益；
> 已把默认改 0，并删除 `--future-context`。详见
> `docs/translation-research-2026-10.md`。
> 批间 summary/target 交接也测过：target chrF +0.6、MQM 无提升；summary QA 少 3、多一次调用/批；都不接默认。

## 2. 时间轴：切块/对齐的成熟做法

### 2.1 VAD/静音边界切块是标准做法

来源：omniASR-server `audio_chunker.py`

- 明确写 “VAD-based segmentation — split at natural pauses”；
- 找不到静音时回退为带 overlap 的切块；
- 参数：max 35s、target 30s、min silence 0.3s、search window 5s。

Qwen3-ASR 官方本身也按低能量边界切 180s；Qwen3-FA 官方 ≤5min、参考 180s。
我们的 ADR-008 已实测：180s 内部切块时间戳塌缩，自切 60s 后 RTF/零时长改善。
所以“60s 固定”不是理论最优，**用语音边界替代固定 60s 是合理下一步**。

### 2.2 对齐范围也应该是语音区

- 强制对齐/字幕对齐的输入应当是 speech 区；BGM/唱歌 token 会把 cue 拉偏。
- FireRedVAD 三集实证：正文 music 占 59.6–78%，singing 8–9%；
  空洞里只有 12/53 是 speech 主导 → 用 AED 过滤“音乐空洞”合理。
- 但 **不能门控删条**；只用于对齐范围/证据。

### 2.3 定向短窗重对齐

- 我们的空洞 53 个/700s，speech 主导只有 12 个/221s；全量重对齐无收益（ADR-008）。
- 只对这 12 个窗口重跑 ASR/FA，成本低、风险小；需要保留上下文和边界连续性。

### 2.4 已跑 45/60/90 A/B（EP001，同一 full-CC/窗口口径）

| chunk | CER | P50 | P90 | 可靠 P90 | char coverage | token zero | collapse gap | RTF |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 45s | **0.3571** | 143 ms | 3464.5 ms | 643.0 ms | **0.456** | 10.8% | 175.2s | 0.3015 |
| 60s | 0.3705 | **129 ms** | **1896.6 ms** | **424.2 ms** | 0.418 | 9.4% | 229.4s | — |
| 90s | 0.3705 | 135.5 ms | 2217.5 ms | 704.8 ms | 0.419 | **7.9%** | **72.4s** | **0.0869** |

读法：

- 45s 文本略好、coverage 最高，但 P90/可靠 P90 明显变差 → **不采纳**；
- 90s RTF/空洞最少，但 P90/可靠 P90 也差 → 不采纳为默认；
- **60s 仍是时间轴最好的固定档**；下一步不是继续调 chunk 长度，而是用 AED
  speech 边界替换“固定 60s 切点”，看能否同时降 collapse 和 P90。
- 口径说明：60s 为既有缓存重算；三档都用同一 full-CC/窗口，45s/90s 为本次实跑。

### 2.5 AED speech 边界切块（EP001）

用 FireRedVAD 的 speech 段，取 gap 中点作为切点（目标 60s、±15s 搜索）：

| 版本 | CER | P50 | P90 | 可靠 P90 | coverage | collapse gap |
|---|---:|---:|---:|---:|---:|---:|
| 60s 固定 | 0.3705 | 129 ms | **1896.6 ms** | **424.2 ms** | 0.418 | 229.4s |
| AED 边界 | **0.3494** | **113 ms** | 2791.6 ms | 720.0 ms | **0.461** | 145.4s |

结论：AED 边界改善 CER/coverage/P50，但 **P90/可靠 P90 变差**，不能直接替换 60s。
可能原因：FireRedVAD speech 事件有噪声/漏检，切点并不总是真静音；下一步需
“只在长 gap 切 + 平滑 speech 区间”再测，不能因为文本变好就上线。

## 3. 全集素材（2026-10 更新）

- 54 个 MKV、54 个日文 CC、108 个中文 ASS（sc/tc）；
- `scripts/eval/build_manifest.py` 生成 `eval/manifest.digimon.full.json`：
  **54 集，dev=EP001–010，test=EP011–054**；
- EP001–003 保留已知 main_window；其余默认 `[95,1240]`，后续按实际 OP/ED 校准；
- 纪律：dev 上做 prompt/切块选择，test 只做最终报告，不拿 test 调参。

### 3.1 Series glossary 已验证

`scripts/nouns/validate_glossary.py` 用 54 集、14,605 个时间对齐 cue 对验证候选词：

- 保留 18 个：`アグモン→亚古兽`、`タケル→阿岳`、`ヒカリ→小光`、
  `デジモン→数码宝贝`、`ファイル島→文件岛` 等；
- 丢弃 2 个：`デジヴァイス`、`サーバ大陸`（没有稳定中文同现）；
- 采用现有中文字幕的 **阿岳**，不采用我先前手填的“阿武”；
- 文件：`glossaries/digimon.series.json`；EP003 名字段 30 条复测 0 违规。

## 4. 优先级（修正版）

1. **翻译术语对照**（已实现）：`ja→zh` 进 prompt；EP003 名字段验证 0 违规。
2. **时间轴语音边界切块**：先 45/60/90 A/B，再用 AED speech 边界；看 P50/P90/coverage/holes。
3. **翻译 prompt 补“逐条/禁止并条”与批间 summary**：成熟项目都有；先小样本 A/B。
4. **speech-aware mapping**：只对齐 speech 区 token；看 P50/P90/漏对白。
5. **12 个 speech 空洞的短窗重对齐**。
6. 两阶段 reflect/series memory/MQM 抽检（质量，不只看 QA）。
