"""把运维知识库解析成星图数据：案例 → 章节 → 条目。

不引第三方库，纯文本解析。条目太多会糊成一团，所以每节最多取 MAX_PER_SECTION 条。
"""
import os
import re

MAX_PER_SECTION = 5

KIND_COLOR = {
    "现象": "#f0b429",
    "原因": "#ff5c5c",
    "处理": "#39d98a",
    "验证": "#4d8dff",
    "注意": "#a78bfa",
}


def parse(path):
    if not os.path.exists(path):
        return {"error": "知识库文件不存在", "path": path, "nodes": [], "edges": []}

    nodes, edges = [], []
    nid = 0

    def add(label, kind, size, payload=None):
        nonlocal nid
        nid += 1
        n = {"id": nid, "label": label, "kind": kind, "size": size}
        if payload:
            n.update(payload)
        nodes.append(n)
        return nid

    case_id = None
    sec_id = None
    sec_kind = ""
    bullets = 0

    with open(path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.rstrip("\n")

            if line.startswith("## "):
                title = line[3:].strip()
                m = re.match(r"(\d{4}-\d{2}-\d{2})\s+(.*)", title)
                date = m.group(1) if m else ""
                name = m.group(2) if m else title
                case_id = add(name, "case", 9, {"date": date, "body": ""})
                sec_id = None
                continue

            if line.startswith("### "):
                if case_id is None:
                    continue
                sec_kind = line[4:].strip()
                sec_id = add(sec_kind, "section", 5,
                             {"color": KIND_COLOR.get(sec_kind, "#8b95a8"), "parent": case_id})
                edges.append({"a": case_id, "b": sec_id})
                bullets = 0
                continue

            s = line.strip()
            if not s or s.startswith("#") or s.startswith("|") or s.startswith("```"):
                continue
            if s.startswith("- ") or s.startswith("* ") or s[0].isdigit():
                if sec_id is None or bullets >= MAX_PER_SECTION:
                    continue
                text = re.sub(r"^[-*\d.、)\s]+", "", s)
                text = re.sub(r"[`*_#>]", "", text)[:70]
                if len(text) < 4:
                    continue
                pid = add(text, "point", 2.5,
                          {"color": KIND_COLOR.get(sec_kind, "#8b95a8"), "parent": sec_id})
                edges.append({"a": sec_id, "b": pid})
                bullets += 1

    cases = sum(1 for n in nodes if n["kind"] == "case")
    return {"nodes": nodes, "edges": edges,
            "stats": {"cases": cases, "nodes": len(nodes), "edges": len(edges)},
            "source": os.path.basename(path)}
