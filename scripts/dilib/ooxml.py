# -*- coding: utf-8 -*-
"""OOXML 底层：仅用标准库读写 docx/xlsx 包，保证命名空间声明不丢失（兼容 Word 2007~365 / WPS / LibreOffice）。"""
from __future__ import annotations

import copy
from functools import lru_cache
import io
import posixpath
import re
import zipfile
from xml.etree import ElementTree as ET

from .safe_xml import parse_xml

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "v": "urn:schemas-microsoft-com:vml",
    "o": "urn:schemas-microsoft-com:office:office",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
    "wpg": "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup",
    "wpc": "http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas",
    "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
    "x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
}
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
PKG_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
XML_NS = "http://www.w3.org/XML/1998/namespace"

RT_COMMENTS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"
RT_HYPERLINK = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
RT_IMAGE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
CT_COMMENTS = "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"

IMAGE_CT = {
    "png": "image/png", "jpeg": "image/jpeg", "jpg": "image/jpeg", "gif": "image/gif",
    "bmp": "image/bmp", "tif": "image/tiff", "tiff": "image/tiff", "emf": "image/x-emf",
    "wmf": "image/x-wmf", "svg": "image/svg+xml",
}

for _p, _u in NS.items():
    if _p != "x":
        ET.register_namespace(_p, _u)


@lru_cache(maxsize=128)
def qn(tag):
    p, l = tag.split(":")
    return "{%s}%s" % (NS[p], l)


W = NS["w"]
R = NS["r"]


@lru_cache(maxsize=256)
def w(local):
    return "{%s}%s" % (W, local)


# 常用标签
W_P, W_R, W_T, W_TBL, W_TR, W_TC = w("p"), w("r"), w("t"), w("tbl"), w("tr"), w("tc")
W_PPR, W_RPR, W_TCPR, W_TBLPR, W_TRPR = w("pPr"), w("rPr"), w("tcPr"), w("tblPr"), w("trPr")
W_VAL = w("val")

# ---------------------------------------------------------------- 序列化（保留命名空间声明）
_START_TAG = re.compile(r"<([^\s/>]+)((?:\s+[^\s=]+\s*=\s*\"[^\"]*\")*)\s*(/?)>", re.S)


def _esc(s, attr=False):
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    if attr:
        s = s.replace('"', "&quot;").replace("\n", "&#10;").replace("\r", "&#13;").replace("\t", "&#9;")
    return s


def _serialize_default_ns(root, ns_decls):
    """ElementTree 不支持“默认命名空间 + 无前缀属性”（如 .rels），这里手写序列化。"""
    default_ns = next(u for p, u in ns_decls if p == "")
    pfx = {u: p for p, u in ns_decls if p}
    pfx.setdefault(XML_NS, "xml")
    extra = {}

    def name(q):
        if q[:1] != "{":
            return q
        uri, local = q[1:].split("}", 1)
        if uri == default_ns:
            return local
        p = pfx.get(uri)
        if p is None:
            p = "ns%d" % len(extra)
            pfx[uri] = extra[uri] = p
        return p + ":" + local
    out = []

    def walk(el, top=False):
        out.append("<" + name(el.tag))
        if top:
            out.append("%NSDECL%")
        for k, v in el.attrib.items():
            out.append(' %s="%s"' % (name(k), _esc(v, True)))
        if len(el) or el.text:
            out.append(">")
            if el.text:
                out.append(_esc(el.text))
            for ch in el:
                walk(ch)
            out.append("</%s>" % name(el.tag))
        else:
            out.append("/>")
        if el.tail and not top:
            out.append(_esc(el.tail))
    walk(root, True)
    decl = ' xmlns="%s"' % default_ns + "".join(' xmlns:%s="%s"' % (p, u) for p, u in ns_decls if p) + \
           "".join(' xmlns:%s="%s"' % (p, u) for u, p in extra.items())
    s = "".join(out).replace("%NSDECL%", decl, 1)
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n' + s).encode("utf-8")


def serialize(root, ns_decls):
    default_ns = None
    for p, u in ns_decls:
        if p == "":
            default_ns = u
        else:
            try:
                ET.register_namespace(p, u)
            except ValueError:
                pass
    if default_ns:
        return _serialize_default_ns(root, ns_decls)
    s = ET.tostring(root, encoding="unicode")
    m = _START_TAG.match(s)
    if m:
        tagname, attrs, selfclose = m.group(1), m.group(2), m.group(3)
        gen = re.findall(r'\s+xmlns(?::([\w.\-]+))?="([^"]*)"', attrs)
        other = re.sub(r'\s+xmlns(?::[\w.\-]+)?="[^"]*"', "", attrs)
        decl, have = [], set()
        for p, u in list(ns_decls) + [(g[0], g[1]) for g in gen]:
            if p in have:
                continue
            have.add(p)
            decl.append('xmlns="%s"' % u if p == "" else 'xmlns:%s="%s"' % (p, u))
        start = "<%s %s%s%s>" % (tagname, " ".join(decl), other, "/" if selfclose else "")
        s = start + s[m.end():]
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n' + s).encode("utf-8")


