# 仓库隐私与 skill 管理调研（2026-10-19）

## 1. 有没有“仓库隐私”类 skill？

**当前 catalog 里没有专门做仓库隐私/密钥审计的 skill。** 55 个已安装 skill 里，
关键词命中的三个都不是这个用途：

| skill | 实际用途 | 与仓库隐私的关系 |
|---|---|---|
| `subtitle-proofread` | 本 skill，文档里提到隐私策略 | 只是声明，不扫描 |
| `wayfinder` | 大任务拆决策票 | 无关 |
| `wizard` | 生成引导式 bash 向导，会写 `.env` / GitHub secrets | 产生密钥，不审计仓库 |

最接近的现成能力：

- `git-guardrails-claude-code`：装 Claude Code hooks，拦截 `push --force`、
  `reset --hard`、`clean` 等危险 git 操作——防误操作，不防泄露。
- `setup-pre-commit`：装 husky/lint-staged；可以在这个钩子里接 gitleaks。
- 外部工具：`skill-auditor`（安装前审计 skill/MCP 的安全）、
  `agent-skill-safety-auditor`（离线权限审计）、`gitleaks`（密钥扫描，含历史）。

结论：**没有能直接拿来管“仓库里不许出现机器路径/密钥”的 skill**；
需要一条自己的最小检查，已实现为 `scripts/harness/privacy_check.py`。

## 2. 本仓库实际踩到的两类泄露

1. **机器路径**：`sk_01_dev/README.md`、`docs/fireredvad-status.md` 里写死了
   本机绝对路径（`%USERPROFILE%` / 仓库盘符；已改为占位符与同步副本说明）。
2. **本机配置**：harness 副本的 `local.json` 含 API key。仓库 `.gitignore`
   忽略它，`sync_skill.py` 不复制它，`init_local.py --check` 输出脱敏。

当前防线：

| 层 | 机制 |
|---|---|
| 文档 | `tests/test_docs_paths.py`：同步文档禁止盘符路径、禁止 `sk_01_dev/` 链接、md 链接必须存在 |
| 仓库 | `scripts/harness/privacy_check.py`：扫描 git 跟踪文件里的 key 模式与 home 路径；`tests/test_privacy.py` 默认跑 |
| 同步 | `sync_skill.py` 只复制运行时白名单，排除 `local.json`、`models/`、`.venv-qwen` |
| 初始化 | `init_local.py` 写 `local.json`，`--check` 脱敏打印 |

历史泄露没扫描（`gitleaks detect --source . --log-opts="--all"` 可在发布前跑一次）。

## 3. skills-manager 评估

仓库：<https://github.com/xingkongliang/skills-manager>（Tauri 桌面应用 + CLI，MIT）。

**它是什么**：跨 50+ agent 的 skill 库/部署管理器。中心库默认
`~/.skills-manager`；从 Git、本地目录、zip、marketplace 导入；
按 agent 用 symlink 或 copy 部署；有 presets、Project Workspace、更新跟踪、
Git 备份/多设备同步、SQLite 元数据、`--json` CLI，以及让 agent 自己调用的
`manage-skills` skill。

**与我们流程的关系**：

| 需求 | skills-manager | 我们现在的 `sync_skill.py` |
|---|---|---|
| 仓库 → harness 构建 | 不负责构建；把 repo 当 skill 导入 | 直接复制运行时白名单 |
| 保留 harness 的 `local.json` | 不理解这个文件 | 明确保留 |
| 部署方式 | 默认 symlink，可切 copy | 总是 copy（我们要求真实副本） |
| 多 skill/多 agent | 强项 | 单 skill |
| 备份 | Git 私有库 + 快照 | 仓库自身 git |

**风险点**：

1. 它默认/可切 symlink，正好是我们刚拆掉的模式；必须确认 copy 模式。
2. 中心库会再存一份 skill 内容；我们的仓库才是源，容易出现“两份内容谁新”的问题。
3. 它宣称“密钥/机器配置不离开本机”，但 `local.json` 在 skill 目录内，
   备份逻辑是否把它当普通文件复制没有文档保证——**不要把 API key 放在被管理的
   skill 目录里**。

**建议（当前阶段）**：

- 继续用 `sync_skill.py` 作为唯一构建/部署路径；
- 如果要用 skills-manager，只把它当**库管理/备份/多 agent 分发**层：
  - 部署方式设为 copy；
  - 新增自定义 agent 路径 `~/.agents/skills`；
  - `local.json` 移出 skill 目录（例如 `%APPDATA%\subtitle-proofread\local.json`），
    配置加载器优先读它；
  - 不让它管理本 skill 的 `SKILL.md` 以外文件（避免覆盖运行时代码）。
- 多 skill 真正变多以后，再考虑把 `~/.agents/skills` 作为它的 Linked Workspace。

## 4. 参考

- skills-manager: <https://github.com/xingkongliang/skills-manager>
- skill-auditor: <https://github.com/Plastikelectrik/skill-auditor>
- agent-skill-safety-auditor: <https://github.com/alptugharun/agent-skill-safety-auditor>
- gitleaks 用法: <https://stevekinney.com/courses/self-testing-ai-agents/secret-scanning-with-gitleaks>
