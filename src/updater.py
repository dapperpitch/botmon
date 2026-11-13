"""版本、下载与替换。

v0.6 起不再只是「检查」，而是完整的三段式更新：

1. 探测：读 `github.com/<repo>/releases/latest` 的 302 跳转拿版本号
   （**故意不依赖 api.github.com** —— 这台机器上它比 github.com 更容易连不上），
   拿不到时再退回 API 取 asset 的 size/digest。
2. 下载：把 `.../latest/download/BotMonitor.exe` 下到程序同目录的
   `BotMonitor-<版本>.new.exe`，边下边报进度，下完核对字节数。
3. 替换：**不在运行中覆盖自己**（Windows 会锁住 exe），由 apply_update.py
   生成一个 cmd，等本进程退出后再改名+替换+重启；旧版留成 `.old` 以便回滚。
"""
import hashlib
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request

OWNER = "dapperpitch"
REPO = "botmon"
API = "https://api.github.com/repos/%s/%s/releases/latest" % (OWNER, REPO)
LATEST_PAGE = "https://github.com/%s/%s/releases/latest" % (OWNER, REPO)
DOWNLOAD = "https://github.com/%s/%s/releases/latest/download/BotMonitor.exe" % (OWNER, REPO)
EXE_NAME = "BotMonitor.exe"

def _ver_path() -> str:
    """打包后 VERSION 在 _MEIPASS 里，开发态在 src/../VERSION。"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        p = os.path.join(base, "VERSION")
        if os.path.exists(p):
            return p
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "VERSION")


VERSION_FILE = _ver_path()


def local_version() -> str:
    try:
        with open(VERSION_FILE, encoding="utf-8-sig") as f:
            return f.read().strip()
    except Exception:
        return "0.0.0"


def _tuple(v: str):
    out = []
    for part in str(v).lstrip("vV").split("."):
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    while len(out) < 3:
        out.append(0)
    return tuple(out[:3])


def is_newer(remote: str, local: str) -> bool:
    return _tuple(remote) > _tuple(local)


# ---------------------------------------------------------------- HTTP 基础

def _ua():
    return "BotMonitor/%s" % local_version()


def _urlopen(req, timeout, ctx=None):
    """证书校验失败时降级重试一次（这台机器的 TLS 偶发被中间拦）。"""
    try:
        return urllib.request.urlopen(req, timeout=timeout) if ctx is None else \
            urllib.request.urlopen(req, timeout=timeout, context=ctx)
    except urllib.error.URLError as e:
        if ctx is not None or "CERTIFICATE_VERIFY_FAILED" not in str(e):
            raise
        ctx2 = ssl._create_unverified_context()
        return urllib.request.urlopen(req, timeout=timeout, context=ctx2)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect", headers, fp)


def _redirect_target(url, timeout):
    """只取 302 的 Location，不下载整个页面。"""
    op = urllib.request.build_opener(_NoRedirect)
    try:
        op.open(urllib.request.Request(url, headers={"User-Agent": _ua()}), timeout=timeout)
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location") if e.headers else ""
        return e.code, loc or ""
    return 200, ""


def latest_tag(timeout=10) -> str:
    """从 releases/latest 的跳转里取 tag。不依赖 api.github.com。"""
    code, loc = _redirect_target(LATEST_PAGE, timeout)
    m = re.search(r"/releases/tag/([^/?#]+)", loc or "")
    if not m:
        # 有些网络环境下 302 会被掐断，降级再试一次
        code, loc = _redirect_target(LATEST_PAGE, timeout)
        m = re.search(r"/releases/tag/([^/?#]+)", loc or "")
    if not m:
        raise RuntimeError("拿不到 latest 版本（HTTP %s）" % code)
    return m.group(1)


def release(timeout=10) -> dict:
    """{version, tag, notes, url, asset:{size,digest,url}, via, err}

    先用 github.com 的跳转拿版本（比 API 稳），再用 API 补 notes 和附件大小；
    API 连不上也不影响「能不能更新」，只是少了校验用的字节数。
    """
    out = {"version": "", "tag": "", "notes": "", "url": "", "asset": {},
           "via": "", "err": ""}
    tag = ""
    try:
        tag = latest_tag(timeout)
        out["via"] = "redirect"
    except Exception as e:  # noqa: BLE001
        out["err"] = str(e)[:160]
    try:
        d = _fetch(timeout)
        if not tag:
            tag = str(d.get("tag_name") or "")
            out["via"] = "api"
        out["notes"] = (d.get("body") or "")[:600]
        out["url"] = d.get("html_url") or ""
        for a in d.get("assets") or []:
            if a.get("name") == EXE_NAME:
                out["asset"] = {"size": int(a.get("size") or 0),
                                "digest": str(a.get("digest") or ""),
                                "url": a.get("browser_download_url") or DOWNLOAD}
                break
    except Exception as e:  # noqa: BLE001
        if not tag:
            out["err"] = (out["err"] or str(e))[:160]
    if tag:
        out["tag"] = tag
        out["version"] = tag.lstrip("vV")
        out["err"] = ""          # 版本号拿到了就不算失败，附件信息缺了也能下
        if not out["url"]:
            out["url"] = "https://github.com/%s/%s/releases/tag/%s" % (OWNER, REPO, tag)
    return out


# ---------------------------------------------------------------- 下载

def app_dir() -> str:
    """程序所在目录：打包后是 exe 同级，开发态是 src/。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def exe_path() -> str:
    if getattr(sys, "frozen", False):
        return os.path.abspath(sys.executable)
    return os.path.join(app_dir(), EXE_NAME)


