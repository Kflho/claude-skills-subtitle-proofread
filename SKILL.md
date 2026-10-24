---
name: subtitle-proofread
description: >
  字幕任务统一入口，管全流程：前期调研（字幕站检索、故事背景、专名表）→ 资源获取
  （B 站、种子站、迅雷派单）→ 转录打轴 → 校对与术语统一 → 翻译交付。用户提到：字幕校对 /
  翻译字幕 / 专名、专有名词、译名、术语统一 / 时间轴修复、轴不对 / OP-ED、片头片尾、固定台词 /
  SRT、ASS 批量处理 / 找字幕、下片源时，先加载本 skill 再动手。维护或开发本 skill
  （改 pipeline、工具、文档）时同样先加载，现有工具优先，避免重复造轮子。
---

# 字幕生产线

> **本文件是主文档，只管全流程与阶段路由。** 具体怎么做在子文档里：
>
> | 要做什么 | 看哪里 |
> |---|---|
> | 前期调研：有没有现成字幕、故事背景、专名表 | [references/research.md](references/research.md) |
> | 资源获取：B 站、种子站、迅雷派单 | [references/acquire.md](references/acquire.md) |
> | 字幕本身：扫描、打轴、专名审查、翻译、润色、Flags | [references/subtitle.md](references/subtitle.md) |
> | 翻译与 QA 细则 | [references/translation.md](references/translation.md) |
>
> **资源驱动**：有什么用什么。有视频 → ASR 出轴；有参考字幕 → 复用/重打轴；
> 什么都没有 → 先做段 0（调研），别直接开跑。缺资源也能残血运行——跳过缺失步骤，
> 剩余步骤照常。

## 何时加载（使用 vs 开发）

- **使用**：任何字幕任务开始前先按本文件定位到阶段，再进子文档，不凭记忆直接改字幕。
- **开发**：修改本仓库任何 pipeline/工具/文档前也先重读「行为分级」「错误恢复」，
  **先找现有实现**（`oped_fixer`、`apply_map`、`run_all`、`references/*`），不要另写一套。
- **开发与 harness**：本仓库是唯一开发源。本机用 **junction** 指向仓库，
  改完立即生效：`python scripts/harness/sync_skill.py --link`；换机器/打包用复制模式
  （去掉 `--link`）。描述变更仍需新会话/重新加载才生效。
  提交/推送前先做隐私扫描（个人运维 skill `kf-personal-service` 的
  `scripts/privacy/*`，或本仓库的路径检查测试）。
- **本机路径与密钥只写在仓库的 `local.json`**（gitignored；junction 下 harness 直接读它）：
  `python scripts/harness/init_local.py --force --output local.json`
  （自动从环境/`~/.claude/settings.json` 读取模型目录、Qwen venv、Whisper、ffmpeg、
  LLM 端点）。仓库里不提交这些。

## 全流程（五段）

| 段 | 目的 | 输入 | 入口 | 完成判据 | 子文档 |
|---|---|---|---|---|---|
| **0 调研** | 先弄清「有没有现成字幕、这作品是什么」 | 片名/日文原名/链接 | 字幕站检索、外部参考、词表 | 有/没有参考字幕都写清；专名表初版就位 | [research.md](references/research.md) |
| **1 获取** | 把视频（和可能的字幕）拿到本地 | 链接 / 种子 | `acquire/bili23.py`、`acquire/p2p.py` | 视频进媒体库，逐文件核对完成 | [acquire.md](references/acquire.md) |
| **2 转录与打轴** | 无字幕时从音频出日文 SRT；有字幕时修轴 | 视频 / 生字幕 | `asr/qwen3_transcribe.py`、`asr/align_cues.py`、`video_to_zh.py` | 条数齐全、时间轴有序、无 0 时长堆积 | [subtitle.md](references/subtitle.md) |
| **3 校对与统一** | 修错字、统一专名、OP/ED 定稿 | 日文 SRT + 词表 | `run_all.py`、`nouns/*`、`fix/oped_fixer.py` | `eval/text_gate.py` 通过（kana=0、变体=0） | [subtitle.md](references/subtitle.md) |
| **4 翻译与交付** | ja→zh、QA、导出 | 日文 SRT + 映射 | `translate_srt.py`、`bin/exports/<YYMMDD>/` | 无日文残留、专名一致、条数一致 | [translation.md](references/translation.md) |

**顺序不能跳**：段 0 的结论决定段 1 下什么、段 2 走哪条路（ASR 还是复用参考字幕）。
没有参考字幕就是没有，别拿 B 站 AI 字幕凑数（[acquire.md](references/acquire.md) §7）。

### 段 0：前期调研

