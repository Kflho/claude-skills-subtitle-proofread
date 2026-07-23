# 日语→中文翻译工具

> 独立于校对 pipeline，可单独使用或配合校对管线。

## 名词库准备（翻译前置）

翻译项目**必须先准备名词库**，否则专名翻译不一致。校对项目可跳过（校对管线有自己的专名流程）。

### 数据流

```
unified_scanner.py → findings.json (term_frequencies)
build_glossary.py  → reports/proper-nouns.md (人类审查用)
                   → temp/noun_mappings.json (脚本用, ja→zh)
        ↓
  🤖 AI 审查词表
        ↓
translate_srt.py --mappings temp/noun_mappings.json
```

### 步骤

**Step 1 — 扫描 + 生成词表**：

```bash
cd "<project>"
python "<scripts>/scan/unified_scanner.py" \
  --target-dir "<日文源字幕>" \
  --output-findings temp/scans/findings_ja.json \
  --output-issues temp/scans/issues_ja/ \
  --build-glossary --glossary-output reports/proper-nouns.md \
  --project-lang ja

python "<scripts>/nouns/build_glossary.py" \
  --findings temp/scans/findings_ja.json \
  -o reports/proper-nouns.md \
  --mappings-output temp/noun_mappings.json
```

输出：
- `reports/proper-nouns.md` — 人类可读的专名表（Markdown）
- `temp/noun_mappings.json` — 机器可读的 ja→zh 映射（JSON），zh 值初始为空

**Step 2 — 🤖 AI 审查词表**：

1. 读 `reports/proper-nouns.md`（全文）
2. 逐条判断：专有名词 or 普通词？
3. **专有名词** → 确定中文译名 → 写入 `temp/noun_mappings.json`
4. **普通词** → 加入 `COMMON_KANJI` / `COMMON_KATAKANA` 黑名单
5. 重跑 `build_glossary.py`（自动保留已填入的 zh 翻译）

`noun_mappings.json` 格式极简，AI 直接编辑即可：
```json
{
  "アトム": "阿童木",
  "お茶の水": "御茶水",
  "プルート": "普鲁托",
  "ハカセ": ""
}
```
zh 值为空 = AI 尚未审查或判定为普通词。

> **注意**：`--mappings-output` 重新运行时自动保留已填入的 zh 翻译（merge 逻辑）。新增词条 zh 为空。

**Step 3 — 翻译时使用**：

```bash
python "<scripts>/translate_srt.py" \
  --input-dir "<日文源>" --output-dir "<中文输出>" \
  --mappings temp/noun_mappings.json
```

`--mappings` 优先于 `--glossary`（后者保留向后兼容）。

---

## translate_srt.py — 批量翻译

### 基本用法

```bash
# 单文件
python translate_srt.py --input EP001.srt --output 中文/EP001.srt

# 批量目录
python translate_srt.py --input-dir 日文ai修复版/ --output-dir 中文翻译后/

# 带名词库
python translate_srt.py --input-dir 日文ai修复版/ --output-dir 中文翻译后/ \
  --mappings temp/noun_mappings.json
```

### 参数

| 参数 | 说明 |
|------|------|
| `--input` / `--input-dir` | 输入文件或目录 |
| `--output` / `--output-dir` | 输出路径 |
| `--mappings` | noun_mappings.json（推荐） |
| `--glossary` | proper-nouns.md（兼容旧版，不推荐） |
| `--model` | LLM 模型（默认 deepseek-chat） |
| `--base-url` | API 端点 |
| `--batch` | 每批翻译句数（默认 10） |
| `--dry-run` | 预览，不调 API |

### 功能

- **OP/ED 预翻译**：检测片头片尾区域，翻译一次后对所有集复用
- **专名预替换**：翻译前用 `noun_mappings.json` 替换已知专名
- **合并润色**：翻译提示词已包含口语化指令，无需二次润色

### 环境变量

```bash
export LLM_API_KEY="sk-..."          # 必须
export LLM_MODEL="deepseek-chat"     # 可选
export LLM_BASE_URL="https://api.deepseek.com/v1"  # 可选
```

---

## find_suspect_nouns.py — 疑似专名搜索

可复用工具，支持三种模式。

### source 模式 — 日文源中找未识别专名

```bash
python "<scripts>/nouns/find_suspect_nouns.py" \
  --input-dir "日文ai修复版/" \
  --glossary reports/proper-nouns.md \
  --lang ja --mode source
```

输出 `temp/scans/suspect_nouns.json`：不在词表中的疑似专名列表。

### translation 模式 — 中文翻译中找不一致

```bash
python "<scripts>/nouns/find_suspect_nouns.py" \
  --input-dir "中文AI翻译验证/" \
  --source-dir "日文ai修复版/" \
  --mappings temp/noun_mappings.json \
  --lang zh --mode translation
```

自动聚类同源异译（如 `小飞`/`扉`/`扉不扉` 都来自日语 `トビ`）。

### search 模式 — 用户提供错例批量查找

```bash
# 1. 创建错例文件
cat > temp/corrections.json << 'EOF'
{"corrections": [
  {"wrong": ["飞","扉","门","小飞","飞跳","飞鱼"],
   "correct": "飞雄", "source_ja": "トビラ"}
]}
EOF

# 2. 批量搜索全部集数
python "<scripts>/nouns/find_suspect_nouns.py" \
  --mode search \
  --input-dir "中文AI翻译验证/" \
  --source-dir "日文ai修复版/" \
  --corrections temp/corrections.json \
  --lang zh

# 3. 一键应用修复（自动备份原文件）
python "<scripts>/nouns/find_suspect_nouns.py" \
  --mode search \
  --input-dir "中文AI翻译验证/" \
  --corrections temp/corrections.json \
  --apply-corrections
```

### search 模式 — 错例文件格式

```json
{
  "corrections": [
    {
      "wrong": ["错误翻译1", "错误翻译2"],
      "correct": "正确翻译",
      "source_ja": "对应的日语原文",
      "note": "备注（可选）"
    }
  ]
}
```

搜索时会：
1. 遍历全部集数的每一条字幕
2. 分词匹配 `wrong` 列表中的词
3. 交叉参照日语原文确认（`source_ja` 匹配）
4. 输出每个命中位置和**建议修复后文本**
5. `--apply-corrections` 可直接原地修复（自动 `.bak` 备份）

### 参数

| 参数 | 适用模式 | 说明 |
|------|---------|------|
| `--input-dir` | 全部 | SRT 目录 |
| `--lang ja\|zh` | 全部 | 分词语言 |
| `--mode source\|translation\|search` | 全部 | 运行模式 |
| `--glossary` | source | proper-nouns.md 路径 |
| `--mappings` | translation | noun_mappings.json 路径 |
| `--source-dir` | translation, search | 日文源目录（交叉参照） |
| `--corrections` | search | 错例 JSON 文件 |
| `--apply-corrections` | search | 实际修复 SRT 文件 |
| `--limit N` | 全部 | 限制扫描文件数（测试用） |

---

## AI 润色（--lang zh，可选）

Pipeline 末尾交互提问 `是否对最终字幕进行 AI 润色？(y/n)`。

- **y** + 已设 `LLM_API_KEY` → 自动调用 `polish_zh.py`，输出到 `中文润色后/`
- **y** + 无 key → AI 助理自行逐句润色（⚠️ 高 token 消耗，仅适合 ≤5 集）
- **n** → 跳过
