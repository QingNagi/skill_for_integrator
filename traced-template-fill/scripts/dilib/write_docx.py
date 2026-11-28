# -*- coding: utf-8 -*-
"""把 plan 中的操作写入目标 docx，生成带批注（含原文定位链接）的草稿；并生成带书签的源文件快照。"""
from __future__ import annotations

import copy
import datetime
import os
import re
import shutil
from collections import Counter
from xml.etree import ElementTree as ET

from .common import TOOL_AUTHOR, TOOL_INITIALS, ensure_dir, norm_text, read_json, short, write_json
from .ooxml import (CT_COMMENTS, IMAGE_CT, NS, PPR_ORDER, RPR_ORDER, RT_COMMENTS, RT_HYPERLINK, RT_IMAGE, TBLPR_ORDER,
                    TCPR_ORDER, TRPR_ORDER, W_P, W_PPR, W_R, W_RPR, W_T, W_TBL, W_TC, W_TR, W_VAL, XML_NS, Package,
                    get_ppr, make_par, make_ppr, make_rpr, make_run, parse_xml_fragment, qn, remove_child, set_child, w)
from .profile import size_pt
from .read_docx import DocxReader

EMU_PER_CM = 360000
START_MARKS = {w("bookmarkStart"), w("commentRangeStart")}
END_MARKS = {w("bookmarkEnd"), w("commentRangeEnd")}


def flags_of(f):
    s = set()
    if "_" in f:
        s.add("sub")
    if "^" in f:
        s.add("sup")
    for ch in "biu":
        if ch in f:
            s.add(ch)
    return s


def bm_name(ref):
    return ("_DI_" + re.sub(r"[^A-Za-z0-9]", "_", ref))[:40]


def link_target(rel_path):
    """按 Word 自身的写法：相对路径、Windows 反斜杠、空格编码为 %20。"""
    p = rel_path.replace("/", "\\") if os.name == "nt" else rel_path.replace("\\", "/")
    return p.replace("%", "%25").replace(" ", "%20").replace("#", "%23")


