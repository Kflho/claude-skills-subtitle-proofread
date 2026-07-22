# 初始化向导

> Claude：此文件是首次初始化时执行的步骤脚本。
> 触发条件：项目 CLAUDE.md 不存在或不含 `SKILL INITIALIZED: true`。
> 完成后写入 CLAUDE.md，末尾加 `SKILL INITIALIZED: true` 标记。

---

## Step 1：告知流程和原理

向用户说明：

> 字幕校对 skill 的运行原理是，使用 Whisper 模型检测原始字幕的乱码片段并自动修复。首先是逐行扫描乱码，然后通过 VAD 语音检测 + Whisper 重转录修复乱码，最后统一专有名词和固定格式。自动修复不了的内容会转交给你判断。

然后根据当前 CLAUDE.md（如有）补充项目已有的配置信息。

---

## Step 2：收集项目路径

向用户提问：

> 请提供以下路径信息（不确定的留空）：
>
> | # | 路径项 | 用途 | 是否必需 |
> |---|--------|------|:---:|
> | 1 | **目标字幕目录** | 待校对字幕文件 | ✅ 必需 |
> | 2 | **参考字幕目录** | 高质量人工字幕，AI 校对时可对照参考 | 可选 |
> | 3 | **参考视频目录** | mkv/mp4，Whisper 提取音频 | 可选（推荐） |

**⚠️ 如果用户未提供 #3 参考视频目录 → 残血运行。** 告知用户：

> 无视频时管线跳过 Whisper 音频修复。Phase 1 扫描出的乱码会原样保留，写入 `reports/问题解决报告.md` 的「未修复乱码」section（带 ⬜ 标记），需手动/AI 对照参考字幕逐条处理。

残血模式产出：
| 有 | 无 |
|----|----|
| ✅ 乱码扫描 + 分类 | ❌ Whisper 音频修复 |
| ✅ 专有名词词表 | ❌ AI 碎片补全 |
| ✅ 问题解决报告（⬜ 待处理） | ❌ 视频片段提取 |

**多项目处理**：提醒用户最好一个项目一个文件夹。如果用户提到有其他项目，记录到 CLAUDE.md 的项目索引表中：

```markdown
## 已知项目
| 项目 | 路径 | 视频目录 | 参考字幕 |
|------|------|---------|---------|
| <项目1> | <path> | <video> | <ref or 无> |
| <项目2> | <path> | <video> | <ref or 无> |
```

---

## Step 3：Whisper 后端检测与选择

### 3.1 自动检测

运行后端检测脚本，查看系统上已安装的 Whisper 实现：

```bash
cd "<project>" && python -c "
from lib.whisper_backends import backend_detection_report
import json
report = backend_detection_report()
print(json.dumps(report, ensure_ascii=False, indent=2))
"
```

输出示例：

```json
{
  "available": ["whisper-cpp", "faster-whisper"],
  "not_available": ["openai-whisper"],
  "recommendations": "可用后端: whisper-cpp, faster-whisper",
  "details": {
    "whisper-cpp": {
      "backend_id": "whisper-cpp",
      "installed": true,
      "version": "v1.7.2",
      "path": "D:/software/video/whisper-cublas-12.4.0-bin-x64/whisper-cli.exe"
    },
    "faster-whisper": {
      "backend_id": "faster-whisper",
      "installed": true,
      "path": "(Python package)"
    }
  }
}
```

### 3.2 处理检测结果

根据检测结果分情况处理：

**情况 A：检测到多个后端** → 问用户选哪个：

> 检测到以下 Whisper 后端可用：
>
> | # | 后端 | 版本 | 特点 |
> |---|------|------|------|
> | 1 | whisper.cpp | v1.7.2 | GGML 量化模型，GPU加速，内存低 |
> | 2 | faster-whisper | — | CTranslate2引擎，比原版快4x |
>
> 请选择要使用的后端（输入数字）：
>
> - **选择 1** → 需要提供 whisper-cli 路径 + GGML 模型路径
> - **选择 2** → 需要提供 CTranslate2 模型目录路径

记录用户选择：`WHISPER_BACKEND=whisper-cpp`（或 `faster-whisper`）。

**情况 B：只检测到一个后端** → 直接使用，告知用户：

