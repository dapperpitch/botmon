#!/usr/bin/env python3
"""夏07 / 贾维斯 监测台 —— 托盘常驻 + 面板窗口。

用法： python tools/botmon/tray.py
打包： pyinstaller --onefile --windowed --name BotMonitor tray.py
"""
import os


def _log_crash(exc):
    """--windowed 打包后没有控制台，异常必须落盘才看得见（连 import 阶段也要）。"""
    import datetime
    import traceback
    p = os.path.join(os.environ.get("TEMP", "."), "botmon_error.log")
    try:
        with open(p, "a", encoding="utf-8") as f:
            f.write("\n==== %s ====\n" % datetime.datetime.now().isoformat())
            f.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    except Exception:
        pass


try:
    import json
    import sys
    import threading
    import time
    import urllib.request
    import webbrowser

    import pystray
    from PIL import Image, ImageDraw, ImageFont

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import server as botmon  # 同目录的 server.py
except BaseException as _e:  # noqa: BLE001
    _log_crash(_e)
    raise

PORT = int(os.environ.get("BOTMON_PORT", "8791"))
BASE = "http://127.0.0.1:%d" % PORT

ICON = None
TRAY = None
WINDOW = None
NOTIFIED = {"offline": False}


def make_icon(color):
    """4 倍画再缩小，边缘才不糊。"""
    s = 256
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((8, 8, s - 8, s - 8), fill=color)
    try:
        f = ImageFont.truetype("arialbd.ttf", 96)
    except Exception:
        f = ImageFont.load_default()
    d.text((s / 2, s / 2 - 6), "07", font=f, fill="white", anchor="mm")
    return im.resize((64, 64), Image.LANCZOS)


def status():
    try:
        with urllib.request.urlopen(BASE + "/api/status", timeout=4) as r:
            d = json.load(r)
    except Exception:
        return "red", "采集不到数据", {}
    s = d.get("data") or {}
    mi = s.get("mi") or {}
    cont = s.get("containers") or {}
    if not (cont.get("astrbot") or "").startswith("Up"):
        return "red", "AstrBot 容器没在跑", s
    if mi.get("online"):
        return "green", "贾维斯在线", s
    if (mi.get("qr_age") is not None) and mi["qr_age"] < 240:
        return "yellow", "贾维斯掉线，等扫码", s
    return "red", "贾维斯掉线（二维码也停了）", s


COLORS = {"green": (52, 199, 123, 255), "yellow": (240, 180, 41, 255),
          "red": (255, 92, 92, 255)}


def refresh():
    global ICON, WINDOW
    color, text, s = status()
    if ICON:
        ICON.icon = make_icon(COLORS[color])
        ICON.title = "夏07 / 贾维斯监测台 — %s" % text
        try:
            ICON.update_menu()
        except Exception:
            pass
    # 掉线弹一次通知
    if color != "green" and not NOTIFIED["offline"]:
        NOTIFIED["offline"] = True
        if ICON:
            try:
                ICON.notify("%s\n%s" % (text, "点这里看面板" if False else ""),
                            "双 Bot 监测台")
            except Exception:
                pass
    if color == "green":
        NOTIFIED["offline"] = False
    return color, text, s


def menu_text():
    color, text, _ = status()
    return "状态：%s" % text


def open_panel(icon=None, item=None):
    global WINDOW
    try:
        import webview
    except Exception:
        webbrowser.open(BASE)
        return
    if WINDOW is None:
        WINDOW = webview.create_window("夏07 / 贾维斯 监测台", BASE,
                                       width=1120, height=820, min_size=(900, 640))
        # 设了 BOTMON_DEBUG=1 就打开开发者工具（右键检查 / F12），排查页面问题用
        _dbg = os.environ.get("BOTMON_DEBUG") == "1"
        webview.start(debug=_dbg)   # 阻塞到窗口关闭
        WINDOW = None
    else:
        try:
            WINDOW.show()
        except Exception:
            webbrowser.open(BASE)


def action(key):
    def _run(icon=None, item=None):
        def worker():
            try:
                req = urllib.request.Request(BASE + "/api/action?do=" + key, method="POST")
                urllib.request.urlopen(req, timeout=180).read()
            except Exception:
                pass
        threading.Thread(target=worker, daemon=True).start()
    return _run


def quit_app(icon=None, item=None):
    if TRAY:
        TRAY.stop()
    os._exit(0)


def build_tray():
    global TRAY
    TRAY = pystray.Icon(
        "botmon", make_icon(COLORS["green"]), "夏07 / 贾维斯监测台",
        menu=pystray.Menu(
            pystray.MenuItem(menu_text, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("打开面板", open_panel, default=True),
            pystray.MenuItem("重启贾维斯（20 秒）", action("restart-napcat")),
            pystray.MenuItem("重启 AstrBot（30 秒）", action("restart-astrbot")),
            pystray.MenuItem("跑一次看门狗", action("watchdog")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", quit_app),
        ))
    ICON = TRAY
    return TRAY


def watcher():
    while True:
        try:
            refresh()
        except Exception:
            pass
        time.sleep(6)


def _update_boot():
    """启动自愈：上次换文件断在半路的话，把旧版还原回来。

    顺手在程序目录放一份 rollback.cmd —— 万一新版本起不来，
    用户双击它就能退回上一版，不需要程序自己活着。
    """
    try:
        import apply_update
    except Exception:
        return
    try:
        info = apply_update.recover()
        if info.get("restored"):
            print("[update] 上次替换中断，已还原上一版", flush=True)
        if info.get("updated_to"):
            print("[update] 已更新到 v%s" % info["updated_to"], flush=True)
        if info.get("failed_to"):
            print("[update] v%s 没能生效，可在设置里回滚" % info["failed_to"], flush=True)
        apply_update.write_rollback_cmd()
    except Exception as e:  # noqa: BLE001
        print("[update] 启动自愈失败: %s" % e, flush=True)


def main():
    global ICON
    _update_boot()
    threading.Thread(target=botmon.stream_loop, daemon=True).start()
    threading.Thread(target=lambda: botmon.ThreadingHTTPServer(
        ("127.0.0.1", PORT), botmon.H).serve_forever(), daemon=True).start()
    time.sleep(1.0)
    tray = build_tray()
    ICON = tray
    threading.Thread(target=watcher, daemon=True).start()
    # 托盘跑在自己的线程，主线程留给窗口
    threading.Thread(target=tray.run, daemon=True).start()
    time.sleep(0.6)
    open_panel()


def _log_crash(exc):
    """--windowed 打包后没有控制台，异常必须落盘才看得见。"""
    import datetime
    import traceback
    p = os.path.join(os.environ.get("TEMP", "."), "botmon_error.log")
    with open(p, "a", encoding="utf-8") as f:
        f.write("\n==== %s ====\n" % datetime.datetime.now().isoformat())
        f.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))


if __name__ == "__main__":
    try:
        main()
    except BaseException as e:  # noqa: BLE001
        _log_crash(e)
        raise
