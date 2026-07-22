# 初始化向导

> Claude：此文件是首次初始化时执行的步骤脚本。
> 触发条件：项目 CLAUDE.md 不存在或不含 `SKILL INITIALIZED: true`。
> 完成后写入 CLAUDE.md，末尾加 `SKILL INITIALIZED: true` 标记。

---

## Step 1：告知流程和原理

向用户说明：

> 字幕校对 skill 的运行原理是，使用 Whisper 模型检测原始字幕的乱码片段并自动修复。首先是逐行扫描乱码，然后通过 VAD 语音检测 + Whisper 重转录修复乱码，最后统一专有名词和固定格式。自动修复不了的内容会转交给你判断。

然后根据当前 CLAUDE.md（如有）补充项目已有的配置信息。

---

## Step 2：收集项目路径

向用户提问：

> 请提供以下路径信息（不确定的留空）：
>
> | # | 路径项 | 用途 | 是否必需 |
> |---|--------|------|:---:|
> | 1 | **目标字幕目录** | 待校对字幕文件 | ✅ 必需 |
> | 2 | **参考字幕目录** | 高质量人工字幕，AI 校对时可对照参考 | 可选 |
> | 3 | **参考视频目录** | mkv/mp4，Whisper 提取音频 | 可选（推荐） |

**⚠️ 如果用户未提供 #3 参考视频目录 → 残血运行。** 告知用户：无视频时管线跳过 Whisper 音频修复，仍可扫描乱码+统一专名，但无法自动修复乱码片段。

**多项目处理**：提醒用户最好一个项目一个文件夹。如果用户提到有其他项目，记录到 CLAUDE.md 的项目索引表中：

```markdown
## 已知项目
| 项目 | 路径 | 视频目录 | 参考字幕 |
|------|------|---------|---------|
| <项目1> | <path> | <video> | <ref or 无> |
| <项目2> | <path> | <video> | <ref or 无> |
```

---

## Step 3：Whisper 后端检测与选择

### 3.1 自动检测

运行后端检测脚本，查看系统上已安装的 Whisper 实现：

```bash
cd "<project>" && python -c "
from lib.whisper_backends import backend_detection_report
import json
report = backend_detection_report()
print(json.dumps(report, ensure_ascii=False, indent=2))
"
```

输出示例：

```json
{
  "available": ["whisper-cpp", "faster-whisper"],
  "not_available": ["openai-whisper"],
  "recommendations": "可用后端: whisper-cpp, faster-whisper",
  "details": {
    "whisper-cpp": {
      "backend_id": "whisper-cpp",
      "installed": true,
      "version": "v1.7.2",
      "path": "D:/software/video/whisper-cublas-12.4.0-bin-x64/whisper-cli.exe"
    },
    "faster-whisper": {
      "backend_id": "faster-whisper",
      "installed": true,
      "path": "(Python package)"
    }
  }
}
```

### 3.2 处理检测结果

根据检测结果分情况处理：

**情况 A：检测到多个后端** → 问用户选哪个：

> 检测到以下 Whisper 后端可用：
>
> | # | 后端 | 版本 | 特点 |
> |---|------|------|------|
> | 1 | whisper.cpp | v1.7.2 | GGML 量化模型，GPU加速，内存低 |
> | 2 | faster-whisper | — | CTranslate2引擎，比原版快4x |
>
> 请选择要使用的后端（输入数字）：
>
> - **选择 1** → 需要提供 whisper-cli 路径 + GGML 模型路径
> - **选择 2** → 需要提供 CTranslate2 模型目录路径

记录用户选择：`WHISPER_BACKEND=whisper-cpp`（或 `faster-whisper`）。

**情况 B：只检测到一个后端** → 直接使用，告知用户：

> 检测到 Whisper 后端：whisper.cpp (v1.7.2)，将使用此后端。

**情况 C：未检测到任何后端** → 引导安装：

> ⚠️ 未检测到任何 Whisper 后端。请先安装以下任一方案：
>
> **方案 1：whisper.cpp（推荐 — 无需 Python 依赖）**
> 1. 下载 whisper-cli：https://github.com/ggerganov/whisper.cpp/releases
> 2. 下载日语模型：https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0-ggml
>    （推荐 `ggml-kotoba-whisper-v2.0-q5_0.bin`）
> 3. 备用模型：`ggml-large-v3-q5_0.bin`（主模型失败时切换）
>
> **方案 2：faster-whisper（Python — pip 安装）**
> ```bash
> pip install faster-whisper
> # 模型自动从 HuggingFace 下载，首次运行需联网
> ```
>
> **方案 3：openai-whisper（Python — pip 安装）**
> ```bash
> pip install openai-whisper
> # 模型自动下载到 ~/.cache/whisper/
> ```
>
> 安装完成后重新运行初始化。

### 3.3 收集模型路径

根据用户选择的后端，要求提供对应路径：

**whisper.cpp 用户**：
> 请提供以下路径：
> - whisper.cpp 可执行文件（如 `D:/.../whisper-cli.exe`）
> - 主模型文件（推荐 kotoba-whisper-v2.0 q5_0 量化版，`.bin` 格式）
> - 备用模型文件（主模型失败时切换，可与主模型相同）

验证路径存在（用 `test -f` 或 `ls`），写入 CLAUDE.md：

