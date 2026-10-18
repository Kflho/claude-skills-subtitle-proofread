# Developer Documentation

此目录包含 skill 开发者/维护者文档。

**仅在用户确认自己是 subtitle-proofread skill 的开发者或维护者时才加载。**
普通用户（只是运行校对 pipeline）不需要这些文件。

如果你只是运行字幕校对，请停止阅读此目录。

## 索引

| 文件 | 内容 |
|---|---|
| [`technical-details.md`](technical-details.md) | 脚本总览、数据流、Whisper/VAD、扫描、置信度、专名、报告、ASS、VAD 统一架构、行为分级 |
| [`whisper-pipeline.md`](whisper-pipeline.md) | Whisper 批量策略、Tier 升级、批量转录时间戳自愈、切段修复、幻觉重复 |
| [`noun-extraction.md`](noun-extraction.md) | AI 驱动专名提取：三步闭环、sidecar 续跑、chunk 失败、`_build_repl` 双写防护 |
| [`translation-pipeline.md`](translation-pipeline.md) | 集内并行、LLM 模型名/token 陷阱、验证、百度 legacy |
| [`troubleshooting.md`](troubleshooting.md) | 编码探测、LLM 空 content、glossary 回显、OP/ED 误伤、ASS 写出、Whisper 时间戳 |
| [`maintenance.md`](maintenance.md) | 维护铁律：改 SRT/skill 前 git 备份；开发者模式行为要求 |
| [`iteration-log.md`](iteration-log.md) | 迭代记录 |

## 边界

- `dev/` 是本地开发文档；`SKILL.md` 不链接它们（skill 运行时不需要）。
- 目前 `.gitignore` 忽略 `dev/`：`_README.md` / `maintenance.md` 是已跟踪的例外，
  其余文件要进 git 需显式 `git add -f`。
- `references/` 是随 skill 分发的 AI 参考文档（命令参考、工作流、初始化），
  不要往 `references/` 塞开发者实现细节。
- `docs/` 是**受 git 跟踪**的开发者文档（ADR、技术路线、进度），与 `dev/` 的区别：
  `docs/` 可随仓库评审/追溯，`dev/` 是本地工作笔记。

## 写作规则

- `SKILL.md` 只放运行时需要的用户/AI 操作说明；实现细节、根因、算法、内部函数
  放 `dev/`。
- `references/` 只放随 skill 分发的运行时参考（命令、工作流、初始化），
  不放开发者实现说明。
