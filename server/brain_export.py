#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 AstrBot 的 brain.db 导出成 3D 脑图要的三个数组。

用法：python3 brain_export.py mike|xia07
输出：{"ok":true,"label":"贾维斯","nodes":[...],"edges":[...],"memories":[...]}
"""
import json
import sqlite3
import sys

BASE = "/opt/astrbot/data/plugin_data/"
PLUGIN = {
    "mike": ("astrbot_plugin_memory_brain", "贾维斯"),
    "xia07": ("astrbot_plugin_memory_brain07", "夏07"),
}


def main() -> int:
    which = (sys.argv[1] if len(sys.argv) > 1 else "mike").strip()
    plugin, label = PLUGIN.get(which, PLUGIN["mike"])
    db = f"{BASE}{plugin}/brain.db"
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"open db: {exc}"}, ensure_ascii=False))
        return 0

    try:
        node_rows = conn.execute(
            "SELECT e.id, e.name, e.type, "
            "(SELECT COUNT(*) FROM memory_entities me WHERE me.entity_id = e.id) AS degree "
            "FROM entities e"
        ).fetchall()
        nodes = [
            {"id": r["id"], "name": r["name"], "type": r["type"] or "thing", "degree": r["degree"] or 0}
            for r in node_rows
        ]

        ids = {n["id"] for n in nodes}
        edges = [
            {"src": r["src_id"], "dst": r["dst_id"], "kind": "relation",
             "label": (r["relation"] or "")[:4], "weight": float(r["weight"] or 0.8)}
            for r in conn.execute("SELECT src_id, dst_id, relation, weight FROM relations")
            if r["src_id"] in ids and r["dst_id"] in ids
        ]

        memories = []
        for r in conn.execute(
            "SELECT id, text, kind, importance, created_at FROM memories "
            "WHERE status = 'active' ORDER BY id"
        ):
            ents = [
                row[0]
                for row in conn.execute(
                    "SELECT entity_id FROM memory_entities WHERE memory_id = ?", (r["id"],)
                )
                if row[0] in ids
            ]
            memories.append({
                "id": r["id"],
                "text": r["text"],
                "kind": r["kind"],
                "importance": float(r["importance"] or 0.5),
                "created_at": float(r["created_at"] or 0),
                "entities": ents,
            })
        conn.close()
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"query: {exc}"}, ensure_ascii=False))
        return 0

    print(json.dumps({
        "ok": True, "which": which, "label": label,
        "nodes": nodes, "edges": edges, "memories": memories,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
