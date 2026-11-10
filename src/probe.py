#!/usr/bin/env python3
"""桌面端读的是服务器上的缓存文件（由 collect_loop.py 每 20 秒刷新）。"""
import json
import os
import subprocess
import time

CACHE = "/tmp/botmon.json"

def read():
    try:
        with open(CACHE, encoding="utf-8") as f:
            d = json.load(f)
        d["cache_age"] = int(time.time() - os.path.getmtime(CACHE))
        return d
    except Exception:
        return None

d = read()
if d is None or d.get("cache_age", 999) > 90:
    # 缓存太旧就自己跑一次（慢，但保证有数据）
    try:
        r = subprocess.run(["python3", "/opt/botmon/collect_once.py"],
                           capture_output=True, timeout=120)
        txt = r.stdout.decode("utf-8", "replace").strip()
        if txt.startswith("{"):
            d = json.loads(txt)
    except Exception:
        pass
print(json.dumps(d or {"err": "no data"}, ensure_ascii=False), flush=True)