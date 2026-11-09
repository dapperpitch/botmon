"""把运维知识库解析成星图数据。

结构：案例（##）→ 章节（###）→ 句子。
每个节点除了画布用的 label，还带完整 text / 来源行号 / 稳定键，
搜索和详情都用完整文本，不再受 44 字截断影响。
"""
import hashlib
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


def _key(*parts) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def parse(path):
    if not os.path.exists(path):
        return {"error": "知识库文件不存在", "path": path,
                "nodes": [], "edges": [], "stats": {"cases": 0, "nodes": 0, "edges": 0}}
    try:
        fh = open(path, encoding="utf-8", errors="replace")
        raw_text = fh.read()
        fh.close()
    except Exception as e:                       # noqa: BLE001
        return {"error": "读取失败：%s" % e, "nodes": [], "edges": [],
                "stats": {"cases": 0, "nodes": 0, "edges": 0}}

    nodes, edges = [], []
    nid = 0

    def add(label, kind, size, payload):
        nonlocal nid
        nid += 1
        n = {"id": nid, "label": label, "kind": kind, "size": size}
        n.update(payload)
        nodes.append(n)
        return nid

    case_id = None
    case_title = ""
    case_date = ""
    sec_id = None
    sec_kind = ""
    count = 0
    in_code = False

    for lineno, line in enumerate(raw_text.splitlines(), 1):
        s = line.strip()

        if s.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue

        if line.startswith("## "):
            title = line[3:].strip()
            m = re.match(r"(\d{4}-\d{2}-\d{2})\s+(.*)", title)
            case_date = m.group(1) if m else ""
            case_title = (m.group(2) if m else title).strip()
            case_id = add(case_title, "case", 6,
                          {"stable_key": "case:" + _key(case_title),
                           "text": case_title, "case_id": None,
                           "case_title": case_title, "date": case_date,
                           "source_line": lineno})
            sec_id = None
            count = 0
            continue

        if line.startswith("### "):
            if case_id is None:
                continue
            sec_kind = line[4:].strip()
            sec_id = add(sec_kind, "section", 3.0,
                         {"stable_key": "sec:" + _key(case_title, sec_kind),
                          "text": sec_kind,
                          "color": KIND_COLOR.get(sec_kind, "#8b95a8"),
                          "parent": case_id, "case_id": case_id,
                          "case_title": case_title, "section_type": sec_kind,
                          "source_line": lineno})
            edges.append({"a": case_id, "b": sec_id})
            count = 0
            continue

        if not s or s.startswith("#") or s.startswith("|"):
            continue
        if sec_id is None or count >= MAX_PER_SECTION:
            continue

        text = re.sub(r"^[-*>\d.、)\s]+", "", s)
        clean = re.sub(r"[`*_#>\[\]()]", "", text)
        for part in re.split(r"[。；;!?！？]", clean):
            part = part.strip()
            if count >= MAX_PER_SECTION:
                break
            if len(part) < 5:
                continue
            pid = add(part[:44], "point", 1.5,
                      {"stable_key": "pt:" + _key(case_title, sec_kind, part),
                       "text": part,
                       "color": KIND_COLOR.get(sec_kind, "#8b95a8"),
                       "parent": sec_id, "case_id": case_id,
                       "case_title": case_title, "section_type": sec_kind,
                       "source_line": lineno})
            edges.append({"a": sec_id, "b": pid})
            count += 1

    cases = sum(1 for n in nodes if n["kind"] == "case")
    return {"nodes": nodes, "edges": edges,
            "stats": {"cases": cases, "nodes": len(nodes), "edges": len(edges)},
            "source": os.path.basename(path)}