# ======================================================================= 源文件快照
class Snapshots:
    def __init__(self, job, draft_dir):
        self.job = job
        self.dir = ensure_dir(os.path.join(draft_dir, "源文件快照"))
        self.reg_file = job.p("草稿", ".快照书签.json")
        self.refs = {k: set(v) for k, v in (read_json(self.reg_file, {}) or {}).items()}
        self.names = {}
        self.raw = bool(getattr(job, "raw_snapshot", False))     # --raw-snapshot：快照保留源文件原样，不净化
        self.warns = []                                          # 快照净化中的警告（cmd_draft 一并输出）

    def file_for(self, alias):
        if not self.names:
            candidates = {}
            for source in self.job.data["sources"]:
                key = source["alias"]
                md = self.job.model(key).m
                base = os.path.basename(md["path"])
                ext = os.path.splitext(md["work"])[1].lower()
                if ext in (".docx", ".xlsx") and os.path.splitext(base)[1].lower() != ext:
                    base = os.path.splitext(base)[0] + ext
                candidates[key] = base
            counts = Counter(name.casefold() for name in candidates.values())
            reserved = set(counts)
            for key, base in candidates.items():
                if counts[base.casefold()] > 1:
                    base = key + "_" + base
                    while base.casefold() in reserved:
                        base = key + "_" + base
                self.names[key] = base
                reserved.add(base.casefold())
        return self.names[alias]

    def link(self, alias, ref):
        """登记引用并返回 (相对路径, 锚点, 提示)。"""
        md = self.job.model(alias).m
        fn = self.file_for(alias)
        rel = "源文件快照/" + fn
        anchor = None
        if md["type"] in ("docx",):
            self.refs.setdefault(alias, set()).add(ref)
            anchor = bm_name(ref)
        elif md["type"] == "xlsx":
            anchor = ref if "!" in ref else None
        elif md["type"] == "pdf":
            anchor = None
        tip = "原文件：%s  位置：%s" % (os.path.basename(md["path"]), ref)     # 只写文件名，不带本机路径
        return rel, anchor, tip

    def build(self):
        for s in self.job.data["sources"]:
            alias = s["alias"]
            md = self.job.model(alias).m
            dst = os.path.join(self.dir, self.file_for(alias))
            if md["type"] == "docx":
                self._docx(md["work"], dst, self.refs.get(alias, set()))
            else:
                src = md["work"] if md["type"] == "xlsx" and md["work"].lower().endswith(".xlsx") else md["path"]
                shutil.copy2(src, dst)
        write_json(self.reg_file, {k: sorted(v) for k, v in self.refs.items()})

    def _docx(self, work, dst, refs):
        rd = DocxReader(work)
        model = rd.read()
        el = rd.el_by_id
        doc = rd.doc
        ids = [int(b.get(w("id"))) for b in doc.iter(w("bookmarkStart")) if (b.get(w("id")) or "").isdigit()]
        nid = max(ids + [0]) + 1
        by = rd.by_id

        def paras_of(x):
            if x is None:
                return []
            if isinstance(x, list):          # 行：单元格信息列表
                out = []
                for cell in x:
                    out += [p for p in cell["tc"].iter(W_P)]
                return out
            if x.tag == W_P:
                return [x]
            return list(x.iter(W_P))
        for ref in sorted(refs):
            target = None
            hl = True
            if ref == "TITLE":
                first = next((b for b in model["blocks"] if b["t"] == "p" and b["text"].strip() and "in" not in b), None)
                target = [el[first["id"]]] if first else []
                hl = False
            elif ref.startswith("H") and ref in by:
                target = [el[by[ref]["start"]]]
            elif ref.startswith("F") and ref in by:
                target = [el[by[ref]["para"]]]
            elif re.match(r"^T\d+$", ref) and ref in by:
                cap = by[ref].get("cap_id")
                target = [el[cap]] if cap else paras_of(el.get(ref + ".R0"))[:1]
                hl = bool(cap)
            else:
                target = paras_of(el.get(ref))
            if not target:
                continue
            p0, p1 = target[0], target[-1]
            bs = ET.Element(w("bookmarkStart"), {w("id"): str(nid), w("name"): bm_name(ref)})
            be = ET.Element(w("bookmarkEnd"), {w("id"): str(nid)})
            nid += 1
            pos = 1 if len(p0) and p0[0].tag == W_PPR else 0
            p0.insert(pos, bs)
            p1.append(be)
            if hl:
                for p in target:
                    for r in p.iter(W_R):
                        if r.find(W_T) is not None:
                            rpr = r.find(W_RPR)
                            if rpr is None:
                                rpr = ET.Element(W_RPR)
                                r.insert(0, rpr)
                            set_child(rpr, "highlight", RPR_ORDER, {"val": "yellow"}, replace=True)
        if not self.raw:
            self._sanitize(rd.pkg, dst)
        rd.pkg.save(dst)

    def _sanitize(self, pkg, dst):
        '''默认净化源文件快照：去掉原批注、作者、修订、隐藏文字、customXml、外部链接（--raw-snapshot 时不调用）。
        出错只登记警告，草稿照常生成，但要如实说这份快照没净化。'''
        name = os.path.basename(dst)
        try:
            from .sanitize import bookmark_names, sanitize_docx
            before = bookmark_names(pkg)
            stat, ws = sanitize_docx(pkg)
            lost = sorted(before - bookmark_names(pkg))
        except Exception as e:
            self.warns.append('源文件快照 %s 没有净化（%s: %s），里面可能带着源文件的原批注、作者等信息，不要外发' % (name, type(e).__name__, e))
            return
        self.warns.extend('源文件快照 %s：%s' % (name, x) for x in ws)
        if lost:
            self.warns.append('源文件快照 %s 净化后丢了 %d 个定位书签，对应的批注链接可能跳不到原文：%s' % (name, len(lost), '、'.join(lost[:3])))
        if stat.get('external_left'):
            self.warns.append('源文件快照 %s 仍有 %d 处外部引用（如链接的图片），没有处理' % (name, stat['external_left']))


