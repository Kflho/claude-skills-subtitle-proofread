# Skill 维护规则

## 铁律

1. 修改 SRT 前必须 git commit 备份。
2. 修改 skill 文件（SKILL.md、user/、scripts/ 等）前必须 git commit 备份。

## Skill 迭代改进

**目标**：每次 `/clear` 后加载 skill + CLAUDE.md，上来就能跑通，无需调试、搜脚本、手动传参。

**流程** — 每次 pipeline 跑完后执行：

1. **检查是否"上来就跑通"** — 本次运行中是否出现了以下任何问题？
   - 参数缺失导致静默失败（如 `--video-dir` 未传）
   - 报错信息不明确，需要读脚本才能定位
   - SKILL.md 或 CLAUDE.md 缺少关键步骤/参数
   - 需要手动搜文件、grep、读源码才能继续

2. **如果出现任何问题** → 更新 SKILL.md（优先）或相关文档，消除问题根因：
   - SKILL.md：补充步骤、参数说明、故障排查、输出解读
   - CLAUDE.md：补充项目特定配置、命令模板
   - 原则：**让下次的 Claude 看到 skill 就知道怎么做**，不需要上下文记忆

3. **如果一次就跑通** → 记录确认，无需改动。

4. **两端 git commit**：
   ```bash
   cd "<skill-dir>" && git add -A && git commit -m "skill迭代: <改进内容>"
   cd "<project-root>" && git add -A && git commit -m "备份：<运行说明>"
   ```

## 运行规则

> 脚本在 skill 目录（`scripts/`），不在项目目录。
> 工作流说明见 `SKILL.md`；CLAUDE.md 只存项目特定配置。

- `--video-dir` 必须传，否则 Whisper 静默失败
- `--lang` 自动检测（从 SRT 文字系统判断 ja/zh），无需手动传
- `--apply-ai-review` 和 `--apply-checklist` 不能和 full run 一起用
