# 控制变量实验台账（2026-10）

> 目的：验证“效果差是不是我们 skill 自己的方案造成的”。
> 规则：同一集、同一音频、同一参考、同一模型；一次只改一个变量。
> 单集结果只当探针，不直接下结论；至少 EP001+EP002/003 再定。

## 1. 时间轴对照（EP001，同一 full-CC/窗口）

| 变体 | 改了什么 | CER | P50 | P90 | 可靠 P90 | coverage | zero | collapse |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 60s chunk + ASR-token 映射 | 当前 skill | .3705 | **129ms** | **1897ms** | 424ms | .418 | 9.4% | 19 / 229s |
| VAD concat → ASR | 去掉静音/音乐，只拼 speech | .3672 | **87ms** | 2204ms | **316ms** | .436 | **3.6%** | **4 / 42s** |
| 官方长音频直出 | 关闭我们的 60s 切块 | **.4490** | 1882ms | 4148ms | 1363ms | .297 | 10.6% | 14 / 333s |
| ASR + series hotwords | 只加 context | .3436 | 149ms | 2616ms | 687ms | .441 | 10.6% | 21 / 225s |
| CC→FA 60s 分组 | 不用 ASR 文本 | **.0366** | 200ms | 3179ms | 626ms | .667 | 22.3% | 14 / 196s |
| CC→FA 单 cue | 不用 ASR 文本 | **.0366** | 836ms | 2000ms | 1840ms | **.961** | 23.4% | 17 / 117s |

初步结论（单集，待多集）：

- **官方长音频直出明显更差**（P50 1882ms、P90 4148ms、coverage .297），说明 60s 切块
  不是“我们把它搞坏”，反而是必要控制；不能为了“直出”去掉切块。
- **VAD concat 不具泛化性**：EP001 看起来 P50/可靠 P90/空洞变好，但 EP002/003
  文本崩（CER .258→.503、.270→.606；coverage .428→.276、.445→.253），
  P90/可靠也没有稳定提升 → **不接**。
- CC→FA 文本/覆盖最好，但边界不是；
- hotwords 只改善文本/覆盖，边界变差。

### 1.1 VAD concat 多集复测

| EP | 方案 | CER | P50 | P90 | 可靠 P90 | coverage | collapse |
|---|---|---:|---:|---:|---:|---:|---:|
| EP002 | 60s | **.2581** | 394ms | 837ms | 494ms | **.428** | 7 / 122s |
| EP002 | VAD concat | .5030 | 390ms | **777ms** | **447ms** | .276 | **6 / 66s** |
| EP003 | 60s | **.2698** | 406ms | **1555ms** | **526ms** | **.445** | 9 / 96s |
| EP003 | VAD concat | .6055 | 399ms | 1586ms | 546ms | .253 | **9 / 73s** |

结论：VAD concat 用“去掉静音/音乐”换来的边界小改善，代价是 **ASR 文本严重退化**；
不适合做默认。保留 60s 切块 + ASR 映射。

### 1.2 FireRedVAD 过滤 ASR token（不改文本）

只保留落在 speech 区的 token，再交给现有 CC 映射：

| EP | 方案 | P50 | P90 | 可靠 P90 | coverage |
|---|---|---:|---:|---:|---:|
| EP001 | 60s | 129ms | 1897ms | 424ms | .418 |
| EP001 | midpoint | **78ms** | **663ms** | 419ms | .388 |
| EP001 | overlap | 98ms | 1689ms | 424ms | .412 |
| EP002 | 60s | 394ms | 837ms | 494ms | .428 |
| EP002 | midpoint | 398ms | 827ms | 677ms | .322 |
| EP002 | overlap | 398ms | 822ms | 607ms | .335 |
| EP003 | 60s | 406ms | 1555ms | 526ms | .445 |
| EP003 | midpoint | 392ms | 1820ms | 511ms | .265 |
| EP003 | overlap | 405ms | 1652ms | 511ms | .274 |

结论：**EP001 看起来大幅改善，但 EP002/003 coverage 崩、可靠 P90 不升反降**。
不具泛化性，不接默认。实验脚本已删除，数值留在本台账。

### 1.3 FireRedVAD 只裁每段首尾静音（保留内部停顿）

EP001：CER .338（好于 .371）、coverage .468（好于 .418），但 P90 2957ms、
可靠 479ms（差于 424ms），collapse gap 78s。**混合结果，单集不接**；
不再投入多集，因为同一模型用法已被 token filter/concat 两次证明不稳健。

## 2. 翻译对照（EP001 前 30 条，23 个时间对齐对）

**关键发现（EP003 名字段）**：过滤 glossary 后，模型在 cue 14 把
「ガブモン／え？」并成「加布兽」，之后整段**错位一条**（cue15 的译文跑到 cue16）。
QA 只抓到 2 条 glossary miss，没抓到错位。这是**我们输出协议的问题**，不是模型单独的问题：
裸 JSON 字符串数组无法校验 id，模型一并条就静默错位。

