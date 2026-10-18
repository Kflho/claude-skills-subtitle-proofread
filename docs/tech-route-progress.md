# 2026 技术路线实施进度

> **用途**：跨会话续跑。新会话先读本文件，再读 [`tech-route-2026.md`](tech-route-2026.md)、[`ADR-006`](adr/006-stable-asr-core.md)、[`ADR-007`](adr/007-cue-alignment.md)、[`ADR-008`](adr/008-qwen3-long-audio-chunking.md)、[`test-plan.md`](test-plan.md)。
> **最后更新**：2026-10-18（第十轮：ASR-first 重设计 + 技术接入总图 + 模型审计；1386 tests / 79.7%）
> **当前阶段**：EP01–03 视频/日文 CC 齐备；只读基准 + 防过拟合协议落地；**用户确认视频/CC 无真实偏移，并明确日语 CC 边界即测试标准**（不再另建人工金标）。第八轮把“P90 为什么下不去”拆开：test 集 90% 的 matched cue 起点系统性早 −0.37/−0.40 s（同段音频重转写复现，是当前对齐的起点缺陷，不是缓存/参考问题；EP001 仅 −55 ms），且 P90 只覆盖 42–45% 的 CC 字符；FA 塌缩吞掉成段对白（累计空洞 122–229 s）。`fix_by_asr` 整集偏移改为**默认关**，分段重对齐实测更差、留 P3。
> **总目标**：把音频链路从 `WebRTC VAD → kotoba Whisper → 时间重叠匹配` 迁移到 `Qwen3-ASR → Qwen3-ForcedAligner → 全局文本对齐`；默认行为在 P2 验收前保持不变。

## 0. 当前一句话（clear 后从这里开始）

已经完成：P1 稳定 ASR 核心、P2 对齐层、`run_all --asr` 接入、实机验证、held-out 三集基准；第八轮补齐 signed 偏差/覆盖分解/P90 归因/空洞诊断；**第九轮**按用户定位重排主次，并把测试标准收成一条 gate 命令（`release_gate.py` + `eval/gate.baseline.json`）。**用户已确认日语 CC 边界即测试标准**，所以 raw 数字就是验收结果：test P50 400 ms / P90 1196 ms（可靠 P90 510 ms），P2-14 未达标。**测试 1386 passed / 79.7% / 0 failed。**

**主次（用户定位：先应用新技术 + 建立测试标准，再慢慢改精度）：**

- **主 1｜新技术落地**：🟡 真实目录 + 真实视频命名端到端已跑通（`run_all --asr qwen3`，
  exit 0）。但**它仍被塞在旧流程里**：只自动修 13/296，272 条 review 全堆在报告——
  这是设计问题，不是模型问题。**按 ADR-010 重设计**：ASR-first 流程 + Review 阶梯
  （L0 自动 / L1 AI 批审 / L2 AI 复核 / L3 人工残留）；旧流程里的 `ai_fragments`、
  `ai_review_fixes`、`auto_classify` 等替代人工机制必须接回来。
  当前下一步：生成 ASR 工作单、`apply_fixes` 加 `set_time`、自动分级。
- **主 2｜测试标准闭环**：✅ 一条命令已落地
  `python scripts\eval\release_gate.py --manifests ... --baseline eval\gate.baseline.json`；
  按 CC 标准判 test 的 P50≤100 / 可靠 P90≤300，并与冻结基线比回归，退出码 0/1。
  剩余：把 auto/AI/human 三档计数接进 gate；基线只在确认新起点时冻结。
- **主 3｜翻译换成 LLM-only**：✅ 百度路径已删除（代码/配置/测试/文档）；
  ✅ 已找到并验证用户配置的 LLM（`~/.claude/settings.json`，DeepSeek，短句实测通过）；
  ✅ `whisper_fixer` 的替换翻译改走 `lib.llm.call_chat`。下一步：把翻译接进 run_all
  （glossary + 场景 + 长度；润色并入同一 prompt）。**翻译是唯一确定使用 LLM 的阶段**，Review 等
  其它阶段不新增 API 调用；需要 token 时只接受国内服务，国外暂不配置。
- **成本策略**：优先免费/本地。Qwen3 1.7B/0.6B + FA、whisper.cpp + kotoba/large-v3、Demucs
  均已在本机；需要 token 时只接国内服务（DeepSeek 已配；国内云 ASR 可选不默认），
  国外服务暂不配置。FireRedVAD/说话人等缺资产的项按 ADR-011 接缝留位，不阻塞主链路。
- **次｜精度迭代**：起点 −0.4 s、FA 塌缩/空洞、覆盖率、chunk、Whisper 文本混合——
  等主 1/2 稳定后按证据逐个改，一次只动一个变量。

> 细则（怎么读 P50/P90、signed 偏差、空洞统计）都写在 `eval/protocol.md`；
> 不再用分析替代交付，也不在 test 集上反复取证。

## 0.5 clear 后恢复清单

### A. 现在就能做

- [x] **EP01 token 时间轴诊断/修复**：`diagnose_timeline.py` 可离线复现（EP01 main：90/962 零时长、39 个 run、最长 18；segments 19/237）。已加质量评估 + segment 兜底 + 零时长 span 不自动修；但「无语音证据不补时间」，彻底修复留给 P3 VAD/分段重对齐。
- [x] **评测协议落地**：`eval/protocol.md` + `episode_benchmark.py`（只读、dev/test 分组、主故事窗口、CER/边界/计划安全性）；dialogue-only CER 仍待做（CC 字幕卡/歌词会抬高 CER）。
- [ ] **测试补洞**：`lib/whisper_backends.py`、根脚本、`nouns` 旧入口；本轮新增 56 条（1306→1362）。
- [ ] **死代码决策**：`episode_workflow._write_ai_review_json`、`--mode`、`auto_classify` / `auto_clean_glossary` 旧入口。
- [ ] **可选工具化**：CC 清洁 + 主故事窗口 + 参考平移仍是一次性脚本；`diagnose_timeline` 有窗口/偏移参数但不是完整工具。

> **新会话入口**：先读 §0.7 第八轮交接，再读本节 A/B/C、`eval/protocol.md`（含 §2.1 指标读法）、`eval/materials.md` §6/§7、`docs/adr/009-reference-as-benchmark.md`。

### B. 素材已就绪（2026-10-18 22:29）

- [x] **EP02/03 视频**：`D:\videos\Animation\TV\数码宝贝` 下 02/03 `.mkv` 完整（1.37 GB 各）。
- [ ] **用户人工确认 EP02/03 日文 CC 与视频同步**（EP01 已确认；EP02/03 实测有 -0.37/-0.40 s 全局偏移，可能是音频切点/CC 版本差异，需人工确认）。
- [ ] **三集分类**：按实际音频标 `clean` / `overlap` / `bgm`（当前 `unclassified`，不猜）。
- [x] **三集评测**：主故事窗口 [95,1240]、缓存 ASR JSON、`episode_benchmark.py` 基线/最终报告已生成。
- [ ] **后续**：chunk 调参、12 集 manifest、默认切换评估；P0 A/B 已完成（Whisper 文本更好、Qwen3 打轴更好）。

### C. 当前现场（2026-10-18 第七轮）

```text
素材：EP01/02/03 视频 ✅；日文 CC 54 集 ✅；中文 ASS 01–05 ✅
参考：EP00X.main.srt（主故事窗口）；用户人工复核：视频/CC 无真实偏移
ASR：Qwen3 1.7B + FA JSON；Whisper large-v3 q5 JSON（temp/asr/*_main.*.json）
基准：temp/reports/digimon_qwen3_raw.{json,md}、digimon_whisper_raw.{json,md}
协议：eval/protocol.md；ADR-009（参考=考卷）
测试：1362 passed / 79.6% / 0 failed
提交：c902ee7（无偏移修正 + Whisper A/B）
```

### D. 第七轮待办（按优先级）

1. **P2-14 raw 验收**：test CER 0.2633、P50 400 ms、可靠 P90 510 ms、±100 ms 5.1%；
   P90 远未达标。不要再把整集/局部偏移写进指标。
2. **测量有效性**：加 dialogue-covered CER（只统计有语音覆盖的 cue）和 P90 outlier 报告；
   不改参数，先让数字能解释。另：`fix_by_asr` 仍默认整集偏移（既有行为），在无真实偏移的
   素材上运行前要确认；下一步给它加显式开关。
3. **P90 机制修复**：在 dev 上试分段重对齐（FA 对短窗口更稳），处理零时长/坍缩；有效再进 test，不靠调阈值。
4. **A/B 后续**：Whisper large-v3 文本更好、打轴更差；评估“Whisper 文本 + Qwen3-FA 对齐”
   的混合方案（需解决把 Whisper 文本喂给 FA 的接口），或至少把 Whisper 作为文本候选。
5. **chunk 45/60/90**：只用零时长率/RTF 在 dev 选，test 验证一次。
6. **P4 翻译/润色**：prompt 用原文 + glossary + 场景；参考字幕不回流。

## 0.6 第七轮交接（2026-10-18，clear 入口）

**提交**：`c902ee7`（无偏移修正 + Whisper A/B）、`8d13613`（状态表）。
**测试**：1362 passed / 79.6% / 0 failed（`pytest -q`，约 8 s）。
**当前默认**：`ASR_ENGINE=whisper` 不变；Qwen3 路线走 `run_all --asr qwen3` / 独立 CLI。

### 结论（先看这 5 条）

1. **用户人工复核：EP01–03 视频/CC 没有真实偏移**。`estimate_global_offset` 的 -0.37/-0.40 s 是
   ASR 起点 vs CC 显示起点的系统差；**raw 才是验收口径**。`align_cues --global-offset` 已改为 opt-in。
2. **Qwen3 raw test**：CER 0.2633、match 153/687、P50 400 ms、P90 1196 ms、±100 ms 5.1%、
   可靠匹配 P90 510 ms。**P2-14 未达标**（目标 P50 ≤100 / P90 ≤300）。
3. **Whisper large-v3 q5 raw test**：CER 0.2041、match 195/687、P50 757 ms、可靠 P90 1325 ms。
   **文本更好、打轴更差**；默认没有切，混合方案待评估。
4. **真实收益**：结尾 display linger 保留后，raw 下三集原本 106 条自动修复会缩短结尾，
   现在 0 条；零时长 span 不再自动修；新增质量诊断/只读基准/防过拟合协议。
5. **被否**：全局偏移、局部偏移、cursor 回看；overlong trim 保持 opt-in。
   不要把 offset-corrected 的 57.5 ms / 68.3% 再写进任何验收。

### 恢复入口

```powershell
# 测试
cd tests; pytest -q

# 三集 raw 基准（Qwen3）
python scripts\eval\episode_benchmark.py --manifest eval\manifest.digimon.json `
  --output temp\reports\digimon_qwen3_raw.md --json-out temp\reports\digimon_qwen3_raw.json

# Whisper A/B（同参考、同窗口）
python scripts\eval\episode_benchmark.py --manifest eval\manifest.digimon.whisper.json `
  --output temp\reports\digimon_whisper_raw.md --json-out temp\reports\digimon_whisper_raw.json

# 时间轴诊断
python scripts\eval\diagnose_timeline.py --asr-json temp\asr\digimon_ep001_main.qwen3.json
```

关键文件：`eval/protocol.md`、`docs/adr/009-reference-as-benchmark.md`、
`eval/reports/digimon_benchmark_2026-10-18.md`、`eval/manifest.digimon*.json`。

### 下一步（按优先级）

1. **P90 机制修复**：P3 FireRedVAD 或分段重对齐，处理 FA 零时长；先 dev，test 只验证一次。
2. **文本路线决策**：Qwen3 单模型 vs “Whisper 文本 + Qwen3-FA 对齐”混合；Whisper 文本明显更好。
3. **测量有效性**：dialogue-covered CER + P90 outlier 报告。
4. **收尾**：`fix_by_asr` 仍默认整集偏移，加显式开关；chunk 45/60/90 用无参考指标在 dev 选。
5. **P4**：翻译/润色 prompt 证据化；默认切换等 P2-14 达标。

## 0.7 第八轮交接（2026-10-18，clear 入口）

**提交**：`8c7615b`（测量有效性 + 偏移开关 + 空洞诊断；前序 7 轮见 §0.6）。
**测试**：1378 passed / 79.7% / 0 failed（仓库根跑 `pytest -q tests`，约 9 s）。
**默认**：`ASR_ENGINE=whisper` 不变；**`fix_by_asr` 整集偏移默认关**，`--asr-global-offset`
或 `ASR_GLOBAL_OFFSET=1` 才应用（估计值仍写入 `temp/scans/<EP>.alignment.json`）。

### 结论（先看这 6 条）

