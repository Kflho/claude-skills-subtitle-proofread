# OP/ED 与固定台词统一：复用 oped_fixer，补一个「文本键控」应用

> 先查现成实现：`scripts/fix/oped_fixer.py` 已经做了跨集聚类、变体收集、
> AI review 候选、统一替换。**没有重新写一套聚类**，只补了它缺的两块：
> ① 候选的 `canonical` 由谁填（meta 优先 + AI 复核）；
> ② 应用时按文本而不是按时间桶匹配。
>
> **补记（2026-10-26）**：这条线解决的是「文本**对但不统一**」。唱词被 ASR 写成乱码时，
> 文本键控也救不回来——那种情况走另一条：`audio/oped_boundaries.py` 先用音频指纹定每集
> 窗口（`--frames-dir` 抽边界帧复核），再 `fix/oped_region.py` 按窗口整段换成定本歌词。
> 两条路的分工：乱码 → 定窗口 + 换定本；只是变体 → 文本键控。

## 1. 现有能力（先读文档的结论）

| 组件 | 已有能力 |
|---|---|
| `fix/oped_fixer.py` | OP/ED 区域划分；同位置跨集聚类；区分器乐/人声；生成 `--ai-review` 候选（variants + suggested_canonical）；`--apply-ai-review` 应用 |
| `fix/oped_fill.py` | 空 cue 填充（边界检测 → 器乐/人声 → 翻译 → 模板） |
| `lib/oped_detect.py` | API 边界检测 |
| `references/*` | 用户向命令说明 |

实测（佐武 52 集中文）：**5 个簇**全部被抓到——OP 旁白两句、标题、ED 一句。
所以「OP/ED/开场白统一」的稀缺部分不是检测，而是**规范句**和**应用范围**。

## 2. 缺口一：`canonical` 空着

`--ai-review` 产出的候选 `canonical` 为空，工具不会替你决定。
新增 `lib/context/oped_canonical.py`：

1. `_meta.fixed_lines` 里已确认的句子优先（相似度 ≥0.35，免 token）；
2. 其余交给语境审查 agent（带作品档案、区域、变体列表）；
3. 校验：非空、长度不超过最长变体 2 倍、不得残留假名；不通过就留空（fixer 会跳过）。

佐武实测：5 条候选 → meta 2 条、LLM 3 条、0 拒绝、0 留空。

## 3. 缺口二：按时间桶应用会漏

`oped_fixer --apply-ai-review` 按「区域 + 时间桶」替换。同一句在不同集出现的时间
不同（16.3s / 16.9s / 35.9s…），桶对不上就漏。实测：95 处变体只统一了 **35** 处。

新增 `apply_text_keyed()`（复用同一份候选，不重新聚类）：按**规范化文本**匹配，
把所有命中变体的 cue 换成 canonical。

佐武实测（副本上）：

| 簇 | 变体出现 | 统一后 canonical | 残留 |
|---|---:|---:|---:|
| OP（30 集） | 34 | 54（含多行合并） | **0** |
| OP 标题（24 集） | 30 | 30 | **0** |
| OP（4 集） | 4 | 4 | **0** |
| OP（22 集） | 22 | 54 | **0** |
| ED（3 集） | 5 | 4 | **0** |

共 **63 条 cue / 50 集**；`--in-place` 写回 `translated/zh` 时每文件先写 `.bak`
（已有 `.bak` 加时间戳，不覆盖），不带 `--in-place` 只写 `--out-dir` 副本。

## 4. 命令

```powershell
# 1) 生成候选（现有工具，只读）
python scripts/fix/oped_fixer.py <项目>/translated/zh --lang zh `
  -o temp/scans/oped_fixes.json --ai-review temp/scans/oped_ai_review.json

# 2) 填 canonical（meta 优先 + AI 复核；只写报告）
python scripts/nouns/context_review.py --project <项目> `
  --glossary <项目>/glossaries/<s>.json --meta <项目>/glossaries/<s>.meta.json `
  --mode oped --candidates <项目>/temp/scans/oped_ai_review.json `
  --report <项目>/temp/oped_filled.json

# 3a) 需要兼容原工具时：交给 oped_fixer 按时间桶应用
# 3b) 需要完整统一时：文本键控，写副本
python scripts/nouns/context_review.py ... --mode oped `
  --candidates <项目>/temp/oped_filled.json --apply-text-keyed `
  --out-dir <项目>/temp/oped_textkeyed
```

## 5. 测试

`tests/test_oped_canonical.py`（10 例）：meta 命中、AI 填充、假名/超长拒绝、
留空不写、文本键控跨时间统一、无 canonical 跳过、CLI 只写报告/副本（源文件不变）。

## 6. 结论

- 检测/聚类/候选：**已有实现，直接复用**；
- 规范句：meta（人工确认）+ AI 复核，校验后落地；
- 应用：时间桶只用于原工具兼容，完整统一走文本键控；
- 风险点：文本键控会命中「同句但语境不同」的罕见情况，所以它只写副本，
  确认后再覆盖，且 canonical 必须来自 meta 或 AI 复核。
