# 检测脚本速查

> 完整配置示例见各脚本 `--help`。所有脚本遵循 **检测 → JSON → Claude 审查 → fixes.json → apply_fixes.py** 流程。

## 统一扫描（推荐入口，所有项目）

```bash
# 字符层统一扫描 — 单次遍历：garbled + repeat + 术语收集
python unified_scanner.py --target-dir <DIR> \
  --output-findings temp/scans/findings.json \
  --output-issues temp/scans/issues/ \
  --build-glossary

# 等价于旧版独立运行（已删除，功能已合并）:
#   bilingual_detect.py + source_lang_detect.py + source_char_detect.py
#   + repeat_detect.py + issue_tracker.py + romaji_fixer.py
```

## 语义层检测脚本（翻译项目）

```bash
# 机翻幻觉（--lang 加载对应语言的预设模式）
python garbled_detect.py --target-dir <DIR> --lang zh > garbled_findings.json

# 固定格式变体（--lang 加载对应语言的预设模式）
python format_detect.py --target-dir <DIR> --lang zh > format_findings.json

# 感叹词残留（需配置 source_char_pattern）
python interjection_detect.py --target-dir <DIR> --config interj_config.json

# 专有名词对照（需要 --ref-dir 参考字幕）
python proper_noun_detect.py --target-dir <DIR> --ref-dir <REF_DIR> > proper_nouns.json

# OP/ED 时间码聚类（语言无关，SRT/ASS 兼容）
python oped_timecode_detect.py --target-dir <DIR> --min-episodes 10
```

## ASS 格式修补

> ⚠️ SRT only 项目全部跳过。`episode_workflow.py --repair-ass` 自动调用。

```bash
# 一键运行所有检查
python ass_repair.py --target-dir <DIR> --check all

# 单独检查
python ass_repair.py --target-dir <DIR> --check names     # Name 字段语言分类
python ass_repair.py --target-dir <DIR> --check styles    # 样式统计
python ass_repair.py --target-dir <DIR> --check drawing   # 绘图指令检测
python ass_repair.py --target-dir <DIR> --check comment   # Comment 行外语残留
python ass_repair.py --target-dir <DIR> --check oped --oped-config config.json  # OP/ED 多样式对比
```

## 中文翻译项目脚本

> ⚠️ 非中文目标语言项目全部跳过。

```bash
# 繁体→简体（--auto 直接转换，推荐）
python trad_to_simp_detect.py --target-dir <DIR> --auto

# 或先检测再审查
python trad_to_simp_detect.py --target-dir <DIR> > trad_findings.json
```

## 工具脚本（通用）

```bash
# 清理空 cue
python clean_empty_cues.py --target-dir <DIR>

# 应用修复
python apply_fixes.py --target-dir <DIR> --fixes fixes.json

# 审查清单→SRT（含 VAD 打轴）
python apply_review_fixes.py review-checklist.md --srt-dir <DIR>

# 报告查询（禁止直接 cat/Read！）
python update_report.py reports/问题解决报告.md --summary
```

## 项目特征 → 脚本选用

| 项目特征 | 必做 | 可选 |
|---------|------|------|
| 所有项目 | `unified_scanner.py` | `oped_timecode_detect.py` |
| ASS 格式 | + `ass_repair.py --check all` | |
| 有参考字幕 | + `translate_srt.py` + `compare_srt.py` | `proper_noun_detect.py` |
| 翻译项目 | | `garbled_detect.py --lang <LANG>`, `format_detect.py --lang <LANG>`, `interjection_detect.py` |
| 中文目标 | | `trad_to_simp_detect.py --auto` |

> 语言差异通过 `--lang` 标志处理，工作流本身不分支。日文项目 `--lang ja` → 语义检测自动空操作。

## fixes.json action 速查

| action | 范围 | SRT | ASS |
|--------|------|:---:|:---:|
| `replace_text` | 逐行 | ✓ | ✓ |
| `replace_global` | 全局字符串 | ✓ | ✓ |
| `replace_global_regex` | 全局正则 | ✓ | ✓ |
| `delete_line` | 逐行 | ✓ | ✓ |
| `replace_name` | 逐行 Name 字段 | ✗ | ✓ |
| `delete_style` | 按样式删除 | ✗ | ✓ |
| `delete_comment` | 按关键词删 Comment | ✗ | ✓ |
| `merge_cues` | 合并相邻 cues | ✓ | ✗ |
