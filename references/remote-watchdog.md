# 远程连接守护（GameViewer / UU 远程）

> 背景：调试 FlClash 系统代理/TUN 时可能把远程连接踢掉。
> 这个守护只做一件事：**本机服务/进程掉了就拉起**，不碰代理、防火墙、路由。

## 1. 谁在提供远程

| 组件 | 作用 |
|---|---|
| `GameViewerService`（Windows 服务，自启动） | 远程链路的核心服务 |
| `GameViewerServer.exe` | 监听/转发远程会话 |
| `GameViewer.exe` | 托盘 UI，可选；缺 Server 时用它带起来 |

只要 **服务 + Server 进程** 在，UI 关着也能远控。

## 2. 用法

```bash
python scripts/net/gameviewer_watchdog.py --status
python scripts/net/gameviewer_watchdog.py --once
python scripts/net/gameviewer_watchdog.py --watch --interval 15
python scripts/net/gameviewer_watchdog.py --install-task
```

- `--status`：服务/进程状态，掉线返回 1；
- `--once`：缺服务就 `sc start`，缺 Server 就启动 `GameViewer.exe`，然后复查；
- `--watch`：常驻循环（Ctrl+C 退出）；
- `--install-task`：建一个「每分钟跑一次 `--once`」的计划任务
  （`SubtitleProofread-GameViewerWatchdog`）。

安装目录来源：`--dir` > 环境变量 `GAMEVIEWER_DIR` > harness `local.json`
的 `paths.gameviewer_dir`。仓库里不写死机器路径。

日志：`%LOCALAPPDATA%\subtitle-proofread\logs\gameviewer_watchdog.log`
（每分钟健康时不会刷日志，只在状态变化/拉起时写）。

## 3. 注意

- `sc start` / 重启服务可能需要管理员；计划任务默认 `LIMITED`，
  若日志里出现拒绝访问，用管理员终端跑一次 `--once` 或改用高权限任务。
- 守护**不会**修改系统代理/TUN。远程掉线后先跑 `--status`；
  如果服务和 Server 都在，那问题在网络层（代理/TUN/防火墙），不要反复重启软件。
- 调试 FlClash 时，远程会话期间不要开 TUN；系统代理用完就关。
- 远程依赖出站连接；如果 FlClash 的 TUN 捕获了全部流量且节点异常，
  远程会表现为“进程在但连不上”——先关 TUN/代理，再跑 `--status` 确认。
