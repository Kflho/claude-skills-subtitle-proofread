# AI 驱动专名提取 — 实现与运维

> 从 `SKILL.md` 移出的开发者说明。用户向三步流程仍在 SKILL.md「专名审查（AI 驱动）」。
> 相关代码：`scripts/nouns/extractor.py`、`aggregate.py`、`apply_map.py`。
> 旧词典流程见 `references/batch-review.md`（legacy）。

## 1. 为什么不用旧词典扫描

旧法（`unified_scanner` → `build_glossary`）从词频里挑映射，噪声大，且会产生
“幻觉标准名”（把「天間」定成「天间」，正确应为「天马博士」）。

新流程让 AI 从真实文本里挑专名实体，**脚本只做 AI 决策的执行工具，不做决策本身**。

共享核心 `extract_names(ja_cues, zh_cues)`：翻译流程内和独立 CLI 同一实现。

## 2. 三步闭环

```bash
cd "<project-root>"

# 1. 提取（翻译时自动：translate_srt.py --extract-nouns；或对已翻译文件独立跑）
python "<scripts-dir>/nouns/extractor.py" \
  --ja-dir "<日文源>" --zh-dir "<中文翻译>" -e EP001-EP005 -o temp/nouns

# 2. 聚合 → running-map（跨集增量合并）
python "<scripts-dir>/nouns/aggregate.py" \
  --map temp/noun_map.json --extracted-dir temp/nouns -o temp/noun_map.json

# 3. 应用：先 dry-run 预览 → 🤖 审查 map → 再写入
python "<scripts-dir>/nouns/apply_map.py" temp/noun_map.json \
  --target-dir "<中文翻译>" --dry-run
python "<scripts-dir>/nouns/apply_map.py" temp/noun_map.json \
  --target-dir "<中文翻译>" --apply

# 导出 ja→zh 幻觉控制表（供 translate_srt --mappings）
python "<scripts-dir>/nouns/apply_map.py" temp/noun_map.json \
  --target-dir "<中文翻译>" \
  --emit-mappings temp/noun_map_ja_to_zh.json \
  --merge-existing temp/noun_mappings.json
```

**🤖 审查点（不可跳过）**：dry-run 之后、`--apply` 之前，审查 `temp/noun_map.json`：
定标准名、定 scope、删误判变体（例：提取可能把标题「铁腕阿童木」并进「阿童木」变体，删掉即可）。

**scope 语义**：

- `global` = 跨全集统一（主要角色 / episodes ≥ 2）
- `per_episode` = 仅该实体出现的集
- `auto` = episodes ≥ 2 取 global，否则取 per_episode

## 3. 提取续跑与健康度

并发分片被 API 限速时，约 17% 集的 chunk LLM 调用会静默失败漏实体。
sidecar 记录 `chunks: {chunks_total, chunks_failed}`，失败与空结果分开记账。

```bash
# 健康度 ✅ 健康 / ⚠ 失败或过少 / ⬜ 未提取（不调 LLM）
python "<scripts-dir>/nouns/extractor.py" \
  --ja-dir "<日文源>" --zh-dir "<中文翻译>" -o temp/nouns --status

# 中断后续跑：跳过健康 sidecar，只跑缺失/失败集（自动重试 chunk 失败）
python "<scripts-dir>/nouns/extractor.py" \
  --ja-dir "<日文源>" --zh-dir "<中文翻译>" -o temp/nouns --resume
```

> 全量提取后**先 `--status` 确认无 ⚠ 再进聚合**，避免漏实体污染 map。
> 旧 sidecar（无 `chunks` 字段）以实体数作失败代理（`--min-entities`，默认 2）。

## 4. chunk 失败降级（DeepSeek 超长请求）

大 chunk（默认 120 对 cue）下，LLM 对超长请求可能返回 **HTTP 200 + 空
`message.content`** → `call_chat` 静默返回 `''` → chunk 判失败但**日志无报错**
（症状：`--status` 成片 ⚠ 而日志干净）。

用 `--chunk-size 60` 再 `40` 逐级收敛（实测 74→22→5→0 失败）。

## 5. `_build_repl` 双写防护

`apply_map` 按变体-标准名关系生成替换正则：

| 关系 | 正则 | 防什么 |
|---|---|---|
| 变体是标准名精确后缀（茶水博士→御茶水博士） | `(?<!前缀)变体` | 「御茶水博士博士」 |
| 变体是标准名精确前缀（乌拉尔→乌拉尔号） | `变体(?!后缀)` | 「乌拉尔号号」 |
| 变体与标准名是近似音译（多里安→多利安博士） | 裸匹配 + `变体(?!标准名全部≥2字真后缀)` | 双写 |

> ⚠️ 近似音译变体是双写高发区：apply 后跑一次双写检测
> （匹配 `标准名+标准名尾2~3字`），确认 apply 引入 0 处。

## 6. 清单记录

`--emit-checklist` 与 `--apply` 同用 = 记录实际统一项。

```bash
python "<scripts-dir>/nouns/apply_map.py" temp/noun_map.json \
  --target-dir "<中文翻译>" --apply \
  --emit-checklist temp/ai_review_checklist.txt
```

行格式 `- [x] NN集：规范名（变体、变体）`，只记实际发生替换的集，
`[x]` 表示 AI 已审查。把行追加到项目清单的 `## ai 审查` 版块；
`## 人工审查` 版块不动。

## 7. 数据流

| 产物 | 内容 | 用途 |
|------|------|------|
| `temp/nouns/extracted_EP###.json` | 每集专名实体 `{ja_forms(含幻觉), zh_forms(含变体), count, samples}` | 聚合输入 |
| `temp/noun_map.json` | running-map `{标准名: {ja_forms, zh_variants, episodes, scope}}` | AI 审查后应用 |
| `temp/noun_map_ja_to_zh.json` | ja→zh 幻觉控制表 | translate_srt `--mappings` |

## 8. 代码位置

| 文件 | 职责 |
|---|---|
| `scripts/nouns/extractor.py` | 逐集/分片提取 `extract_names`；sidecar 健康度；`--resume` |
| `scripts/nouns/aggregate.py` | 跨集增量合并 running-map；scope 推导 |
| `scripts/nouns/apply_map.py` | 变体→标准名替换；`_build_repl`；`--emit-mappings` / `--emit-checklist` |
| `scripts/fix/translate_srt.py` | 翻译后调用 extractor（`--extract-nouns`） |
| `scripts/lib/llm.py` | `call_chat`；空 content 诊断 |

## 9. 已知坑

- LLM 返回 HTTP 200 + 空 content：不是成功，是超长请求/限速；看 `--status`。
- `_comment` 等元数据键写进 noun_mappings JSON 会污染翻译 prompt；说明文字写
  CLAUDE.md，不要写 JSON。
- 接受循环把回显记成成功：用 `is_untranslated()` 剥编号前缀再比（见 `dev/troubleshooting.md`）。
- 提取会把标题并入角色名变体：必须在 dry-run 后人工/AI 审查 map。
