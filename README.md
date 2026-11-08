# BotMonitor · 双 QQ Bot 监测台

一个跑在 Windows 上的桌面小工具，用来盯住服务器上的两个 QQ 机器人：
**夏07**（QQ 官方 Bot）和 **小米**（NapCat 协议端）。

托盘常驻、状态变色、掉线弹通知，点开是一个面板：在线状态、二维码、服务器负载、
看门狗判定、审计累计，外加几个一键操作。

> 它是**观测台**，不是运行环境。机器人跑在远端服务器上，这个程序只是把状态拉过来看。
> 电脑关了、程序没开，机器人照常工作。

---

## 功能

- **状态面板**：两个 bot 的在线/掉线、容器状态、近 10 分钟消息量
- **扫码登录**：小米掉线时自动显示最新二维码（NapCat 每 2 分钟换一次，页面自动跟）
- **服务器指标**：内存、磁盘、负载、运行时长
- **看门狗日志**：最近判定原文，一眼看出是"等扫码"还是"容器卡死"
- **审计累计**：小米群聊 / 夏07 私聊的自检问题统计与待采纳规则数
- **一键操作**：重启小米、重启 AstrBot、跑一次看门狗、看两边日志（重启类有二次确认）
- **托盘常驻**：🟢 正常 / 🟡 等扫码 / 🔴 挂了，掉线弹 Windows 通知
- **版本检查**：读 GitHub Releases，提示有没有新版

## 截图

> 待补：状态页 / 设置页各一张

---

## 怎么工作

```
┌──────────────┐   SSH 长连接   ┌──────────────────┐
│ 本机 BotMonitor │ ───────────→ │ 服务器 /opt/botmon/probe.py │
│  (托盘+窗口)   │ ←─────────── │  （只读采集，输出一行 JSON） │
└──────────────┘   15 秒一次    └──────────────────┘
```

**采集只用一条常驻 SSH 连接**，服务器端 `while true` 循环刷新并逐行输出 JSON。

> 这一点是踩坑换来的：早期版本每 5 秒新建一次 SSH 去采集，一个多小时后把 `sshd` 的
> `MaxStartups` 打满，连接开始 `banner exchange` 超时——症状和"服务器卡死"一模一样。

采集脚本**只读**：`docker ps`、`get_status`、`free`、`df`、`tail` 日志，不改任何状态。

---

## 安装

1. 从 [Releases](../../releases) 下载 `BotMonitor.exe`
2. **放到纯 ASCII 路径**，例如 `C:\BotMonitor\`
   > ⚠️ 放在含中文的路径（比如 OneDrive 的 `文档`）下会**闪退且无任何提示**，
   > 这是 PyInstaller onefile 的限制
3. 双击运行，第一次需要配置（见下）

## 配置

配置放在 `%APPDATA%\BotMonitor\config.json`（**不会**进仓库）：

```json
{
  "ssh_key": "C:\\Users\\you\\.ssh\\id_ed25519.pem",
  "ssh_host": "root@your-server-ip",
  "remote_probe": "/opt/botmon/probe.py"
}
```

也可以用环境变量 `BOTMON_KEY` / `BOTMON_HOST` 覆盖。

服务端需要放好采集脚本：

```bash
mkdir -p /opt/botmon
# 把 src/probe.py 传到 /opt/botmon/probe.py
python3 /opt/botmon/probe.py | head -c 200   # 应该输出一行 JSON
```

要求：本机有 `ssh`（Windows 10/11 自带 OpenSSH），服务器可用密钥免密登录。

---

## 从源码运行

```bash
cd src
python tray.py
```

依赖：`pystray`、`pywebview`、`Pillow`（`pip install pystray pywebview pillow`）

## 打包

```bash
pyinstaller --onefile --windowed --name BotMonitor --icon ../assets/icon.ico ^
  --add-data "../VERSION;." ^
  --hidden-import webview.platforms.edgechromium ^
  --hidden-import webview.platforms.winforms ^
  --hidden-import clr ^
  --hidden-import pystray._win32 ^
  --collect-submodules webview tray.py
```

产物在 `dist/BotMonitor.exe`（约 30 MB）。

---

## 版本与更新

```
C:\BotMonitor\
├── BotMonitor.exe
├── current.txt          # 当前版本
└── releases\<版本>\      # 可保留旧版，随时回滚
```

- 版本号在仓库根目录的 `VERSION` 文件
- 「设置 → 检查更新」读取 GitHub Releases 的最新 tag 与本地版本对比
- **不自动下载替换** —— 更新中途断电、杀毒误报、文件占用这些情况没处理好之前，
  自动替换只会把软件搞坏
- 发版：改 `VERSION` → 打 tag `v0.3.0` → Releases 上传 exe

---

## 目录结构

```
├── VERSION
├── config.example.json
├── assets/icon.ico
└── src/
    ├── tray.py      # 入口：托盘 + 窗口
    ├── server.py    # 本地 HTTP 服务 + 面板页面 + SSH 采集
    ├── probe.py     # 部署到服务器，只读采集，输出 JSON
    └── updater.py   # 版本检查（GitHub Releases）
```

---

## 已知限制

- 只支持单台服务器、两个 bot（写死两个卡片位）
- 采集依赖 SSH 免密登录，没有密码认证
- 托盘通知用 pystray 的系统气泡，Windows 专注助手开启时可能被吞
- 修改服务器地址要改 `config.json`，没有图形化配置界面

## 开发状态

`v0.2.0` — 面板 + 托盘 + 版本检查可用。计划：

- [ ] v0.3 星图（知识库可视化）
- [ ] v0.4 脑图（记忆/实体关系）
- [ ] v0.5 自动更新与回滚
- [ ] v0.6 语音试听、审计详情页

## 许可

个人项目，暂未选择开源协议。