def stage_path(version: str) -> str:
    return os.path.join(app_dir(), "%s-%s.new.exe" % (os.path.splitext(EXE_NAME)[0], version))


def old_path() -> str:
    return exe_path() + ".old"


def has_old() -> bool:
    return os.path.isfile(old_path())


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def motw_present(path: str) -> bool:
    """有没有 Zone.Identifier 流。

    注意不能用 os.path.exists：它对「文件:流」这种路径永远返回 False。
    只能真去打开看看。
    """
    try:
        with open(path + ":Zone.Identifier", "rb"):
            return True
    except OSError:
        return False


def strip_motw(path: str) -> bool:
    """去掉 Zone.Identifier（Mark-of-the-Web）。

    程序自己用 urllib 下载的文件本来就不带这个标记（只有浏览器/资源管理器会加），
    所以这一步是防御性的：万一 staged 文件是别处拷来的，带 MOTW 启动会弹
    SmartScreen「Windows 已保护你的电脑」，用户会以为是病毒。

    删除用 DeleteFileW —— 它能直接删掉 ADS；删不掉就截断成空流（空流等于没有区域信息）。
    """
    if not motw_present(path):
        return False
    ads = path + ":Zone.Identifier"
    if sys.platform == "win32":
        try:
            import ctypes
            if ctypes.windll.kernel32.DeleteFileW(ctypes.c_wchar_p(ads)):
                return not motw_present(path)
        except Exception:
            pass
    try:
        with open(ads, "r+b") as f:
            f.truncate(0)
        return True
    except Exception:
        return False


def cleanup_stage(keep: str = ""):
    """删掉上次残留的 .new.exe / .part，只留下 keep。"""
    try:
        for name in os.listdir(app_dir()):
            low = name.lower()
            if not low.endswith((".new.exe", ".new.exe.part")):
                continue
            p = os.path.join(app_dir(), name)
            if keep and os.path.abspath(p) == os.path.abspath(keep):
                continue
            try:
                os.remove(p)
            except Exception:
                pass
    except Exception:
        pass


RETRIES = 5          # 断了就续，最多 5 轮
CHUNK = 1 << 18


def _fetch_into(part: str, timeout: int, expected: int, on_progress=None):
    """把 DOWNLOAD 的内容续写进 part（支持 Range 断点续传）。返回 (got, total)。"""
    start = os.path.getsize(part) if os.path.exists(part) else 0
    headers = {"User-Agent": _ua(), "Accept": "application/octet-stream"}
    if start:
        headers["Range"] = "bytes=%d-" % start
    req = urllib.request.Request(DOWNLOAD, headers=headers)
    with _urlopen(req, timeout) as resp:
        code = getattr(resp, "status", None) or 200
        clen = int(resp.headers.get("Content-Length") or 0)
        if start and code == 206:
            total = (start + clen) if clen else (expected or 0)
            mode, got = "ab", start
        else:
            # 服务器不认 Range：只能从头写
            total, mode, got = (clen or expected or 0), "wb", 0
        with open(part, mode) as f:
            while True:
                buf = resp.read(CHUNK)
                if not buf:
                    break
                f.write(buf)
                got += len(buf)
                if on_progress:
                    try:
                        on_progress(got, total)
                    except Exception:
                        pass
            f.flush()
            os.fsync(f.fileno())
    return got, total


