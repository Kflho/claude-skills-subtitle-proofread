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

## 3. 目录约定：媒体库根目录写在 local.json

视频不进项目目录。机器上的动画库根目录只写一处 —— `local.json` 的
`paths.media`，跟模型路径同一个思路：

```json
"paths": {
  "media": {
    "root": "<动画库根目录>",
    "categories": {"animation": ""},
    "kinds": {"tv": "TV", "ova": "ova", "movie": "movie"}
  }
}
```

解析结果是 `<root>/<category>/<KIND>/<系列名>`，`categories` 留空串表示种类目录
直接挂在根下。每段都按磁盘上的真实大小写匹配，所以 `TV` / `tv` 混用不用改配置。

**根目录要先问用户，不要猜。** 换机器时用 `--discover` 扫出来再让用户确认：

```bash
python scripts/lib/media_paths.py --discover            # 扫描候选库
python scripts/lib/media_paths.py --check --series "<系列名>" --media-type tv
```

项目里**什么都不记**：系列名、类型、字幕目录都写在 skill 根的
`projects_registry.json`（键 = 项目文件夹名），项目文件夹只放内容：

```json
"projects": {
  "<项目文件夹名>": {
    "series": "<系列名>",
    "media_type": "tv",
    "subtitles": {
      "reference_dir": "bin/reference_docs/参考字幕",
      "input_dir": "data/ja",
      "output_dir": "data/zh"
    }
  }
}
```

取值优先级一律是 **CLI > projects_registry.json > local.json > 推导**。

## 4. 先定类型，再落盘

TV / OVA / MOVIE 决定进哪个文件夹，所以下载前先 `plan`：

```bash
python scripts/acquire/bili23.py plan "<url>"
```

`plan` 输出集数、单集时长、猜测的类型与理由、解析出的目标目录，以及 Bili23
当前的并发设置。判定规则（在 `lib/media_paths.py` 里）：单文件且 ≥50 分钟 →
movie；多集但单集 ≥45 分钟，或 ≤6 集且单集 ≥25 分钟 → ova；其余 → tv。
猜错就让用户改，别默默下载。

确认后再下：

```bash
python scripts/acquire/bili23.py download "<url>" --episodes all \
  --media-type tv --series "<系列名>" --quality 1080P --codec AVC/H.264 \
  --container mp4 --subtitle-format srt --subtitle-lang zh-CN --project "<项目根>"
```

字幕语言用 B 站代码：`zh-CN`、`zh-Hant`、`en-US`、`ai-zh`（AI 字幕）。
空的 `available` 里没有字幕项，就说明这条视频没有 CC，只能靠 ASR。

## 5. 并发：`download_parallel` 是「同时几集」

源码里 `DownloadListModel._manageConcurrentDownloads` 按
`active_count < download_parallel` 派发排队任务；`Downloader.__init__` 用
`thread_pool.setMaxThreadCount(download_thread)` 决定**单集内分块线程数**。

所以默认 `download_parallel=1` 时就是**一集一集下**。`status` / `download`
会把这两项打出来，`1` 时还会提示。改法（必须先完全关闭 Bili23）：

```bash
# 关掉 Bili23 后
python scripts/acquire/_patch_bili_config.py <config.json 备份路径>   # 4 并发 / 8 线程
```

实测（B 站普通码率 1080P、单集 ~280 MB）：单路 4 线程约 4.4 MB/s；
4 路并发 × 8 线程仍是 **4.6 MB/s 总量** —— 瓶颈在带宽/CDN，不在并发数。
并发仍然值得开：单集排队时间变短、掉线重试更稳，但别指望线性提速。

> ⚠️ **绝对不要用 PowerShell 的 `Set-Content -Encoding UTF8` 改 Bili23 配置**：
> 它会写 UTF-8 BOM，而 Bili23 用 orjson 读配置，BOM 直接报
> `UTF-8 byte order mark (BOM) is not supported`，然后当成旧版配置「修补」，
> **把 Cookie、MCP token、下载目录一起重置成默认值**。
> 要改就用 `_patch_bili_config.py`（按字节改写、保持无 BOM），并用 python 读写。

## 6. 队列清理：MCP 没有取消接口

MCP 只有 `parse_url` / `create_download` / `list_tasks` / `get_task_status` /
`get_login_status`。重复建任务会把队列堆成几十上百条，**没法从 MCP 取消**。
清理要关掉 Bili23 直接改它的 SQLite：

```bash
# 先关 Bili23
python scripts/acquire/_clean_bili_tasks.py --show     # 看有哪些表/多少条
python scripts/acquire/_clean_bili_tasks.py --prune    # 清空排队表，保留完成历史
```

- `download_task` 是当前队列（要清的）；
- `completed_task` 是完成历史（决定「已下载跳过」），默认保留；
- 清完再按磁盘实际缺的集号重新建任务，别整批重下。

`download --redownload` 用于 Bili23 认为「已下载」但文件实际不在的情况。

## 7. B 站字幕：默认不当参考

**B 站字幕轨道绝大多数是机器生成的**（`lan` 为 `ai-zh`，接口里 `type: 1` +
`ai_status: 2`），质量远低于翻译底线：要么只有几十条、要么整集缺失、
要么专名全错。拿它当参考会污染译文，拿它当源语言更是错的。

规则：

- **默认不抓、不用**。只有在用户明确说「这套字幕是别人手工上传的」时，
  才允许当参考；
- 即使抓了，也只作**对照**（人名、专名写法），不作源语言、不作时间轴基准；
- 项目里没有参考字幕就是没有，走 ASR + 翻译，别拿 AI 字幕凑数。

需要确认某条投稿到底有没有人工字幕时：

```bash
python scripts/acquire/bili_subs.py --bvid BV... --check
```

输出会区分「人工」与「只有 AI 字幕」，并汇总哪些分 P 有人工字幕。
真要取人工轨（用户已确认来源）：

```bash
python scripts/acquire/bili_subs.py --bvid BV... --project "<项目根>"
```

输出到 `subtitles.reference_dir`（默认 `bin/reference_docs/参考字幕`），原始 JSON 落在
旁边的 `raw/`。`--include-ai` 只在用户明确要求时用。

> 判定依据是接口字段，偶尔会抖（同一条投稿两次查询返回的人工轨可能不同）；
> 数量对不上时以 `--check` 的实际结果为准，别缓存第一次的结论。

## 8. 与 pipeline 的衔接

1. `bili23.py download ... --media-type ... ` 落到库目录；
2. 视频留在库里，`--video-dir` 指向它，**不复制进项目**；
3. 参考字幕进 `bin/reference_docs/参考字幕/`，转写在 `data/ja/`，译文在 `data/zh/`；
4. 正常跑 `run_all.py` / `translate_srt.py` / `video_to_zh.py`。

## 9. 已知限制

- **GUI 必须开着**，MCP 是程序内服务，不是独立进程；
- 解析会替换界面的解析列表；用户在列表里勾选了条目时，MCP 会拒绝解析；
- 高清/大会员/部分 CC 字幕需要登录（`status` 里看 `logged_in`）；
- 同一视频已下载过会被跳过（用 `--redownload` 覆盖）；
- 项目条款：仅个人非商业使用，不要批量抓取/公开分发。

## 10. 备选（未接入）

如果以后要无头/CI 下载，Bili23 不适合；可换维护中的 CLI（如 BBDown、yutto），
但需要重新做登录/字幕格式适配，不在本 skill 当前范围内。