1. **0.4 s 的 P50 是当前对齐的起点缺陷，不是逐 cue 噪声，也不是参考噪声**。
   **用户已确认日语 CC 边界即测试标准**，所以这些 signed 数字直接判定：Qwen3 test
   matched cue signed P50 −371/−397 ms、early 89–92%、P90−P10 481/559 ms（离散不大）；
   dev EP001 signed P50 −55 ms、early 63%。已用同一段音频重新转写复现（token 时间与
   缓存差 ≤0.02 s，抽音频也与视频逐样本对齐），排除缓存过期/抽音频偏移；偏差随集不同，
   不能用全局常数。**raw 直接判定，不做偏移修正；修偏差要在对齐/音频侧。**
2. **P90 只在 42–45% 的 CC 字符上计算**。Qwen3 字符覆盖 0.422/0.428/0.445（Whisper
   0.473/0.457/0.521），matched-span CER 0.069–0.084，covered-span CER 0.165–0.220。
   P90 旁边必须同时看覆盖率与 match 数（49/79/74）。
3. **P90 尾部主要是错误匹配，不是边界误差**。>300 ms：15/65/67 条；`|start err|>2 s`
   只有 5/6/6 条（字幕卡、重复台词）。归因计数（EP001/02/03）：
   zero_span 0/0/2、collapsed 0/3/0、stretched 3/1/5、short 3/7/1、low-conf 6/3/9、
   ambiguous 3/10/7、plain 7/52/53。test 的 `plain` 大头就是结论 1 的起点偏差带。
4. **FA 塌缩吞掉整段对白**。EP001/02/03 有 19/7/9 个「塌缩 run + 前置空洞」，累计
   229/122/96 s 空洞、152/44/91 个字符堆在单点；EP003 另有 52.32 s 的单 token。
   这是覆盖率上不去的直接原因（EP002 的 64.4→115.8 s 空洞里 6 条 CC 对白全部无轴）。
5. **分段重对齐（同一 FA 重跑）已被实测否定**。dev EP001 最大空洞（20.56 s、18 units/36 字）
   重跑后 19 个 unit 里 11 个零时长，单个「門」吃到 8.8 s；EP002（test，仅诊断、未据此调参）
   的 51 s 空洞重跑后「フィシーズ」吃掉 41 s。5–6 s 短窗口会被 clip 起点夹住（首 token
   从 0 开始）。overlong trim 也几乎无效（test rel P90 510→501 ms）；cue 级 rate trim 更差
   （dev rel P90 424→1086 ms）。三者留作负结果，P3 必须先在音频侧切边界（VAD）。
6. **`fix_by_asr` 整集偏移改为默认关**；`run_all --asr-global-offset` 显式开启。
   注意：开关只停整集平移，**逐 cue 的 start fix 仍按 ASR 起点走**，所以 0.37–0.40 s 的
   起点偏差仍会以逐条形式落盘；在偏差修掉前不要把它当“已解决”。同时修正 aligner
   适配器忽略 `ASRConfig.model` 的 bug（离线/本地模型路径不再回落 Hub）。

### 恢复入口

```powershell
# 测试 + 覆盖率（一定在仓库根跑；在 tests/ 下 --cov 会换成另一套 source 解析）
python -m pytest -q tests
python -m pytest -q tests --cov --cov-report=term-missing

# 三集 raw 基准（Qwen3 / Whisper 同参考、同窗口）
python scripts\eval\episode_benchmark.py --manifest eval\manifest.digimon.json `
  --output temp\reports\digimon_qwen3_r8.md --json-out temp\reports\digimon_qwen3_r8.json
python scripts\eval\episode_benchmark.py --manifest eval\manifest.digimon.whisper.json `
  --output temp\reports\digimon_whisper_r8.md --json-out temp\reports\digimon_whisper_r8.json

# 时间轴诊断 / 空洞
python scripts\eval\diagnose_timeline.py --asr-json temp\asr\digimon_ep002_main.qwen3.json
```

关键文件：`eval/protocol.md`（§2.1 新指标读法）、`docs/adr/009-reference-as-benchmark.md`、
`temp/reports/digimon_qwen3_r8.*`、`temp/reports/digimon_whisper_r8.*`。

### 下一步（按主次）

- **主 1｜新技术落地**：`run_all --asr qwen3` 在真实字幕目录跑通一集（含失败回退、报告/备份、
  ASS 策略），验证默认切换所需的开关和回滚；先保证“换上去能用”。
- **主 2｜测试标准闭环**：把三集基准收成一条 gate 命令：`CC 边界标准 → P50/P90/±100ms/
  CER/覆盖率`，读基线、给通过/回退判定；test 只在 release candidate 跑一次。
- **次｜精度迭代**：起点偏差（0.37–0.40 s）、FA 空洞/塌缩、覆盖率、chunk 45/60/90、
  Whisper 文本 + Qwen3-FA 混合——主 1/2 稳定后按证据逐个做，一次只动一个变量。
- 细则与负结果保留在 `eval/protocol.md` §2.1/§3.1，不再在会话里反复取证。

## 0.8 第九轮交接（2026-10-18，主次重排 + release gate）

**用户定位**：先应用新技术、建立测试标准，再慢慢改精度；不要陷进单点技术细节。
**提交**：`d40ce74`（CC 即边界测试标准）+ 本轮 gate 提交（见 git log 顶部）。
**测试**：1386 passed / 79.7% / 0 failed。

已完成：

1. **CC 即边界测试标准**：协议/素材/ADR/报告口径统一，删除“人工金标”计划；
   raw P50/P90 直接判定，当前 P2-14 未达标。
2. **release gate 落地**：`scripts/eval/release_gate.py` 一条命令跑两个 manifest，
   test 目标 P50 ≤100 ms / 可靠 P90 ≤300 ms，并对 `eval/gate.baseline.json` 做回归
   （P50 +20 ms、可靠 P90 +50 ms、CER +0.01、字符覆盖 −0.02），退出码 0/1。
   当前判定：Qwen3 与 Whisper 均 FAIL（P50 400 / 756 ms，可靠 P90 510 / 1325 ms）。
3. **主 1 端到端跑通**：真实目录 + 真实视频命名（`01、漂流？…mkv`）跑完整集，
   exit 0；24 chunks / 1188 tokens；13 auto time fix + 272 review。顺带修掉视频名
   不补零导致 ASR 路由静默 0 条的 bug（`find_video` / `SubtitleSession`），并让 ASS
   提前退出、ASR 失败都打印真实原因；这两条冒烟也过了（文件不变、exit 0）。
   冒烟细节见 `eval/materials.md` §8。
4. **测试**：+14（gate 9 + 视频名解析 3 + 早退/失败提示 2），总计 1392 / 79.8%。

下一步（按主次）：

1. **主 1（收尾）**：默认切换的开关/回滚写清楚（目前是每次 `--asr qwen3`，回滚=去掉；
   `ASR_ENGINE` 不影响 run_all 是否走 ASR 路线）；ASR 失败是否要非 0 退出待定。
2. **主 2**：把 gate 接进日常流程——改完必跑；基线只在确认新起点时 `--update-baseline` 冻结。
3. **次**：起点偏差、FA 空洞/塌缩、覆盖率等，按 gate 数字逐个迭代，一次只动一个变量。

## 0.9 新技术接入台账（第九轮盘点，对照任务书）

> 全量技术（含备选/拒绝/云/说话人/分离/翻译模型）见 **ADR-011 §4**；本表只是当时的冲刺视图。

对照 `tech-route-2026.md` §0.1 / §8.1，按「代码 / 资产 / 真机测试」三列看：

| 技术（任务书优先级） | 代码 | 本机资产 | 真机测试 | 状态 |
|---|---|---|---|---|
| Qwen3-ASR 1.7B（P0） | ✅ adapter/worker/router | ✅ `models/` | ✅ 3 集 + 端到端 | ✅ `--asr qwen3` opt-in |
| Qwen3-FA 0.6B（P0） | ✅ | ✅ | ✅ | ✅ |
| Qwen3-ASR 0.6B 草稿（P0） | 🟡 只能换 model，无模式/CLI | ✅ | 🟡 试跑 10 分钟未完成 EP001；无日语独立基准 | ⛔ 审计：非领先，暂不接 |
| Whisper large-v3 兜底（P0） | ✅ adapter | ✅ whisper.cpp CUDA + kotoba/large-v3 | ✅ 历史 A/B | ⛔ 按用户规则：不下载、不再测试 |
| FireRedVAD / AED（P1） | ✅ provider/CLI 已接线 | ✅ 权重 0.01GB + 代码 | ✅ EP001 442 事件 | ✅ 主流程未消费 |
| 置信度 v2：跨引擎/可读性（P1） | 🟡 对齐置信度有，跨引擎/LLM 无 | ⚠️ 跨引擎需第二本地引擎；LLM 可读性不新增调用 | ⚠️ | ⬜ |
| glossary-first → ASR context（P1） | 🟡 context 通到 worker；run_all 不传，`ASR_CONTEXT_FILE` 未使用 | ✅ glossary 可生成 | ❌ | ⬜ 零新资产，可立即接 |
| LLM 主翻译 + glossary（P1） | ✅ 独立 `scripts/translate_srt.py`；百度路径已删除 | ✅ `~/.claude/settings.json` 已配 DeepSeek，短句实测通过 | ❌ | 🟡 工具在，未进 run_all；**翻译是唯一 LLM 例外**（ADR-011） |
| 人声分离默认关（P2） | ✅ demucs，仅 Whisper 路径 | ✅ HTDemucs | ❌ ASR 路径 | ⛔ 审计：老模型，只作 fallback |
| OP/ED AED + 指纹（P2） | ⬜ | 依赖 FireRedVAD | ⬜ | ⛔ |
| 切片重对齐（P2） | ⬜（还是旧 spot fix） | ✅ FA | ❌ | ⬜ |
| 说话人分离（P5） | ⬜ | ⬜（pyannote 需 HF token → 排除；3D-Speaker 免费待评估） | ⬜ | ⬜ |
| VibeVoice（P5） | ⬜ | ⬜ 7B | ⬜ | ⛔ |
| 云兜底（P5） | ⬜ | ❌ 付费/token | ⬜ | ⛔ 免费原则排除 |

**结论**：P0 的 ASR/FA 已接并跑过；P1 里只有 **glossary-first** 是零新资产、可立即接，
**0.6B** 是资产在本机、可直接测。FireRedVAD / VibeVoice / 云兜底被离线挡住，
LLM 翻译被 key 挡住，Whisper 运行时被依赖挡住（只剩历史缓存做 A/B）。

## 0.10 第十轮：ASR-first 重设计 + Review 阶梯 + 去百度

**用户决定（2026-10-18）**：
1. 百度翻译静默砍掉，全部走 LLM；之后不再维护/测试传统机翻。
2. 流程按选用的技术设计（取长补短），不硬塞进旧流程；旧流程只是工程实现，必要时重设计。
3. review 不该有 272 条；旧流程里有很多替代人工的机制，先读懂再设计。
4. **优先免费/本地；需要 token 只接受国内服务**，国外暂不配置。翻译阶段用已配置的
   DeepSeek（国内）；Review 不新增 LLM API 调用；国内云 ASR 可选但不默认。

**已核实的本机资产（2026-10-18）**：
- LLM：`~/.claude/settings.json` 的 `env` 已配 `LLM_API_KEY` + `LLM_BASE_URL`（DeepSeek）
  + `LLM_MODEL=deepseek-v4-pro`；短句翻译实测返回“早上好，你还好吗？”。Claude Code 自动注入，
  DSH shell 需手动加载。
- Whisper：`whisper-cli.exe` + kotoba q5 / large-v3 q5 在
  `D:/software/video/whisper-cublas-12.4.0-bin-x64/`；之前“运行时缺失”的判断作废，fallback/A-B 可用。
- Qwen3-1.7B/0.6B + FA、HTDemucs 在 `models/` / HF cache。

**已落库**：
- `docs/adr/010-asr-first-pipeline.md`：ASR-first 数据流、三种模式、Review 阶梯、
  LLM-only 翻译、迁移步骤。旧 3-Phase 只保留编排骨架。
- `docs/adr/011-technology-integration-map.md`：**全量技术接入总图**。所有候选技术
  （含备选/拒绝）都映射到接缝：Ingest/Separator、AudioEvents（VAD/AED/说话人）、
  ASR/FA registry、Ensemble、Alignment/Repair、Evidence/Review、Glossary/Translation、
  Evaluation。可选能力默认 null 实现，新增 = adapter/stage + 配置 + 证据，不改流程。
- `CONTEXT.md` 核心原则 7/8/9/10：流程按技术设计；Review 阶梯；翻译全部 LLM；
  技术只通过接缝接入。
