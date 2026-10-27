---
name: subtitle-proofread
description: 字幕任务统一入口：调研 → 资源获取 → 转录打轴 → 校对统一 → 翻译交付。适用于：字幕校对、翻译字幕、专名/术语/译名统一、变体归一、时间轴修复、跨语言重打轴、乱码扫描、OP-ED/片头片尾/固定台词、参考字幕复用、补条与 AI 复核、SRT/ASS 批量处理、配置与项目登记、快速上手、排错。维护或开发本 skill 时同样先加载，现有工具优先。
---

# 字幕生产线

从视频或外语字幕产出可交付的中文字幕。**资源驱动**：有什么用什么——有视频走 ASR，有参考字幕走复用或重打轴；缺的资源记成缺口，其余步骤照跑。

## 何时加载与能力分层

启动只加载常驻层；选择性文档按「阶段路由」走到那一步再读一份，不预读。

| 层 | 内容 | 加载方式 |
|---|---|---|
| **常驻** | 本文件：五段流程、阶段路由、能力边界、行为分级、常用入口 | 每次会话 |
| **选择性** | `references/` 各文档按路由取一份；开发本 skill 前读 [references/development.md](references/development.md)，改实现前读 [references/design/](references/design/) 的设计记录 | 触发才读 |
| **交付前** | `python -m pytest tests -q`、`python scripts/capabilities.py --check`；发布前再跑 `eval/release_gate.py`，隐私扫描与同步走 `kf-personal-service` | 闸门统一调用 |

## 阶段路由

先定位，再进子文档；按任务或症状找，别凭记忆改字幕。

| 触发 | 读 | 什么时候用 |
|---|---|---|
| 快速上手 / 环境 | [references/quickstart.md](references/quickstart.md)、[references/setup.md](references/setup.md)、[references/first-run.md](references/first-run.md) | 刚装好环境、想先跑通 3 集 |
| 打轴 / 转录 | [references/workflows.md](references/workflows.md)、[references/subtitle.md](references/subtitle.md) | 没有字幕，要 ASR 出轴再翻译 |
| 字幕校对 / 轴不对 | [references/subtitle.md](references/subtitle.md)、[references/retime-existing-subs.md](references/retime-existing-subs.md) | 已有字幕要修文本或重打轴 |
| 跨语言重打轴 | [references/design/crosslingual-retime.md](references/design/crosslingual-retime.md) | 外语字幕 + 另一语种音轨 |
| 乱码扫描 / 碎片 | [references/phase1-scan.md](references/phase1-scan.md)、[references/phase2-triage.md](references/phase2-triage.md)、[references/phase3-unify.md](references/phase3-unify.md) | 三段式扫描修轴 |
| 专名 / 术语 / 变体归一 | [references/research.md](references/research.md)、[references/design/glossary-variants.md](references/design/glossary-variants.md) | 译名不一致、同源异译 |
| 补条 / AI 复核 | [references/design/review-ai-fill.md](references/design/review-ai-fill.md)、[references/design/asr-name-repair.md](references/design/asr-name-repair.md) | 有人声没字幕、ASR 把名字听错 |
| OP-ED / 片头片尾 / 固定台词 | [references/workflows.md](references/workflows.md)、[references/design/oped-fixed-lines.md](references/design/oped-fixed-lines.md) | 唱词乱码、片头片尾定稿 |
| 参考字幕复用 | [references/full-mode.md](references/full-mode.md)、[references/reuse-reference-subs.md](references/reuse-reference-subs.md) | 有参考字幕可借 |
| 翻译字幕 / QA | [references/translation.md](references/translation.md) | 翻译参数、QA、润色、重译判定 |
| 调研 / 梗概 / 外部资料 | [references/research.md](references/research.md)、[references/external-references.md](references/external-references.md) | 查读音、招式、时代用语、剧情梗概 |
| 配置 / 项目登记 | [references/config.md](references/config.md) | local.json、projects_registry.json |
| 排错 | [references/troubleshooting.md](references/troubleshooting.md) | 报错、结果不对、静默失败 |
| AI 介入点 | [references/interventions.md](references/interventions.md) | 每个 🤖 点怎么判、怎么问 |
| 开发 / 维护本 skill | [references/development.md](references/development.md) | 改脚本、改文档、加能力、发版 |

