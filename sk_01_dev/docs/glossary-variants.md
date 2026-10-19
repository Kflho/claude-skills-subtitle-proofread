# 专名变体归一 + 质量闸门（2026-10-19）

> 解决的问题：分批翻译时同一个专名会漂成多种写法；旧集没有词表，漂移已经写进字幕。
> 设计原则：**发现只产生候选，决策只来自人工确认的 `aliases`，执行是确定性的，结果有闸门。**

## 1. 为什么不是「先留空最后回写」

EP051 实测（15 次专名出现、15 批）：

| 臂 | 规范写法命中 | 集内不同写法 | 同批并存 |
|---|---:|---:|---:|
| 项目现有译文 | 2/15 | 7 种 | 2 |
| 重译，无词表 | 2/15 | 6 种 | 2 |
| 重译，**带词表** | **15/15** | 1 种 | 0 |

词表注入已经把 93% 的问题解决；留空回写（mask→填）要切断助词和中文语序，
风险高于收益，只在 OP 旁白/标题/关键专名兜底时用（见
`佐武_分批翻译一致性方案.md`）。剩下的是**旧集和零星漏网**，用确定性归一处理。

## 2. 三层结构

```
_meta.aliases          人工确认的变体表（唯一能改字幕的输入）
      ↓
lib/glossary.py        安全检查 + 最长匹配（不碰规范写法、不碰普通词）
      ↓
nouns/normalize_variants.py   dry-run 预览 → --apply 写盘（.bak）
      ↓
eval/text_gate.py      kana / echo / variants / CPS 计数，超阈值退出 1
```

- `glossary`（扁平 ja→zh）给翻译用；`meta` 只放决策与说明，**不要混进扁平表**
  （`find_suspect_nouns` 会把 `_meta` 当词条直接崩）。
- 发现器 `discover_variants` 用**判别式打分**：术语出现的 cue 里反复出现、
  术语不出现的 cue 里很少出现的 CJK 子串才是候选（`positive ≥ 2` 且
  `positive > negative`），只输出候选，永不写盘。

## 3. 安全规则（都有测试）

| 规则 | 为什么 | 测试 |
|---|---|---|
| 变体 == 规范 → 丢弃 | 无意义替换 | `test_build_variant_map_drops_unsafe_entries` |
| 变体 < 2 字 → 丢弃 | 单字会命中普通词 | 同上 |
| 变体是规范名的子串 → 丢弃 | 「市之」⊂「市」这类会误伤 | `test_substring_of_canonical_is_dropped` |
| 最长匹配 | 「サブヤン」不能被「サブ」截断 | `test_normalizer_prefers_longest_match` |
| 规范写法永不重写 | 避免二次污染 | `test_normalizer_never_touches_canonical_only_text` |
| 普通词不碰 | 市场/小偷/强盗/笨蛋 | `test_normalizer_leaves_decoy_words_alone` |
| 不修改入参 | 便于 dry-run 与回滚 | `test_normalize_cues_does_not_mutate_input` |

### 一次真实的误伤（已变成回归测试）

第一版把 `圣诞` 作为 `三太` 的变体，结果 EP001/EP010 的
「**圣诞老人**，你这小鬼！」被改成「**三太老人**」。这是典型的
「别名是合法词组的一部分」误伤。处理：

- `圣诞` / `阿市` / `世间暗处` 移出 `aliases`，写进 `needs_review` 并注明原因；
- 新增 `test_project_aliases_do_not_overreach`：断言这三者不被替换。

## 4. 真实数据（52 集副本上跑，不动项目）

```
normalize --apply  → 174 cue / 22 文件被改写（去掉误伤前是 197/28）
gate before        → kana 83, echo 0, variants 197, cps 234   FAIL
gate after         → kana 46, echo 0, variants 0,   cps 234   FAIL(kana)
```

- 变体残留 **197 → 0**；
- 残留假名 **83 → 46**（归一顺带清掉变体里的假名）；
- 剩 46 条假名不在别名覆盖范围（`あんまり`、`つごろく`、`决まり` 这类 ASR 噪声），
  属于 **translation-QA 失败类**，该走 review 填充，而不是扩大别名。

## 5. 命令

```powershell
$env:PYTHONPATH = "scripts"

# 1) 候选（只读）：看还有哪些变体没进表
python scripts\nouns\normalize_variants.py --zh-dir <zh> `
  --glossary <glossaries/sabu.series.json> --meta <...meta.json> `
  --discover --ja-dir <日文参考> --report temp\variants_candidates.json

# 2) 预览 / 写盘
python scripts\nouns\normalize_variants.py --zh-dir <zh> --glossary ... --meta ... --dry-run
python scripts\nouns\normalize_variants.py --zh-dir <zh> --glossary ... --meta ... --apply

# 3) 闸门（CI 用）
python scripts\eval\text_gate.py --zh-dir <zh> --glossary ... --meta ... `
  --source-dir <日文参考> --max-kana 0 --max-variants 0 --report temp\gate.json
```

## 6. 测试清单

| 文件 | 锁什么 |
|---|---|
| `tests/test_glossary_normalize.py` | 变体表安全过滤、最长匹配、普通词不动、发现器判别式打分、真实词表不越界（含「圣诞老人」回归） |
| `tests/test_normalize_cli.py` | dry-run 不写盘、apply 生效且 `.bak`、二次运行幂等、未命中文件不动、discover 不写、冲突参数报错 |
| `tests/test_text_gate.py` | kana/echo/变体/CPS 四种闸门、阈值可调、报告结构、空目录报错 |

合计 29 例；全量 `1510 passed`。

## 7. 下一步

1. 用 `--discover` 把剩下 46 条假名归类：属于「原文残留」的进 translation 复核，
   属于「专名变体」的进 `aliases`（要先看正负样本，别拍脑袋）。
2. 把 `text_gate` 接进每集流程：translate → normalize → gate，三条命令串起来。
3. 英文路线（EP001–030）补一个 EN→ZH 的别名表，复用同一套 canonical。

## 8. 多源（英/日）专名统一

同一部剧可能一部分集从英文译、一部分从日文译，最终要合成一套中文字幕。
两条线必须共用同一套 canonical：

- **英文形进 `aliases`**：`{"佐武": ["Sabu", "SABU", "萨布"]}`；匹配按词边界 +
  忽略大小写（`Sabu` 不会误伤 `Sabuyan` 内部的 `Sabu`，`midori` 也能命中）。
- **英文专名同时进扁平词表**（`Sabu -> 佐武`），这样 `translate_srt --mappings`
  在英译中时也能预替换。
- **闸门加 `--max-latin 0`**：统计非制作名单行里的拉丁词，英文残留会直接失败。

佐武实测（52 集，30 英源 + 22 日源）：

| 指标 | 处理前 | 处理后 |
|---|---:|---:|
| 拉丁词残留 | 178 | **0** |
| 含拉丁词的 cue | 163 | **0** |
| 别名变体残留 | 197 | **0**（仅保留 blocked 的 `萨布托米多`） |
| 两条线共同出现的 canonical | — | 23 个，写法一致 |

英文形里有近一半是音译待定（`Sono/索诺`、`Taka/泷吉` 等），统一记在
`_meta.needs_review`，先统一写法、后续对照原音再改名。

测试：`tests/test_multisource_names.py`（5 例，默认运行）锁「两源同一 canonical、
大小写/词边界、最长匹配、一个别名只属于一个 canonical、gate 前后」；
`tests/test_project_multisource.py`（env 门控）在真实语料上断言拉丁残留 0、
变体残留 0。
