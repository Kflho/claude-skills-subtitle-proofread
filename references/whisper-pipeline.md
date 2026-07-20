# Whisper 音频修复管线

> 加载条件：视频文件 + whisper.cpp CLI + GGML 模型。
> 入口脚本：`unified_scanner.py` 生成 per-episode issues → Whisper 三层递进修复。
> **推荐**：搭配 `--separate-vocals`（需 Python 3.12 + PyTorch CUDA + demucs）减少 BGM 幻觉。

## 批量处理策略

> 每次执行完整流程时自动遵循此策略，无需每次手动决策。

### 决策树

```
issues/ 目录包含 N 集 per-episode JSON →
  1. 统计每集 issue 数，按严重程度排序
  2. Tier 1（集群重转录）— 所有有 issue 的集，按 P0→P3 顺序
  3. Tier 1 后残留碎片 ≥15 条 → Tier 2（整集重转录）
  4. Tier 2 后仍有残留 → Tier 3（深修）；否则跳过
  5. Git 备份：Tier 1 前、Tier 2 前、Tier 3 后 各一次
```

### 优先级排序

| 优先级 | issue 数 | 处理策略 |
|--------|---------|---------|
| **P0** | ≥ 40 | 最优先，Tier 1 处理 |
| **P1** | 20–39 | 中等，Tier 1 处理 |
| **P2** | 5–19 | 轻度，Tier 1 处理 |
| **P3** | < 5 | 最后处理或跳过（视内容而定） |

### 跳过条件

以下情况可跳过该集 Whisper 处理：
- 全部 issue 类型为 `ai_noise` → romaji_fixer 已处理，无需重新转录
- issue ≤ 2 且全部为词典已覆盖的罗马字单词（me/re/ni/dare 等）
- 该集不存在于 issues/ 目录中（无 issue = 文本层已完全修复）

### 逐集流程

```bash
# 对每个 issue 文件，找到对应的视频和 SRT：
# 1. 从 issues/issues_EPxxx.json 中读取 ep 字段
# 2. 在 AI审查后/ 中找对应 SRT（用 extract_ep_number 匹配）
# 3. 在视频目录中找对应视频（同样匹配）
# 4. 运行 Tier 1 whisper_transcribe.py
# 5. 检查残留碎片数，决定是否升级到 Tier 2
```

### 性能参考

| GPU | 模型 | 单段 (~15s) | 典型集 (25 段) | 125 集全量 |
|-----|------|:----------:|:-------------:|:---------:|
| RTX 3080 Ti | kotoba q5_0 | ~3s | ~1–2 min | **2–4 h** |
| RTX 3080 Ti | large-v3 q5_0 | ~8s | ~3–5 min | **6–10 h** |
| + demucs | — | +2s/30s | +30s | +~1 h |

> `--separate-vocals` 显著减少 BGM 触发的幻觉，建议至少对 P0 集使用。

### Tier 升级阈值

| 条件 | 动作 |
|------|------|
| Tier 1 后残留碎片 ≥ 15 | 升级到 Tier 2 |
| Tier 1 后残留碎片 < 15 且类型为 pure_romaji | Claude 逐条审查，词典修复 |
| Tier 2 后仍有残留 | 升级到 Tier 3 |
| Tier 3 后仍有残留 | 标记为「音频质量差，无法自动修复」→ 步骤16 人工审查 |

### 分批建议

大项目（50+ 集）建议分批处理：

```bash
# 批次 1: P0 集（issue ≥ 40） — 通常 ~10-15 集
# 批次 2: P1 集（issue 20-39） — 通常 ~20-30 集
# 批次 3: P2 集（issue 5-19） — 通常 ~40-50 集
# 批次 4: P3 集（issue < 5） — 余下全部

# 每批处理完成后 git commit 备份
# 每批结束后检查 --update-report 更新
```

### 统计命令

```bash
# 生成 issue 统计并按优先级排序
python -c "
import json, os
issues_dir = 'issues/'
eps = []
for f in os.listdir(issues_dir):
    if f.endswith('.json'):
        data = json.load(open(os.path.join(issues_dir, f), encoding='utf-8'))
        eps.append((data['episode'], data['issue_count']))
eps.sort(key=lambda x: -x[1])
p0 = [e for e in eps if e[1] >= 40]
p1 = [e for e in eps if 20 <= e[1] < 40]
p2 = [e for e in eps if 5 <= e[1] < 20]
p3 = [e for e in eps if e[1] < 5]
print(f'P0 (≥40): {len(p0)} 集 — {[e[0] for e in p0]}')
print(f'P1 (20-39): {len(p1)} 集')
print(f'P2 (5-19): {len(p2)} 集')
print(f'P3 (<5): {len(p3)} 集')
print(f'总计: {len(eps)} 集, {sum(e[1] for e in eps)} issues')
"
```

## 前置检查

```bash
ls D:/software/video/whisper-cublas-*/whisper-cli.exe
ls D:/software/video/whisper-cublas-*/models/ggml-kotoba-whisper-v2.0-q5_0.bin

# 人声分离（可选但推荐）
python --version                          # Python 3.12.x
python -c "import torch; print(torch.cuda.is_available())"  # CUDA: True
python -c "import demucs; print('OK')"    # demucs OK
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
  --update-report reports/ --json \
  --separate-vocals

# Tier 2: 整集重转录（碎片≥15条的集）
python whisper_full_episode.py <video> <srt> \
  --whisper-cli ... --model .../kotoba-q5_0.bin \
  --update-report reports/ --json \
  --separate-vocals

# Tier 3: silencedetect 拆分修复（Tier 1+2 后仍有残留）
python whisper_deep_fix.py \
  --report reports/问题解决报告.md --srt-dir AI审查后/ \
  --video-dir reports/manual-review/ \
  --whisper-cli ... --model .../large-v3-q5_0.bin \
  --separate-vocals
```

> **`--separate-vocals`**：转录前先用 demucs 分离人声，去除 BGM/音效。
> 实测可显著减少 Whisper 幻觉（BGM 是幻觉主要触发源）。需 Python 3.12 + PyTorch CUDA + demucs（见 `setup-guide.md` 第四步）。
> 每 30 秒音频约需 2 秒分离（RTX 3080 Ti），不含分离的纯转录耗时不变。

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
