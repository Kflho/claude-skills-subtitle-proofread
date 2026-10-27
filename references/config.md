# 配置分两档：机器级 vs 项目声明（都在全局）

原则一条：**项目文件夹只放内容（字幕、报告、文档），不放配置**。机器相关的绝对路径
写在 `local.json`；「这个项目是哪个系列、什么类型、风格、目录覆盖」写在同目录的
`projects_registry.json`。两份都在 skill 根、都 gitignored，打开 skill 就知道本机
所有项目——项目文件夹里没有 `project.json`。

## 1. 全局配置（机器级）

位置：harness 副本的 `local.json`（`~/.agents/skills/subtitle-proofread/local.json`），
**不进 git**。只放很少变化、跟机器绑定的东西：

```text
models_dir / asr_model / aligner_model / qwen_python / ffmpeg
whisper_cli / whisper_model / whisper_retry_model
llm.api_key / llm.base_url / llm.model
paths.bili23_config / paths.bili23_exe
paths.media            媒体库根目录（视频在哪）
projects_root          字幕项目根目录（项目在哪）
```

模板：`local.example.json`；用 `scripts/harness/init_local.py --force` 生成/更新。

### 1.1 媒体库（`paths.media`）

```json
"paths": {
  "media": {
    "root": "<动画库根目录>",
    "categories": {"animation": ""},
    "kinds": {"tv": "TV", "ova": "ova", "movie": "movie"}
  },
  "projects_root": "<字幕项目根目录>"
}
```

- 解析结果 `<root>/<category>/<KIND>/<系列名>`；`categories` 值为空串表示种类目录直接挂根上；
- `kinds` 的值若写绝对路径，就优先用它（某台机器的 TV 放在别处时用得上）；
- 每段按磁盘真实大小写匹配，`TV`/`tv` 混用不用改配置。

**根目录必须先问用户。** 不知道就先扫：

```bash
python scripts/lib/media_paths.py --discover     # 列出候选库
python scripts/lib/media_paths.py --check        # 打印本机解析结果
```

`--discover` 只认「孩子里有 ≥2 个类型目录」或「目录名像 animation/anime/动漫」的文件夹，
扫两层，不猜内容。

### 1.2 项目登记表（`projects_registry.json`）＋总账（`projects_index.json`）

两份都在 skill 根、和 `local.json` 并列、都 gitignored。

**登记表是唯一手写事实源**（`projects_registry.json`，键 = 项目文件夹名）：

```json
{
  "schema_version": 1,
  "projects": {
    "赤胴铃之助": {
      "series": "赤胴铃之助",
      "media_type": "tv",
      "category": "animation",
      "style": "江户时代剑戟动画，短句喊话的少年英雄口气；口吻见项目参考档案",
      "source_lang": "ja",
      "target_lang": "zh-CN",
      "video_dir": "",
      "subtitles": {
        "reference_dir": "bin/reference_docs/参考字幕",
        "input_dir": "data/ja",
        "output_dir": "data/zh"
      },
      "bili23": {"source_url": "", "download_dir": ""}
    }
  }
}
```

- `series` / `media_type` / `category`：决定从 `paths.media` 推哪个视频目录；
- `video_dir`：**只在目录不符合库规则时写**（嵌在合集里的、发布组命名的），
  相对路径按项目根解析；
- `subtitles.*`：相对项目根，留空就用 `data/ja`、`data/zh`、`bin/reference_docs/参考字幕`；
- `bili23.download_dir` 由 `set-download-path --project` 写入，`--scope global` 才落
  `local.json`。

**总账是生成物**（`projects_index.json`），把登记表 + `local.json` + 磁盘解析成一张
绝对路径表（系列/类型/风格/语言/视频目录及来源/文件数/体积/三段字幕目录/成品目录）：

```bash
python scripts/lib/projects_index.py          # 用 local.json 的 projects_root
python scripts/lib/projects_index.py --check  # 只打印，不写文件
python scripts/lib/projects_registry.py       # 看登记表（--project <dir> 看单条）
```