> 检测到 Whisper 后端：whisper.cpp (v1.7.2)，将使用此后端。

**情况 C：未检测到任何后端** → 引导安装：

> ⚠️ 未检测到任何 Whisper 后端。请先安装以下任一方案：
>
> **方案 1：whisper.cpp（推荐 — 无需 Python 依赖）**
> 1. 下载 whisper-cli：https://github.com/ggerganov/whisper.cpp/releases
> 2. 下载日语模型：https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0-ggml
>    （推荐 `ggml-kotoba-whisper-v2.0-q5_0.bin`）
> 3. 备用模型：`ggml-large-v3-q5_0.bin`（主模型失败时切换）
>
> **方案 2：faster-whisper（Python — pip 安装）**
> ```bash
> pip install faster-whisper
> # 模型自动从 HuggingFace 下载，首次运行需联网
> ```
>
> **方案 3：openai-whisper（Python — pip 安装）**
> ```bash
> pip install openai-whisper
> # 模型自动下载到 ~/.cache/whisper/
> ```
>
> 安装完成后重新运行初始化。

### 3.3 收集模型路径

根据用户选择的后端，要求提供对应路径：

**whisper.cpp 用户**：
> 请提供以下路径：
> - whisper.cpp 可执行文件（如 `D:/.../whisper-cli.exe`）
> - 主模型文件（推荐 kotoba-whisper-v2.0 q5_0 量化版，`.bin` 格式）
> - 备用模型文件（主模型失败时切换，可与主模型相同）

验证路径存在（用 `test -f` 或 `ls`），写入 CLAUDE.md：

```bash
export WHISPER_BACKEND='whisper-cpp'
export WHISPER_CLI='D:/software/video/whisper-cublas-12.4.0-bin-x64/whisper-cli.exe'
export WHISPER_MODEL='D:/software/video/whisper-cublas-12.4.0-bin-x64/models/ggml-kotoba-whisper-v2.0-q5_0.bin'
export WHISPER_RETRY_MODEL='D:/software/video/whisper-cublas-12.4.0-bin-x64/models/ggml-large-v3-q5_0.bin'
```

**faster-whisper 用户**：
> 请提供模型路径（CTranslate2 格式目录，或 HuggingFace 模型 ID）：
> - 本地路径如 `D:/models/faster-whisper-kotoba-v2.0`
> - 或 HuggingFace ID 如 `kotoba-tech/kotoba-whisper-v2.0`

写入 CLAUDE.md：

```bash
export WHISPER_BACKEND='faster-whisper'
export WHISPER_MODEL='kotoba-tech/kotoba-whisper-v2.0'
export WHISPER_RETRY_MODEL='deepdml/faster-whisper-large-v3-turbo-ct2'
```

**openai-whisper 用户**：
> 请提供模型名称（`tiny` / `base` / `small` / `medium` / `large-v3`）或本地 `.pt` 文件路径。

写入 CLAUDE.md：

```bash
export WHISPER_BACKEND='openai-whisper'
export WHISPER_MODEL='large-v3'
export WHISPER_RETRY_MODEL='medium'
```

### 3.4 验证后端可用性

```bash
cd "<project>" && python -c "
from lib.whisper_backends import validate_backend, BACKEND_INFO
# 对选定的后端跑冒烟测试（1秒静音片段）
import os
backend = os.environ.get('WHISPER_BACKEND', 'whisper-cpp')
model = os.environ.get('WHISPER_MODEL', '')
ok, msg = validate_backend(backend, model)
print(f'{backend}: {msg}')
if not ok:
    print('WARNING: backend validation failed — Whisper may not work at runtime.')
"
```

> ⚠️ 验证失败时警告用户但不阻止继续。残血运行模式跳过音频修复，仍可扫描乱码+统一专名。

---

## Step 3.5：Python 依赖安装

### 3.5.1 Python 版本

此 skill 需要 **Python 3.12+**。验证：

```bash
python --version  # 应为 Python 3.12.x 或更高
```

> Python 是房间里的那头大象 — 太基础反而容易被忽略。请确认版本后再继续。

### 3.5.2 核心依赖：jamdict（日语词典）+ Janome（形态素解析）

日语项目依赖两个库：

