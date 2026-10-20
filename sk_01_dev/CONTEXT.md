# CONTEXT.md — Subtitle Proofread Skill

## 项目是什么

一个 3-Phase 字幕校对流水线，支持日语/中文/英语。输入 SRT/ASS 字幕文件，通过字符扫描→Whisper ASR 修复→专有名词统一→交付审查，输出校对后的字幕。

## 架构图

```
Phase 1: SCAN                    Phase 2: FIX                    Phase 3: UNIFY + DELIVER
─────────────────                ─────────────────               ─────────────────────────
unified_scanner.py               fix_orchestrator.py             noun_checker.py
  ├─ garbled_cues ───────────────→ Fixer.run_auto()               find_suspect_nouns.py
  ├─ VAD speech timeline ────────→   ├─ WhisperFixer              oped_fixer.py
  └─ glossary ───────────────────→   │   (VAD→fix regions→Whisper)  apply_fixes.py
                                      │                           step_deliver()
                                      ├─ FragmentProcessor
                                      │   (AI补全+VAD对齐)
                                      └─ → SRT + 问题解决报告
```

**数据契约链**: JSON 作为阶段间桥梁
```
findings.json → (Phase 2) → ai_fragments_{EP}.json → (Phase 3) → all_fixes.json
noun_check.json → suspect_nouns.json → ai_review_candidates.json → ai_review_fixes.json
```

## 模块地图

