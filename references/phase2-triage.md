# Phase 2 — 分诊修复命令参考

> 加载时机：Phase 2 执行时。AI 按需查阅。
> 环境验证和依赖安装由 first-run 处理，此处不重复。

## 分诊逻辑

```
eval_text = Whisper 输出（或原文本如果 Whisper 失败）

① meaningful_jp_count(eval_text) < 2  → auto-cut（纯拉丁、裸感叹词）
② looks_like_plausible_japanese()      → auto-keep（写 SRT ✅）
③ 其余（有日文 + 拉丁污染）             → AI fragment 补全
```

AI 无法修复的 → VAD 检查 → 无语音则 auto-cut / 有语音则人工审查。

## 一键全流程

```bash
cd "<project>"
python "<scripts>/run_all.py" \
  --lang ja \                    # ja | zh | auto
  --input-dir "<SUBTITLE_DIR>" \ # 字幕子目录（默认 AI审查后，用 . 表示直接路径）
  [--video-dir "<VIDEO_DIR>"] \  # 可选；无视频加 --skip-whisper
  [--skip-whisper]               # 残血运行：跳过音频修复
```

常用变体：`--limit N`（前N集）、`-e EP001-EP010`（指定范围）、`--dry-run`、`--force-rescan`。

> ⚠️ `--apply-ai-review` 和 `--apply-checklist` 是后处理快速路径，不能和 full run 一起用。

## Whisper 管线

### 后端自动检测

```bash
cd "<project>" && python -c "
from lib.whisper_backends import backend_detection_report
import json
print(json.dumps(backend_detection_report(), ensure_ascii=False, indent=2))
"
```

### 逐集处理

```bash
# 单集 Whisper 修复
cd "<project>" && python "<scripts>/fix/fix_orchestrator.py" EP005 --step whisper

# 检查单集是否干净
cd "<project>" && python "<scripts>/fix/fix_orchestrator.py" EP005 --step check

# 生成审查清单 + 视频片段
cd "<project>" && python "<scripts>/fix/fix_orchestrator.py" EP005 --step review
```

### Tier 升级阈值

| 条件 | 动作 |
|------|------|
| Tier 1 后残留碎片 ≥ 15 | 升级到 Tier 2 |
| Tier 1 后残留碎片 < 15 且类型为 pure_romaji | Claude 逐条审查，词典修复 |
| Tier 2 后仍有残留 | 升级到 Tier 3 |
| Tier 3 后仍有残留 | 标记为「音频质量差，无法自动修复」→ 人工交付 |

### 优先级排序

| 优先级 | issue 数 | 处理策略 |
|--------|---------|---------|
| **P0** | ≥ 40 | 最优先，Tier 1 处理 |
| **P1** | 20–39 | 中等，Tier 1 处理 |
| **P2** | 5–19 | 轻度，Tier 1 处理 |
| **P3** | < 5 | 最后处理或跳过（视内容而定） |

## AI 碎片补全

**触发**: `[ai-review] N pending`

```bash
# 1. 读 ai_fragments_{EP}.json
# 2. 填每个 fragment 的 correction 字段
# 3. 应用
python run_all.py --apply-ai-review --video-dir "<VIDEO_DIR>"
```

> ⚠️ 必须带 `--video-dir`，否则无法为人工审查项提取视频片段。

## OP/ED 修复

```bash
# 自动清理 instrumental OP/ED
python "<scripts>/fix/oped_fixer.py" AI审查后/ --lang ja --auto-only -o temp/scans/oped_fixes.json

# 完整（auto-clean + AI review）
python "<scripts>/fix/oped_fixer.py" AI审查后/ --lang ja \
  -o temp/scans/oped_fixes.json --ai-review temp/scans/oped_ai_review.json

# 带参考字幕
python "<scripts>/fix/oped_fixer.py" AI审查后/ --lang ja -o temp/scans/oped_fixes.json \
  --ai-review temp/scans/oped_ai_review.json --reference <ref_sub_dir>/

# 应用 AI 审查结果
python "<scripts>/fix/oped_fixer.py" AI审查后/ --lang ja -o temp/scans/oped_fixes.json \
  --apply-ai-review temp/scans/oped_ai_review.json
```

## 人声分离（可选但推荐）

用 demucs AI 模型分离人声，去除 BGM/音效后再送 Whisper。显著减少幻觉。

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
pip install demucs
```

测试效果：
```bash
ffmpeg -y -ss 00:08:30 -t 30 -i "<视频>.mkv" -vn -ac 1 -ar 16000 _test_original.wav
python -m demucs --two-stems=vocals -o _test_sep _test_original.wav
whisper-cli -m <model> -l ja -f _test_original.wav --no-timestamps
whisper-cli -m <model> -l ja -f _test_sep/htdemucs/_test_original/vocals.wav --no-timestamps
```

## 回退机制

主模型失败 → `RETRY_MODEL` 重试 → 仍失败则跳过该片段，不阻塞 pipeline。

| 后端 | 模型格式 | 速度 | GPU | 安装难度 |
|------|---------|:---:|:---:|:---:|
| whisper.cpp | GGML `.bin` | ★★★★ | CUDA/Metal/Vulkan | 中（下载exe+模型） |
| faster-whisper | CTranslate2 目录 | ★★★★★ | CUDA (CTranslate2) | 低（pip install） |
| openai-whisper | PyTorch `.pt` | ★★★ | CUDA (PyTorch) | 低（pip install） |

## 纳排标准

### 纳入 Whisper（无法用文本规则修复）

| type | 判定 |
|------|------|
| `garbled` | 纯罗马字/英文乱码 |
| `mixed_romaji` | 日语中混入罗马字碎片 |
| `hallucination` | AI 将噪声转录为不可能出现的现代词 |
| `unintelligible` | 完全无法理解的音节堆砌 |

### 不纳入（仍用规则修复）

- 假名拼写错误（上下文可推断）
- 同音词错误（Claude 逐句判断）
- OP/ED 歌词（固定模式，全局替换）
- 碎片化 cue（用 `merge_cues` 合并）

### 跳过条件

以下情况可跳过该集 Whisper 处理：
- 全部 issue 类型为 `ai_noise` → romaji_fixer 已处理，无需重新转录
- issue ≤ 2 且全部为词典已覆盖的罗马字单词
- 该集不存在于 issues/ 目录中（无 issue = 文本层已完全修复）

## Whisper 置信度

### 三项指标

| 指标 | 来源 | 含义 | AI 审查阈值 |
|------|------|------|-----------|
| `avg_logprob` | token 平均对数概率 | 模型对转录的确信度 | < -1.0 |
| `no_speech_prob` | `<|nospeech|>` token 概率 | 该段是否静音 | > 0.4 |
| `compression_ratio` | gzip 压缩比 | 重复文本→幻觉标志 | > 2.0 |

### 三道防线过滤

1. `no_speech_prob > 0.6` → 丢弃
2. `avg_logprob < -1.5` → 丢弃
3. `compression_ratio > 2.4` → 丢弃

三项指标全部健康 → 直接应用 ✅。任一触发阈值 → AI 审查 ⬜。`confidence='none'` → 人工交付 ⬜。
