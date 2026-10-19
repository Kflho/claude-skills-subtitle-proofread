# 环境设置（当前版本）

> 首次使用或换机器时执行。**本机绝对路径/密钥不写进 skill，也不进 GitHub。**

## 1. 基础

- Python 3.12+
- ffmpeg（在 PATH）
- `PYTHONIOENCODING=utf-8`（Windows 防乱码）

```bash
python --version
ffmpeg -version
```

## 2. LLM（翻译/repair 必需）

使用任意 OpenAI 兼容 `/chat/completions` 端点：

```bash
export LLM_API_KEY="<your-key>"
export LLM_BASE_URL="https://api.deepseek.com/v1"   # 默认
export LLM_MODEL="deepseek-v4-pro"                  # 以你的配置为准
```

Windows PowerShell 可从已有配置文件读取，但**不要打印/提交 key**：

```powershell
$s = Get-Content "$env:USERPROFILE\.claude\settings.json" -Raw | ConvertFrom-Json
$env:LLM_API_KEY = $s.env.LLM_API_KEY
$env:LLM_BASE_URL = $s.env.LLM_BASE_URL
$env:LLM_MODEL = $s.env.LLM_MODEL
```

验证：

```bash
python scripts/translate_srt.py --input EP001.srt --output /tmp/test.srt --dry-run
```

## 3. Qwen3-ASR / ForcedAligner（可选，时间轴主路线）

- 独立 venv：`.venv-qwen`（torch + qwen-asr + fireredvad）；
- 模型目录：`models/Qwen3-ASR-1.7B`、`models/Qwen3-ForcedAligner-0.6B`；
- 通过 `.venv-qwen/Scripts/python.exe` 启动 worker，不污染主环境。

```bash
python scripts/run_all.py \
  --input-dir "<字幕目录>" --video-dir "<视频目录>" \
  --lang ja --asr qwen3 \
  --asr-python ".venv-qwen/Scripts/python.exe" \
  --translate
```

没有 Qwen 环境时不要加 `--asr qwen3`，走 legacy Whisper 路线。

## 4. FireRedVAD（可选，当前仅诊断）

- `.venv-qwen` + `fireredvad 0.0.2`；
- 权重：`models/FireRedVAD/{VAD,Stream-VAD,AED}`；
- 输出 hints，但默认流程**不消费**；只在 OP/ED 预研/诊断用。

```bash
python scripts/audio/detect_events.py \
  --wav temp/ep001.wav --mode aed \
  --out temp/scans/EP001.audio_hints.json
```

## 5. Legacy Whisper（可选）

本机已有 whisper.cpp CUDA 时，`WHISPER_CLI`/`WHISPER_MODEL` 由本地配置提供；
不在 skill 中写死路径。没有 Whisper 也能用 `--skip-whisper` + Qwen/仅翻译。

## 6. Python 依赖（可选增强）

```bash
python -c "import jamdict"   # 日语词典分类
python -c "import janome"    # 日语分词
python -c "import jieba"     # 中文分词
```

缺依赖时对应步骤降级，不影响主翻译/时间轴。

## 7. 本机配置 / 隐私

所有绝对路径、模型路径、API 配置只写**项目 `CLAUDE.md`** 或
`~/.claude/settings.json`；**脚本代码里不硬编码用户路径/密钥**。
初始化向导负责收集这些路径；普通用户按上面 1–6 节配置即可。

本机软件版本、模型绝对路径、API 配置来源由维护者本地保存，**不随 skill 分发**；
普通用户按上面 1–6 节配置即可。新会话需要恢复本机配置时，读维护者本地的
`sk_01_dev/LOCAL_ENV.md`（不在 GitHub）。

## 8. 快速验证

```bash
python -m pytest -q tests
```

期望全绿；有失败先修环境，不要继续跑 pipeline。