1. **先查有没有现成字幕**——四类站点各查一遍，结果写进项目 `bin/reference_docs/` 的调研记录：
   日文字幕优先（可直接当源语言），中文字幕只作译名/术语对照，不当源。

   ```bash
   python "<scripts-dir>/acquire/subs_search.py" plan -q "<日文原名>" \
     --out "bin/reference_docs/字幕调研.md" --json "data/temp/subs_search.json"
   ```

   清单里没实测的站点只给「人工检索」，不会拼错的检索 URL；查到什么都要把结论写回。
2. **建外部参考档案**（第 0 步，跑 ASR 之前）：`bin/reference_docs/<作品>_外部参考.md`。
   ASR 只回答「听到什么」，回答不了「正确写法是什么」；名字读音、招式、OP/ED 歌词、
   集号对照、时代用语都要从外部查。查什么、去哪查、可信度阶梯见
   [references/external-references.md](references/external-references.md)。
3. **专名表初版**：名字/招式 → `data/glossaries/<series>.series.json`；
   读音 → 同目录 `.meta.json` 的 `readings` + `canonical_ja`。

> 上游结构（页数、每页时长、**有没有字幕轨**）先用
> `python "<scripts-dir>/acquire/bili_meta.py" --bvid BV... --out data/reference/bili_meta.json`。
> 拿不到的来源记成缺口，不要用搜索摘要硬凑设定。

### 段 1：资源获取

B 站还有的 → Bili23；老片只在公开 BT 发布里 → `p2p.py`。两边都是
「探清落点和体积 → 用户确认 → 再下」。详细命令见 [references/acquire.md](references/acquire.md)。

**迅雷派单（本机唯一的 BT 客户端）**：

- 迅雷路径/下载目录自动探测，本机路径不写进仓库；`--thunder-exe` / `--download-dir` 可覆盖；
- 迅雷 12 的「新建任务面板」要人工确认一次「立即下载」（Electron 面板拿不到控件树）。
  自动确认统一走**调 `kf-personal-service` 的 GUI 任务脚本**
  （`tasks.py run thunder-download`，本 skill 只调用）：`grab` 派单后默认就调，
  `--no-confirm` 关掉，`--save-dir` 顺便改落点；脚本位置配置见
  [references/acquire.md](references/acquire.md) §11；
- **远程协作期间禁止开启迅雷等占用型软件**：远程同步开发时迅雷吃带宽/磁盘/CPU，
  会把远程会话和同步挤掉。`p2p.py grab` 派单前会检查远程会话信号，检测到就拒绝；
  确实要用时由用户明确同意后加 `--allow-thunder`；
- 落点可能是迅雷的「上次使用目录」而不是默认目录，`status/collect` 两处都找；
- 先在 `--dry-run` 里看体积和盘余量：BD 1080p 全集约 25 GiB。

### 段 2–4：字幕本身

细节全在 [references/subtitle.md](references/subtitle.md)，本文件只给入口：

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

## 配置与目录

### 首次使用？

检查项目 `CLAUDE.md` 末尾是否有 `## SKILL INITIALIZED: true`。

**没有** → 首次使用。读取 `references/first-run.md`，跟随初始化向导完成配置后再继续。

**有** → 已初始化。从 CLAUDE.md 获取路径，直接进入流程。

> 如需重新初始化（添加参考字幕、更换模型等），删除 CLAUDE.md 中的 `SKILL INITIALIZED: true` 行即可。

**项目级配置不写在项目里**：系列名、类型、风格、字幕目录写在 skill 根的
`projects_registry.json`（键 = 项目文件夹名），机器路径写在 `local.json`，
解析后的绝对路径看 `projects_index.json`：

```bash
python "<scripts-dir>/lib/projects_registry.py" --project "<项目名>"   # 本项目登记了什么
python "<scripts-dir>/lib/media_paths.py" --check                       # 视频/字幕目录解析结果
python "<scripts-dir>/lib/projects_index.py" --check                    # 全部项目总账
```

### 输出目录约定

项目根按「二元目录」分层，别把中间产物和成品混在根上：

```text
<project>/
  bin/reference_docs/   输入资料（参考字幕、源字幕、外部参考）
  bin/exports/<YYMMDD>/ 成品，一次发布一个日期目录；同名 zip 放旁边
  data/temp/            一次性中间产物（scans/、ASR、锚点、缓存）
  data/reports/         生成的报告（问题解决报告.md、proper-nouns.md、manual-review/）
  data/glossaries/      词表
  data/ja/  data/zh/    日文转写 / 中文工作副本
```

`lib/project_paths.py` 是唯一路径入口：**项目下有 `data/` 就写 `data/temp`、
`data/reports`；没有就退回旧布局 `temp/`、`reports`**。

