#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""桌面端探针（只读，要求快）。

第一版 57 秒才回：
  · docker exec napcat curl 两次（get_status / get_login_info）各要几秒
  · docker logs --since 10m | grep -c 两条，扫日志极慢
现在：合并 docker 调用、去掉日志扫描，走 /proc 读系统指标。
"""
import base64
import glob
import json
import os
import subprocess
import time


def sh(cmd, timeout=8):
    try:
        r = subprocess.run("PATH=/usr/local/bin:/usr/bin:/bin " + cmd, shell=True,
                           capture_output=True, timeout=timeout)
        return (r.stdout or b"").decode("utf-8", "replace").strip()
    except Exception:
        return ""


out = {"ts": time.time()}

# 1) 容器（一次 docker ps）
for line in sh("docker ps --format '{{.Names}}|{{.Status}}'").splitlines():
    if "|" in line:
        n, s = line.split("|", 1)
        out.setdefault("containers", {})[n] = s

# 2) 贾维斯：一次 docker exec 里把 status/login 都取回来
mi = {"api_ok": False, "online": False, "nickname": "", "qr_age": None, "qr_b64": ""}
tok = ""
try:
    cfg = json.load(open(sorted(glob.glob("/opt/napcat/onebot11_*.json"))[0], encoding="utf-8"))
    tok = cfg["network"]["httpServers"][0]["token"]
except Exception:
    pass

if tok:
    both = sh('docker exec napcat sh -c "curl -s -m 3 \'http://127.0.0.1:3000/get_status?access_token=%s\';'
              'echo; curl -s -m 3 \'http://127.0.0.1:3000/get_login_info?access_token=%s\'"' % (tok, tok), 12)
    parts = [p for p in both.splitlines() if p.strip().startswith("{")]
    for p in parts:
        try:
            d = json.loads(p)
        except Exception:
            continue
        data = d.get("data") or {}
        if "online" in data:
            mi["api_ok"] = True
            mi["online"] = bool(data.get("online"))
        if data.get("nickname"):
            mi["nickname"] = data["nickname"]

st = sh("docker exec napcat stat -c %Y /app/napcat/cache/qrcode.png", 6)
if st.isdigit():
    mi["qr_age"] = int(time.time()) - int(st)
    mi["qr_b64"] = sh("docker exec napcat base64 -w0 /app/napcat/cache/qrcode.png", 6)

out["mi"] = mi

# 3) 系统指标：直接读 /proc，不用起进程
try:
    mem = {}
    for line in open("/proc/meminfo"):
        k, v = line.split(":")[0], line.split()[1]
        mem[k] = int(v) // 1024
    out["mem"] = {"total": mem.get("MemTotal", 0),
                  "used": mem.get("MemTotal", 0) - mem.get("MemAvailable", 0),
                  "avail": mem.get("MemAvailable", 0)}
except Exception:
    pass

try:
    s = os.statvfs("/")
    size = s.f_blocks * s.f_frsize // 1048576
    used = (s.f_blocks - s.f_bfree) * s.f_frsize // 1048576
    out["disk"] = {"size": size, "used": used, "pct": "%d%%" % (used * 100 // max(1, size))}
except Exception:
    pass

try:
    la = open("/proc/loadavg").read().split()[:3]
    out["load"] = ", ".join(la)
    up = float(open("/proc/uptime").read().split()[0])
    d, h, m = int(up // 86400), int(up % 86400 // 3600), int(up % 3600 // 60)
    out["uptime"] = "up %d days, %d:%02d" % (d, h, m)
except Exception:
    pass

# 4) 看门狗 + 审计（都是本地小文件）
out["watch"] = sh("tail -6 /var/log/napcat-watch.log").splitlines()
out["audit"] = {}
for key, path in (("mi", "/opt/astrbot/data/mi_audit/patches.json"),
                  ("x07", "/opt/astrbot/data/xia07_audit/patches.json")):
    try:
        p = json.load(open(path, encoding="utf-8"))
        out["audit"][key] = {"counts": {k: v.get("n") for k, v in (p.get("counts") or {}).items()},
                             "pending": len(p.get("pending") or [])}
    except Exception:
        pass

# 5) 消息量：日志扫描太慢，改成读 astrbot 的会话库不可行，这里直接省略（前端显示 —）
out["msgs"] = {"mi": None, "x07": None}

print(json.dumps(out, ensure_ascii=False), flush=True)
