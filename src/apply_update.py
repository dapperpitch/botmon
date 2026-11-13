# -*- coding: utf-8 -*-
"""换文件：等本进程退出后，由外部 cmd 完成替换并重启。

为什么不能在程序里直接覆盖自己：
    Windows 会锁住正在运行的 exe，`os.replace` 必然失败（WinError 32）。
    所以新版下载到 `BotMonitor-<版本>.new.exe` 后，本进程只负责**生成脚本并退出**，
    真正的 move 交给 cmd。

为什么用 cmd 而不是再起一个 Python：
    cmd 是系统自带、不依赖 Python 运行时；脚本里只有 move/start/tasklist，
    被杀软盯上的概率最低。中文一律不写进 cmd（cmd 默认按 ANSI 读，会乱码）。

断电安全：
    顺序是「旧版改名 .old → 新版改名正式名」。任何一步断掉，
    下次启动 `recover()` 都会把 .old 还原回去，不会出现“exe 不见了”。
"""
import json
import os
import subprocess
import sys
import time

import updater

PENDING = "update_pending.json"


def _appdata() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "BotMonitor")
    os.makedirs(d, exist_ok=True)
    return d


def pending_path() -> str:
    return os.path.join(_appdata(), PENDING)


# ---------------------------------------------------------------- 状态记录

def mark_pending(new_exe: str, from_ver: str, to_ver: str):
    """记下“正在换到哪个版本”，用来判断下次启动到底成没成。"""
    try:
        with open(pending_path(), "w", encoding="utf-8") as f:
            json.dump({"from": from_ver, "to": to_ver, "new": new_exe,
                       "at": time.time()}, f, ensure_ascii=False)
    except Exception:
        pass


def pending() -> dict | None:
    try:
        with open(pending_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def clear_pending():
    try:
        os.remove(pending_path())
    except Exception:
        pass


# ---------------------------------------------------------------- 生成脚本

SWAP_TMPL = r"""@echo off
setlocal
cd /d "{dir}"
rem clear PyInstaller env vars, or the new instance exits at once
set "_MEIPASS2="
set "_PYI_APPLICATION_HOME_DIR="
set "_PYI_PARENT_PROCESS_LEVEL="
set "_PYI_ARCHIVE_FILE="
set "_PYI_SPLASH_IPC="
set PID={pid}
set TRY=0
:wait
tasklist /NH /FI "PID eq %PID%" 2>nul | find "%PID%" >nul
if errorlevel 1 goto swap
set /a TRY+=1
if %TRY% GEQ 120 goto swap
ping -n 2 127.0.0.1 >nul
goto wait
:swap
set TRY=0
:trymove
move /y "{exe}" "{old}" >nul 2>nul
if not exist "{exe}" goto moved
set /a TRY+=1
if %TRY% GEQ 60 goto fail
ping -n 2 127.0.0.1 >nul
goto trymove
:moved
if not exist "{new}" goto fail
move /y "{new}" "{exe}" >nul 2>nul
if not exist "{exe}" goto fail
start "" "{exe}"
goto done
:fail
if not exist "{exe}" if exist "{old}" move /y "{old}" "{exe}" >nul
start "" "{exe}"
:done
del "%~f0"
"""

ROLLBACK_TMPL = r"""@echo off
setlocal
cd /d "{dir}"
set "_MEIPASS2="
set "_PYI_APPLICATION_HOME_DIR="
set "_PYI_PARENT_PROCESS_LEVEL="
set "_PYI_ARCHIVE_FILE="
set "_PYI_SPLASH_IPC="
set PID={pid}
if "%PID%"=="0" goto checkrun
set TRY=0
:wait
tasklist /NH /FI "PID eq %PID%" 2>nul | find "%PID%" >nul
if errorlevel 1 goto trymove
set /a TRY+=1
if %TRY% GEQ 120 goto trymove
ping -n 2 127.0.0.1 >nul
goto wait
:checkrun
tasklist /NH /FI "IMAGENAME eq {exename}" 2>nul | find /i "{exename}" >nul
if not errorlevel 1 goto busy
goto trymove
:busy
echo Please quit BotMonitor first, then run this again.
pause
exit /b 1
:trymove
if not exist "{old}" goto nover
set TRY=0
:retry
if not exist "{exe}" goto placeold
move /y "{exe}" "{exe}.bad" >nul 2>nul
if not exist "{exe}" goto placeold
set /a TRY+=1
if %TRY% GEQ 60 goto fail
ping -n 2 127.0.0.1 >nul
goto retry
:placeold
move /y "{old}" "{exe}" >nul 2>nul
if not exist "{exe}" goto fail
start "" "{exe}"
goto done
:nover
echo No previous version to roll back to.
pause
exit /b 1
:fail
echo Rollback failed.
start "" "{exe}"
:done
del "%~f0"
"""


def _detach(cmdline: str):
    """以“不弹黑框、不占父进程”的方式起 cmd。

    ⚠️ 必须剥掉 PyInstaller 的 `_PYI_*` / `_MEIPASS2` 环境变量：
    onefile 的 bootloader 会把这些变量传给子进程，而 `start` 出来的新实例
    会继承它们 —— 新 bootloader 一看变量已在，就以为自己是“旧实例的子进程”，
    直接用那个正在被删除的 _MEI 临时目录，于是子进程秒退、连日志都来不及写。
    现象就是：新版本的父进程在，子进程立刻消失，面板端口也起不来。
    """
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("_PYI") and k != "_MEIPASS2"}
    flags = 0x08000000                       # CREATE_NO_WINDOW
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    return subprocess.Popen(cmdline, shell=False, creationflags=flags,
                            startupinfo=si, close_fds=True, env=env,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def apply(new_exe: str, to_ver: str) -> dict:
    """生成替换脚本并起它。调用方随后必须尽快退出本进程。"""
    exe = updater.exe_path()
    old = updater.old_path()
    if not getattr(sys, "frozen", False) or not os.path.isfile(exe):
        return {"ok": False, "err": "当前是源码运行，不能自动替换（请用打包后的 exe）"}
    if not os.path.isfile(new_exe):
        return {"ok": False, "err": "找不到已下载的新版本：%s" % new_exe}
    d = os.path.dirname(os.path.abspath(exe))
    if os.path.abspath(d) != os.path.abspath(os.path.dirname(os.path.abspath(new_exe))):
        return {"ok": False, "err": "新版本不在程序目录，拒绝替换"}
    cmd_path = os.path.join(os.environ.get("TEMP") or d, "botmon-swap-%d.cmd" % os.getpid())
    body = SWAP_TMPL.format(dir=d, pid=os.getpid(), exe=exe, old=old, new=new_exe)
    with open(cmd_path, "w", encoding="ascii", newline="\r\n") as f:
        f.write(body)
    mark_pending(new_exe, updater.local_version(), to_ver)
    try:
        _detach('cmd.exe /c "%s"' % cmd_path)
    except Exception as e:  # noqa: BLE001
        clear_pending()
        return {"ok": False, "err": "起替换脚本失败：%s" % e}
    return {"ok": True, "cmd": cmd_path, "exe": exe, "old": old}


def write_rollback_cmd(pid: int = 0) -> str:
    """在程序目录放一个独立回滚脚本。

    pid=0：手动双击用，发现程序在跑就提示先退出；
    pid>0：面板里点回滚用，脚本会自己等这个进程退出再换文件
           （面板点回滚时程序还没退，不能要求“先退出”）。
    """
    exe = updater.exe_path()
    d = os.path.dirname(os.path.abspath(exe))
    path = os.path.join(d, "rollback.cmd")
    body = ROLLBACK_TMPL.format(dir=d, exe=exe, old=updater.old_path(),
                                exename=os.path.basename(exe), pid=int(pid or 0))
    try:
        with open(path, "w", encoding="ascii", newline="\r\n") as f:
            f.write(body)
    except Exception:
        return ""
    return path


def rollback() -> dict:
    """程序还活着时的回滚：把 .old 换回来（本进程随后要退出）。"""
    exe = updater.exe_path()
    old = updater.old_path()
    if not os.path.isfile(old):
        return {"ok": False, "err": "没有上一版可回滚（找不到 %s）" % os.path.basename(old)}
    cmd = write_rollback_cmd(pid=os.getpid())
    if not cmd:
        return {"ok": False, "err": "写回滚脚本失败（目录不可写？）"}
    try:
        _detach('cmd.exe /c "%s"' % cmd)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "err": "起回滚脚本失败：%s" % e}
    return {"ok": True, "cmd": cmd}


