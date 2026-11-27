# -*- coding: utf-8 -*-
"""从 WMF/EMF 矢量图（公式编辑器对象、Visio 图、Office 图表的预览图）中提取文字，无需 OCR。"""
from __future__ import annotations

import struct

_SYMBOL = {
    0x22: "∀", 0x24: "∃", 0x27: "∋", 0x2A: "∗", 0x2D: "−", 0x40: "≅", 0x41: "Α", 0x42: "Β", 0x43: "Χ",
    0x44: "Δ", 0x45: "Ε", 0x46: "Φ", 0x47: "Γ", 0x48: "Η", 0x49: "Ι", 0x4B: "Κ", 0x4C: "Λ", 0x4D: "Μ",
    0x4E: "Ν", 0x4F: "Ο", 0x50: "Π", 0x51: "Θ", 0x52: "Ρ", 0x53: "Σ", 0x54: "Τ", 0x55: "Υ", 0x57: "Ω",
    0x58: "Ξ", 0x59: "Ψ", 0x5A: "Ζ", 0x5E: "⊥", 0x61: "α", 0x62: "β", 0x63: "χ", 0x64: "δ", 0x65: "ε",
    0x66: "φ", 0x67: "γ", 0x68: "η", 0x69: "ι", 0x6A: "ϕ", 0x6B: "κ", 0x6C: "λ", 0x6D: "μ", 0x6E: "ν",
    0x6F: "ο", 0x70: "π", 0x71: "θ", 0x72: "ρ", 0x73: "σ", 0x74: "τ", 0x75: "υ", 0x77: "ω", 0x78: "ξ",
    0x79: "ψ", 0x7A: "ζ", 0x7E: "∼", 0xA2: "′", 0xA3: "≤", 0xA5: "∞", 0xB0: "°", 0xB1: "±", 0xB2: "″",
    0xB3: "≥", 0xB4: "×", 0xB5: "∝", 0xB6: "∂", 0xB7: "•", 0xB8: "÷", 0xB9: "≠", 0xBA: "≡", 0xBB: "≈",
    0xD6: "√", 0xD7: "⋅", 0xE5: "∑", 0xF2: "∫", 0xAE: "→", 0xAC: "←", 0xC5: "⊕", 0xC6: "∅", 0xC7: "∩",
    0xC8: "∪", 0xCE: "∈", 0xCF: "∉", 0xD1: "∇", 0xE1: "〈", 0xF1: "〉", 0x28: "(", 0x29: ")", 0x2B: "+",
    0x3D: "=", 0x3C: "<", 0x3E: ">", 0x5B: "[", 0x5D: "]", 0x7B: "{", 0x7D: "}", 0x7C: "|", 0x2F: "/",
}
_CHARSET_ENC = {134: "gbk", 136: "big5", 128: "cp932", 129: "cp949", 0: "cp1252", 1: "gbk", 238: "cp1250",
                204: "cp1251", 161: "cp1253", 162: "cp1254", 177: "cp1255", 178: "cp1256", 186: "cp1257"}


def _map_symbol(s, face):
    f = (face or "").lower()
    if "symbol" in f:
        return "".join(_SYMBOL.get(ord(c) & 0xFF if ord(c) >= 0xF000 else ord(c), c) for c in s)
    if "mt extra" in f or "wingdings" in f:
        return "".join(c for c in s if ord(c) < 0xF000 and c.isprintable() and ord(c) > 0x20)
    return "".join(chr(ord(c) - 0xF000) if 0xF020 <= ord(c) <= 0xF0FF else c for c in s)


def _wmf_texts(data):
    out = []
    pos = 0
    if len(data) >= 22 and struct.unpack_from("<I", data, 0)[0] == 0x9AC6CDD7:
        pos = 22
    if len(data) < pos + 18:
        return out
    hsize = struct.unpack_from("<H", data, pos + 2)[0] * 2
    pos += hsize
    objects = []
    cur_font = {"face": "", "charset": 1}
    create_ops = {0x02FA, 0x02FC, 0x02FB, 0x00F7, 0x01F9, 0x0142, 0x06FF}
    while pos + 6 <= len(data):
        size_w, func = struct.unpack_from("<IH", data, pos)
        size = size_w * 2
        if size < 6 or pos + size > len(data):
            break
        p = pos + 6
        if func == 0x0000:
            break
        if func in create_ops:
            obj = None
            if func == 0x02FB and size >= 6 + 18:
                # LogFont: 5*i16(10) + italic,underline,strike(3) + charset(1) + 4字节 + 字体名(32)
                charset = data[p + 13]
                face = data[p + 18: p + 18 + 32].split(b"\0")[0].decode("latin-1", "replace")
                obj = {"face": face, "charset": charset}
            try:
                i = objects.index(None)
                objects[i] = obj or {}
            except ValueError:
                objects.append(obj or {})
        elif func == 0x012D:  # SELECTOBJECT
            idx = struct.unpack_from("<H", data, p)[0]
            if idx < len(objects) and objects[idx] and "face" in objects[idx]:
                cur_font = objects[idx]
        elif func == 0x01F0:  # DELETEOBJECT
            idx = struct.unpack_from("<H", data, p)[0]
            if idx < len(objects):
                objects[idx] = None
        elif func in (0x0521, 0x0A32):
            try:
                if func == 0x0521:
                    n = struct.unpack_from("<H", data, p)[0]
                    raw = data[p + 2: p + 2 + n]
                    so = p + 2 + n + (n & 1)
                    y, x = struct.unpack_from("<hh", data, so)
                else:
                    y, x, n, opts = struct.unpack_from("<hhHH", data, p)
                    q = p + 8 + (8 if opts & 0x0006 else 0)
                    raw = data[q: q + n]
                enc = _CHARSET_ENC.get(cur_font.get("charset", 1), "gbk")
                if enc == "cp1252" and any(b >= 0x80 for b in raw):
                    try:
                        raw.decode("gbk")
                        enc = "gbk"      # 中文环境下 ANSI 字符集常实际为 GBK
                    except UnicodeDecodeError:
                        pass
                if "symbol" in (cur_font.get("face") or "").lower() or cur_font.get("charset") == 2:
                    s = "".join(_SYMBOL.get(b, chr(b)) for b in raw)
                else:
                    s = raw.decode(enc, "replace")
                    s = _map_symbol(s, cur_font.get("face"))
                if s.strip():
                    out.append((y, x, s))
            except struct.error:
                pass
        pos += size
    return out