# ======================================================================= 写入器
class DocxWriter:
    def __init__(self, target_docx, job, profile, draft_path):
        self.job, self.prof, self.draft_path = job, profile, draft_path
        self._load(target_docx)
        self.snap = Snapshots(job, os.path.dirname(draft_path))
        self.captions = {}           # 段落元素 -> (类型, 标题)
        self.n_img = 0
        self.warnings = []
        self.comment_buf = {}        # 锚点 -> 合并的批注
        self.word_questions = []

    def _load(self, path):
        self.rd = DocxReader(path)
        self.rd.read()
        self.pkg = self.rd.pkg
        self.el = self.rd.el_by_id
        self.doc = self.rd.doc
        self.body = self.doc.find(w("body"))
        self.parent = {c: p for p in self.doc.iter() for c in p}
        ids = [int(x.get("id")) for x in self.doc.iter(qn("wp:docPr")) if (x.get("id") or "").isdigit()]
        self.docpr = max(ids + [0]) + 1

    # ------------------------------------------------------------ 结构变更（先执行）
    def add_rows(self, op):
        tbl = self.el[op["table"]]
        rows = [r for r in tbl.iter(W_TR) if self.parent.get(r) is tbl or self.parent.get(self.parent.get(r)) is tbl]
        base = rows[op["after_row"]]
        par = self.parent[base]
        idx = list(par).index(base)
        for k in range(op["count"]):
            nr = copy.deepcopy(base)
            for t in nr.iter(W_T):
                t.text = ""
            par.insert(idx + 1 + k, nr)

    def reload_after_structure(self, tmp_path):
        self.pkg.save(tmp_path)
        self._load(tmp_path)

    # ------------------------------------------------------------ 定位
    def first_para(self, ref):
        x = self.el.get(ref)
        if x is None:
            raise KeyError(ref)
        if isinstance(x, list):
            x = x[0]["tc"]
        if x.tag == W_P:
            return x
        ps = [p for p in x.iter(W_P)]
        if not ps:
            p = ET.SubElement(x, W_P)
            return p
        return ps[0]

    def last_para(self, ref):
        x = self.el.get(ref)
        if isinstance(x, list):
            x = x[-1]["tc"]
        if x.tag == W_P:
            return x
        return [p for p in x.iter(W_P)][-1]

    @staticmethod
    def base_rpr(p):
        for r in p.iter(W_R):
            if r.find(W_T) is not None and r.find(W_RPR) is not None:
                rp = copy.deepcopy(r.find(W_RPR))
                remove_child(rp, "highlight")
                return rp
        ppr = p.find(W_PPR)
        if ppr is not None and ppr.find(W_RPR) is not None:
            rp = copy.deepcopy(ppr.find(W_RPR))
            for bad in ("ins", "del", "moveFrom", "moveTo", "rPrChange"):
                remove_child(rp, bad)
            return rp
        return None

    @staticmethod
    def replace_para_runs(p, runs):
        """Keep bookmarks and comment anchors around replacement text, including their reference runs."""
        tail = []
        for ch in list(p):
            if ch.tag == W_PPR or ch.tag in START_MARKS:
                continue
            p.remove(ch)
            if ch.tag in END_MARKS:
                tail.append(ch)
                continue
            for original in ch.iter(W_R):
                refs = original.findall(w("commentReference"))
                if refs:
                    ref_run = ET.Element(W_R)
                    props = original.find(W_RPR)
                    if props is not None:
                        ref_run.append(props)
                    ref_run.extend(refs)
                    tail.append(ref_run)
        p.extend(runs)
        p.extend(tail)

    # ------------------------------------------------------------ runs
    def build_runs(self, runs, base, extra_flags=(), font=None, latin=None, size=None, bold=None, alias=None):
        out = []
        for txt, f in runs:
            if f.startswith("@"):
                r = self.image_run(alias, f[1:], inline=True)
                if r is not None:
                    out.append(r)
                elif txt:
                    out.append(make_run(txt, make_rpr(base, extra_flags, font, latin, size, bold)))
                continue
            if not txt:
                continue
            fl = flags_of(f) | set(extra_flags)
            out.append(make_run(txt, make_rpr(base, fl, font, latin, size, bold)))
        return out

    # ------------------------------------------------------------ 单元格操作
    def set_cell(self, op, runs=None):
        cid = op["cell"]
        tc = self.el.get(cid)
        if tc is None:
            self.warnings.append("找不到单元格 %s" % cid)
            return
        ps = [p for p in tc if p.tag == W_P]
        if not ps:
            ps = [ET.SubElement(tc, W_P)]
        p0 = ps[0]
        base = self.base_rpr(p0)
        for p in ps[1:]:
            if any(ch.tag in START_MARKS or ch.tag in END_MARKS or ch.tag == w("commentReference") for ch in p.iter()):
                self.replace_para_runs(p, [])
            else:
                tc.remove(p)
        self.replace_para_runs(p0, self.build_runs(runs if runs is not None else op["runs"], base))

    def append_runs(self, op):
        tc = self.el.get(op["cell"])
        if tc is None:
            self.warnings.append("找不到单元格 %s" % op["cell"])
            return
        ps = [p for p in tc if p.tag == W_P]
        last = ps[-1]
        has_text = any((t.text or "").strip() for t in last.iter(W_T))
        if has_text:
            np_ = ET.Element(W_P)
            if last.find(W_PPR) is not None:
                np_.append(copy.deepcopy(last.find(W_PPR)))
            tc.insert(list(tc).index(last) + 1, np_)
            base = self.base_rpr(last)
            last = np_
        else:
            base = self.base_rpr(last) or self.base_rpr(ps[0])
        for r in self.build_runs(op["runs"], base):
            last.append(r)

    def fill_blanks(self, op):
        tc = self.el.get(op["cell"])
        p = next((p for p in tc.iter(W_P) if "".join(t.text or "" for t in p.iter(W_T)).strip()), None)
        if p is None:
            return
        text = "".join(t.text or "" for t in p.iter(W_T))
        vals = list(op["values"])
        out, pos, k = [], 0, 0
        for m in re.finditer(r"[ 　]{2,}|_{3,}", text):
            if k >= len(vals):
                break
            out.append(text[pos:m.start()].rstrip(" ") + " " + vals[k] + " ")
            pos = m.end()
            k += 1
        out.append(text[pos:].lstrip(" ") if k else text[pos:])
        base = self.base_rpr(p)
        self.replace_para_runs(p, [make_run("".join(out), make_rpr(base))])

    def set_text(self, op):
        ref = op.get("cell") or op.get("block")
        x = self.el.get(ref)
        if x is None:
            self.warnings.append("找不到 %s" % ref)
            return
        if getattr(x, "tag", None) == W_TC:
            self.set_cell({"cell": ref}, runs=[[op["text"], ""]])
        else:
            base = self.base_rpr(x)
            self.replace_para_runs(x, [make_run(op["text"], make_rpr(base))])

    # ------------------------------------------------------------ 标题格式
    def format_heading(self, op):
        p = self.el.get(op["block"])
        if p is None:
            return
        h = self.prof["heading"]
        sz = size_pt(h)
        for r in p.iter(W_R):
            if r.find(W_T) is None:
                continue
            rpr = r.find(W_RPR)
            if rpr is None:
                rpr = ET.Element(W_RPR)
                r.insert(0, rpr)
            new = make_rpr(rpr, (), h.get("font"), h.get("latin_font"), sz, h.get("bold"))
            r.remove(rpr)
            r.insert(0, new)
        ppr = get_ppr(p)
        sp = set_child(ppr, "spacing", PPR_ORDER)
        sp.set(w("line"), str(int(round(float(h.get("line_spacing", 1.0)) * 240))))
        sp.set(w("lineRule"), "auto")

    # ------------------------------------------------------------ 段落/表格/图 构造
    def _ppr(self, spec, jc=None, keep_next=False):
        sz = size_pt(spec) or 12
        return make_ppr(jc=jc or (spec.get("align") if spec.get("align") in ("center", "left", "right", "both") else None),
                        first_line_chars=spec.get("first_line_chars"), line_spacing=spec.get("line_spacing"),
                        line_rule=spec.get("line_rule", "auto"), keep_next=keep_next, size_pt=sz)

    def para(self, runs, spec_name, alias=None, bold=None, jc=None, keep_next=False):
        spec = self.prof[spec_name]
        ppr = self._ppr(spec, jc, keep_next)
        rr = self.build_runs(runs, None, (), spec.get("font"), spec.get("latin_font"), size_pt(spec),
                             spec.get("bold") if bold is None else bold, alias=alias)
        return make_par(rr, ppr)

    def caption(self, kind, title):
        spec = self.prof["caption"]
        p = self.para([["%s?%s%s" % (spec["table_prefix"] if kind == "表" else spec["figure_prefix"], spec.get("sep", "  "),
                                     title), ""]], "caption", jc=spec.get("align", "center"), keep_next=(kind == "表"))
        self.captions[p] = (kind, title)
        return p

    def table_from_grid(self, rows, hdr_rows, ncols, alias=None):
        """rows: [[{c, cs, rs, runs} 或 None(被纵向合并覆盖)]...]"""
        spec = self.prof["table"]
        sz = size_pt(spec) or 10.5
        widths = [0] * ncols
        for row in rows:
            for cell in row:
                if cell and cell.get("cs", 1) == 1:
                    txt = "".join(r[0] for r in cell["runs"])
                    wdt = sum(2 if ord(ch) > 0x2E80 else 1 for ch in txt.split("\n")[0])
                    widths[cell["c"]] = max(widths[cell["c"]], min(wdt, 36))
        widths = [max(4, x) for x in widths]
        total = self.text_width()
        # “根据内容调整”：列宽≈内容宽度（半角字符宽≈字号/2）+ 单元格边距；超出版心时按比例压缩
        half = sz * 10                                         # 半角字符宽度（twip）
        cols = [int(x * half + 260) for x in widths]
        if not spec.get("autofit", True) or sum(cols) > total:
            tw = sum(widths)
            cols = [int(total * x / tw) for x in widths]
        tbl = ET.Element(W_TBL)
        tblpr = ET.SubElement(tbl, w("tblPr"))
        set_child(tblpr, "tblW", TBLPR_ORDER, {"w": "0", "type": "auto"})
        set_child(tblpr, "jc", TBLPR_ORDER, {"val": "center"})
        b = set_child(tblpr, "tblBorders", TBLPR_ORDER)
        for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
            ET.SubElement(b, w(side), {w("val"): "single", w("sz"): "4", w("space"): "0", w("color"): "000000"})
        set_child(tblpr, "tblLayout", TBLPR_ORDER, {"type": "autofit" if spec.get("autofit", True) else "fixed"})
        set_child(tblpr, "tblLook", TBLPR_ORDER, {"val": "04A0", "firstRow": "1", "lastRow": "0", "firstColumn": "1",
                                                  "lastColumn": "0", "noHBand": "0", "noVBand": "1"})
        grid = ET.SubElement(tbl, w("tblGrid"))
        for x in cols:
            ET.SubElement(grid, w("gridCol"), {w("w"): str(x)})
        cover = {}
        for r, row in enumerate(rows):
            for cell in row:
                if cell and cell.get("rs", 1) > 1:
                    for k in range(1, cell["rs"]):
                        cover[(r + k, cell["c"])] = cell
        for r, row in enumerate(rows):
            tr = ET.SubElement(tbl, W_TR)
            trpr = ET.SubElement(tr, w("trPr"))
            set_child(trpr, "cantSplit", TRPR_ORDER)
            if r < hdr_rows and spec.get("repeat_header", True):
                set_child(trpr, "tblHeader", TRPR_ORDER)
            starts = {cell["c"]: cell for cell in row if cell}
            c = 0
            while c < ncols:
                if c in starts:
                    cell, vm = starts[c], ("restart" if starts[c].get("rs", 1) > 1 else None)
                elif (r, c) in cover:
                    cell, vm = cover[(r, c)], "continue"
                else:
                    cell, vm = {"c": c, "cs": 1, "runs": []}, None
                cs = cell.get("cs", 1)
                tc = ET.SubElement(tr, W_TC)
                tcpr = ET.SubElement(tc, w("tcPr"))
                set_child(tcpr, "tcW", TCPR_ORDER, {"w": str(sum(cols[c:c + cs])), "type": "dxa"})
                if cs > 1:
                    set_child(tcpr, "gridSpan", TCPR_ORDER, {"val": str(cs)})
                if vm == "restart":
                    set_child(tcpr, "vMerge", TCPR_ORDER, {"val": "restart"})
                elif vm == "continue":
                    set_child(tcpr, "vMerge", TCPR_ORDER)
                set_child(tcpr, "vAlign", TCPR_ORDER, {"val": spec.get("valign", "center")})
                runs = cell["runs"] if vm != "continue" else []
                lines = [[]]
                for t_, f_ in runs:
                    parts = t_.split("\n")
                    for k, part in enumerate(parts):
                        if k:
                            lines.append([])
                        if part or f_.startswith("@"):
                            lines[-1].append([part, f_])
                for ln in lines:
                    ppr = make_ppr(jc=spec.get("align", "center"), line_spacing=spec.get("line_spacing", 1.0),
                                   first_line_chars=0, size_pt=sz)
                    rr = self.build_runs(ln, None, ("b",) if (r < hdr_rows and spec.get("header_bold", True)) else (),
                                         spec.get("font"), spec.get("latin_font"), sz, None, alias=alias)
                    tc.append(make_par(rr, ppr))
                c += cs
        return tbl

    def text_width(self):
        sp = self.body.find(w("sectPr"))
        try:
            pg = sp.find(w("pgSz"))
            mar = sp.find(w("pgMar"))
            return int(pg.get(w("w"))) - int(mar.get(w("left"))) - int(mar.get(w("right")))
        except Exception:
            return 8300

    def source_table(self, alias, tid):
        md = self.job.model(alias)
        ids = [tid] + [b["id"] for b in md.m["blocks"] if b.get("cont") == tid]
        rows, hdr, ncols = [], md.by[tid].get("hdr", 1), md.by[tid]["ncols"]
        foot = []
        for k, x in enumerate(ids):
            t = md.by[x]
            start = 0 if k == 0 else t.get("hdr", 1)
            byr = {}
            for cell in t["cells"]:
                byr.setdefault(cell["r"], []).append(cell)
            for r in range(start, t["nrows"]):
                if r == t.get("foot_row"):
                    continue
                row = []
                for cell in sorted(byr.get(r, []), key=lambda c: c["c"]):
                    row.append({"c": cell["c"], "cs": cell.get("cs", 1),
                                "rs": min(cell.get("rs", 1), t["nrows"] - r - (1 if t.get("foot_row") else 0)) or 1,
                                "runs": [list(x) for x in (cell.get("runs") or [[cell["text"], ""]])]})
                rows.append(row)
            foot += t.get("foot", []) or []
        return rows, hdr, ncols, foot

    def image_run(self, alias, iid, inline=False, max_cm=None):
        if alias is None:
            return None
        md = self.job.model(alias).m
        info = (md.get("images") or {}).get(iid)
        if not info:
            return None
        fp = info.get("file")
        data = None
        if fp and os.path.exists(fp):
            data = open(fp, "rb").read()
        elif info.get("part"):
            try:
                data = Package(md["work"]).parts.get(info["part"])
            except Exception:
                data = None
        if not data:
            return None
        ext = (info.get("ext") or "png").lower().replace("jpg", "jpeg")
        self.n_img += 1
        name = "word/media/di_img%d.%s" % (self.n_img, ext)
        while name in self.pkg.parts:
            self.n_img += 1
            name = "word/media/di_img%d.%s" % (self.n_img, ext)
        self.pkg.add_part(name, data)
        self.pkg.ensure_default(ext, IMAGE_CT.get(ext, "image/" + ext))
        rid = self.pkg.rels("word/document.xml").add(RT_IMAGE, name.replace("word/", ""))
        cx, cy = info.get("cx"), info.get("cy")
        if not cx or not cy:
            cx, cy = 5 * EMU_PER_CM, 3 * EMU_PER_CM
        mx = (max_cm or self.prof["figure"].get("max_width_cm", 15)) * EMU_PER_CM
        if cx > mx:
            cy, cx = int(cy * mx / cx), int(mx)
        pid = self.docpr
        self.docpr += 1
        xml = ('<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="%d" cy="%d"/>'
               '<wp:docPr id="%d" name="图片 %d"/><wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/>'
               '</wp:cNvGraphicFramePr><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
               '<pic:pic><pic:nvPicPr><pic:cNvPr id="0" name="di_img%d.%s"/><pic:cNvPicPr/></pic:nvPicPr>'
               '<pic:blipFill><a:blip r:embed="%s"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
               '<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="%d" cy="%d"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/>'
               '</a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>') % (
            cx, cy, pid, pid, self.n_img, ext, rid, cx, cy)
        return parse_xml_fragment(xml)[0]

    # ------------------------------------------------------------ 章节插入
    def insert_section(self, op):
        anchor = self.el.get(op["anchor"])
        if anchor is None:
            self.warnings.append("找不到插入位置 %s" % op["anchor"])
            return
        par = self.parent[anchor]
        for bid in op.get("clear", []):
            p = self.el.get(bid)
            if p is None or p.tag != W_P:
                continue
            txt = "".join(t.text or "" for t in p.iter(W_T)).strip()
            if txt or any(ch.tag in (w("commentRangeStart"), w("commentRangeEnd"), w("bookmarkStart"), w("bookmarkEnd"))
                          for ch in p.iter()) or p.find(".//" + w("drawing")) is not None or p.find(".//" + w("sectPr")) is not None:
                continue
            if self.parent.get(p) is par:
                par.remove(p)
        pos = list(par).index(anchor) + 1
        new = []
        sub_n = 0
        comments = []
        for it in op["items"]:
            first, last = None, None
            for g in it["group"]:
                els = []
                alias = (it.get("src") or {}).get("alias") or g.get("alias")
                if g["type"] == "subheading":
                    sub_n += 1
                    fmt = self.prof["subheading"].get("numbering", "（{n}）")
                    els.append(self.para([[fmt.format(n=sub_n) + g["text"], ""]], "subheading", keep_next=True))
                elif g["type"] == "para":
                    bold = True if g.get("lvl") is not None else None
                    spec = "body"
                    els.append(self.para(g["runs"], spec, alias=alias, bold=bold))
                elif g["type"] == "table":
                    rows, hdr, ncols, foot = self.source_table(g["alias"], g["table"])
                    cap = self.caption("表", g.get("caption") or (it.get("src") or {}).get("title", ""))
                    els += [cap, self.table_from_grid(rows, hdr, ncols, alias=g["alias"])]
                    for f in foot:
                        els.append(self.para([["注：" + f.replace("\n", "；"), ""]], "note"))
                elif g["type"] == "kvtable":
                    rows = [[{"c": i, "cs": 1, "runs": [[h, ""]]} for i, h in enumerate(g["header"])]]
                    for kv in g["rows"]:
                        rows.append([{"c": i, "cs": 1, "runs": [[v, ""]]} for i, v in enumerate(kv)])
                    els += [self.caption("表", g.get("caption", "")), self.table_from_grid(rows, 1, len(g["header"]))]
                elif g["type"] == "figure":
                    md = self.job.model(g["alias"])
                    f = md.by.get(g["fig"])
                    runs = [self.image_run(g["alias"], i) for i in (f.get("imgs") if f else [])]
                    runs = [r for r in runs if r is not None]
                    pp = make_par(runs, make_ppr(jc=self.prof["figure"].get("align", "center"), first_line_chars=0,
                                                 line_spacing=1.0, keep_next=True))
                    els += [pp, self.caption("图", g.get("caption") or "")]
                elif g["type"] == "note":
                    els.append(self.para([[g["text"], ""]], "note"))
                for e in els:
                    par.insert(pos, e)
                    self.parent[e] = par
                    pos += 1
                    new.append(e)
                    if e.tag == W_P:
                        first = first if first is not None else e
                        last = e
                    elif first is None:
                        pass
            if first is not None:
                comments.append((first, last, it))
        # 章节末尾空一行
        nxt = par[pos] if pos < len(par) else None
        if nxt is not None and nxt.tag == W_P and "".join(t.text or "" for t in nxt.iter(W_T)).strip():
            sp = make_par([], None)
            par.insert(pos, sp)
        for first, last, it in comments:
            src = it.get("src") or {}
            links = []
            if src.get("alias"):
                ref = src.get("sec") or src.get("start") or (src.get("range") or [None])[0]
                if ref:
                    links.append({"label": "打开原文", "alias": src["alias"], "ref": ref})
                md = self.job.model(src["alias"]).m
                text = "【摘自】%s ›%s" % (md["name"], short(src.get("title", ""), 30))
                if it.get("warn"):
                    text = "⚠" + text + "；" + it["warn"]
            else:
                text = "⚠【待补充】%s" % (it.get("warn") or "源内容缺失")
            self.add_comment(first, last, text, links, warn=bool(it.get("warn")))

    # ------------------------------------------------------------ 批注
    def comments_root(self):
        if not hasattr(self, "_croot"):
            if self.pkg.has("word/comments.xml"):
                self._croot = self.pkg.xml("word/comments.xml")
            else:
                root = ET.Element(w("comments"))
                self.pkg.new_xml("word/comments.xml", root, [("w", NS["w"]), ("r", NS["r"])])
                self.pkg.rels("word/document.xml").add(RT_COMMENTS, "comments.xml")
                self.pkg.ensure_override("word/comments.xml", CT_COMMENTS)
                self._croot = root
            ids = [int(c.get(w("id"))) for c in self._croot.findall(w("comment")) if (c.get(w("id")) or "").lstrip("-").isdigit()]
            ids += [int(c.get(w("id"))) for c in self.doc.iter(w("commentRangeStart")) if (c.get(w("id")) or "").isdigit()]
            self._cid = max(ids + [-1]) + 1
        return self._croot

    def add_comment(self, p_start, p_end, text, links, warn=False):
        root = self.comments_root()
        cid = self._cid
        self._cid += 1
        now = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        c = ET.SubElement(root, w("comment"), {w("id"): str(cid), w("author"): TOOL_AUTHOR, w("date"): now,
                                              w("initials"): TOOL_INITIALS})
        lines = text.split("\n")
        for k, ln in enumerate(lines):
            p = ET.SubElement(c, W_P)
            if k == 0:
                r = ET.SubElement(p, W_R)
                ET.SubElement(r, w("annotationRef"))
            rpr = make_rpr(None, (), color="C00000" if (warn and k == 0) else None)
            p.append(make_run(ln, rpr))
        if links:
            p = ET.SubElement(c, W_P)
            crels = self.pkg.rels("word/comments.xml")
            for k, ln in enumerate(links):
                rel, anchor, tip = self.snap.link(ln["alias"], ln["ref"])
                rid = crels.add(RT_HYPERLINK, link_target(rel), external=True)
                if k:
                    p.append(make_run("  ", None))
                h = ET.SubElement(p, w("hyperlink"), {qn("r:id"): rid, w("history"): "1"})
                if anchor:
                    h.set(w("anchor"), anchor)
                h.set(w("tooltip"), tip[:250])
                h.append(make_run("▶" + ln["label"], make_rpr(None, ("u",), color="0563C1")))
        pos = 1 if len(p_start) and p_start[0].tag == W_PPR else 0
        p_start.insert(pos, ET.Element(w("commentRangeStart"), {w("id"): str(cid)}))
        p_end.append(ET.Element(w("commentRangeEnd"), {w("id"): str(cid)}))
        rr = ET.SubElement(p_end, W_R)
        ET.SubElement(rr, w("commentReference"), {w("id"): str(cid)})
        return str(cid)

    def prepare_word_questions(self, questions):
        """Register the actual choices for this draft in the job, outside the editable document."""
        self.word_questions = []
        for q in questions:
            if not q.get("defaulted") or q.get("multi") or not q["key"].startswith(("src:verify:", "src:spec:", "td:samplemap")):
                continue
            options = q["options"]
            positive = [i for i, option in enumerate(options) if option["effect"].get("ok") or
                        (q["key"].startswith("td:samplemap") and i and option["effect"].get("samplemap"))]
            bare = positive[0] if len(positive) == 1 else None
            lines = ["【Word 核对 %s】%s" % (q["id"], q["question"])]
            lines.extend("%d. %s" % (i, option["label"]) for i, option in enumerate(options, 1))
            lines.append("审核人请直接回复“确认 N”，N 为选项序号。")
            if bare is not None:
                lines.append("单独回复“确认”表示选择第 %d 项。" % (bare + 1))
            self.word_questions.append({"id": q["id"], "key": q["key"], "text": "\n".join(lines),
                                        "options": options, "bare": bare, "input_fp": q["input_fp"],
                                        "applied": options[q.get("recommended", 0)]["effect"]})

    def comment_op(self, op):
        anc = op["anchor"]
        try:
            p0 = self.first_para(anc)
            p1 = p0 if re.match(r"^T\d+\.R\d+\.C\d+$", anc) else self.last_para(anc)
        except (KeyError, IndexError):
            self.warnings.append("批注锚点不存在 %s" % anc)
            return
        key = id(p0)
        if key in self.comment_buf:
            buf = self.comment_buf[key]
            buf["text"] += "\n" + op["text"]
            buf["links"] += op.get("links", [])
            buf["warn"] = buf["warn"] or op.get("warn", False)
        else:
            self.comment_buf[key] = {"p0": p0, "p1": p1, "text": op["text"], "links": list(op.get("links", [])),
                                     "warn": op.get("warn", False)}

    # ------------------------------------------------------------ 题注编号
    def number_captions(self):
        cnt = {"表": 0, "图": 0}
        for p in self.body.iter(W_P):
            if p in self.captions:
                kind, title = self.captions[p]
                cnt[kind] += 1
                spec = self.prof["caption"]
                txt = ("%s%d%s%s" % (spec["table_prefix"] if kind == "表" else spec["figure_prefix"], cnt[kind],
                                     spec.get("sep", "  "), title)) if spec.get("numbering", True) else title
                ts = list(p.iter(W_T))
                if ts:
                    ts[0].text = txt
                    ts[0].set("{%s}space" % XML_NS, "preserve")
                    for t in ts[1:]:
                        t.text = ""
            else:
                t = "".join(x.text or "" for x in p.iter(W_T)).strip()
                m = re.match(r"^(表|图)\s*(\d+)(?![.\-\d])", t)
                if m and len(t) < 60:
                    cnt[m.group(1)] = max(cnt[m.group(1)], int(m.group(2)))

    # ------------------------------------------------------------ 执行
    def apply(self, ops, tmp_dir):
        struct = [o for o in ops if o["op"] == "add_rows"]
        if struct:
            for o in struct:
                self.add_rows(o)
            self.reload_after_structure(os.path.join(tmp_dir, "_struct.docx"))
        order = {"set_cell": 1, "append_runs": 1, "fill_blanks": 1, "set_text": 1, "format_heading": 2,
                 "insert_section": 3, "comment": 4}
        for o in sorted([o for o in ops if o["op"] != "add_rows"], key=lambda o: order.get(o["op"], 5)):
            fn = getattr(self, o["op"] if o["op"] != "comment" else "comment_op", None)
            if fn is None:
                self.warnings.append("未知操作 %s" % o["op"])
                continue
            try:
                fn(o)
            except Exception as e:  # 单个操作失败不影响整体
                self.warnings.append("操作 %s(%s) 失败：%s" % (o["op"], o.get("cell") or o.get("anchor") or "", e))
        for buf in self.comment_buf.values():
            self.add_comment(buf["p0"], buf["p1"], buf["text"], buf["links"], buf["warn"])
        if self.word_questions:
            anchor = next(self.body.iter(W_P), None)
            if anchor is None:
                self.warnings.append("草稿没有可放置 Word 核对批注的段落，请通过 answer 回答。")
            else:
                for q in self.word_questions:
                    self.add_comment(anchor, anchor, q["text"], [], warn=True)
                self.job.data.setdefault("word_questions", {})[os.path.basename(self.draft_path)] = self.word_questions
        self.number_captions()
        self.snap.build()
        self.pkg.save(self.draft_path)
        return self.warnings
