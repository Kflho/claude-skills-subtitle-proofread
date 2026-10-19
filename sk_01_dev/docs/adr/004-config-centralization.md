# ADR-004: 配置集中化 — 散落的环境变量 → 单一配置模块

- **Date**: 2026-07-24
- **Status**: ✅ Accepted
- **Context**: 同一环境变量（如 `WHISPER_CLI`）在 5+ 处独立读取。`LLM_API_KEY` 的 `POLISH_API_KEY` fallback 链在 3 个文件中重复。硬编码默认值（`'ja'`、`'AI审查后'`、`600`、`0.4`、`2.0`）散落各处。无集中可检查的配置点。

## Decision

创建 `lib/config.py` 作为**所有配置的单一来源**。

```python
# lib/config.py — 模块级常量，导入时一次性求值
WHISPER_CLI = os.environ.get('WHISPER_CLI', '')
LLM_API_KEY = os.environ.get('LLM_API_KEY', '') or os.environ.get('POLISH_API_KEY', '')
LLM_MODEL_DEFAULT = 'deepseek-chat'
DEFAULT_INPUT_DIR = 'AI审查后'
# ... 等 20+ 个配置项
```

所有其他模块从此导入，不再直接 `os.environ.get()`。

`get_input_dir()` 是唯一的函数（非常量）— 因为 `INPUT_DIR` 在运行时被 `run_all.py` 动态设置。

附随修复：`OP_BOUNDARY_SEC` 在 3 个文件中重复定义 → 统一从 `whisper_utils` 导入。

## Consequences

- ✅ 12 个文件 ~45 处 `os.environ.get()` 替换为 config 常量
- ✅ 新增配置项只需在 config.py 加一行
- ✅ 所有配置可在一处查看/审计
- ✅ 消除了重复的 `POLISH_*` fallback 链（3 处→1 处）
- ✅ 不需要新依赖（纯 Python `os.environ`）
- ⚠️ 模块级常量在导入时求值 — 如果 `import lib.config` 之后修改 `os.environ`，常量不会更新（但对于本项目的使用方式这不构成问题，因为环境变量在脚本启动前已设置）
