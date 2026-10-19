# Skill 维护规则

## 铁律

1. **修改 SRT 前必须 git commit 备份。** Pipeline 直接覆写 SRT 文件，不可逆。
   每次跑 `run_all.py`（Phase 2+3）之前，先在**项目目录**执行：
   ```bash
   git add -A && git commit -m "备份：pipeline前 — $(date +%Y-%m-%d)"
   ```
   如果项目目录不是 git repo → 先 `git init` + 初始 commit（所有文件）。

2. **修改 skill 文件前必须 git commit 备份。** 涉及 SKILL.md、`references/`、`dev/`、`scripts/` 的改动。
   改动前在 **skill 目录** 执行：
   ```bash
   git add -A && git commit -m "skill备份：<改动前>"
   ```

3. 以上两条是硬性要求，无论项目大小、改动规模，都不可省略。
   项目目录和 skill 目录各一份 git 历史，分别管理。

## 开发者模式 — Claude 的行为要求

当 CLAUDE.md 或 skill 上下文中出现「开发者模式」标记时，
Claude 在**每次破坏性操作**（修改 SRT、skill 文件、脚本）之前必须：

1. 检查目标目录是否为 git repo（`git status`）
2. 不是 → `git init` + `git add -A` + `git commit -m "初始化：项目基线"`
3. 是 → `git add -A` + `git commit -m "备份：<操作说明>"`
4. 改动完成后 → 再次 commit 备份

> 这不是可选项。如果跳过 git 备份直接修改 SRT/skill 文件，就是违反了铁律。
> Claude 必须主动提示、主动执行，不需要等待用户提醒。
