# 环境设置

> 首次使用或更换环境时执行。已验证过的项目跳过。

## 导出环境变量

从项目 CLAUDE.md「密钥与路径」段逐条 `export`。**不要跳过** — 缺 env var 会导致 Whisper 静默跳过。

```bash
export PYTHONIOENCODING=utf-8   # 防 Windows GBK 乱码
```

## 验证关键路径

```bash
test -f "$WHISPER_CLI" && echo "[OK] whisper-cli" || echo "[MISSING] whisper-cli"
test -f "$WHISPER_MODEL" && echo "[OK] model" || echo "[MISSING] model"
test -d "<VIDEO_DIR>" && echo "[OK] video" || echo "[MISSING] video"
```

有 `[MISSING]` → 告知用户。Whisper 缺失可残血运行（跳过音频修复）。

## 验证 Python 依赖

```bash
python --version           # 需要 Python 3.12+

# 日语项目 — jamdict（JMdict 词典，专名分类）+ Janome（形态素解析，词汇提取）
python -c "from jamdict import Jamdict; Jamdict(); print('[OK] jamdict')" 2>/dev/null \
  || { echo "[INSTALL] jamdict..."; pip install jamdict; }
python -c "from janome.tokenizer import Tokenizer; Tokenizer().tokenize('テスト'); print('[OK] janome')" 2>/dev/null \
  || { echo "[INSTALL] janome..."; pip install janome; }

# 中文项目 — jieba（分词 + 词典过滤，对标 jamdict）
python -c "import jieba; jieba.initialize(); print('[OK] jieba', len(jieba.dt.FREQ), 'words')" 2>/dev/null \
  || { echo "[INSTALL] jieba..."; pip install jieba; }
```

`[OK]` → 继续。`[INSTALL]` → 自动安装后继续。安装失败 → 残血运行（退回规则分类）。

降级影响：
- **jamdict** 不可用 → Phase 3 退回规则分类
- **Janome** 不可用 → Phase 1 退回 n-gram 切分（~40% 碎片率）
- **jieba** 不可用 → Phase 1 退回 n-gram 切分，Phase 3 退回规则分类

## LLM API 密钥（翻译 + 润色，--lang zh 可选）

```bash
# OpenAI 兼容 API — 翻译脚本 + 润色脚本共用
# 支持 DeepSeek、OpenAI、Gemini 等任何 /chat/completions 端点
export LLM_API_KEY="sk-..."
# 可选：覆盖默认模型和端点
export LLM_MODEL="deepseek-chat"                    # 默认
export LLM_BASE_URL="https://api.deepseek.com/v1"   # 默认
```

> ⚠️ 不要复用 Claude Code 的 key。创建独立的 API key。
> 未设置时降级：翻译功能不可用（需先配 key）；Pipeline 末尾润色交互提问时选 `y` → AI 助理逐句润色（⚠️ 高 token 消耗，7.5 万 cue）。
> 选 `n` → 跳过润色，直接交付。

## Git 备份（铁律）

**每次修改 SRT 前必须 git commit 备份** — pipeline 原地覆写文件，不可逆。

```bash
cd "<project-root>"
git add -A && git commit -m "备份：pipeline前"
```

> 如果项目目录还不是 git repo，先 `git init` + `git add -A` + `git commit`。
