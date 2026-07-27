# Subtitle Proofread — Claude Code Skill

[![Claude Code](https://img.shields.io/badge/Claude%20Code-Skill-6C4DFF)](https://claude.com/claude-code)
[![Python](https://img.shields.io/badge/Python-3.12+-blue)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green)](./LICENSE)

让 AI 用 3 阶段流水线自动校对字幕：**扫描乱码 → Whisper ASR 修复 → 专有名词统一 + 交付**。

主要用途：看缺乏中文字幕的冷门资源时，从视频直接 Whisper 生成日文字幕 → AI 翻译 → 校对优化，完整产出可读的中文字幕。也可接受已有的机翻/听译字幕作为输入进行后处理。它不是专业字幕软件——没有时间轴编辑、翻译记忆、协作功能——但覆盖了从零到可读字幕的核心链路。

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

## 核心能力

### 翻译（translate_srt.py）

日→中批量翻译，OpenAI 兼容 API。核心特性：

- **集内并行**：所有 batch 同时发出（ThreadPoolExecutor），上下文用日文原文（预计算，零依赖），193 集约 10 分钟
- **专名一致**：`--mappings noun_mappings.json` 预替换 ja→zh 专名，翻译前统一译法
- **集数过滤**：`--episodes/-e`（范围）、`--start-from`（从第 N 集开始），照搬 `run_all.py` 逻辑
- **OP/ED 控制**：`--skip-oped` 跳过片头片尾检测（无 OP/ED 的作品）
- **模型选择**：`LLM_MODEL` env 或 `--model` CLI 覆盖

```bash
# 全量翻译（带专名映射，跳过 OP/ED）
python translate_srt.py --input-dir 日文源/ --output-dir 中文/ \
  --mappings noun_mappings.json --skip-oped

# 只翻译指定范围
python translate_srt.py --input-dir 日文源/ --output-dir 中文/ \
  --mappings noun_mappings.json -e EP076-EP193
```

### 从视频生成字幕（whisper_batch_transcribe.py）

无需已有字幕，直接从视频 Whisper 转录生成 SRT：

```bash
python whisper_batch_transcribe.py --video-dir "视频/" --output-dir "字幕/" --lang ja
```

> 视频匹配使用数字边界正则 `(?<!\d)N(?!\d)`，避免哈希子串误匹配（如 "192" 匹配 "[C319227A]"）。

### 切段修复（whisper_spot_fix.py）🆕

全片 Whisper 在多人争吵/对话密集场景会把多句合并成一条长 cue，VAD 失准导致乱码。`whisper_spot_fix.py` 对指定时间轴切段单独重跑 Whisper + 翻译，输出干净参考：

```bash
# 单段
python whisper_spot_fix.py EP001 --start 24:35 --end 24:44

# 多段
python whisper_spot_fix.py EP042 --spots "12:10-12:18,18:30-18:42"

# 仅日文（不调翻译 API）
python whisper_spot_fix.py EP001 --start 24:35 --end 24:44 --no-translate
```

> 原理：短音频窗口让 Whisper 内部 VAD 分段更准确，避免全片模式下长 cue 合并。
> 输出 JA（Whisper 干净日文）+ ZH（LLM 翻译）。

### Whisper 幻觉重复

Whisper 在音乐/噪声段会产生幻觉重复（连续多条 cue 文本高度相似）。切片重跑方案已验证无效（`fix_repeated_cues.py` 已弃用）。正确做法是在翻译阶段由 LLM 根据上下文判断。

### 专名审查（Phase B）

翻译前自动扫描词频 → 生成专名表 → AI 审查填写 ja→zh 映射 → 翻译时自动替换：

```bash
# 扫描 + 生成词表
python unified_scanner.py --target-dir 日文源/ --build-glossary --project-lang ja
# 生成映射模板
python build_glossary.py --findings findings.json --mappings-output noun_mappings.json
# 🤖 AI 审查 → 填写 noun_mappings.json → 翻译时 --mappings 注入
```

支持 AI 预搜索专名（`--ai-nouns`）、自动清理普通词（`auto_clean_glossary.py`）、预分类（`auto_classify.py`）。

---

## 实际效果

用 193 集日语动画（1963 版《铁腕阿童木》）测试了完整流程：

| 指标 | 数值 |
|------|------|
| 视频 → 日文字幕 | 193 集，53,079 cues（Whisper kotoba-v2.0-q5_0） |
| 专名映射 | 199 条 ja→zh（基于官方中文标题 + 萌娘百科 + Wikipedia 验证） |
| 日→中翻译 | 193 集全量，集内并行 ~10 分钟 |
| 翻译质量 | 零 `[???]` 自动产出，~2 行/集假名残留（专名未映射为主） |

---

## 为什么是 Skill 而不是纯脚本

纯脚本能做机械化的事（扫描、Whisper 调用、批量替换），但做不了需要判断的事：

| ✅ 脚本做 | 🤖 AI 做 |
|----------|---------|
| 扫描字幕文件，检测乱码字符 | 判断 Whisper 输出是不是合理的日语 |
| VAD + Whisper 重转录 | 补全拉丁污染片段 |
| Jamdict 查词典 | 决定一个词是专名还是普通词 |
| 批量替换 | 审查 199 条专名词表 + 确定中文译名 |

Claude 填补了脚本够不到的 gap — 负责所有需要理解和判断的决策。

---

## 项目结构

```
├── SKILL.md                  ← AI 入口（skill 加载时读取）
├── README.md                 ← GitHub 首页（你在这里）
├── references/               ← AI 参考文档
│   ├── interventions.md      ←   AI 介入判断规则
│   ├── first-run.md          ←   初始化向导
│   ├── translation.md        ←   翻译工具完整参数
│   ├── batch-review.md       ←   大规模专名审查
│   ├── workflows.md          ←   典型工作流场景
│   └── architecture.md       ←   脚本架构与数据流
├── scripts/                  ← Python 工具链
│   ├── run_all.py            ←   流水线编排器
│   ├── translate_srt.py      ←   日→中批量翻译（集内并行）
│   ├── whisper_batch_transcribe.py ← 视频→SRT 批量转录
│   ├── whisper_spot_fix.py   ←   🆕 切段 Whisper + 翻译修复
│   ├── scan/                 ←   Phase 1：乱码扫描 + 词频
│   ├── fix/                  ←   Phase 2：Whisper + 分类
│   ├── nouns/                ←   Phase 3：专名 + 词典
│   ├── apply/                ←   修复应用
│   └── lib/                  ←   共享库（SRT/ASS、Whisper 后端）
├── templates/                ← 项目配置模板
└── tests/                    ← 测试
```

> 🤖 **AI 注意**：入口是 `SKILL.md`，不是这个文件。从 SKILL.md 的「首次使用？」段开始执行。

## 支持的语言

- **日语** (ja) — 完整：乱码检测 + Jamdict 词典 + 专名分类 + 日→中翻译
- **中文** (zh) — 完整：乱码检测 + 繁简映射 + 拼音检测
- **俄语** (ru) — 翻译支持（ru→zh）
- **其他** — 翻译支持（任意→zh），无词典辅助

> **适用场景**：Whisper 视频→日文字幕 + AI 日→中翻译 + 专名统一校对，完整覆盖从零到可读字幕的全链路。不适合需要精细时间轴编辑、翻译记忆库或多人协作的场景（请用 Aegisub/Subtitle Edit）。

## 许可与数据来源

- **JMdict/JMnedict**：通过 [`jamdict`](https://pypi.org/project/jamdict/) Python 库查询，遵循 [CC-BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)。不捆绑或分发词典文件。
- **Whisper**：OpenAI 发布（MIT），[kotoba-whisper](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0) 为社区日语微调版。

## 参考

- [Claude Code Skills 文档](https://docs.claude.com/en/claude-code/skills)
- [JMdict 项目](https://www.edrdg.org/wiki/index.php/JMdict-EDICT_Dictionary_Project)
- [kotoba-whisper](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0)
