# CLAUDE.md — <PROJECT_NAME>

## 项目特征

- 格式: <SRT_or_ASS> | 语言: <ja_or_zh>

## 可用资源

> 资源驱动：有什么用什么。有视频→Whisper 修复；有参考字幕→AI 校对时注入参考。

| 资源 | 路径 |
|------|------|
| 目标字幕 | <target_sub_dir>（子目录名，如 `机翻电影字幕`） |
| 视频文件 | <video_dir or "无"> |
| 参考字幕 | <ref_sub_dir or "无"> |
| demucs | 可选（人声分离，减少 BGM 幻觉） |

## 密钥与路径

> ⚠️ 含私密信息，禁止上传公开仓库。

```bash
export PYTHONIOENCODING=utf-8   # 防 Windows GBK 乱码
export PYTHONPATH="<skill_scripts_dir>"

# Whisper 后端选择（三选一，根据 first-run 检测结果填写）
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

# ── Baidu 翻译（可选 — 仅 --lang zh 项目需要）──
# Whisper 转录日语→日语。中文校对时，Baidu 将 Whisper 输出翻译成中文，
# 避免 AI 自行翻译浪费 token。无凭证时自动降级（日语原文→AI翻译）。
#
# 注册: https://fanyi-api.baidu.com/ → 通用翻译API
#   个人认证（高级版）: 100万字符/月免费, 10 QPS, 推荐
#   标准版（未认证）:   5万字符/月免费,  1 QPS
#
# 凭证存放: ~/.baidu_translate（推荐，不污染命令行历史）或环境变量
#   BAIDU_APPID=你的APPID
#   BAIDU_SECRET=你的密钥
#
# Endpoint: 默认直连百度官方 API。如果没有固定公网 IP，需自建 nginx 代理：
#   export BAIDU_API_ENDPOINT='http://<服务器IP>:<端口>/api/trans/vip/translate'
#   nginx 配置: proxy_pass https://fanyi-api.baidu.com; + proxy_ssl_server_name on;
export BAIDU_APPID=''
export BAIDU_SECRET=''
# export BAIDU_API_ENDPOINT='http://<IP>:<端口>/api/trans/vip/translate'

# ── LLM API（翻译 + 润色共用，--lang zh 项目可选）──
# 支持任何 OpenAI 兼容 API（DeepSeek、OpenAI、Gemini 等）
# 翻译脚本 (translate_srt.py) 和润色脚本 (polish_zh.py) 共用此配置
#
# 获取 key: https://platform.deepseek.com/（推荐，有免费额度）
# 其他: https://platform.openai.com/ / https://aistudio.google.com/
#
# ⚠️ 实际 key 建议设为系统环境变量，不要明文写入此文件
export LLM_API_KEY=''
export LLM_MODEL='deepseek-chat'
export LLM_BASE_URL='https://api.deepseek.com/v1'
```

## 运行

```bash
cd "<project_root>"
python "<skill_scripts_dir>/run_all.py" \
  --input-dir "<input_sub_dir>" \
  [--video-dir "<video_dir>"] \
  [--skip-whisper]
```

常用变体：`--limit N`（前N集）、`-e EP001-EP010`（指定范围）、`--dry-run`、`--force-rescan`。

> `<input_sub_dir>` 是字幕文件所在子目录。默认 `AI审查后`，指向项目根目录下直接包含字幕的文件夹。
> 无视频时加 `--skip-whisper`，生成 `reports/问题解决报告.md`（⬜ 待人工处理）。

> ⚠️ `--apply-ai-review` 是后处理快速路径，不能和 full run 一起用。
> 正确用法：先跑 full run，AI 审查完成后，再单独带 flag 跑一次。

## SKILL INITIALIZED: true
