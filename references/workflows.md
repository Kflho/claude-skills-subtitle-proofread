# 工作流场景

典型场景及工具组合。每个场景标记**起点**（你有什么）→ **终点**（你要什么）。

## 场景 1：从视频生成字幕

> 起点：视频文件，无任何字幕 → 终点：SRT 字幕文件

```bash
python "<scripts>/whisper_batch_transcribe.py" \
  --video-dir "<VIDEO_DIR>" \
  --output-dir "<OUTPUT_DIR>" \
  --lang ja

# 先测试 3 集
python "<scripts>/whisper_batch_transcribe.py" \
  --video-dir "<VIDEO_DIR>" \
  --output-dir "<OUTPUT_DIR>" \
  --lang ja --limit 3
```

Whisper 自带 VAD 分段，直接从视频音频生成 SRT。无需已有字幕文件。

## 场景 2：翻译字幕

> 起点：源语言 SRT（如日文）→ 终点：目标语言 SRT（如中文）

```bash
# 无专名映射（首次翻译）
python "<scripts>/translate_srt.py" \
  --input-dir "<SRC_DIR>" \
  --output-dir "<OUT_DIR>"

# 带专名映射（确保专名译法一致）
python "<scripts>/translate_srt.py" \
  --input-dir "<SRC_DIR>" \
  --output-dir "<OUT_DIR>" \
  --mappings temp/noun_mappings.json
```

源语言自动检测（ja/zh/ru 等）。建议翻译前先做专名审查（见场景 4），确保译法一致。

## 场景 3：修复字幕

> 起点：有问题的 SRT（乱码、碎片化、缺字幕）→ 终点：修复后的 SRT

```bash
# Full pipeline：扫描 + VAD + Whisper 修复 + 专名统一
python "<scripts>/run_all.py" \
  --input-dir "<SUBTITLE_DIR>" \
  --video-dir "<VIDEO_DIR>"

# 仅扫描预演（不改文件）
python "<scripts>/run_all.py" \
  --input-dir "<SUBTITLE_DIR>" \
  --video-dir "<VIDEO_DIR>" \
  --dry-run
```

Pipeline 修复三类问题：乱码 cue、碎片化 cue（部分覆盖）、缺失 cue（有人声无字幕）。结果直接写入 SRT。

> **注意**：`run_all.py` 是修复 pipeline，需要已有 SRT 作为起点。如果完全没有字幕文件，用场景 1 的 `whisper_batch_transcribe.py`。

## 已知问题

### Whisper 幻觉重复
音乐/噪声段 Whisper 会产生连续多条相同或高度相似的 cue。
- ❌ 切片重跑 Whisper **无效**（根因是音频本身触发幻觉，重跑只换一组幻觉）
- ✅ 正确做法：翻译阶段告知 LLM 这是 bug，照常翻译每条，最终人工审查决定保留哪条
- 🔧 检测可用（`fix_repeated_cues.py` 的相似度扫描），修复不可用

## 场景 4：专名统一

> 起点：翻译后的 SRT（专名译法不一致）→ 终点：统一专名的 SRT

```bash
# 生成专名词表（在翻译前做 — 推荐）
python "<scripts>/scan/unified_scanner.py" \
  --target-dir "<SOURCE_SRT_DIR>" \
  --build-glossary --glossary-output reports/proper-nouns.md \
  --project-lang ja

python "<scripts>/nouns/build_glossary.py" \
  --findings temp/scans/findings.json \
  -o reports/proper-nouns.md \
  --mappings-output temp/noun_mappings.json

# 🤖 编辑 temp/noun_mappings.json（ja→zh 映射）

# 翻译后复查专名一致性
python "<scripts>/auto_translate.py" \
  --source-dir "<SOURCE_SRT_DIR>" \
  --target-dir "<TRANSLATED_SRT_DIR>" \
  --mappings temp/noun_mappings.json
```

`auto_translate.py` 反复运行自动推进：有 candidates → 修复 → 重跑 → candidates 归零即完成。

## 场景 5：完整流程（视频 → 成品中文）

> 起点：仅有视频 → 终点：中文 SRT（专名统一、翻译一致）

> 快捷粗剪可用 `video_to_zh.py`（只做 A 转写 + C 翻译）；它不是下面 A–D 的替代。
> 要专名审查、auto_translate、OP/ED、润色，仍走本场景完整流程。

```
Phase A: Whisper 转录
  whisper_batch_transcribe.py  →  日语参考字幕/

Phase B: 专名审查（翻译前）
  unified_scanner + build_glossary  →  noun_mappings.json
  🤖 AI 审查词表

Phase C: 翻译
  translate_srt.py --mappings  →  ai翻译XXXX/

Phase D: 中文校验
  auto_translate.py  →  专名一致性复查
  oped_fixer.py      →  OP/ED 处理
```