# ---------------------------------------------------------------- 启动自愈

def recover() -> dict:
    """启动时调用：上次替换中途断了就把旧版还原回来。"""
    exe = updater.exe_path()
    old = updater.old_path()
    out = {"restored": False, "err": ""}
    if not os.path.isfile(exe) and os.path.isfile(old):
        try:
            os.replace(old, exe)
            out["restored"] = True
        except Exception as e:  # noqa: BLE001
            out["err"] = str(e)
    p = pending()
    if p:
        mine = str(p.get("to") or "") == updater.local_version()
        age = time.time() - float(p.get("at") or 0)
        if mine:
            # 已经跑到新版本上了，说明替换成功
            clear_pending()
            out["updated_to"] = p.get("to")
        elif age > 300:
            # 过了 5 分钟还没到目标版本，判定上次替换失败，留着让面板提示
            out["failed_from"] = p.get("from")
            out["failed_to"] = p.get("to")
    return out


def status() -> dict:
    """给面板用：能不能更新、有没有待应用的新版、能不能回滚。"""
    exe = updater.exe_path()
    ver = updater.local_version()
    cand = ""
    try:
        for name in os.listdir(updater.app_dir()):
            if name.lower().endswith(".new.exe"):
                cand = os.path.join(updater.app_dir(), name)
                break
    except Exception:
        pass
    return {
        "local": ver,
        "exe": exe,
        "exe_exists": os.path.isfile(exe),
        "staged": cand,
        "staged_ver": os.path.basename(cand).split("-")[-1].replace(".new.exe", "") if cand else "",
        "can_rollback": updater.has_old(),
        "pending": pending() or {},
        "running_from_source": not getattr(sys, "frozen", False),
    }


if __name__ == "__main__":
    print(json.dumps(status(), ensure_ascii=False, indent=1))
