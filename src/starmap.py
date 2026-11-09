"""把运维知识库解析成星图数据：案例（星系）→ 章节 → 句子（星星）。

纯文本解析，不引第三方库。一段话拆成句子当星星，密度才够“星图”。
"""
import os
import re

MAX_PER_SECTION = 40

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
    count = 0
    in_code = False

    with open(path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.rstrip("\n")
            s = line.strip()

            if s.startswith("```"):
                in_code = not in_code
                continue
            if in_code:
                continue

            if line.startswith("## "):
                title = line[3:].strip()
                m = re.match(r"(\d{4}-\d{2}-\d{2})\s+(.*)", title)
                date = m.group(1) if m else ""
                name = m.group(2) if m else title
                case_id = add(name.strip(), "case", 7, {"date": date})
                sec_id = None
                count = 0
                continue

            if line.startswith("### "):
                if case_id is None:
                    continue
                sec_kind = line[4:].strip()
                sec_id = add(sec_kind, "section", 3.2,
                             {"color": KIND_COLOR.get(sec_kind, "#8b95a8"),
                              "parent": case_id})
                edges.append({"a": case_id, "b": sec_id})
                count = 0
                continue

            if not s or s.startswith("#") or s.startswith("|"):
                continue
            if sec_id is None or count >= MAX_PER_SECTION:
                continue

            text = re.sub(r"^[-*>\d.、)\s]+", "", s)
            text = re.sub(r"[`*_#>\[\]()]", "", text)
            for part in re.split(r"[。；;!?！？]", text):
                part = part.strip()
                if count >= MAX_PER_SECTION:
                    break
                if len(part) < 5:
                    continue
                pid = add(part[:44], "point", 1.6,
                          {"color": KIND_COLOR.get(sec_kind, "#8b95a8"),
                           "parent": sec_id})
                edges.append({"a": sec_id, "b": pid})
                count += 1

    cases = sum(1 for n in nodes if n["kind"] == "case")
    return {"nodes": nodes, "edges": edges,
            "stats": {"cases": cases, "nodes": len(nodes), "edges": len(edges)},
            "source": os.path.basename(path)}
