# -*- coding: utf-8 -*-
"""docx -> 内容模型（带稳定ID的段落/表格/图/章节/批注），供提纲、检索、引用和写入使用。

ID 规则（同一文件多次解析结果一致）：
  P12            正文第12个段落（从0计）
  T3             正文第3个表格；T3.R5.C2 第5行、网格第2列（合并单元格取左上角）
  T3.R5.C2.P1    单元格内第1个段落；T3.R5.C2.T0 单元格内嵌套表格
  F4             第4个图（图片/OLE预览/图表）
  H7             第7个章节（标题或“（1）xxx”这类伪标题）
"""
from __future__ import annotations

import os
import re

from .common import norm_text, short
from .numbering import Numbering
from .text_patterns import CAPTION_RE, HEAD_PATTERNS
from .ooxml import (Package, W, W_P, W_PPR, W_R, W_RPR, W_T, W_TBL, W_TC, W_TR, W_VAL, iter_skip_fallback, qn, w)

SYMBOL_MAP = {
    "F0B7": "•", "F0A3": "≤", "F0B3": "≥", "F0B1": "±", "F0D7": "×", "F070": "π", "F06D": "μ", "F057": "Ω",
    "F0B0": "°", "F044": "Δ", "F0BB": "≈", "F0B9": "≠", "F0AE": "→", "F0AC": "←", "F0A5": "∞", "F0D6": "√",
    "F0E5": "∑", "F06C": "λ", "F061": "α", "F062": "β", "F067": "γ", "F064": "δ", "F071": "θ", "F074": "τ",
    "F0A8": "◆", "F0FC": "√", "F06E": "■", "F075": "◆",
}

A_BLIP = qn("a:blip")
V_IMAGEDATA = qn("v:imagedata")
C_CHART = qn("c:chart")
WP_EXTENT = qn("wp:extent")
V_SHAPE = qn("v:shape")
R_EMBED = qn("r:embed")
R_LINK = qn("r:link")
R_ID = qn("r:id")


def _rpr_flags(rpr):
    if rpr is None:
        return ""
    f = ""
    va = rpr.find(w("vertAlign"))
    if va is not None:
        v = va.get(W_VAL)
        if v == "subscript":
            f += "_"
        elif v == "superscript":
            f += "^"
    b = rpr.find(w("b"))
    if b is not None and b.get(W_VAL) not in ("0", "false", "off"):
        f += "b"
    i = rpr.find(w("i"))
    if i is not None and i.get(W_VAL) not in ("0", "false", "off"):
        f += "i"
    u = rpr.find(w("u"))
    if u is not None and u.get(W_VAL) not in ("none", None):
        f += "u"
    return f


