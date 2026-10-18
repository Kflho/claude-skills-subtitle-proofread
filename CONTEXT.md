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
│   ├── asr/                    #   ASR 稳定核心 + 插件适配器（2026 路线）
│       ├── base.py             #     契约：ASRResult/ASRConfig/BackendInfo/Protocol
│       ├── registry.py         #     自动发现 adapters/*
│       ├── router.py           #     引擎选择/fallback；不 import 模型
│       └── adapters/           #     qwen3 / qwen3_aligner / whisper
│   └── alignment/              # P2 cue 对齐（纯 stdlib，不 import 模型）
│       ├── base.py             #     契约/归一化/状态词表（含 display linger 规则）
│       ├── timeline.py         #     ASRResult → 字符时间轴（token/segment 兜底）
│       ├── quality.py          #     时间轴健康度：零时长 run/overlong/fallback
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
│   ├── translate_srt.py        #   百度翻译+LLM翻译
│   └── episode_workflow.py     #   单集子进程工作流
│
├── scan/                       # Phase 1: 字符扫描
│   └── unified_scanner.py      #   乱码检测+VAD语音时间线提取 (gap检测由Phase 2统一处理)
│
├── asr/                        # 2026 ASR/P2 独立入口（不接默认管线）
│   ├── qwen3_worker.py         #   长驻 worker（隔离 venv）
│   ├── qwen3_transcribe.py     #   单文件转录 CLI
│   └── align_cues.py           #   P2 对齐 CLI（默认 dry-run；默认整集偏移）
│
├── eval/                       # 评测（只读；参考字幕=考卷，见 eval/protocol.md）
│   ├── asr_eval.py             #   P0 全文件 CER/边界
│   ├── episode_benchmark.py    #   三集 held-out 基准（dev/test、CER/边界/修复安全性）
│   ├── diagnose_timeline.py    #   无参考的 token/segment 健康度
│   └── clean_cc_reference.py   #   CC 清洁
│
├── nouns/                      # Phase 3: 专有名词
│   ├── noun_checker.py         #   名词表对比
│   └── find_suspect_nouns.py   #   未收录疑似专名搜索
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

2. **资源驱动**: 有什么用什么。有视频→Whisper修复；有参考字幕→AI注入上下文。无视频→残血运行（仅扫描+人工审查）。

3. **单一配置源**: `lib/config.py`。所有 env var 读取出处唯一。不直接 `os.environ.get()`。

4. **永不降级**: 报告条目状态 `✅ > 🗑️ > ⬜`，新状态优先级必须 ≥ 旧状态。

5. **CoW 写策略**: 修改 SRT 前 git commit 备份。所有工具原地覆写文件。

6. **稳定核心 + 插件适配器**: ASR 契约/registry/router/评测固定；新模型只加 `lib/asr/adapters/<name>.py`，不改 router/CLI/修复管线。见 `docs/adr/006-stable-asr-core.md`。

## 测试

```bash
cd <skill>/tests && pytest -q    # 1362 tests, ~9s, 无外部依赖
pytest -q --cov --cov-report=term-missing   # 覆盖率 79.6%，矩阵见 docs/test-plan.md
```

分层: Tier 1 (纯函数) → Tier 2 (tempfile) → Tier 3 (mock 外部二进制)。
原则：真实项目只用于发现问题；修之前先抽最小 fixture test（见 `tests/README.md`）。
P0 链路（Whisper 编排/VAD、翻译、专名、OP/ED、编排）已补齐；剩余缺口见 `docs/test-plan.md` §3。

## 技术路线

2026 技术栈修订提案：`docs/tech-route-2026.md`；决策记录：`docs/adr/005-asr-alignment-stack.md`、`docs/adr/006-stable-asr-core.md`、`docs/adr/007-cue-alignment.md`、`docs/adr/008-qwen3-long-audio-chunking.md`、`docs/adr/009-reference-as-benchmark.md`。

核心变化：`Qwen3-ASR-1.7B + Qwen3-ForcedAligner-0.6B` 替代 `Whisper kotoba + 时间重叠匹配`；`FireRedVAD` 只作提示/AED；Whisper 保留 fallback。**默认管线仍走旧链路**，2026 链路以独立 CLI 交付。

ASR 架构已按「稳定核心 + 插件适配器」落地：`lib/asr/base.py`（契约）、`registry.py`（自动发现）、`router.py`（路由），新模型只写 `lib/asr/adapters/<name>.py`，不改 core。P1 进度见 `docs/tech-route-progress.md`。

P2 对齐层已落地：`lib/alignment/*`（字符时间轴 → 时间窗匹配 → 信号/置信度；`quality.py` 评估零时长/overlong，token 灾难性塌缩才回退 segment）、`fix/align_repair.py`（修复计划/时间码/整集偏移/相邻 cue 联动；结尾 display linger 保留）、`scripts/asr/align_cues.py`（端到端 CLI，默认 dry-run，默认先做整集偏移）、`run_all --asr`（flag 控制）。参考字幕按 `eval/protocol.md` 只作 held-out 基准；三集基准看 `temp/reports/digimon_benchmark_final.{json,md}`。莎莉 EP001 历史实机验证：`qwen_asr` 180s 分块会塌缩 → worker 自切 60s + 补零（ADR-008；机械/吞吐验证，不作金标）。

素材现状：**验收作品定为数码宝贝（Digimon Adventure）**，目录 `D:\videos\Animation\TV\数码宝贝`。**EP01/02/03 视频完整（各约 1386s）**；EP01 已由用户人工核对日文 CC 与视频同步，EP02/03 待确认（实测有 -0.37/-0.40s 全局偏移）。日文参考：`日语字幕\` 下 S01E01–S01E54 共 54 集 `ja[cc].srt`，已清洁；主故事窗口 `[95,1240]`。01–05 另有中文字幕，只能做参考无关/翻译复用。素材台账 `eval/materials.md`，清单 `eval/manifest.digimon.json`（EP01=dev，EP02/03=test）。莎莉/阿童木历史素材只保留机制/吞吐回放。

测试：1362 passed / 0 failed / 79.6% 覆盖；矩阵与剩余缺口见 `docs/test-plan.md`。

## 配置

所有配置在 `lib/config.py`。环境变量在 CLAUDE.md 中设置。关键变量:
- `WHISPER_CLI` / `WHISPER_MODEL` — Whisper 路径
- `ASR_ENGINE` / `ASR_MODEL` / `QWEN_PYTHON` — 2026 ASR 路线（默认 `whisper`，未启用）
- `LLM_API_KEY` — LLM API key
- `BAIDU_APPID` / `BAIDU_SECRET` — 百度翻译（可选）