```
scripts/
├── lib/                        # 基础库（零副作用，被所有模块导入）
│   ├── config.py               #   单一配置源 — 所有 env var + 默认值
│   ├── subtitle_io.py          #   规范 I/O — read_subtitles/write_subtitles/apply_fixes_to_cues
│   ├── whisper_utils.py        #   文本分类/时间码/Whisper过滤
│   ├── whisper_backends.py     #   多后端Whisper抽象 (cpp/faster/openai)
│   ├── subprocess_utils.py     #   统一子进程 — run_ffmpeg/run_whisper/run_git
│   ├── project_utils.py        #   项目检测/资源发现
│   ├── ass_utils.py            #   ASS格式解析
│   ├── srt_utils.py            #   SRT格式构建
│   ├── language_utils.py       #   语言→工具模块分发
│   ├── chinese_utils.py        #   中文常词/繁简映射
│   ├── japanese_utils.py       #   日文常词/假名检测
│   ├── english_utils.py        #   英文常词
│   ├── llm.py                  #   OpenAI 兼容 chat 客户端（JSON 模式/推理开关）
│   ├── glossary.py             #   系列词表 + 变体归一 + 候选发现（只读表 vs 决策表分离）
│   ├── review/                 #   复核包：pack/fill/apply/stage/failures（ADR-012）
│   ├── asr/                    #   ASR 稳定核心 + 插件适配器（2026 路线）
│       ├── base.py             #     契约：ASRResult/ASRConfig/BackendInfo/Protocol
│       ├── registry.py         #     自动发现 adapters/*
│       ├── router.py           #     引擎选择/fallback；不 import 模型
│       └── adapters/           #     qwen3 / qwen3_aligner / whisper
│   └── alignment/              # P2 cue 对齐（纯 stdlib，不 import 模型）
│       ├── base.py             #     契约/归一化/状态词表（含 display linger 规则）
│       ├── timeline.py         #     ASRResult → 字符时间轴（token/segment 兜底）
│       ├── quality.py          #     时间轴健康度：零时长 run/overlong/fallback/空洞(lost_spans)
│       ├── cue_mapper.py       #     时间窗 + 字符匹配 + 精确串就近
│       ├── diagnostics.py      #     信号/置信度/边界统计
│       └── aligner.py          #     registry 对齐服务（ALIGNER_ENGINE）
├── fix/                        # Phase 2: 错误修复引擎
│   ├── fix_orchestrator.py     #   薄orchestrator — 组合WhisperFixer+FragmentProcessor
│   ├── subtitle_session.py     #   路径解析/检测/缓存 — 被orchestrator和worker共用
│   ├── whisper_fixer.py        #   VAD→统一fix regions→Whisper转录→分诊 (数据契约:WhisperResult)
│   ├── fragment_processor.py   #   AI碎片JSON+apply+VAD对齐 (直接写SRT, pragmatic exception)
│   ├── oped_fixer.py           #   OP/ED跨集一致性修复
│   ├── align_repair.py         #   P2 对齐→修复计划/时间码/窗口重对齐
│   ├── compare_srt.py          #   字幕对比/相似度
│   ├── translation_stage.py    #   终翻译：源 SRT → translated/zh + QA 报告
│   └── episode_workflow.py     #   单集子进程工作流
│
├── lib/translation/            # 翻译 QA（覆盖率/残留/回显/术语/行长/CPS）
│   └── qa.py
│
├── scan/                       # Phase 1: 字符扫描
│   └── unified_scanner.py      #   乱码检测+VAD语音时间线提取 (gap检测由Phase 2统一处理)
│
├── asr/                        # 2026 ASR/P2 独立入口（不接默认管线）
│   ├── qwen3_worker.py         #   长驻 worker（隔离 venv）
│   ├── qwen3_transcribe.py     #   单文件转录 CLI
│   └── align_cues.py           #   P2 对齐 CLI（默认 dry-run；整集偏移 opt-in）
│
├── eval/                       # 评测（只读；CC=考卷兼边界测试标准，见 eval/protocol.md）
│   ├── asr_eval.py             #   P0 全文件 CER/边界
│   ├── episode_benchmark.py    #   三集 held-out 基准（dev/test、CER/边界/文本分解/归因）
│   ├── release_gate.py         #   一条命令 gate：CC 标准目标 + 基线回归（退出码 0/1）
│   ├── text_gate.py            #   译后闸门：kana/echo/变体/CPS（退出码 0/1）
│   ├── review_fill_eval.py     #   ASR 侧复核对照实验
│   ├── review_translation_eval.py # 翻译失败注入实验
│   ├── diagnose_timeline.py    #   无参考的 token/segment 健康度
│   └── clean_cc_reference.py   #   CC 清洁
│
├── nouns/                      # Phase 3: 专有名词
│   ├── noun_checker.py         #   名词表对比
│   ├── find_suspect_nouns.py   #   未收录疑似专名搜索
│   ├── extractor.py            #   AI 专名提取（extract→aggregate→apply 闭环）
│   ├── aggregate.py            #   跨集 running-map 合并
│   ├── apply_map.py            #   规范名应用（dry-run/apply/emit-mappings）
│   ├── normalize_variants.py   #   变体归一（dry-run/apply/discover，.bak 备份）
│   └── validate_glossary.py    #   对照对齐样本验证候选词表
│
├── apply/                      # 批量修复应用
│   └── apply_fixes.py          #   JSON修复→SRT写入
│
├── ass/                        # ASS格式工具
│   └── ass_repair.py           #   ASS格式修补
│
├── utils/
│   └── update_report.py        #   问题解决报告读写 (JSON权威存储+MD导出)
│
├── translate_srt.py            # 独立翻译工具 (日→中)
├── polish_zh.py                # 独立润色工具 (去翻译腔)
├── auto_translate.py           # 专名自动校对
└── run_all.py                  # 顶层编排器 (唯一入口)
```

## 核心设计原则

1. **数据契约**: 模块间通过结构化数据（dataclass/dict/JSON）通信，orchestrator 统一写文件。例外: FragmentProcessor 直接写 SRT（与 VAD 对齐的耦合不可避免）。

2. **资源驱动**: 有什么用什么。有视频→ASR 链路；有参考字幕→AI注入上下文。无视频→残血运行（仅扫描+AI 审查）。

3. **单一配置源**: `lib/config.py`。所有 env var 读取出处唯一。不直接 `os.environ.get()`。

4. **永不降级**: 报告条目状态 `✅ > 🗑️ > ⬜`，新状态优先级必须 ≥ 旧状态。

5. **CoW 写策略**: 修改 SRT 前 git commit 备份。所有工具原地覆写文件。

6. **稳定核心 + 插件适配器**: ASR 契约/registry/router/评测固定；新模型只加 `lib/asr/adapters/<name>.py`，不改 router/CLI/修复管线。见 `docs/adr/006-stable-asr-core.md`。

