# 检测脚本速查

> 完整配置示例见各脚本 `--help`。所有脚本遵循 **检测 → JSON → Claude 审查 → fixes.json → apply_fixes.py** 流程。

## 统一扫描（推荐入口）

```bash
# 字符层统一扫描 — 单次遍历替代 bilingual + source_lang + source_char + issue_tracker
python unified_scanner.py --target-dir <DIR> \
  --output-findings findings.json \
  --output-issues issues/ \
  --output-delete delete_candidates.json

# 从扫描结果生成修复
python romaji_fixer.py --findings findings.json --output fixes.json
python romaji_fixer.py --findings findings.json --mode dict-only --output dict_fixes.json
python romaji_fixer.py --findings findings.json --mode delete-only --output delete_fixes.json
```

## 语义层检测脚本

```bash
# 卡死重复
python repeat_detect.py --target-dir <DIR>

# 机翻幻觉（仅中文目标语言项目）
python garbled_detect.py --target-dir <DIR> --config garbled_config.json

# 固定格式变体（仅翻译项目）
python format_detect.py --target-dir <DIR> --config format_config.json

# 专有名词（需要参考字幕 --ref-dir）
python proper_noun_detect.py --target-dir <DIR> --ref-dir <REF_DIR>

# OP/ED 时间码聚类（无依赖，SRT 兼容）
python oped_timecode_detect.py --srt-dir <DIR>
```

## ASS 专用脚本

```bash
# Name 字段语言分类
python names_detect.py --target-dir <DIR> --lang-check

# 样式统计
python styles_detect.py --target-dir <DIR>

# 绘图指令检测
python drawing_detect.py --target-dir <DIR>

# Comment 行外语残留
python comment_detect.py --target-dir <DIR> --langs en,jp,ru
```

## 中文翻译项目脚本

```bash
# 繁体→简体（--auto 直接转换）
python trad_to_simp_detect.py --target-dir <DIR> --auto

# 感叹词残留
python interjection_detect.py --target-dir <DIR> --config interj_config.json
```

## 工具脚本

```bash
# 清理空 cue
python clean_empty_cues.py --target-dir <DIR>

# 应用修复
python apply_fixes.py --target-dir <DIR> --fixes fixes.json

# 审查清单→SRT（含 VAD 打轴）
python apply_review_fixes.py review-checklist.md --srt-dir <DIR>

# 报告查询
python update_report.py reports/问题解决报告.md --summary
```

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
