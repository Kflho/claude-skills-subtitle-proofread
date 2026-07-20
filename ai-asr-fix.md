# AI 听译字幕修复（语音识别错误修正）

> 📁 **本文档内容已整合到新架构中。** ASR 修复流程见 `SKILL.md` 分支 A（unified_scanner + romaji_fixer）+ 分支 B（Whisper 管线）。

> 加载条件：用户提供的字幕本身是 AI 语音识别生成的（路径 #3），如 YouTube 自动字幕。
> 此模式修复 ASR 特有错误（罗马字泄漏、同音词、乱码等），修复后的字幕可作为后续翻译的源语言参照。
> 与完整模式或独立模式叠加使用。

## 适用场景

当目标字幕的**源语言**是通过 AI 语音识别（ASR）生成时（如 YouTube 自动字幕），会引入特有的错误类型。此模式专门处理这些错误。

典型流程：
```
AI 语音识别 → SRT 字幕（含罗马字泄漏、同音词错误等） → 本模式修复 → 翻译为目标语言
```

---

## AI 转录特有错误

### 罗马字泄漏（极高频率）

YouTube 日语 ASR 间歇性将假名输出为罗马字：

| 模式 | 出现次数（193集参考） |
|------|:---:|
| `me` → `め` | 1,828 |
| `ni` → `に` | 509 |
| `re` → `れ` | 179 |
| `car` → `カー` | 326 |
| `dare` → `だれ` | 41 |

**修复方式**：运行 `bilingual_detect.py` 检测 → `generate_romaji_fixes.py` 生成修复规则 → Claude 审查 → `apply_fixes.py` 写入。

> ⚠️ 单字符全局替换（如 `a`→`あ`）会误伤合法英语文本，必须 Claude 逐条审查上下文。

### 同音词错误

AI 将日语单词识别为同音异义词：

| 错误 | 正解 | 原因 |
|------|------|------|
| `扉` (tobira/门) | `飛雄` (Tobio) | 角色名被听成普通名词 |
| `歌舞伎町` (Kabukicho) | `科学省` (Kagaku-shō) | 地名 vs 机构名 |
| `精密対局` | `精密機械` | 对局(game)→機械(machinery) |

**修复方式**：无法自动检测，需 Claude 逐句审查，结合上下文判断。

### OP/ED 歌词乱码

AI 将主题曲旋律哼唱转录为无意义罗马字（`mememe`, `paranino` 等），跨 100+ 集出现。

**检测方法**：`oped_timecode_detect.py` 跨集时间码聚类，自动识别 OP/ED 区间。

### 碎片化字幕行

AI 将单个音节/助词输出为独立的 cue（如 `を` 作为独立字幕）。

**修复方式**：`apply_fixes.py` 的 `merge_cues` action 合并相邻碎片。

---

## 工作流

### 步骤 1：跨集 OP/ED 区间检测

```bash
python scripts/oped_timecode_detect.py --srt-dir ./AI审查后/ > oped_blocks.json
```

输出 OP/ED 时间码区间，用于后续跳过这些区域的检测。

### 步骤 2：双语混合 + 罗马字检测

```bash
python scripts/bilingual_detect.py --target-dir ./AI审查后/ > bilingual.json
```

### 步骤 3：生成罗马字修复规则

```bash
python scripts/generate_romaji_fixes.py --bilingual-json bilingual.json > romaji_fixes.json
```

Claude 审查 `romaji_fixes.json`，去掉会误伤英语的规则（如单字符替换）。

### 步骤 4：应用修复

```bash
python scripts/apply_fixes.py --target-dir ./AI审查后/ --fixes romaji_fixes.json --dry-run
python scripts/apply_fixes.py --target-dir ./AI审查后/ --fixes romaji_fixes.json
```

### 步骤 5：生成 Whisper 问题清单

规则修复后，剩余的罗马字乱码写入 issue 清单，交给 Whisper 阶段处理：

```bash
python scripts/issue_tracker.py --srt-dir ./AI审查后/ --video-dir ./videos/ --output-dir ./issues/
```

---

## 相关脚本

| 脚本 | 用途 |
|------|------|
| `bilingual_detect.py` | 检测中英/多语混合行、纯源语言行 |
| `generate_romaji_fixes.py` | 从 bilingual_detect 结果生成罗马字→假名 fixes |
| `oped_timecode_detect.py` | 跨集时间码聚类，自动识别 OP/ED 区间 |
| `issue_tracker.py` | 扫描 SRT 生成每集问题清单 JSON（供 Whisper 阶段） |