7. **流程按技术设计，不硬套旧流程**: 先定数据流和能力边界，再落到代码。
   旧 3-Phase 只保留编排骨架（SCAN → FIX → UNIFY/DELIVER）；内部步骤围绕
   `Qwen3-ASR + FA` 设计（ASR-first），VAD/AED 只做提示。旧流程是工程实现，
   不是规范；必要时重设计。见 `docs/adr/010-asr-first-pipeline.md`。

8. **Review 阶梯（管线内自填，无人工队列）**: AI 只在**检测器判失败**时接入。
   ① `translation`（QA：`japanese_residual`/`echo`/`empty`/`glossary_miss`/`too_long`/`cps`）
   → `<EP>.zh_pack.json`，默认允许改写为中文；② `asr_repair`（ASR 与原文分歧）
   → `<EP>.ai_pack.json`，只有 `route=text/delete` 会问模型，**时间轴不进模型**
   （`route=time` 只做 L0 证据），默认 `REVIEW_ACTIONS=keep` 只分类。
   决策经 route×kind 白名单 + 数值/语言校验 + 逐条回滚，结果写 `<EP>.{ai,zh}_applied.json`；
   keep 不进报告。实测：translation 30 条注入全清、0 误伤；ASR 侧给权 47/48 变差，
   所以只分类。阈值/提示词/实验见 `docs/review-ai-fill.md`，决策见 ADR-012。

9. **LLM 只在两个阶段出现**: 翻译（必须）与复核（检测器判失败的条目）。
   无传统 MT/百度 fallback（已移除）。用用户已配置的**国内** LLM
   （`~/.claude/settings.json` 的 DeepSeek，`LLM_MODEL=deepseek-v4-pro`，已实测）。
   成本原则：优先免费/本地；需要 token 时**只接受国内服务**，国外服务暂不配置。
   复核一个集：translation 28–30 条（2 批）+ ASR 40–60 条（3–4 批）≈ 10–20 s；
   云 ASR 国内可选但不默认，国外不纳入。

10. **技术只通过接缝接入**: 所有候选技术（ASR/FA/VAD/AED/分离/翻译/云/双引擎等）
    必须在 `docs/adr/011-technology-integration-map.md` 的接缝里有位置；可选能力默认
    null 实现，新增 = 加 adapter/stage + 配置 + 证据，不改流程。**没有接缝的技术不接；
    没有指标（能力 + review/成本）的接入不算完成。**

## 测试

```bash
cd <skill>/tests && pytest -q    # 1643 passed + 4 skipped（4 例项目词表测试需 SABU_GLOSSARY_DIR）
cd <skill> && pytest -q tests --cov --cov-report=term-missing   # 覆盖率 79.0%，矩阵见 docs/test-plan.md
```

分层: Tier 1 (纯函数) → Tier 2 (tempfile) → Tier 3 (mock 外部二进制)。
原则：真实项目只用于发现问题；修之前先抽最小 fixture test（见 `tests/README.md`）。
P0 链路（Whisper 编排/VAD、翻译、专名、OP/ED、编排）已补齐；剩余缺口见 `docs/test-plan.md` §3。

## 技术路线

2026 技术栈修订提案：`docs/tech-route-2026.md`；决策记录：`docs/adr/005-asr-alignment-stack.md`、`docs/adr/006-stable-asr-core.md`、`docs/adr/007-cue-alignment.md`、`docs/adr/008-qwen3-long-audio-chunking.md`、`docs/adr/009-reference-as-benchmark.md`、`docs/adr/010-asr-first-pipeline.md`、`docs/adr/011-technology-integration-map.md`；
技术调研：`docs/research-2026-10-tech.md`（翻译提示词/切块/对齐的成熟方案）、`docs/research-2026-10-model-usage.md`（Qwen3-FA 直接对齐/ASR hotwords/FireRedVAD/Mel-Band 用对）、`docs/controlled-experiments.md`（控制变量台账）、`docs/fireredvad-status.md`（FireRedVAD 当前能力/接线/缺口）、`docs/review-ai-fill.md`（复核缺口 AI 填充：契约、路由、守卫、EP001–003 实验）、`docs/glossary-variants.md`（专名变体归一 + 质量闸门：安全规则、测试、52 集实测）。