- 百度路径删除：`fix/translate_srt.py`、`BAIDU_*` 配置、`test_baidu_translate.py`、
  SKILL/技术路线文档中的 Baidu 说明；`whisper_fixer` 的替换翻译改走 `lib.llm.call_chat`，
  无 key 时保留原文交给翻译阶段。

**旧流程里可直接复用的“替代人工”机制**（ASR 路由目前没接）：
`whisper_pipeline` 的置信度 triage（high/retry/none）、`ai_fragments_<EP>.json` +
`Fixer.apply_ai_fragments()`、`ai_review_candidates.json` → `ai_review_fixes.json` →
`apply_fixes`、`auto_classify`/`auto_clean_glossary`、OP/ED 预替换、`reuse/apply`、`polish_zh`。

**Review 目标（按 CC 条数，每集）**：L0 ≥60%（现状 13/285≈5%）；L1+L2 覆盖剩余 ≥90%；
L3 人工 ≤5%（现状 272 条全堆在报告）。

**下一步（实现顺序）**：
1. ✅ `fix_by_asr` 为每条非自动修复生成 `<EP>.asr_review.json`（证据 + 空 decision）。
2. ✅ `apply_fixes` 加 `set_time`（SRT）；run_all 的 AI review 步会列出 ASR 工作单。
3. 自动分级：高置信 `replace_text`（`--asr-fix-text` 时）直接 L0；其余进 L1。
4. ASR-first 编排收口：`--asr qwen3` 跳过 VAD 门控/Whisper 修复区，Phase 3 接 LLM 翻译。
5. 评测加 auto/AI/human 三档计数，进 gate，防止“把 review 挪个文件”冒充优化。

**测试**：1386 passed / 79.7%（AudioHints +6；上一轮删百度 −15）。

**0.6B 试跑（同轮，未完成）**：三集主故事窗口用 `Qwen3-ASR-0.6B + FA` 重转，
10 分钟没跑完 EP001（1.7B 约 90s/集），GPU 37% / 4.9GB，无报告产出，手动终止。
作为草稿模式前要先加 max-token/单集时间上限 + 重复检测，并查 qwen3-0.6B 是否在长 chunk 上循环。

## 0.11 技术审计（2026-10）：先验领先，再下载

**结论**：不是所有“新”都领先。下载前按发布时间、独立日语基准、域匹配、许可/本地可行性审了一遍。

| 结论 | 技术 | 依据 |
|---|---|---|
| ✅ 保留 | Qwen3-ASR-1.7B + Qwen3-FA | 2026-01；日语媒体 WER 0.185 第一；FA 日语 AAS 42.2ms |
| ✅ P0 下载 | FireRedVAD + AED | 2026-03；FLEURS-VAD-102 F1 97.57，超 Silero 95.95/TEN 95.19；0.01GB |
| ✅ P1 下载 | VibeVoice-ASR-BitNet | 2026-01；BitNet GGUF **1.58GB**（CPU）；`asr_infer.exe` 已构建；单集未测 |
| ✅ P2 下载 | Mel-Band RoFormer | fp16 456MB + fp32 913MB；比 Demucs 新；运行时未接 |
| ⛔ 淘汰 | Parakeet JA / ReazonSpeech v2 | 2024 年模型，日语 WER 0.344/0.348，非领先 |
| ⛔ 淘汰 | Whisper 全系 / WhisperX | 用户规则：只保留本机 CUDA，不下载/不再测试 |
| ⛔ 淘汰 | BuzzASR | 2026-09 但基于 Whisper large-v3，违反约束 |
| ⛔ 淘汰 | pyannote community-1 | gated/外国 token，暂不配置 |
| ⛔ 降级 | Demucs | 2021/22 老模型，只作 fallback |
| ⛔ 暂不接 | Qwen3-ASR-0.6B | 无日语领先证据；试跑 10min 未完成 EP001 |

**下载/接线状态（2026-10-19）**：
- FireRedVAD/AED：权重 0.01GB 已下；`.venv-qwen` + `fireredvad` 0.0.2 已验证；
  `lib/audio/base.py` + `lib/audio/fireredvad.py` + `scripts/audio/detect_events.py` 已接线；
  EP001 已生成 `temp/scans/EP001.audio_hints.json`（442 事件；speech 48.8% / singing 8.9% / music 78.1%）。
  主流程尚未消费 hints（下一步 OP/ED/空洞）。
- VibeVoice-ASR-BitNet：只有 GGUF（1.58GB，不碰 13GB safetensors）；VibeASR.cpp 已 clone + MinGW 构建出
  `models/VibeASR.cpp/build/bin/asr_infer.exe`；单集测试未跑。
- Mel-Band RoFormer：fp16/fp32 权重已下；ComfyUI 格式，运行时/适配器未接。
- Whisper 系：一个都没下，按用户规则只保留本机 CUDA。

**证据**：Neosophie 2026-05 日语 9 模型实测、Qwen3/FireRedVAD 模型卡与 HF 发布时间、
FireRedASR2S 论文。详细报告：`docs/model-audit-2026-10.md`；调用手册：`docs/model-assets.md`。

**下一步**：1) 主流程消费 `AudioHints`（OP/ED 候选、FA 空洞诊断）；
2) VibeVoice 单集“角色归属是否改善翻译”实验；3) Review 自动分级 + gate 计数。

## 1. 状态总览

| 阶段 | 内容 | 状态 | 验收 |
|---|---|---|---|
| P0 | 边界测试标准 + 基线指标 | ✅ 用户确认日语 CC 边界即测试标准；三集基准 + 测量有效性（signed/覆盖/空洞）已落地 | `episode_benchmark` 新报告字段；CER/边界/偏差可复现 |
| P1 | 稳定核心 + Qwen3/Whisper 适配 + 评测/CLI | ✅ 3 集跑通；Qwen3 vs Whisper large-v3 A/B 已记录；默认不变 | 契约/registry/router/CLI 稳定；A/B 有 raw + matched-span 数据 |
| P2 | Qwen3-FA + cue mapper + align_repair | 🟡 核心/CLI/`run_all --asr` 完成；**P2-14 按 CC 标准未达标** | raw test P50 400 ms / 可靠 P90 510 ms；signed P50 −384 ms（起点缺陷） |
| 测试 | 1386 tests / 79.7% 覆盖 / 0 failed | 🟡 主链补齐；剩余根脚本/nouns 旧入口/whisper_backends/align_cues | 关键路径每个分支至少一条测试 |
| P3 | FireRedVAD/AED，VAD 降级为提示 | ⬜ 未开始 | speech/music/singing 时间线对比 |
| P4 | glossary-first + 翻译 prompt，百度下线 | ⬜ 未开始 | 专名 F1 / 翻译人工评分 |
| P5 | VibeVoice / 云兜底 | ⬜ 未开始 | 单集实验 + 成本/隐私评估 |

图例：⬜ 未开始 / 🟡 进行中 / ✅ 完成 / ⛔ 阻塞

## 2. 稳定核心原则（硬约束）

> 用户明确要求：**代码不能反复重写；新模型只写一个新接口，不碰核心。**

| 层 | 文件 | 变更频率 | 规则 |
|---|---|---|---|
| 契约 | `scripts/lib/asr/base.py` | 极低 | `ASRResult/Token/Segment/ASRConfig/BackendInfo/ASRBackend/Aligner`；JSON 带 `schema_version` |
| 注册器 | `scripts/lib/asr/registry.py` | 极低 | 自动 import `adapters/*.py`，读 `NAME/KIND/INFO/build` |
| 路由 | `scripts/lib/asr/router.py` | 极低 | 只依赖 registry + 契约；**不 import 模型** |
| 适配器 | `scripts/lib/asr/adapters/<name>.py` | 高 | 每模型一个文件；实现 `available()/transcribe()` |
| Worker | `scripts/asr/qwen3_worker.py` | 中 | 引擎自己的进程；协议固定 `ping/load/transcribe/align/shutdown` |
| CLI/评测 | `scripts/asr/qwen3_transcribe.py`、`scripts/eval/asr_eval.py` | 低 | `--engine` 从 registry 动态取值 |

**新增模型 5 步**：

1. 复制 `templates/asr_adapter.py` → `scripts/lib/asr/adapters/<name>.py`；
2. 填 `NAME/KIND/INFO/build`，实现 `available()/transcribe()`；
3. 需要 token 时间戳的，另写 `KIND='aligner'` 适配器，或后端暴露 `align()`；
4. 加契约测试（`validate_backend` + fake 调用，不下载模型）；
5. `python scripts/asr/qwen3_transcribe.py --list-engines` 应自动看到新引擎。

**禁止**：在 router/CLI 里加 `if engine == 'xxx'`；模型返回私有 JSON；每个模型复制一套评测/修复。模型特有参数放 `ASRConfig.options` 或 adapter 的 `build(**kwargs)`。

## 3. 本次已完成

### P0

- [x] P0-1：`eval/manifest.example.json` 格式（id/category/audio|video/reference_srt/context）
- [x] P0-2：`scripts/eval/asr_eval.py`：CER（NFKC、去标点、字符级）、segment↔cue 边界诊断、P50/P90、RTF、Markdown/JSON 输出
- [x] P0-3：`eval/README.md` + 12 集模板（实际路径待用户填）

### P1

- [x] P1-1：`lib/asr/base.py`：`Token/Segment/ASRResult/AlignResult/ASRConfig/BackendInfo/ASRBackend/Aligner/validate_backend`，`CONTRACT_VERSION=1`
- [x] P1-2：`lib/asr/adapters/whisper.py`：旧 Whisper 包装成同一契约（segment 级，tokens 为空）
- [x] P1-3：`lib/asr/adapters/qwen3.py`：worker 客户端 + adapter；`available()` 检查解释器/worker/当前环境 `qwen_asr`
- [x] P1-4：`scripts/asr/qwen3_worker.py`：长驻 worker，惰性加载模型，`ping/transcribe/align/shutdown`，错误返回 `ok:false`
- [x] P1-5：`lib/asr/registry.py`：自动发现 + `register/build/available/auto_candidates`；`lib/asr/router.py` 只走 registry
- [x] P1-6：`scripts/asr/qwen3_transcribe.py`：单文件/单视频 CLI，JSON + 可选 SRT；`--list-engines` 动态列出
- [x] P1-7：`lib/config.py` 增加 `ASR_ENGINE/ASR_MODEL/ASR_DEVICE/ASR_DTYPE/ASR_MAX_NEW_TOKENS/ASR_REQUEST_TIMEOUT/ASR_CONTEXT_FILE/ASR_FALLBACK/ALIGNER_ENGINE/ALIGNER_MODEL/QWEN_PYTHON`；默认仍 `ASR_ENGINE=whisper`
- [x] P1-8：测试：契约/路由/注册器/worker 协议/fake worker/CER/边界/Whisper 适配/CLI SRT
- [x] P1-8b：`templates/asr_adapter.py` + `docs/adr/006-stable-asr-core.md`
- [x] P1-9：真实模型 smoke test（0.6B + 1.7B + aligner，见 §4）
- [ ] P1-10：3 集 A/B 记录（待素材）

### P2

- [x] P2-1：`lib/alignment/base.py`：`CueAlignment/AlignmentConfig/AlignmentReport`、状态与动作词表、文本归一化（NFKC/去 tag/去标点）、`schema_version`
- [x] P2-2：`lib/alignment/timeline.py`：`ASRResult` → 字符时间轴（token 优先、segment 兜底、无时间戳返回空轴）
- [x] P2-3：`lib/alignment/cue_mapper.py`：时间窗锚点 + 单调 cursor + `SequenceMatcher(autojunk=False)` + 最佳簇 + 精确串就近选择
- [x] P2-4：`lib/alignment/diagnostics.py`：语音覆盖率、重复度、语种占比、内部停顿、置信度投票、边界统计（MAE/P50/P90/±100ms）
- [x] P2-5：`lib/alignment/aligner.py`：registry 对齐服务；`ALIGNER_ENGINE=none` 可整体关闭；不 import 模型
- [x] P2-6：`fix/align_repair.py`：修复计划（`fix_time/replace_text/review/split/delete_candidate`）、时间码应用（邻居安全）、单 cue 窗口重对齐（ffmpeg + aligner）
- [x] P2-7：`scripts/asr/align_cues.py`：端到端 CLI（SRT/ASS + ASR JSON/音频/视频；默认 dry-run；`--write`/`--fix-text`）
- [x] P2-8：测试 101 项（`test_alignment_*`、`test_cue_*`、`test_align_repair`、`test_align_cues_cli`、`test_alignment_bench`）
- [x] P2-9：真实 cue 结构回归 + `scripts/eval/alignment_bench.py`（魔法使莎莉 EP001，405 cue；见 §4）
- [x] P2-10：`docs/adr/007-cue-alignment.md`
- [x] P2-12：`Fixer.fix_by_asr()` + `run_all --asr/--asr-python/--asr-fix-text`（默认 time-only；ASS 拒绝写回；诊断落 `temp/scans/<EP>.alignment.json`）
- [x] P2-13：真实日语视频实机验证（莎莉 EP001；发现 180s 分块塌缩 → worker 自切 60s + 补零；见 §4）
- [x] P2-15：整集偏移估计（`estimate_global_offset` + `shift_cues`）；EP001 +0.5s 平移测试 367/370 还原
- [x] P2-16：测试基线（576 passed / 36.3% 覆盖 / 0 failed）+ `docs/test-plan.md` 能力矩阵
- [ ] P2-14：按日语 CC 边界（用户确认即测试标准）做 P50/P90 验收；当前 raw test P50 400 ms / 可靠 P90 510 ms，未达标 ← **下一步优先：先修起点偏差**
- [ ] P2-11：参考无关 A/B（原计划阿童木 193 集已取消；改等用户提供的视频素材）

