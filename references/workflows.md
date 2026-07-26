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

## 工具速查

| 工具 | 输入 | 输出 | 用途 |
|------|------|------|------|
| `whisper_batch_transcribe.py` | 视频 | SRT | 从零生成字幕 |
| `translate_srt.py` | SRT（源语言） | SRT（目标语言） | 翻译字幕 |
| `run_all.py` | SRT（有问题）+ 视频 | SRT（修复后） | 修复乱码/碎片/缺失 |
| `auto_translate.py` | SRT + 映射表 | candidates.json | 专名一致性审查 |
| `unified_scanner.py` | SRT | findings.json + glossary | 扫描乱码 + 生成词表 |
| `oped_fixer.py` | SRT | oped_fixes.json | OP/ED 文本统一 |
| `oped_fill.py` | SRT（空白行）+ 视频 | SRT（填词） | OP/ED 空白行填充 |
