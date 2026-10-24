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

## 11. P2P（BitTorrent）：B 站没有的老片

老番的视频通常只存在于公开 BT 发布。`scripts/acquire/p2p.py` 把「搜索引站 →
拿 `.torrent` 解析文件清单 → 交给本机客户端 → 按清单核对 → 归位媒体库」串成一条线：

```bash
# 本机装了什么、下载到哪、走什么代理
python scripts/acquire/p2p.py clients

# 四个索引站一起搜（nyaa / dmhy / mikan / acg.rip）
python scripts/acquire/p2p.py search -q "じゃりン子チエ" --limit 6

# 先 dry-run 看清单、体积和目标目录，再真下
python scripts/acquire/p2p.py grab --torrent-id nyaa:1927106 \
    --series 小麻烦千惠 --media-type tv --dry-run
python scripts/acquire/p2p.py grab --torrent-id nyaa:1927106 --series 小麻烦千惠

# 下载中/完成：逐文件比对 .torrent 里的 length，不信客户端的「100%」
python scripts/acquire/p2p.py status

# 完成后搬进 <media root>/<category>/TV/<series>
python scripts/acquire/p2p.py collect --series 小麻烦千惠 --dry-run
```

- **`.torrent` 优先于磁力**：只有种子带文件清单，才能判断「是不是真下完了」；
  磁力没有清单时 `grab` 只报任务已交给客户端，不写 manifest；
- manifest 与状态写在 `data/temp/p2p/`（项目有 `data/` 时），`status` 不依赖客户端 API；
- 迅雷路径/下载目录**自动探测**（`magnet:` 协议处理器 + `Profiles/config.json`），
  本机路径不写进仓库；`--thunder-exe` / `--download-dir` 可覆盖；
- 迅雷 12 的「新建任务面板」需要**人工点一次「立即下载」**。面板是 Electron，
  UIA 拿不到控件树；自动确认统一走 `kf-personal-service` 的 **GUI 任务脚本**
  （`scripts/ui/tasks.py run thunder-download`，本 skill 只调用、不复制点击逻辑）：
  - 脚本位置：`KF_SERVICE_DIR` 环境变量，或 `local.json` 的 `paths.kf_service`；
  - `grab` 派单成功后默认就会调它点确认；`--no-confirm` 只派单不点，
    `--confirm` 强制要求（找不到脚本就明说「请人工点」，不假装成功）；
  - 落点：`grab --save-dir <绝对路径>` 透传给任务脚本，走「传统选择方式」改「下载到」；
  - 退出码：0 已确认 / 2 输入有问题 / 3 被远程守卫拦下（此时请人工确认或等会话结束）；
- **远程协作期间禁止开启迅雷等占用型软件**。远程同步开发时迅雷会吃满带宽、磁盘
  和 CPU，把远程会话和同步挤掉。`grab` 派单前会跑远程守卫
  （`acquire/remote_guard.py`：进程表里出现 `GameViewerServer.exe` / `GameViewer.exe`，
  或显式 `KF_REMOTE_SESSION=1`），命中就**在拉起迅雷之前**退出 3。确需现在下载，
  由用户明确同意后加 `--allow-thunder`；`--dry-run` 只提示不拦；
  - 只看 `GameViewerService` 不算数：它是自启动服务，常驻不代表有人正在远控；
  - 探测不到的信号（换了别的远控软件）用 `KF_REMOTE_SESSION=1` 手动声明；
- **落点先确认再点下载**：面板「下载到」用的是迅雷自己的「上次使用」目录
  （`PathAndCategory.LastUsedPath`），不一定等于 `local.json` 的媒体库根。
  要改路径：面板右下角文件夹图标 →「传统选择方式」→ 在**地址栏**输入目标目录。
  系列目录**不用预先建**：BT 任务会自己生成发布名文件夹（如
  `[AniArchive] Jarinko Chie (1981) [BD][1496x1080]`），收片时再 `collect` 归位；
- BT 下载中迅雷写 `<文件名>.bt.xltd` 临时文件，下完才改名；`status` 会同时认这两者，
  否则下载中永远显示 0%；
