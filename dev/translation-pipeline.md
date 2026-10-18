# 翻译管线 — 实现细节

> 从 `SKILL.md` 移出的开发者说明。用户向用法见 `references/translation.md`。
> 相关代码：`scripts/translate_srt.py`、`scripts/fix/translate_srt.py`、
> `scripts/polish_zh.py`、`scripts/lib/llm.py`、`scripts/lib/config.py`。

## 1. 集内 batch 并行（v2）

`translate_srt.py` 支持**集内 batch 并行**：

- 每个 batch 的上下文用**日文原文**（前 3 条 cue），无需等翻译结果
- 所有 batch 通过 `ThreadPoolExecutor` 同时发出
  （`max_workers=min(batches, 16)`）
- 每集从 ~40s（串行）降至 ~3s（并行），193 集约 10 分钟

> 上下文用日文原文优于中文翻译结果——LLM 本身做日→中翻译，看原文更能理解语流。

## 2. LLM API 配置

翻译（`translate_srt.py`）、润色（`polish_zh.py`）、fix_tail_wo 翻译步骤共用
`LLM_API_KEY`。

当前默认模型：`deepseek-flash`（`lib/config.py:LLM_MODEL_DEFAULT`）。

```bash
export LLM_API_KEY='sk-...'
export LLM_MODEL='deepseek-flash'
export LLM_BASE_URL='https://api.deepseek.com/v1'
```

> 推荐写入 `~/.claude/settings.json` 的 `env` 字段持久化，不污染项目 CLAUDE.md。

### 模型名历史（避免踩旧名）

| 名称 | 状态 |
|---|---|
| `deepseek-flash` | ✅ 当前默认，直接用 |
| `deepseek-v4-flash` / `deepseek-v4-pro` | ⚠️ 仍可调用，但对应模型已下线；请求由 DeepSeek-V4.1-Flash 提供并按 Flash 计费 |
| `deepseek-chat` | ❌ 已失效（HTTP 400） |

> 模型名不兼容时，检查 API 返回的 supported model names，更新
> `lib/config.py:LLM_MODEL_DEFAULT`；也可用 `LLM_MODEL` env 或 `--model` 覆盖。

## 3. 推理模型的 token 陷阱

`deepseek-flash` / `deepseek-v4-pro` 返回 `reasoning_content`，且
`completion_tokens` 里 `reasoning_tokens` 可占满全部额度 → `content` 为空串、
`finish_reason='length'`。

调用方拿到空串 → 判失败 → 整批 cue 保留原文（**静默**，无任何错误日志）。

实测（10 条 cue 一批）：

| 配置 | 耗时 | content | 解析条数 |
|---|---:|---:|---:|
| 思考开 | 21.4s | 0 | 0 |
| 思考关 | 1.4s | 180 | 10 |

所以默认 `LLM_REASONING_EFFORT='none'`。不要靠加大 `max_tokens` 解决：
`max_tokens=32768` 时 reasoning 仍 13832 tok，62s 才收敛。

非 DeepSeek 端点若不认 `reasoning_effort`，把它设为空串即可省略该参数。

## 4. 翻译后验证（必须）

不靠 “Pipeline complete” / exit 0 判断成功：

1. **grep 日语残留（零容忍）**：成片 cue 不应保留日文原文。
2. **grep 已知错误专名**：对照 noun_mappings / 已知正确名。
3. 发现残留 → 手工修复或标 `[???]`；错误专名 → 回到映射完整性检查。

### `is_untranslated()` 与回显

接受循环若把判据写成 `out != src`，会把「带编号的原文」当成功：

```
1. 原文
2. 原文
```

回显文本确实“不同”，但没翻译。正确做法：用 `is_untranslated()` 剥掉
`^\s*\d+\s*[.、)）]\s*` 前缀再比较。

### Glossary 回显污染

译文里成片出现 `1. 原文` `2. 原文`（带编号）→ prompt 被 glossary 污染，
模型整批回显。

`load_mappings` 会把**所有值**拼进「固定译名参考」，`_comment` 之类的元数据键
会混入。已自动跳过 `_` 前缀键；**说明文字写进 CLAUDE.md，不要留在 JSON 里**
（`apply_map.py --merge-existing` 等消费方未必过滤）。

### `--glossary` 表格解析（legacy 路径）

`load_glossary` 读 markdown 表格时有两个坑（2026-10 修复，见 `tests/test_translate_srt.py`）：

1. `line.split('|')` 首尾各多一个空串。直接取 `cells[1]`/`cells[2]` 会把表头
   `| 日语 | 中文 |` 当成一条 **日语→中文** 映射，并把「中文」「出现次数」塞进 prompt。
   先滤掉空单元格再取列。
2. `build_glossary.py` 产出的表是 `| 日语 | 出现次数 | 拼写变体 |`，**没有中文列**。
   硬当 ja→zh 映射会把「96」当译名，随后 `apply_noun_pre_replace` 把数字替换进正文。
   现在只接受第二列含汉字的行，其余跳过。

首选仍是 `--mappings noun_mappings.json`（结构化、无解析歧义）。

## 5. 百度翻译（legacy）

旧 `scripts/fix/translate_srt.py` 调百度 API：

- 认证：MD5 签名（appid + query + salt + key）
- 限速：1 QPS，200 万字符/月免费
- 环境变量 `BAIDU_APPID`、`BAIDU_SECRET`
- 可选 HTTP 代理：`BAIDU_API_ENDPOINT`

现状：百度翻译是**可选降级层**；主翻译走 LLM。`--lang zh` 时 Whisper 输出
ja→zh 才可能用它，未配置时降级 AI 翻译。

> 2026 技术路线 P4 会把百度默认路径下线，保留为显式 fallback。

## 6. 数据流

```
源字幕 SRT/ASS
  │  translate_srt.py --input-dir ... --output-dir ...
  │  ├─ 读 cue / 分批（--batch，默认 10）
  │  ├─ 上下文 = 前 3 条日文原文
  │  ├─ ThreadPoolExecutor 并发请求
  │  ├─ 解析 JSON / 校验（is_untranslated / 术语）
  │  └─ 写目标 SRT（时间轴继承源 cue）
  ▼
中文译文
  ├─ polish_zh.py（可选，--lang zh）
  └─ 验证：日语残留 / 专名 / 条数与时间轴对齐
```

## 7. 已知坑

- 无 `LLM_API_KEY`：`translate_srt.py` 无法运行；不要静默降级为 AI 自行翻译
  （193 集 × 200 条量级不可行）。告知用户设置后重跑。
- 推理模型空 content：静默导致整批保留原文；确认 `LLM_REASONING_EFFORT=none`。
- glossary 回显：检查 JSON 里有没有说明文字键。
- 百度 1 QPS：大批量会慢；不要用它跑全量。
- 目标 cue 时间轴应与源 cue 对齐；翻译不改时间轴。
