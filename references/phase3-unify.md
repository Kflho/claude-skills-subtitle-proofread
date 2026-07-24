# Phase 3 — 专名统一 + 交付命令参考

> 加载时机：Phase 3 执行时。AI 按需查阅。

## 专名词表维护

AI 直审全文，无启发式预筛选。名词库通常 30-100 条，一次性审查。

```
build_glossary.py      → 词频统计 → proper-nouns.md
AI glossary review      → 读全文 → 管理 PROPER_NOUNS_WHITELIST / COMMON_KANJI/KATAKANA
noun_checker.py        → 扫描 SRT 发现变体
find_suspect_nouns.py  → 全量扫描（无上限）→ candidates.json（统一 AI 审查格式）
AI candidate review    → 所有候选项直送 AI，逐条判断
```

### 统一专名审查（v2）

`find_suspect_nouns.py --mode translation --output-format candidates` 输出统一的 `candidates.json`：

```json
{
  "candidates": [
    {
      "id": 1,
      "type": "inconsistency",
      "ja_source": "アトム",
      "zh_canonical_in_mappings": "阿童木",
      "zh_appearances": [
        {"text": "阿童木", "count": 1212, "sample_locations": [...]},
        {"text": "阿托姆", "count": 4, "sample_locations": [
          {"file": "EP060.srt", "cue_index": 307, "timestamp": "00:16:22", "text": "跟着阿托姆走吧"}
        ]}
      ],
      "sample_contexts": [{"file": "EP060.srt", "zh_text": "跟着阿托姆走吧"}]
    },
    {
      "id": 2,
      "type": "unknown_suspect",
      "zh_term": "哈卡塞",
      "frequency": 55,
      "in_mappings": false,
      "sample_contexts": [...]
    }
  ],
  "stats": {"total": 150, "inconsistency": 20, "unknown_suspect": 130}
}
```

**v2 关键改进**：
- **全量扫描**：所有集数、所有候选，无 50 条 cap，无 20 集限制
- **jieba 预注册**：映射表中的中文专名注入 jieba 词典，避免拆分（如「阿托姆」→「阿」+「托姆」）
- **short-reading 阈值**：日语读音 substring 匹配要求 ≥4 字符，减少误聚类
- **降级模式**：无日文源时自动切换为中文侧启发式扫描
- **向后兼容**：`--output-format legacy` 输出旧 groups+singletons 格式，`run_all.py` 的 `_wire_suspect_nouns_to_report()` 自动检测并适配两种格式

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
