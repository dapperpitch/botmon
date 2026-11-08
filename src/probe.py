#!/usr/bin/env python3
"""在 ECS 上跑，输出一份状态 JSON 给本地面板用。只读，不改任何东西。"""
import json
import subprocess
import time

NAPCAT_CFG = "/opt/napcat/onebot11_1182409706.json"


def sh(cmd, timeout=8):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "").strip()
    except Exception:
        return ""


out = {"ts": time.time()}

ps = sh("docker ps --format '{{.Names}}|{{.Status}}'")
out["containers"] = {}
for line in ps.splitlines():
    if "|" in line:
        n, s = line.split("|", 1)
        out["containers"][n] = s

tok = ""
try:
    cfg = json.load(open(NAPCAT_CFG, encoding="utf-8"))
    tok = cfg["network"]["httpServers"][0]["token"]
except Exception:
    pass

mi = {"api_ok": False, "online": False, "nickname": "", "qr_age": None}
if tok:
    raw = sh('docker exec napcat curl -s -m 5 "http://127.0.0.1:3000/get_status?access_token=%s"' % tok, 12)
    try:
        d = json.loads(raw)
        mi["api_ok"] = True
        mi["online"] = bool((d.get("data") or {}).get("online"))
    except Exception:
        pass
    raw2 = sh('docker exec napcat curl -s -m 5 "http://127.0.0.1:3000/get_login_info?access_token=%s"' % tok, 12)
    try:
        mi["nickname"] = (json.loads(raw2).get("data") or {}).get("nickname", "")
    except Exception:
        pass
    st = sh("docker exec napcat stat -c %Y /app/napcat/cache/qrcode.png")
    if st.isdigit():
        mi["qr_age"] = int(time.time()) - int(st)
out["mi"] = mi

mem = sh("free -m | sed -n 2p").split()
if len(mem) >= 7:
    out["mem"] = {"total": int(mem[1]), "used": int(mem[2]), "avail": int(mem[6])}
df = sh("df -Pm / | tail -1").split()
if len(df) >= 5:
    out["disk"] = {"size": int(df[1]), "used": int(df[2]), "pct": df[4]}
up = sh("uptime").split("load average:")
out["load"] = up[1].strip() if len(up) > 1 else ""
out["uptime"] = sh("uptime -p")

n = sh("docker logs --since 10m astrbot 2>&1 | grep -ac 'napcat(aiocqhttp)' || true")
x = sh("docker logs --since 10m astrbot 2>&1 | grep -ac 'qq_official' || true")
out["msgs"] = {"mi": int(n) if n.isdigit() else 0,
               "x07": int(x) if x.isdigit() else 0}

out["watch"] = sh("tail -6 /var/log/napcat-watch.log").splitlines()
out["audit"] = {}
try:
    p = json.load(open("/opt/astrbot/data/mi_audit/patches.json", encoding="utf-8"))
    out["audit"]["mi"] = {"counts": {k: v.get("n") for k, v in (p.get("counts") or {}).items()},
                          "pending": len(p.get("pending") or [])}
except Exception:
    pass
try:
    p = json.load(open("/opt/astrbot/data/xia07_audit/patches.json", encoding="utf-8"))
    out["audit"]["x07"] = {"counts": {k: v.get("n") for k, v in (p.get("counts") or {}).items()},
                           "pending": len(p.get("pending") or [])}
except Exception:
    pass

print(json.dumps(out, ensure_ascii=False))