def download(on_progress=None, timeout=60, force=False) -> dict:
    """下载最新 exe 到程序目录（断点续传 + 自动重试）。

    这台机器走日本节点，37 MB 中途断一次很常见：第一版没有续传，
    一次超时整个下载就白费。现在失败会带着 Range 从 `.part` 已有字节继续，
    最多 RETRIES 轮；最终仍要以字节数和 SHA256 为准。

    只下载，不替换：真正换文件在 apply() 里，由外部 cmd 等本进程退出后做。
    """
    r = release(timeout)
    ver = r.get("version") or ""
    if not ver:
        return {"ok": False, "err": r.get("err") or "拿不到版本号"}
    if not force and not is_newer(ver, local_version()):
        return {"ok": False, "err": "已经是最新的 v%s" % local_version()}

    dest = stage_path(ver)
    part = dest + ".part"
    expected = int((r.get("asset") or {}).get("size") or 0)
    try:
        cleanup_stage(keep=part)       # 上次没下完的 .part 留着续传
    except Exception:
        pass

    last_err = ""
    for attempt in range(1, RETRIES + 1):
        try:
            got, total = _fetch_into(part, timeout, expected, on_progress)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404):
                return {"ok": False, "err": "下载地址返回 HTTP %s（版本或附件不存在）" % e.code}
            last_err = "HTTP %s" % e.code
        except Exception as e:  # noqa: BLE001
            last_err = str(e)[:120]
        else:
            size = os.path.getsize(part) if os.path.exists(part) else 0
            want = expected or total
            if want and size >= want:
                if size > want:
                    # 比预期还大，说明拼错了，删掉重下
                    try:
                        os.remove(part)
                    except Exception:
                        pass
                    last_err = "字节数超出预期（%d>%d）" % (size, want)
                else:
                    break
            else:
                last_err = "不完整（%d/%d）" % (size, want or 0)
        if attempt < RETRIES:
            time.sleep(min(2 * attempt, 8))

    if not os.path.exists(part):
        return {"ok": False, "err": last_err or "下载失败"}
    size = os.path.getsize(part)
    want = expected or 0
    if want and size != want:
        return {"ok": False, "err": "重试 %d 次仍不完整（%d/%d）：%s" % (RETRIES, size, want, last_err)}
    try:
        os.replace(part, dest)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "err": "落盘失败：%s" % e}
    strip_motw(dest)
    digest = (r.get("asset") or {}).get("digest") or ""
    sha = sha256(dest)
    if digest.startswith("sha256:") and digest.split(":", 1)[1].lower() != sha:
        try:
            os.remove(dest)
        except Exception:
            pass
        return {"ok": False, "err": "哈希校验失败（文件可能损坏，已删除）"}
    return {"ok": True, "path": dest, "size": size, "version": ver, "sha": sha, "err": ""}


def check(timeout=10) -> dict:
    """返回 {local, remote, newer, notes, url, err, asset, via}"""
    local = local_version()
    out = {"local": local, "remote": "", "newer": False, "notes": "", "url": "",
           "err": "", "asset": {}, "via": ""}
    r = release(timeout)
    if r.get("version"):
        out["remote"] = r["version"]
        out["notes"] = r.get("notes") or ""
        out["url"] = r.get("url") or ""
        out["asset"] = r.get("asset") or {}
        out["via"] = r.get("via") or ""
        out["newer"] = is_newer(r["version"], local)
    else:
        msg = r.get("err") or "检查失败"
        if "404" in msg:
            msg = "仓库还没有 Release（先发一个 v%s）" % local
        out["err"] = msg[:200]
    return out


def _fetch(timeout):
    """取 latest release（API）。证书校验失败时降级重试一次。

    这台机器上的 TLS 偶发被中间拦（杀软/代理），一次抖动就会让面板显示
    “检查失败”。读的是公开只读接口，降级不涉及任何凭据，可以接受。
    """
    req = urllib.request.Request(API, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": _ua(),
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.URLError as e:
        if "CERTIFICATE_VERIFY_FAILED" not in str(e):
            raise
        ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return json.load(r)


if __name__ == "__main__":
    print(json.dumps(check(), ensure_ascii=False, indent=1))
