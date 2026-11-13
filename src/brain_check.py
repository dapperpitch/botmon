#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给桌面端的「体检」面板用：一次性输出两个 bot 的记忆/卡片诊断。只读。"""
import json
import os
import sqlite3
import time

BASE = "/opt/astrbot/data/plugin_data/"
BOTS = {
    "xia07": ("astrbot_plugin_memory_brain07", "夏07"),
    "mike": ("astrbot_plugin_memory_brain", "贾维斯"),
}
NOW = time.time()
DAY = 86400


def one(db, sql, args=()):
    try:
        cur = sqlite3.connect(db).execute(sql, args)
        return cur.fetchone()[0]
    except Exception:
        return 0


def export(key):
    plugin, label = BOTS[key]
    db = os.path.join(BASE, plugin, "brain.db")
    out = {"label": label, "ok": os.path.exists(db), "facts": [], "issues": [],
           "by_kind": {}, "recent7": [0] * 7, "facts_count": 0,
           "mem_total": 0, "ent_total": 0, "max_stale_days": 0}
    if not out["ok"]:
        out["issues"].append({"sev": "bad", "title": "数据库不存在",
                              "why": db, "tag": "缺失"})
        return out

    out["mem_total"] = one(db, "select count(*) from memories where status='active'")
    out["ent_total"] = one(db, "select count(*) from entities")
    try:
        c = sqlite3.connect(db)
        for kind, n in c.execute("select kind, count(*) from memories where status='active' group by kind"):
            out["by_kind"][str(kind)] = n
    except Exception:
        pass

    # 近 7 天新增
    try:
        c = sqlite3.connect(db)
        for i in range(7):
            day = time.mktime(time.localtime(NOW - i * DAY)[:3] + (0, 0, 0, 0, 0, -1))
            n = c.execute("select count(*) from memories where created_at >= ? and created_at < ?",
                          (day, day + DAY)).fetchone()[0]
            out["recent7"][6 - i] = n
    except Exception:
        pass

    # 事实卡片
    try:
        c = sqlite3.connect(db)
        rows = c.execute(
            "select subject,key,value,confidence,hits,last_seen,first_seen from facts "
            "where status='active' order by subject, key").fetchall()
    except Exception:
        rows = []

    out["facts_count"] = len(rows)
    stale_days = 0
    for subject, key, value, conf, hits, last_seen, first_seen in rows:
        age = int((NOW - float(last_seen or first_seen or NOW)) / DAY)
        stale_days = max(stale_days, age)
        out["facts"].append({"subject": subject, "key": key, "value": value,
                             "conf": round(float(conf or 0), 2), "hits": int(hits or 0),
                             "age": age})
    out["max_stale_days"] = stale_days

    # 诊断规则
    if out["mem_total"] > 20 and out["facts_count"] == 0:
        out["issues"].append({"sev": "bad", "title": "还没有事实卡片", "tag": "卡片缺失",
                              "why": "%d 条记忆、%d 个实体，但 facts 表是空的 —— "
                                     "她记得事，却抽不出「谁 · 什么 · 值」"
                                     % (out["mem_total"], out["ent_total"])})
    low = [f for f in out["facts"] if f["conf"] < 0.6]
    if low:
        out["issues"].append({"sev": "warn", "title": "%d 张卡置信度偏低" % len(low),
                              "tag": "低置信",
                              "why": "、".join("%s|%s %.2f" % (f["subject"], f["key"], f["conf"])
                                              for f in low[:3])})
    stale = [f for f in out["facts"] if f["age"] > 30]
    if stale:
        out["issues"].append({"sev": "warn", "title": "%d 张卡超过 30 天没再出现" % len(stale),
                              "tag": "陈旧",
                              "why": "、".join("%s|%s（%d 天）" % (f["subject"], f["key"], f["age"])
                                              for f in stale[:3])})
    idle = [f for f in out["facts"] if f["hits"] == 0 and f["age"] > 7]
    if idle:
        out["issues"].append({"sev": "warn", "title": "%d 张卡抽出来就没用过" % len(idle),
                              "tag": "久未命中",
                              "why": "创建超过 7 天、hits 仍为 0，可能是抽取误判"})
    if sum(out["recent7"]) == 0 and out["mem_total"] > 0:
        out["issues"].append({"sev": "warn", "title": "7 天没有新增记忆", "tag": "断层",
                              "why": "最近一周没有新的记忆写入"})
    if not out["issues"]:
        out["issues"].append({"sev": "ok", "title": "没发现问题", "tag": "正常",
                              "why": "卡片数量、置信度、新鲜度都在正常范围"})
    return out


print(json.dumps({"ts": NOW, "bots": {"xia07": export("xia07"), "mike": export("mike")}},
                 ensure_ascii=False))