# ---------------------------------------------------------------- 包
class Rels:
    def __init__(self, pkg, rels_name):
        self.pkg, self.name = pkg, rels_name
        if rels_name in pkg.parts:
            self.root = parse_xml(pkg.parts[rels_name])
        else:
            self.root = ET.Element("{%s}Relationships" % PKG_REL)
        self.dirty = False

    def items(self):
        for el in self.root:
            yield el.get("Id"), el.get("Type"), el.get("Target"), el.get("TargetMode")

    def target(self, rid):
        for el in self.root:
            if el.get("Id") == rid:
                return el.get("Target"), el.get("TargetMode")
        return None, None

    def add(self, rtype, target, external=False):
        for el in self.root:
            if el.get("Type") == rtype and el.get("Target") == target and \
                    (el.get("TargetMode") == "External") == external:
                return el.get("Id")
        ids = {el.get("Id") for el in self.root}
        n = len(ids) + 1
        while "rId%d" % n in ids:
            n += 1
        el = ET.SubElement(self.root, "{%s}Relationship" % PKG_REL)
        el.set("Id", "rId%d" % n)
        el.set("Type", rtype)
        el.set("Target", target)
        if external:
            el.set("TargetMode", "External")
        self.dirty = True
        return "rId%d" % n

    def save(self):
        if self.dirty:
            self.pkg.parts[self.name] = serialize(self.root, [("", PKG_REL)])
            if self.name not in self.pkg.order:
                self.pkg.order.append(self.name)


class Package:
    """docx/xlsx 包：parts 保存原始字节；xml() 解析后的部件在 save() 时序列化。"""

    def __init__(self, path=None, data=None):
        self.path = path
        z = zipfile.ZipFile(io.BytesIO(data) if data is not None else path)
        self.order = [i.filename for i in z.infolist()]
        self.parts = {n: z.read(n) for n in self.order}
        z.close()
        self._xml = {}
        self._ns = {}
        self._rels = {}

    def has(self, name):
        return name in self.parts

    def xml(self, name):
        if name not in self._xml:
            data = self.parts[name]
            namespaces = {}
            self._xml[name] = parse_xml(data, namespaces)
            self._ns[name] = list(namespaces.items())
        return self._xml[name]

    def new_xml(self, name, root, ns_decls):
        self._xml[name] = root
        self._ns[name] = ns_decls
        if name not in self.order:
            self.order.append(name)
        self.parts.setdefault(name, b"")

    def rels_name(self, part):
        d, b = posixpath.split(part)
        return posixpath.join(d, "_rels", b + ".rels")

    def rels(self, part):
        rn = self.rels_name(part)
        if rn not in self._rels:
            self._rels[rn] = Rels(self, rn)
        return self._rels[rn]

    def resolve(self, part, target):
        """相对 part 的 Target -> 包内部件名。"""
        if target.startswith("/"):
            return target[1:]
        return posixpath.normpath(posixpath.join(posixpath.dirname(part), target))

    def add_part(self, name, data):
        self.parts[name] = data
        if name not in self.order:
            self.order.append(name)

    # ---- [Content_Types].xml
    def _ct(self):
        return self.xml("[Content_Types].xml")

    def ensure_default(self, ext, ctype):
        ct = self._ct()
        for el in ct:
            if el.tag.endswith("Default") and (el.get("Extension") or "").lower() == ext.lower():
                return
        el = ET.Element("{%s}Default" % PKG_CT)
        el.set("Extension", ext)
        el.set("ContentType", ctype)
        ct.insert(0, el)

    def ensure_override(self, partname, ctype):
        ct = self._ct()
        pn = "/" + partname.lstrip("/")
        for el in ct:
            if el.tag.endswith("Override") and el.get("PartName") == pn:
                return
        el = ET.SubElement(ct, "{%s}Override" % PKG_CT)
        el.set("PartName", pn)
        el.set("ContentType", ctype)

    def save(self, path):
        for r in self._rels.values():
            r.save()
        for name, root in self._xml.items():
            self.parts[name] = serialize(root, self._ns.get(name, []))
        order = list(self.order)
        if "[Content_Types].xml" in order:
            order.remove("[Content_Types].xml")
            order.insert(0, "[Content_Types].xml")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for n in order:
                z.writestr(n, self.parts[n])