- 下载落点不一定等于默认目录：`status` / `collect` 会依次在默认目录、
  LastUsedPath、manifest 记录之间找。**注意大小写**：Windows 上
  `<盘>:\Video` 和 `<盘>:\videos` 可以是两个不同目录；媒体库以 `local.json`
  的 `paths.media` 为准（`<root>/<category>/<KIND>/<series>`），别被面板里的
  同名路径骗了。下到别处也能 `collect` 搬回来，但更省事的是把迅雷默认下载目录
  设成媒体库的 TV 目录；
- 索引站可达性：nyaa / dmhy / mikan / acg.rip 本机直连常超时，脚本默认读系统代理
  （`--proxy` 可覆盖，`--no-proxy` 关闭）。

选片参考（同一部片实测）：BD 1080p HEVC 全集 ≈ 25 GiB；BD-BOX + 剧场版 ≈ 55 GiB；
BDMV 原盘 ≈ 185 GiB。**先看目标盘余量再下**，宁可选小一档。

### 索引站：接入了哪些、还差哪些

`search` 默认跑四个站（`DEFAULT_SITES`），都是 RSS/HTML 适配器：

| 站 | 适配器 | 检索入口 | 状态 |
|---|---|---|---|
| nyaa.si | `search_nyaa` | RSS `?page=rss&q=` | 可用（老片主力） |
| share.dmhy.org 动花园 | `search_dmhy` | `/topics/list?keyword=` | 可用 |
| mikanani.me 蜜柑 | `search_mikan` | `/RSS/Search?searchstr=` | 可用 |
| acg.rip | `search_acg` | `/.xml?term=` | 可用 |
| ta.sefan.cc | — | 无公开检索接口 | **不接入**：2026-10 实测首页是混淆过的 iframe 壳（解出 `cdn.sofan.one:65533`，用 base64 + 混淆 JS 拼路径），没有可稳定复用的检索端点；别为它写猜测式适配器 |

想加下一档（如 AnimeGarden 的开放 BT 接口）时，先确认真实端点与字段，
再写适配器 + 一个离线用例（喂固定 HTML/XML，不联网）；不清楚端点就不要猜。

> 仅个人非商业使用；公开分发、批量抓取都不在支持范围内。

### 选型记录（2026-10 实测）

本机没有 qBittorrent / aria2 / libtorrent，只有迅雷 12.4.7（VIP）。GitHub 上的
活跃度（star / 最近提交，2026-10-21 查询）供换机器时参考：

| 方案 | 活跃度 | 接入方式 | 本机结论 |
|---|---|---|---|
| 迅雷 12（本机） | 商业软件 | `magnet:` 协议处理器 → `Thunder.exe "%1" -StartType:magnet` | **采用**：VIP 加速，但新建任务面板要人工确认一次 |
| qBittorrent | 41.0k / 2026-10-20 | Web API（`qbittorrent-api` 516 star） | 未安装；要无头/常驻时首选 |
| aria2 | 43.2k / 2026-06-25 | RPC（`aria2p` 575 star），磁力靠 DHT | 未安装；轻量兜底 |
| rqbit | 1.9k / 2026-10-09 | Rust，本地 HTTP API | 备选 |
| webtorrent-cli | 1.4k / 2026-10-15 | Node CLI | 备选 |
| Jackett / Prowlarr | 16.2k / 7.4k | 索引器聚合 | 不需要：四个 RSS 站够用 |
| AnimeGarden | 1.4k / 2026-10-20 | 动花镜像 + 开放 BT 接口 | 中文圈搜索的备选后端 |
| Torrent-Api-py | 426 / 2025-02 | 多站聚合 API | 维护放缓，暂不接入 |

索引站实测（走系统代理）：nyaa / dmhy / mikan / acg.rip 均可访问，老片主要来自 nyaa。
同一部片的发布体积：BD 1080p HEVC 全集 ≈ 25 GiB，BD-BOX + 剧场版 ≈ 55 GiB，
BDMV 原盘 ≈ 185 GiB——下之前先看盘余量。


