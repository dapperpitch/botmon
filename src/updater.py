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


def download(on_progress=None, timeout=30, force=False) -> dict:
    """下载最新 exe 到程序目录。返回 {ok, path, size, version, err, sha}。

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
        cleanup_stage()
        req = urllib.request.Request(
            DOWNLOAD, headers={"User-Agent": _ua(), "Accept": "application/octet-stream"})
        got = 0
        with _urlopen(req, timeout) as resp:
            total = int(resp.headers.get("Content-Length") or expected or 0)
            with open(part, "wb") as f:
                while True:
                    buf = resp.read(1 << 18)
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
        size = os.path.getsize(part)
        if total and size != total:
            os.remove(part)
            return {"ok": False, "err": "下载不完整（%d/%d 字节）" % (size, total)}
        if expected and size != expected:
            os.remove(part)
            return {"ok": False, "err": "大小与 Release 不符（%d≠%d）" % (size, expected)}
        os.replace(part, dest)
        digest = (r.get("asset") or {}).get("digest") or ""
        sha = sha256(dest)
        if digest.startswith("sha256:") and digest.split(":", 1)[1].lower() != sha:
            os.remove(dest)
            return {"ok": False, "err": "哈希校验失败"}
        return {"ok": True, "path": dest, "size": size, "version": ver,
                "sha": sha, "err": ""}
    except Exception as e:  # noqa: BLE001
        try:
            if os.path.exists(part):
                os.remove(part)
        except Exception:
            pass
        return {"ok": False, "err": str(e)[:200]}


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
