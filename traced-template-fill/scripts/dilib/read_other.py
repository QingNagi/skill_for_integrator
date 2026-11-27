# -*- coding: utf-8 -*-
"""xlsx / xls / csv / md / txt / pdf -> 内容模型。"""
from __future__ import annotations

import csv
import datetime
import io
import os
import re
import zipfile
from decimal import Decimal, InvalidOperation

from .safe_xml import parse_xml

from .common import DIError, short
from .text_patterns import CAPTION_RE, HEAD_PATTERNS

S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
RNS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


# ======================================================================= Excel
def col_letter(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def col_index(letters):
    n = 0
    for ch in letters.upper():
        n = n * 26 + ord(ch) - 64
    return n - 1


def split_ref(ref):
    m = re.match(r"^\$?([A-Za-z]+)\$?(\d+)$", ref)
    if not m:
        raise DIError("无效单元格引用：%s" % ref)
    return int(m.group(2)) - 1, col_index(m.group(1))


_DATE_FMT_IDS = set(range(14, 23)) | {45, 46, 47, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 50, 51, 52, 53, 54, 55,
                                       56, 57, 58}


def _excel_date(v):
    try:
        base = datetime.datetime(1899, 12, 30)
        d = base + datetime.timedelta(days=float(v))
        if d.hour == 0 and d.minute == 0 and d.second == 0:
            return d.strftime("%Y-%m-%d")
        return d.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return v


def read_xlsx(path):
    with zipfile.ZipFile(path) as z:
        return _read_xlsx(path, z)


def measurement_value(sheet, row, col):
    """Get the unrounded numeric spelling for a measurement; other users keep typed sheet cells."""
    values = sheet["rows"][row]
    value = values[col] if col < len(values) else None
    return sheet.get("numeric_text", {}).get(col_letter(col) + str(row + 1), value)


def _read_xlsx(path, z):
    names = set(z.namelist())
    ss = []
    if "xl/sharedStrings.xml" in names:
        for si in parse_xml(z.read("xl/sharedStrings.xml")).findall(S + "si"):
            ss.append("".join(t.text or "" for t in si.iter(S + "t") if not _in_rph(si, t)))
    date_styles = set()
    decimal_styles = {}
    if "xl/styles.xml" in names:
        st = parse_xml(z.read("xl/styles.xml"))
        custom = {}
        fixed = {2: 2, 4: 2}
        nf = st.find(S + "numFmts")
        if nf is not None:
            for f in nf.findall(S + "numFmt"):
                code = (f.get("formatCode") or "").lower()
                places = re.fullmatch(r"(?:#,##)?0\.(0{1,28})", code)
                if places:
                    fixed[int(f.get("numFmtId"))] = len(places[1])
                code = re.sub(r'"[^"]*"|\[[^\]]*\]', "", code)
                if re.search(r"[ymdhs]", code) and not re.search(r"[0#]", code.replace("0.0", "")):
                    custom[int(f.get("numFmtId"))] = True
        xfs = st.find(S + "cellXfs")
        if xfs is not None:
            for i, xf in enumerate(xfs.findall(S + "xf")):
                fid = int(xf.get("numFmtId", "0"))
                if fid in _DATE_FMT_IDS or fid in custom:
                    date_styles.add(i)
                elif fid in fixed:
                    decimal_styles[i] = fixed[fid]
    wb = parse_xml(z.read("xl/workbook.xml"))
    rels = parse_xml(z.read("xl/_rels/workbook.xml.rels"))
    rmap = {r.get("Id"): r.get("Target") for r in rels}
    sheets = []
    for sh in wb.find(S + "sheets"):
        name = sh.get("name")
        tgt = rmap.get(sh.get(RNS + "id"), "")
        part = tgt[1:] if tgt.startswith("/") else "xl/" + tgt
        if part not in names:
            continue
        x = parse_xml(z.read(part))
        cells = {}
        numeric_text = {}
        maxr = maxc = -1
        for row in x.iter(S + "row"):
            for c in row.findall(S + "c"):
                ref = c.get("r")
                r_, c_ = split_ref(ref)
                t = c.get("t")
                v = c.find(S + "v")
                val = v.text if v is not None else None
                if t == "s" and val is not None:
                    val = ss[int(val)]
                elif t == "inlineStr":
                    val = "".join(tt.text or "" for tt in c.iter(S + "t"))
                elif t == "b":
                    val = "TRUE" if val == "1" else "FALSE"
                elif t in (None, "n") and val is not None:
                    style = int(c.get("s", "0"))
                    if style in date_styles:
                        val = _excel_date(val)
                    else:
                        try:
                            original = val
                            fv = float(val)
                            val = int(fv) if fv.is_integer() and "e" not in val.lower() and "." not in val else fv
                            if style in decimal_styles:
                                d = Decimal(original)
                                places = decimal_styles[style]
                                if d.is_finite() and d.adjusted() <= 308 and d.as_tuple().exponent > -places:
                                    original = format(d, ".%df" % places)
                            if original != str(val):
                                numeric_text[ref] = original
                        except (ValueError, InvalidOperation):
                            pass
                if val is None or val == "":
                    continue
                cells[(r_, c_)] = val
                maxr, maxc = max(maxr, r_), max(maxc, c_)
        merges = []
        mc = x.find(S + "mergeCells")
        if mc is not None:
            merges = [m.get("ref") for m in mc]
        rows = [[cells.get((r, c)) for c in range(maxc + 1)] for r in range(maxr + 1)]
        sheets.append({"name": name, "nrows": maxr + 1, "ncols": maxc + 1, "rows": rows, "merges": merges,
                       "numeric_text": numeric_text, "charts": _xlsx_charts(z, part, names)})
    return {"type": "xlsx", "title": os.path.splitext(os.path.basename(path))[0], "sheets": sheets}


def _in_rph(si, t):
    return False


def _xlsx_charts(z, sheet_part, names):
    out = []
    rels_name = os.path.dirname(sheet_part) + "/_rels/" + os.path.basename(sheet_part) + ".rels"
    if rels_name not in names:
        return out
    for r in parse_xml(z.read(rels_name)):
        if r.get("Type", "").endswith("/drawing"):
            dpart = os.path.normpath(os.path.join(os.path.dirname(sheet_part), r.get("Target"))).replace("\\", "/")
            drels = os.path.dirname(dpart) + "/_rels/" + os.path.basename(dpart) + ".rels"
            if drels not in names:
                continue
            for dr in parse_xml(z.read(drels)):
                if dr.get("Type", "").endswith("/chart"):
                    cpart = os.path.normpath(os.path.join(os.path.dirname(dpart), dr.get("Target"))).replace("\\", "/")
                    if cpart in names:
                        out.append(_chart_xml(z.read(cpart)))
    return out


def _chart_xml(data):
    C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
    A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    root = parse_xml(data)

    def vals(el):
        if el is None:
            return []
        pts = [(int(p.get("idx", "0")), (p.find(C + "v").text if p.find(C + "v") is not None else ""))
               for p in el.iter(C + "pt")]
        return [v for _, v in sorted(pts)]
    title = "".join(t.text or "" for t in root.iter(A + "t")) if root.find(".//" + C + "title") is not None else ""
    ser = [{"name": (vals(s.find(C + "tx")) or [""])[0], "cats": vals(s.find(C + "cat")) or vals(s.find(C + "xVal")),
            "vals": vals(s.find(C + "val")) or vals(s.find(C + "yVal"))} for s in root.iter(C + "ser")]
    return {"title": title, "series": ser}


def read_xls(path):
    """旧版 .xls：优先 xlrd，否则由调用方先转换为 xlsx。"""
    import xlrd  # noqa
    book = xlrd.open_workbook(path)
    sheets = []
    for sh in book.sheets():
        rows = []
        for r in range(sh.nrows):
            row = []
            for c in range(sh.ncols):
                cell = sh.cell(r, c)
                v = cell.value
                if cell.ctype == xlrd.XL_CELL_DATE:
                    v = _excel_date(v)
                elif cell.ctype == xlrd.XL_CELL_NUMBER and float(v).is_integer():
                    v = int(v)
                elif v == "":
                    v = None
                row.append(v)
            rows.append(row)
        merges = ["%s%d:%s%d" % (col_letter(c0), r0 + 1, col_letter(c1 - 1), r1) for r0, r1, c0, c1 in sh.merged_cells]
        sheets.append({"name": sh.name, "nrows": sh.nrows, "ncols": sh.ncols, "rows": rows, "merges": merges,
                       "charts": []})
    return {"type": "xlsx", "title": os.path.splitext(os.path.basename(path))[0], "sheets": sheets}


def read_csv(path):
    with open(path, "rb") as stream:
        raw = stream.read()
    for enc in ("utf-8-sig", "gb18030", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    dialect = csv.excel
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        pass
    rows = []
    numeric_text = {}
    for row in csv.reader(io.StringIO(text), dialect):
        out = []
        for v in row:
            v = v.strip()
            if v == "":
                out.append(None)
                continue
            try:
                fv = float(v)
                value = int(fv) if fv.is_integer() and re.match(r"^-?\d+$", v) else fv
                if str(value) != v:
                    numeric_text[col_letter(len(out)) + str(len(rows) + 1)] = v
                out.append(value)
            except ValueError:
                out.append(v)
        rows.append(out)
    ncols = max((len(r) for r in rows), default=0)
    rows = [r + [None] * (ncols - len(r)) for r in rows]
    return {"type": "xlsx", "title": os.path.splitext(os.path.basename(path))[0],
            "sheets": [{"name": "Sheet1", "nrows": len(rows), "ncols": ncols, "rows": rows, "merges": [], "charts": [],
                        "numeric_text": numeric_text}]}


# ======================================================================= 文本类：md / txt
def _sections_from_blocks(blocks, heading_rank):
    secs, stack = [], []
    for b in blocks:
        rank = heading_rank(b)
        if rank is not None:
            while stack and stack[-1]["rank"] >= rank:
                stack.pop()
            s = {"id": "H%d" % len(secs), "title": b["text"].strip(), "rank": rank, "start": b["id"], "blocks": [],
                 "parent": stack[-1]["id"] if stack else None, "in": "body", "depth": len(stack)}
            for p in stack:
                p["blocks"].append(b["id"])
            secs.append(s)
            stack.append(s)
        else:
            for p in stack:
                p["blocks"].append(b["id"])
    by = {b["id"]: b for b in blocks}
    for s in secs:
        s["size"] = {"chars": sum(len(by[x].get("text", "")) for x in s["blocks"] if by[x]["t"] == "p"),
                     "tables": sum(1 for x in s["blocks"] if by[x]["t"] == "tbl"),
                     "figs": sum(1 for x in s["blocks"] if by[x].get("fig"))}
    return secs


def _pseudo_rank(text):
    t = text.strip()
    if not t or len(t) > 60 or re.search(r"[。；;，,]$", t):
        return None
    for rank, pat in HEAD_PATTERNS:
        if pat.match(t) and re.search(r"[A-Za-z一-鿿]", pat.sub("", t, count=1)):
            return 100 + rank
    return None


def read_markdown(path, text=None):
    if text is None:
        with open(path, "rb") as stream:
            raw = stream.read()
        text = raw.decode("utf-8-sig", "replace")
    lines = text.splitlines()
    blocks, figures = [], []
    np_ = nt = nf = 0
    i = 0
    para, pstart = [], None

    def flush():
        nonlocal np_, para, pstart
        if para:
            blocks.append({"id": "P%d" % np_, "t": "p", "text": " ".join(x.strip() for x in para), "line": pstart + 1})
            np_ += 1
        para, pstart = [], None

    in_code = False
    while i < len(lines):
        ln = lines[i]
        if ln.strip().startswith("```"):
            flush()
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith("```"):
                j += 1
            blocks.append({"id": "P%d" % np_, "t": "p", "text": "\n".join(lines[i + 1:j]), "line": i + 1, "code": True})
            np_ += 1
            i = j + 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", ln)
        if m:
            flush()
            blocks.append({"id": "P%d" % np_, "t": "p", "text": m.group(2), "lvl": len(m.group(1)) - 1, "line": i + 1})
            np_ += 1
            i += 1
            continue
        if ln.strip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
            flush()
            rows = []
            j = i
            while j < len(lines) and lines[j].strip().startswith("|"):
                if not re.match(r"^\s*\|?\s*:?-{2,}", lines[j]):
                    cells = [c.strip() for c in lines[j].strip().strip("|").split("|")]
                    rows.append(cells)
                j += 1
            ncols = max(len(r) for r in rows)
            tb = {"id": "T%d" % nt, "t": "tbl", "nrows": len(rows), "ncols": ncols, "hdr": 1, "line": i + 1,
                  "cells": [{"r": r, "c": c, "text": (rows[r][c] if c < len(rows[r]) else "")}
                            for r in range(len(rows)) for c in range(ncols)]}
            # 表题：前一个非空段落形如“表N ...”
            for pb in reversed(blocks[-2:]):
                if pb["t"] == "p" and CAPTION_RE.match(pb["text"]) and pb["text"].strip().startswith(("表", "Table")):
                    tb["cap"], tb["cap_id"] = pb["text"].strip(), pb["id"]
                    pb["cap"] = "表"
                    break
            blocks.append(tb)
            nt += 1
            i = j
            continue
        im = re.match(r"^\s*!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"([^\"]*)\")?\)\s*$", ln)
        if im:
            flush()
            src = im.group(2)
            full = src if os.path.isabs(src) or re.match(r"^\w+://", src) else \
                os.path.normpath(os.path.join(os.path.dirname(path or "."), src))
            bid = "P%d" % np_
            np_ += 1
            iid = "I%d" % nf
            cap = im.group(3) or im.group(1) or ""
            # 下一行是“图N ...”则作为题注
            if i + 1 < len(lines) and CAPTION_RE.match(lines[i + 1].strip() or "x") and lines[i + 1].strip().startswith(("图", "Fig")):
                cap = lines[i + 1].strip()
                i += 1
            blocks.append({"id": bid, "t": "p", "text": "", "imgs": [iid], "line": i + 1, "fig": "F%d" % nf,
                           "runs": [["", "@" + iid]]})
            figures.append({"id": "F%d" % nf, "para": bid, "imgs": [iid], "cap": cap,
                            "img_files": {iid: full}})
            nf += 1
            i += 1
            continue
        if not ln.strip():
            flush()
            i += 1
            continue
        lm = re.match(r"^\s*([-*+•·])\s+(.*)$", ln) or re.match(r"^\s*(\d+[.)])\s+(.*)$", ln)
        cm = None if lm else re.match(r"^\s*(\d+[）、]|[（(]\d+[)）]|[a-z][)）])\s*([^\d\s].*)$", ln)
        if lm:
            flush()
            lab = lm.group(1)
            blocks.append({"id": "P%d" % np_, "t": "p", "text": lm.group(2), "label": "•" if lab in "-*+•·" else lab,
                           "line": i + 1})
            np_ += 1
            i += 1
            continue
        if cm and not re.match(r"^\s*[（(]\d+[)）]\s*\S{1,30}$", ln):     # 中文编号列表“1）…”“a）…”逐行成段
            flush()
            blocks.append({"id": "P%d" % np_, "t": "p", "text": ln.strip(), "line": i + 1})
            np_ += 1
            i += 1
            continue
        if pstart is None:
            pstart = i
        para.append(ln)
        i += 1
    flush()
    # 清理行内 markdown 标记（保留文字）
    for b in blocks:
        if b["t"] == "p" and not b.get("code"):
            b["text"] = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), b["text"])
            b["text"] = re.sub(r"`([^`]+)`", r"\1", b["text"])
            b["text"] = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", b["text"])
            if CAPTION_RE.match(b["text"]) and len(b["text"]) < 60 and not b.get("cap"):
                b["cap"] = "表" if b["text"].lstrip().startswith(("表", "Table")) else "图"

    def rank(b):
        if b["t"] != "p":
            return None
        if b.get("lvl") is not None:
            return b["lvl"] * 10
        if b.get("cap") or b.get("label"):
            return None
        return _pseudo_rank(b["text"])
    secs = _sections_from_blocks(blocks, rank)
    images = {}
    for f in figures:
        for iid, fp in f.pop("img_files").items():
            ext = os.path.splitext(fp)[1].lstrip(".").lower()
            images[iid] = {"id": iid, "file": fp, "ext": ext}
    title = next((b["text"] for b in blocks if b.get("lvl") == 0), os.path.splitext(os.path.basename(path or "md"))[0])
    return {"type": "md", "title": title, "blocks": blocks, "sections": secs, "figures": figures, "comments": [],
            "images": images, "stats": {"paras": np_, "tables": nt, "figures": nf}}


def read_txt(path):
    with open(path, "rb") as stream:
        raw = stream.read()
    for enc in ("utf-8-sig", "gb18030"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("latin-1")
    m = read_markdown(path, text=text)
    m["type"] = "txt"
    return m


# ======================================================================= PDF
def read_pdf(path, media_dir=None, ocr=None):
    from . import _bootstrap
    from .pdf_reader import read_layout
    _bootstrap.activate_vendor()
    options = ocr if ocr is not None else _bootstrap.ocr_hint()
    try:
        return read_layout(path, media_dir, options)
    except Exception as exc:
        # An unreadable PDF catalog may still be recoverable by the independent reader.
        reason = '%s: %s' % (type(exc).__name__, exc)
    if _bootstrap.use_pypdf() is None:
        raise DIError('无法读取 PDF，未找到 pypdf 降级库：' + reason)
    try:
        model = _pdf_pypdf(path)
    except Exception as exc:
        raise DIError('PDF 读取失败（pdfplumber：%s；pypdf：%s）' % (reason, exc)) from exc
    model['pdf_backend'] = 'pypdf'
    model['backend_note'] = 'pdfplumber 不可用，pypdf 仅恢复文本：' + reason
    return model


def _pdf_common_post(blocks, figures, images, title, path):
    sizes = sorted(b.get("size", 0) for b in blocks if b["t"] == "p" and b.get("size"))
    body = sizes[len(sizes) // 2] if sizes else 10.5

    def rank(b):
        if b["t"] != "p" or b.get("cap"):
            return None
        t = b["text"].strip()
        r = _pseudo_rank(t)
        if b.get("page", 1) > 1 and b.get("size", 0) >= body * 1.25 and len(t) <= 40:   # 封面大字不作章节
            return int(max(0, 40 - b["size"])) if r is None else min(r, int(max(0, 40 - b["size"])))
        if r is not None and (b.get("bold") or b.get("size", 0) > body * 1.05):
            return r
        return r if r is not None and len(t) <= 30 else None
    if not title:
        cover = [b for b in blocks if b["t"] == "p" and b.get("page") == 1 and b.get("size", 0) >= body * 1.25
                 and not re.search(r"有限公司|股份|集团|研究所|研究院|大学|发布|实施|企业.{0,4}标准|\d{4}\s*[-年.]", b["text"])]
        title = " ".join(b["text"].strip() for b in cover[:3])      # 保持阅读顺序
    for b in blocks:
        if b["t"] == "p" and CAPTION_RE.match(b["text"]) and len(b["text"]) <= 60:
            b["cap"] = "表" if b["text"].lstrip().startswith(("表", "Table", "附表")) else "图"
    # 表题与图题；表头相同且无独立表题（或表题含“续”）的视为续表
    prev = None
    for i, b in enumerate(blocks):
        if b["t"] == "tbl":
            for j in range(i - 1, max(-1, i - 3), -1):
                if blocks[j]["t"] == "p" and blocks[j].get("cap") == "表":
                    b["cap"], b["cap_id"] = blocks[j]["text"].strip(), blocks[j]["id"]
                    break
            if prev is not None and _hdr_sig(prev) == _hdr_sig(b) and _hdr_sig(b) and \
                    b.get("page", 0) <= prev.get("page", 0) + 1 and (not b.get("cap") or re.search(r"续", b["cap"])):
                b["cont"] = prev.get("cont") or prev["id"]
                b["cap"] = prev.get("cap", b.get("cap", ""))
            prev = b
    for f in figures:
        pi = next((i for i, b in enumerate(blocks) if b["id"] == f["para"]), None)
        if pi is None:
            continue
        for j in list(range(pi + 1, min(len(blocks), pi + 4))) + list(range(pi - 1, max(-1, pi - 3), -1)):
            if blocks[j]["t"] == "p" and blocks[j].get("cap") == "图":
                f["cap"], f["cap_id"] = blocks[j]["text"].strip(), blocks[j]["id"]
                break
    secs = _sections_from_blocks(blocks, rank)
    ocr_blocks = {b['id'] for b in blocks if b.get('ocr')}
    if ocr_blocks:
        # Saved references to native section IDs must still select the same headings.
        native_first = sorted(secs, key=lambda sec: sec['start'] in ocr_blocks)
        ids = {sec['id']: 'H%d' % i for i, sec in enumerate(native_first)}
        for sec in secs:
            sec['id'] = ids[sec['id']]
            sec['parent'] = ids.get(sec['parent'])
    return {"type": "pdf", "title": title or os.path.splitext(os.path.basename(path))[0], "blocks": blocks,
            "sections": secs, "figures": figures, "comments": [], "images": images,
            "stats": {"paras": sum(1 for b in blocks if b["t"] == "p"), "tables": sum(1 for b in blocks if b["t"] == "tbl"),
                      "figures": len(figures), "pages": max([b.get("page", 1) for b in blocks] or [0])}}


def _hdr_sig(tb):
    return tuple(c["text"] for c in tb["cells"] if c["r"] < tb.get("hdr", 1))


def _span_runs(spans):
    """单元格内的文字片段 -> runs：按字号与基线偏移恢复上下标。"""
    if not spans:
        return []
    main = max(s["size"] for s in spans)
    mains = [s for s in spans if s["size"] >= main * 0.85]
    lines = []
    for s in sorted(mains, key=lambda s: (s["origin"][1], s["origin"][0])):
        for ln in lines:
            if abs(ln["y"] - s["origin"][1]) <= main * 0.3:
                ln["spans"].append((s, ""))
                break
        else:
            lines.append({"y": s["origin"][1], "spans": [(s, "")]})
    for s in spans:
        if s["size"] >= main * 0.85:
            continue
        best = None
        for ln in lines:
            d = s["origin"][1] - ln["y"]
            if abs(d) <= main * 0.8 and (best is None or abs(d) < abs(best[1])):
                best = (ln, d)
        if best is None:
            lines.append({"y": s["origin"][1], "spans": [(s, "")]})
            continue
        best[0]["spans"].append((s, "_" if best[1] > main * 0.08 else ("^" if best[1] < -main * 0.08 else "")))
    lines.sort(key=lambda l: l["y"])
    runs = []
    for i, ln in enumerate(lines):
        items = sorted(ln["spans"], key=lambda x: x[0]["origin"][0])
        if i and runs:
            prev = runs[-1][0]
            nxt = items[0][0]["text"].lstrip() if items else ""
            # 换行处：中文/数字被截断时直接拼接，其余（英文单词之间）补空格
            if prev and nxt and not (re.search(r"[一-鿿，、；\-~/]$", prev) or re.match(r"^[一-鿿，、；\-~/]", nxt)
                                     or (prev[-1:].isdigit() and nxt[:1].isdigit())):
                if not prev.endswith(" "):
                    runs.append([" ", ""])
        for sp, fl in items:
            t = sp["text"]
            if fl and t.strip():
                t = t.strip()
            if not t:
                continue
            if runs and runs[-1][1] == fl:
                runs[-1][0] += t
            else:
                runs.append([t, fl])
    runs = [[cjk_space(re.sub(r"(?<=\S) (?=[=，,*])|(?<=[=*]) ", "", t)) if not f else t, f] for t, f in runs if t]
    if runs:
        runs[0][0] = runs[0][0].lstrip()
        runs[-1][0] = runs[-1][0].rstrip()
    return [r for r in runs if r[0]]


def cjk_space(t):
    """去掉 PDF 导出时在中英文之间插入的空格（Word 的“中西文自动间距”）。"""
    return re.sub(r"(?<=[A-Za-z0-9）)]) (?=[一-鿿])|(?<=[一-鿿]) (?=[A-Za-z0-9（(])", "", t)


def _pdf_pypdf(path):
    import pypdf
    with open(path, "rb") as stream:
        return _read_pypdf(pypdf.PdfReader(stream), path)


def _read_pypdf(r, path):
    blocks, np_ = [], 0
    unread = []      # 降级读取：没有文本层或读取失败的页必须登记，不能当作“没有内容”
    for pno, page in enumerate(r.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as e:
            unread.append({"page": pno, "why": "页面读取失败（pypdf 降级）：%s" % e})
            continue
        try:
            has_images = bool(page.images)
        except Exception:
            has_images = True
        if not text.strip() or has_images:
            unread.append({"page": pno, "why": "缺少文本层或存在未识别图像，pypdf 降级读取，未做 OCR"})
        for ln in text.splitlines():
            if ln.strip():
                blocks.append({"id": "P%d" % np_, "t": "p", "text": ln.strip(), "page": pno})
                np_ += 1
    m = _pdf_common_post(blocks, [], {}, "", path)
    m["unread"] = unread
    m["ocr_pages"] = []
    m["stats"]["pages"] = len(r.pages)
    return m
