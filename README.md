# Subtitle Proofread — Claude Code Skill

[![Claude Code](https://img.shields.io/badge/Claude%20Code-Skill-6C4DFF)](https://claude.com/claude-code)
[![Python](https://img.shields.io/badge/Python-3.12+-blue)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green)](./LICENSE)

一个 Claude Code skill，让 AI 用 3 阶段流水线自动校对字幕：**扫描乱码 → Whisper ASR 修复 → 专有名词统一 + 交付**。

## ✨ 它能做什么

```
你的乱码 SRT ──→ Phase 1 扫描 ──→ Phase 2 Whisper ──→ Phase 3 统一 ──→ 干净字幕 + 审查报告
```

| 阶段 | 做什么 | AI 介入点 |
|------|--------|-----------|
| **Phase 1** Scan | 扫描全部字幕文件，检测乱码字符、重复模式、词频统计 | 术语表 borderline 审查（≤20条） |
| **Phase 2** Triage | VAD 语音检测 → Whisper 重转录 → 自动分类（auto-keep / auto-cut / AI补全） | 碎片补全（🤖 配对审查） |
| **Phase 3** Unify | OP/ED 一致性修复、专名变体检测、自动分类、批量应用 | 专名判断（迭代收敛，12→6→3→0） |

**资源驱动**：有视频+Whisper→修复乱码；有参考字幕→注入校对上下文。缺资源也能残血运行 — 跳过缺失步骤，剩余步骤照常。

## 🚀 快速开始

### 前置条件

- **Python 3.12+**
- Whisper 后端（三选一）：
  - whisper.cpp（推荐 — GGML 量化模型，GPU 加速）
  - faster-whisper（pip install，CTranslate2 引擎）
  - openai-whisper（legacy，PyTorch）
- 日语项目需要 `jamdict`（自动安装：`pip install jamdict`）

### 安装

```bash
# 1. 克隆 skill
git clone https://github.com/Kflho/claude-skills-subtitle-proofread.git \
  ~/.claude/skills/subtitle-proofread

# 2. 在 Claude Code 中激活
#     对话中输入 /subtitle-proofread
#     首次使用会自动运行初始化向导
```

### 典型工作流

```bash
# 环境变量（从项目 CLAUDE.md 获取）
export PYTHONIOENCODING=utf-8
export WHISPER_CLI='path/to/whisper-cli.exe'
export WHISPER_MODEL='path/to/model.bin'

# 运行流水线
python scripts/run_all.py \
  --video-dir "path/to/videos" \
  --limit 5
```

## 📂 项目结构

```
├── SKILL.md                  ← AI 入口（skill 加载时读取）
├── README.md                 ← GitHub 首页（你在这里）
├── scripts/
│   ├── run_all.py            ← 流水线编排器
│   ├── scan/                 ← Phase 1：乱码扫描
│   ├── fix/                  ← Phase 2：Whisper 修复 + 分类
│   ├── nouns/                ← Phase 3：专名提取 + 分类
│   ├── apply/                ← 统一修复应用
│   └── lib/                  ← 共享库（SRT/ASS 解析、Whisper 后端等）
├── user/                     ← 用户文档
│   ├── init-wizard.md        ← 首次初始化向导
│   └── run-reference.md      ← 独立命令参考
├── AI-INTERVENTIONS.md       ← AI 介入点详细规则
├── templates/                ← 项目 CLAUDE.md 模板
└── tests/                    ← 测试
```

> 🤖 **如果你是 AI**：你正在加载一个 Claude Code skill。入口是 `SKILL.md`，不是这个文件。从 SKILL.md 的「首次使用？」段开始执行。

## 🎯 支持的语言

- **日语** (ja) — 完整支持：乱码检测 + Jamdict 词典过滤 + 专名分类
- **中文** (zh) — 完整支持：乱码检测 + 繁简映射 + 拼音检测
- **英文** (en) — 基础支持

## 📄 数据来源与许可

- **JMdict/JMnedict**：日语词典数据来自 [JMdict](https://www.edrdg.org/jmdict/j_jmdict.html)，遵循 [CC-BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)。通过 `jamdict` Python 库查询，不捆绑分发。
- **Whisper**：ASR 模型由 OpenAI 发布（MIT 许可），kotoba-whisper 为社区微调版。

## 🔗 参考

- [Claude Code Skills 文档](https://docs.claude.com/en/claude-code/skills)
- [JMdict 项目](https://www.edrdg.org/wiki/index.php/JMdict-EDICT_Dictionary_Project)
- [kotoba-whisper](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0)
