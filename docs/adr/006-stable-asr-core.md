# ADR-006: ASR 稳定核心 + 插件适配器

- **Date**: 2026-10-18
- **Status**: 🟡 Proposed
- **Context**: 模型迭代很快（Qwen3 → 下一代可能 6 个月后出现）。如果每次换模型都改 router、CLI、评测脚本和修复管线，代码会反复重写，回归风险高。需要把「稳定的编排/契约」和「易变的模型调用」分开。

## Decision

采用 **stable core + adapter plugin**：

| 层 | 文件 | 变更频率 | 规则 |
|---|---|---|---|
| 契约 | `scripts/lib/asr/base.py` | 极低 | `ASRResult` / `Token` / `Segment` / `ASRConfig` / `BackendInfo` / `ASRBackend` / `Aligner`；只做加法，JSON 带 `schema_version` |
| 注册器 | `scripts/lib/asr/registry.py` | 极低 | 自动发现 `lib/asr/adapters/*.py`；新增模型不改这里 |
| 路由 | `scripts/lib/asr/router.py` | 极低 | 只依赖 registry + 契约；不 import 任何模型 |
| 适配器 | `scripts/lib/asr/adapters/<name>.py` | 高 | 每个模型一个文件；暴露 `NAME/KIND/INFO/build(config)`；实现 `ASRBackend` |
| Worker | `scripts/asr/qwen3_worker.py` | 中 | 引擎自己的进程；协议固定 `ping/load/transcribe/align/shutdown` |
| CLI/评测 | `scripts/asr/qwen3_transcribe.py`、`scripts/eval/asr_eval.py` | 低 | `--engine` 从 registry 动态取值，不写死模型名 |

**新增模型的步骤**（不改 core）：

1. 复制 `templates/asr_adapter.py` 到 `scripts/lib/asr/adapters/<name>.py`；
2. 填 `NAME/KIND/INFO/build`，实现 `available()/transcribe()`；
3. 需要 token 时间戳的，另写 `KIND='aligner'` 的适配器，或在后端暴露 `align()`；
4. 加一个不下载模型的契约测试（`validate_backend` + fake 调用）；
5. `python scripts/asr/qwen3_transcribe.py --list-engines` 应自动看到新引擎。

**稳定核心的改动条件**：

- 需要新增能力（如 diarization、streaming）→ 先加可选字段/协议，不破坏现有 adapter；
- `ASRResult.schema_version` 增加，旧 adapter 仍可工作；
- 不为单个模型在 router/CLI 里加 `if name == ...`。模型特有参数走 `ASRConfig.options` 或 adapter 自己的 `build(**kwargs)`。

## Consequences

- ✅ 新模型 = 一个新文件 + 配置，不再重写管线
- ✅ 旧模型可并存、A/B、fallback；默认行为由配置决定
- ✅ 单元测试不依赖模型下载；契约测试保证新 adapter 不跑偏
- ✅ 评测脚本对引擎无感，同一 manifest 可跑任意 adapter
- ⚠️ 需要维护 `ASRConfig` 的通用字段；模型特有参数放 `options`
- ⚠️ 自动发现会 import 所有 adapter 模块；adapter 顶层不得 import torch/大依赖
- ⚠️ `--engine` 从写死 choices 改为 registry 校验；错误信息需列可用引擎

## Alternatives rejected

| 方案 | 原因 |
|---|---|
| 在 router 里 `if engine == 'qwen3'` / `elif whisper` | 每来一个模型改一次 core，违反目标 |
| 每个模型一套独立 CLI/评测 | 重复代码，A/B 不可比 |
| 让模型直接返回各自 JSON | 下游（修复/评测/报告）要写 N 套解析 |
| 大而全的框架（LangChain 式） | 过度抽象；本项目只需要 4 个契约 |

## Follow-up

- 把 P2 的 `align_repair.py` 也依赖 `ASRResult`/`ASRBackend`，不依赖 Qwen3。
- 新增 aligner adapter 时复用 `Aligner` 契约与 registry `kind='aligner'`。
- 在 CI 里跑 `tests/test_asr_registry.py`，新 adapter 必须通过契约测试。

详细方案见 [`docs/tech-route-2026.md`](../tech-route-2026.md)。