### 实际命令

```bash
cd "<PROJECT_DIR>"

# A: 生成日文 SRT
python "<scripts>/whisper_batch_transcribe.py" \
  --video-dir "<VIDEO_DIR>" --output-dir "日语参考字幕" --lang ja

# B: 专名审查
python "<scripts>/scan/unified_scanner.py" \
  --target-dir "日语参考字幕" --build-glossary \
  --glossary-output reports/proper-nouns.md --project-lang ja
python "<scripts>/nouns/build_glossary.py" \
  --findings temp/scans/findings.json \
  -o reports/proper-nouns.md --mappings-output temp/noun_mappings.json
# 🤖 AI 审查 temp/noun_mappings.json

# C: 翻译
python "<scripts>/translate_srt.py" \
  --input-dir "日语参考字幕" --output-dir "ai翻译XXXX" \
  --mappings temp/noun_mappings.json

# D: 中文校验
python "<scripts>/auto_translate.py" \
  --source-dir "日语参考字幕" --target-dir "ai翻译XXXX" \
  --mappings temp/noun_mappings.json
python "<scripts>/fix/oped_fixer.py" "ai翻译XXXX" \
  --lang zh --detect-boundaries --auto-only -o temp/scans/oped_fixes.json
```

## 场景 6：OP/ED 唱词定稿（音频定窗口 → 画面复核 → 换定本）

> 起点：SRT 里 OP/ED 段是 ASR 乱写的唱词；终点：该段换成人工定本，时间对得上演唱。
> 顺序不能倒过来——**先定时间，再换文本**；从唱词猜窗口会被「冷えたわけじゃない」这类误听带偏。

```bash
# 1) 参考集的 OP/ED 时间（先看帧确认：片头应落在报幕/标题卡）
python "<scripts>/audio/oped_boundaries.py" --video-dir "<VIDEO_DIR>" \
  --op-ref "<EP001.avi>@40-110" --ed-ref "<EP001.avi>@1442-1486" \
  --output data/temp/oped_windows.json --frames-dir data/temp/oped_frames

# 2) 复核 data/temp/oped_frames/<EP>_{op,ed}_{start,mid,end}.png
#    同一部片子的 OP/ED 曲子通常每集一样 → 分数应 ≥0.9；掉到 0.5 以下说明换曲/换剪辑

# 3) 窗口内换定本歌词（一行一句的 txt；默认只写副本，确认后 --in-place）
python "<scripts>/fix/oped_region.py" "<中文目录>" \
  --windows data/temp/oped_windows.json \
  --op-lines data/temp/op_zh.txt --ed-lines data/temp/ed_zh.txt \
  --out-dir data/temp/oped_out
```

判断规则：

- 定本行数按**实际演唱句数**给（片头有烧录歌词的片子最好数：一屏一句）；
- 时长按行字数分配，别用 ASR 的 cue 边界——那些块长能从 1 秒跳到 16 秒；
- 替换后该段条数会变（合并），**不要**再拿「与日文源条数一致」去卡 OP/ED；
- 只有文本不统一（不是乱码）时，才用 `oped_fixer.py` + `nouns/context_review.py --mode oped` 的文本键控路线。

## 工具速查

| 工具 | 输入 | 输出 | 用途 |
|------|------|------|------|
| `whisper_batch_transcribe.py` | 视频 | SRT | 从零生成字幕（legacy） |
| `asr/qwen3_transcribe.py` | 视频/音频 | SRT + ASR JSON | Qwen3 转写（单文件） |
| `video_to_zh.py` | 视频目录 | `ja/` + `zh/` + qa | 整季一键（跳过已有 ja） |
| `translate_srt.py` | SRT（源语言） | SRT（目标语言）+ QA | 翻译字幕 |
| `run_all.py` | SRT（有问题）+ 视频 | SRT（修复后） | 修复乱码/碎片/缺失 |
| `auto_translate.py` | SRT + 映射表 | candidates.json | 专名一致性审查 |
| `unified_scanner.py` | SRT | findings.json + glossary | 扫描乱码 + 生成词表 |
| `audio/oped_boundaries.py` | 视频目录 + 参考 OP/ED 段 | `oped_windows.json` + 边界帧 | OP/ED 时间段（音频指纹） |
| `fix/oped_region.py` | SRT + 窗口 + 定本歌词 | SRT（替换后） | OP/ED 唱词定稿 |
| `oped_fixer.py` | SRT | oped_fixes.json | OP/ED 文本统一（变体） |
| `oped_fill.py` | SRT（空白行）+ 视频 | SRT（填词） | OP/ED 空白行填充 |
