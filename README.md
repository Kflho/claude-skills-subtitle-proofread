# Subtitle Proofread — Claude Code Skill

<!-- AI: 此文件是 GitHub 首页，不是 skill 入口。请读 SKILL.md。 -->

[![Claude Code](https://img.shields.io/badge/Claude%20Code-Skill-6C4DFF)](https://claude.com/claude-code)
[![Python](https://img.shields.io/badge/Python-3.12+-blue)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green)](./LICENSE)

让 AI 用 3 阶段流水线自动校对字幕：**扫描乱码 → Whisper ASR 修复 → 专有名词统一 + 交付**。

主要用途：看缺乏中文字幕的冷门资源时，爬取视频网站的自动生成字幕或外网其他语言字幕作为来源，机翻后用这个工具对字幕进行质量优化。它不是专业字幕翻译软件——没有时间轴编辑、没有翻译记忆、没有协作功能。就是一个字幕后处理脚本：把粗糙的机翻/听译字幕变得可读。

---

## 怎么用

```bash
# 1. 克隆到 Claude Code skills 目录
git clone https://github.com/Kflho/claude-skills-subtitle-proofread.git \
  ~/.claude/skills/subtitle-proofread

# 2. 在 Claude Code 对话中输入
/subtitle-proofread

# 3. 跟随内置初始化向导，告诉 AI 你的字幕和视频在哪
#    然后 AI 自动跑完整套校对流程。
```

**就这三步。** 没有配置文件要手写，没有参数要记。

### 内置初始化 —— 对话式配置

首次运行 `/subtitle-proofread` 时，skill 自动执行初始化向导，引导你完成：

1. 告知原理 — AI 会做什么、改什么
2. 收集路径 — 字幕在哪？视频在哪？参考字幕（可选）？
3. 检测 Whisper — 自动扫描可用的 Whisper 后端，你选
4. 安装依赖 — Python 3.12+、jamdict 日语词典（自动 `pip install`）
5. 生成配置 — 写入项目 `CLAUDE.md`，下次直接用

整个过程是对话式的，AI 问，你答，不用手写任何配置文件。

> 想重新配置？删掉项目 `CLAUDE.md` 中的 `SKILL INITIALIZED: true` 即可。

### 日常用法

```bash
/subtitle-proofread                  # 校对全部
/subtitle-proofread --limit 5        # 前 5 集
/subtitle-proofread -e EP027-EP050   # 指定范围
/subtitle-proofread --skip-whisper   # 跳过音频（无视频时）
/subtitle-proofread --dry-run        # 预览，不改文件
```

---

## 它做什么

```
你的字幕 ──→ Phase 1 扫描 ──→ Phase 2 修复 ──→ Phase 3 统一 ──→ 干净字幕 + 审查报告
 (SRT/ASS)    (只读，不改文件)   (Whisper + AI)    (名词 + OP/ED)
```

| 阶段 | 做什么 | AI 管什么 |
|------|--------|-----------|
| **Phase 1** Scan | 扫描全部字幕，检测乱码 + 统计词频 + 生成术语表 | 术语表 borderline 审查（≤20条） |
| **Phase 2** Triage | VAD → Whisper → 自动分类（auto-keep / auto-cut / AI补全） | 碎片补全（🤖 配对判断） |
| **Phase 3** Unify | OP/ED 一致性、专名变体检测、自动分类、批量应用 | 专名判断（迭代收敛：12→6→3→0） |

**资源驱动，缺了也能跑**：有视频+Whisper → 完整修复；只有字幕 → 跳过音频步骤，扫描+统一专名照样跑。

---

## 实际效果

用 193 集日语动画测试了完整流程：

| 指标 | 数值 |
|------|------|
| 总 cue 数 | 78,259 |
| 检测到乱码 | 3,673（107 集） |
| OP/ED 自动清理 | 171 条 |
| 专名统一 | 76 条规则，覆盖 59 集 |
| 最终待处理 | **0** ⬜ |

---

## 为什么是 Skill 而不是纯脚本

纯脚本能做机械化的事（扫描、Whisper 调用、批量替换），但做不了需要判断的事：

| ✅ 脚本做 | 🤖 AI 做 |
|----------|---------|
| 扫描字幕文件，检测乱码字符 | 判断 Whisper 输出是不是合理的日语 |
| VAD + Whisper 重转录 | 补全拉丁污染片段 |
| Jamdict 查词典 | 决定一个词是专名还是普通词 |
| 批量替换 | 判断人名变体（`ヒゲオヤジ` vs `ヒゲおやじ`） |

Claude 填补了脚本够不到的 gap — 负责所有需要理解和判断的决策。

---

## 项目结构

```
├── SKILL.md                  ← AI 入口（skill 加载时读取）
├── README.md                 ← GitHub 首页（你在这里）
├── references/               ← AI 参考文档
│   ├── interventions.md      ←   AI 介入判断规则
│   ├── first-run.md             ←   初始化向导
│   ├── phase1-scan.md        ←   Phase 1 扫描命令
│   ├── phase2-triage.md      ←   Phase 2 Whisper 修复命令
│   ├── phase3-unify.md       ←   Phase 3 专名统一 + 交付命令
│   ├── full-mode.md          ←   参考字幕完整工作流
│   └── architecture.md       ←   脚本架构与数据流
├── scripts/                  ← Python 工具链（~13,500 行）
│   ├── run_all.py            ←   流水线编排器
│   ├── scan/                 ←   Phase 1：乱码扫描
│   ├── fix/                  ←   Phase 2：Whisper + 分类
│   ├── nouns/                ←   Phase 3：专名 + 词典
│   ├── apply/                ←   修复应用
│   └── lib/                  ←   共享库（SRT/ASS、Whisper 后端）
├── templates/                ← 项目配置模板
└── tests/                    ← 测试
```

> 🤖 **AI 注意**：入口是 `SKILL.md`，不是这个文件。从 SKILL.md 的「首次使用？」段开始执行。

## 支持的语言

- **日语** (ja) — 完整：乱码检测 + Jamdict 词典 + 专名分类
- **中文** (zh) — 完整：乱码检测 + 繁简映射 + 拼音检测
- **英文** (en) — 基础

> **不适用场景**：不替代专业字幕软件（无时间轴编辑/翻译记忆/协作）、不做翻译本身、不做从零开始的字幕制作。

## 许可与数据来源

- **JMdict/JMnedict**：通过 [`jamdict`](https://pypi.org/project/jamdict/) Python 库查询，遵循 [CC-BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)。不捆绑或分发词典文件。
- **Whisper**：OpenAI 发布（MIT），[kotoba-whisper](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0) 为社区日语微调版。

## 参考

- [Claude Code Skills 文档](https://docs.claude.com/en/claude-code/skills)
- [JMdict 项目](https://www.edrdg.org/wiki/index.php/JMdict-EDICT_Dictionary_Project)
- [kotoba-whisper](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0)
