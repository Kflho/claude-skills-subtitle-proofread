# sk_01 开发者文档（已并入主仓库）

这是 `subtitle-proofread` skill 的开发者文档 / ADR / 研究 / 评测记录，
现在**作为主仓库的 `sk_01_dev/` 子目录跟踪**（原来的独立本地仓库已并入，历史见
`projects/sk_01_dev_backup.bundle`）。

skill 本体仍然只保留用户上手文档（`SKILL.md`、`README.md`、`references/`）；
实现细节、根因、ADR、研究记录都在这里。

## 目录

| 路径 | 内容 |
|---|---|
| `docs/` | ADR、技术路线、研究/控制实验、评测、翻译架构、删除审计 |
| `docs/translation-research-2026-10.md` | AI 翻译文献/上下文/默认启发式实验结论 |
| `CONTEXT.md` | 开发上下文、模块地图、当前测试/覆盖 |
| `dev/` | 本地维护笔记（长文、坑、维护铁律） |
| `LOCAL_ENV.md` | 本机软件/模型/API 配置（无密钥值；**被 .gitignore 忽略，只在本机**） |

## 铁律

- 不把 API key 写进任何文件；
- 本机绝对路径只写 `LOCAL_ENV.md`（被 ignore），不写进被跟踪的文档；
- 迁移/删除开发文档前先提交。

## skill 本体路径

`D:\data\local\software\programming\skill\projects\sk_01`

## DSH skill 注册

`C:\Users\54238\.agents\skills\subtitle-proofread` → skill 本体（junction）。
DSH 只扫描 `~/.agents/skills`、`~/.dsh/skills`、项目的 `.agents/skills`/`.dsh/skills`，
不扫描 `~/.claude/skills`；这是之前 catalog 看不到的原因。