**技术接入总图**（ADR-011）：所有候选技术按接缝分层——Ingest/Separator、AudioEvents（VAD/AED）、
ASR/FA registry、Ensemble、Alignment/Repair、Evidence/Review、Glossary/Translation、Evaluation。
当前**未接入**：FireRedVAD/AED（仅 provider/CLI）、Silero/TEN、RoFormer、云 ASR、
跨引擎一致性、LLM 可读性、OP/ED 指纹、切片重对齐、chunk 选择、glossary→ASR context、
Review 自动分级/auto-AI-human 计数（已由 `lib/review` 的 pack 路由 + `stats` 提供，
见 ADR-012）。可选能力默认 null，新增不改流程。
**说话人/VibeVoice 已由用户删除（2026-10）**，不再保留接缝或计划。

**翻译链路**（`docs/translation-architecture.md`）：`translate_srt.py`（批处理/术语）
→ `lib/translation/qa.py`（覆盖率/日语残留/回显/术语/行长/CPS）
→ `--repair` 只修问题条 → `fix/translation_stage.py` 输出 `translated/zh/<EP>.srt`
+ `<EP>.qa.json`；`run_all --translate` 已接（默认关，失败不致命）。
EP001 全量 276 条：baseline 2 违规 → repair 后 0；30 条样本 0 违规。
未来上下文/series memory/chrF guardrail 仍未做。

**2026-10 技术审计**（`docs/model-audit-2026-10.md`）：下载前先验“新 = 领先”。
Qwen3-ASR-1.7B/FA 仍是日语媒体第一梯队（WER 0.185 / AAS 42.2ms），保留；
**P0 只下 FireRedVAD/AED**（2026-03，F1 97.57，0.01GB）；说话人/VibeVoice 已删除；
Parakeet JA、ReazonSpeech v2、BuzzASR、WhisperX、pyannote、Sortformer v1 淘汰；
Demucs 降级 fallback；Whisper 全系只保留本机 CUDA，不下载/不再测试；
Qwen3-0.6B 无日语领先证据，暂不接。

**模型资产/调用手册**：`docs/model-assets.md`（新会话先读，别重下）。
模型都在 `models/`（gitignored）：FireRedVAD 已下载并实测（EP001 speech 666.7s /
music 893.8s / singing 101.8s）；Mel-Band RoFormer fp16/fp32 权重已下（运行时未接线）。
`.venv-qwen` 含 torch 2.6、modelscope、fireredvad。

核心变化：`Qwen3-ASR-1.7B + Qwen3-ForcedAligner-0.6B` 替代 `Whisper kotoba + 时间重叠匹配`；`FireRedVAD` 只作提示/AED；Whisper 只保留本机 CUDA（不下载/不再测试）。**流程按 ASR-first 重设计**（ADR-010）：3-Phase 只保留编排骨架，内部不再用 VAD 门控修复区、不再用时间重叠贴 cue；review 走 L0 自动 → L1 AI 批审 → L2 AI 复核 → L3 人工残留；翻译用已配置的国内 LLM（DeepSeek，唯一 LLM 阶段；百度已移除）。迁移期默认链路仍是旧 Whisper，回滚开关 `ASR_ENGINE=whisper`。

ASR 架构已按「稳定核心 + 插件适配器」落地：`lib/asr/base.py`（契约）、`registry.py`（自动发现）、`router.py`（路由），新模型只写 `lib/asr/adapters/<name>.py`，不改 core。P1 进度见 `docs/tech-route-progress.md`。

