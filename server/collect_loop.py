#!/usr/bin/env python3
"""服务器端采集循环：每 20 秒跑一次 collect_once.py，写 /tmp/botmon.json。

为什么要它：这台机器上 docker 命令极慢（docker ps 12s、docker exec 3~5s），
桌面端每 15 秒跑一次会一直超时；把慢活搬到后台，桌面端只读一个小文件。
"""
import json
import os
import subprocess
import time

CACHE = "/tmp/botmon.json"

while True:
    try:
        r = subprocess.run(["python3", "/opt/botmon/collect_once.py"],
                           capture_output=True, timeout=120)
        if r.returncode == 0:
            txt = r.stdout.decode("utf-8", "replace").strip()
            if txt.startswith("{"):
                json.loads(txt)                     # 校验能解析
                tmp = CACHE + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(txt)
                os.replace(tmp, CACHE)
    except Exception:
        pass
    time.sleep(20)