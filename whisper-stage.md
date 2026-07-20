# Whisper 音频修复阶段

> 加载条件：用户提供了视频文件目录（路径 #4）+ whisper.cpp（路径 #5）。
> 不依赖参考字幕或原语言字幕。

## 前置检查

确认以下资源可用：

```bash
# whisper.cpp 可执行文件
ls D:/software/video/whisper-cublas-*/whisper-cli.exe

# GGML 模型文件
ls D:/software/video/whisper-cublas-*/models/ggml-kotoba-whisper-v2.0-q5_0.bin

# whisper_transcribe.py 脚本
ls whisper_transcribe.py
```

## 每集问题清单

校对过程中，记录**仅靠文本规则无法修复**的问题，写入每集的 issue 文件。

### 格式

校对时为每集生成 `issues_EP{NNN}.json`：

```json
{
  "episode": "019",
  "srt_file": "日语ai生成字幕/철완 아톰 (Astro Boy)1963 - 019 EP. ... .srt",
  "video_file": "E:/Animation/TV/.../[Anonymoose] 鉄腕アトム - 019 - ... .mkv",
  "issues": [
    {
      "start": "00:01:50,680",
      "end": "00:01:52,100",
      "original_text": "therefore ん",
      "type": "garbled",
      "note": "AI罗马字乱码，无法从上下文推断正确文本"
    }
  ]
}
```

### 纳入条件（仅记录以下类型）

| type | 判定标准 | 示例 |
|------|----------|------|
| `garbled` | 纯罗马字/英文乱码，无明显日语含义 | `therefore ん`, `padiwh`, `nime7` |
| `mixed_romaji` | 日语中混入罗马字碎片 | `かすかに号車にも感じますconoha` |
| `hallucination` | AI 将无意义噪声转录为"单词" | `iphone 4を` (1963年不可能出现) |
| `unintelligible` | 完全无法理解的音节堆砌 | `えっはりなキャイー nd` |

### 不纳入

以下问题**仍用规则修复**，不进入 Whisper 阶段：
- 假名拼写错误（可用上下文推断）
- 同音词错误（如 扉→飛雄，Claude 逐句判断）
- OP/ED 歌词（固定模式，全局替换）
- 碎片化 cue（单音节拆分，用 merge_cues 合并）

### 自动扫描

```bash
python scripts/issue_tracker.py \
  --srt-dir ./日语ai生成字幕/ \
  --video-dir "E:/Animation/TV/..." \
  --output-dir ./issues/
```

---

## 执行流程

### 步骤 1：收集问题清单

扫描所有 `issues_EP*.json` 文件，统计：
- 总需处理集数
- 总乱码段数
- 按 type 分布

### 步骤 2：逐集运行 Whisper

对每个 `issues_EP*.json`：

```bash
# ⚠️ 修改 SRT 前必须先备份
git add -A && git commit -m "备份：Whisper处理 {集号}"

# 预览
python whisper_transcribe.py <video_file> <srt_file> \
  --whisper-cli ... --model ... --dry-run

# 执行修复 + 自动同步报告
python whisper_transcribe.py <video_file> <srt_file> \
  --whisper-cli ... --model ... \
  --retry-model .../large-v3-q5_0.bin \
  --update-report reports/ \
  --json
```

`--update-report` 会自动更新 `reports/问题解决报告.md`：步骤15 写入已修复条目（✅），步骤16 写入待人工审查条目（⬜）。同时标记旧 `whisper-pending.md` 为废弃。

**工作原理**：
1. 扫描 SRT 中的英文/罗马字乱码段（与 issue 清单交叉验证）
2. 从视频提取音频 → 切出乱码区域片段 → 拼接
3. 调用 whisper.cpp CLI（CUDA，日语 kotoba 模型）重转录
4. 输出原 YT 字幕 vs Whisper 转录的对比

**默认参数**：`t=8 p=2 bs=5 bo=8`（16 线程 / 80% CPU，GPU 加速，36× 实时）

### 步骤 3：审查 Whisper 输出

Claude 审查对比结果，判断：
- ✅ **可直接替换**：Whisper 输出为合理日语句子 → 写入 SRT
- ⚠️ **需人工确认**：Whisper 输出语义可疑或与上下文不一致 → 标记为「待确认」
- ❌ **无法修复**：音频质量过差，Whisper 也未匹配 → 保留原文或删除

### 步骤 4：应用修复

对确认的修复生成 `whisper_fixes.json`：

```json
[
  {"action": "replace_text", "file": "철완 아톰...019 EP...srt", "line": 42, "replacement": "かすかに放射能を感じる", "note": "Whisper修复: therefore ん"}
]
```