### 测试（2026-10-18 第二轮：P0 链路补齐）

- [x] T1：`run_tier1`/`run_tier2` 编排（真实 WAV 拼接 + mock `run_whisper`），Tier 2 转录 sidecar
- [x] T2：`get_speech_timeline` 的 WebRTC / silencedetect 两条路径（fake webrtcvad + 16k WAV）
- [x] T3：`align_and_fix` 覆盖阈值 / 无效文本 / 转录写盘失败
- [x] T4：faster-whisper / openai-whisper 后端（fake 模块）+ 缺依赖 / 异常分支
- [x] T5：`whisper_pipeline.main()` 的 VAD 闸门（默认不删 / `--vad-clean-apply` / `--dry-run` 覆盖）、Tier 升级、dry-run 不写 SRT
- [x] T6：翻译链路（prompt/glossary、OP/ED 只覆写歌词、JSON 容错、回显重试、百度签名+降级、resume、润色 dry-run）
- [x] T7：专名链路（extractor 对齐/sidecar、aggregate 机械合并+LLM 决策、build_glossary 过滤与输出、find_suspect 规则与聚类）
- [x] T8：OP/ED（器乐/歌词/正片三分类、AI review/apply、只填空行、±3s 模板匹配）
- [x] T9：编排（`run_all.main` 顺序/`--resume`/`--dry-run`/`--apply-ai-review`/ASR 路由；`_run_pipeline` 步骤选择；`step_audio` 分派）
- [x] T10：本轮修掉 4 个真 bug 并各配回归测试（见 §4）

### 测试（2026-10-18 第四轮：P1/P2 主链收尾）

- [x] T11：`run_all` 各 `step_*` 副作用 —— scan 缓存/命令参数、nouns 聚合与 AI 候选、apply 汇总/去重、deliver 降级报告、clean、AI 复核应用、glossary 追加、suspect 报告；`main` 的资源/flag 分支。
- [x] T12：`scan/unified_scanner.py` —— `scan_all` 聚合/集过滤/空目录、VAD 缓存/跳过/失败、findings/issues 写出、CLI `main`、jieba/janome 与 n-gram 回退。
- [x] T13：`apply/apply_fixes.py` —— `main` 全分支、ASS `replace_name`、繁→简（SRT+ASS）、degloss、报告/dry-run。
- [x] T14：`qwen3_worker` chunk/offset/零时长/segments/align/协议；`qwen3_transcribe` CLI 选项与返回码。
- [x] T15：`ass_repair` CLI、`reuse/align.py` CLI。
- [x] T16：本轮修掉 5 个真 bug 并各配回归测试（见 §4 “第四轮测试基线”）。

### 测试（2026-10-18 第五轮：主链收尾 2）

- [x] T17：`lib/whisper_utils.py` 音频/VAD/ASS/文本判定（45 → 100%），并修 `separate_vocals` 裸文件名崩溃。
- [x] T18：`fix/episode_workflow.py` 各 `step_*` / `_run_pipeline` / `main`（36 → 100%），修 `step_translate` 日志语言。
- [x] T19：`reuse/common.py` + `reuse/apply.py`（49/65 → 100/98%）：variants/参考加载/目标对齐/续行合并/R6/CLI。
- [x] T20：`eval/asr_eval.py`（47 → 99%）：`_resolve_audio`/`evaluate_episode`/`_summary`/`_markdown`/`main`/limit/list-engines。
- [x] T21：新增 `eval/clean_cc_reference.py`（日文 CC → 对白参考）+ 11 项测试；数码宝贝 54 集已清洁（19,812 → 18,903 cue）。

## 4. 验证记录

### 单元测试（2026-10-18）

```text
python -m pytest -q tests/test_asr_*.py tests/test_qwen3_*.py
58 passed in 3.26s
```

覆盖：

- `test_asr_base.py`：token/segment/result round-trip、`build_segments`、`schema_version`
- `test_asr_registry.py`：自动发现、注册新 adapter、`validate_backend`、auto 不抛异常
- `test_asr_router.py`：主引擎/fallback/none/auto
- `test_qwen3_client.py`：fake worker 的 ping/transcribe/align/错误/超时/close
- `test_qwen3_worker.py`：`--once` ping、unknown cmd、WAV 时长、对齐 items 转换
- `test_asr_eval.py`：CER 替换/插入/删除、边界 MAE/miss
- `test_asr_whisper.py`：segment 映射、空输出、缺模型
- `test_qwen3_cli.py`：SRT 时间码/写盘

### 环境（2026-10-18）

- 主环境：Python 3.12.10、torch 2.6.0+cu124、CUDA True、RTX 3080 Ti Laptop 16GB
- 隔离环境：`.venv-qwen` 已建，`qwen-asr==0.0.6` + `transformers==4.57.6` + `modelscope==1.40.2` 安装成功
- ModelScope 下载完成：`models/Qwen3-ASR-0.6B`（1.88GB）、`models/Qwen3-ASR-1.7B`（4.70GB，两 shard）、`models/Qwen3-ForcedAligner-0.6B`（1.84GB）
- `--list-engines`：`qwen3`（asr）、`whisper`（asr）、`qwen3_fa`（aligner）正常列出

### 真实 smoke test（2026-10-18）

输入：`temp/smoke/ja_sample.wav`（Windows Haruka TTS，16k mono，4.08s，文本「お前、何してんだよ。早く行くぞ。」）

```text
Qwen3-ASR-0.6B + Qwen3-ForcedAligner-0.6B
→ text: お前何してんだよ早く行くぞ（13 chars）
→ tokens: 10（字符/词级，0.08s–3.36s）
→ segments: 2（0.08–1.76 / 2.56–3.36）
→ SRT: temp/smoke/ja_sample.srt
→ 主环境经 worker 调用：exit=0，JSON 正常
→ 直接 align：16 chars → 10 tokens，首 token 0.08–0.48，末 token 3.20–3.36
```

结论：ASR、ForcedAligner、worker 协议、主环境→隔离 venv 链路、SRT 输出均跑通；0.6B 文本与已知台词一致（仅缺标点），1.7B 连标点也对上：

| 模型 | 输出 | tokens | segments | 耗时 |
|---|---|---:|---:|---|
| 0.6B | お前何してんだよ早く行くぞ | 10 | 2 | ~1s（模型已载） |
| 1.7B | お前何してんだよ。早く行くぞ。 | 10 | 2 | ~1s（模型已载） |

1.7B SRT：

```srt
1
00:00:00,080 --> 00:00:01,760
お前何してんだよ。

2
00:00:02,560 --> 00:00:03,360
早く行くぞ。
```

> SRT 标点保留：worker 从 ASR 全文按句末标点分块，再把 token 时间分配回句子；不再用纯 token join 丢掉标点。

### 评测链路 smoke（2026-10-18）

用同一个 4.08s 样例 + 人工参考 SRT 跑 `scripts/eval/asr_eval.py`：

```text
engine=qwen3 (1.7B + aligner)
CER=0.0000
boundary MAE=230.0ms（P90=300ms）
RTF=0.387（warmup 后，含 ASR+FA）
```

说明：`warmup`/`load` 把模型加载排除在计时外；4s 短样本的 RTF 不代表 24 分钟整集。评测脚本、manifest、BOM 容错、报表链路均已跑通。

### 本轮会话记录（2026-10-18 第五轮：主链收尾 2）

- **测试**：1162 → **1306 passed**（+144）；74.0% → **79.0%**；0 failed；全量 ~10s。
- **模块**：`whisper_utils` 45→100%、`episode_workflow` 36→100%、`reuse/common` 49→100%、`reuse/apply` 65→98%、`asr_eval` 47→99%、`clean_cc_reference` 98%。
- **修 1 个真 bug + 1 处日志**：`separate_vocals` 裸文件名 `os.makedirs('')` 崩溃；`step_translate` 日志硬编码 `→ ja`。
- **0.6B 复测**：见 §4 “0.6B 复测” 小节（同机同音频、60s 分块）。
- **待决策 / 清理**：`episode_workflow._write_ai_review_json` 无调用方（死代码）；`--mode` 是已废除的残留 flag；`auto_classify` / `auto_clean_glossary` 0 覆盖旧入口。
- **素材**：金标仍等用户「完整字幕 + 视频」；A/B、chunk 调参继续等素材。
- **基线提交**：`7a82f60`（代码/测试/文档）；本记录为随后的文档提交。

### 第五轮测试基线（2026-10-18：1306 tests / 79.0%）

```text
python -m pytest -q
1306 passed in ~10s          # 无网络、无模型、无真实项目
coverage: 79.0% (13,715 statements)     # 上轮 1162 / 74.0%
```

覆盖变化（按目录）：

| 目录 | 语句 | 本轮 | 上轮 | 说明 |
|---|---:|---:|---:|---|
| `lib` | 3,313 | **84.9%** | 75.9% | whisper_utils 45→100% |
| `fix` | 3,219 | **77.9%** | 72.6% | episode_workflow 36→100% |
| `nouns` | 2,424 | **67.2%** | 67.2% | 旧入口仍 0% |
| 根脚本 | 2,011 | **65.7%** | 65.7% | run_all 96% |
| `asr` | 581 | **95.0%** | 95.0% | worker/CLI 99% |
| `reuse` | 389 | **97.9%** | 73.8% | common 100%、apply 98% |
| `scan` | 372 | **94.6%** | 94.6% | — |
| `apply` | 380 | **100.0%** | 100.0% | — |
| `eval` | 428 | **94.2%** | 61.9% | asr_eval 99%；CC 清洁 98% |
| `ass` | 313 | **86.3%** | 86.3% | — |
| `utils` | 285 | **80.4%** | 80.4% | — |
| **合计** | **13,715** | **79.0%** | 74.0% | — |

**本轮修掉的真 bug**（各配回归测试）：

| 位置 | 问题 | 影响 |
|---|---|---|
| `lib/whisper_utils.separate_vocals` | 裸相对文件名且未传 `output_dir` 时 `dirname=''` → `os.makedirs('')` 抛 `FileNotFoundError` | 在调用 demucs 前崩溃；主链通常显式传目录，故此前未暴露 |
| `fix/episode_workflow.step_translate` | 成功日志硬编码 `→ ja` | `--lang zh` 时日志误导（不影响实际参数） |

### 0.6B 复测（2026-10-18，莎莉 EP001；仅吞吐/时间轴，不作金标）

条件：同机同音频 `temp/asr/sally_ep001.wav`（1511.8s），60s 分块，bfloat16；0.6B 从 GPU 52°C 起跑，1.7B 在 0.6B 结束后立即跑（保持热态）。

| 配置 | 整集耗时 | RTF | 字符 | token/段 | 零时长 token | 时间轴联合覆盖 | cue 覆盖均值 | OP / 正片 / ED |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.6B 旧记录（19:17，旧 worker） | 880s | 0.582 | 4842 | 2555 / 389 | 28.8% | 43.7% | 0.587 | 0.258 / 0.592 / 0.533 |
| **0.6B 复测（本轮）** | **453.1s** | **0.300** | 4842 | 2555 / 376 | **10.2%** | **54.2%** | **0.725** | 0.373 / 0.731 / 0.644 |
| 1.7B 旧记录（终版） | 312.9s | 0.207 | 5034 | 2609 / 370 | 8.4% | 59.6% | 0.792 | 0.718 / 0.790 / 0.871 |
| **1.7B 同态（本轮）** | **237.0s** | **0.157** | 5034 | 2609 / 370 | 8.4% | 59.6% | 0.792 | 0.718 / 0.790 / 0.871 |

结论：

