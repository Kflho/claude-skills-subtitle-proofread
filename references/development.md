# 开发与维护本 skill

改这个仓库时适用；跑字幕任务不需要读。

## 1 改动先分三段

| 段 | 改什么 | 怎么验收 |
|---|---|---|
| **能力** | 新脚本、新入口、新闸门 | `tests/` 加测试 + 一条真实输入跑通 |
| **行为** | 原本也能做，现在按流程做（先看什么、按什么顺序、调谁） | 场景验收：给输入看 agent 有没有走进对应路由 |
| **规范** | 阈值、判据、命名、术语 | 能脚本判的一律写成检查（`tests/` 或 `capabilities.py`） |

说不清落在哪一段，说明还没想清楚要改什么；只写规范、没有能力或行为的改动，通常是加了一条记不住的禁令。

## 2 改之前先查入口

按 `SKILL.md`「常用入口」表找现成工具。缺参数 → 给现成工具加透传 + 测试；缺功能 → 先看
[workflows.md](workflows.md) 的场景表与「工具速查」，仍然缺再报告用户，不直接写新脚本。

## 3 能力清单

根目录 `capabilities.json` 登记每个能力（层 / 入口 / 文档 / 触发词），`scripts/capabilities.py` 校验：

```bash
python scripts/capabilities.py --report   # 按层列出能力、覆盖率与失效项
python scripts/capabilities.py --check    # 入口、文档、触发词任一缺失 → 退出码 2
```

新增能力时同步三处：脚本、`references/` 里的说明、`capabilities.json`；`--check` 会抓漏。
模型与算法本身的能力测试另记开发记录（`capability-tests`），和这里的「能力入口清单」不是一回事。

## 4 测试与文档

- 测试按文档分目录：`tests/<段>/` 对应 `references/<段>.md`，`tests/shared/` 统管 SKILL 与配置；
- 全量：`python -m pytest tests -q`；只改文档跑 `tests/shared/test_pipeline_docs.py`
  与 `tests/shared/test_capabilities.py`；
- `SKILL.md` 只放常驻层（≤150 行：判据、阶段路由、能力边界、常用入口）。细节下沉 `references/`，
  每个指针写清触发分支；不写版本记录、测试计数、踩坑复盘（放开发记录或 CHANGELOG）；
- 设计记录（含放弃的方案）放 `references/design/`，废弃文档标 legacy；
- 静态激活检查：`python <kf-personal-development>/scripts/check_activation.py --skill <本目录>`，
  0 error 才提交（它查 description 触发词、路由链接、孤儿文档）。

## 5 同步、隐私与发布

- 本机路径与密钥只写 `local.json`（gitignored）；`projects_registry.json` /
  `projects_index.json` 同样不入库；
- 提交前跑隐私扫描（`kf-personal-service` 的 `scripts/privacy/scan.py`）与 `check_files`；
- 发布走 `kf-personal-development` 的 `scripts/repo/release.py verify`，不手推；
- junction 模式改完立即生效：`python scripts/harness/sync_skill.py --link`；
- 跨机同时开发先定写方，通信与锁走 `kf-personal-service` 的 relay。
