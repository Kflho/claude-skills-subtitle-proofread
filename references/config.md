# 配置分两档：全局 vs 项目

## 1. 全局配置（机器级）

位置：harness 副本的 `local.json`（`~/.agents/skills/subtitle-proofread/local.json`），
**不进 git**。只放很少变化、跟机器绑定的东西：

```text
models_dir / asr_model / aligner_model
ffmpeg / whisper_cli / qwen_python
LLM_API_KEY / LLM_BASE_URL / LLM_MODEL
paths.bili23_config（Bili23 程序的安装/配置文件位置）
```

模板：`local.example.json`；用 `scripts/harness/init_local.py --force` 生成/更新。

## 2. 项目配置（随项目变）

位置：**项目根目录的 `project.json`**，**不进项目 git**（加入项目 `.gitignore`）。

```json
{
  "bili23": {
    "download_dir": "<download-root>/<项目>/downloads"
  },
  "translation": {
    "target_lang": "zh-CN",
    "style": "..."
  }
}
```

适合放：

- Bili23 下载目录（每个项目通常不同）；
- 目标语言/风格/术语表路径；
- 字幕输出、staging、参考字幕目录；
- 项目级的开关。

## 3. 取值优先级

```text
CLI 参数
  > project.json
  > local.json（全局兜底）
  > 默认值
```

Bili23 的 `download` 现在会：

1. 读 `--download-dir`；
2. 读 `--project <dir>/project.json` 的 `bili23.download_dir`；
3. 读 `local.json` 的 `paths.bili23_download_dir`（全局兜底）；
4. 都没有 → **直接报错，要求先问用户**，不会猜、不会沿用上一项目。

## 4. 先问用户，再写配置

下载路径这类信息不要让 agent 猜。标准动作：

```bash
# 1) 问用户：这个项目下载到哪个目录？
# 2) 完全退出 Bili23（它会覆写 config.json）
python scripts/acquire/bili23.py set-download-path "<dir>" --project "<项目根>"
# 3) 启动 Bili23，再下载
```

`set-download-path` 会同时：

- 改 Bili23 的 `Download.download_path`（MCP 实际使用这个）；
- 写 `<项目>/project.json` 的 `bili23.download_dir`；
- 若用 `--scope global`，写 `local.json` 的全局兜底。

## 5. 审查建议

`references/release.md` 的发布检查之外，配置还要看：

- `local.json` 是否被 git 跟踪？跟踪了就是泄露机器路径/密钥，必须移除；
- `project.json` 是否被项目 git 跟踪？里面是绝对下载路径，建议 ignore；
- 全局配置里是否混进了项目级键（`bili23.download_dir`、`output_dir`）？
  有就搬到 `project.json`；
- 相对仓库路径（`scripts/...`、`rules/...`）可以提交；绝对本机路径不行。