class DocxReader:
    def __init__(self, path, media_dir=None):
        self.path = path
        self.pkg = Package(path)
        self.media_dir = media_dir
        self.num = Numbering(self.pkg)
        self.doc = self.pkg.xml("word/document.xml")
        self.rels = self.pkg.rels("word/document.xml")
        self.blocks = []          # 顶层顺序块（含单元格内段落，方便检索）
        self.by_id = {}
        self.el_by_id = {}        # id -> XML 元素（写入/锚定用）
        self.images = {}          # 图片部件 -> 信息
        self.figures = []
        self.comments = {}
        self.anchor_text = {}
        self.anchor_blocks = {}
        self._open_comments = set()
        self.np = self.nt = 0
        self.nfig = 0

    # ------------------------------------------------------------ 公共入口
    def read(self):
        self._read_comments()
        body = self.doc.find(w("body"))
        self._walk_container(body, "", top=True)
        self._post_captions()
        sections = self._build_sections()
        title = self._guess_title()
        comments = []
        for cid, c in self.comments.items():
            comments.append({"id": cid, "author": c["author"], "text": c["text"],
                             "anchor": short(self.anchor_text.get(cid, ""), 80),
                             "blocks": self.anchor_blocks.get(cid, [])[:12]})
        for b in self.blocks:
            b.pop("_sid", None)
        return {
            "type": "docx", "title": title,
            "blocks": self.blocks, "sections": sections, "figures": self.figures,
            "comments": comments,
            "stats": {"paras": self.np, "tables": self.nt, "figures": self.nfig},
        }

    # ------------------------------------------------------------ 批注
    def _read_comments(self):
        if not self.pkg.has("word/comments.xml"):
            return
        root = self.pkg.xml("word/comments.xml")
        for c in root.findall(w("comment")):
            cid = c.get(w("id"))
            paras = []
            for p in c.findall(".//" + W_P):
                paras.append("".join(self._para_runs_text(p)))
            self.comments[cid] = {"author": c.get(w("author")) or "", "text": "\n".join(x for x in paras if x)}

    # ------------------------------------------------------------ 遍历
    def _children(self, container):
        for el in container:
            t = el.tag
            if t in (W_P, W_TBL):
                yield el
            elif t == w("sdt"):
                sc = el.find(w("sdtContent"))
                if sc is not None:
                    yield from self._children(sc)
            elif t in (w("customXml"), w("smartTag")):
                yield from self._children(el)
            elif t in (w("commentRangeStart"), w("commentRangeEnd")):
                yield el

    def _walk_container(self, container, prefix, top=False):
        ids = []
        pi = ti = 0
        for el in self._children(container):
            if el.tag == w("commentRangeStart"):
                self._open_comments.add(el.get(w("id")))
                continue
            if el.tag == w("commentRangeEnd"):
                self._open_comments.discard(el.get(w("id")))
                continue
            if el.tag == W_P:
                bid = ("P%d" % self.np) if top else "%sP%d" % (prefix, pi)
                if top:
                    self.np += 1
                pi += 1
                self._para(el, bid, prefix.rstrip(".") or "body")
                ids.append(bid)
            else:
                bid = ("T%d" % self.nt) if top else "%sT%d" % (prefix, ti)
                if top:
                    self.nt += 1
                ti += 1
                self._table(el, bid, prefix.rstrip(".") or "body")
                ids.append(bid)
        return ids

    # ------------------------------------------------------------ 段落
    def _para_runs(self, p):
        """返回 [[text, flags], ...]，处理符号、制表、换行、域结果、批注锚点文本。"""
        runs = []
        fld_depth = 0
        in_instr = []
        for e in iter_skip_fallback(p):
            t = e.tag
            if t == w("commentRangeStart"):
                cid = e.get(w("id"))
                self._open_comments.add(cid)
                lst = self.anchor_blocks.setdefault(cid, [])
                if getattr(self, "_cur_bid", None) and self._cur_bid not in lst:
                    lst.append(self._cur_bid)
            elif t == w("commentRangeEnd"):
                self._open_comments.discard(e.get(w("id")))
            elif t == W_R:
                fl = _rpr_flags(e.find(W_RPR))
                for c in e:
                    ct = c.tag
                    txt = None
                    if ct == w("fldChar"):
                        ft = c.get(w("fldCharType"))
                        if ft == "begin":
                            in_instr.append(True)
                        elif ft == "separate":
                            if in_instr:
                                in_instr[-1] = False
                        elif ft == "end":
                            if in_instr:
                                in_instr.pop()
                    elif any(in_instr):
                        continue
                    elif ct == W_T:
                        txt = c.text or ""
                    elif ct == w("tab"):
                        txt = "\t"
                    elif ct in (w("br"), w("cr")):
                        txt = "\n"
                    elif ct == w("noBreakHyphen"):
                        txt = "-"
                    elif ct == w("sym"):
                        code = (c.get(w("char")) or "").upper()
                        txt = SYMBOL_MAP.get(code) or SYMBOL_MAP.get("F0" + code[-2:]) or "?"
                    elif ct in (w("drawing"), w("object"), w("pict")):
                        # 行内图片/公式对象：记为 [矢量文字, "@图片ID"]，复制时还原为图片
                        for iid, vt in self._inline_images(c):
                            runs.append([vt, "@" + iid])
                        continue
                    if txt:
                        if runs and runs[-1][1] == fl:
                            runs[-1][0] += txt
                        else:
                            runs.append([txt, fl])
                        for cid in self._open_comments:
                            self.anchor_text[cid] = self.anchor_text.get(cid, "") + txt
            elif t == qn("m:t"):
                txt = e.text or ""
                if txt:
                    runs.append([txt, ""])
        return runs

    def _inline_images(self, el):
        """run 内的图片/公式对象 -> [(图片ID, 矢量文字)]。"""
        res = []
        ext = None
        for e in iter_skip_fallback(el):
            if e.tag == WP_EXTENT:
                ext = (int(e.get("cx")), int(e.get("cy")))
                break
            if e.tag == V_SHAPE and e.get("style"):
                m1 = re.search(r"width:([\d.]+)pt", e.get("style"))
                m2 = re.search(r"height:([\d.]+)pt", e.get("style"))
                if m1 and m2:
                    ext = (int(float(m1.group(1)) * 12700), int(float(m2.group(1)) * 12700))
                    break
        for e in iter_skip_fallback(el):
            rid, kind = None, "image"
            if e.tag == A_BLIP:
                rid = e.get(R_EMBED) or e.get(R_LINK)
            elif e.tag == V_IMAGEDATA:
                rid = e.get(R_ID) or e.get(qn("o:relid"))
            elif e.tag == C_CHART:
                rid, kind = e.get(R_ID), "chart"
            if not rid:
                continue
            tgt, mode = self.rels.target(rid)
            if not tgt or mode == "External":
                continue
            info = self._register_image(self.pkg.resolve("word/document.xml", tgt), kind, ext)
            if info:
                res.append((info["id"], info.get("text", "")))
        return res

    def _para_runs_text(self, p):
        out = []
        for e in p.iter():
            if e.tag == W_T and e.text:
                out.append(e.text)
            elif e.tag == w("tab"):
                out.append("\t")
        return out

    def _para_images(self, p, bid):
        imgs = []
        for e in iter_skip_fallback(p):
            rid = None
            kind = "image"
            if e.tag == A_BLIP:
                rid = e.get(R_EMBED) or e.get(R_LINK)
            elif e.tag == V_IMAGEDATA:
                rid = e.get(R_ID) or e.get(qn("o:relid"))
            elif e.tag == C_CHART:
                rid = e.get(R_ID)
                kind = "chart"
            if not rid:
                continue
            tgt, mode = self.rels.target(rid)
            if not tgt or mode == "External":
                continue
            part = self.pkg.resolve("word/document.xml", tgt)
            imgs.append({"part": part, "kind": kind})
        if not imgs:
            return []
        # 尺寸
        ext = None
        for e in iter_skip_fallback(p):
            if e.tag == WP_EXTENT:
                ext = (int(e.get("cx")), int(e.get("cy")))
                break
            if e.tag == V_SHAPE and e.get("style"):
                m1 = re.search(r"width:([\d.]+)pt", e.get("style"))
                m2 = re.search(r"height:([\d.]+)pt", e.get("style"))
                if m1 and m2:
                    ext = (int(float(m1.group(1)) * 12700), int(float(m2.group(1)) * 12700))
                    break
        out = []
        for im in imgs:
            info = self._register_image(im["part"], im["kind"], ext)
            if info:
                out.append(info["id"])
        return out

    def _register_image(self, part, kind, ext):
        if part in self.images:
            return self.images[part]
        if part not in self.pkg.parts:
            return None
        iid = "I%d" % len(self.images)
        info = {"id": iid, "part": part, "kind": kind, "ext": os.path.splitext(part)[1].lstrip(".").lower(),
                "cx": ext[0] if ext else None, "cy": ext[1] if ext else None}
        if kind == "chart":
            info["chart"] = self._chart_data(part)
            ch = info["chart"] or {}
            info["text"] = (ch.get("title") or "图表") + "：" + "；".join(
                "%s=%s" % (sr.get("name") or "系列", ",".join(sr.get("vals", [])[:12]))
                for sr in ch.get("series", [])[:6])
        else:
            if info["ext"] in ("wmf", "emf"):
                from .metafile import metafile_text
                info["text"] = metafile_text(self.pkg.parts[part], info["ext"])
        if kind != "chart" and self.media_dir:
            os.makedirs(self.media_dir, exist_ok=True)
            fn = os.path.join(self.media_dir, "%s.%s" % (iid, info["ext"]))
            with open(fn, "wb") as f:
                f.write(self.pkg.parts[part])
            info["file"] = fn
        self.images[part] = info
        return info

    def _chart_data(self, part):
        """解析图表缓存数据：标题 + 各系列(名称, 类别, 数值)。"""
        try:
            root = self.pkg.xml(part)
        except Exception:
            return None
        C = "{%s}" % "http://schemas.openxmlformats.org/drawingml/2006/chart"
        A = "{%s}" % "http://schemas.openxmlformats.org/drawingml/2006/main"

        def cache_vals(el):
            if el is None:
                return []
            pts = []
            for pt in el.iter(C + "pt"):
                v = pt.find(C + "v")
                pts.append((int(pt.get("idx", "0")), v.text if v is not None else ""))
            return [v for _, v in sorted(pts)]
        title = "".join(t.text or "" for t in root.iter(A + "t")) if root.find(".//" + C + "title") is not None else ""
        series = []
        for ser in root.iter(C + "ser"):
            name = cache_vals(ser.find(C + "tx"))
            cats = cache_vals(ser.find(C + "cat")) or cache_vals(ser.find(C + "xVal"))
            vals = cache_vals(ser.find(C + "val")) or cache_vals(ser.find(C + "yVal"))
            series.append({"name": name[0] if name else "", "cats": cats, "vals": vals})
        kinds = [k.tag.split("}")[1] for k in root.iter() if k.tag.endswith("Chart") and k.tag.startswith(C)]
        return {"title": title, "type": kinds[0] if kinds else "", "series": series}

    def _para(self, p, bid, container):
        ppr = p.find(W_PPR)
        sid = None
        if ppr is not None:
            ps = ppr.find(w("pStyle"))
            if ps is not None:
                sid = ps.get(W_VAL)
        if sid is None:
            sid = self._default_pstyle()
        for cid in list(self._open_comments):
            self.anchor_blocks.setdefault(cid, []).append(bid)
        self._cur_bid = bid
        runs = self._para_runs(p)
        self._cur_bid = None
        text = "".join(r[0] for r in runs)
        label = ""
        np_ = self.num.resolve(ppr, sid)
        if np_:
            label = self.num.label(*np_)
        lvl = None
        if ppr is not None and ppr.find(w("outlineLvl")) is not None:
            v = int(ppr.find(w("outlineLvl")).get(W_VAL))
            lvl = v if v < 9 else None
        if lvl is None and sid:
            lvl = self.num.outline_level(sid)
        blk = {"id": bid, "t": "p", "text": text, "_sid": sid}
        if any(r[1] for r in runs):
            blk["runs"] = runs
        if label:
            blk["label"] = label.strip()
        sname = self.num.style_name.get(sid, "") if sid else ""
        if sname:
            blk["style"] = sname
        if lvl is not None and text.strip():
            blk["lvl"] = lvl
        jc = ppr.find(w("jc")) if ppr is not None else None
        if jc is not None:
            blk["jc"] = jc.get(W_VAL)
        if container != "body":
            blk["in"] = container
        imgs = self._para_images(p, bid)
        if imgs:
            blk["imgs"] = imgs
        full = ((label.strip() + " ") if label.strip() else "") + text.strip()
        m = CAPTION_RE.match(full)
        if m and len(full) <= 80 and (("表" in sname or "图" in sname or "caption" in sname.lower() or "题注" in sname)
                                      or blk.get("jc") == "center" or label or len(full) <= 40):
            blk["cap"] = "表" if m.group(1) in ("表", "附表", "Table") else "图"
        self.blocks.append(blk)
        self.by_id[bid] = blk
        self.el_by_id[bid] = p
        return blk

    def _default_pstyle(self):
        if not hasattr(self, "_defp"):
            self._defp = None
            if self.pkg.has("word/styles.xml"):
                for st in self.pkg.xml("word/styles.xml").findall(w("style")):
                    if st.get(w("type")) == "paragraph" and st.get(w("default")) in ("1", "true"):
                        self._defp = st.get(w("styleId"))
        return self._defp

    # ------------------------------------------------------------ 表格
    def _rows(self, tbl):
        for el in tbl:
            if el.tag == W_TR:
                yield el
            elif el.tag == w("sdt"):
                sc = el.find(w("sdtContent"))
                if sc is not None:
                    yield from self._rows(sc)

    def _cells(self, tr):
        for el in tr:
            if el.tag == W_TC:
                yield el
            elif el.tag == w("sdt"):
                sc = el.find(w("sdtContent"))
                if sc is not None:
                    yield from self._cells(sc)

    def table_layout(self, tbl):
        """返回 (rows, ncols)；rows[r] = [{'tc','c','span','vm'}...]，并解析纵向合并。"""
        rows = []
        ncols = 0
        for tr in self._rows(tbl):
            trpr = tr.find(w("trPr"))
            col = 0
            if trpr is not None and trpr.find(w("gridBefore")) is not None:
                col = int(trpr.find(w("gridBefore")).get(W_VAL))
            cells = []
            for tc in self._cells(tr):
                tcpr = tc.find(w("tcPr"))
                span, vm = 1, None
                if tcpr is not None:
                    gs = tcpr.find(w("gridSpan"))
                    if gs is not None:
                        span = int(gs.get(W_VAL))
                    v = tcpr.find(w("vMerge"))
                    if v is not None:
                        vm = v.get(W_VAL) or "continue"
                cells.append({"tc": tc, "c": col, "span": span, "vm": vm})
                col += span
            ncols = max(ncols, col)
            rows.append(cells)
        # 纵向合并：continue 指向上方起始单元格
        for r, cells in enumerate(rows):
            for cell in cells:
                cell["rs"] = 1
                cell["origin"] = (r, cell["c"])
                if cell["vm"] == "continue" and r > 0:
                    for up in rows[r - 1]:
                        if up["c"] == cell["c"]:
                            cell["origin"] = up["origin"]
                            break
        for r, cells in enumerate(rows):
            for cell in cells:
                o = cell["origin"]
                if o != (r, cell["c"]):
                    for oc in rows[o[0]]:
                        if oc["c"] == o[1]:
                            oc["rs"] = max(oc["rs"], r - o[0] + 1)
        return rows, ncols

    def _table(self, tbl, bid, container):
        rows, ncols = self.table_layout(tbl)
        blk = {"id": bid, "t": "tbl", "nrows": len(rows), "ncols": ncols, "cells": []}
        if container != "body":
            blk["in"] = container
        self.blocks.append(blk)
        self.by_id[bid] = blk
        self.el_by_id[bid] = tbl
        for r, cells in enumerate(rows):
            self.el_by_id["%s.R%d" % (bid, r)] = cells
            for cell in cells:
                if cell["origin"] != (r, cell["c"]):
                    continue
                cid = "%s.R%d.C%d" % (bid, r, cell["c"])
                self.el_by_id[cid] = cell["tc"]
                start = len(self.blocks)
                sub = self._walk_container(cell["tc"], cid + ".")
                paras = [self.by_id[s] for s in sub if s in self.by_id and self.by_id[s]["t"] == "p"]
                text = "\n".join(((p.get("label", "") + " ") if p.get("label") and not p.get("lvl") else "") +
                                 p["text"] for p in paras).strip("\n")
                runs = []
                for k, p in enumerate(paras):
                    if k:
                        runs.append(["\n", ""])
                    if p.get("label"):
                        runs.append([p["label"] + " ", ""])
                    runs.extend(p.get("runs") or [[p["text"], ""]])
                c = {"r": r, "c": cell["c"], "text": text}
                if cell["span"] > 1:
                    c["cs"] = cell["span"]
                if cell["rs"] > 1:
                    c["rs"] = cell["rs"]
                if any(x[1] for x in runs):
                    c["runs"] = runs
                imgs = [i for p in paras for i in p.get("imgs", [])]
                if imgs:
                    c["imgs"] = imgs
                if any(self.by_id[s]["t"] == "tbl" for s in sub if s in self.by_id):
                    c["nested"] = [s for s in sub if self.by_id[s]["t"] == "tbl"]
                blk["cells"].append(c)
        blk["hdr"] = self._header_rows(blk)
        return blk

    def _header_rows(self, blk):
        """表头行数：首行 + 首行纵向合并覆盖的行。"""
        h = 1
        for c in blk["cells"]:
            if c["r"] == 0 and c.get("rs", 1) > 1:
                h = max(h, c["rs"])
        return min(h, max(1, blk["nrows"] - 1)) if blk["nrows"] > 1 else 1

    # ------------------------------------------------------------ 题注
    def _post_captions(self):
        top = [b for b in self.blocks if "in" not in b]
        prev_tbl = None
        for i, b in enumerate(top):
            if b["t"] == "tbl":
                # 表题在表前（最多向前看2段）
                for j in range(i - 1, max(-1, i - 3), -1):
                    pb = top[j]
                    if pb["t"] == "p" and pb.get("cap") == "表":
                        b["cap"] = self.caption_text(pb)
                        b["cap_id"] = pb["id"]
                        break
                    if pb["t"] == "tbl" or (pb["t"] == "p" and pb["text"].strip() and not pb.get("cap")):
                        break
                cap = b.get("cap", "")
                if prev_tbl is not None and re.search(r"[（(]续[)）]", cap):
                    b["cont"] = prev_tbl["id"]
                    b["cap"] = prev_tbl.get("cap", cap)
                foot = self._footnote_rows(b)
                if foot:
                    b["foot"] = foot
                prev_tbl = b if not b.get("cont") else prev_tbl
        # 图：含图片的段落 -> 之后最近的“图”题注（中间允许注释段落/尺寸表）
        pending = []
        for i, b in enumerate(top):
            if b["t"] == "p" and b.get("imgs"):
                pending.append(b)
                if b.get("cap") == "图" and b["text"].strip():
                    pass
            if b["t"] == "p" and b.get("cap") == "图" and pending:
                cap = self.caption_text(b)
                for pb in pending:
                    self._add_figure(pb, cap, b["id"])
                pending = []
            elif b["t"] == "p" and b.get("lvl") is not None and pending:
                for pb in pending:
                    self._add_figure(pb, "", None)
                pending = []
        for pb in pending:
            self._add_figure(pb, "", None)
        # 表格单元格内的图片
        for b in self.blocks:
            if b["t"] == "p" and b.get("imgs") and "in" in b and not any(b["id"] == f["para"] for f in self.figures):
                self._add_figure(b, "", None)

    def caption_text(self, pb):
        lab = pb.get("label", "")
        txt = pb["text"].strip()
        s = (lab + " " + txt).strip() if lab else txt
        return re.sub(r"\s+", " ", s)

    def _add_figure(self, pb, cap, cap_id):
        fid = "F%d" % self.nfig
        self.nfig += 1
        f = {"id": fid, "para": pb["id"], "imgs": pb["imgs"], "cap": cap}
        if cap_id:
            f["cap_id"] = cap_id
        self.figures.append(f)
        pb["fig"] = fid
        self.by_id[fid] = f

    def _footnote_rows(self, b):
        foot = []
        last = b["nrows"] - 1
        for c in b["cells"]:
            if c["r"] == last and c.get("cs", 1) >= b["ncols"] and b["nrows"] > 2:
                t = c["text"].strip()
                if t and (re.match(r"^(注|备注|说明|[a-z][\s:：)）]?|\d[)）])", t) or c.get("runs", [[""]])[0][1].find("^") >= 0):
                    foot.append(t)
                    b["foot_row"] = last
        return foot

    # ------------------------------------------------------------ 章节
    def _heading_rank(self, b, first_in_cell):
        if b["t"] != "p":
            return None
        text = b["text"].strip()
        if not text or b.get("cap"):
            return None
        if b.get("lvl") is not None:
            return b["lvl"] * 10
        full = (b.get("label", "") + text) if b.get("label") else text
        if len(full) > 80:
            return None
        for rank, pat in HEAD_PATTERNS:
            if pat.match(full):
                body = pat.sub("", full, count=1).strip()
                if not body or not re.search(r"[A-Za-z一-鿿]", body):
                    return None
                if first_in_cell:
                    return 100 + rank
                if len(full) <= 40 and not re.search(r"[。；;，,]$", full) and not re.search(r"[：:]\s*\S", body):
                    return 100 + rank
        return None

    def _build_sections(self):
        heads = []
        containers = {}
        for b in self.blocks:
            containers.setdefault(b.get("in", "body"), []).append(b)
        sections = []
        for cont, blocks in containers.items():
            stack = []
            seen_text = False
            multi = sum(1 for b in blocks if b["t"] == "p") >= 2
            if cont != "body" and not multi:
                continue          # 单段落单元格（普通数据格）不识别章节
            for b in blocks:
                first_in_cell = cont != "body" and not seen_text
                if b["t"] == "p" and b["text"].strip():
                    seen_text = True
                rank = self._heading_rank(b, first_in_cell)
                if rank is not None and cont != "body" and rank < 100:
                    rank = 109 + rank // 10        # 单元格内的大纲样式视为更低层级
                if rank is not None:
                    while stack and stack[-1]["rank"] >= rank:
                        stack.pop()
                    sec = {"id": None, "title": ((b.get("label", "") + " ") if b.get("label") else "") + b["text"].strip(),
                           "rank": rank, "start": b["id"], "blocks": [], "parent": stack[-1]["_k"] if stack else None,
                           "in": cont, "_k": len(heads)}
                    heads.append(sec)
                    for s in stack:
                        s["blocks"].append(b["id"])
                    stack.append(sec)
                else:
                    for s in stack:
                        s["blocks"].append(b["id"])
        # 按文档顺序编号
        order = {b["id"]: i for i, b in enumerate(self.blocks)}
        heads.sort(key=lambda s: order.get(s["start"], 0))
        kmap = {}
        for i, s in enumerate(heads):
            kmap[s["_k"]] = "H%d" % i
        for i, s in enumerate(heads):
            s["id"] = "H%d" % i
        for s in heads:
            s["parent"] = kmap.get(s["parent"]) if s["parent"] is not None else None
            s.pop("_k", None)
            s["title"] = re.sub(r"\s+", " ", s["title"])
            # 层级深度
        depth = {}
        for s in heads:
            depth[s["id"]] = depth.get(s["parent"], -1) + 1 if s["parent"] else 0
            s["depth"] = depth[s["id"]]
            nchar = sum(len(self.by_id[x].get("text", "")) for x in s["blocks"] if x in self.by_id and self.by_id[x]["t"] == "p")
            ntbl = sum(1 for x in s["blocks"] if x in self.by_id and self.by_id[x]["t"] == "tbl")
            s["size"] = {"chars": nchar, "tables": ntbl,
                         "figs": sum(1 for x in s["blocks"] if x in self.by_id and self.by_id[x].get("fig"))}
            self.by_id[s["id"]] = s
        return heads

    def _guess_title(self):
        lines, started = [], False
        for b in self.blocks[:80]:
            if "in" in b:
                continue
            if b["t"] != "p" or b.get("lvl") is not None:
                if started:
                    break
                continue
            t = b["text"].strip()
            centered = b.get("jc") == "center" or self.num.style_jc(b.get("_sid")) == "center"
            if t and centered and len(t) <= 40 and not re.match(r"^[\d（(]", t):
                lines.append(t)
                started = True
            elif t and started:
                break
            elif not t and len(lines) >= 1 and started and len(lines) >= 3:
                break
        if lines:
            return " ".join(lines[:4])
        for b in self.blocks[:60]:
            if b["t"] == "p" and "in" not in b and b.get("lvl") is not None and 2 <= len(b["text"].strip()) <= 30:
                return b["text"].strip()
        core = self.pkg.parts.get("docProps/core.xml")
        if core:
            m = re.search(rb"<dc:title>([^<]+)</dc:title>", core)
            if m:
                return m.group(1).decode("utf-8", "replace")
        return os.path.splitext(os.path.basename(self.path))[0]


def read_docx(path, media_dir=None):
    return DocxReader(path, media_dir).read()


def element_map(path):
    """供写入/锚定：返回 (reader, el_by_id)。"""
    r = DocxReader(path)
    r.read()
    return r, r.el_by_id