| 库 | 用途 | 阶段 | 包大小 |
|------|------|------|------|
| **Janome** | 形态素解析 → 词汇提取（替代 n-gram） | Phase 1 | ~2MB |
| **jamdict** | JMdict 词典查询 → 专名分类 | Phase 3 | ~500KB |

```bash
pip install janome jamdict
```

`jamdict` 首次运行时自动下载 JMdict 词典数据库（SQLite，~50MB），仅一次。Janome 的词表随 pip 包安装，无需额外下载。

**验证**：

```bash
python -c "from janome.tokenizer import Tokenizer; Tokenizer().tokenize('テスト'); print('[OK] janome')"
python -c "from jamdict import Jamdict; j = Jamdict(); print('OK:', len(j.lookup('日本').entries), 'entries')"
```

期望输出 `[OK] janome` + `OK: N entries` → 安装成功。

> ⚠️ 安装失败时警告用户但不阻止继续。
> - **Janome** 不可用 → Phase 1 退回 n-gram 切分（~40% 碎片率），Phase 3 照常
> - **jamdict** 不可用 → Phase 3 退回规则分类（精度略降）

### 3.5.3 开源许可说明

jamdict 使用的 JMdict/JMnedict 词典数据遵循 [CC-BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) 许可。此 skill 仅通过 jamdict 库查询词典数据，不捆绑或分发词典文件。

### 3.5.4 核心依赖：jieba（中文分词）

中文项目依赖 `jieba`（结巴分词），用于 Phase 1 词汇提取 + Phase 3 词典过滤 — 对标日语项目的 jamdict。词表（~498K 词）随 pip 包一起安装，无需额外下载。

```bash
pip install jieba
```

**验证**：

```bash
python -c "import jieba; jieba.initialize(); print('OK:', len(jieba.dt.FREQ), 'words')"
```

期望输出 `OK: 498113 words` → 安装成功。

> ⚠️ 安装失败时警告用户但不阻止继续。`jieba` 不可用时 Phase 1 退回 n-gram 切分，Phase 3 退回规则分类（精度略降），仍可运行。

**ja ↔ zh 依赖对照**：

| 功能 | ja（日语） | zh（中文） |
|------|------|------|
| 词典库 | jamdict (JMdict, ~50MB) | jieba (dict.txt, ~5MB) |
| 词条数 | ~200K | ~498K |
| 首次下载 | 自动（JMdict SQLite） | 无需（随 pip） |
| 分词 | 形态素（内置） | jieba.lcut() |

---

## Step 3.6：Baidu 翻译（可选 — 中文项目推荐）

> ⚠️ 仅当项目语言为 `zh`（中文）且用户有视频+Whisper 时才需要。
> 日语项目（`--lang ja`）的校对目标就是日语，Whisper 输出直接可用，跳过此步骤。

### 3.6.1 说明原理

向用户说明：

> Whisper 转录的是日语原文，但你的校对目标是**中文**。如果每次让 AI 把 Whisper 输出的日语翻译成中文，token 开销很大。
>
> **Baidu 翻译 API** 在 Whisper 和修复之间插入一个自动翻译层：
> ```
> 日语音频 → Whisper 转录（日语）→ Baidu 翻译（日语→中文）→ 填入中文字幕
> ```
>
> 这是**可选的**。不配也能用——没有 Baidu 时，日语原文保留在 AI fragments 里，AI 自己翻译（结果更准但 token 开销大）。

### 3.6.2 询问是否配置

> 是否配置 Baidu 翻译 API？（输入 y/n）
>
> - **y** → 引导注册 + 配置
> - **n** → 跳过。管线自动降级。

**如果选 n**：

> 好的，跳过。Whisper 输出的日语原文将保留在 AI 审查文件中，AI 自行翻译（每次校对约多消耗 5-10K token）。

跳到 Step 4。

### 3.6.3 引导注册

**如果选 y**，先介绍注册：

> **第一步：注册 Baidu 翻译 API**
>
> 1. 打开 https://fanyi-api.baidu.com/
> 2. 注册/登录百度账号
> 3. 选择「通用翻译 API」
> 4. 完成实名认证：
>    - **个人认证（高级版）**：100 万字符/月免费，10 QPS，推荐
>    - 标准版（未认证）：5 万字符/月，1 QPS
>    - 企业认证（尊享版）：200 万字符/月，100 QPS
> 5. 在「管理控制台」→「开发者信息」获取：
>    - **APP ID**（一串数字）
>    - **密钥**（Secret Key）
>
> 拿到 APP ID 和密钥后告诉我。