1. **880s 反常未复现**：0.6B 整集 453s（RTF 0.300），接近旧 1.7B 的慢值（0.295），不是「0.6B 整集必然慢 2×」的稳定现象；旧 880s 更像散热/负载/旧 worker 的综合结果。
2. **0.6B 仍不应做快模型**：同热态下 1.7B 237s vs 0.6B 453s（0.6B 慢 1.9×）；文本更短（4842 vs 5034），时间轴更差（联合覆盖 54.2% vs 59.6%，cue 覆盖 0.725 vs 0.792，OP 0.373 vs 0.718）。草稿模式不启用。
3. 旧 0.6B 的 28.8% 零时长来自旧 worker 未补零；本轮 10.2% 与 1.7B 的 8.4% 同量级，补零生效。
4. 1.7B 本轮输出与旧终版逐字/逐 token 一致（5034 chars、2609 tokens、370 segments、指标完全相同）→ 结果可复现，差异只在耗时（237 vs 313s），属机器负载/温度差异。

### 数码宝贝 EP01 首轮冒烟（2026-10-18，非验收）

素材：EP01 视频（1386s，日语 FLAC）+ 清洁后的日文 CC（286 cue）；用户已人工核对 **CC 与视频对得上**。
Qwen3-ASR 1.7B + FA，60s 分块。

```text
转写：2427 chars / 1188 tokens / 253 segments / 1386.3s
耗时：105.2s / RTF 0.076
P0 全文件 CER=0.4522；boundary MAE=4810ms（长 segment 下 P0 指标无意义）
P2 align：match 55/286，garbled 115，missing_asr 74，no_speech 40
         （55 条匹配：P50 140ms / P90 2380ms / ±100ms 45.5%）
```

诊断：

1. **token 时间轴在 OP/音乐段塌缩**：95 个零时长 token；全文件旁白 ASR 落 75.5s，CC 在 99.5s。
   只跑正片（95–1240s）后首段恢复（旁白 0.9s vs 参考 4.47s），但 cue 覆盖仍约 0.50。
2. **CC 与 ASR 的对白范围不同**：CC 含角色名字幕卡/语气词（`あ…`/`うわー`），Qwen3 常省略；
   按 cue 算大量 unmatched，不能直接当“ASR 错”。
3. **全文件 CER 不可用**：ASR 还转写 OP/ED 歌词与预告（CC 主故事只到 20:33，ED 20:39→，预告→22:48）。
4. CC 写法（ケータイ/片假名名）与 ASR（携帯/汉字名）不同，也会抬高 CER。

**结论**：首轮数字只作冒烟。三集评测改用 **主故事窗口 95–1233s**（三集同结构），
参考先过滤字幕卡/非对白，并单独统计 ASR 漏掉的短语气词；token 时间轴需单独修（或分段重对齐）。
当前 EP02/03 视频已到位，三集基准见 §4.6。

### 三集参考基准（2026-10-18 第六/七轮；EP01=dev，EP02/03=test）

**协议**：`eval/protocol.md`、ADR-009。参考字幕只打分，不回流参数；test 冻结后只跑一次。
**素材**：EP00X 视频（1386 s）；主故事窗口 `[95,1240]`；清洁 CC `temp/references/digimon/EP00X.main.srt`。
**ASR**：Qwen3-ASR-1.7B + FA-0.6B，60 s 自切，缓存 `temp/asr/digimon_ep00X_main.qwen3.json`。
**用户人工复核（第七轮）**：视频/CC **没有真实偏移**；此前 -0.37/-0.40 s 是 ASR 起点 vs CC
显示起点的系统差。以下全部按 **raw / 无偏移**。

| split | 集 | CER | ref chars | cues | match | P50 起 | P90 起 | ±100 ms | 可靠 n | 可靠 P90 | auto | 截短 | token zero |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| dev | EP001 | 0.3643 | 2056 | 276 | 49 | 129 ms | 1897 ms | 46.9% | 35 | 424 ms | 13 | 0 | 9.4% |
| test | EP002 | 0.2581 | 4107 | 370 | 79 | 394 ms | 837 ms | 7.6% | 69 | 494 ms | 21 | 0 | 3.2% |
| test | EP003 | 0.2698 | 3336 | 317 | 74 | 406 ms | 1555 ms | 2.7% | 64 | 526 ms | 17 | 0 | 6.3% |
| test 合计 | 2 | 0.2633 micro | 7443 | 687 | 153 | 400 ms | 1196 ms | 5.1% | 133 | 510 ms | 38 | 0 | 4.7% |

「可靠」= match 且 cue 字数 ≥3、confidence ≠ low；1–2 字语气词天然歧义，单列不进主 P90。
合并样本（pooled）test：all P50 402 / P90 1059 / ±100 ms 5.2%；可靠 P50 392 / P90 521 / ±100 ms 5.3%。
报告：`temp/reports/digimon_qwen3_raw.{json,md}`；冻结摘要
[`eval/reports/digimon_benchmark_2026-10-18.md`](../eval/reports/digimon_benchmark_2026-10-18.md)。

**Whisper large-v3 q5 A/B（同参考、同窗口、raw）**：

| split | 集 | CER | match | P50 起 | P90 起 | ±100 ms | 可靠 n | 可靠 P90 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| dev | EP001 | 0.3069 | 52/276 | 560 ms | 2569 ms | 9.6% | 39 | 1135 ms |
| test | EP002 | 0.2089 | 96/370 | 824 ms | 1578 ms | 3.1% | 84 | 1448 ms |
| test | EP003 | 0.1981 | 99/317 | 689 ms | 1729 ms | 4.0% | 88 | 1203 ms |
| test 合计 | 2 | 0.2041 micro | 195/687 | 757 ms | 1653 ms | 3.5% | 172 | 1325 ms |

结论：**Whisper 文本更好**（test CER 0.204 vs 0.263，match 195 vs 153），**Qwen3+FA 打轴更好**
（P50 400 vs 757 ms，可靠 P90 510 vs 1325 ms）。Whisper 只有 segment 级时间戳；混合方案
（Whisper 文本 + Qwen3-FA 对齐）是后续候选，不在本轮实现。

**真实收益（与参考对比）**：

1. **结尾 display linger 不再截短**：参考 CC 显示时间普遍晚于 ASR 语音结束（起点匹配好的
   条件下，end error 中位数 EP01 0.53 s / EP02 0.33 s / EP03 0.19 s）。旧逻辑会改 end；
   现在 `end_is_display_linger` 保留结尾，只修起点。raw 下三集自动修复 121→51 条，其中
   **会缩短结尾的 106→0 条**；起点 P50/P90 不变。
2. **零时长 span 不自动修**：collapsed token 只给 review，不再把 cue 移到单点（`confidence_for`/
   `action_for`）；EP01 匹配里的 2 条 collapsed span 已从 auto 降级。
3. **时间轴质量可诊断/可回退**：`diagnose_timeline.py` 输出零时长 run、overlong、质量分；
   `TimelinePolicy` 只在 token 灾难性塌缩时回退 segment（EP01–03 都保留 token，段级更差）。

**被否掉的“优化”**：

- **整集偏移**：用户人工确认无真实偏移；`estimate_global_offset` 得到 -0.37/-0.40 s 是在吸收
  ASR 起点与 CC 显示起点的系统差。把它当默认修正会让字幕整体提前，已从 `align_cues` 默认移除，
  只保留 `--global-offset` 作为“确有独立证据的整文件漂移”诊断开关。**不要再把它写进验收指标。**
- **局部偏移原型**：同样会吸收该偏差，不采纳。
- segment-start overlong trim：EP01 dev 提升、EP02/03 test 中性，保持 opt-in。
- cursor 回看：增加 match 但 P90/±100 ms 变差，不采纳。

**仍缺什么**：可靠匹配 P90 510 ms 远高于 300 ms 目标；主因是短语气词/重复台词歧义匹配和
FA 在音乐/重叠段的零时长。需要 P3 VAD 或分段重对齐，不能继续在 test 上调阈值/常数。

### 本轮会话记录（2026-10-18 第四轮：测试收尾）

- **非素材依赖任务完成**：金标素材未到，先完成不依赖「参考字幕 + 视频」的测试收尾（`docs/test-plan.md` §3 的 P1/P2 缺口）。
- **测试**：967 → **1162 passed**（+195）；64.0% → **74.0%**；0 failed；全量 ~7.2s。
- **模块**：`run_all` 34→96%、`unified_scanner` 38→95%、`apply_fixes` 35→100%、`qwen3_worker` 37→99%、`qwen3_transcribe` 27→99%、`ass_repair` 65.5→86%、`reuse/align` 70→96%。
- **修 5 个真 bug**（均有回归测试，见下表）：扫描产物坏 JSON 打死流水线、ASS `replace_name` 静默失效、SRT 繁→简无效、单句 segments 丢句内标点、worker 非对象 JSON 退出。
- **素材策略不变**：金标仍等用户「完整字幕 + 视频」；莎莉/阿童木不作验收。P2-14 / A/B / chunk 调参继续等素材。
- **可先做**：0.6B 复测（不依赖金标，需散热稳定）；`whisper_utils` / `eval` / `reuse` 剩余补测。
- **基线提交**：`c2e1eed`（代码/测试/文档）；本记录为随后的文档提交。

### 第四轮测试基线（2026-10-18：1162 tests / 74.0%）

```text
python -m pytest -q
1162 passed in ~7.2s         # 无网络、无模型、无真实项目
coverage: 74.0% (13,627 statements)     # 上轮 967 / 64.0%
```

覆盖变化（按目录）：

| 目录 | 语句 | 本轮 | 上轮 | 说明 |
|---|---:|---:|---:|---|
| `lib` | 3,313 | **75.9%** | 75.4% | 对齐/ASR 契约稳；whisper_utils 45% |
| `fix` | 3,220 | **72.6%** | 72.6% | whisper_pipeline 81%、whisper_fixer 93% |
| `nouns` | 2,424 | **67.2%** | 67.2% | aggregate 86%、build_glossary 92% |
| 根脚本 | 2,011 | **65.7%** | 42.3% | run_all 34→96%、translate_srt 85% |
| `asr` | 581 | **95.0%** | 42.5% | worker 37→99%、CLI 27→99% |
| `reuse` | 389 | **73.8%** | 62.2% | align 70→96%、apply 65%、common 49% |
| `scan` | 372 | **94.6%** | 37.6% | scan_all/VAD/CLI 补齐 |
| `apply` | 380 | **100.0%** | 35.1% | main/ASS/繁→简/degloss 全分支 |
| `eval` | 339 | **61.9%** | 61.9% | manifest 缺路径分支未补 |
| `ass` | 313 | **86.3%** | 65.5% | CLI main 已补 |
| `utils` | 285 | **80.4%** | 80.4% | — |
| **合计** | **13,627** | **74.0%** | 64.0% | — |

**本轮修掉的 5 个真 bug**（各配回归测试）：

| 位置 | 问题 | 影响 |
|---|---|---|
| `run_all._step_noun_classify` / `step_ai_review` | 批量枚举 `*_nouns.json` / `ai_fragments_*.json` 时，单文件截断/损坏抛 `JSONDecodeError` | 一个坏文件打死整个 Phase 1/2/3；改为 `_load_json_lenient` 跳过后告警 |
| `apply/apply_fixes` ASS `replace_name` | `build_dialogue_line` 复用 `_raw_parts` 只刷新 text | 报告 applied 但文件 Name 没变 |
| `apply/apply_fixes._run_trad_to_simp` | 只走 ASS `split(',', 9)` 路径 | SRT 项目 `--trad-to-simp` / `--lang zh` 始终 0 changed |
| `asr/qwen3_worker._segments_from_asr_text` | 单句文本用 token 拼接 | 句内逗号/顿号/英文空格丢失（`お前、何してんだよ！`→`お前何！`） |
| `asr/qwen3_worker.main` | 合法 JSON 但非对象时 `request.get` 抛 `AttributeError` | 一行坏消息终止长驻 worker；改为 `ok:false` + continue |

### 本轮会话记录（2026-10-18 第三轮）

- **测试收尾**：`whisper_fixer` 22→93%、`fragment_processor` 30→90%、`find_suspect_nouns` 37→74%、`reuse` 10→62%、`noun_checker` 39→79%、`apply_map` 28→96%；全量 967 passed / 64.0% / 0 failed。
- **相邻 cue 联动求解**：`_resolve_joint_time_fixes`；EP001 +0.5s 回放自动应用 89 → 262，50ms 还原 70 → 247（有整集偏移时 364/370 vs 旧 365/370）。
- **修复 4 个真 bug + 1 处补强**（均有回归测试，见下表）：glossary 表头/计数表误当译名、跨盘 `relpath` 崩溃、`oped_fill` 写盘参数顺序、`reuse` 复核结果被丢弃；中文呼称正则补全角逗号。
- **素材策略变更（用户要求）**：莎莉/阿童木字幕质量差，**不再用于测试/验收**；阿童木 A/B 任务已取消，`temp/asr/atom` 临时音频已清理；两者历史数据只保留机制/吞吐回放。
- **当前阻塞**：金标素材（完整字幕 + 视频）**待用户提供**。到位后：建 `eval/manifest.json` → 跑 CER / 边界 P50/P90 → 同批视频跑 Qwen3 vs Whisper A/B → chunk 调参 → 达标才切默认。
- **基线提交**：`de899b9`（代码/测试/文档）；本记录更新在 `f79a5e9` 之后。

