# 脚本配置参考

> 开发者参考：检测脚本的配置格式、输出示例和 fixes.json 写法。
> 所有脚本遵循 **检测 → Claude 审查 → apply_fixes.py 修复** 三步流程。

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

## fixes.json 示例

```json
[
  {"action": "replace_global", "original": "...", "replacement": "...", "note": "..."},
  {"action": "replace_global_regex", "pattern": "...", "replacement": "...", "note": "..."},
  {"action": "replace_text", "file": "...", "line": N, "replacement": "..."},
  {"action": "delete_line", "file": "...", "line": N},
  {"action": "delete_style", "style": "..."}
]
```

## 统一扫描（Phase 1）

```bash
python scripts/scan/unified_scanner.py --target-dir AI审查后/ \
  --output-findings temp/scans/findings.json \
  --output-issues temp/scans/issues/ \
  --build-glossary --project-lang ja
```

输出：`findings.json`（garbled_cues + repeats + per_episode_issues + summary）

## 一键全流程

```bash
python scripts/run_all.py --lang ja                    # 全量
python scripts/run_all.py --lang ja --limit 5           # 前5集
python scripts/run_all.py --lang ja -e EP001-EP010      # 指定范围
python scripts/run_all.py --lang ja --apply-checklist   # 应用人工审查修正
python scripts/run_all.py --lang ja --apply-ai-review   # 应用 AI 审查修正
```

## 单层调试

```bash
# Phase 1: 扫描
python scripts/scan/unified_scanner.py --target-dir AI审查后/ --project-lang ja

# Phase 2: 单集 Whisper
python scripts/fix/fix_orchestrator.py EP002 --step whisper
python scripts/fix/fix_orchestrator.py EP002 --step check

# Phase 2 + 人工: 生成审查清单
python scripts/fix/fix_orchestrator.py EP002 --step review

# Phase 3: 专名
python scripts/nouns/noun_checker.py AI审查后/ --lang ja --oped
python scripts/nouns/auto_classify.py --candidates candidates.json --lang ja

# Phase 3: 批量修复
python scripts/apply/apply_fixes.py --target-dir AI审查后/ --fixes fixes.json --lang ja

# ASS only
python scripts/ass/ass_repair.py --target-dir AI审查后/ --check all
```

## 工具脚本

```bash
python scripts/utils/clean_empty_cues.py --target-dir AI审查后/
python scripts/utils/update_report.py reports/问题解决报告.md --summary
```

## 专名词表维护

```
build_glossary.py      → JMdict 过滤 → 生成 proper-nouns.md
auto_clean_glossary.py → 启发式清理 → 更新 COMMON_KANJI/KATAKANA
noun_checker.py        → 扫描 SRT 发现变体
auto_classify.py       → ACCEPT/REJECT/NEEDS_AI
```

## OP/ED 修复

```bash
# 自动清理 instrumental OP/ED
python scripts/fix/oped_fixer.py AI审查后/ --lang ja --auto-only -o temp/scans/oped_fixes.json

# 带参考字幕
python scripts/fix/oped_fixer.py AI审查后/ --lang ja -o temp/scans/oped_fixes.json \
  --ai-review temp/scans/oped_ai_review.json --reference <ref_sub_dir>/

# 应用 AI 审查结果
python scripts/fix/oped_fixer.py AI审查后/ --lang ja -o temp/scans/oped_fixes.json \
  --apply-ai-review temp/scans/oped_ai_review.json
```