音频事件接缝已落地第一段：`lib/audio/base.py`（`AudioEvent`/`AudioHints` JSON 契约）、
`lib/audio/fireredvad.py`（主环境 subprocess 调 `.venv-qwen` worker，不 import torch）、
`scripts/audio/detect_events.py`（16k mono wav → `<EP>.audio_hints.json`）。
FireRedVAD/AED 已下载并实测，EP001 生成 442 个事件（speech 48.8% / singing 8.9% / music 78.1%）；
**主流程已接 `run_all --audio-hints`**：在 ASR 抽出的音频上跑 provider，写
`<EP>.audio_hints.json`；失败只警告，不影响修复。**决策尚未消费 hints**（OP/ED/空洞是下一步）。
能力测试（`docs/capability-tests.md`）：三集 53 个空洞里
只有 12 个（23%）是 speech 主导，其余多为音乐/混合 → hints 只能做证据过滤，不能做门控；
整集 OP/ED 实验：singing 在 OP 56% / ED 50–51% vs 正文 8–9%，是可用的候选信号；
三集 OP 段几乎逐秒一致。候选规则尚未接主流程。说话人/VibeVoice 已删除（用户决定），不再保留计划。

P2 对齐层已落地：`lib/alignment/*`（字符时间轴 → 时间窗匹配 → 信号/置信度；`quality.py` 评估零时长/overlong/空洞 `lost_spans`，token 灾难性塌缩才回退 segment）、`fix/align_repair.py`（修复计划/时间码/整集偏移/相邻 cue 联动；结尾 display linger 保留）、`scripts/asr/align_cues.py`（端到端 CLI，默认 dry-run；整集偏移改为 opt-in）、`run_all --asr`（flag 控制；`--asr-global-offset` 单独 opt-in）。**用户确认日语 CC 边界即测试标准**（不再另建人工金标），raw 数字直接判定，评测入口是 scripts/eval/release_gate.py（目标 + 基线回归，退出码 0/1）；三集基准：`temp/reports/digimon_qwen3_raw.{json,md}`、`digimon_whisper_raw.{json,md}`（第八轮新增 signed 偏差/覆盖分解/空洞：`temp/reports/digimon_qwen3_r8.*`、`digimon_whisper_r8.*`）。**第八轮结论**：test 集 90% matched cue 起点系统性早 0.37–0.40 s（重转写复现，是当前对齐的起点缺陷；EP001 仅 −55 ms），P90 只覆盖 42–45% 的 CC 字符，FA 塌缩吞掉成段对白（累计空洞 122–229 s）；分段重对齐/overlong/cue 级 rate trim 均实测无收益，起点偏差与空洞要 P3 VAD 才能修。

素材现状：**验收作品定为数码宝贝（Digimon Adventure）**，目录 `<video-root>\数码宝贝`。**EP01/02/03 视频完整（各约 1386s）**；用户人工复核三集视频/CC 均无真实偏移。日文参考：`日语字幕\` 下 S01E01–S01E54 共 54 集 `ja[cc].srt`，已清洁；主故事窗口 `[95,1240]`。01–05 另有中文字幕，只能做参考无关/翻译复用。素材台账 `eval/materials.md`，清单 `eval/manifest.digimon.json`（Qwen3）与 `eval/manifest.digimon.whisper.json`（A/B）。莎莉/阿童木历史素材只保留机制/吞吐回放。

测试：1643 passed / 4 skipped（需 SABU_GLOSSARY_DIR 的项目词表例）/ 0 failed；矩阵与剩余缺口见 `docs/test-plan.md`。

## 配置

所有配置在 `lib/config.py`。环境变量在 CLAUDE.md / `~/.claude/settings.json` 中设置。关键变量:
- `WHISPER_CLI` / `WHISPER_MODEL` — Whisper 路径；用户环境已配置 whisper.cpp：
  `D:/software/video/whisper-cublas-12.4.0-bin-x64/whisper-cli.exe`，
  模型 kotoba q5 / large-v3 q5（fallback/A-B 可用）。
- `ASR_ENGINE` / `ASR_MODEL` / `QWEN_PYTHON` — 2026 ASR 路线（默认 `whisper`，未启用）
- `ASR_GLOBAL_OFFSET` — 整集偏移默认关；`--asr-global-offset` 或 `ASR_GLOBAL_OFFSET=1` 才应用
- `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` — 翻译用 LLM；用户已在
  `~/.claude/settings.json` 的 `env` 配置（DeepSeek，`LLM_MODEL=deepseek-v4-pro`，已实测）。
  Claude Code 会自动注入；其他 shell（如 DSH）需手动 export 或在命令前加载。
  **只有翻译阶段允许调用它**；Review/润色等不新增 API 调用。
