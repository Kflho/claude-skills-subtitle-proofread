# Phase 3 — 专名统一 + 交付命令参考

> 加载时机：Phase 3 执行时。AI 按需查阅。

## 专名词表维护

AI 直审全文，无启发式预筛选。名词库通常 30-100 条，一次性审查。

```
build_glossary.py      → 词频统计 → proper-nouns.md
AI glossary review      → 读全文 → 管理 PROPER_NOUNS_WHITELIST / COMMON_KANJI/KATAKANA
noun_checker.py        → 扫描 SRT 发现变体
AI candidate review    → 所有候选项直送 AI（≤50条）
```

### 命令

```bash
# 生成词表
python "<scripts>/nouns/build_glossary.py" --findings temp/scans/findings.json \
  -o reports/proper-nouns.md --lang ja

# 扫描变体
python "<scripts>/nouns/noun_checker.py" AI审查后/ --lang ja \
  --noun-table reports/proper-nouns.md -o temp/scans/nouns/
```

> `auto_clean_glossary.py` 和 `auto_classify.py` 仍可用作独立工具，但 pipeline 不再调用。
> AI 直接审查全文，开销合理且判断更准确。

## 应用修复

```bash
python "<scripts>/apply/apply_fixes.py" --target-dir AI审查后/ \
  --fixes temp/scans/all_fixes.json --lang ja
```

### 内置转换

| 转换 | 条件 | 实现 |
|------|------|------|
| 繁→简 | `--lang zh` 自动 | `str.maketrans()` 150+ 字符映射 |
| 翻译腔去机械化 | `--lang zh` | 15 条 EN→ZH 正则替换 |
| 空白 cue 清理 | 自动 | 删除 text 为空的 cue |

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

### 格式示例

```json
[
  {"action": "replace_global", "original": "...", "replacement": "...", "note": "..."},
  {"action": "replace_global_regex", "pattern": "...", "replacement": "...", "note": "..."},
  {"action": "replace_text", "file": "...", "line": N, "replacement": "..."},
  {"action": "delete_line", "file": "...", "line": N},
  {"action": "delete_style", "style": "..."}
]
```

## 交付

### [???] 标记审查

无法自动修复的条目由 `apply_ai_fragments()` 写入 `[???]` 标记到 SRT。
在 Aegisub 中打开字幕文件 → Search → Find → `[???]` → 逐条审查修复。

```bash
# 统计当前 [???] 标记数量
grep -c '\[???\]' AI审查后/*.srt
```

## 工具命令

```bash
# 清理空 cue
python "<scripts>/utils/clean_empty_cues.py" --target-dir AI审查后/

# 报告摘要（禁止直接 cat/Read！）
python "<scripts>/utils/update_report.py" reports/问题解决报告.md --summary
```

## ASS 格式修补（仅 ASS 项目）

```bash
python "<scripts>/ass/ass_repair.py" --target-dir AI审查后/ --check all
```

5 种检查：`names` / `styles` / `drawing` / `comment` / `oped`
