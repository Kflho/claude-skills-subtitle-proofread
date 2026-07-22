# 运行参考

> 独立命令、环境验证、调试指南。

## 环境验证

首次使用或更换机器后，确认以下检查通过：

### Whisper 后端检测（自动）

```bash
cd "<project>" && python -c "
from lib.whisper_backends import backend_detection_report
import json
print(json.dumps(backend_detection_report(), ensure_ascii=False, indent=2))
"
```

### whisper.cpp 后端

```bash
# 1. whisper-cli 可执行
<whisper-cli> --version

# 2. 模型文件存在
ls <model-dir>/ggml-kotoba-whisper-v2.0-q5_0.bin

# 3. ffmpeg 可用
ffmpeg -version
```

### faster-whisper 后端

```bash
# 1. faster-whisper 已安装
python -c "import faster_whisper; print('OK')"

# 2. 模型可用（自动下载或本地路径）
ls <model_dir>/  # CTranslate2 格式：model.bin + config.json + tokenizer.json + ...

# 3. ffmpeg 可用
ffmpeg -version
```

### openai-whisper 后端

```bash
# 1. openai-whisper 已安装
python -c "import whisper; print('OK')"

# 2. PyTorch 可用
python -c "import torch; print(torch.cuda.is_available())"

# 3. ffmpeg 可用
ffmpeg -version
```

> 路径从 CLAUDE.md 获取，不在此硬编码。

## 人声分离（可选但推荐）

用 demucs AI 模型分离人声，去除 BGM/音效后再送 Whisper。显著减少幻觉。

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
pip install demucs
```

测试效果：
```bash
# 提取 30 秒测试音频
ffmpeg -y -ss 00:08:30 -t 30 -i "<视频>.mkv" -vn -ac 1 -ar 16000 _test_original.wav

# 分离人声
python -m demucs --two-stems=vocals -o _test_sep _test_original.wav

# 对比 Whisper 转录效果（whisper.cpp 示例）
whisper-cli -m <model> -l ja -f _test_original.wav --no-timestamps
whisper-cli -m <model> -l ja -f _test_sep/htdemucs/_test_original/vocals.wav --no-timestamps
```

## Whisper 后端切换

skill 支持三种 Whisper 实现，初始化时自动检测。如需切换：

```bash
# 设置后端类型
export WHISPER_BACKEND='faster-whisper'  # 或 whisper-cpp / openai-whisper

# 后端特定配置（以 faster-whisper 为例）
export WHISPER_MODEL='kotoba-tech/kotoba-whisper-v2.0'
export WHISPER_RETRY_MODEL='deepdml/faster-whisper-large-v3-turbo-ct2'
```

| 后端 | 模型格式 | 速度 | GPU | 安装难度 |
|------|---------|:---:|:---:|:---:|
| whisper.cpp | GGML `.bin` | ★★★★ | CUDA/Metal/Vulkan | 中（下载exe+模型） |
| faster-whisper | CTranslate2 目录 | ★★★★★ | CUDA (CTranslate2) | 低（pip install） |
| openai-whisper | PyTorch `.pt` | ★★★ | CUDA (PyTorch) | 低（pip install） |

回退机制：主模型失败 → `RETRY_MODEL` 重试 → 仍失败则跳过该片段，不阻塞 pipeline。

## Phase 1：扫描

```bash
cd "<project>" && python "<scripts>/scan/unified_scanner.py" \
  --target-dir AI审查后/ --output-findings temp/scans/findings.json --project-lang ja
```

扫描是只读的。不写入报告。

## Phase 2：Triage（Whisper → 分类 → 修复）

### 分诊逻辑

```
eval_text = Whisper 输出（或原文本如果 Whisper 失败）

① meaningful_jp_count(eval_text) < 2  → auto-cut（纯拉丁、裸感叹词）
② looks_like_plausible_japanese()      → auto-keep（写 SRT ✅）
③ 其余（有日文 + 拉丁污染）             → AI fragment 补全
```

AI 无法修复的 → VAD 检查 → 无语音则 auto-cut / 有语音则人工审查。

### 独立命令

```bash
# 单集 Whisper 修复
cd "<project>" && python "<scripts>/fix/fix_orchestrator.py" EP005 --step whisper

# 检查单集是否干净
cd "<project>" && python "<scripts>/fix/fix_orchestrator.py" EP005 --step check

# 生成审查清单 + 视频片段
cd "<project>" && python "<scripts>/fix/fix_orchestrator.py" EP005 --step review
```

## Phase 3：专名统一 + 批量修复

### OP/ED 修复

```bash
# 自动清理 instrumental OP/ED
python "<scripts>/fix/oped_fixer.py" AI审查后/ --lang ja --auto-only -o temp/scans/oped_fixes.json

# 完整（auto-clean + AI review）
python "<scripts>/fix/oped_fixer.py" AI审查后/ --lang ja \
  -o temp/scans/oped_fixes.json --ai-review temp/scans/oped_ai_review.json
```

### 专名词表维护

```
build_glossary.py      → JMdict 过滤 → proper-nouns.md
auto_clean_glossary.py → 启发式清理 → 更新 COMMON_KANJI/KATAKANA
noun_checker.py        → 扫描 SRT 发现变体
auto_classify.py       → ACCEPT/REJECT/NEEDS_AI
```

```bash
# 生成词表
python "<scripts>/nouns/build_glossary.py" --findings temp/scans/findings.json \
  -o reports/proper-nouns.md --lang ja

# 自动清理
python "<scripts>/nouns/auto_clean_glossary.py" --glossary reports/proper-nouns.md --apply --yes

# 扫描变体
python "<scripts>/nouns/noun_checker.py" AI审查后/ --lang ja \
  --noun-table reports/proper-nouns.md -o temp/scans/nouns/

# 自动分类
python "<scripts>/nouns/auto_classify.py" --candidates temp/scans/noun_candidates.json \
  --lang ja --output temp/scans/noun_classified.json
```

### 应用修复

```bash
python "<scripts>/apply/apply_fixes.py" --target-dir AI审查后/ \
  --fixes temp/scans/all_fixes.json --lang ja
```

## 工具命令

```bash
# 清理空 cue
python "<scripts>/utils/clean_empty_cues.py" --target-dir AI审查后/

# 报告摘要
python "<scripts>/utils/update_report.py" reports/问题解决报告.md --summary
```

## 项目特征 → 执行方式

| 有... | 自动启用 |
|-------|---------|
| 视频文件 | Whisper 音频修复 |
| 参考字幕 | AI 校对注入参考文本 |
| 两者都有 | 两者都用 |

> 语言差异通过 `--lang ja|zh` 处理，工作流本身不分支。
