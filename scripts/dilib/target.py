# -*- coding: utf-8 -*-
"""目标文件分析：批注要求、空章节、表单字段、数据填写区、行内空白、勾选框。"""
from __future__ import annotations

import re

from . import tablemap
from .common import short

BLANK_RE = re.compile(r"(\S)([ 　]{2,}|_{3,})(\S)|^([ 　]{2,}|_{3,})\S|(：|:)([ 　]{2,}|_{3,})\s*$")
DATE_BLANK_RE = re.compile(r"年\s{1,}月\s{1,}日|^\s*年\s*月\s*日")
PLACEHOLDER_RE = re.compile(r"_{3,}|【[^】]{0,20}】|\{\{[^}]+\}\}|（待[填补]）|\(待[填补]\)|XXX+|×××")


def _cells_by_row(t):
    rows = {}
    for c in t["cells"]:
        rows.setdefault(c["r"], []).append(c)
    for r in rows:
        rows[r].sort(key=lambda c: c["c"])
    return rows


def _covering(t, r, col):
    """返回覆盖 (r, col) 的原点单元格。"""
    for c in t["cells"]:
        if c["r"] <= r < c["r"] + c.get("rs", 1) and c["c"] <= col < c["c"] + c.get("cs", 1):
            return c
    return None


def is_label(text):
    t = text.strip()
    return 0 < len(t) <= 24 and not re.fullmatch(r"[\d.\-+~%\s]+", t) and not BLANK_RE.search(text) and "□" not in t


def analyze_table(t):
    tid = t["id"]
    rows = _cells_by_row(t)
    nrows = t["nrows"]
    empty_row = {}
    for r in range(nrows):
        own = rows.get(r, [])
        empty_row[r] = len(own) >= 2 and all(not c["text"].strip() and not c.get("imgs") for c in own)
    regions, used = [], set()

    def datalike(r):
        own = rows.get(r, [])
        return len(own) >= 3 and any(c["text"].strip() for c in own)

    def header_like(r):
        own = rows.get(r, [])
        return sum(1 for c in own if c["text"].strip() and tablemap.role_of([c["text"].strip()])[0]) >= 2

    r = 0
    while r < nrows:
        if empty_row[r] or datalike(r):
            sig = tuple((c["c"], c.get("cs", 1)) for c in rows[r])
            r2 = r
            while r2 + 1 < nrows and (empty_row[r2 + 1] or datalike(r2 + 1)) and tuple((c["c"], c.get("cs", 1)) for c in rows[r2 + 1]) == sig:
                r2 += 1
            filled = any(not empty_row[x] for x in range(r, r2 + 1))
            if filled and r2 > r and header_like(r):
                r += 1  # 首行是表头（与数据行结构相同）：数据从下一行开始
            if (r2 > r or len(sig) >= 3) and (not filled or r2 - r >= 3):
                hr_end = r - 1
                if hr_end >= 0:
                    origins = []
                    for col in range(t["ncols"]):
                        cv = _covering(t, hr_end, col)
                        if cv:
                            origins.append(cv["r"])
                    hr_start = min(origins) if origins else hr_end
                    # 表头不应跨越到上方更早的表单区：限制最多3行
                    hr_start = max(hr_start, hr_end - 2)
                else:
                    hr_start = hr_end = -1
                cols = []
                for c in rows[r]:
                    path = []
                    seen = set()
                    for hr in range(hr_start, hr_end + 1):
                        cv = _covering(t, hr, c["c"]) if hr >= 0 else None
                        if cv and id(cv) not in seen and cv["text"].strip():
                            seen.add(id(cv))
                            path.append(cv["text"].strip())
                    cols.append({"c": c["c"], "cs": c.get("cs", 1), "path": path})
                if filled and sum(1 for c in cols if tablemap.role_of(c["path"])[0]) < 2:
                    r = r2 + 1  # 已有内容但表头不像数据列表头（如“标签/值”型表单行）：不当作数据区
                    continue
                left = None
                lc = _covering(t, r, 0)
                if lc and lc["r"] < r and lc["text"].strip():
                    left = lc["text"].strip()
                regions.append({"id": "%s.D%d" % (tid, len(regions)), "rows": [r, r2], "header": [hr_start, hr_end],
                                "cols": cols, "label": left, "filled": filled})
                used.update(range(max(hr_start, 0), r2 + 1))
                r = r2 + 1
                continue
        r += 1
    fields, blanks, checks = [], [], []
    for r in range(nrows):
        own = rows.get(r, [])
        for i, c in enumerate(own):
            txt = c["text"]
            cid = "%s.R%d.C%d" % (tid, c["r"], c["c"])
            if "□" in txt or "☐" in txt or "■" in txt or "☑" in txt:
                opts = [x.strip() for x in re.split(r"[□☐■☑]", txt)[1:] if x.strip()]
                checks.append({"cell": cid, "options": [short(o, 12) for o in opts], "text": short(txt, 60)})
            compact = re.sub(r"[\s　]+", "", txt)
            # “组　别”“备　注”等分散对齐的标签不是待填空白（日期“年 月 日”除外）
            is_spaced_label = bool(re.fullmatch(r"[一-鿿]{1,6}", compact)) and not DATE_BLANK_RE.search(txt)
            if (BLANK_RE.search(txt) or DATE_BLANK_RE.search(txt)) and txt.strip() and not is_spaced_label:
                blanks.append({"cell": cid, "text": txt.strip()})
            if r in used:
                continue
            if is_label(txt) and i + 1 < len(own) and not own[i + 1]["text"].strip():
                nxt = own[i + 1]
                fields.append({"label": re.sub(r"\s+", "", txt), "cell": "%s.R%d.C%d" % (tid, nxt["r"], nxt["c"]),
                               "label_cell": cid})
    return {"id": tid, "nrows": nrows, "ncols": t["ncols"], "regions": regions, "fields": fields, "blanks": blanks,
            "checks": checks}