### 测试基线（2026-10-18 第三轮：967 tests / 64.0%）

```text
python -m pytest -q
967 passed in ~6.5s          # 无网络、无模型、无真实项目
coverage: 64.0% (13,607 statements)     # 上轮 576 / 36.3%
```

覆盖变化（按目录）：

| 目录 | 语句 | 本轮 | 上轮 | 说明 |
|---|---:|---:|---:|---|
| `lib` | 3,313 | 75.4% | 73.7% | whisper_backends 44→59%（faster/openai fake 模块） |
| `fix` | 3,220 | **72.6%** | 28.4% | whisper_pipeline 36→81%、whisper_fixer 22→93%、fragment_processor 30→90%、oped 0→70–77% |
| `nouns` | 2,424 | **67.2%** | 9.5% | aggregate 86%、build_glossary 92%、extractor 85%、find_suspect 74%、noun_checker 39→79%、apply_map 28→96% |
| 根脚本 | 2,005 | **42.3%** | 7.5% | translate_srt 85%、polish_zh 99%、run_all 34% |
| `reuse` | 389 | **62.2%** | 10.3% | align 0→70%、apply 0→65%、common 38→46% |
| `asr` / `scan` / `apply` / `eval` / `ass` / `utils` | — | 10–80% | 同左 | 两轮均未动 |

两轮共新增 13 个测试文件 + 6 个扩展：
`test_translate_srt`、`test_polish_zh`、`test_nouns_aggregate`、
`test_build_glossary`、`test_nouns_extractor`、`test_find_suspect_nouns`、`test_oped_fixer`、
`test_oped_fill`、`test_run_all_steps`、`test_episode_workflow`、`test_whisper_fixer`、
`test_reuse`；扩展 `test_whisper_pipeline`、`test_whisper_backends`、`test_align_repair`、
`test_fix_by_asr`、`test_fragment_processor`、`test_find_suspect_nouns`、
`test_noun_checker`、`test_apply_map`。
矩阵、剩余 P1 缺口与渐进门槛见 [`docs/test-plan.md`](test-plan.md)。

**测试顺带修掉的 4 个真 bug**（各配回归测试）：

| 位置 | 问题 | 影响 |
|---|---|---|
| `translate_srt.load_glossary` | `split('|')` 的空首列没滤掉，表头被当成「日语→中文」映射；`build_glossary` 的「日语\|次数\|变体」表被当译名表 | 数字（96）会被 `apply_noun_pre_replace` 替换进正文，prompt 也被污染 |
| `nouns/aggregate.py`、`nouns/extractor.py`、`translate_srt.py` | Windows 跨盘 `os.path.relpath` 抛 `ValueError` | 日志行能打死整条流水线（temp 在 C:、项目在 D: 时） |
| `fix/oped_fill._apply_templates` | `write_subtitles(cues, fpath)` 参数顺序反了 | 歌词模板永远写不回字幕文件 |
| `reuse/align.pair_up` | `zip(batches, ex.map(work, ...))` 把 work 返回的 `(batch, answers)` 又包了一层，`r` 变成批次列表（恒真） | **LLM 复核结果被丢弃，所有候选一律缓存为 verified**；`--apply` 前只剩 apply 的 R1–R6 兜底 |

- 另补强：中文呼称正则不识别全角逗号（`喂，阿童木！`）→ 已补，`test_noun_checker` 锁定。
- 原则：**真实项目只用于发现问题；修之前先落最小 fixture test。**

### P2 结构回归：魔法使莎莉 EP001（2026-10-18；历史机制验证，不作金标）

> 素材是 ru→zh 纯翻译项目、无音轨，所以**不能**测 ASR 质量；这里用真实 cue 时间分布反推合成 ASR，
> 测的是 mapper 机制（还原率/边界/单调性）。工具已入库：
> `scripts/eval/alignment_bench.py --srt <字幕>`。

输入：405 cue，其中 400 条对白（5 条 `\p1` 矢量图形被过滤）；乱序行 12 处、重叠 13 处、时长 0.44–7.75s。

| 场景 | match | 边界 P50（起/止） | 边界 P90（起/止） | ±100ms（起/止） | 还原率 | 单调冲突 |
|---|---:|---:|---:|---:|---:|---:|
| A token 级 +50ms 抖动 | 400/400 | 28.8 / 27.0 ms | 79.4 / 46.0 ms | 90.0% / 98.5% | 98.0% | 0 |
| B 3 个 cue 合并为 1 段（模拟 Whisper） | 379/400 | 295.9 / 264.1 ms | 2032.6 / 2297.1 ms | 37.7% / 42.2% | 94.8% | 0 |
| C 10% 丢弃 + 10% 破坏 + 幻觉段 | 285/400 | 28.2 / 27.1 ms | 869.2 / 45.6 ms | 88.8% / 97.2% | 80.1%※ | 0 |
| D 整体 +0.8s 漂移 | 400/400 | 801.4 / 800.4 ms | 846.5 / 844.3 ms | 0 / 0 | 98.5% | 0 |

※ C 的还原率分母是「保留下来的 361 个 cue」。

结论：

1. **token 级时间轴能把边界压到 P50 ≈30ms、P90 <80ms**，远优于 100ms 目标；A 有 4 条转 review（OP 重复字幕，误差 >2s），其余 357 条 keep、39 条自动改时间。
2. **段级 ASR（无 token）只能到 P50 ≈300ms**——Whisper 兜底可以诊断、不能定轴；Qwen3-FA 是必要前提。
3. C 的 80% 还原率是重建；丢弃/破坏的 cue 本来就不该被还原。
4. 性能：405 cue 约 **0.23s/集**（纯 stdlib），193 集约 45s。

### P2 真实验证：莎莉 EP001 日语视频（2026-10-18；历史吞吐/时间轴验证，不作金标）

素材：`Mahou_Tsukai_Sally_1966_DVD_Ep001_Simu.mkv`，1512s（25.2 min），AC3 双声道
（实测 L/R 相关 1.000 = 双单声道），转 16k mono。项目没有日语参考字幕（源字幕是俄语），
所以**不能算 CER/边界金标**；本节只测时间轴可用性、吞吐、跨引擎一致性。

| 配置 | 耗时 | RTF | 字符 | token/段 | 零时长 token | 时间轴联合覆盖 | cue 覆盖均值 |
|---|---:|---:|---:|---:|---:|---:|---:|
| qwen_asr 内部 180s 分块 | 1252.8s | 0.829 | 4144 | 2176 / 310 | 48.2% | — | 0.361 |
| 自切 60s | 446.8s | 0.295 | 5034 | 2609 / 377 | 27.1% | 47.9% | 0.570 |
| 自切 60s + 补零（终版） | **312.9s** | **0.207** | 5034 | 2609 / 370 | **8.4%** | **59.6%** | **0.717** |
| Whisper large-v3 q5（对照） | 277.9s | 0.184 | 5361 | 0 / 369 | — | 近平铺 | 0.953* |

> 终版 cue 覆盖：median 0.805，≥0.8 占 51.2%，≤0.2 仅 10.6%；OP/正片/ED 分别 0.515 / 0.720 / 0.860。
> 两次整集运行 312.9–446.8s（RTF 0.207–0.295），差异来自笔记本散热/功耗，排期要按慢的那次算。

\* Whisper 的 segment 连续平铺，cue 覆盖率天然接近 1，不能当「语音检测更准」。

分长度切片（同源 300s 起，独立运行）：

| 输入 | 零时长 token | token 时长中位数 | 文本 |
|---:|---:|---:|---|
| 60s | 17.6% | 0.107s | 通顺可读 |
| 90s | 34.7% | 0.080s | 基本可读 |
| 120s | 54.6% | 0.000s | 开始明显错词 |
| 180s | 56.8% | 0.000s | 错词更多 |

结论：

1. **长音频必须自切 ≤60s**：内部 180s 分块既塌缩时间戳又慢（RTF 0.83 vs 0.21–0.30，
   零时长 48% vs 8.4%）。已落地 `--chunk-sec`（默认 60）+ 零时长补充，
   见 [ADR-008](adr/008-qwen3-long-audio-chunking.md)。
2. **吞吐修正**：1.7B 60s 分块 5.2–7.4 min/集 → 193 集约 **17–24h**，是方案估计（6–12h）的 2 倍；
   Whisper large-v3 q5 为 4.6 min/集 → 约 15h。
3. **0.6B 不能直接当快模型**：热态 120s 切片 0.6B RTF 0.122 vs 1.7B 0.183（快 1.5×），
   但整集 0.6B 880s vs 1.7B 313–447s（疑为持续满载降频）；质量也更差
   （cue 覆盖 0.504 / OP 0.215 vs 1.7B 的 0.570 / 0.418）。需复测后再定。
4. **跨引擎一致度只有 mean 0.505 / median 0.585**：同一段音频 Qwen3 与 Whisper 文本差异很大；
   单引擎自动替换文本风险高，双引擎/glossary 策略必须保留。
5. **P2 验收仍未完成**：没有日语金标，P50 ≤100ms 无法验证；下一步人工听校 100 条 cue 边界。
6. 端到端 CLI 自检：用生成的 377 条日语 SRT 跑 `align_cues.py`，377/377 match、P50 0ms、0.7s。
7. **`run_all --asr qwen3` 端到端**：把生成的 370 条日语 SRT 整体 +0.5s 后放进临时项目，
   配真实视频跑完整 pipeline：exit 0、总耗时 481s；365/370 match、344 条需要改时间，
   首轮只有 **89 条自动应用**——其余被「新窗口与相邻 cue 重叠」挡下转 review。
   补上**整集偏移估计**后用缓存 ASR 重放：检测到 `-0.50s`，370 条整体平移，
   **367/370 回到原始位置 50ms 内**。没有整集偏移时，密集字幕仍只能逐条修，见 ADR-007 follow-up。

### P2 相邻 cue 联动求解：EP001 回放（2026-10-18）

改动：`align_repair._prepare_time_fix` 只做单条合法性检查（shift / 最短时长），
`_resolve_joint_time_fixes` 做集合级冲突消解 —— 移动者用提议窗口、静止者用旧窗口，
两两提议重叠且原本不重叠 → 双方 review，重复到稳定。

回放素材：`temp/asr/sally_ep001.final.srt`（370 cue，60s 分块 1.7B + FA 生成）
整体 +0.5s 后与同一份 ASR JSON 重新对齐：

| 场景 | 自动应用（旧 → 新） | 50ms 内还原（旧 → 新） |
|---|---:|---:|
| 无整集偏移（纯逐条修） | **89 → 262** | **70 → 247** |
| 先应用整集偏移（-0.5s） | 5 → 6 | 365 → 364 |

结论：

1. 无整集偏移时，联动求解把自动应用率从 24% 提到 71%（262/370），自动修里
   50ms 内还原 247 条（旧逻辑 70 条）；剩余 108 条转 review（多为低相似/歧义匹配）。
2. 有整集偏移时两者几乎等价（364 vs 365）；唯一差异是一条 24.5s 长 cue 的
   163ms 边界修正，属提案噪声，不是定位错误。
3. 性能：370 cue 联动求解约 **0.03s**（纯 stdlib）；400 cue 随机漂移约 0.08s。
4. 回归：`tests/test_align_repair.py` 新增 7 项（密集相邻同时移动、三连、静止邻居、
   冲突提议双方 review、既有重叠、未匹配 cue 保护、最短时长）。

### 文档整理（2026-10-18）

- `references/architecture.md`（开发者架构文档）→ 合并进 `dev/technical-details.md`
  §1/§15，删除原文件；`references/` 不再放开发者实现。
- `SKILL.md` 711 → 487 行，移出：
  - 专名提取实现/`_build_repl`/sidecar → `dev/noun-extraction.md`
  - 翻译并行/模型名/token 细节 → `dev/translation-pipeline.md`
  - 错误根因/编码/ASS/OP-ED 细节 → `dev/troubleshooting.md`
  - Whisper 批量转录时间戳/切段修复/幻觉 → `dev/whisper-pipeline.md`
  - gates 理由 → `dev/technical-details.md` §16
