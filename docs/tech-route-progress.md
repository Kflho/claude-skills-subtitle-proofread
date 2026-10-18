# 2026 技术路线实施进度

> **用途**：跨会话续跑。新会话先读本文件，再读 [`tech-route-2026.md`](tech-route-2026.md)、[`ADR-006`](adr/006-stable-asr-core.md)、[`ADR-007`](adr/007-cue-alignment.md)、[`ADR-008`](adr/008-qwen3-long-audio-chunking.md)、[`test-plan.md`](test-plan.md)。
> **最后更新**：2026-10-18（HEAD `d7d585c`；第二轮：测试 P0 链路补齐）
> **当前阶段**：P2 对齐 + `run_all --asr` + 实机验证完成；测试 P0 补完（858 tests / 56.2%）；默认仍走 Whisper。下一步 = **P2-14 人工金标 + P1-10 阿童木 A/B**。
> **总目标**：把音频链路从 `WebRTC VAD → kotoba Whisper → 时间重叠匹配` 迁移到 `Qwen3-ASR → Qwen3-ForcedAligner → 全局文本对齐`；默认行为在 P2 验收前保持不变。

## 0. 当前一句话（clear 后从这里开始）

已经完成：P1 稳定 ASR 核心、P2 对齐层、`run_all --asr` 接入、莎莉 EP001 实机验证（发现并修复 `qwen_asr` 180s 分块导致的时间戳塌缩：worker 自切 60s + 补零，RTF 0.83→0.207–0.295）、整集偏移估计；**测试 P0 链路补齐：858 passed / 56.2%（上轮 576 / 36.3%）**，并顺带修掉 3 个真 bug（glossary 表头/计数表误当译名、跨盘 `relpath` 崩溃、`oped_fill` 写盘参数顺序反了）。

**下一步按顺序：**

1. **P2-14 人工金标（默认切换的唯一阻塞）**：EP001 抽 100 条 cue，人工听校起止边界；算真实 P50/P90/±100ms。
2. **P1-10 参考无关 A/B（阿童木 3 集）**：视频已到位（193 MKV），但同目录 SRT 是**机翻中文、2s 粗轴**，不能当参考；只做 RTF、时间轴覆盖、Qwen3/Whisper 跨引擎一致度、幻觉/重复度。
3. **chunk 调参**：45/60/90s 在金标上的边界误差 vs 耗时，确定默认。
4. **0.6B 复测**：解释整集 880s 与热态 0.122 RTF 的反常，再决定草稿模式。
5. **P1 测试补洞（可与 1–4 并行，不阻塞）**：`whisper_fixer`/`fragment_processor`、`find_suspect_nouns` 聚类、`noun_checker`/`apply_map`、`reuse/*`、`run_all` 各 `step_*`（见 `test-plan.md` §3）。
6. **默认切换**：只有金标达标才把 `ASR_ENGINE` 切到 `qwen3`；`ASR_ENGINE=whisper` 保留回滚。

## 1. 状态总览

| 阶段 | 内容 | 状态 | 验收 |
|---|---|---|---|
| P0 | 评测金标清单 + 基线指标 | 🟡 代码就绪，待真实素材 | `eval/manifest.json` 填 12 集；旧管线 CER/边界可复现 |
| P1 | 稳定核心 + Qwen3/Whisper 适配 + 评测/CLI | 🟡 smoke 通过；阿童木 A/B 可跑（仅参考无关） | 3 集跑通；A/B 有记录；默认不变 |
| P2 | Qwen3-FA + cue mapper + align_repair | 🟡 核心/CLI/run_all/实机验证完成；默认未切 | 边界 P50 ≤100ms / P90 ≤300ms（待人工金标） |
| 测试 | 858 tests / 56.2% 覆盖 / 0 failed | 🟡 P0 链路补齐；P1 缺口见 test-plan | 关键路径每个分支至少一条测试 |
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
- [ ] P2-14：人工金标（100 条 cue 边界）+ P50/P90 验收 ← **下一步优先**
- [ ] P2-11：阿童木 193 集参考无关 A/B（视频已到位；字幕是机翻中文、2s 粗轴，**不能当金标**）

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
- [x] T10：本轮修掉 3 个真 bug 并各配回归测试（见 §4）

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

### 测试基线（2026-10-18 第二轮：858 tests / 56.2%）

```text
python -m pytest -q
858 passed in ~6.3s          # 无网络、无模型、无真实项目
coverage: 56.2% (13,577 statements)     # 上轮 576 / 36.3%
```

覆盖变化（按目录）：

| 目录 | 语句 | 本轮 | 上轮 | 说明 |
|---|---:|---:|---:|---|
| `lib` | 3,313 | 75.3% | 73.7% | whisper_backends 44→59%（faster/openai fake 模块） |
| `fix` | 3,190 | **61.0%** | 28.4% | whisper_pipeline 36→81%、oped_fixer 0→77%、oped_fill 0→70% |
| `nouns` | 2,424 | **47.4%** | 9.5% | aggregate 86%、build_glossary 92%、extractor 85%、find_suspect 37% |
| 根脚本 | 2,005 | **42.3%** | 7.5% | translate_srt 85%、polish_zh 99%、run_all 34% |
| `asr` / `scan` / `apply` / `reuse` / `eval` / `ass` / `utils` | — | 10–80% | 同左 | 本轮未动 |