已改为 **`{"id": n, "translation": "..."}` 协议**，按 id 回填；缺 id/重复 id 整批失败重试。
EP003 名字段复测：cue14 恢复「加布兽／嗯？」，cue15 恢复「你去阿岳身边」，
**错位消失、QA 0**。EP001 前 30 条：chrF 38.49、1 条行长，需 repair。

多集复测（EP002/EP003 前 30 条，id 协议 + 按批过滤 + 系列 glossary）：

- EP002：QA 0；
- EP003：1 条 glossary miss（`デジモン` 被意译成「它」），repair 1 条后 QA 0；
- 说明协议/过滤可泛化，剩余是“术语被意译掉”的单点问题，交给 QA→repair。

| 变体 | 批次 | 术语注入 | chrF 句均 | QA |
|---|---:|---|---:|---|
| 旧 prompt | 10 | 4 条中文名 | **43.33** | 0 |
| 当前 pairs | 10 | 18 条 ja→zh | 36.69 | 0 |
| 当前 pairs（按批过滤） | 10 | 本批出现的 7 条 | **40.61** | 0 |
| ID 协议 + 按批过滤 | 10 | 本批出现的 7 条 | 38.49 | 1（行长） |
| 当前 pairs | 1 批 30 条 | 18 条 ja→zh | 31.73 | 2（glossary 1 + 行长 1） |
| 当前无 glossary | 10 | 无 | 40.63 | 2（日语残留 1 + 行长 1） |

注意：这不是严格单变量——旧 prompt 的术语集合/格式都不同。已暴露的问题：

- **一对一批次明显好于 30 条一批**；我们保留批处理是对的；
- **全量 pairs 注入会降 chrF，并且实测出现「石田阿和」这类错名**；
- 只注入“本批出现的术语”后，chrF 回到 40.61 且 QA 0；说明
  **不是术语对照本身错，是“把整本 glossary 塞进每个 batch”错**；
- 无 glossary 更流畅，但会出现未译/错名；
- 已把 `_glossary_for_batch` 接进 `translate_file`；下一步才是“译后术语校验/规范化”。

## 3. 下一步控制实验

1. ✅ 官方长音频直出 vs 60s：直出明显更差，切块保留。
2. ✅ VAD concat 多集：文本崩，不接。
3. ✅ 全量 glossary vs 按批过滤：过滤后 chrF/QA 都更好，保留过滤。
4. 同一输出：QA/repair 开 vs 关，确认 repair 只修问题条、不伤好条。
5. 至少 EP001/EP002/003 重复；单集提升不作为结论。
6. 翻译加 10 条 MQM 人工/AI 评分，不以 chrF 单独定优劣。

## 4. 整集 E2E 翻译（EP001，276 cues）

命令：

```powershell
run_all --target-dir <abs-project> --input-dir . --lang ja --skip-whisper --translate
```

结果：

- 输出 276/276，coverage 1.0，源 SRT hash 不变；
- 首轮 QA：15 违规（日语残留 14、echo 2、行长 1）；
- repair 一遍：4 违规；
- repair 第二遍：**7 违规**（名字变成 阿武/武尔/石田阿和，QA 反而变差）；
- 定位：
  - QA glossary 只按短词判定，`高石タケル→高石岳` 被误判；
  - repair 不逐条验收，越修越差；
- 修复：
  - `glossary_misses` 最长匹配（全名覆盖短名）；
  - `repair_file` 逐条验收：只有该条 QA 严格变少、且不留 fatal（日语残留/echo/empty）才接受；最多 2 遍；
- 对已坏的 9 违规输出重跑 gated repair：**9 条修到 0**。

修复后 fresh run（同 EP001，276 cues）：

- 初始 QA：3 违规（glossary 2、行长 1）；
- gated repair：修 3 条 → **0 违规**；
- 说明：首轮质量本身也变好，不再依赖“旧输出补修”。

结论：整条链路能出中文；当前版本 fresh EP001 可到 QA 0。  
但 OP/ED 未覆盖、视频直出未接、全 54 集未跑，不能外推为“全部交付完成”。

## 5. 视频一键直出（video_to_zh.py）

实现方式：薄 wrapper，串现有 `qwen3_transcribe.py`/`whisper_batch_transcribe.py`
→ `translate_srt.py`，不重写模型。

60s 视频片段实测：

- Qwen3 转写 12 条日文 SRT（句子级，3–5s/条）；
- 翻译 12/12，QA：1 条 CPS（短 cue `嗯？` 12.5 > 12），repair 0；
- 输出 `ja/EP001.srt`、`zh/EP001.srt`、`zh/EP001.qa.json`；
- 文本有 ASR 错（`水田→水電`），翻译继承。