```bash
export WHISPER_BACKEND='whisper-cpp'
export WHISPER_CLI='D:/software/video/whisper-cublas-12.4.0-bin-x64/whisper-cli.exe'
export WHISPER_MODEL='D:/software/video/whisper-cublas-12.4.0-bin-x64/models/ggml-kotoba-whisper-v2.0-q5_0.bin'
export WHISPER_RETRY_MODEL='D:/software/video/whisper-cublas-12.4.0-bin-x64/models/ggml-large-v3-q5_0.bin'
```

**faster-whisper 用户**：
> 请提供模型路径（CTranslate2 格式目录，或 HuggingFace 模型 ID）：
> - 本地路径如 `D:/models/faster-whisper-kotoba-v2.0`
> - 或 HuggingFace ID 如 `kotoba-tech/kotoba-whisper-v2.0`

写入 CLAUDE.md：

```bash
export WHISPER_BACKEND='faster-whisper'
export WHISPER_MODEL='kotoba-tech/kotoba-whisper-v2.0'
export WHISPER_RETRY_MODEL='deepdml/faster-whisper-large-v3-turbo-ct2'
```

**openai-whisper 用户**：
> 请提供模型名称（`tiny` / `base` / `small` / `medium` / `large-v3`）或本地 `.pt` 文件路径。

写入 CLAUDE.md：

```bash
export WHISPER_BACKEND='openai-whisper'
export WHISPER_MODEL='large-v3'
export WHISPER_RETRY_MODEL='medium'
```

### 3.4 验证后端可用性

```bash
cd "<project>" && python -c "
from lib.whisper_backends import validate_backend, BACKEND_INFO
# 对选定的后端跑冒烟测试（1秒静音片段）
import os
backend = os.environ.get('WHISPER_BACKEND', 'whisper-cpp')
model = os.environ.get('WHISPER_MODEL', '')
ok, msg = validate_backend(backend, model)
print(f'{backend}: {msg}')
if not ok:
    print('WARNING: backend validation failed — Whisper may not work at runtime.')
"
```

> ⚠️ 验证失败时警告用户但不阻止继续。残血运行模式跳过音频修复，仍可扫描乱码+统一专名。

---

## Step 3.5：Python 依赖安装

### 3.5.1 核心依赖：jamdict（日语词典）

日语项目依赖 `jamdict`（JMdict + JMnedict 词典），用于 Phase 3 专名自动分类 — 区分"普通日语词"和"专有名词"。包体很小（~500KB），pip 一键安装。

```bash
pip install jamdict
```

`jamdict` 首次运行时自动下载 JMdict 词典数据库（SQLite，~50MB），仅一次。

**验证**：

```bash
python -c "from jamdict import Jamdict; j = Jamdict(); print('OK:', len(j.lookup('日本').entries), 'entries')"
```

期望输出 `OK: N entries` → 安装成功。

> ⚠️ 安装失败时警告用户但不阻止继续。`jamdict` 不可用时 Phase 3 退回规则分类（精度略降），仍可运行。

### 3.5.2 开源许可说明

jamdict 使用的 JMdict/JMnedict 词典数据遵循 [CC-BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) 许可。此 skill 仅通过 jamdict 库查询词典数据，不捆绑或分发词典文件。

---

## Step 4：项目特征自动检测

扫描目标字幕目录，自动检测：

1. **格式**：SRT 还是 ASS（看文件扩展名）
2. **语言**：扫描几个 SRT 文本，判断 ja（有假名）还是 zh（有汉字+无假名）

将检测结果告知用户确认：

> 检测到：
> - 格式: <SRT/ASS>
> - 语言: <日语/中文>
>
> 是否正确？

确认后写入 CLAUDE.md。

---

## Step 5：生成 CLAUDE.md

读取 `templates/CLAUDE-template.md`，将用户的回答填入模板：

- `<PROJECT_NAME>` → 项目目录名
- `<target_sub_dir>` → 用户提供的目标字幕目录
- `<video_dir>` → 用户提供的视频目录
- `<ref_sub_dir or "无">` → 用户提供的参考字幕目录，或"无"
- `<skill_scripts_dir>` → 当前 skill 的 `scripts/` 目录绝对路径
- `<whisper_cli_path>` → Whisper CLI 路径
- `<main_model_path>` → 主模型路径
- `<backup_model_path>` → 备用模型路径
- `<project_root>` → 当前项目目录
- `<SRT_or_ASS>` → 自动检测的格式
- `<ja_or_zh>` → 自动检测的语言

**绝不硬编码任何用户路径到 skill 文件。所有路径只写入 CLAUDE.md。**

---

## Step 6：开发者检查

向用户提问：

> 你是这个 skill 的开发者/维护者吗？（即需要修改脚本、改进 skill 本身）
>
> - **是** → 开启 Git 自动备份 + 加载维护规则
> - **否** → 跳过（推荐）

如果选**是**：
- 在 CLAUDE.md 末尾追加维护规则引用（指向 `dev/maintenance.md`）
- 后续运行中加载 `dev/` 目录内容

如果选**否**：
- 不提 git
- 不加载 `dev/` 目录
- 所有修改直接写入 SRT（无 git 备份步骤）

---

## Step 7：完成

告知用户初始化完成，摘要配置：

> 初始化完成！配置摘要：
> - 项目: <name>
> - 格式: <SRT/ASS> | 语言: <ja/zh>
> - 视频: <video_dir>
> - Whisper: <model_name>
> - 参考字幕: <有/无>
> - 开发者模式: <是/否>
>
> 下次运行 skill 时将直接进入校对流程。如需重新初始化，删除 CLAUDE.md 中的 `SKILL INITIALIZED: true` 行即可。

写入 CLAUDE.md 末尾：`## SKILL INITIALIZED: true`
