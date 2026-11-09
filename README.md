# BotMonitor · 双 QQ Bot 监测台

一个跑在 Windows 上的桌面小工具，用来盯住服务器上的两个 QQ 机器人：
**夏07**（QQ 官方 Bot）和 **小米**（NapCat 协议端）。

托盘常驻、状态变色、掉线弹通知；点开是一个面板：在线状态、二维码、服务器负载、
看门狗判定、审计累计；还有一张会转的**知识星图**，把运维知识库画成星系。

> 它是**观测台**，不是运行环境。机器人跑在远端服务器上，这个程序只是把状态拉过来看。
> 电脑关了、程序没开，机器人照常工作 —— 真正的自愈在服务器上的看门狗。

---

## 功能

**状态**
- 两个 bot 的在线 / 掉线、容器状态、近 10 分钟消息量
- 小米掉线时自动显示最新二维码（NapCat 每 2 分钟换新，页面自动跟）
- 服务器内存、磁盘、负载、运行时长
- 看门狗最近判定原文（区分"等扫码"还是"容器卡死"）
- 小米群聊 / 夏07 私聊的审计累计与待采纳规则数

**知识星图**
- 把 `运维知识库.md` 解析成「案例 → 章节 → 句子」三层
- 每个案例画成一个星系：随机散布、椭圆形状、中心密外圈疏
- 深空背景：星云、背景星场、暗角
- 缓慢 360° 自转；左键拖 = 平移，Shift/右键拖 = 手动转，双击 = 暂停，滚轮 = 缩放
- 悬停发光放大，点击看原文

**操作**
- 一键重启小米 / 重启 AstrBot / 跑一次看门狗 / 拉两边日志（重启类有二次确认）

**托盘**
- 🟢 正常 / 🟡 等扫码 / 🔴 挂了，掉线弹 Windows 通知

**版本**
- 读 GitHub Releases 提示新版（不自动替换，见下文）

## 截图

> 待补：状态页 / 星图页

---

## 它是怎么工作的

```
┌──────────────┐   一条常驻 SSH   ┌────────────────────┐
│ 本机 BotMonitor │ ─────────────→ │ 服务器 probe.py      │
│  (托盘 + 窗口)  │ ←───────────── │ （只读采集，逐行 JSON）│
└──────────────┘   ~15 秒一行     └────────────────────┘
```

采集脚本**只读**：`docker ps`、`get_status`、`free`、`df`、`tail` 日志，不改任何状态。

**为什么用常驻连接而不是定时轮询**：早期版本每 5 秒新建一次 SSH，一个多小时后
`sshd` 开始 `banner exchange` 超时，症状和「服务器卡死」一模一样。现在改成一条长连接，
服务器端 `while true` 循环输出，客户端逐行读；断线走**指数退避**（30 秒 → 5 分钟封顶），
任何情况下不许 30 秒内重连。

---

## 安装

1. 从 [Releases](../../releases) 下载 `BotMonitor.exe`
2. **放到纯 ASCII 路径**，例如 `C:\BotMonitor\`
3. 双击运行，第一次需要配置（见下）

> ⚠️ 放在含中文的路径（比如 OneDrive 的 `文档`）下会**闪退且无任何提示** ——
> PyInstaller onefile 的限制。桌面放快捷方式指向 `C:\BotMonitor\BotMonitor.exe` 即可。

## 配置

配置放在 `%APPDATA%\BotMonitor\config.json`（**不会**进仓库）：

```json
{
  "ssh_key": "C:\\Users\\you\\.ssh\\id_ed25519.pem",
  "ssh_host": "root@your-server-ip",
  "remote_probe": "/opt/botmon/probe.py",
  "kb_path": "C:\\path\\to\\运维知识库.md"
}
```

也可以用环境变量 `BOTMON_KEY` / `BOTMON_HOST` 覆盖。

服务端放好采集脚本：

```bash
mkdir -p /opt/botmon
# 把 src/probe.py 传上去
python3 /opt/botmon/probe.py | head -c 200   # 应该输出一行 JSON
```

要求：本机有 `ssh`（Windows 10/11 自带 OpenSSH），服务器可用**密钥免密**登录。

---

## 从源码运行

```bash
cd src
python tray.py
```

依赖：`pystray`、`pywebview`、`Pillow`

```bash
pip install pystray pywebview pillow
```

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

产物 `dist/BotMonitor.exe`，约 30 MB。

---

## 版本与更新

- 版本号在仓库根目录的 `VERSION`
- 「设置 → 检查更新」读 GitHub Releases 最新 tag 与本地比较
- **不自动下载替换**：更新中途断电、杀毒误报、文件占用这些没处理好之前，
  自动替换只会把软件搞坏。看到新版点「打开仓库」自己下

发版：改 `VERSION` → `git tag v0.3.0` → GitHub Releases 上传 exe

---

## 目录结构

```
├── VERSION
├── config.example.json
├── assets/icon.ico
└── src/
    ├── tray.py      # 入口：托盘 + 窗口
    ├── server.py    # 本地 HTTP 服务 + 页面 + SSH 流式采集
    ├── probe.py     # 部署到服务器，只读采集，输出 JSON
    ├── starmap.py   # 知识库 → 星图数据
    └── updater.py   # 版本检查（GitHub Releases）
```

---

## 踩过的坑（都已修，留作前车之鉴）

| 坑 | 症状 | 原因 |
|---|---|---|
| exe 放中文路径 | 双击闪退，没有任何提示 | PyInstaller onefile 限制 |
| 控制台窗口一闪一闪 | 满屏黑框 | `--windowed` 的进程起 `ssh` 子进程会新建控制台；要 `creationflags=0x08000000` |
| 采集全空 | `'NoneType' object has no attribute 'strip'` | Windows `subprocess` 默认按 GBK 解码，UTF-8 中文炸掉 |
| SSH 突然连不上 | `banner exchange` 超时 | 高频新建连接 + sshd 假死；改常驻连接 + 指数退避 |

---

## 已知限制

- 只支持单台服务器、两个 bot（两张卡片写死）
- 依赖 SSH 免密登录，不支持密码认证
- 托盘通知走系统气泡，Windows 专注助手开启时可能被吞
- 改服务器地址要手改 `config.json`，没有图形化配置界面

## 开发状态

**v0.3.0** — 状态面板 + 托盘 + 星图 + 版本检查

- [x] v0.2 图标导航、设置页、版本检查
- [x] v0.3 知识星图（星系式、可旋转缩放）
- [ ] v0.4 脑图（记忆 / 实体关系）
- [ ] v0.5 自动更新与回滚
- [ ] v0.6 语音试听、审计详情页

## 许可

个人项目，暂未选择开源协议。
