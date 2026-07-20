# Whisper 音频修复管线

> 加载条件：视频文件 + whisper.cpp CLI + GGML 模型。
> 入口脚本：`unified_scanner.py` 生成 per-episode issues → Whisper 三层递进修复。

## 前置检查

```bash
ls D:/software/video/whisper-cublas-*/whisper-cli.exe
ls D:/software/video/whisper-cublas-*/models/ggml-kotoba-whisper-v2.0-q5_0.bin
```

## 执行流程

### 步骤 1：收集问题

`unified_scanner.py` 同时输出 per-episode issue JSONs，替代旧的 `issue_tracker.py`：

```bash
python unified_scanner.py --target-dir ./AI审查后/ \
  --output-findings findings.json \
  --output-issues issues/
```

### 步骤 2：逐集运行 Whisper

```bash
# ⚠️ 修改 SRT 前必须先备份！
git add -A && git commit -m "备份：Whisper处理 {集号}"

# Tier 1: 集群切片重转录（默认首选）
python whisper_transcribe.py <video> <srt> \
  --whisper-cli .../whisper-cli.exe --model .../kotoba-q5_0.bin \
  --retry-model .../large-v3-q5_0.bin \
  --update-report reports/ --json

# Tier 2: 整集重转录（碎片≥15条的集）
python whisper_full_episode.py <video> <srt> \
  --whisper-cli ... --model .../kotoba-q5_0.bin \
  --update-report reports/ --json

# Tier 3: silencedetect 拆分修复（Tier 1+2 后仍有残留）
python whisper_deep_fix.py \
  --report reports/问题解决报告.md --srt-dir AI审查后/ \
  --video-dir reports/manual-review/ \
  --whisper-cli ... --model .../large-v3-q5_0.bin
```

### 步骤 3：审查 Whisper 输出

Claude 审查对比结果：
- ✅ 可直接替换：Whisper 输出为合理日语句子
- ⚠️ 需人工确认：Whisper 输出语义可疑
- ❌ 无法修复：音频质量过差

### 步骤 4：应用修复 + 导出审查清单

`--update-report` 自动生成：
- 已修复条目（步骤15 ✅）
- 待人工审查条目（步骤16 ⬜）
- 视频切片 → `reports/manual-review/`
- 删除候选 → `reports/whisper_delete_candidates.json`

审查完成后：
```bash
python apply_review_fixes.py review-checklist.md --srt-dir ./AI审查后/
```

## 性能参考（RTX 3080 Ti + kotoba-whisper-v2.0 q5_0）

| 单集音频段 | 转录耗时 | 实时倍率 |
|-----------|---------|:------:|
| ~400s (7分钟) | ~11s | 36× |

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
- 碎片化 cue（用 merge_cues 合并）
