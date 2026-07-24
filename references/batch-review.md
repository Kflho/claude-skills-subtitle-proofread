# API 批量专名审查

当 `auto_translate.py` 扫描出大量 `unknown_suspect`（> 50条）时，不要手动逐条审查。用 LLM API 批量分类。

## 流程

### 1. 扫描 → 提取候选

```bash
python auto_translate.py --source-dir <日文源> --target-dir <中文翻译> --mappings temp/noun_mappings.json
# 输出: 1428 candidates (39 inconsistencies, 1389 unknown)
```

### 2. 写分类脚本 → API 批量判断

用 `urllib.request` 调用 OpenAI 兼容 API（项目不依赖 openai 库），每批 30 条：

```python
import json, urllib.request

# 读 candidates.json
with open('temp/scans/candidates.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

unknowns = [c for c in data['candidates'] if c['type'] == 'unknown_suspect']

# 分批调用 API
for batch in chunks(unknowns, 30):
    terms = '\n'.join(f"- {u['zh_term']} (频率={u['frequency']}) | {u['sample_contexts'][:2]}" 
                      for u in batch)
    
    prompt = f"""你是中文专有名词分类专家。判断以下词是「专有名词」还是「普通词」。
专有名词：人名、地名、组织名、机器人名、星球名
普通词：动词、形容词、副词、日常用语

返回JSON: {{"classifications": [{{"term": "词", "classification": "proper_noun或common_word"}}]}}

待分类词：
{terms}"""
    
    # ... API call with urllib (参照 translate_srt.py 的 _call_llm 模式)
```

### 3. 应用分类结果

```bash
# 普通词 → 黑名单
python -c "
import json
# 读 classified_terms.json
# common_word 的 term → temp/zh_common_blacklist.json
"

# 专名 → 映射表（self-mapping）
python -c "
import json
# proper_noun 的 term → noun_mappings.json (key=value=term)
"
```

### 4. 重跑扫描

```bash
# auto_translate.py 自动检测 zh_common_blacklist.json 并传给扫描器
python auto_translate.py --source-dir <日文源> --target-dir <中文翻译> --mappings temp/noun_mappings.json
# 候选数: 1428 → ~50（-96%）
```

### 5. 手动处理剩余

剩余的 inconsistency（~40条）大多是误报——扫描器聚类算法把和专名同句的普通词误判为翻译变体。逐个检查 sample_contexts 确认是否是真正的译法不一致。

## 关键文件

| 文件 | 格式 | 作用 |
|------|------|------|
| `temp/zh_common_blacklist.json` | `["词1", "词2", ...]` | 中文普通词黑名单，扫描器自动跳过 |
| `temp/noun_mappings.json` | `{"ja_term": "zh_term"}` | 专名映射表，扫描器 skip known_zh |
| `temp/scans/classified_terms.json` | `{"proper_nouns": [...], "common_words": [...]}` | API 分类结果 |

## 黑名单机制

`find_suspect_nouns.py --zh-blacklist <JSON>` 加载外部黑名单文件。黑名单中的词在扫描时自动跳过，不会出现在 candidates 中。

`auto_translate.py` 在 `stage_review()` 中自动检测项目 `temp/zh_common_blacklist.json` 并传递给扫描器——无需手动传参。

## 成本

- deepseek-chat：1260 条分词 42 批，~$0.01
- 比手动审查节约大量 AI 上下文 token