运行 `apply_fixes.py` 批量写入。

### 步骤 5：更新 `问题解决报告.md`

运行 `--update-report reports/` 自动同步。已修复条目写入步骤15（✅），未修复集群写入步骤16（⬜ 待人工审查）。旧的 `whisper-pending.md` 自动标记为废弃。

### 步骤 6：清理无法修复的乱码

`--update-report` 会自动生成 `reports/whisper_delete_candidates.json`，区分两类：

- **可删除**：纯罗马字短碎片（≤2 词，无假名/汉字），如 `poj`、`daren`。音乐/噪声误识别。
- **保留审查**：含日语假名/汉字的混合长句。

`whisper_delete_candidates.json` 为标准 fixes 格式，可直接喂给 `apply_fixes.py`：

```json
[
  {"action": "delete_line", "file": "철완 아톰...019...srt", "line": 463, "note": "噪声: daren"}
]
```

**Claude 必须询问用户是否删除**，列出建议删除项和保留项。用户同意后：

```bash
python scripts/apply_fixes.py --target-dir ./日语ai生成字幕/ \
  --fixes reports/whisper_delete_candidates.json
```

保留项留在 `问题解决报告.md` 步骤16，标记为 ⬜「待人工审查」。

---

### 步骤 7：导出视频切片 + 审查清单

`--update-report` 导出**视频切片**（带画面）和审查清单到 `reports/manual-review/`：

```
reports/manual-review/
├── review-checklist.md               ← 审查清单（一行文件，一行修正栏）
├── EP019_00-08-10_to_00-08-42.mp4    ← 视频切片（含画面+音频）
└── EP019_00-09-42_to_00-09-44.mp4
```

**审查清单格式**（审查员在「修正:」下方每行写一句台词，可多行）：

```markdown
EP019 | 00:08:10.569 ~ 00:08:42.810 | EP019_00-08-10_to_00-08-42.mp4
残留: えっはりなキャイー nd / hani / pa / uh uh
修正:
メンバーが怪物を生き返らせた
山を爆破したんだ
これは大変なことになった
```

**审查完成后**，运行批量应用脚本：

```bash
python scripts/apply_review_fixes.py review-checklist.md --srt-dir ./日语ai生成字幕/
```

脚本自动用 ffmpeg silencedetect 检测人声区间，为每行分配时间轴。修正行数 ≤ 人声段数时 1:1 分配，否则按比例均分。

---

## 性能参考（RTX 3080 Ti + kotoba-whisper-v2.0 q5_0）

| 单集音频段 | 转录耗时 | 实时倍率 |
|-----------|---------|:------:|
| ~400s (7分钟) | ~11s | 36× |
| 全193集扫描+转录 | ~35分钟 | — |

---

## Tier 2: 整集重转录（碎片≥15条的集）

对碎片密集的集，整集音频喂给 Whisper 获得完整上下文，然后按时间对齐到现有 SRT。

```bash
# ⚠️ 先备份
git add -A && git commit -m "备份：整集重转录 {集号}"

# 预览
python scripts/whisper_full_episode.py video.mkv sub.srt \
  --whisper-cli ... --model ... --dry-run --json

# 执行修复 + 更新报告
python scripts/whisper_full_episode.py video.mkv sub.srt \
  --whisper-cli ... --model ... \
  --update-report reports/ --json
```

**工作原理**：
1. 提取全片音频（WAV 16kHz mono 无损）
2. Whisper 整集转录 → 完整时间轴
3. 对齐算法：对每个乱码 cue，找时间重叠 ≥30% 的 whisper segment
4. whisper 文本为有效日语（含假名/汉字）→ 替换；否则保留

## Tier 3: 深度碎片拆分

对 Tier 1+2 后仍剩余的未修复碎片，用 ffmpeg silencedetect 拆分后逐个重转录。

```bash
# ⚠️ 先备份
git add -A && git commit -m "备份：深度碎片修复"

python scripts/whisper_deep_fix.py \
  --report reports/问题解决报告.md \
  --srt-dir 日语ai生成字幕/ \
  --video-dir reports/manual-review/ \
  --whisper-cli ... \
  --model .../large-v3-q5_0.bin
```

**工作原理**：
1. 读取统一报告步骤16（⬜ 待处理条目）
2. 找到对应的 mp4 视频切片
3. ffmpeg silencedetect 找 ≥1.5s 静音断点
4. 在断点处拆分子片段，每个单独喂 Whisper（-nth 0.3 极低阈值）
5. 结果回填 SRT + 更新统一报告步骤16 状态