- `dev/_README.md` 增加索引与写作规则；`docs/` 继续放受 git 跟踪的 ADR/技术路线/进度。
- `dev/` 大多被 `.gitignore` 忽略；本次已把 `technical-details`、`whisper-pipeline`、
  `noun-extraction`、`translation-pipeline`、`troubleshooting`、`iteration-log`
  显式 `git add -f` 跟踪。以后新增开发文档仍需 `-f`，或迁到 `docs/`。

## 5. 进行中 / 阻塞

- ✅ 0.6B + 1.7B + aligner 已下载；两种模型 smoke test 均通过（§4）。
- ✅ P2 对齐核心 + 修复计划 + CLI + `run_all --asr` 完成；P2 专项 101 项测试、真实 cue 结构回归已跑（§4）。
- ✅ 莎莉日语视频已到位（110 集 MKV）：`D:\videos\Animation\TV\[1966-1981] Toei Majokko series_東映魔女大系(東映魔女っ子シリーズ)_TV\[1966-1991] Sally_小仙女莎莉(魔法使いサリー)_TV\[1966-1968] Sally_小仙女莎莉 第1作(魔法使いサリー)_TV`。**仅历史/机制回归用途；字幕质量差，按用户要求不再作测试/验收素材。**
- ✅ P2-13 实机验证完成：180s 分块塌缩 → 自切 60s + 补零；RTF 0.83→0.207–0.295，零时长 48%→8.4%（§4）。
- ✅ 测试基线：全量 **1306 passed / 0 failed / 79.0%**（上轮 1162 / 74.0%）；第五轮补齐 `whisper_utils`、`episode_workflow`、`reuse/*`、`asr_eval`；1 个真 bug + 1 处日志修正，均配回归测试。
- ⬜ 验收金标素材：**数码宝贝**（`D:\videos\Animation\TV\数码宝贝`，见 `eval/materials.md`）；EP01 视频完整、用户已人工核对日文 CC 与视频同步；**EP02/03 视频下载中**（`.mkv.downloading` 0 B）；日文 CC 54 集已到位。P2-14 未开始，三集计划用 95–1233s 主故事窗口。
- ✅ P1/P2 主链补测完成：`scan` / `apply` 主流程、`run_all` 各 `step_*`、worker chunk、`whisper_utils`、`episode_workflow`、`reuse`、`eval` 已补（见 `test-plan.md` §3）。
- ⬜ 剩余补测（不阻塞验收）：根脚本部分分支（65.7%）、`nouns` 旧入口（67.2%）、`whisper_backends`（59%）、`align_cues`（78%）；确认 `auto_classify` / `auto_clean_glossary` / `_write_ai_review_json` / `--mode` 去留。
- ✅ 整集 0.6B（880s）与热态 RTF 0.122 反常：本轮复测 **453.1s / RTF 0.300**，同态 1.7B **237.0s / RTF 0.157**；880s 未复现，0.6B 仍慢 1.9× → 草稿模式不启用（见 §4 “0.6B 复测”）。
- ✅ 阿童木视频已到位：`D:\videos\Animation\TV\[Anonymoose] 鉄腕アトム (DVD, 10bit)`，193 MKV + 193 同名 SRT。**按用户要求不再用于测试/验收。**
- ⚠️ 阿童木同目录 SRT 是**机翻中文、2s 粗轴**（样例：`嗯` / `飞雄啊——！`），不是日语参考，也**不能当金标**。
- ⬜ 金标与 A/B 均等用户素材；阿童木 Qwen3 侧任务已取消，临时音频已清理。
- ⛔ 无硬阻塞；默认切换等用户金标素材（P2-14）。

## 6. 下一步（按顺序）

1. **P2-14 金标验收（默认切换的唯一阻塞）**：等用户提供「完整字幕 + 动画视频」文件夹；登记为 `eval/manifest.json` 金标，跑 `alignment_bench` 边界统计与 `asr_eval.py`，得出真实 P50/P90/±100ms。**莎莉/阿童木不再用于验收。**
2. **参考无关 A/B（用户素材）**
   - 用同一批视频跑 `qwen3_transcribe.py --timestamps`（1.7B，60s 分块）与 `--engine whisper`（large-v3 q5），记录 RTF、字数、token/segment、零时长率、时间轴覆盖、跨引擎逐段一致度、重复/幻觉段。
   - 与金标解耦：A/B 负责吞吐/稳定性，金标负责精度。
3. **chunk 调参**：45/60/90s 在同一金标上的边界误差 vs 处理时间，确定最终默认值。
4. **0.6B 复测（已完成）**：整集 453.1s / RTF 0.300；同态 1.7B 237.0s / RTF 0.157。880s 未复现，0.6B 仍慢 1.9×，草稿模式不启用。
5. **剩余补测（可与 1–4 并行，不卡验收）**
   - 根脚本部分分支（65.7%）：`translate_srt` / `polish_zh` 的少量错误路径。
   - `lib/whisper_backends.py`（59%）、`asr/align_cues.py`（78%）、`fix/whisper_pipeline.py`（81%）。
   - `nouns/auto_classify.py` / `auto_clean_glossary.py`（0%）：确认旧入口去留，保留则补冒烟。
   - 清理决策：`episode_workflow._write_ai_review_json`（无调用方）、`--mode`（残留 flag）。
6. **默认切换评估**：只有金标达标（P50 ≤100ms、P90 ≤300ms、干净 cue 破坏率 <1%）才把 `ASR_ENGINE` 切到 `qwen3`；保留 `ASR_ENGINE=whisper`。
7. **P3 / P4**：FireRedVAD 提示层、glossary-first 按原计划排队。

## 7. 文件清单（新增/改动）

| 文件 | 作用 | 状态 |
|---|---|---|
| `docs/tech-route-2026.md` | 方案 | ✅ |
| `docs/adr/005-asr-alignment-stack.md` | 技术选型 | ✅ |
| `docs/adr/006-stable-asr-core.md` | 稳定核心+插件 | ✅ |
| `docs/tech-route-progress.md` | 本文件 | ✅ |
| `references/architecture.md` | 开发者架构文档 | 🗑️ 合并进 dev 后删除 |
| `dev/technical-details.md` | 架构/数据流/VAD 统一/gates | ✅ |
| `dev/whisper-pipeline.md` | Whisper 策略/批量/切段/幻觉 | ✅ |
| `dev/noun-extraction.md` | AI 专名提取实现 | ✅ |
| `dev/translation-pipeline.md` | 翻译并行/模型/token | ✅ |
| `dev/troubleshooting.md` | 错误根因/编码/ASS | ✅ |
| `dev/_README.md` | 开发者文档索引 | ✅ |
| `SKILL.md` | 裁剪到运行时说明（711→487 行） | ✅ |
| `README.md` | 项目结构去掉 architecture.md | ✅ |
| `scripts/lib/asr/base.py` | 契约 | ✅ |
| `scripts/lib/asr/registry.py` | 插件注册 | ✅ |
| `scripts/lib/asr/router.py` | 引擎路由 | ✅ |
| `scripts/lib/asr/adapters/qwen3.py` | Qwen3 ASR 适配 | ✅ |
| `scripts/lib/asr/adapters/qwen3_aligner.py` | Qwen3-FA 适配（KIND=aligner） | ✅ |
| `scripts/lib/asr/adapters/whisper.py` | Whisper 适配 | ✅ |
| `scripts/lib/asr/qwen3.py` / `whisper.py` | 兼容 shim | ✅ |
| `scripts/asr/qwen3_worker.py` | 长驻 worker（60s 自切块 + 补零时长） | ✅ |
| `scripts/asr/qwen3_transcribe.py` | 单文件 CLI | ✅ |
| `scripts/asr/setup_qwen3.ps1` | 隔离环境 | ✅ |
| `scripts/asr/README.md` | 运行说明 | ✅ |
| `scripts/eval/asr_eval.py` | 评测 | ✅ |
| `eval/manifest.example.json` / `README.md` | 金标模板 | ✅ |
| `eval/materials.md` | 金标素材台账（数码宝贝目录/现状/接收约定） | ✅ |
| `eval/manifest.digimon.json` | 数码宝贝评测清单草稿（待日文字幕填 reference_srt） | 🟡 |
| `scripts/eval/clean_cc_reference.py` | 日文 CC → 对白参考（去标签/振假名/♪） | ✅ |
| `tests/test_clean_cc_reference.py` | CC 清洁 11 项（含 CLI/report） | ✅ |
| `templates/asr_adapter.py` | 新 ASR 骨架 | ✅ |
| `templates/aligner_adapter.py` | 新对齐器骨架 | ✅ |
| `tests/test_asr_*.py` / `test_qwen3_*.py` | ASR 测试 | ✅ |
| `docs/adr/007-cue-alignment.md` | 对齐决策 | ✅ |
| `docs/adr/008-qwen3-long-audio-chunking.md` | 长音频分块 + 补零决策 | ✅ |
| `docs/test-plan.md` | 测试能力矩阵 / 覆盖率 / P0 缺口 | ✅ |
| `tests/README.md` | 测试约定与运行方式 | ✅ |
| `.coveragerc` | 覆盖率配置 | ✅ |
| `scripts/lib/alignment/base.py` | 对齐契约/归一化 | ✅ |
| `scripts/lib/alignment/timeline.py` | 字符时间轴 | ✅ |
| `scripts/lib/alignment/cue_mapper.py` | 时间窗 + 字符匹配 | ✅ |
| `scripts/lib/alignment/diagnostics.py` | 信号/置信度/统计 | ✅ |
| `scripts/lib/alignment/aligner.py` | registry 对齐服务 | ✅ |
| `scripts/fix/align_repair.py` | 修复计划/时间应用/重对齐 | ✅ |
| `scripts/asr/align_cues.py` | P2 端到端 CLI | ✅ |
| `scripts/eval/alignment_bench.py` | P2 结构回归（合成 ASR） | ✅ |
| `tests/test_alignment_*.py` / `test_cue_*.py` / `test_align_repair.py` / `test_align_cues_cli.py` / `test_alignment_bench.py` / `test_fix_by_asr.py` | P2 测试（101 项） | ✅ |
| `tests/test_gates.py` / `test_compare_srt.py` / `test_clean_empty_cues.py` / `test_apply_fixes.py` / `test_video_scan.py` / `test_llm.py` / `test_oped_detect.py` / `test_ass_repair.py` / `test_unified_scanner.py` / `test_noun_checker.py` / `test_apply_map.py` / `test_fix_repeated_cues.py` / `test_reuse_common.py` / `test_run_all_helpers.py` / `test_whisper_pipeline.py` / `test_whisper_backends.py` | 本轮新增测试（17 个文件） | ✅ |
| `lib/config.py` | 新配置项 | ✅ |
| `run_all.py` / `fix_orchestrator.py` / `episode_workflow.py` | P2 `--asr` 接入（flag 控制） | ✅ |
| `tests/test_whisper_pipeline.py` / `test_whisper_backends.py` | 扩展：VAD 时间线 / Tier1+Tier2 / `main()` 闸门 / faster+openai 后端 | ✅ |
| `tests/test_translate_srt.py` / `test_polish_zh.py` | 翻译链路（LLM prompt/JSON、润色写盘策略）；百度测试已于第十轮删除 | ✅ |
| `tests/test_nouns_aggregate.py` / `test_build_glossary.py` / `test_nouns_extractor.py` / `test_find_suspect_nouns.py` | 专名链路（合并/输出/sidecar/规则） | ✅ |
| `tests/test_oped_fixer.py` / `test_oped_fill.py` | OP/ED（三分类 / 只改歌词 / 只填空行） | ✅ |
| `tests/test_run_all_steps.py` / `test_episode_workflow.py` | 编排（顺序 / flag / 资源驱动步骤 / Whisper-ASR 分派） | ✅ |
| `tests/test_whisper_fixer.py` / `test_reuse.py`（扩展 `test_fragment_processor.py` / `test_find_suspect_nouns.py`） | P1 补洞：修复 / 片段 / 复用 | ✅ |
| `tests/test_noun_checker.py` / `test_apply_map.py`（扩展） | 专名检查 / 统一主流程（提取模式、表格、应用、清单、导出） | ✅ |
| `scripts/nouns/noun_checker.py` | 中文呼称正则补全角逗号 | ✅ |
| `scripts/reuse/align.py` | 复核结果解包修复（double zip） | ✅ |
| `tests/test_align_repair.py` / `test_fix_by_asr.py`（扩展） | 相邻 cue 联动求解（密集同时移动 / 冲突 review / 端到端写回） | ✅ |
| `scripts/lib/project_utils.py` | 新增 `display_path()`（跨盘 `relpath` 兜底） | ✅ |
| `scripts/translate_srt.py` / `nouns/aggregate.py` / `nouns/extractor.py` / `fix/oped_fill.py` | 本轮 bug 修复（glossary 解析 / relpath / 写盘参数顺序） | ✅ |
| `tests/test_run_all_step_functions.py`（新） | 第四轮：`run_all` 各 `step_*` 副作用 + `main` 资源/flag 分支（52 项） | ✅ |
| `tests/test_unified_scanner.py` / `test_apply_fixes.py` / `test_qwen3_worker.py` / `test_qwen3_cli.py` / `test_ass_repair.py` / `test_reuse.py`（扩展） | 第四轮：scanner 95%、apply 100%、worker/CLI 99%、ass 86%、reuse/align 96% | ✅ |
| `scripts/run_all.py` / `apply/apply_fixes.py` / `asr/qwen3_worker.py` | 第四轮 5 个真 bug 修复（坏 JSON 跳过 / ASS Name / SRT 繁→简 / 单句标点 / worker 非对象 JSON） | ✅ |
| `tests/test_whisper_utils.py`（扩展） | 第五轮：ASS/VAD/抽音频/人声分离/文本判定（45→100%，+56 例） | ✅ |
| `tests/test_episode_workflow.py`（扩展） | 第五轮：各 `step_*` / `_run_pipeline` / `main`（36→100%，+42 例） | ✅ |
| `tests/test_asr_eval.py` / `test_reuse_common.py` / `test_reuse.py`（扩展） | 第五轮：eval 47→99%、reuse common 49→100%、apply 65→98% | ✅ |
| `scripts/lib/whisper_utils.py` / `scripts/fix/episode_workflow.py` | 第五轮：`separate_vocals` 裸文件名修复；`step_translate` 日志语言 / 删除未用变量 | ✅ |