等待用户提供 APP ID 和 Secret。

### 3.6.4 配置凭证

收到凭证后：

> 凭证可以两种方式存储：
> - **配置文件**（推荐，不污染命令行历史）：写入 `~/.baidu_translate`
> - **环境变量**：`export BAIDU_APPID=...` `export BAIDU_SECRET=...`

推荐写入配置文件：

```bash
# 创建 ~/.baidu_translate（如果还不存在）
cat > ~/.baidu_translate << 'EOF'
BAIDU_APPID=<用户提供的APPID>
BAIDU_SECRET=<用户提供的密钥>
EOF
```

> ⚠️ **绝不把 APPID/Secret 明文写入 CLAUDE.md 或任何会被提交到 git 的文件。**
> CLAUDE.md 只写 `export BAIDU_APPID=''` 和 `export BAIDU_SECRET=''` 作为占位符。

### 3.6.5 解释 Endpoint 并询问网络环境

> **第二步：API 端点配置**
>
> Baidu 翻译 API 的默认地址是 `https://fanyi-api.baidu.com/api/trans/vip/translate`。
>
> 如果你的运行环境**有固定公网 IP**（如云服务器、专线），可以直接用默认地址。
>
> 如果你的运行环境**没有固定 IP**（家庭宽带、移动热点），百度 API 可能拒绝连接，需要用一台有固定 IP 的服务器做**反向代理**（nginx）。

询问用户：

> 你的运行环境有固定公网 IP 吗？
>
> - **有固定 IP / 不确定** → 先用默认地址测试，不通再说
> - **需要代理** → 引导配置 nginx

### 3.6.6 情况 A：使用默认地址

> 好的，使用百度官方 API 地址。后续如果连接超时，再切代理。

写入 CLAUDE.md 时不写 `BAIDU_API_ENDPOINT`（走默认值）。跳到 3.6.9 验证。

### 3.6.7 情况 B：需要 nginx 代理

**先收集信息，再生成配置。**

询问用户：

> 请提供以下信息：
>
> 1. **代理服务器 IP**（有固定公网 IP 的服务器地址）
> 2. **端口号**（想用哪个端口？建议 8890，只要不冲突就行）

收到后，根据用户提供的 IP 和端口生成 nginx 配置文件内容：

> 在代理服务器（`<IP>`）上，创建 `/etc/nginx/conf.d/baidu-translate.conf`：
>
> ```nginx
> server {
>     listen       <用户指定的端口>;
>
>     location / {
>         proxy_pass https://fanyi-api.baidu.com;  # 百度官方 API
>         proxy_ssl_server_name on;                # 必须：SNI 握手
>         proxy_set_header Host fanyi-api.baidu.com;
>     }
> }
> ```
>
> 然后启动：
> ```bash
> sudo nginx -t                    # 检查配置语法
> sudo systemctl reload nginx      # 重载生效
> ```
>
> 在本地机器上验证代理是否通：
> ```bash
> curl http://<IP>:<端口>/api/trans/vip/translate
> # 应返回 {"error_code":"52003","error_msg":"UNAUTHORIZED USER"}
> # 52003 = 鉴权失败（正常，因为没带参数），说明代理通了
> ```

如果用户自己不确定端口是否被占用，帮用户检查：

```bash
# SSH 到服务器后执行
ss -tlnp | grep <端口>   # 空 = 可用
```

### 3.6.8 写入凭证和端点

凭证写入 `~/.baidu_translate`：

```
BAIDU_APPID=<APPID>
BAIDU_SECRET=<密钥>
BAIDU_ENDPOINT=http://<IP>:<端口>/api/trans/vip/translate
```

CLAUDE.md 写入（**不写明文凭证，只写占位符 + endpoint**）：