## 能力边界

本 skill 只产出字幕本身：调研结论、源/译字幕、时间轴、专名表、交付文件。

| 不做 | 去做 |
|---|---|
| GUI 点击（迅雷 / 浏览器）、VPN、SSH、网盘、仓库总账、笔记登记 | `kf-personal-service`：迅雷确认面板调它的 `tasks.py run thunder-download` |
| 开发规范、发布闸门、隐私扫描、跨机协作 | `kf-personal-development` |
| 申报书配图、PPT、MATLAB 数据图 | `kf-proposal-exports` |
| 字幕站爬虫自研、模型训练、说话人分离 | 不做；参考字幕只用现成结果 |

## 行为分级

每个写盘行为在 `scripts/lib/gates.py` 登记级别。判据：这一步会不会在用户没要求时改变字幕内容？会 → 至少 L1。

| 级别 | 含义 | 包含 |
|---|---|---|
| **L0** | 默认必做，只读 | 扫描 / 编码探测、VAD 台词↔人声匹配、交付校验 |
| **L1** | 默认只检测并标注，写盘要开关 | VAD 删「无人声」条（`--vad-clean-apply`）、missing 补条 |
| **L2** | 只在明确要求时执行 | OP/ED 与专名预替换、TV 复用覆写、润色、繁→简、`fixes.json` 改写 |
| **L3** | 执行前必须确认 | 覆盖已交付字幕、覆盖既有 `.bak` |

默认取安全侧：VAD 删条只出清单，`missing` 只列不插。新增写盘功能先在 `gates.py` 加 `Gate(...)`，再写实现。

## 全流程（五段）

| 段 | 目的 | 输入 | 完成判据 | 子文档 |
|---|---|---|---|---|
| **0 调研** | 先弄清「有没有现成字幕、这作品是什么」 | 片名 / 日文原名 / 链接 | 有 / 没有参考字幕都写清；背景与梗概落档；专名表初版就位 | [research.md](references/research.md) |
| **1 获取** | 把视频（和可能的字幕）拿到本地 | 链接 / 种子 | 视频进媒体库，逐文件核对完成 | [acquire.md](references/acquire.md) |
| **2 转录与打轴** | 无字幕时从音频出日文 SRT；有字幕时修轴 | 视频 / 生字幕 | 条数齐全、时间轴有序、无 0 时长堆积 | [subtitle.md](references/subtitle.md) |
| **3 校对与统一** | 修错字、统一专名、OP/ED 定稿 | 日文 SRT + 词表 | `eval/text_gate.py` 通过（kana=0、变体=0） | [subtitle.md](references/subtitle.md) |
| **4 翻译与交付** | ja→zh、QA、导出 | 日文 SRT + 映射 | 无日文残留、专名一致、条数一致 | [translation.md](references/translation.md) |

顺序不能跳：段 0 的结论决定段 1 下什么、段 2 走哪条路（ASR 还是复用参考字幕）。

### 段 0：调研（跑 ASR 前）

1. 四类字幕站各查一遍，结论写进项目 `bin/reference_docs/字幕调研.md`；日文字幕优先，可当源语言，中文字幕只作译名 / 术语对照。
2. 建 `bin/reference_docs/<作品>_外部参考.md`：故事背景与整体梗概、单集梗概、人名读音与招式、OP/ED 歌词、集号与时代语体。单集梗概最省的办法是抽片尾「次回予告」。
3. 定翻译调子：由背景定整体语体、由人物定各自台词口吻，写成正面指引；配事后 `style_lint` 检测与定向改写（[design/style-context.md](references/design/style-context.md)）。
4. 专名表初版：`data/glossaries/<series>.series.json`；读音进同目录 `.meta.json` 的 `readings` + `canonical_ja`。

### 段 1：获取