# ---------------------------------------------------------------- 元素顺序（Word 对子元素顺序敏感）
PPR_ORDER = ["pStyle", "keepNext", "keepLines", "pageBreakBefore", "framePr", "widowControl", "numPr",
             "suppressLineNumbers", "pBdr", "shd", "tabs", "suppressAutoHyphens", "kinsoku", "wordWrap",
             "overflowPunct", "topLinePunct", "autoSpaceDE", "autoSpaceDN", "bidi", "adjustRightInd",
             "snapToGrid", "spacing", "ind", "contextualSpacing", "mirrorIndents", "suppressOverlap", "jc",
             "textDirection", "textAlignment", "textboxTightWrap", "outlineLvl", "divId", "cnfStyle", "rPr",
             "sectPr", "pPrChange"]
RPR_ORDER = ["rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike", "outline",
             "shadow", "emboss", "imprint", "noProof", "snapToGrid", "vanish", "webHidden", "color", "spacing",
             "w", "kern", "position", "sz", "szCs", "highlight", "u", "effect", "bdr", "shd", "fitText",
             "vertAlign", "rtl", "cs", "em", "lang", "eastAsianLayout", "specVanish", "oMath"]
TCPR_ORDER = ["cnfStyle", "tcW", "gridSpan", "hMerge", "vMerge", "tcBorders", "shd", "noWrap", "tcMar",
              "textDirection", "tcFitText", "vAlign", "hideMark"]
TBLPR_ORDER = ["tblStyle", "tblpPr", "tblOverlap", "bidiVisual", "tblStyleRowBandSize", "tblStyleColBandSize",
               "tblW", "jc", "tblCellSpacing", "tblInd", "tblBorders", "shd", "tblLayout", "tblCellMar", "tblLook"]
TRPR_ORDER = ["cnfStyle", "divId", "gridBefore", "gridAfter", "wBefore", "wAfter", "cantSplit", "trHeight",
              "tblHeader", "tblCellSpacing", "jc", "hidden", "ins", "del", "trPrChange"]


def set_child(parent, local, order, attrs=None, replace=False):
    tag = w(local)
    el = parent.find(tag)
    if el is not None and replace:
        parent.remove(el)
        el = None
    if el is None:
        el = ET.Element(tag)
        idx = order.index(local) if local in order else len(order)
        pos = len(parent)
        for i, ch in enumerate(parent):
            ln = ch.tag.split("}")[-1]
            if ln in order and order.index(ln) > idx:
                pos = i
                break
        parent.insert(pos, el)
    if attrs:
        for k, v in attrs.items():
            el.set(w(k) if ":" not in k else qn(k), str(v))
    return el


def remove_child(parent, local):
    el = parent.find(w(local))
    if el is not None:
        parent.remove(el)


def get_ppr(p, create=True):
    ppr = p.find(W_PPR)
    if ppr is None and create:
        ppr = ET.Element(W_PPR)
        p.insert(0, ppr)
    return ppr


def get_rpr(r, create=True):
    rpr = r.find(W_RPR)
    if rpr is None and create:
        rpr = ET.Element(W_RPR)
        r.insert(0, rpr)
    return rpr


# ---------------------------------------------------------------- 构造
def make_rpr(base=None, flags=(), font=None, latin_font=None, size_pt=None, bold=None, italic=None,
             color=None, underline=None):
    """base: 作为基础复制的 rPr 元素（如单元格段落标记格式）；flags: {'b','i','u','sub','sup','strike'}"""
    rpr = copy.deepcopy(base) if base is not None else ET.Element(W_RPR)
    rpr.tag = W_RPR
    for bad in ("rStyle", "vanish", "webHidden", "specVanish", "rPrChange", "del", "ins", "moveFrom", "moveTo"):
        remove_child(rpr, bad)
    if font or latin_font:
        rf = set_child(rpr, "rFonts", RPR_ORDER, replace=True)
        lf = latin_font or font
        if lf:
            rf.set(w("ascii"), lf)
            rf.set(w("hAnsi"), lf)
            rf.set(w("cs"), lf)
        if font:
            rf.set(w("eastAsia"), font)
    flags = set(flags or ())
    if bold is None and "b" in flags:
        bold = True
    if italic is None and "i" in flags:
        italic = True
    if bold is not None:
        for t in ("b", "bCs"):
            remove_child(rpr, t)
            if bold:
                set_child(rpr, t, RPR_ORDER)
            else:
                set_child(rpr, t, RPR_ORDER, {"val": "0"})
    if italic is not None:
        remove_child(rpr, "i")
        if italic:
            set_child(rpr, "i", RPR_ORDER)
    if color:
        set_child(rpr, "color", RPR_ORDER, {"val": color}, replace=True)
    if size_pt:
        hp = str(int(round(float(size_pt) * 2)))
        set_child(rpr, "sz", RPR_ORDER, {"val": hp}, replace=True)
        set_child(rpr, "szCs", RPR_ORDER, {"val": hp}, replace=True)
    if underline or "u" in flags:
        set_child(rpr, "u", RPR_ORDER, {"val": "single"}, replace=True)
    if "strike" in flags:
        set_child(rpr, "strike", RPR_ORDER)
    remove_child(rpr, "vertAlign")
    if "sub" in flags:
        set_child(rpr, "vertAlign", RPR_ORDER, {"val": "subscript"})
    elif "sup" in flags:
        set_child(rpr, "vertAlign", RPR_ORDER, {"val": "superscript"})
    return rpr if len(rpr) else None