没有任何配置的项目文件夹照样能被发现（标记是 `CLAUDE.md`），此时身份退回文件夹名，
总账把它标成「(未登记)」——正常流程应当补登记。视频目录的兜底顺序：
登记表 `video_dir` → 库规则推导 → `CLAUDE.md` 里记过的真实目录 → 项目根 → 同名模糊匹配，
`video_source` 字段写明是哪一条给出的答案。

## 2. 项目文件夹里放什么

只放内容和文档：`data/ja`、`data/zh`、`data/reports`、`bin/reference_docs`、
`bin/exports`、`CLAUDE.md`。**不再有 `project.json`**；想改系列名/类型/风格/目录覆盖，
改 `projects_registry.json`，然后重跑 `projects_index.py` 看解析结果。

## 3. 取值优先级

```text
CLI 参数
  > projects_registry.json（项目声明）
  > local.json（全局兜底）
  > 推导默认（库规则 / 项目内相对目录）
```

Bili23 的 `download` 现在会：

1. 读 `--download-dir`；
2. 读登记表里该项目 `bili23.download_dir`；
3. 用 `--media-type` + `--series` 按 `paths.media` 推出目录；
4. 读 `local.json` 的 `paths.bili23_download_dir`（全局兜底）；
5. 都没有 → **直接报错，要求先问用户**，不会猜、不会沿用上一项目。

## 4. 先问用户，再写配置

下载路径这类信息不要让 agent 猜。标准动作：

```bash
# 1) 问用户：这个项目下载到哪个库/哪个目录？
# 2) 库根目录写进 local.json 的 paths.media（换机器时重问一次）
# 3) 完全退出 Bili23（它会覆写 config.json）
python scripts/acquire/bili23.py set-download-path "<dir>" --project "<项目根>"
# 4) 启动 Bili23，再下载
```

`set-download-path` 会同时：

- 改 Bili23 的 `Download.download_path`（MCP 实际使用这个）；
- 写 `projects_registry.json` 里该项目的 `bili23.download_dir`（键 = 项目文件夹名）；
- 若用 `--scope global`，写 `local.json` 的全局兜底。

> 改 Bili23 的 `config.json` **只能用 python / `_patch_bili_config.py`**（无 BOM）。
> PowerShell 的 `Set-Content -Encoding UTF8` 会带 BOM，orjson 读不了，
> Bili23 会把 Cookie、MCP token、下载目录一起重置成默认值。

## 5. 审查建议

```bash
python scripts/config/audit.py --skill <skill-dir> [--project <project-dir>]
```

本节之外，配置还要看：

- `local.json` / `projects_registry.json` / `projects_index.json` 是否被 git 跟踪？
  跟踪了就是泄露机器路径/密钥，必须移除；
- 项目文件夹里是否又长出 `project.json`、`local.json`？一律不该有，搬到登记表；
- 全局 `local.json` 里是否混进了项目级键（`bili23.download_dir`、`bili23_source_url`、
  `style`）？有就搬到 `projects_registry.json`；
- 媒体库根目录是否被写进了 `SKILL.md` / `references/*.md`？写了就是从 `local.json`
  回退成硬编码，必须删；
- 相对仓库路径（`scripts/...`、`rules/...`）可以提交；绝对本机路径不行。

## 6 输出目录：项目内按二元目录分层

中间产物和成品分开，别混在项目根：

```text
<project>/
  bin/reference_docs/   输入资料（参考字幕、源字幕、外部参考）
  bin/exports/<YYMMDD>/ 成品，一次发布一个日期目录；同名 zip 放旁边
  data/temp/            一次性中间产物（scans/、ASR、锚点、缓存）
  data/reports/         生成的报告（问题解决报告.md、proper-nouns.md、manual-review/）
  data/glossaries/      词表
  data/ja/  data/zh/    日文转写 / 中文工作副本
```

`lib/project_paths.py` 是唯一路径入口：项目下有 `data/` 就写 `data/temp`、`data/reports`；
没有就退回旧布局 `temp/`、`reports`。新建项目先 `mkdir data` 再跑管线；成品只进
`bin/exports/<日期>/`，发布目录只读，要改动就新建日期目录。