新增 11 个测试文件：`test_whisper_pipeline`（扩展）、`test_whisper_backends`（扩展）、
`test_translate_srt`、`test_baidu_translate`、`test_polish_zh`、`test_nouns_aggregate`、
`test_build_glossary`、`test_nouns_extractor`、`test_find_suspect_nouns`、
`test_oped_fixer`、`test_oped_fill`、`test_run_all_steps`、`test_episode_workflow`。
矩阵、剩余 P1 缺口与渐进门槛见 [`docs/test-plan.md`](test-plan.md)。

**本轮测试顺带修掉的 3 个真 bug**（各配回归测试）：

| 位置 | 问题 | 影响 |
|---|---|---|
| `translate_srt.load_glossary` | `split('|')` 的空首列没滤掉，表头被当成「日语→中文」映射；`build_glossary` 的「日语\|次数\|变体」表被当译名表 | 数字（96）会被 `apply_noun_pre_replace` 替换进正文，prompt 也被污染 |
| `nouns/aggregate.py`、`nouns/extractor.py`、`translate_srt.py` | Windows 跨盘 `os.path.relpath` 抛 `ValueError` | 日志行能打死整条流水线（temp 在 C:、项目在 D: 时） |
| `fix/oped_fill._apply_templates` | `write_subtitles(cues, fpath)` 参数顺序反了 | 歌词模板永远写不回字幕文件 |

- 原则：**真实项目只用于发现问题；修之前先落最小 fixture test。**

### P2 结构回归：魔法使莎莉 EP001（2026-10-18）

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

### P2 真实验证：莎莉 EP001 日语视频（2026-10-18）

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
- ✅ 莎莉日语视频已到位（110 集 MKV）：`D:\videos\Animation\TV\[1966-1981] Toei Majokko series_東映魔女大系(東映魔女っ子シリーズ)_TV\[1966-1991] Sally_小仙女莎莉(魔法使いサリー)_TV\[1966-1968] Sally_小仙女莎莉 第1作(魔法使いサリー)_TV`。
- ✅ P2-13 实机验证完成：180s 分块塌缩 → 自切 60s + 补零；RTF 0.83→0.207–0.295，零时长 48%→8.4%（§4）。
- ✅ 测试基线：全量 **858 passed / 0 failed / 56.2%**（上轮 576 / 36.3%）；P0 链路（Whisper 编排/VAD、翻译、专名、OP/ED、编排）补齐；3 个真 bug 修复并配回归测试。
- ⬜ 莎莉没有日语参考字幕（源字幕是俄语），CER/边界金标仍缺（P2-14）。
- ⬜ P1 测试缺口：`whisper_fixer` / `fragment_processor`、`find_suspect_nouns` 聚类、`noun_checker` / `apply_map`、`reuse/*`、`run_all` 各 `step_*`（见 `test-plan.md` §3）。
- ⬜ 整集 0.6B（880s）与热态 RTF 0.122 反常，需散热稳定后复测。
- ✅ 阿童木视频已到位：`D:\videos\Animation\TV\[Anonymoose] 鉄腕アトム (DVD, 10bit)`，193 MKV + 193 同名 SRT。
- ⚠️ 阿童木同目录 SRT 是**机翻中文、2s 粗轴**（样例：`嗯` / `飞雄啊——！`），不是日语参考，也**不能当金标**。
- ⬜ 阿童木/Sally 都缺可信日语参考 → CER 与边界金标仍需人工听校（P2-14）。
- ⛔ 无硬阻塞；默认切换等人工金标（P2-14）。

## 6. 下一步（按顺序）

1. **P2-14 人工金标（默认切换的唯一阻塞）**：从 EP001（Sally 或 Atom 均可）抽 100 条 cue，人工听校起止边界；跑 `alignment_bench` 边界统计与 `asr_eval.py`，得出真实 P50/P90/±100ms。
2. **P1-10 阿童木参考无关 A/B（3 集）**
   - 视频：`D:\videos\Animation\TV\[Anonymoose] 鉄腕アトム (DVD, 10bit)`；同目录 SRT 是机翻中文、2s 粗轴，**只当输入，不当参考**。
   - 跑 `qwen3_transcribe.py --timestamps`（1.7B，60s 分块）与 `--engine whisper`（large-v3 q5），记录 RTF、字数、token/segment、零时长率、时间轴覆盖、跨引擎逐段一致度、重复/幻觉段。
   - 不计算 CER / 边界误差；报告明确标「非金标」。参考无关指标模板见 §4 莎莉 EP001。
