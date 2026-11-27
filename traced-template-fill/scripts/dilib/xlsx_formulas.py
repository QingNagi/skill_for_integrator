"""Find spreadsheet formula dependencies and invalidate stale cached values."""
from __future__ import annotations

import re

from .plan_xlsx import S, col_index, col_letter, split_ref
from .safe_xml import parse_xml

_PFX = r"(?:(?:'((?:[^']|'')+)'|([^\s!'\"(),;=+\-*/&<>^:{}\[\]%]+))!)?"
_A1 = re.compile(r"(?<![\w.$'])" + _PFX + r"(\$?)([A-Z]{1,3})(\$?)(\d+)(?::(\$?)([A-Z]{1,3})(\$?)(\d+))?(?![\w(])")
_COLS = re.compile(r"(?<![\w.$'])" + _PFX + r"(\$?)([A-Z]{1,3}):(\$?)([A-Z]{1,3})(?![\w(\d])")


def _sheet_of(m, here):
    return (m.group(1) or '').replace("''", "'") or m.group(2) or here


def _shift(formula, dr, dc):
    '''共享公式：从主格的公式推出某个子格的公式（相对引用按偏移平移，带 $ 的不动）。'''

    def col(c, absolute):
        return c if absolute else col_letter(max(0, col_index(c) + dc))

    def row(r, absolute):
        return r if absolute else str(max(1, int(r) + dr))

    def a1(m):
        s = m.group(0)[:m.start(3) - m.start(0)]
        s += m.group(3) + col(m.group(4), m.group(3)) + m.group(5) + row(m.group(6), m.group(5))
        if m.group(8):
            s += ':' + m.group(7) + col(m.group(8), m.group(7)) + m.group(9) + row(m.group(10), m.group(9))
        return s
    return _A1.sub(a1, formula)


def _ranges_in(formula, here, names, depth=0):
    '''公式引用的区域 [(工作表名, r0, c0, r1, c1)]（行列从 0 起，整列引用行到无穷）；定义名称展开（最多 3 层）。'''
    out = []
    for m in _A1.finditer(formula):
        c0, r0 = col_index(m.group(4)), int(m.group(6)) - 1
        c1, r1 = (col_index(m.group(8)), int(m.group(10)) - 1) if m.group(8) else (c0, r0)
        out.append((_sheet_of(m, here), min(r0, r1), min(c0, c1), max(r0, r1), max(c0, c1)))
    for m in _COLS.finditer(formula):
        a, b = col_index(m.group(4)), col_index(m.group(6))
        out.append((_sheet_of(m, here), 0, min(a, b), 10 ** 7, max(a, b)))
    if depth < 3:
        for nm, ref in names.items():
            if nm in formula and re.search(r'(?<![\w.])%s(?![\w(])' % re.escape(nm), formula):
                out += _ranges_in(ref, here, names, depth + 1)
    return out


def invalidate_dependents(pkg, metas, written, skip=()):
    '''written: {工作表名: {(r, c)}} 本次写入的格。依赖它们的公式（直接或经过别的公式、定义名称间接引用）的缓存值清掉，
    打开时重算；不然收件人在不重算的查看器里看到的是旧结果。返回被清掉的个数。结构化引用/外部引用看不懂的，按“依赖”处理。'''
    if not any(written.values()):
        return 0
    wb = pkg.xml('xl/workbook.xml')
    names = {}
    for dn in wb.iter(S + 'definedName'):
        if dn.get('localSheetId') is None and dn.get('name') and dn.text and not dn.get('name').startswith('_xlnm'):
            names[dn.get('name')] = dn.text
    formulas = []      # (工作表名, 部件, r, c, 引用区域或 None=看不懂)
    for meta in metas:
        part = meta['part']
        if not part or meta['name'] in skip or not pkg.has(part):
            continue
        root = pkg._xml.get(part)
        if root is None:
            data = pkg.parts[part]
            if b'<f' not in data and b':f>' not in data and b':f ' not in data:
                continue
            root = parse_xml(data)
        masters = {}
        cells = []
        for el in root.iter(S + 'c'):
            f = el.find(S + 'f')
            if f is None:
                continue
            r, c = split_ref(el.get('r'))
            if f.get('t') == 'shared' and f.text:
                masters[f.get('si')] = (r, c, f.text)
            cells.append((r, c, f))
        for r, c, f in cells:
            text = f.text
            if not text and f.get('t') == 'shared' and f.get('si') in masters:
                r0, c0, mtext = masters[f.get('si')]
                text = _shift(mtext, r - r0, c - c0)
            rng = None if (not text or '[' in text) else _ranges_in(text, meta['name'], names)
            formulas.append((meta['name'], part, r, c, rng))
    dirty = {k: set(v) for k, v in written.items()}
    hit, changed = set(), True
    while changed:
        changed = False
        for sh, part, r, c, rng in formulas:
            if (sh, r, c) in hit:
                continue
            if rng is None or any(any(a <= rr <= b and d <= cc <= e for rr, cc in dirty.get(s, ())) for s, a, d, b, e in rng):
                hit.add((sh, r, c))
                dirty.setdefault(sh, set()).add((r, c))
                changed = True
    byp = {}
    for sh, part, r, c, _rng in formulas:
        if (sh, r, c) in hit:
            byp.setdefault(part, set()).add('%s%d' % (col_letter(c), r + 1))
    n = 0
    for part, refs in byp.items():
        for el in pkg.xml(part).iter(S + 'c'):
            if el.get('r') in refs:
                v = el.find(S + 'v')
                if v is not None:
                    el.remove(v)
                    n += 1
                if el.get('t') in ('str', 'b', 'e', 'n'):
                    del el.attrib['t']
    return n