def analyze(model):
    m = model.m
    res = {"name": m.get("name"), "type": m["type"], "comments": [], "sections": [], "tables": [], "placeholders": []}
    if m["type"] == "xlsx":
        return analyze_sheet_target(m)
    comments = m.get("comments", [])
    res["comments"] = comments
    by = model.by
    heads = {s["start"]: s for s in m.get("sections", []) if s.get("in", "body") == "body"}
    secs = [s for s in m.get("sections", []) if s.get("in", "body") == "body"]
    for s in secs:
        body = []
        for x in s["blocks"]:
            if x in heads:
                break
            body.append(x)
        nonempty = [x for x in body if by.get(x) and (by[x]["t"] == "tbl" or by[x].get("text", "").strip() or by[x].get("imgs"))]
        req = [c for c in comments if s["start"] in c.get("blocks", [])]
        res["sections"].append({"id": s["id"], "title": s["title"], "start": s["start"], "depth": s.get("depth", 0),
                                "body": body, "empty": not nonempty,
                                "requirements": [{"comment": c["id"], "text": c["text"], "author": c.get("author", "")}
                                                 for c in req]})
    for b in m.get("blocks", []):
        if b["t"] == "tbl" and "in" not in b:
            res["tables"].append(analyze_table(b))
        elif b["t"] == "p" and "in" not in b and PLACEHOLDER_RE.search(b.get("text", "")):
            res["placeholders"].append({"block": b["id"], "text": short(b["text"], 80)})
    # 批注落在表格单元格上的，作为该单元格/字段的要求
    for c in comments:
        for x in c.get("blocks", []):
            mm = re.match(r"^(T\d+\.R\d+\.C\d+)", x)
            if mm:
                c.setdefault("cells", []).append(mm.group(1))
    return res


def analyze_sheet_target(m):
    """Excel 目标：找“标签 + 右侧空格”字段与空白数据区。"""
    res = {"name": m.get("name"), "type": "xlsx", "comments": [], "sections": [], "tables": [], "placeholders": [],
           "sheets": []}
    from .read_other import col_letter
    for sh in m["sheets"]:
        fields = []
        for r, row in enumerate(sh["rows"]):
            for c, v in enumerate(row):
                if isinstance(v, str) and is_label(v):
                    nxt = row[c + 1] if c + 1 < len(row) else None
                    if nxt in (None, ""):
                        fields.append({"label": v.strip(), "cell": "%s!%s%d" % (sh["name"], col_letter(c + 1), r + 1)})
        res["sheets"].append({"name": sh["name"], "fields": fields[:200]})
    return res


def summary(an, max_lines=60):
    lines = ["目标文件：%s" % an["name"]]
    if an["type"] == "xlsx":
        for sh in an.get("sheets", []):
            lines.append("  工作表 %s：待填字段 %d 个：%s" % (sh["name"], len(sh["fields"]),
                                                     "；".join("%s→%s" % (f["label"], f["cell"]) for f in sh["fields"][:15])))
        return "\n".join(lines)
    if an["comments"]:
        lines.append("  批注要求 %d 条（目标文件中的批注将作为填写要求）：" % len(an["comments"]))
        for c in an["comments"][:20]:
            lines.append("    #%s @%s “%s”  ← 锚定于“%s”" % (c["id"], ",".join(c.get("blocks", [])[:2]), short(c["text"], 60),
                                                         short(c.get("anchor", ""), 24)))
    secs = an["sections"]
    if secs:
        lines.append("  章节 %d 个（空=需填写）：" % len(secs))
        for s in secs[:40]:
            lines.append("    %s%s %s%s%s" % ("  " * s["depth"], s["id"], short(s["title"], 30), "  [空]" if s["empty"] else "",
                                            ("  要求:" + "；".join(short(q["text"], 40) for q in s["requirements"])) if s["requirements"] else ""))
    for t in an["tables"]:
        lines.append("  表格 %s（%d行×%d列）：" % (t["id"], t["nrows"], t["ncols"]))
        for rg in t["regions"]:
            cols = " | ".join("C%d:%s" % (c["c"], "/".join(short(p, 10) for p in c["path"]) or "?") for c in rg["cols"])
            lines.append("    数据区 %s：第%d-%d行(共%d空行)%s 列：%s" % (rg["id"], rg["rows"][0], rg["rows"][1],
                                                           rg["rows"][1] - rg["rows"][0] + 1,
                                                           ("，左侧标签“%s”" % short(rg["label"], 10)) if rg.get("label") else "", cols))
        if t["fields"]:
            lines.append("    空字段：" + "；".join("%s→%s" % (short(f["label"], 12), f["cell"]) for f in t["fields"][:20]))
        if t["blanks"]:
            lines.append("    含空白待填：" + "；".join("%s“%s”" % (b["cell"], short(b["text"], 30)) for b in t["blanks"][:10]))
        if t["checks"]:
            lines.append("    勾选项：" + "；".join("%s[%s]" % (c["cell"], "/".join(c["options"][:6])) for c in t["checks"][:5]))
    if an["placeholders"]:
        lines.append("  占位符：" + "；".join("%s“%s”" % (p["block"], p["text"]) for p in an["placeholders"][:10]))
    return "\n".join(lines[:max_lines])