新建项目时先 `mkdir data`，再跑管线；成品只进 `bin/exports/<日期>/`，发布目录只读，
改动新建日期目录。

### 行为分级：哪些默认做，哪些等指令

每个会**写盘**的行为都在 `lib/gates.py` 登记一个级别。判据只有一条：
**这一步会不会在用户没要求的情况下改变字幕内容？** 会 → 至少 L1。

| 级别 | 含义 | 包含 |
|---|---|---|
| **L0** | 默认必做，只读 | 扫描/编码探测、VAD 台词↔人声匹配（含「有人声无字幕」缺口清单）、交付校验 |
| **L1** | 默认只检测并标注，写盘要开关 | VAD 删「无人声」条（`--vad-clean-apply`）、missing 补条 |
| **L2** | 只在明确要求时执行 | OP/ED 预替换、专名预替换、TV 复用覆写、润色、繁→简、`fixes.json` 改写 |
| **L3** | 执行前必须确认 | 覆盖已交付字幕、覆盖既有 `.bak` |

默认取安全侧：VAD 删条只出清单，`missing` 只列不插。
新增会写盘的功能时，先在 `lib/gates.py` 加一条 `Gate(...)`，再写实现。

## 错误恢复

| 输出/症状 | 操作 |
|---|---|
| `SyntaxError` / `UnicodeEncodeError` | emoji→ASCII、括号补全，修完重跑 |
| `Done: 0 fixed` + 无 `[whisper]` 输出 | `--video-dir` 缺失或路径错 |
| 某步骤失败但已写中间文件 | 清空 `temp/` + `reports/`，`--force-rescan` 重跑 |
| 参考字幕乱码 / 解析 0 cue | 编码探测失准；检查 BOM/转码；ASS 惯例 UTF-16LE+BOM |
| `LLM_API_KEY not set`（翻译/润色） | 明确失败或保留原文；不要降级到传统机翻（已移除） |
| `HTTP 400 invalid model` | 用 `deepseek-flash`，或更新 `lib/config.py:LLM_MODEL_DEFAULT` |
| 翻译 exit 0 但成片仍是日文 | 推理 token 吃光预算：`LLM_REASONING_EFFORT=none`，必要时提高 `LLM_MAX_TOKENS` |
| `--repair` 后 `echo`/术语全灭 | `--input` 给了译文而不是源文；QA 在拿中文和中文比 |
| 整段译文错位一格 | 模型把两条并成一条或多吐一条；查 `[translate] ... returned N item(s)` 日志 |
| 译文带编号原文（`1. 原文`） | glossary 回显；说明文字写 CLAUDE.md，不要写 JSON |
| OP/ED 对白被歌词覆写/变少 | 检查 OP/ED 窗口与歌词判定；与日文源逐条对数量 |
| ASS 内容/编码/字段异常 | 用 `ass_repair.py --check all`；ASS 写 UTF-16LE+BOM |
| 迅雷派单被拒（远程会话） | 远程协作期间不开迅雷；等会话结束，或用户明确同意后 `--allow-thunder` |
| 字幕站检索全失败 | 记缺口继续（不是所有作品都有字幕）；确认代理可用，别猜站点字段 |

## AI 介入点

→ [references/interventions.md](references/interventions.md) — 每个 🤖 点：触发条件、操作流程、判断规则。

## 子文档索引

| 子文档 | 内容 |
|---|---|
| [references/research.md](references/research.md) | 段 0：字幕站检索、故事背景、专名表 |
| [references/acquire.md](references/acquire.md) | 段 1：Bili23（B 站）、P2P/BT、迅雷派单与落点 |
| [references/subtitle.md](references/subtitle.md) | 段 2–4 操作手册：专名审查、扫描、打轴、重打轴、模块化调用、翻译、润色、Flags |
| [references/translation.md](references/translation.md) | 翻译与 QA 细则 |
| [references/external-references.md](references/external-references.md) | 外部参考：查什么、可信度阶梯、落地清单 |
| [references/workflows.md](references/workflows.md) | 典型工作流场景（从视频生成、翻译、修复、完整流程）。**设计工作流前先看，避免重复造轮子** |
| [references/first-run.md](references/first-run.md) | 首次使用向导 |
| [references/config.md](references/config.md) | 配置分层（local.json / projects_registry.json） |
| [references/interventions.md](references/interventions.md) | AI 介入点 |
| `references/phase{1,2,3}-*.md` | 三阶段命令参考（扫描 / 修轴 / 统一） |
| [references/full-mode.md](references/full-mode.md) | 有参考字幕时的完整工作流 |
| [references/reuse-reference-subs.md](references/reuse-reference-subs.md) | 参考字幕复用的判定与判据 |
