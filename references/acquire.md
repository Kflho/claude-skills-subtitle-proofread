# 资源获取：B 站 / Bili23 Downloader

> 用途：把「只有 B 站链接」的项目变成 pipeline 能吃的 `视频 + 字幕`。
> Bili23 是 GUI 程序（v2.20，GPL-3.0），**没有对外 CLI**；但它自带 MCP server，
> 这是唯一稳定的脚本化入口。

## 1. 为什么不用 PyPI 的 `bili23` CLI

PyPI 包 `bili23` 最后更新是 2022-12（v1.1.1）：

- `--subtitle` 仍按旧 XML 接口解析 `<subtitle>`，B 站早已改成 JSON，
  实测直接 `IndexError`；
- 音视频合并步骤同样会崩，只留下未合并的 `video.mp4` / `audio.mp3`。

所以：**元数据还能查，下载链路不可用**，不要接进 pipeline。

## 2. Bili23 MCP：怎么开

Bili23 v2.20 的 MCP 默认关闭，配置在
`%APPDATA%\Bili23 Downloader\config.json` 的 `MCP` 节：

```json
{"mcp_enabled": false, "mcp_port": 23330, "mcp_token": ""}
```

开启步骤（**先完全退出 Bili23**，否则退出时会被覆盖）：

```bash
python scripts/acquire/bili23.py enable
# 再启动 Bili23；MCP 监听 127.0.0.1:23330/mcp，Bearer token 校验
python scripts/acquire/bili23.py status
```

`enable` 会先备份 `config.json.bak`，并只在 token 为空时生成新 token。
token 只存在 Bili23 自己的配置里，本 skill 不复制、不打印。

## 3. 命令

```bash
# 登录状态、端口、下载目录（token 不会出现在输出里）
python scripts/acquire/bili23.py status

# 解析链接，列出 episode_id / 标题；链接指向的那一项带 *
python scripts/acquire/bili23.py parse "https://www.bilibili.com/video/BV..."

# 解析 + 建下载任务（默认下字幕 srt、简体中文、自动画质/mkv 容器）
python scripts/acquire/bili23.py download "https://www.bilibili.com/bangumi/play/ss..." \
  --episodes all --subtitle-format srt --subtitle-lang zh-CN --wait

# 指定集（episode_id 来自 parse）
python scripts/acquire/bili23.py download "<url>" --episodes 1,3,5

# 查看任务/产物路径
python scripts/acquire/bili23.py tasks --state completed --limit 10
```

`--wait` 轮询 `get_task_status` 直到完成/失败，并打印每个任务的
`file_path`（合并后的视频）。字幕文件与视频同目录、同 basename。

## 4. 输出目录与命名

MCP 明确不允许改下载目录和命名模板：**用 Bili23 设置里的值**。
建议把 Bili23 的下载目录设为项目的一个 staging 目录，例如：

```text
<project>/downloads/          # Bili23 download_path
<project>/videos/             # pipeline 的 --video-dir（必要时改名/移动）
<project>/日语参考字幕/        # 字幕 srt 放这里
```

字幕语言用 B 站代码：`zh-CN`、`zh-Hant`、`en-US`、`ai-zh`（AI 字幕）。
空数组表示下载全部语言；不存在的语言不会生成文件。

## 5. 与 pipeline 的衔接

1. `bili23.py download ... --wait` 拿到 `file_path`；
2. 视频放进 `--video-dir`，字幕 srt 放进字幕目录（按 `EP001.srt` 规则命名，
   或直接用 `--input-dir` 指向实际目录）；
3. 正常跑 `run_all.py` / `translate_srt.py`。

## 6. 已知限制

- **GUI 必须开着**，MCP 是程序内服务，不是独立进程；
- 解析会替换界面的解析列表；用户在列表里勾选了条目时，MCP 会拒绝解析；
- 高清/大会员/部分 CC 字幕需要登录（`status` 里看 `logged_in`）；
- 同一视频已下载过会被跳过（除非传 `redownload`）；
- 项目条款：仅个人非商业使用，不要批量抓取/公开分发。

## 7. 备选（未接入）

如果以后要无头/CI 下载，Bili23 不适合；可换维护中的 CLI（如 BBDown、yutto），
但需要重新做登录/字幕格式适配，不在本 skill 当前范围内。
