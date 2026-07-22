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

## Step 3：Whisper 安装检查

向用户提问：

> 你是否已安装 whisper.cpp？
>
> - 如果**已安装** → 请提供以下路径：
>   - whisper.cpp 可执行文件
>   - 主模型文件（推荐 kotoba-whisper-v2.0 q5_0 量化版）
>   - 备用模型文件（主模型失败时切换，可与主模型相同）
> - 如果**未安装** → 需要先安装 whisper.cpp 和模型才能继续。以下是安装参考：
>   - whisper.cpp: https://github.com/ggerganov/whisper.cpp/releases
>   - 日语推荐模型: https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0-ggml
>
> ⚠️ 未安装 Whisper 强行运行发生的任何问题概不负责。

如果用户已安装，验证路径存在（用 `test -f` 或 `ls`）。记录路径。

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
