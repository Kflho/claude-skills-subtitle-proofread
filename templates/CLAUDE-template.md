# CLAUDE.md — <PROJECT_NAME>

## 项目特征

- 格式: <SRT_or_ASS> | 语言: <ja_or_zh>

## 可用资源

> 资源驱动：有什么用什么。有视频→Whisper 修复；有参考字幕→AI 校对时注入参考。

| 资源 | 路径 |
|------|------|
| 目标字幕 | <target_sub_dir> |
| 视频文件 | <video_dir> |
| 参考字幕 | <ref_sub_dir or "无"> |
| demucs | 可选（人声分离，减少 BGM 幻觉） |

## 密钥与路径

> ⚠️ 含私密信息，禁止上传公开仓库。

```bash
export PYTHONIOENCODING=utf-8   # 防 Windows GBK 乱码
export PYTHONPATH="<skill_scripts_dir>"

# Whisper 后端选择（三选一，根据 init-wizard 检测结果填写）
export WHISPER_BACKEND='<whisper-cpp|faster-whisper|openai-whisper>'

# ── whisper.cpp 后端（WHISPER_BACKEND=whisper-cpp 时填写以下三项）──
export WHISPER_CLI='<whisper_cli_path>'
export WHISPER_MODEL='<main_model_path>'
export WHISPER_RETRY_MODEL='<backup_model_path>'

# ── faster-whisper 后端（WHISPER_BACKEND=faster-whisper 时填写）──
# export WHISPER_MODEL='kotoba-tech/kotoba-whisper-v2.0'
# export WHISPER_RETRY_MODEL='deepdml/faster-whisper-large-v3-turbo-ct2'

# ── openai-whisper 后端（WHISPER_BACKEND=openai-whisper 时填写）──
# export WHISPER_MODEL='large-v3'
# export WHISPER_RETRY_MODEL='medium'
```

## 运行

```bash
cd "<project_root>"
python "<skill_scripts_dir>/run_all.py" --video-dir "<video_dir>"
```

常用变体：`--limit N`（前N集）、`-e EP001-EP010`（指定范围）、`--dry-run`、`--force-rescan`。

> ⚠️ `--apply-ai-review` 和 `--apply-checklist` 是后处理快速路径，不能和 full run 一起用。
> 正确用法：先跑 full run，AI/人工审查完成后，再单独带 flag 跑一次。

## SKILL INITIALIZED: true