3. **chunk 调参**：45/60/90s 在同一金标上的边界误差 vs 处理时间，确定最终默认值。
4. **0.6B 复测**：散热稳定后重跑整集，解释 880s vs 313–447s，再决定是否保留草稿模式。
5. **P1 测试补洞（可与 1–4 并行，不卡验收）**
   - `fix/whisper_fixer.py` / `fragment_processor.py`：`_apply_whisper_result` 的 cues_to_clear/cut 组合、AI 片段 JSON。
   - `nouns/find_suspect_nouns.py` 的 `_cluster_by_source` / `search_corrections` / `_format_candidates`；`noun_checker` / `apply_map` 主流程。
   - `reuse/align.py` / `reuse/apply.py`：R1/R2 采纳与「不采纳总集篇」规则。
   - `run_all.step_scan/step_nouns/step_apply_all/step_deliver/step_clean` 的副作用分支。
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
| `tests/test_translate_srt.py` / `test_baidu_translate.py` / `test_polish_zh.py` | 翻译链路（LLM prompt/JSON、百度签名、润色写盘策略） | ✅ |
| `tests/test_nouns_aggregate.py` / `test_build_glossary.py` / `test_nouns_extractor.py` / `test_find_suspect_nouns.py` | 专名链路（合并/输出/sidecar/规则） | ✅ |
| `tests/test_oped_fixer.py` / `test_oped_fill.py` | OP/ED（三分类 / 只改歌词 / 只填空行） | ✅ |
| `tests/test_run_all_steps.py` / `test_episode_workflow.py` | 编排（顺序 / flag / 资源驱动步骤 / Whisper-ASR 分派） | ✅ |
| `scripts/lib/project_utils.py` | 新增 `display_path()`（跨盘 `relpath` 兜底） | ✅ |
| `scripts/translate_srt.py` / `nouns/aggregate.py` / `nouns/extractor.py` / `fix/oped_fill.py` | 本轮 bug 修复（glossary 解析 / relpath / 写盘参数顺序） | ✅ |

## 8. 环境命令

```powershell
# ── 测试（日常/CI，不需要模型和素材）──
python -m pytest -q
python -m pytest -q --cov --cov-report=term-missing
python -m pytest -q tests\test_whisper_pipeline.py
python -m pytest -q tests\test_cue_mapper.py -k duplicate

# 本轮 P0 链路（Whisper 编排 / 翻译 / 专名 / OP-ED / 编排）
python -m pytest -q tests\test_whisper_pipeline.py tests\test_whisper_backends.py `
  tests\test_translate_srt.py tests\test_baidu_translate.py tests\test_polish_zh.py `
  tests\test_nouns_aggregate.py tests\test_build_glossary.py `
  tests\test_nouns_extractor.py tests\test_find_suspect_nouns.py `
  tests\test_oped_fixer.py tests\test_oped_fill.py `
  tests\test_run_all_steps.py tests\test_episode_workflow.py

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

# ── 真实 EP001 复现（可选，约 5–8 分钟；模型/路径按本机）──
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
- **0.6B 不默认**：热态快 1.5×，但整集反而慢、质量更差；需复测后再定草稿模式。
- **测试优先（第二轮）**：858 tests / 56.2% 覆盖 / 0 failed；P0 链路每条安全策略都有断言（dry-run 不写、VAD 默认不删、OP/ED 只改歌词、只填空行）；真实项目发现的问题必须先落最小 fixture test，矩阵见 [`docs/test-plan.md`](test-plan.md)。
- **glossary 表格解析规则**：只有「日语\|中文」两列才当译名映射；`build_glossary` 的「日语\|出现次数\|拼写变体」表不产生映射（踩坑 #17：否则数字会被预替换进正文）。
- **日志路径用 `display_path`**：Windows 跨盘 `os.path.relpath` 抛 `ValueError`，不能出现在主流程里（temp 在 C:、项目在 D: 就会触发）。
- **`write_subtitles(path, cues)` 顺序固定**：`oped_fill` 曾写成 `(cues, fpath)`，模板永远写不回去；有回归测试锁定。
- **金标来源规则**：机翻 / ASR / LLM 润色后的字幕都不能当参考。阿童木同目录 SRT 是机翻中文、2s 粗轴，只能用于参考无关 A/B；CER/边界必须人工听校（规则写进 `eval/README.md`）。
- **进程隔离**：主环境不 import torch/qwen-asr；worker JSON lines。
- **Windows**：vLLM 不支持；走 Transformers 后端 + SDPA。
- **模型下载**：HF 直连被墙；用 ModelScope（已验证 200）/ 镜像。
- **已知精度边界**：段级 ASR（无 token）P50 ≈300ms；Qwen3-FA 在真实动画音频上仍有 8.4% 零时长 token。
- **相邻 cue 联动未做**：没有整集偏移时，逐条时间修正遇到「与相邻 cue 重叠」会转 review（EP001 首轮 89/344 自动）；下一步需要相邻 cue 联合求解。
- **既存失败已清零**：OP/ED 常量断言改成行为测试、`MockWhisperResult` 补 `cues_to_clear`；全量 858 passed。
- **续跑原则**：先看 §0 的 6 步，再看 §4 的验证数据；不要重写核心，只加 adapter/adapter 测试。
