# 2026 技术路线实施进度

> **用途**：跨会话续跑。新会话先读本文件，再读 [`tech-route-2026.md`](tech-route-2026.md) 与 [`ADR-006`](adr/006-stable-asr-core.md)、[`ADR-007`](adr/007-cue-alignment.md)。
> **最后更新**：2026-10-18
> **当前阶段**：P2 对齐核心 + CLI + `run_all --asr` 接入完成；真实日语视频验证进行中，默认仍走 Whisper
> **总目标**：把音频链路从 `WebRTC VAD → kotoba Whisper → 时间重叠匹配` 迁移到 `Qwen3-ASR → Qwen3-ForcedAligner → 全局文本对齐`；默认行为在 P2 验收前保持不变。

## 0. 当前一句话

P1 的稳定 ASR 核心（契约/注册器/路由/评测/CLI）已落地并 smoke 通过；P2 对齐层已做成可运行切片：字符时间轴 → 时间窗内字符匹配 → 状态/置信度 → 修复计划/时间码应用，并接入 `run_all --asr`（默认仍 Whisper）。真实日语视频（莎莉 EP001）已到位，正在跑 Qwen3-ASR 做端到端验证。

## 1. 状态总览

| 阶段 | 内容 | 状态 | 验收 |
|---|---|---|---|
| P0 | 评测金标清单 + 基线指标 | 🟡 代码就绪，待真实素材 | `eval/manifest.json` 填 12 集；旧管线 CER/边界可复现 |
| P1 | 稳定核心 + Qwen3/Whisper 适配 + 评测/CLI | 🟡 smoke 通过，待 3 集 A/B | 3 集跑通；与旧管线 A/B 有记录；默认不变 |
| P2 | Qwen3-FA + cue mapper + align_repair | 🟡 核心 + CLI + run_all 接入（flag）；默认未切 | 边界 P50 ≤100ms / P90 ≤300ms（真实金标待测） |
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
- [x] P2-8：测试 91 项（`test_alignment_*`、`test_cue_*`、`test_align_repair`、`test_align_cues_cli`、`test_alignment_bench`）
- [x] P2-9：真实 cue 结构回归 + `scripts/eval/alignment_bench.py`（魔法使莎莉 EP001，405 cue；见 §4）
- [x] P2-10：`docs/adr/007-cue-alignment.md`
- [x] P2-12：`Fixer.fix_by_asr()` + `run_all --asr/--asr-python/--asr-fix-text`（默认 time-only；ASS 拒绝写回；诊断落 `temp/scans/<EP>.alignment.json`）
- [ ] P2-13：真实日语视频端到端验证（莎莉 EP001；Qwen3-ASR + FA 全片转写与对齐，进行中）
- [ ] P2-11：真实日语素材 A/B（阿童木 193 集；视频目录当前不在盘上）

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

### P2 单元测试（2026-10-18）

```text
python -m pytest -q tests/test_alignment_base.py tests/test_alignment_timeline.py \
  tests/test_cue_mapper.py tests/test_cue_diagnostics.py \
  tests/test_align_repair.py tests/test_alignment_aligner.py \
  tests/test_align_cues_cli.py tests/test_alignment_bench.py
91 passed
```

覆盖：归一化/tag 处理、token 与 segment 时间轴、重复台词、时间漂移、长段合并、重叠 cue、空文本/无时间戳、
置信度分层、修复计划与邻居冲突、`--write`/`--fix-text`、ASS 写入拒绝、fake aligner registry、
ffmpeg 窗口重对齐、bench 合成场景。

全量：`408 passed, 3 failed` —— 3 个既存失败与 P2 无关（OP/ED 边界断言 95/120 vs 实际 180；`MockWhisperResult` 缺 `cues_to_clear`）。

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
- ✅ P2 对齐核心 + 修复计划 + CLI + `run_all --asr` 完成；91 项测试、真实 cue 结构回归已跑（§4）。
- ✅ 莎莉日语视频已到位：`D:\videos\Animation\TV\[1966-1981] Toei Majokko series_…\[1966-1991] Sally…\[1966-1968] Sally…\TV`（110 集 MKV，EP001 = 1512s / AC3 stereo）。
- 🟡 真实验证：EP001 全片 Qwen3-ASR-1.7B + FA 转写/对齐进行中（P2-13）。
- ⬜ 莎莉没有日语参考字幕（源字幕是俄语），CER/边界金标仍缺；只能用参考无关指标 + Whisper 交叉对比。
- ⬜ 阿童木 193 集视频目录不在盘上（`D:\videos\Animation\TV\[Anonymoose] 鉄腕アトム (DVD, 10bit)`）。
- ⛔ 无硬阻塞；默认切换等真实验证结论。

## 6. 下一步（按顺序）

