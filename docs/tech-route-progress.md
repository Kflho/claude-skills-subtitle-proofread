# 2026 技术路线实施进度

> **用途**：跨会话续跑。新会话先读本文件，再读 [`tech-route-2026.md`](tech-route-2026.md) 与 [`ADR-006`](adr/006-stable-asr-core.md)。
> **最后更新**：2026-10-18
> **当前阶段**：P1 稳定核心 + 0.6B/1.7B smoke 通过；等 3 集素材做 A/B
> **总目标**：把音频链路从 `WebRTC VAD → kotoba Whisper → 时间重叠匹配` 迁移到 `Qwen3-ASR → Qwen3-ForcedAligner → 全局文本对齐`；默认行为在 P2 验收前保持不变。

## 0. 当前一句话

已经落地 **稳定 ASR 核心**（契约/注册器/路由/评测/CLI）和 **Qwen3/Whisper 两个适配器**；新增模型只需在 `lib/asr/adapters/` 放一个文件，不改 core。下一步是用真实 Qwen3 模型跑 3 集 A/B，然后进 P2 的 cue 对齐/修复。

## 1. 状态总览

| 阶段 | 内容 | 状态 | 验收 |
|---|---|---|---|
| P0 | 评测金标清单 + 基线指标 | 🟡 代码就绪，待真实素材 | `eval/manifest.json` 填 12 集；旧管线 CER/边界可复现 |
| P1 | 稳定核心 + Qwen3/Whisper 适配 + 评测/CLI | 🟡 smoke 通过，待 3 集 A/B | 3 集跑通；与旧管线 A/B 有记录；默认不变 |
| P2 | Qwen3-FA + cue mapper + align_repair | ⬜ 未开始 | 边界 P50 ≤100ms / P90 ≤300ms |
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
- `dev/` 目前大多被 `.gitignore` 忽略（仅 `_README`/`maintenance` 跟踪）；
  需要进 git 的开发文档要显式 `git add -f`，或迁到 `docs/`。

## 5. 进行中 / 阻塞

- ✅ 0.6B + 1.7B + aligner 已下载；两种模型 smoke test 均通过（§4）。
- ⬜ 真实 3 集素材（视频+参考字幕）不在仓库；需要用户提供路径填 `eval/manifest.json`。
- ⛔ 无（当前没有硬阻塞；HF 直连被墙，已改用 ModelScope）。

## 6. 下一步（按顺序）

1. **P1-10 3 集 A/B**：填 `eval/manifest.json`，跑 `--engine whisper` 与 `--engine qwen3`，把 CER/边界/RTF 填进第 4 节。
2. **P1-11 性能记录**（可选）：同一 30s 音频分别用 0.6B / 1.7B，记录显存峰值、RTF、首 token 延迟；决定默认模型。
3. **P2 入口**：
   - `lib/alignment/base.py`：`Aligner` 契约（已在 `lib/asr/base.py` 有协议，可复用/搬移）；
   - `lib/alignment/qwen3_fa.py`：调用 worker `align`（或直接用 `qwen3_fa` adapter）；
   - `lib/alignment/cue_mapper.py`：时间粗锚点 + SequenceMatcher/带状 DP；
   - `fix/align_repair.py`：只依赖 `ASRResult`，不依赖 Qwen3；
   - `run_all.py --asr qwen3` 接 `fix_by_asr()`（P2，不在 P1 改默认）。
4. **P3**：`lib/audio/vad.py` + FireRedVAD；VAD 只作提示。

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
| `tests/test_asr_*.py` / `test_qwen3_*.py` | 测试 | ✅ |
| `lib/config.py` | 新配置项 | ✅ |
| `run_all.py` / `fix_orchestrator.py` | P2 接入 | ⬜ |

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

# 已通过的 smoke test
.venv-qwen\Scripts\python.exe scripts\asr\qwen3_transcribe.py `
  --audio temp\smoke\ja_sample.wav --asr models\Qwen3-ASR-0.6B `
  --aligner models\Qwen3-ForcedAligner-0.6B --timestamps `
  --output temp\smoke\ja_sample.json --srt temp\smoke\ja_sample.srt
```

## 9. 决策 / 风险

- **默认不变**：`ASR_ENGINE=whisper`；P2 验收后才切默认。
- **不伪造置信度**：P1 只出文本/token 时间；P2 用对齐一致度 + 双引擎。
- **进程隔离**：主环境不 import torch/qwen-asr；worker JSON lines。
- **Windows**：vLLM 不支持；走 Transformers 后端 + SDPA。
- **模型下载**：HF 直连被墙；用 ModelScope（已验证 200）/ 镜像。
- **已有测试**：全量 3 个既存失败（OP/ED 边界断言 95/120 vs 实际 180；`MockWhisperResult` 缺 `cues_to_clear`），与本路线无关；另开任务处理。
- **续跑原则**：先看第 1 节状态、第 4 节验证、第 6 节下一步；不要重写核心，只加 adapter。