结论：**“只有视频一键出中文”已接**，但输出是粗剪，不是最终字幕；
需要后续 cue 级断句/文本校对/QA 人工处理。

原有 A–D 没有删除/重写：A 转写、B 专名审查、C 翻译、D `auto_translate`/
`oped_fixer`/`polish_zh` 都还在；wrapper 只做 A+C，完整流程见
`references/workflows.md` 场景 5。另修复了 `oped_fixer.py`/`oped_fill.py`
从仓库根直接运行时的 `lib` 导入路径。

### 5.1 新路线是否优于旧 Whisper？

旧路径代码/文档都还在：`whisper_batch_transcribe.py`、`run_all` legacy
Whisper、`references/workflows.md` 场景 1/5。Whisper 可执行文件/模型路径已写入
`LOCAL_ENV.md`（`D:\software\video\whisper-cublas-12.4.0-bin-x64`），
`--dry-run` 已验证能选中视频；**尚未重跑质量 A/B**。

已有 3 集 A/B（对齐基准，不是端到端视频直出）：

| 指标 | Qwen3+FA | Whisper |
|---|---:|---:|
| test CER | 0.263 | **0.204** |
| test match | 153/687 | **195/687** |
| P50 起 | **400ms** | 757ms |
| 可靠 P90 | **510ms** | 1325ms |

结论：**文本 Whisper 更好，时间轴 Qwen3 更好**。
没有“视频→日文 SRT→中文”的端到端质量 A/B，因此**不能声称新字幕生成整体优于旧 Whisper**。
要下结论，需要同一视频、同一 A–D/翻译配置，比较 MQM/chrF/QA/time。

### 5.2 3×60s 片段 A/B（2026-10 复核）

同 EP001/2/3 的 95–155s 片段，Qwen3 走 `video_to_zh`，Whisper 走
`whisper_batch_transcribe`；两边同一翻译/QA/glossary。

| EP | Qwen CER | Whisper CER | Qwen zh chrF | Whisper zh chrF | Qwen QA | Whisper QA |
|---|---:|---:|---:|---:|---:|---:|
| EP001 | **.110** | .118 | 22.32 | **23.66** | 1 CPS | 0 |
| EP002 | **.131** | .178 | 19.30 | **20.71** | 2 行长 | 0 |
| EP003 | **.115** | .153 | 32.12 | **32.38** | 0 | 0 |

读法：

- **日文 CER：Qwen3 在三段都更低**，推翻了“旧 Whisper 文本一定更好”的单集印象；
- **中文 chrF/QA：Whisper 略好或持平**；Qwen3 因 ASR 分段更长，出现 10.8s cue
  和 2 条行长违规；
- 名字：EP001 Whisper「八神太一/石田大和」更准，Qwen3 出「矢神太一/石大山」；
- 样本只有 60s/集，**不能替代整集 A/B**；但足以说明旧结论“Whisper 全面更好”不成立，
  新模型也不是全面更好——文本 CER 与中文交付质量是两回事。

下一步：整集（EP001/002/003）同 A–D vs A+C，加 10 条 MQM；当前不切换默认。

## 6. AI 翻译启发式清理（2026-10）

详见 `docs/translation-research-2026-10.md`。摘要：

- 无上下文 chrF 平均 26.59，前 3 条 26.11，前 10 条 26.29，全部前文 26.24，
  全部前后文 26.08；**默认 3 条上下文无证据优势**；
- 简化 prompt + 不做默认预替换：平均 chrF 26.68 / QA 5，旧默认 26.11 / QA 7；
- 盲评 MQM 12 条：旧默认 accuracy 4.67/fluency 4.50/term 4.67，
  新配置 4.58/4.42/4.75，基本持平；
- 已删除 `--future-context`、角色年龄/性别规则；上下文默认 0；
- 专名预替换改为 `--noun-pre-replace` opt-in；
- 批间 summary/target 交接：target 平均 chrF +0.6，但 EP001 盲评 MQM 无提升；
  summary QA 少 3 条但多一次/批调用；均不接默认；
- batch 曲线 5/10/20/30/50：20 平均 chrF 27.00、QA 3；10 为 26.68/5；
  默认改 20。50 只在 EP001 最高，跨集不稳；
- EP002 prompt 硬约束（20 字/12 CPS）：QA 3→2，但 chrF 27.99→26.53；
  纯翻译 + `--repair`：QA 0、chrF 27.94。**不默认加 prompt 硬约束**；
- EP001 31 条 reflect 抽样：只改 2 条，chrF 25.21→25.22，无收益；
  **不做全量第二遍**，只保留 QA 问题条 repair；
- 还未验证：人工 MQM、series memory、分割/断句专项。
