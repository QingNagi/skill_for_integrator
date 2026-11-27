# -*- coding: utf-8 -*-
"""docx -> Markdown（标题、段落、表格、图片、题注），用于“其他格式输出”或给小模型阅读。"""
from __future__ import annotations

import os
import shutil

from .content import Model, render_runs
from .read_docx import DocxReader


def _cell(c):
    t = render_runs(c["runs"]) if c.get("runs") else c["text"]
    return t.replace("\n", "<br>").replace("|", "\\|")


def docx_to_md(path, out_path):
    media = os.path.splitext(out_path)[0] + "_图片"
    rd = DocxReader(path, media)
    m = rd.read()
    md = Model(m, "X")
    heads = {s["start"]: s for s in m["sections"] if s.get("in", "body") == "body"}
    lines = []
    for b in m["blocks"]:
        if b.get("in"):
            continue
        if b["t"] == "p":
            txt = render_runs(b["runs"]) if b.get("runs") else b["text"]
            imgs = b.get("imgs") or []
            for iid in imgs:
                info = next((v for v in rd.images.values() if v["id"] == iid), None)
                if info and info.get("file"):
                    lines.append("![%s](%s)" % (iid, os.path.relpath(info["file"], os.path.dirname(out_path)).replace("\\", "/")))
            txt = "".join(r[0] for r in (b.get("runs") or [[b["text"], ""]]) if not r[1].startswith("@"))
            if not txt.strip():
                if not imgs:
                    lines.append("")
                continue
            if b["id"] in heads:
                lines.append("#" * min(6, heads[b["id"]].get("depth", 0) + 1) + " " + ((b.get("label", "") + " ") if b.get("label") else "") + txt.strip())
            elif b.get("cap"):
                lines.append("**%s**" % (((b.get("label", "") + " ") if b.get("label") else "") + txt.strip()))
            else:
                lines.append(((b.get("label", "") + " ") if b.get("label") else "") + txt)
            lines.append("")
        elif b["t"] == "tbl":
            grid, runs, origin = md.table_grid(b["id"], fill_merged=True)
            for r in range(b["nrows"]):
                row = []
                for c in range(b["ncols"]):
                    o = origin[r][c]
                    if o is None:
                        row.append("")
                    elif o[1] != c:
                        row.append("")       # 横向合并的后续列留空
                    else:
                        cell = {"text": grid[r][c] or "", "runs": runs[r][c]}
                        row.append(_cell(cell))
                lines.append("| " + " | ".join(row) + " |")
                if r == 0:
                    lines.append("|" + "---|" * b["ncols"])
            lines.append("")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).strip() + "\n")
    return out_path