B 站还有 → `acquire/bili23.py`；老片只在公开 BT 发布里 → `acquire/p2p.py`。两条路都是「探清落点与体积 → 用户确认 → 再下」。

迅雷是本机唯一的 BT 客户端：新建任务面板的「立即下载」走 `kf-personal-service` 的任务脚本确认（`grab` 派单后默认调用 `tasks.py run thunder-download`，`--no-confirm` 关掉，`--save-dir` 改落点）。远程协作期间不开迅雷：`p2p.py grab` 检测到远程会话信号会拒绝，用户明确同意后加 `--allow-thunder`。

### 段 2–4：字幕本身

操作手册见 [references/subtitle.md](references/subtitle.md)；三条常用入口：

```bash
# 只有视频 → 日文 SRT（先测吞吐再铺全量）
python "<scripts-dir>/asr/qwen3_transcribe.py" --video "<第01話.avi>" --srt "data/ja/EP001.srt"

# 有日文字幕、要中文 → 翻译 + QA
python "<scripts-dir>/translate_srt.py" --input-dir "data/ja" --output-dir "data/zh" \
  --mappings "data/temp/noun_mappings.json" --batch 20 --qa-report --repair

# 粗剪一键（扫描 + 打轴 + 翻译）
python "<scripts-dir>/video_to_zh.py" --video-dir "<视频目录>" --output-dir "<输出目录>" \
  --engine qwen3 --mappings "data/glossaries/<series>.series.json"
```

## 常用入口

| 要干的事 | 现成入口 | 判据 / 常用参数 |
|---|---|---|
| 视频 → 日文 SRT → 中文（整季 / 整目录） | `video_to_zh.py` | 已有 `ja/<EP>.srt` 自动 reuse；`--chunk-sec/--max-batch` 透传给 ASR |
| 转录 + 修轴 + 专名 + 翻译一条龙 | `run_all.py` | `--asr qwen3`、`--translate`、`--skip-whisper` |
| 单集转写 / 单集翻译 | `asr/qwen3_transcribe.py`、`translate_srt.py --input` | 单集调试，不再包 wrapper |
| 批量翻译 + QA | `translate_srt.py` 逐集，或 `video_to_zh.py` | 单集模式才有 QA |
| OP/ED 定窗口、唱词统一定稿 | `audio/oped_boundaries.py`、`fix/oped_fixer.py`、`fix/oped_region.py`、`nouns/context_review.py --mode oped` | 指纹定窗口 + `--frames-dir` 抽帧复核 |
| 专名归一 / 变体替换 / 文本闸门 / 交付 | `nouns/normalize_variants.py`、`nouns/apply_map.py`、`eval/text_gate.py`、`eval/release_gate.py` | 闸门计数说了算 |
| 参考字幕复用 | `reuse/*` | 配对与重打轴 |

缺参数就给现有工具加透传并补测试；缺功能先看 [references/workflows.md](references/workflows.md) 的场景表，仍缺再报告用户。

## 配置与首次使用

项目 `CLAUDE.md` 末尾有 `## SKILL INITIALIZED: true` 即初始化完成；没有就按 [references/first-run.md](references/first-run.md) 走一遍。本机路径与密钥只写 `local.json`（gitignored）：

```bash
python scripts/harness/init_local.py --force --output local.json   # 从环境 / ~/.claude/settings.json 生成
python "<scripts-dir>/lib/projects_registry.py" --project "<项目名>" # 本项目登记了什么
```

系列 / 类型 / 风格 / 字幕目录写在 skill 根的 `projects_registry.json`，解析结果看 `projects_index.json`；输出目录与配置分档见 [references/config.md](references/config.md)。

## 开发与维护

改脚本前先查「常用入口」，缺参数加透传、缺功能先看 workflows 场景表。测试 `python -m pytest tests -q`，能力清单 `python scripts/capabilities.py --check`；junction 模式改完立即生效：`python scripts/harness/sync_skill.py --link`。改动分段、文档结构、同步与发布见 [references/development.md](references/development.md)。
