# -*- coding: utf-8 -*-
"""源文件加载（带缓存与格式转换）、提纲、按 ID 查看、关键词检索。
   设计目标：模型只看提纲与局部片段，不读整份文件 —— 节省 token、避免上下文溢出。"""
from __future__ import annotations

import hashlib
import os
import re

from . import office
from .common import DIError, bigrams, contains_score, ensure_dir, file_sha1, read_json, short, write_json

MODEL_VERSION = 14
DOC_EXT = {".docx", ".docm", ".dotx"}
LEGACY_WORD = {".doc", ".wps", ".wpt", ".rtf", ".dot", ".odt", ".mht"}
SHEET_EXT = {".xlsx", ".xlsm"}
LEGACY_SHEET = {".xls", ".et", ".ett", ".ods", ".xlsb"}


def work_copy(path, cache_dir):
    """返回可直接解析的文件路径（旧格式先转换为 docx/xlsx）。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in LEGACY_WORD:
        return office.convert(path, "docx", cache_dir=ensure_dir(os.path.join(cache_dir, "conv")))
    if ext in LEGACY_SHEET:
        if ext == ".xls":
            try:
                import xlrd  # noqa
                return path
            except ImportError:
                pass
        return office.convert(path, "xlsx", cache_dir=ensure_dir(os.path.join(cache_dir, "conv")))
    return path


def load_model(path, cache_dir):
    """读取任意支持的文件 -> 内容模型（按内容哈希缓存）。"""
    if not os.path.exists(path):
        raise DIError("文件不存在：%s" % path)
    sha = file_sha1(path)
    # Equal bytes in two directories can have different relative image links and provenance.
    location = os.path.normcase(os.path.abspath(path))
    key = sha + "-" + hashlib.sha256(os.fsencode(location)).hexdigest()[:16]
    mpath = os.path.join(ensure_dir(os.path.join(cache_dir, "models")), "%s.json" % key)
    m = read_json(mpath)
    ext = os.path.splitext(path)[1].lower()
    ocr = None
    if ext == ".pdf":
        from . import _bootstrap
        ocr = dict(_bootstrap.ocr_hint(), ocr_cache_dir=os.path.join(cache_dir, "ocr"))
    if m and m.get("_v") == MODEL_VERSION and (not ocr or m.get("_pdf_reader") == ocr["signature"]):
        return m
    media = ensure_dir(os.path.join(cache_dir, "media", key))
    wp = work_copy(path, cache_dir)
    wext = os.path.splitext(wp)[1].lower()
    if wext in DOC_EXT:
        from .read_docx import DocxReader
        r = DocxReader(wp, media)
        m = r.read()
        m["images"] = {v["id"]: {k: v[k] for k in ("id", "part", "ext", "cx", "cy", "file", "text", "kind") if k in v}
                       for v in r.images.values()}
    elif wext in SHEET_EXT:
        from .read_other import read_xlsx
        m = read_xlsx(wp)
    elif wext == ".xls":
        from .read_other import read_xls
        m = read_xls(wp)
    elif wext in (".csv", ".tsv"):
        from .read_other import read_csv
        m = read_csv(wp)
    elif wext in (".md", ".markdown"):
        from .read_other import read_markdown
        m = read_markdown(wp)
    elif wext == ".txt":
        from .read_other import read_txt
        m = read_txt(wp)
    elif wext == ".pdf":
        from .read_other import read_pdf
        m = read_pdf(wp, media, ocr)
        from importlib.metadata import PackageNotFoundError, version
        try:
            m["pdf_backend_version"] = version(m["pdf_backend"])
        except PackageNotFoundError:
            m["pdf_backend_version"] = "版本元数据缺失"
        m["_pdf_reader"] = _bootstrap.parsed_signature(ocr, m.get('pdf_backend'))
    else:
        raise DIError("不支持的文件类型：%s" % ext)
    m["_v"] = MODEL_VERSION
    m["path"] = os.path.abspath(path)
    m["work"] = os.path.abspath(wp)
    m["sha1"] = sha
    m["name"] = os.path.basename(path)
    write_json(mpath, m, compact=True)
    return m


# ======================================================================= 访问
class Model:
    def __init__(self, m, alias):
        self.m = m
        self.alias = alias
        self.by = {}
        for b in m.get("blocks", []):
            self.by[b["id"]] = b
        for s in m.get("sections", []):
            self.by[s["id"]] = s
        for f in m.get("figures", []):
            self.by[f["id"]] = f
        self.cells = {}
        for b in m.get("blocks", []):
            if b["t"] == "tbl":
                for c in b["cells"]:
                    self.cells["%s.R%d.C%d" % (b["id"], c["r"], c["c"])] = c

    @property
    def kind(self):
        return "sheet" if self.m["type"] == "xlsx" else "doc"

    def get(self, bid):
        if bid in self.by:
            return self.by[bid]
        if bid in self.cells:
            return self.cells[bid]
        return None

    def table_grid(self, tid, fill_merged=True):
        """返回表格二维网格（文本），合并单元格按需填充；以及 runs 网格。"""
        t = self.by[tid]
        grid = [[None] * t["ncols"] for _ in range(t["nrows"])]
        runs = [[None] * t["ncols"] for _ in range(t["nrows"])]
        origin = [[None] * t["ncols"] for _ in range(t["nrows"])]
        for c in t["cells"]:
            for dr in range(c.get("rs", 1)):
                for dc in range(c.get("cs", 1)):
                    r, cc = c["r"] + dr, c["c"] + dc
                    if r < t["nrows"] and cc < t["ncols"]:
                        if fill_merged or (dr == 0 and dc == 0):
                            grid[r][cc] = c["text"]
                            runs[r][cc] = c.get("runs") or ([[c["text"], ""]] if c["text"] else [])
                        origin[r][cc] = (c["r"], c["c"])
        return grid, runs, origin

    def sections(self):
        return self.m.get("sections", [])


def parse_ref(ref):
    """'S1:T3.R2.C1' -> ('S1', 'T3.R2.C1')；'S2:Sheet1!B2:D9' -> ('S2', 'Sheet1!B2:D9')"""
    m = re.match(r"^([A-Za-z]+\d*)\s*:\s*(.+)$", ref.strip())
    if not m:
        raise DIError("引用格式应为 源别名:ID，例如 S1:T3.R2.C1 或 S1:H5，收到：%s" % ref)
    return m.group(1), m.group(2)


# ======================================================================= 提纲
def outline(model, alias, max_lines=80, max_depth=3, section=None):
    m = model.m
    lines = []
    st = m.get("stats", {})
    if m["type"] == "xlsx":
        lines.append("[%s] %s  (表格文件, %d个工作表)" % (alias, m["name"], len(m["sheets"])))
        for sh in m["sheets"]:
            if not sh["nrows"]:
                lines.append("  工作表 %s：空" % sh["name"])
                continue
            hdr = [short(str(v), 12) for v in (sh["rows"][0] if sh["rows"] else []) if v is not None][:10]
            lines.append("  工作表 %s：%d行×%d列  首行：%s" % (sh["name"], sh["nrows"], sh["ncols"], " | ".join(hdr)))
            colA = [str(r[0]) for r in sh["rows"][1:] if r and r[0] not in (None, "")]
            if colA:
                uniq = list(dict.fromkeys(colA))
                lines.append("    A列标签 %d 个（去重）：%s%s" % (len(uniq), "、".join(short(x, 24) for x in uniq[:12]),
                                                         " …" if len(uniq) > 12 else ""))
            for ch in sh.get("charts", []):
                lines.append("    图表：%s（%d个系列）" % (ch.get("title") or "无标题", len(ch.get("series", []))))
        return "\n".join(lines)
    head = "[%s] %s  (%s; %d段 %d表 %d图%s)" % (alias, m["name"], m["type"], st.get("paras", 0), st.get("tables", 0),
                                          st.get("figures", 0), ("; %d页" % st["pages"]) if st.get("pages") else "")
    lines.append(head)
    if m.get("backend_note"):
        lines.append("  " + m["backend_note"])
    if m.get("ocr_pages") and not section:
        first = next(b for b in m["blocks"] if b.get("ocr"))
        lines.append("  OCR 页：%s。识别文字从 %s 开始，请对照原页核对标记的文字、数字和单位。" %
                     ("、".join(map(str, m["ocr_pages"])), first["id"]))
    if m.get("title"):
        label = "  PDF 元数据标题：" if any(b.get('metadata_title_hint') for b in m['blocks']) else "  标题："
        lines.append(label + short(m["title"], 60))
    secs = m.get("sections", [])
    if section:
        root = model.by.get(section)
        if not root:
            raise DIError("找不到章节 %s" % section)
        keep = {section}
        for s in secs:
            if s.get("parent") in keep:
                keep.add(s["id"])
        secs = [s for s in secs if s["id"] in keep]
        base = root.get("depth", 0)
    else:
        base = 0
    shown = 0
    hidden = 0
    for s in secs:
        d = s.get("depth", 0) - base
        if d >= max_depth:
            hidden += 1
            continue
        if shown >= max_lines:
            hidden += 1
            continue
        sz = s.get("size", {})
        info = []
        if sz.get("chars"):
            info.append("%d字" % sz["chars"])
        else:
            info.append("空")
        if sz.get("tables"):
            info.append("%d表" % sz["tables"])
        if sz.get("figs"):
            info.append("%d图" % sz["figs"])
        extra = ""
        tabs = []
        for x in s["blocks"]:
            b = model.by.get(x)
            if b and b["t"] == "tbl" and "in" not in b and b.get("cap") and not b.get("cont"):
                n_cont = sum(1 for y in s["blocks"] if model.by.get(y, {}).get("cont") == b["id"])
                tabs.append("%s %s[%d×%d%s]" % (b["id"], short(b["cap"], 20), b["nrows"], b["ncols"],
                                                 "+续表" if n_cont else ""))
        direct = [x for x in s["blocks"] if not any(x in c["blocks"] for c in secs if c.get("parent") == s["id"])]
        tabs = [t for t in tabs if t.split()[0] in direct]
        if tabs:
            extra = "  ▸ " + "；".join(tabs[:4])
        loc = "" if s.get("in", "body") == "body" else " @%s" % s["in"]
        lines.append("  " + "  " * d + "%s %s (%s)%s%s" % (s["id"], short(s["title"], 40), ",".join(info), loc, extra))
        shown += 1
    if hidden:
        lines.append("  … 另有 %d 个更深层/更多章节未显示（用 outline --section Hx 或 --depth 展开）" % hidden)
    loose = [b for b in m.get("blocks", []) if b["t"] == "tbl" and "in" not in b and not any(
        b["id"] in s["blocks"] for s in m.get("sections", []))]
    if loose and not section:
        lines.append("  未归属章节的表格：" + "；".join("%s %s[%d×%d]" % (b["id"], short(b.get("cap") or _tbl_head(model, b), 24),
                                                                    b["nrows"], b["ncols"]) for b in loose[:8]))
    figs = m.get("figures", [])
    if figs and not section:
        lines.append("  图：" + " | ".join("%s %s" % (f["id"], short(f.get("cap") or "(无题注)", 18)) for f in figs[:12]) +
                     (" …" if len(figs) > 12 else ""))
    cms = m.get("comments", [])
    if cms and not section:
        lines.append("  批注 %d 条：" % len(cms))
        for c in cms[:15]:
            lines.append("    #%s [%s] “%s” ← %s" % (c["id"], ",".join(c.get("blocks", [])[:2]), short(c["text"], 50),
                                                     short(c.get("anchor", ""), 20)))
    return "\n".join(lines)


def _tbl_head(model, b):
    first = [c["text"] for c in b["cells"] if c["r"] == 0][:4]
    return "首行:" + "|".join(short(x, 8) for x in first)


# ======================================================================= 查看
def render_runs(runs):
    out = []
    for t, f in runs:
        if f.startswith("@"):
            out.append("[图%s%s]" % (f[1:], (":" + t) if t else ""))
        elif "_" in f and t.strip():
            out.append("_{%s}" % t)
        elif "^" in f and t.strip():
            out.append("^{%s}" % t)
        else:
            out.append(t)
    return "".join(out)


def render_table(model, tid, rows=None, maxw=24, full=False):
    t = model.by[tid]
    grid, runs, origin = model.table_grid(tid, fill_merged=False)
    r0, r1 = 0, t["nrows"]
    if rows:
        a, _, b = rows.partition("-")
        r0, r1 = int(a), int(b or a) + 1
    out = ["%s %s  (%d行×%d列, 表头%d行%s)" % (tid, t.get("cap", ""), t["nrows"], t["ncols"], t.get("hdr", 1),
                                         ", 续表←%s" % t["cont"] if t.get("cont") else "")]
    out.append("| R\\C | " + " | ".join("C%d" % c for c in range(t["ncols"])) + " |")
    out.append("|" + "---|" * (t["ncols"] + 1))
    for r in range(r0, min(r1, t["nrows"])):
        cells = []
        for c in range(t["ncols"]):
            o = origin[r][c]
            if o is None:
                cells.append("")
            elif o != (r, c):
                cells.append("↑" if o[1] == c else "←")
            else:
                txt = render_runs(runs[r][c]) if runs[r][c] else ""
                txt = txt.replace("\n", "/")
                cells.append(txt if full else short(txt, maxw))
        out.append("| R%d | " % r + " | ".join(x.replace("|", "\\|") for x in cells) + " |")
    if t.get("foot"):
        out.append("注：" + " / ".join(short(x, 80) for x in t["foot"]))
    return "\n".join(out)


def render_block(model, bid, full=False, maxchars=1500):
    b = model.get(bid)
    if b is None:
        raise DIError("找不到 %s（可先用 outline / search 查ID）" % bid)
    if "blocks" in b and "title" in b:     # 章节
        heading = model.by.get(b.get("start"), {})
        out = ["%s %s%s" % (bid, _ocr_label(heading), b["title"])]
        used = 0
        for x in b["blocks"]:
            bb = model.by.get(x)
            if not bb:
                continue
            if bb["t"] == "tbl" and bb.get("in") is None or (bb["t"] == "tbl" and bb.get("in") == b.get("in")):
                s = render_table(model, x, full=full)
            elif bb["t"] == "p":
                if bb.get("in") and bb.get("in") != b.get("in"):
                    continue
                lab = (bb.get("label") + " ") if bb.get("label") else ""
                s = "%s: %s%s%s" % (x, _ocr_label(bb), lab, render_runs(bb["runs"]) if bb.get("runs") else bb["text"])
                if bb.get("fig"):
                    f = model.by.get(bb["fig"])
                    s += "  ⟨%s %s⟩" % (bb["fig"], f.get("cap", "") if f else "")
            else:
                continue
            if not full and used + len(s) > maxchars:
                out.append("…（已截断，用 --full 查看全部，或 show 单个ID）")
                break
            out.append(s)
            used += len(s)
        return "\n".join(out)
    if b.get("t") == "tbl":
        return render_table(model, bid, full=full)
    if "imgs" in b and "para" in b:        # 图
        imgs = model.m.get("images", {})
        out = ["%s %s  (所在段落 %s)" % (bid, b.get("cap") or "(无题注)", b["para"])]
        for i in b["imgs"]:
            info = imgs.get(i, {})
            out.append("  %s %s %s%s" % (i, info.get("ext", ""), info.get("file", ""),
                                         ("  图中文字: " + short(info["text"].replace("\n", " / "), 200)) if info.get("text") else ""))
        return "\n".join(out)
    if "r" in b and "c" in b:              # 单元格
        return "%s: %s" % (bid, render_runs(b["runs"]) if b.get("runs") else b["text"])
    lab = (b.get("label") + " ") if b.get("label") else ""
    return "%s: %s%s%s" % (bid, _ocr_label(b), lab, render_runs(b["runs"]) if b.get("runs") else b.get("text", ""))


def _ocr_label(block):
    if not block.get('ocr'):
        return ''
    warning = _ocr_review(block)
    return '[OCR，第 %s 页] %s' % (block['page'], warning + ' ' if warning else '')


def _ocr_review(block):
    from .pdf_ocr import review_groups
    labels = ['⚠ 低置信%s ' % label + '、'.join('%s（%.1f/100）' % (w['text'], w['confidence'])
                                          for w in words)
              for label, words in review_groups(block) if words]
    if block.get('metadata_title_hint'):
        labels.append('PDF 元数据标题「%s」，OCR 原文：' % block['metadata_title_hint'])
    return ' '.join(labels)


def render_sheet(model, rng=None, max_rows=40):
    m = model.m
    sheet, cells = None, rng
    if rng and "!" in rng:
        sheet, cells = rng.split("!", 1)
    sh = next((s for s in m["sheets"] if s["name"] == sheet), m["sheets"][0] if not sheet else None)
    if sh is None:
        raise DIError("找不到工作表 %s" % sheet)
    from .read_other import col_letter, split_ref
    r0, c0, r1, c1 = 0, 0, sh["nrows"] - 1, sh["ncols"] - 1
    if cells and cells != sh["name"]:
        a, _, b = cells.partition(":")
        r0, c0 = split_ref(a)
        r1, c1 = split_ref(b) if b else (r0, c0)
    r1 = min(r1, sh["nrows"] - 1, r0 + max_rows - 1)
    out = ["工作表 %s  %s%d:%s%d" % (sh["name"], col_letter(c0), r0 + 1, col_letter(c1), r1 + 1)]
    out.append("| 行 | " + " | ".join(col_letter(c) for c in range(c0, c1 + 1)) + " |")
    for r in range(r0, r1 + 1):
        row = sh["rows"][r] if r < len(sh["rows"]) else []
        out.append("| %d | " % (r + 1) + " | ".join("" if c >= len(row) or row[c] is None else short(str(row[c]), 20)
                                                    for c in range(c0, c1 + 1)) + " |")
    return "\n".join(out)


# ======================================================================= 检索
def search(models, query, top=8, kinds=("sec", "tbl", "p", "fig")):
    """models: {alias: Model}；返回 [(score, alias, id, 描述)]。"""
    res = []
    q = query.strip()
    literal = q.casefold()
    for alias, md in models.items():
        m = md.m
        if m["type"] == "xlsx":
            for sh in m["sheets"]:
                for r, row in enumerate(sh["rows"]):
                    for c, v in enumerate(row):
                        if v is not None and isinstance(v, str) and q and contains_score(q, v) >= 0.8:
                            from .read_other import col_letter
                            res.append((0.9, alias, "%s!%s%d" % (sh["name"], col_letter(c), r + 1), short(v, 50)))
            continue
        if "sec" in kinds:
            for s in m.get("sections", []):
                sc = contains_score(q, s["title"])
                if sc > 0.3:
                    res.append((sc + 0.2, alias, s["id"], "章节 " + short(s["title"], 50)))
        if "tbl" in kinds:
            for b in m.get("blocks", []):
                if b["t"] == "tbl" and b.get("cap"):
                    sc = contains_score(q, b["cap"])
                    if sc > 0.3:
                        res.append((sc + 0.15, alias, b["id"], "表 " + short(b["cap"], 50)))
        if "fig" in kinds:
            for f in m.get("figures", []):
                txt = (f.get("cap") or "") + " " + " ".join(
                    (m.get("images", {}).get(i, {}).get("text") or "") for i in f.get("imgs", []))
                sc = contains_score(q, txt)
                if sc > 0.4:
                    res.append((sc + 0.1, alias, f["id"], "图 " + short(f.get("cap") or "(无题注)", 50)))
        if "p" in kinds:
            for b in m.get("blocks", []):
                if b["t"] == "p" and b["text"].strip():
                    sc = 1.0 if literal and literal in b["text"].casefold() else contains_score(q, b["text"])
                    if sc > 0.5:
                        label = "OCR 第 %s 页：" % b["page"] if b.get("ocr") else ""
                        if b.get("ocr"):
                            warning = _ocr_review(b)
                            if warning:
                                label += warning + " "
                        res.append((sc, alias, b["id"], label + short(b["text"], 60)))
                elif b["t"] == "tbl":
                    for c in b["cells"]:
                        if c["text"] and contains_score(q, c["text"]) > 0.7:
                            res.append((contains_score(q, c["text"]) - 0.05, alias,
                                        "%s.R%d.C%d" % (b["id"], c["r"], c["c"]), "单元格 " + short(c["text"], 50)))
    res.sort(key=lambda x: -x[0])
    seen, out = set(), []
    for r in res:
        k = (r[1], r[2])
        if k not in seen:
            seen.add(k)
            out.append(r)
        if len(out) >= top:
            break
    return out