1. **P2-11 真实 A/B**：拿到日语视频后，任选 3 集，先用
   `qwen3_transcribe.py --timestamps` 生成 ASR JSON，再用
   `align_cues.py --srt <参考字幕> --asr-json <json>` 跑 mapper，把
   P50/P90/±100ms 与人工边界对比填进 §4；manifest 同步填 `eval/manifest.json`。
2. **P2-12 接默认管线**：`run_all.py --asr qwen3` → `Fixer.fix_by_asr()`；
   只在 A/B 达到 DoD 后切默认，保留 `ASR_ENGINE=whisper` 回滚开关。
3. **P3**：`lib/audio/vad.py` + FireRedVAD；`speech_coverage` 从 ASR 近似换成真实 VAD，VAD 只作提示。
4. **P1-10 / P4**：ASR A/B 记录与 glossary-first 仍按原计划排队。

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
| `scripts/asr/qwen3_worker.py` | 长驻 worker | ✅ |
| `scripts/asr/qwen3_transcribe.py` | 单文件 CLI | ✅ |
| `scripts/asr/setup_qwen3.ps1` | 隔离环境 | ✅ |
| `scripts/asr/README.md` | 运行说明 | ✅ |
| `scripts/eval/asr_eval.py` | 评测 | ✅ |
| `eval/manifest.example.json` / `README.md` | 金标模板 | ✅ |
| `templates/asr_adapter.py` | 新 ASR 骨架 | ✅ |
| `templates/aligner_adapter.py` | 新对齐器骨架 | ✅ |
| `tests/test_asr_*.py` / `test_qwen3_*.py` | ASR 测试 | ✅ |
| `docs/adr/007-cue-alignment.md` | 对齐决策 | ✅ |
| `scripts/lib/alignment/base.py` | 对齐契约/归一化 | ✅ |
| `scripts/lib/alignment/timeline.py` | 字符时间轴 | ✅ |
| `scripts/lib/alignment/cue_mapper.py` | 时间窗 + 字符匹配 | ✅ |
| `scripts/lib/alignment/diagnostics.py` | 信号/置信度/统计 | ✅ |
| `scripts/lib/alignment/aligner.py` | registry 对齐服务 | ✅ |
| `scripts/fix/align_repair.py` | 修复计划/时间应用/重对齐 | ✅ |
| `scripts/asr/align_cues.py` | P2 端到端 CLI | ✅ |
| `scripts/eval/alignment_bench.py` | P2 结构回归（合成 ASR） | ✅ |
| `tests/test_alignment_*.py` / `test_cue_*.py` / `test_align_repair.py` / `test_align_cues_cli.py` / `test_alignment_bench.py` | P2 测试（91 项） | ✅ |
| `lib/config.py` | 新配置项 | ✅ |
| `run_all.py` / `fix_orchestrator.py` | P2 接入（默认管线） | ⬜ |

## 8. 环境命令

```powershell
# 主环境测试（不装 qwen-asr）
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

# 已通过的 smoke test
.venv-qwen\Scripts\python.exe scripts\asr\qwen3_transcribe.py `
  --audio temp\smoke\ja_sample.wav --asr models\Qwen3-ASR-0.6B `
  --aligner models\Qwen3-ForcedAligner-0.6B --timestamps `
  --output temp\smoke\ja_sample.json --srt temp\smoke\ja_sample.srt
```

## 9. 决策 / 风险

- **默认不变**：`ASR_ENGINE=whisper`；P2 只以独立 CLI 交付，`run_all` 未接。
- **不伪造置信度**：P1 只出文本/token 时间；P2 用对齐一致度 + 多信号投票（ADR-007）。
- **对齐只在时间轴上动刀**：文本替换默认关闭（`allow_text_replace=False`）；删除永不自动。
- **自动改时间码的硬上限**：单边 ≤2s，且不能撞上原本不重叠的 cue；超限转 review。
- **乱序 cue 在 mapper 内排序再还原**：真实 ASS 有乱序行，不能要求输入先排序。
- **进程隔离**：主环境不 import torch/qwen-asr；worker JSON lines。
- **Windows**：vLLM 不支持；走 Transformers 后端 + SDPA。
- **模型下载**：HF 直连被墙；用 ModelScope（已验证 200）/ 镜像。
- **已知精度边界**：段级 ASR（无 token）P50 ≈300ms，只有 Qwen3-FA 的 token 时间轴能到 P50 ≈30ms。
- **已有测试**：全量 3 个既存失败（OP/ED 边界断言 95/120 vs 实际 180；`MockWhisperResult` 缺 `cues_to_clear`），与本路线无关；另开任务处理。
- **续跑原则**：先看第 1 节状态、第 4 节验证、第 6 节下一步；不要重写核心，只加 adapter。