```bash
# ── Baidu 翻译（可选 — Whisper 输出 ja→zh）──
# 凭证在 ~/.baidu_translate，注册: https://fanyi-api.baidu.com/
export BAIDU_APPID=''
export BAIDU_SECRET=''
export BAIDU_API_ENDPOINT='http://<IP>:<端口>/api/trans/vip/translate'
# 如果用自建代理，取消注释下一行：
# export BAIDU_API_ENDPOINT='http://<IP>:<端口>/api/trans/vip/translate'
```

### 3.6.9 验证

```bash
python -c "
from fix.translate_srt import baidu_translate, load_credentials
appid, secret, endpoint = load_credentials()
if appid and secret:
    result = baidu_translate('こんにちは', appid, secret, source='ja', target='zh', endpoint=endpoint)
    if result:
        print(f'✅ Baidu 翻译配置成功: こんにちは → {result}')
    else:
        print('❌ 翻译失败，请检查 APPID/Secret/endpoint')
else:
    print('⚠️ 未找到凭证，请检查 ~/.baidu_translate')
"
```

期望输出 `✅ Baidu 翻译配置成功: こんにちは → 你好`。

**验证失败时**：
- 超时（10060）→ 网络不通，建议切代理
- 52003 UNAUTHORIZED USER → APPID/Secret 错误
- 54003 → 频率限制，等一秒重试

---

## Step 4：项目特征自动检测

扫描目标字幕目录，自动检测：

1. **格式**：SRT 还是 ASS（看文件扩展名）
2. **语言**：扫描几个文本，判断 ja（有假名）还是 zh（有汉字+无假名）

将检测结果告知用户确认：

> 检测到：
> - 格式: <SRT/ASS>
> - 语言: <日语/中文>
>
> 是否正确？

确认后写入 CLAUDE.md。**同时告知语言选择的影响**：

> **日语 (ja)**：Whisper kotoba 模型 → jamdict 词典过滤 → 片假名/汉字专名提取
> **中文 (zh)**：Whisper 需中文模型 → jieba 分词 + 词典过滤 → 汉字专名提取
> 无视频时两种语言均可残血运行（跳过 Whisper，扫描 + 专名统一照常）。

---

## Step 5：生成 CLAUDE.md

读取 `templates/CLAUDE-template.md`，将用户的回答填入模板：

- `<PROJECT_NAME>` → 项目目录名
- `<target_sub_dir>` → 用户提供的目标字幕目录名
- `<input_sub_dir>` → 字幕子目录名（同 `<target_sub_dir>`，用于 `--input-dir` 参数）
- `<video_dir>` → 用户提供的视频目录，或"无"
- `<ref_sub_dir or "无">` → 用户提供的参考字幕目录，或"无"
- `<skill_scripts_dir>` → 当前 skill 的 `scripts/` 目录绝对路径
- `<whisper_cli_path>` → Whisper CLI 路径
- `<main_model_path>` → 主模型路径
- `<backup_model_path>` → 备用模型路径
- `<project_root>` → 当前项目目录
- `<SRT_or_ASS>` → 自动检测的格式
- `<ja_or_zh>` → 自动检测的语言

**绝不硬编码任何用户路径到 skill 文件。所有路径只写入 CLAUDE.md。**

---

## Step 6：开发者检查

向用户提问：

> 你是这个 skill 的开发者/维护者吗？（即需要修改脚本、改进 skill 本身）
>
> - **是** → 开启 Git 自动备份 + 加载维护规则
> - **否** → 跳过（推荐）

如果选**是**：
- 在 CLAUDE.md 末尾追加维护规则引用（指向 `dev/maintenance.md`）
- 后续运行中加载 `dev/` 目录内容

如果选**否**：
- 不提 git
- 不加载 `dev/` 目录
- 所有修改直接写入 SRT（无 git 备份步骤）

---

## Step 7：完成

告知用户初始化完成，摘要配置：

> 初始化完成！配置摘要：
> - 项目: <name>
> - 格式: <SRT/ASS> | 语言: <ja/zh>
> - 视频: <video_dir>
> - Whisper: <model_name>
> - 参考字幕: <有/无>
> - 开发者模式: <是/否>
>
> 下次运行 skill 时将直接进入校对流程。如需重新初始化，删除 CLAUDE.md 中的 `SKILL INITIALIZED: true` 行即可。

写入 CLAUDE.md 末尾：`## SKILL INITIALIZED: true`
