"""版本与更新：跟 GitHub Releases 比版本号。

只做「检查 + 告诉你」，不自动下载替换（自动替换留到 v0.5，
那一步要处理杀毒误报、更新中途断电、回滚，不是几行代码能写稳的）。
"""
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

OWNER = "dapperpitch"
REPO = "botmon"
API = "https://api.github.com/repos/%s/%s/releases/latest" % (OWNER, REPO)

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


def _fetch(timeout):
    """取 latest release。证书校验失败时降级重试一次。

    这台机器上的 TLS 偶发被中间拦（杀软/代理），一次抖动就会让面板显示
    “检查失败”。读的是公开只读接口，降级不涉及任何凭据，可以接受。
    """
    req = urllib.request.Request(API, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "BotMonitor/%s" % local_version(),
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


def check(timeout=10) -> dict:
    """返回 {local, remote, newer, notes, url, err}"""
    local = local_version()
    out = {"local": local, "remote": "", "newer": False, "notes": "", "url": "", "err": ""}
    try:
        d = _fetch(timeout)
        tag = str(d.get("tag_name") or "")
        out["remote"] = tag.lstrip("vV")
        out["notes"] = (d.get("body") or "")[:600]
        out["url"] = d.get("html_url") or ""
        out["newer"] = is_newer(tag, local)
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "404" in msg:
            msg = "仓库还没有 Release（先发一个 v%s）" % local
        out["err"] = msg[:200]
    return out


if __name__ == "__main__":
    print(json.dumps(check(), ensure_ascii=False, indent=1))
