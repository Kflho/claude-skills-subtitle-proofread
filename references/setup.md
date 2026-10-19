# 环境设置（当前版本）

> 仓库只放代码和文档；**机器路径、模型目录、venv、密钥都不进仓库**。
> harness 用的是同步副本，本机信息写在副本的 `local.json`。

## 0. 开发 → 同步 → 初始化

```bash
# 1) 在开发仓库里改完
python scripts/harness/sync_skill.py --dry-run   # 预览会同步哪些文件
python scripts/harness/sync_skill.py             # 同步到 ~/.agents/skills/<name>

# 2) 在 harness 副本里生成/更新本机配置
cd ~/.agents/skills/subtitle-proofread
python scripts/harness/init_local.py --check     # 只检查
python scripts/harness/init_local.py --force     # 写入 local.json
```

`local.json` 的解析顺序：**环境变量 > local.json > 内置默认**。
`SKILL_LOCAL_CONFIG` 可指向另一个 local.json（测试/多机）。

## 1. 基础

- Python 3.12+
- ffmpeg（PATH 或 `local.json: paths.ffmpeg`）
- `PYTHONIOENCODING=utf-8`（Windows 防乱码）

```bash
python --version
ffmpeg -version
```

## 2. LLM（翻译/repair 必需）

任意 OpenAI 兼容 `/chat/completions` 端点。`init_local.py` 会从
`~/.claude/settings.json` 的 `env` 读取 `LLM_*` 写入 `local.json`；

```bash
export LLM_API_KEY="<your-key>"
export LLM_BASE_URL="https://api.deepseek.com/v1"
export LLM_MODEL="deepseek-flash"
```

环境变量优先于 `local.json`，适合临时覆盖。不要打印/提交 key。

验证：

```bash
python scripts/translate_srt.py --input EP001.srt --output /tmp/test.srt --dry-run
```

## 3. Qwen3-ASR / ForcedAligner（可选，时间轴主路线）

路径全部来自 `local.json`：

| 字段 | 含义 |
|---|---|
| `paths.qwen_python` | 独立 venv 的 python（torch + qwen-asr + fireredvad） |
| `paths.models_dir` | 模型根目录（含 `Qwen3-ASR-1.7B`、`Qwen3-ForcedAligner-0.6B`） |
| `paths.asr_model` / `paths.aligner_model` | 可选覆盖；空则用 models_dir 下的默认名 |

```bash
python scripts/run_all.py \
  --input-dir "<字幕目录>" --video-dir "<视频目录>" \
  --lang ja --asr qwen3 \
  --asr-python "<qwen-python>" \
  --translate
```

没有 Qwen 环境时不要加 `--asr qwen3`，走 legacy Whisper 路线。

## 4. FireRedVAD（可选，当前仅诊断）

- `<qwen-python>` + `fireredvad 0.0.2`；
- 权重：`<models-dir>/FireRedVAD/{VAD,Stream-VAD,AED}`；
- 输出 hints，默认流程**不消费**，仅 OP/ED 预研/诊断。

```bash
python scripts/audio/detect_events.py \
  --wav temp/ep001.wav --mode aed \
  --out temp/scans/EP001.audio_hints.json
```

## 5. Legacy Whisper（可选）

`paths.whisper_cli` / `paths.whisper_model` / `paths.whisper_retry_model`
由 `local.json` 提供（环境变量可临时覆盖）。没有 Whisper 也能用
`--skip-whisper` + Qwen/仅翻译。

## 6. Python 依赖（可选增强）

```bash
python -c "import jamdict"   # 日语词典分类
python -c "import janome"    # 日语分词
python -c "import jieba"     # 中文分词
```

缺依赖时对应步骤降级，不影响主翻译/时间轴。

## 7. 本机配置 / 隐私

- 所有绝对路径、模型路径、API 配置只写 **harness 副本的 `local.json`**
  （或临时环境变量）；仓库、SKILL.md、references 里不出现机器路径/密钥。
- `sync_skill.py` 不会覆盖目标里的 `local.json`；换机器重新跑
  `init_local.py --force` 即可。
- `local.json` 含密钥，只在本机保存，不要提交/分享。

## 8. 快速验证

仓库里（需要测试依赖）：

```bash
python -m pytest -q tests
```

harness 副本里：

```bash
python scripts/harness/init_local.py --check
```

期望全绿/无 problems；有失败先修环境，不要继续跑 pipeline。

## 9. 隐私检查

`ash
python scripts/harness/privacy_check.py
``n
扫描 git 跟踪文件里的密钥模式与 home 路径；返回非 0 就先修，再提交。

