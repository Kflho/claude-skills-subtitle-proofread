# 检测脚本速查

> 最后更新: 2026-07-21（审计清理）
> 所有旧版独立脚本已合并到统一工作流中，本文档只列当前有效脚本。

## 统一扫描（L1）

```bash
# 字符层统一扫描 — 单次遍历：garbled + repeat + 术语收集
python scripts/scan/unified_scanner.py --target-dir AI审查后/ \
  --output-findings temp/scans/findings.json \
  --output-issues temp/scans/issues/ \
  --build-glossary --project-lang ja

# 已合并的旧脚本（功能已迁移，文件已删除）:
#   bilingual_detect.py + source_lang_detect.py + source_char_detect.py
#   + repeat_detect.py + issue_tracker.py + romaji_fixer.py
```

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
# L1: 扫描
python scripts/scan/unified_scanner.py --target-dir AI审查后/ --project-lang ja

# L2: 单集 Whisper
python scripts/fix/fix_orchestrator.py EP002 --step whisper
python scripts/fix/fix_orchestrator.py EP002 --step check    # 检查是否干净

# L2.5 + L6: 生成审查清单
python scripts/fix/fix_orchestrator.py EP002 --step review

# L3: 专名
python scripts/nouns/noun_checker.py AI审查后/ --lang ja --oped
python scripts/nouns/auto_classify.py --candidates candidates.json --lang ja

# L4: 批量修复
python scripts/apply/apply_fixes.py --target-dir AI审查后/ --fixes fixes.json --lang ja

# L5: ASS only（SRT 项目跳过）
python scripts/ass/ass_repair.py --target-dir AI审查后/ --check all
```

## 工具脚本

```bash
# 清理空 cue
python scripts/utils/clean_empty_cues.py --target-dir AI审查后/

# 报告查询（禁止直接 cat/Read！）
python scripts/utils/update_report.py reports/问题解决报告.md --summary
```

## 项目特征 → 脚本选用

| 项目特征 | 执行方式 |
|---------|---------|
| 所有项目 | `run_all.py --lang <LANG>` 一键全流程 |
| SRT only | 自动跳过 L5 (ASS 修补) |
| ASS 格式 | L5 自动运行 `ass_repair.py --check all` |
| 有参考字幕 | text 模式：翻译参考字幕 → 对照 |
| 无参考字幕 | audio 模式：VAD + Whisper 转录 |
| 中文目标 | L4 自动繁→简 + 翻译腔去机械化 |

> 语言差异通过 `--lang ja|zh` 处理，工作流本身不分支。

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
