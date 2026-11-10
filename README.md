# BotMonitor · 双 QQ Bot 监测台

一个跑在 Windows 上的桌面小工具，用来盯住服务器上的两个 QQ 机器人：
**夏07**（QQ 官方 Bot）和 **小米**（NapCat 协议端）。

托盘常驻、状态变色、掉线弹通知；点开是一个面板：在线状态、二维码、服务器负载、
看门狗判定、审计累计；还有一张**会自转的三维银河星图**，把运维知识库画成星系。

> 它是**观测台**，不是运行环境。机器人跑在远端服务器上，这个程序只是把状态拉过来看。
> 电脑关了、程序没开，机器人照常工作 —— 真正的自愈在服务器上的看门狗。

---

## 功能

**状态**
- 两个 bot 的在线 / 掉线、容器状态、近 10 分钟消息量
- 小米掉线时自动显示最新二维码（每 2 分钟自动换新）
- 服务器内存、磁盘、负载、运行时长
- 看门狗最近判定原文（区分“等扫码”还是“容器卡死”）
- 群聊 / 私聊的审计累计与待采纳规则数

**知识星图（三维银河）**
- 装饰粒子一次性生成三维坐标，逐帧按相机旋转 + 透视投影，**有真实厚度和景深**
- 近 2.6 万颗粒子：旋臂（每条臂独立相位与拧度，带断续）、核球、星晕、远景、巨星
- 18 个案例是「知识星」，**落在旋臂上**，白核 + 彩色柔光；悬停放大并显示名称
- 拖动绕银河观察、滚轮拉近拉远、可切换三种形态（旋涡 / 椭圆星云 / 不规则）
- 加载是**分阶段进度条 + 实时计时**，一眼能分清“在算”还是“卡死”
- 详情浮在画布内，章节可折叠展开全文；搜索命中后镜头会飞过去

**操作**
- 一键重启小米 / 重启 AstrBot / 跑一次看门狗 / 拉两边日志（重启类有二次确认）

**托盘**
- 🟢 正常 / 🟡 等扫码 / 🔴 挂了，掉线弹 Windows 通知

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

**为什么用常驻连接而不是定时轮询**：早期版本每 5 秒新建一次 SSH，一小时后 `sshd` 开始
`banner exchange` 超时，症状和「服务器卡死」一模一样。现在是一条长连接，服务器端
`while true` 循环输出，客户端逐行读；断线走**指数退避**（30 秒 → 5 分钟封顶）。

---

## 安装

1. 从 [Releases](../../releases) 下载 `BotMonitor.exe`
2. **放到纯 ASCII 路径**，例如 `C:\BotMonitor\`
3. 双击运行，第一次需要配置（见下）

> ⚠️ 放在含中文的路径（比如 OneDrive 的 `文档`）下会**闪退且无任何提示** ——
> PyInstaller onefile 的限制。

## 配置

`%APPDATA%\BotMonitor\config.json`（**不会**进仓库）：

```json
{
  "ssh_key": "C:\\Users\\you\\.ssh\\id_ed25519.pem",
  "ssh_host": "root@your-server-ip",
  "remote_probe": "/opt/botmon/probe.py",
  "kb_path": "C:\\path\\to\\运维知识库.md"
}
```

也可用环境变量 `BOTMON_KEY` / `BOTMON_HOST` 覆盖。

服务端：

```bash
mkdir -p /opt/botmon
# 把 src/probe.py 传上去
python3 /opt/botmon/probe.py | head -c 200   # 应输出一行 JSON
```

---

## 从源码运行 / 打包

```bash
cd src
pip install pystray pywebview pillow
python tray.py
```

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

- 仓库：<https://github.com/dapperpitch/botmon>（「设置 → 打开仓库」跳这里）
- 版本号在 `VERSION`；「设置 → 检查更新」读 GitHub Releases 比对
- **不自动下载替换**：更新中途断电、杀毒误报、文件占用这些没处理好之前，
  自动替换只会把软件搞坏
- 发版：改 `VERSION` → `git tag v0.4.0` → Releases 上传 exe

---

## 目录结构

```
├── VERSION
├── config.example.json
├── assets/icon.ico
├── server/              # 部署到 ECS 的脚本（不属于桌面端）
│   ├── collect_once.py  # 只读采集：docker/系统/自检
│   ├── collect_loop.py  # systemd 常驻，每 20 秒跑一次写 /tmp/botmon.json
│   ├── brain_check.py   # 记忆体检（低置信/陈旧/未命中/卡片缺失）
│   └── brain_export.py  # brain.db → 3D 脑图数据
└── src/
    ├── tray.py      # 入口：托盘 + 窗口
    ├── server.py    # 本地 HTTP 服务 + 页面 + SSH 流式采集
    ├── starui.py    # 星图界面（HTML + 三维银河渲染）
    ├── starmap.py   # 知识库 → 星图数据
    ├── probe.py     # 部署到服务器，只读采集
    └── updater.py   # 版本检查
```

---

## 记忆大脑

面板左侧 **◍** 有两个页签：

- **3D 脑图**：和 DSH 插件同源（`brain.html` + `brain-view.html` + `module.js`），
  数据走 `/astrbot-xia07/brain/data?which=mike|xia07`，同一份 `brain_export.py`
- **体检**：低置信、超过 30 天未出现、抽取后从未命中、记忆多但无卡片 —— 按严重度排序

> 采集为什么不在桌面端直接跑：这台机器 `docker ps` 要 12 秒、`docker exec` 3~5 秒，
> 每 15 秒跑一次必然超时。所以服务器上有 `collect_loop.py` 常驻，桌面端只读缓存文件。

---

## 踩过的坑

| 坑 | 症状 | 原因 |
|---|---|---|
| exe 放中文路径 | 双击闪退，无提示 | PyInstaller onefile 限制 |
| 控制台窗口一闪一闪 | 满屏黑框 | `--windowed` 进程起 `ssh` 子进程会新建控制台 |
| 采集全空 | `'NoneType' object has no attribute 'strip'` | Windows `subprocess` 默认 GBK 解码 |
| SSH 突然连不上 | `banner exchange` 超时 | 高频新建连接 + sshd 假死 |
| 星图白屏 | 一万五千颗 30px 粒子叠加过曝 | 世界单位当成像素用了 |
| 星图 0.4 帧 | 一开就冻住 | 粒子每帧重建；且外层循环写成粒子数 → 1.28 亿次迭代 |

---

## 已知限制

- 只支持单台服务器、两个 bot（两张卡片写死）
- 依赖 SSH 免密登录，不支持密码认证
- 托盘通知走系统气泡，专注助手开启时可能被吞
- 星图不表达语义关系：案例落在旋臂上只是布局，**位置不代表知识关联**
- 改服务器地址要手改 `config.json`，没有图形化配置界面

## 开发状态

**v0.5.1** — 仓库迁到 `dapperpitch/botmon`，版本检查指向新地址

- [x] v0.2 图标导航、设置页、版本检查
- [x] v0.3 知识星图
- [x] v0.4 三维银河（透视、景深、形态切换、加载进度）
- [x] v0.5 脑图（记忆 / 实体关系）
- [ ] v0.6 自动更新与回滚
- [ ] v0.7 语音试听、审计详情页

## 许可

个人项目，暂未选择开源协议。