def make_run(text, rpr=None):
    r = ET.Element(W_R)
    if rpr is not None:
        r.append(copy.deepcopy(rpr))
    parts = re.split(r"(\t|\n)", text or "")
    for part in parts:
        if part == "\t":
            ET.SubElement(r, w("tab"))
        elif part == "\n":
            ET.SubElement(r, w("br"))
        elif part:
            t = ET.SubElement(r, W_T)
            t.text = part
            if part != part.strip() or "  " in part:
                t.set("{%s}space" % XML_NS, "preserve")
    return r


def make_par(runs=(), ppr=None):
    p = ET.Element(W_P)
    if ppr is not None and len(ppr):
        pp = copy.deepcopy(ppr)
        pp.tag = W_PPR
        p.append(pp)
    for r in runs:
        p.append(r)
    return p


def make_ppr(jc=None, first_line_chars=None, line_spacing=None, line_rule="auto", before_lines=None,
             after_lines=None, keep_next=False, base=None, mark_rpr=None, outline_lvl=None, size_pt=None):
    ppr = copy.deepcopy(base) if base is not None else ET.Element(W_PPR)
    ppr.tag = W_PPR
    for bad in ("numPr", "pPrChange", "sectPr"):
        remove_child(ppr, bad)
    if keep_next:
        set_child(ppr, "keepNext", PPR_ORDER)
    if line_spacing is not None or before_lines is not None or after_lines is not None:
        sp = set_child(ppr, "spacing", PPR_ORDER, replace=True)
        if before_lines is not None:
            sp.set(w("beforeLines"), str(int(before_lines * 100)))
            sp.set(w("before"), str(int(before_lines * 312)))
        if after_lines is not None:
            sp.set(w("afterLines"), str(int(after_lines * 100)))
            sp.set(w("after"), str(int(after_lines * 312)))
        if line_spacing is not None:
            if line_rule == "exact":
                sp.set(w("line"), str(int(float(line_spacing) * 20)))
                sp.set(w("lineRule"), "exact")
            else:
                sp.set(w("line"), str(int(round(float(line_spacing) * 240))))
                sp.set(w("lineRule"), "auto")
    if first_line_chars is not None:
        ind = set_child(ppr, "ind", PPR_ORDER, replace=True)
        if first_line_chars:
            ind.set(w("firstLineChars"), str(int(first_line_chars * 100)))
            ind.set(w("firstLine"), str(int(first_line_chars * (size_pt or 12) * 20)))
        else:
            ind.set(w("firstLine"), "0")
            ind.set(w("firstLineChars"), "0")
    if jc:
        set_child(ppr, "jc", PPR_ORDER, {"val": jc}, replace=True)
    if outline_lvl is not None:
        set_child(ppr, "outlineLvl", PPR_ORDER, {"val": str(outline_lvl)}, replace=True)
    if mark_rpr is not None:
        remove_child(ppr, "rPr")
        mr = copy.deepcopy(mark_rpr)
        mr.tag = W_RPR
        ppr.append(mr)  # rPr 在 PPR_ORDER 中位于末尾附近，append 即可
        # 保证 rPr 位于 sectPr/pPrChange 之前
    return ppr


def iter_skip_fallback(el):
    """深度优先遍历，跳过 mc:Fallback（避免 Choice/Fallback 的图片、文本框重复）。"""
    fb = qn("mc:Fallback")
    if next(el.iter(fb), None) is None:
        yield from el.iter()
        return
    stack = [el]
    while stack:
        e = stack.pop()
        yield e
        kids = [k for k in list(e) if k.tag != fb]
        stack.extend(reversed(kids))


def parse_xml_fragment(s):
    """解析带 w:/r: 等前缀的片段。"""
    decl = " ".join('xmlns:%s="%s"' % (p, u) for p, u in NS.items())
    root = parse_xml("<root %s>%s</root>" % (decl, s))
    return list(root)
