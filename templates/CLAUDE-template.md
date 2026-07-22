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

## 密钥与路径

> ⚠️ 含私密信息，禁止上传公开仓库。

```bash
export PYTHONPATH="<skill_scripts_dir>"
export WHISPER_CLI='<whisper_cli_path>'
export WHISPER_MODEL='<main_model_path>'
export WHISPER_RETRY_MODEL='<backup_model_path>'
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
