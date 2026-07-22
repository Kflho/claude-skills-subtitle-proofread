# Phase 1 — 扫描命令参考

> 加载时机：Phase 1 执行时。AI 按需查阅。
> 环境验证和依赖安装由 first-run 处理，此处不重复。

## 统一扫描

```bash
cd "<project>" && python "<scripts>/scan/unified_scanner.py" \
  --target-dir "<SUBTITLE_DIR>" \
  --output-findings temp/scans/findings.json \
  --output-issues temp/scans/issues/ \
  --build-glossary --project-lang ja
```

输出：`findings.json`（`garbled_cues` + `repeats` + `per_episode_issues` + `summary`）

- ja: Janome 形态素解析（名词提取）
- zh: jieba 分词
- en: n-gram

扫描是只读的。不写入报告。

## 术语表生成

```bash
# 生成词表
python "<scripts>/nouns/build_glossary.py" --findings temp/scans/findings.json \
  -o reports/proper-nouns.md --lang ja

# 自动清理
python "<scripts>/nouns/auto_clean_glossary.py" --glossary reports/proper-nouns.md --apply --yes
```

词典过滤策略（三级）：

| 层级 | 机制 | 说明 |
|------|------|------|
| Tier 1 | `COMMON_KANJI` / `COMMON_KATAKANA` frozensets | 硬覆盖，始终生效 |
| Tier 2 | Jamdict (JMdict) / jieba 词典 | 在词典 → 普通词 → 删 |
| Tier 3 | 启发式模式匹配 | 动词词干/时间碎片/修饰语/代词片段 → 删 |

## 单步调试

```bash
# Phase 1 单独扫描
python scripts/scan/unified_scanner.py --target-dir AI审查后/ --project-lang ja
```

## 项目特征 → 脚本选用

| 项目特征 | 执行方式 |
|---------|---------|
| 所有项目 | `run_all.py --lang <LANG>` 一键全流程 |
| SRT only | 自动跳过 ASS 修补 |
| ASS 格式 | ASS 修补自动运行 `ass_repair.py --check all` |
| 有参考字幕 | text 模式：翻译参考字幕 → 对照 |
| 无参考字幕 | audio 模式：VAD + Whisper 转录 |
| 中文目标 | 自动繁→简 + 翻译腔去机械化 |

> 语言差异通过 `--lang ja|zh` 处理，工作流本身不分支。