## 8. 环境命令

```powershell
# ── 用户金标素材到位后的接入流程（P2-14）──
# 1) 按 eval/README.md 填 eval/manifest.json：
#    {"project": "...", "language": "Japanese", "episodes": [
#      {"id": "EP001", "category": "clean|overlap|bgm",
#       "video": "<视频路径或目录>", "reference_srt": "<可信字幕>"}]}
# 2) 旧管线基线
python scripts\eval\asr_eval.py --manifest eval\manifest.json `
  --engine whisper --json-out reports\asr_eval_whisper.json `
  --output reports\asr_eval_whisper.md
# 3) 2026 链路
python scripts\eval\asr_eval.py --manifest eval\manifest.json `
  --engine qwen3 --python .venv-qwen\Scripts\python.exe `
  --json-out reports\asr_eval_qwen3.json --output reports\asr_eval_qwen3.md
# 4) 结构回归（不需要音频，任意可信字幕）
python scripts\eval\alignment_bench.py --srt "<参考字幕>"

# ── 测试（日常/CI，不需要模型和素材）──
python -m pytest -q
python -m pytest -q --cov --cov-report=term-missing
python -m pytest -q tests\test_whisper_pipeline.py
python -m pytest -q tests\test_cue_mapper.py -k duplicate

# 本轮 P0 链路（Whisper 编排 / 翻译 / 专名 / OP-ED / 编排）
python -m pytest -q tests\test_whisper_pipeline.py tests\test_whisper_backends.py `
  tests\test_translate_srt.py tests\test_polish_zh.py `
  tests\test_nouns_aggregate.py tests\test_build_glossary.py `
  tests\test_nouns_extractor.py tests\test_find_suspect_nouns.py `
  tests\test_oped_fixer.py tests\test_oped_fill.py `
  tests\test_run_all_steps.py tests\test_episode_workflow.py

# 第四轮补测子集（run_all 步骤 / scanner / apply / worker / CLI / ass / reuse）
python -m pytest -q tests\test_run_all_step_functions.py tests\test_unified_scanner.py `
  tests\test_apply_fixes.py tests\test_qwen3_worker.py tests\test_qwen3_cli.py `
  tests\test_ass_repair.py tests\test_reuse.py

# 主环境 ASR 子集（不装 qwen-asr）
python -m pytest -q tests\test_asr_base.py tests\test_asr_router.py tests\test_asr_registry.py tests\test_qwen3_client.py tests\test_qwen3_worker.py tests\test_asr_eval.py tests\test_asr_whisper.py tests\test_qwen3_cli.py

# 隔离环境（已就绪）
.venv-qwen\Scripts\python.exe scripts\asr\qwen3_transcribe.py --check
.venv-qwen\Scripts\python.exe scripts\asr\qwen3_transcribe.py --list-engines

# 从主环境调 worker
python scripts\asr\qwen3_transcribe.py --audio ep.wav `
  --python .venv-qwen\Scripts\python.exe --timestamps --output temp\asr\EP001.json

# P2 对齐（默认 dry-run；--asr-json 可复用上面的结果）
python scripts\asr\align_cues.py --srt ep.srt --asr-json temp\asr\EP001.json
python scripts\asr\align_cues.py --srt ep.srt --video ep.mkv --engine qwen3 `
  --python .venv-qwen\Scripts\python.exe --write

# P2 测试
python -m pytest -q tests\test_alignment_base.py tests\test_alignment_timeline.py `
  tests\test_cue_mapper.py tests\test_cue_diagnostics.py `
  tests\test_align_repair.py tests\test_alignment_aligner.py `
  tests\test_align_cues_cli.py tests\test_alignment_bench.py

# P2 结构回归（任意真实字幕；合成 ASR，不需要音频）
python scripts\eval\alignment_bench.py --srt ep.srt
python scripts\eval\alignment_bench.py --srt ep.ass --scenario segment --json temp\bench.json

# ── 历史 EP001 复现（仅机制/吞吐回放；勿当验收素材；模型/路径按本机）──
$video = "D:\videos\Animation\TV\[1966-1981] Toei Majokko series_東映魔女大系(東映魔女っ子シリーズ)_TV\[1966-1991] Sally_小仙女莎莉(魔法使いサリー)_TV\[1966-1968] Sally_小仙女莎莉 第1作(魔法使いサリー)_TV\Mahou_Tsukai_Sally_1966_DVD_Ep001_Simu.mkv"
ffmpeg -y -v error -i "$video" -vn -ac 1 -ar 16000 -c:a pcm_s16le temp\asr\sally_ep001.wav
.\.venv-qwen\Scripts\python.exe scripts\asr\qwen3_transcribe.py `
  --audio temp\asr\sally_ep001.wav --asr models\Qwen3-ASR-1.7B `
  --aligner models\Qwen3-ForcedAligner-0.6B --timestamps `
  --output temp\asr\sally_ep001.final.json --srt temp\asr\sally_ep001.final.srt

# run_all --asr 端到端（需要本地模型路径；ASS 项目写回会被拒绝）
$env:ASR_MODEL="$PWD\models\Qwen3-ASR-1.7B"
$env:ALIGNER_MODEL="$PWD\models\Qwen3-ForcedAligner-0.6B"
python scripts\run_all.py --lang ja --target-dir temp\proj_asr --input-dir subs `
  --video-dir "<SALLY TV DIR>" --episodes EP001 `
  --asr qwen3 --asr-python .venv-qwen\Scripts\python.exe --no-skip-if-clean

# 已通过的 smoke test
.venv-qwen\Scripts\python.exe scripts\asr\qwen3_transcribe.py `
  --audio temp\smoke\ja_sample.wav --asr models\Qwen3-ASR-0.6B `
  --aligner models\Qwen3-ForcedAligner-0.6B --timestamps `
  --output temp\smoke\ja_sample.json --srt temp\smoke\ja_sample.srt
```

## 9. 决策 / 风险

- **默认不变**：`ASR_ENGINE=whisper`；P2 通过 `--asr` flag 启用，默认管线未切。
- **不伪造置信度**：P1 只出文本/token 时间；P2 用对齐一致度 + 多信号投票（ADR-007）。
- **对齐只在时间轴上动刀**：文本替换默认关闭（`allow_text_replace=False`）；删除永不自动。
- **自动改时间码的硬上限**：单边 ≤2s，且不能撞上原本不重叠的 cue；超限转 review。
- **整集偏移先判后移**：≥20 条高相似 match 且 MAD ≤150ms 才认定整集漂移；EP001 的 +0.5s 平移测试 367/370 还原。
- **乱序 cue 在 mapper 内排序再还原**：真实 ASS 有乱序行，不能要求输入先排序。
- **长音频必须自切 60s + 补零**：`qwen_asr` 内部 180s 分块会让 27–48% token 变零时长并慢 2.8×（ADR-008）。
- **吞吐按实测算**：1.7B 60s 分块 5.2–7.4 min/集 → 193 集约 17–24h；不要沿用方案里的 6–12h。
- **0.6B 不默认**：本轮同态复测 1.7B 237s vs 0.6B 453s（0.6B 慢 1.9×），且文本更短、时间轴更差；旧 880s 未复现（更像散热/负载/旧 worker）。
- **测试优先（第五轮）**：1306 tests / 79.0% 覆盖 / 0 failed；P0/P1/P2 主链每条安全策略都有断言（dry-run 不写、VAD 默认不删、OP/ED 只改歌词、只填空行）；真实项目发现的问题必须先落最小 fixture test，矩阵见 [`docs/test-plan.md`](test-plan.md)。
- **扫描产物 JSON 宽松读取**：`lib.project_utils.load_json` 保持严格（坏 JSON 抛异常，便于发现损坏）；只有 `run_all` 批量枚举的 `*_nouns.json` / `ai_fragments_*.json` 用 `_load_json_lenient`，跳过并告警，不能让一个截断文件打死整轮。
- **glossary 表格解析规则**：只有「日语\|中文」两列才当译名映射；`build_glossary` 的「日语\|出现次数\|拼写变体」表不产生映射（踩坑 #17：否则数字会被预替换进正文）。
- **日志路径用 `display_path`**：Windows 跨盘 `os.path.relpath` 抛 `ValueError`，不能出现在主流程里（temp 在 C:、项目在 D: 就会触发）。
- **`write_subtitles(path, cues)` 顺序固定**：`oped_fill` 曾写成 `(cues, fpath)`，模板永远写不回去；有回归测试锁定。
- **reuse 复核结果不能丢**：`pair_up` 的双重 zip 让所有候选一律通过；修好后 `test_reuse` 锁定「LLM 说 no/失败就不缓存」这条底线。
- **ASS 行重建不要复用 `_raw_parts`**：`build_dialogue_line` 只在 `_raw_parts` 上刷新 text；改 Name/Style 等字段前必须 `pop('_raw_parts')`，否则修改被静默丢弃（`replace_name` 就是这样失效的）。
- **单句 segments 用 ASR 原文**：Qwen3-ASR 的句内标点/空格不在 aligner token 里，单段时直接沿用原文；多段才按 token 分配。
- **`dirname()` 可能为空**：`separate_vocals` 等“默认同输入目录”的函数要写 `dirname(...) or '.'`；否则裸相对文件名会触发 `os.makedirs('')`。
- **旧入口/死代码先标记不删**：`auto_classify`、`auto_clean_glossary`、`_write_ai_review_json`、`--mode` 已确认不在主链，删除属产品决策；本轮只在 test-plan 记录，不顺手改行为。
- **金标来源规则**：机翻 / ASR / LLM 润色后的字幕都不能当参考。**验收作品：数码宝贝**（`D:\videos\Animation\TV\数码宝贝`，见 `eval/materials.md`）；日文 CC 54 集已到位（配信闭合字幕，需清洁标签/振假名/♪），视频仅 EP01，**还缺至少 2 集视频**。中文字幕只能做非金标冒烟；莎莉/阿童木只保留机制回放。
- **进程隔离**：主环境不 import torch/qwen-asr；worker JSON lines。
- **Windows**：vLLM 不支持；走 Transformers 后端 + SDPA。
- **模型下载**：HF 直连被墙；用 ModelScope（已验证 200）/ 镜像。
- **已知精度边界**：段级 ASR（无 token）P50 ≈300ms；Qwen3-FA 在真实动画音频上仍有 8.4% 零时长 token。
- **相邻 cue 联动已落地**：`_resolve_joint_time_fixes` 让密集字幕一起重排；无整集偏移时 EP001 +0.5s 回放的自动应用从 89 提到 262（50ms 还原 70→247），有偏移时 364/370 vs 旧 365/370（唯一差异是一条 24.5s 长 cue 的 163ms 边界修正）。
- **既存失败已清零**：OP/ED 常量断言改成行为测试、`MockWhisperResult` 补 `cues_to_clear`；全量 1162 passed。
- **续跑原则**：先看 §0 的 6 步，再看 §4 的验证数据；不要重写核心，只加 adapter/adapter 测试。
