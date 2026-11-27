# -*- coding: utf-8 -*-
"""Word 自动编号求值：还原 “3.5”“表 1”“图 2”“a)” 等由列表编号生成、文档 XML 中不存在的文字。"""
from __future__ import annotations

from .ooxml import W, w, W_VAL

_CN_DIG = "零一二三四五六七八九"
_CN_LEGAL = "零壹贰叁肆伍陆柒捌玖"
_TIAN_GAN = "甲乙丙丁戊己庚辛壬癸"
_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
_BULLET_MAP = {
    "": "•", "": "●", "": "▪", "": "➢", "": "◆",
    "": "■", "": "√", "": "❖", "": "○", "": "◇",
    "": "➢", "": "➢", "o": "○", "•": "•",
}


def _roman(n):
    vals = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"),
            (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    s = ""
    for v, r in vals:
        while n >= v:
            s += r
            n -= v
    return s


def _letter(n):
    if n <= 0:
        return ""
    k = (n - 1) // 26 + 1
    return chr(ord("A") + (n - 1) % 26) * k


def _cn(n, digits=_CN_DIG):
    if n < 10:
        return digits[n]
    if n < 20:
        return "十" + (digits[n % 10] if n % 10 else "")
    if n < 100:
        return digits[n // 10] + "十" + (digits[n % 10] if n % 10 else "")
    return str(n)


def format_num(n, fmt):
    if n is None:
        n = 1
    if fmt in ("decimal", "decimalZero") or not fmt:
        return ("%02d" % n) if fmt == "decimalZero" and n < 10 else str(n)
    if fmt == "upperRoman":
        return _roman(n)
    if fmt == "lowerRoman":
        return _roman(n).lower()
    if fmt == "upperLetter":
        return _letter(n)
    if fmt == "lowerLetter":
        return _letter(n).lower()
    if fmt in ("chineseCounting", "chineseCountingThousand", "taiwaneseCounting", "japaneseCounting",
               "ideographDigital", "taiwaneseCountingThousand"):
        return _cn(n)
    if fmt == "chineseLegalSimplified":
        return _cn(n, _CN_LEGAL)
    if fmt in ("ideographTraditional",):
        return _TIAN_GAN[(n - 1) % 10]
    if fmt in ("decimalEnclosedCircle", "decimalEnclosedCircleChinese", "ideographEnclosedCircle"):
        return _CIRCLED[n - 1] if 0 < n <= 20 else str(n)
    if fmt == "decimalFullWidth" or fmt == "decimalFullWidth2":
        return "".join(chr(ord(c) + 0xFEE0) for c in str(n))
    if fmt == "decimalEnclosedParen":
        return "(%d)" % n
    if fmt == "decimalEnclosedFullstop":
        return "%d." % n
    if fmt == "none":
        return ""
    return str(n)


class Numbering:
    def __init__(self, pkg):
        self.abstract = {}      # absId -> {ilvl: dict}
        self.abs_link = {}      # absId -> numStyleLink
        self.nums = {}          # numId -> (absId, {ilvl: startOverride})
        self.style_num = {}     # styleId -> (numId, ilvl)
        self.style_outline = {}
        self.style_name = {}
        self.style_based = {}
        self.style_type = {}
        self.style_jcs = {}
        self.counters = {}
        self.used = set()
        self._load_styles(pkg)
        self._load_numbering(pkg)

    # ---------------------------------------------------------------- 样式
    def _load_styles(self, pkg):
        if not pkg.has("word/styles.xml"):
            return
        root = pkg.xml("word/styles.xml")
        for st in root.findall(w("style")):
            sid = st.get(w("styleId"))
            self.style_type[sid] = st.get(w("type"))
            nm = st.find(w("name"))
            self.style_name[sid] = nm.get(W_VAL) if nm is not None else sid
            bo = st.find(w("basedOn"))
            if bo is not None:
                self.style_based[sid] = bo.get(W_VAL)
            ppr = st.find(w("pPr"))
            if ppr is not None:
                np_ = ppr.find(w("numPr"))
                if np_ is not None:
                    nid = np_.find(w("numId"))
                    il = np_.find(w("ilvl"))
                    self.style_num[sid] = (nid.get(W_VAL) if nid is not None else None,
                                           int(il.get(W_VAL)) if il is not None else None)
                ol = ppr.find(w("outlineLvl"))
                if ol is not None:
                    self.style_outline[sid] = int(ol.get(W_VAL))
                jc = ppr.find(w("jc"))
                if jc is not None:
                    self.style_jcs[sid] = jc.get(W_VAL)

    def style_jc(self, sid):
        for s in self.style_chain(sid):
            if s in self.style_jcs:
                return self.style_jcs[s]
        return None

    def style_chain(self, sid):
        seen = []
        while sid and sid not in seen:
            seen.append(sid)
            sid = self.style_based.get(sid)
        return seen

    def outline_level(self, sid):
        """样式的大纲级别（0=一级标题），无则 None。"""
        for s in self.style_chain(sid):
            if s in self.style_outline:
                lv = self.style_outline[s]
                return lv if lv < 9 else None
            name = (self.style_name.get(s) or "").lower().replace(" ", "")
            for prefix in ("heading", "标题"):
                if name.startswith(prefix) and name[len(prefix):].isdigit():
                    return int(name[len(prefix):]) - 1
        return None

    def style_numpr(self, sid):
        numid, ilvl = None, None
        for s in self.style_chain(sid):
            if s in self.style_num:
                n, i = self.style_num[s]
                if numid is None and n is not None:
                    numid = n
                if ilvl is None and i is not None:
                    ilvl = i
        return numid, ilvl

    # ---------------------------------------------------------------- 编号定义
    def _load_numbering(self, pkg):
        if not pkg.has("word/numbering.xml"):
            return
        root = pkg.xml("word/numbering.xml")
        for an in root.findall(w("abstractNum")):
            aid = an.get(w("abstractNumId"))
            lvls = {}
            for lv in an.findall(w("lvl")):
                il = int(lv.get(w("ilvl")))
                d = {"start": 1, "fmt": "decimal", "text": "", "lgl": False, "pstyle": None}
                e = lv.find(w("start"))
                if e is not None:
                    d["start"] = int(e.get(W_VAL))
                e = lv.find(w("numFmt"))
                if e is not None:
                    d["fmt"] = e.get(W_VAL)
                e = lv.find(w("lvlText"))
                if e is not None:
                    d["text"] = e.get(W_VAL) or ""
                if lv.find(w("isLgl")) is not None:
                    d["lgl"] = True
                e = lv.find(w("pStyle"))
                if e is not None:
                    d["pstyle"] = e.get(W_VAL)
                lvls[il] = d
            self.abstract[aid] = lvls
            sl = an.find(w("numStyleLink"))
            if sl is not None:
                self.abs_link[aid] = sl.get(W_VAL)
        for num in root.findall(w("num")):
            nid = num.get(w("numId"))
            a = num.find(w("abstractNumId"))
            ov = {}
            for lo in num.findall(w("lvlOverride")):
                so = lo.find(w("startOverride"))
                if so is not None:
                    ov[int(lo.get(w("ilvl")))] = int(so.get(W_VAL))
            self.nums[nid] = (a.get(W_VAL) if a is not None else None, ov)

    def _abs_of(self, numid, depth=0):
        if numid not in self.nums or depth > 3:
            return None
        aid = self.nums[numid][0]
        if aid in self.abs_link:   # 编号样式链接
            st = self.abs_link[aid]
            n2, _ = self.style_numpr(st)
            if n2 and n2 != numid:
                return self._abs_of(n2, depth + 1) or aid
        return aid

    # ---------------------------------------------------------------- 求值
    def resolve(self, ppr, style_id):
        """返回 (numId, ilvl) 或 None。"""
        numid = ilvl = None
        if ppr is not None:
            np_ = ppr.find(w("numPr"))
            if np_ is not None:
                e = np_.find(w("numId"))
                if e is not None:
                    numid = e.get(W_VAL)
                e = np_.find(w("ilvl"))
                if e is not None:
                    ilvl = int(e.get(W_VAL))
        if numid is None and style_id:
            numid, sil = self.style_numpr(style_id)
            if ilvl is None:
                ilvl = sil
        if not numid or numid == "0" or numid not in self.nums:
            return None
        if ilvl is None:
            # 样式与编号级别通过 lvl/pStyle 关联
            aid = self._abs_of(numid)
            ilvl = 0
            for il, d in (self.abstract.get(aid) or {}).items():
                if d.get("pstyle") and d["pstyle"] == style_id:
                    ilvl = il
                    break
        return numid, ilvl

    def label(self, numid, ilvl):
        aid = self._abs_of(numid)
        lvls = self.abstract.get(aid)
        if not lvls or ilvl not in lvls:
            return ""
        cnt = self.counters.setdefault(aid, [None] * 10)
        ov = self.nums[numid][1]
        if numid not in self.used:
            self.used.add(numid)
            for il, st in ov.items():
                cnt[il] = st - 1
                for d in range(il + 1, 10):
                    cnt[d] = None
        lv = lvls[ilvl]
        cnt[ilvl] = lv["start"] if cnt[ilvl] is None else cnt[ilvl] + 1
        for d in range(ilvl + 1, 10):
            cnt[d] = None
        if lv["fmt"] == "bullet":
            t = lv["text"]
            return _BULLET_MAP.get(t, t if t and ord(t[0]) < 0xE000 else "•")
        text = lv["text"]
        for k in range(9):
            ph = "%" + str(k + 1)
            if ph in text:
                lk = lvls.get(k, {"start": 1, "fmt": "decimal"})
                val = cnt[k] if cnt[k] is not None else lk["start"]
                fmt = "decimal" if lv["lgl"] else lk["fmt"]
                text = text.replace(ph, format_num(val, fmt))
        return text