def _emf_texts(data):
    out = []
    pos = 0
    objects = {}
    cur_face = ""
    while pos + 8 <= len(data):
        rtype, size = struct.unpack_from("<II", data, pos)
        if size < 8 or pos + size > len(data):
            break
        try:
            if rtype == 82 and size >= 12 + 92:     # EXTCREATEFONTINDIRECTW
                ih = struct.unpack_from("<I", data, pos + 8)[0]
                face = data[pos + 12 + 28: pos + 12 + 28 + 64].decode("utf-16le", "replace").split("\0")[0]
                objects[ih] = face
            elif rtype == 37:                         # SELECTOBJECT
                ih = struct.unpack_from("<I", data, pos + 8)[0]
                if ih in objects:
                    cur_face = objects[ih]
            elif rtype in (83, 84):                   # EXTTEXTOUTA / EXTTEXTOUTW
                base = pos + 8 + 16 + 12
                x, y, n, off, opts = struct.unpack_from("<iiIII", data, base)
                if not (opts & 0x10) and n:           # ETO_GLYPH_INDEX 无法还原
                    if rtype == 84:
                        s = data[pos + off: pos + off + 2 * n].decode("utf-16le", "replace")
                    else:
                        s = data[pos + off: pos + off + n].decode("gbk", "replace")
                    s = _map_symbol(s, cur_face)
                    if s.strip():
                        out.append((y, x, s))
            elif rtype == 108:                        # SMALLTEXTOUT
                x, y, n, opts = struct.unpack_from("<iiII", data, pos + 8)
                q = pos + 8 + 16 + 12 + (0 if opts & 0x100 else 16)
                if not (opts & 0x10) and n:
                    if opts & 0x200:
                        s = data[q: q + n].decode("latin-1", "replace")
                    else:
                        s = data[q: q + 2 * n].decode("utf-16le", "replace")
                    s = _map_symbol(s, cur_face)
                    if s.strip():
                        out.append((y, x, s))
            elif rtype == 14:                         # EOF
                break
        except struct.error:
            pass
        pos += size
    return out


def metafile_text(data, kind=None):
    """返回按行拼接的文字；无文字返回 ''。"""
    if kind is None:
        kind = "emf" if data[:4] == b"\x01\x00\x00\x00" and data[40:44] == b" EMF" else "wmf"
    try:
        frags = _emf_texts(data) if kind == "emf" else _wmf_texts(data)
    except Exception:
        return ""
    if not frags:
        return ""
    # 按 y 分行（容差取字号量级），行内按 x 排序
    frags.sort(key=lambda f: (f[0], f[1]))
    lines, cur, cy = [], [], None
    ys = sorted(f[0] for f in frags)
    tol = max(1, (ys[-1] - ys[0]) / max(4, len(frags)) / 2) if len(ys) > 1 else 1
    for y, x, s in frags:
        if cy is None or abs(y - cy) <= max(tol, 3):
            cur.append((x, s))
            cy = y if cy is None else cy
        else:
            lines.append(cur)
            cur, cy = [(x, s)], y
    if cur:
        lines.append(cur)
    if len(frags) <= 6 and 2 <= len(lines) <= 3:
        # 小公式：取最长的一行为主体，上方为上标、下方为下标，如 192^{+5}_{0}
        texts = ["".join(s for _, s in sorted(ln)).strip() for ln in lines]
        # 三行：中间行为主体；两行：起始 x 最小（最靠左）的行为主体
        mi = 1 if len(lines) == 3 else min(range(len(lines)), key=lambda i: min(x for x, _ in lines[i]))
        sup = "".join(texts[:mi])
        sub = "".join(texts[mi + 1:])
        return texts[mi] + ("^{%s}" % sup if sup else "") + ("_{%s}" % sub if sub else "")
    res = []
    sep = " " if len(frags) > 6 else ""
    for ln in lines:
        ln.sort()
        res.append(sep.join(s.strip() for _, s in ln).strip())
    return "\n".join(r for r in res if r)
