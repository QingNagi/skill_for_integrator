# -*- coding: utf-8 -*-
'''Excel 目标的写入与定稿（骨架，分段填充）。

直接改 sheet XML 写值（保留样式/合并/公式/数据验证/定义名称）；草稿：被填格浅黄底色 + 旧式批注(comments+vmlDrawing) + “审核说明”工作表；
终稿：去底色/批注/审核说明表，做收件人视角检查，未核项机制与 docx 一致。'''
from __future__ import annotations

import copy
import datetime
import os
import posixpath
import re
import shutil
from xml.etree import ElementTree as ET

from .safe_xml import parse_xml
from .xlsx_formulas import invalidate_dependents

from . import office
from .common import TOOL_AUTHOR, DIError, ensure_dir, lock_file_of, short
from .ooxml import PKG_CT, PKG_REL, XML_NS, Package, qn
from .plan_xlsx import S, RNS, col_letter, read_workbook, target_xlsx

NS_MAIN = S[1:-1]
RT_PREFIX = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
RT_COMMENTS = RT_PREFIX + 'comments'
RT_VML = RT_PREFIX + 'vmlDrawing'
RT_STYLES = RT_PREFIX + 'styles'
CT_COMMENTS = 'application/vnd.openxmlformats-officedocument.spreadsheetml.comments+xml'
CT_VML = 'application/vnd.openxmlformats-officedocument.vmlDrawing'
CT_STYLES = 'application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml'
DRAFT_FILL = 'FFFFF2A6'      # 草稿里被填单元格的浅黄底；取一个模板里不会用到的色值，定稿时据此识别
REVIEW_SHEET = '审核说明'


def _key(el):
    '''元素的规范化序列化文本（去掉标签间空白），用来比较两个填充/样式是否相同。'''
    s = ET.tostring(el, encoding='unicode')
    return re.sub(r'>\s+<', '><', s).strip()


def _sub(parent, tag, **kw):
    return ET.SubElement(parent, S + tag, {k: str(v) for k, v in kw.items()})


def draft_fill():
    f = ET.Element(S + 'fill')
    p = _sub(f, 'patternFill', patternType='solid')
    _sub(p, 'fgColor', rgb=DRAFT_FILL)
    _sub(p, 'bgColor', indexed=64)
    return f


def ensure_styles(pkg):
    '''目标没有 styles.xml（极简合成文件）时补一个最小的，保证后面能加样式。'''
    root = ET.Element(S + 'styleSheet')
    f = _sub(_sub(root, 'fonts', count=1), 'font')
    _sub(f, 'sz', val=11)
    _sub(f, 'name', val='Calibri')
    fills = _sub(root, 'fills', count=2)
    _sub(_sub(fills, 'fill'), 'patternFill', patternType='none')
    _sub(_sub(fills, 'fill'), 'patternFill', patternType='gray125')
    b = _sub(_sub(root, 'borders', count=1), 'border')
    for t in ('left', 'right', 'top', 'bottom', 'diagonal'):
        _sub(b, t)
    _sub(_sub(root, 'cellStyleXfs', count=1), 'xf', numFmtId=0, fontId=0, fillId=0, borderId=0)
    _sub(_sub(root, 'cellXfs', count=1), 'xf', numFmtId=0, fontId=0, fillId=0, borderId=0, xfId=0)
    _sub(_sub(root, 'cellStyles', count=1), 'cellStyle', name='Normal', xfId=0, builtinId=0)
    pkg.new_xml('xl/styles.xml', root, [('', NS_MAIN)])
    pkg.ensure_override('xl/styles.xml', CT_STYLES)
    pkg.rels('xl/workbook.xml').add(RT_STYLES, 'styles.xml')


class Styles:
    '''styles.xml 的最小封装：只复制 cellXfs 并换填充，不改动原有样式（草稿底色、定稿还原都靠它）。'''

    def __init__(self, pkg):
        if not pkg.has('xl/styles.xml'):
            ensure_styles(pkg)
        self.root = pkg.xml('xl/styles.xml')
        self.fills = self.root.find(S + 'fills')
        self.xfs = self.root.find(S + 'cellXfs')
        if self.fills is None or self.xfs is None or not len(self.xfs):
            raise DIError('styles.xml 缺少 fills 或 cellXfs，无法设置草稿底色')
        self._cache = {}

    def _xf(self, i):
        xfs = list(self.xfs)
        return xfs[i] if 0 <= i < len(xfs) else xfs[0]

    def _fill_index(self, fill):
        k = _key(fill)
        for i, f in enumerate(self.fills):
            if _key(f) == k:
                return i
        self.fills.append(copy.deepcopy(fill))
        self.fills.set('count', str(len(self.fills)))
        return len(self.fills) - 1

    def fill_key(self, xf_index):
        '''样式 xf_index 的原填充（序列化文本；无填充为空串）。草稿里逐格记下来，定稿时据此还原。'''
        fid = int(self._xf(xf_index).get('fillId') or 0)
        fills = list(self.fills)
        return _key(fills[fid]) if 1 < fid < len(fills) else ''

    def is_draft(self, xf_index):
        fid = int(self._xf(xf_index).get('fillId') or 0)
        fills = list(self.fills)
        return fid < len(fills) and any(e.get('rgb') == DRAFT_FILL for e in fills[fid].iter(S + 'fgColor'))

    def with_fill(self, xf_index, fill):
        '''复制样式 xf_index，只把填充换成 fill 元素（None=无填充）；返回新样式序号，相同结果复用。'''
        fid = 0 if fill is None else self._fill_index(fill)
        ck = (xf_index, fid)
        if ck in self._cache:
            return self._cache[ck]
        xf = copy.deepcopy(self._xf(xf_index))
        xf.tail = None
        xf.set('fillId', str(fid))
        if fid:
            xf.set('applyFill', '1')
        else:
            xf.attrib.pop('applyFill', None)
        k = _key(xf)
        idx = next((i for i, x in enumerate(self.xfs) if _key(x) == k), None)
        if idx is None:
            self.xfs.append(xf)
            self.xfs.set('count', str(len(self.xfs)))
            idx = len(self.xfs) - 1
        self._cache[ck] = idx
        return idx

    def draft_style(self, xf_index):
        return self.with_fill(xf_index, draft_fill())

    def restore_style(self, xf_index, fill_key):
        '''定稿：把草稿底色还原成记录下来的原填充（空串=无填充）。'''
        return self.with_fill(xf_index, parse_xml(fill_key) if fill_key else None)

# @@SEC:styles@@

from .plan_xlsx import cid_parse, si_text, split_ref  # noqa: E402

WS_ORDER = ['sheetPr', 'dimension', 'sheetViews', 'sheetFormatPr', 'cols', 'sheetData', 'sheetCalcPr', 'sheetProtection',
            'protectedRanges', 'scenarios', 'autoFilter', 'sortState', 'dataConsolidate', 'customSheetViews', 'mergeCells',
            'phoneticPr', 'conditionalFormatting', 'dataValidations', 'hyperlinks', 'printOptions', 'pageMargins',
            'pageSetup', 'headerFooter', 'rowBreaks', 'colBreaks', 'customProperties', 'cellWatches', 'ignoredErrors',
            'smartTags', 'drawing', 'legacyDrawing', 'legacyDrawingHF', 'picture', 'oleObjects', 'controls',
            'webPublishItems', 'tableParts', 'extLst']
_BAD_CHARS = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f]')
_NUM = re.compile(r'^-?(0|[1-9]\d*)(\.\d+)?$')


def xml_text(s):
    return _BAD_CHARS.sub('', s)


def as_number(value):
    '''纯十进制数（最多15位有效数字）写成数值格；范围“1.2~1.5”、“合格”、前导0等一律写成文本，原样不改写。'''
    if value == value.strip() and _NUM.match(value) and len(re.sub(r'\D', '', value)) <= 15 and value != '-0':
        return value
    return None


def is_text_fmt(styles, xf_index):
    return styles._xf(xf_index).get('numFmtId') == '49'      # 单元格格式为“文本”：数字也要写成文本


def ws_insert(root, el, local):
    '''按 CT_Worksheet 规定的子元素顺序插入（顺序错了 Excel 会报“文件已损坏”）。'''
    idx = WS_ORDER.index(local)
    pos = len(root)
    for i, ch in enumerate(root):
        ln = ch.tag.split('}')[-1]
        if ln in WS_ORDER and WS_ORDER.index(ln) > idx:
            pos = i
            break
    root.insert(pos, el)


def sheet_parts(pkg):
    '''工作簿里的工作表：[{name, part, state, el}]，按工作簿顺序（序号与规划里的 T<序号> 一致）。'''
    wb = pkg.xml('xl/workbook.xml')
    rels = pkg.rels('xl/workbook.xml')
    shs = wb.find(S + 'sheets')
    out = []
    for sh in (list(shs) if shs is not None else []):
        tg, mode = rels.target(sh.get(RNS + 'id'))
        part = pkg.resolve('xl/workbook.xml', tg) if tg and mode != 'External' else None
        out.append({'name': sh.get('name'), 'part': part, 'state': sh.get('state') or 'visible', 'el': sh})
    return out


RT_SST = RT_PREFIX + 'sharedStrings'
CT_SST = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml'


class SST:
    '''共享字符串表：写入的文字追加到 sharedStrings.xml（Excel 自己的写法，Excel 2007 起和 WPS 都认）；没有就新建。
    只和“纯文字”的项去重，不复用带格式（富文本/拼音）的项；只有真的要写时才把这个部件登记为待改写。'''

    def __init__(self, pkg):
        self.pkg, self.part, self.root = pkg, None, None
        for _i, ty, tg, mode in pkg.rels('xl/workbook.xml').items():
            if ty == RT_SST and mode != 'External':
                self.part = pkg.resolve('xl/workbook.xml', tg)
        self.texts = []
        if self.part and pkg.has(self.part):
            self.texts = [si_text(si) for si in parse_xml(pkg.parts[self.part]).findall(S + 'si')]
        self.plain = {}
        self.added = 0

    def text(self, k):
        return self.texts[k] if 0 <= k < len(self.texts) else ''

    def _load(self):
        if self.root is not None:
            return
        if self.part and self.pkg.has(self.part):
            self.root = self.pkg.xml(self.part)
        else:
            self.part = 'xl/sharedStrings.xml' if not self.pkg.has('xl/sharedStrings.xml') else _free_part(self.pkg, 'xl/sharedStrings%d.xml')
            self.root = ET.Element(S + 'sst')
            self.pkg.new_xml(self.part, self.root, [('', NS_MAIN)])
            self.pkg.rels('xl/workbook.xml').add(RT_SST, _rel_to('xl/workbook.xml', self.part))
            self.pkg.ensure_override(self.part, CT_SST)
        for i, si in enumerate(self.root.findall(S + 'si')):
            if len(si) == 1 and si[0].tag == S + 't':
                self.plain.setdefault(si[0].text or '', i)

    def index(self, value):
        self._load()
        idx = self.plain.get(value)
        if idx is None:
            si = ET.SubElement(self.root, S + 'si')
            t = ET.SubElement(si, S + 't')
            t.text = value
            if value != value.strip() or '  ' in value or '\n' in value:
                t.set('{%s}space' % XML_NS, 'preserve')
            idx = len(self.texts)
            self.texts.append(value)
            self.plain[value] = idx
        self.added += 1
        return idx

    def finish(self):
        if self.root is None:
            return
        n = len(self.root.findall(S + 'si'))
        self.root.set('uniqueCount', str(n))
        self.root.set('count', str(max(n, int(self.root.get('count') or 0) + self.added)))


class Sheet:
    '''一个工作表部件的编辑器：按行列找格、写值、登记原值与原填充；不动公式、合并区、数据验证。'''

    def __init__(self, pkg, part, name, styles, sst):
        self.pkg, self.part, self.name, self.styles, self.sst = pkg, part, name, styles, sst
        self.root = pkg.xml(part)
        self.data = self.root.find(S + 'sheetData')
        if self.data is None:
            raise DIError('工作表 %s 缺少 sheetData，无法写入' % name)
        self.log = {}          # 'B3' -> {'old': 原值文本, 'fill': 原填充}（同一格多次写入时只记第一次）
        self.rows, rn = {}, 0
        for row in self.data.findall(S + 'row'):
            rn = int(row.get('r') or rn + 1)
            row.set('r', str(rn))
            self.rows[rn] = row
            cn = -1
            for el in row.findall(S + 'c'):
                ref = el.get('r')
                cn = split_ref(ref)[1] if ref else cn + 1
                if not ref:
                    el.set('r', '%s%d' % (col_letter(cn), rn))
        self.merges = []
        for m in self.root.iter(S + 'mergeCell'):
            a, _, b = (m.get('ref') or '').partition(':')
            try:
                r0, c0 = split_ref(a)
                r1, c1 = split_ref(b or a)
                self.merges.append((r0, c0, r1, c1))
            except DIError:
                pass
        self.colstyle = [(int(c.get('min')), int(c.get('max')), int(c.get('style'))) for c in self.root.iter(S + 'col')
                         if c.get('style') and c.get('min') and c.get('max')]

    def _default_style(self, c, row):
        if row.get('customFormat') in ('1', 'true') and row.get('s'):
            return int(row.get('s'))
        for lo, hi, st in self.colstyle:
            if lo <= c + 1 <= hi:
                return st
        return 0

    def cell(self, r, c, create=False):
        '''r、c 从 0 起；返回 <c> 元素。create=True 时按行列顺序新建（继承行/列默认样式）。'''
        rn = r + 1
        row = self.rows.get(rn)
        if row is None:
            if not create:
                return None
            row = ET.Element(S + 'row', {'r': str(rn)})
            later = sorted(k for k in self.rows if k > rn)
            pos = list(self.data).index(self.rows[later[0]]) if later else len(self.data)
            self.data.insert(pos, row)
            self.rows[rn] = row
        before = None
        for el in row.findall(S + 'c'):
            ec = split_ref(el.get('r'))[1]
            if ec == c:
                return el
            if ec > c and before is None:
                before = el
        if not create:
            return None
        el = ET.Element(S + 'c', {'r': '%s%d' % (col_letter(c), rn)})
        st = self._default_style(c, row)
        if st:
            el.set('s', str(st))
        if before is not None:
            row.insert(list(row).index(before), el)
        else:
            row.append(el)
        return el

    def merge_of(self, r, c):
        for m in self.merges:
            if m[0] <= r <= m[2] and m[1] <= c <= m[3]:
                return m
        return None

    def text_of(self, el):
        '''单元格当前文字：共享字符串、内联字符串、数值都能读。'''
        t = el.get('t') or 'n'
        if t == 'inlineStr':
            node = el.find(S + 'is')
            return si_text(node) if node is not None else ''
        v = el.find(S + 'v')
        if v is None or v.text is None:
            return ''
        if t == 's':
            return self.sst.text(int(v.text))
        return v.text

    def write(self, r, c, value, draft=True, clear=False):
        '''写一个值（保留样式；草稿时换成浅黄底）。返回 {'ok', 'old', 'why'}；公式格、合并区非左上角都不写，并说明原因；
        空值只在 clear=True（人工 set 空值）时清除该格，否则不写。'''
        m = self.merge_of(r, c)
        if m and (m[0], m[1]) != (r, c):
            return {'ok': False, 'why': '位于合并区 %s%d:%s%d 内部，不是左上角，没有写' % (col_letter(m[1]), m[0] + 1, col_letter(m[3]), m[2] + 1)}
        if not value and not clear:
            return {'ok': False, 'why': '填入值为空，没有写'}
        el = self.cell(r, c)
        if el is not None and el.find(S + 'f') is not None:
            return {'ok': False, 'why': '该格是公式，没有覆盖'}
        if el is None:
            el = self.cell(r, c, create=True)
        old = self.text_of(el)
        s0 = int(el.get('s') or 0)
        ref = '%s%d' % (col_letter(c), r + 1)
        self.log.setdefault(ref, {'old': old, 'fill': '' if self.styles.is_draft(s0) else self.styles.fill_key(s0)})
        for ch in list(el):
            if ch.tag in (S + 'v', S + 'is'):
                el.remove(ch)
        el.attrib.pop('t', None)
        num = as_number(value) if value else None
        if not value:
            pass        # 清除：只去掉值，样式（边框等）保留
        elif num is not None and not is_text_fmt(self.styles, s0):
            ET.SubElement(el, S + 'v').text = num
        else:
            el.set('t', 's')
            ET.SubElement(el, S + 'v').text = str(self.sst.index(xml_text(value)))
        if draft and not self.styles.is_draft(s0):
            el.set('s', str(self.styles.draft_style(s0)))
        self.rows[r + 1].attrib.pop('spans', None)
        return {'ok': True, 'old': self.log[ref]['old']}

    def fix_dimension(self):
        rs, cs = [], []
        for rn, row in self.rows.items():
            for el in row.findall(S + 'c'):
                rs.append(rn)
                cs.append(split_ref(el.get('r'))[1])
        if not rs:
            return
        a = '%s%d' % (col_letter(min(cs)), min(rs))
        b = '%s%d' % (col_letter(max(cs)), max(rs))
        dim = self.root.find(S + 'dimension')
        if dim is None:
            dim = ET.Element(S + 'dimension')
            ws_insert(self.root, dim, 'dimension')
        dim.set('ref', a if a == b else a + ':' + b)

VML_HEAD = ('<xml xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office" '
            'xmlns:x="urn:schemas-microsoft-com:office:excel">\r\n'
            '<o:shapelayout v:ext="edit"><o:idmap v:ext="edit" data="%d"/></o:shapelayout>\r\n'
            '<v:shapetype id="_x0000_t202" coordsize="21600,21600" o:spt="202" path="m,l,21600r21600,l21600,xe">'
            '<v:stroke joinstyle="miter"/><v:path gradientshapeok="t" o:connecttype="rect"/></v:shapetype>\r\n')
VML_SHAPE = ('<v:shape id="_x0000_s%d" type="#_x0000_t202" style="position:absolute;margin-left:80pt;margin-top:2pt;'
             'width:220pt;height:%dpt;z-index:%d;visibility:hidden" fillcolor="#ffffe1" o:insetmode="auto">'
             '<v:fill color2="#ffffe1"/><v:shadow on="t" color="black" obscured="t"/><v:path o:connecttype="none"/>'
             '<v:textbox style="mso-direction-alt:auto"><div style="text-align:left"></div></v:textbox>'
             '<x:ClientData ObjectType="Note"><x:MoveWithCells/><x:SizeWithCells/>'
             '<x:Anchor>%d, 15, %d, 2, %d, 15, %d, 16</x:Anchor><x:AutoFill>False</x:AutoFill>'
             '<x:Row>%d</x:Row><x:Column>%d</x:Column></x:ClientData></v:shape>\r\n')


def _ensure_ns(pkg, part, prefix, uri):
    '''部件根元素上补一个命名空间前缀声明（sheet 根上原来没有 xmlns:r 时，新加 r:id 属性要用）。'''
    pkg.xml(part)
    decl = pkg._ns.setdefault(part, [])
    if not any(p == prefix for p, _u in decl):
        decl.append((prefix, uri))


def _rel_to(from_part, to_part):
    return posixpath.relpath(to_part, posixpath.dirname(from_part))


def _free_part(pkg, fmt):
    n = 1
    while pkg.has(fmt % n):
        n += 1
    return fmt % n


def _comment_el(cl, ref, aid, text):
    cm = ET.SubElement(cl, S + 'comment', {'ref': ref, 'authorId': str(aid)})
    tx = ET.SubElement(cm, S + 'text')
    r1 = ET.SubElement(tx, S + 'r')
    rp = ET.SubElement(r1, S + 'rPr')
    ET.SubElement(rp, S + 'b')
    ET.SubElement(rp, S + 'sz', {'val': '9'})
    ET.SubElement(rp, S + 'color', {'indexed': '81'})
    ET.SubElement(rp, S + 'rFont', {'val': 'Tahoma'})
    ET.SubElement(r1, S + 't').text = TOOL_AUTHOR + ':'
    r2 = ET.SubElement(tx, S + 'r')
    rp2 = ET.SubElement(r2, S + 'rPr')
    ET.SubElement(rp2, S + 'sz', {'val': '9'})
    ET.SubElement(rp2, S + 'color', {'indexed': '81'})
    ET.SubElement(rp2, S + 'rFont', {'val': 'Tahoma'})
    t = ET.SubElement(r2, S + 't')
    t.text = '\n' + xml_text(text)[:2000]
    t.set('{%s}space' % XML_NS, 'preserve')


def add_comments(pkg, sh, items, sheet_no):
    '''在工作表 sh 上加旧式批注（comments + vmlDrawing，Excel 2007 以上和 WPS 都认）。items: [(行, 列, 文字)]，行列从 0 起。
    返回 (已加的引用, 因该格已有批注而没加的引用)；已有批注的格（模板里的编制要求）不动，说明写在“审核说明”表里。'''
    rels = pkg.rels(sh.part)
    cpart = vpart = None
    for _i, ty, tg, mode in rels.items():
        if ty == RT_COMMENTS and mode != 'External':
            cpart = pkg.resolve(sh.part, tg)
    ld = sh.root.find(S + 'legacyDrawing')
    if ld is not None:
        tg, mode = rels.target(ld.get(RNS + 'id'))
        if tg and mode != 'External':
            vpart = pkg.resolve(sh.part, tg)
    if cpart and pkg.has(cpart):
        croot = pkg.xml(cpart)
    else:
        cpart = _free_part(pkg, 'xl/comments%d.xml')
        croot = ET.Element(S + 'comments')
        pkg.new_xml(cpart, croot, [('', NS_MAIN)])
        rels.add(RT_COMMENTS, _rel_to(sh.part, cpart))
        pkg.ensure_override(cpart, CT_COMMENTS)
    au = croot.find(S + 'authors')
    if au is None:
        au = ET.Element(S + 'authors')
        croot.insert(0, au)
    cl = croot.find(S + 'commentList')
    if cl is None:
        cl = ET.SubElement(croot, S + 'commentList')
    names = [(a.text or '') for a in au]
    if TOOL_AUTHOR not in names:
        ET.SubElement(au, S + 'author').text = TOOL_AUTHOR
        names.append(TOOL_AUTHOR)
    aid = names.index(TOOL_AUTHOR)
    have = {cm.get('ref') for cm in cl}
    ref_of = lambda r, c: '%s%d' % (col_letter(c), r + 1)
    new = [(r, c, t) for r, c, t in items if ref_of(r, c) not in have]
    skipped = [ref_of(r, c) for r, c, _t in items if ref_of(r, c) in have]
    if not new:
        return [], skipped
    if vpart and pkg.has(vpart):
        vtext = pkg.parts[vpart].decode('utf-8', 'replace')
        ids = [int(x) for x in re.findall(r'_x0000_s(\d+)', vtext)]
        mm = re.search(r'o:idmap[^>]*data="(\d+)', vtext)
        base = max(ids + [1024 * (int(mm.group(1)) if mm else sheet_no + 1)])
    else:
        vpart = _free_part(pkg, 'xl/drawings/vmlDrawing%d.vml')
        data = sheet_no + 1
        vtext = VML_HEAD % data + '</xml>'
        base = 1024 * data
        rid = rels.add(RT_VML, _rel_to(sh.part, vpart))
        _ensure_ns(pkg, sh.part, 'r', RNS[1:-1])
        ws_insert(sh.root, ET.Element(S + 'legacyDrawing', {RNS + 'id': rid}), 'legacyDrawing')
        pkg.ensure_default('vml', CT_VML)
    shapes = []
    for k, (r, c, t) in enumerate(new, 1):
        _comment_el(cl, ref_of(r, c), aid, t)
        h = min(300, 12 * (t.count('\n') + 2 + len(t) // 28) + 16)
        top = max(r - 1, 0)
        shapes.append(VML_SHAPE % (base + k, h, k, c + 1, top, c + 4, top + h // 15 + 1, r, c))
    i = vtext.rfind('</xml>')
    if i < 0:
        raise DIError('工作表 %s 的 vmlDrawing 结构异常（没有结束标记），无法加批注' % sh.name)
    pkg.add_part(vpart, (vtext[:i] + ''.join(shapes) + vtext[i:]).encode('utf-8'))
    return [ref_of(r, c) for r, c, _t in new], skipped

# ---------------------------------------------------------------- 工作簿层：部件增删、计算设置
RT_WS = RT_PREFIX + 'worksheet'
RT_CALCCHAIN = RT_PREFIX + 'calcChain'
CT_WS = 'application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml'
WB_ORDER = ['fileVersion', 'fileSharing', 'workbookPr', 'workbookProtection', 'bookViews', 'sheets', 'functionGroups',
            'externalReferences', 'definedNames', 'calcPr', 'oleSize', 'customWorkbookViews', 'pivotCaches', 'smartTagPr',
            'smartTagTypes', 'webPublishing', 'fileRecoveryPr', 'webPublishObjects', 'extLst']


def wb_child(wb, local, create=True):
    el = wb.find(S + local)
    if el is None and create:
        el = ET.Element(S + local)
        idx = WB_ORDER.index(local)
        pos = len(wb)
        for i, ch in enumerate(wb):
            ln = ch.tag.split('}')[-1]
            if ln in WB_ORDER and WB_ORDER.index(ln) > idx:
                pos = i
                break
        wb.insert(pos, el)
    return el


def drop_part(pkg, part):
    '''从包里删掉一个部件（连同它的关系文件和内容类型登记）。'''
    for name in (part, pkg.rels_name(part)):
        pkg.parts.pop(name, None)
        pkg._xml.pop(name, None)
        pkg._rels.pop(name, None)
        if name in pkg.order:
            pkg.order.remove(name)
    ct = pkg.xml('[Content_Types].xml')
    for el in list(ct):
        if el.get('PartName') == '/' + part:
            ct.remove(el)


def drop_calc_chain(pkg):
    '''calcChain 只是计算顺序的提示；增删公式格或工作表后，旧的会让 Excel 报“发现不可读取的内容”。去掉它，Excel 打开时自己重建。'''
    rels = pkg.rels('xl/workbook.xml')
    for el in list(rels.root):
        if el.get('Type') == RT_CALCCHAIN:
            rels.root.remove(el)
            rels.dirty = True
            drop_part(pkg, pkg.resolve('xl/workbook.xml', el.get('Target')))


def full_calc_on_load(pkg):
    '''打开时全部重算：依赖本次写入格的公式缓存值已清掉，Excel/WPS 打开时按新值重算。'''
    wb_child(pkg.xml('xl/workbook.xml'), 'calcPr').set('fullCalcOnLoad', '1')


# ---------------------------------------------------------------- 依赖本次写入格的公式：清掉过期的缓存值
# ---------------------------------------------------------------- 审核说明表
REVIEW_COLS = [('序号', 6), ('单元格', 13), ('填入值', 18), ('来源', 34), ('方式', 22), ('原始值', 22), ('检查', 18),
               ('原有内容', 14), ('说明', 56)]


class ReviewStyles:
    '''审核说明表用到的几种样式（标题/表头/正文/链接/警示/分节），追加到 styles.xml，不动原有样式。'''

    def __init__(self, styles):
        self.st = styles
        self.fonts = styles.root.find(S + 'fonts')
        base = self._font()
        bold = self._font(bold=True)
        link = self._font(underline=True, color='FF0563C1')
        red = self._font(color='FFC00000')
        gray = styles._fill_index(self._solid('FFD9D9D9'))
        orange = styles._fill_index(self._solid('FFFCE4D6'))
        self.title = self._xf(bold)
        self.head = self._xf(bold, gray)
        self.body = self._xf(base)
        self.link = self._xf(link)
        self.warn = self._xf(red)
        self.sec = self._xf(bold, orange)

    @staticmethod
    def _solid(rgb):
        f = ET.Element(S + 'fill')
        p = _sub(f, 'patternFill', patternType='solid')
        _sub(p, 'fgColor', rgb=rgb)
        _sub(p, 'bgColor', indexed=64)
        return f

    def _font(self, bold=False, underline=False, color=None):
        f = copy.deepcopy(list(self.fonts)[0]) if len(self.fonts) else ET.Element(S + 'font')
        f.tail = None
        for ch in list(f):
            if ch.tag in (S + 'b', S + 'i', S + 'u', S + 'strike', S + 'color', S + 'vertAlign'):
                f.remove(ch)
        pos = 0
        if bold:
            f.insert(pos, ET.Element(S + 'b'))
            pos += 1
        if underline:
            f.insert(pos, ET.Element(S + 'u'))
        if color:
            nm = f.find(S + 'name')
            f.insert(list(f).index(nm) if nm is not None else len(f), ET.Element(S + 'color', {'rgb': color}))
        k = _key(f)
        for i, x in enumerate(self.fonts):
            if _key(x) == k:
                return i
        self.fonts.append(f)
        self.fonts.set('count', str(len(self.fonts)))
        return len(self.fonts) - 1

    def _xf(self, font, fill=0):
        xf = ET.Element(S + 'xf', {'numFmtId': '0', 'fontId': str(font), 'fillId': str(fill), 'borderId': '0', 'xfId': '0',
                                   'applyFont': '1', 'applyAlignment': '1'})
        if fill:
            xf.set('applyFill', '1')
        _sub(xf, 'alignment', vertical='top', wrapText=1)
        k = _key(xf)
        for i, x in enumerate(self.st.xfs):
            if _key(x) == k:
                return i
        self.st.xfs.append(xf)
        self.st.xfs.set('count', str(len(self.st.xfs)))
        return len(self.st.xfs) - 1


def fq(s):
    '''公式里的字符串常量：双引号加倍；Excel 规定每个常量不超过 255 个字符。'''
    return '"%s"' % s.replace('"', '""')[:255]


def link_cell(target, friendly):
    '''HYPERLINK 公式格：(公式, 缓存显示值)。目标太长（超过 Excel 255 字符的限制）时退回纯文字。'''
    if len(target) > 255:
        return None, friendly
    return 'HYPERLINK(%s,%s)' % (fq(target), fq(friendly[:200])), friendly[:200]


def _width(s):
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in s)


def add_review_sheet(pkg, styles, rows, title):
    '''在工作簿末尾加“审核说明”工作表。rows：[[cell…]]，cell 为 None 或 {'v': 文字, 'f': 公式, 's': 样式键}；
    文字一律用内联字符串（不进共享字符串表：定稿删表时不留残余）。返回实际用的表名。'''
    rs = ReviewStyles(styles)
    wb = pkg.xml('xl/workbook.xml')
    sheets = wb_child(wb, 'sheets')
    names = {sh.get('name') for sh in sheets}
    name, k = REVIEW_SHEET, 2
    while name in names:
        name, k = '%s%d' % (REVIEW_SHEET, k), k + 1
    part = _free_part(pkg, 'xl/worksheets/sheet%d.xml')
    rid = pkg.rels('xl/workbook.xml').add(RT_WS, _rel_to('xl/workbook.xml', part))
    _ensure_ns(pkg, 'xl/workbook.xml', 'r', RNS[1:-1])
    sid = max([int(sh.get('sheetId') or 0) for sh in sheets] + [0]) + 1
    ET.SubElement(sheets, S + 'sheet', {'name': name, 'sheetId': str(sid), RNS + 'id': rid})
    pkg.ensure_override(part, CT_WS)
    ncol = len(REVIEW_COLS)
    root = ET.Element(S + 'worksheet')
    _sub(_sub(root, 'sheetPr'), 'tabColor', rgb='FFFFC000')
    _sub(root, 'dimension', ref='A1:%s%d' % (col_letter(ncol - 1), len(rows) + 2))
    sv = _sub(_sub(root, 'sheetViews'), 'sheetView', workbookViewId=0)
    _sub(sv, 'pane', ySplit=2, topLeftCell='A3', activePane='bottomLeft', state='frozen')
    _sub(sv, 'selection', pane='bottomLeft', activeCell='A3', sqref='A3')
    _sub(root, 'sheetFormatPr', defaultRowHeight=15)
    cols = _sub(root, 'cols')
    for i, (_h, wd) in enumerate(REVIEW_COLS, 1):
        _sub(cols, 'col', min=i, max=i, width=wd, customWidth=1)
    data = _sub(root, 'sheetData')
    allrows = [[{'v': title, 's': 'title'}], [{'v': h, 's': 'head'} for h, _w in REVIEW_COLS]] + rows
    for rn, row in enumerate(allrows, 1):
        lines = 1
        rel = _sub(data, 'row', r=rn)
        for cn, cell in enumerate(row):
            if not cell or (cell.get('v') in (None, '') and not cell.get('f')):
                continue
            ref = '%s%d' % (col_letter(cn), rn)
            v = xml_text(str(cell.get('v') or ''))[:3000]
            st = getattr(rs, cell.get('s') or ('link' if cell.get('f') else 'body'))
            el = _sub(rel, 'c', r=ref, s=st)
            if cell.get('f'):
                el.set('t', 'str')
                ET.SubElement(el, S + 'f').text = cell['f']
                ET.SubElement(el, S + 'v').text = v
            else:
                el.set('t', 'inlineStr')
                t = ET.SubElement(ET.SubElement(el, S + 'is'), S + 't')
                t.text = v
                t.set('{%s}space' % XML_NS, 'preserve')
            wd = REVIEW_COLS[cn][1] if rn > 1 else sum(w_ for _h, w_ in REVIEW_COLS)
            lines = max(lines, sum(-(-_width(x) // max(1, int(wd * 0.95))) or 1 for x in v.split('\n')))
        if lines > 1:
            rel.set('ht', str(min(15 * lines, 150)))
            rel.set('customHeight', '1')
    _sub(_sub(root, 'mergeCells', count=1), 'mergeCell', ref='A1:%s1' % col_letter(ncol - 1))
    _sub(root, 'pageMargins', left=0.7, right=0.7, top=0.75, bottom=0.75, header=0.3, footer=0.3)
    pkg.new_xml(part, root, [('', NS_MAIN), ('r', RNS[1:-1])])
    return name

# ---------------------------------------------------------------- 写入器
WRITE_OPS = ('set_cell', 'set_text', 'append_runs', 'fill_blanks')


def runs_text(runs):
    '''runs -> 纯文字。Excel 单元格不保留上下标（V_OH 写成 VOH），图片占位不写。'''
    return ''.join(str(r[0]) for r in (runs or []) if not str(r[1]).startswith('@'))


def fill_blanks_text(text, vals):
    '''“  只合格，  只不合格”里的空白依次填上数字（与 Word 目标的写法相同）。'''
    out, pos, k = [], 0, 0
    for m in re.finditer(r'[ 　]{2,}|_{3,}', text):
        if k >= len(vals):
            break
        out.append(text[pos:m.start()].rstrip(' ') + ' ' + vals[k] + ' ')
        pos = m.end()
        k += 1
    out.append(text[pos:].lstrip(' ') if k else text[pos:])
    return ''.join(out)


def list_validations(root):
    '''下拉列表型数据验证（选项直接写在公式里的）：[(区域列表, 选项集合)]。'''
    out = []
    for dv in root.iter(S + 'dataValidation'):
        f1 = dv.find(S + 'formula1')
        if dv.get('type') != 'list' or f1 is None or not (f1.text or '').startswith('"'):
            continue
        opts = {x.strip() for x in f1.text.strip('"').split(',')}
        rngs = []
        for part in (dv.get('sqref') or '').split():
            a, _, b = part.partition(':')
            try:
                rngs.append(split_ref(a) + split_ref(b or a))
            except DIError:
                pass
        out.append((rngs, opts))
    return out


class XlsxWriter:
    '''把规划的操作写进 Excel 目标的副本，生成草稿：写值（保留样式/合并/公式/数据验证/定义名称）、被填格浅黄底、
    旧式批注、“审核说明”工作表（HYPERLINK 指向本格和源文件快照）。'''

    def __init__(self, job, work, draft_path):
        from .write_docx import Snapshots
        self.job, self.work, self.draft = job, work, draft_path
        self.pkg = Package(work)
        if not self.pkg.has('xl/workbook.xml'):
            raise DIError('目标不是有效的 xlsx（缺少 xl/workbook.xml）：%s' % os.path.basename(work))
        self.styles = Styles(self.pkg)
        self.sst = SST(self.pkg)
        self.metas = sheet_parts(self.pkg)
        self.snap = Snapshots(job, os.path.dirname(draft_path))
        self._sheets, self._dv = {}, {}
        self.warnings = []
        self.writes, self.order = {}, []

    # ------------------------------------------------------------ 定位
    def sheet(self, idx):
        if idx not in self._sheets:
            m = self.metas[idx] if 0 <= idx < len(self.metas) else None
            if not m or not m['part'] or not self.pkg.has(m['part']):
                return None
            self._sheets[idx] = Sheet(self.pkg, m['part'], m['name'], self.styles, self.sst)
        return self._sheets[idx]

    def xref(self, cid):
        s, r, c = cid_parse(cid)
        name = self.metas[s]['name'] if 0 <= s < len(self.metas) else '?'
        return '%s!%s%d' % (name, col_letter(c), r + 1)

    def jump(self, cid):
        '''审核说明表里跳到本格的链接。'''
        s, r, c = cid_parse(cid)
        name = self.metas[s]['name'] if 0 <= s < len(self.metas) else ''
        f, v = link_cell("#'%s'!%s%d" % (name.replace("'", "''"), col_letter(c), r + 1), self.xref(cid))
        return {'f': f, 'v': v} if f else {'v': v}

    def src_link(self, alias, ref):
        '''来源链接：xlsx 源定位到单元格区域，docx 源定位到书签（快照里会建好），其余只打开文件。'''
        try:
            rel, anchor, _tip = self.snap.link(alias, ref)
            md = self.job.model(alias).m
        except DIError:
            return None
        path = rel.replace('/', chr(92))
        loc = None
        if md['type'] == 'xlsx' and anchor and '!' in anchor:
            sh, _, rng = anchor.rpartition('!')
            loc = "'%s'!%s" % (sh.strip("'").replace("'", "''"), rng)
        elif md['type'] == 'docx' and anchor:
            loc = anchor
        f, v = link_cell(path + ('#' + loc if loc else ''), '%s %s:%s' % (os.path.basename(md['path']), alias, ref))
        return {'f': f, 'v': v} if f else {'v': v}

    # ------------------------------------------------------------ 写值
    def value_for(self, o, sh, r, c):
        kind = o['op']
        if kind == 'set_cell':
            return runs_text(o['runs']) if o.get('runs') is not None else str(o.get('text', ''))
        if kind == 'set_text':
            return str(o.get('text', ''))
        el = sh.cell(r, c)
        old = sh.text_of(el) if el is not None else ''
        if kind == 'append_runs':
            return old + runs_text(o.get('runs'))
        return fill_blanks_text(old, [str(x) for x in o.get('values', [])])

    def check_dv(self, s, sh, r, c, value):
        if s not in self._dv:
            self._dv[s] = list_validations(sh.root)
        for rngs, opts in self._dv[s]:
            if any(a <= r <= b and d <= c <= e for a, d, b, e in rngs) and value not in opts:
                return '⚠ 不在该格的下拉选项（%s）里' % '/'.join(sorted(opts)[:6])
        return ''

    def write_op(self, o):
        cid = o.get('cell') or o.get('block') or ''
        try:
            s, r, c = cid_parse(cid)
        except DIError:
            self.warnings.append('找不到单元格 %s（Excel 目标的位置写成 T0.R2.C1 或 Sheet1!B3）' % cid)
            return
        sh = self.sheet(s)
        if sh is None:
            self.warnings.append('找不到工作表（%s）' % cid)
            return
        value = self.value_for(o, sh, r, c)
        clear = value == '' and o['op'] in ('set_cell', 'set_text')      # 人工 set 空值：清除这一格
        res = sh.write(r, c, value, clear=clear)
        rec = self.writes.get(cid)
        if rec is None:
            self.order.append(cid)
        if res['ok']:
            self.writes[cid] = {'value': value, 'old': res['old'] if not (rec and rec.get('ok')) else rec['old'], 'op': o,
                                'ok': True, 'dv': self.check_dv(s, sh, r, c, value) if value else ''}
        elif not (rec and rec.get('ok')):
            self.writes[cid] = {'value': value, 'old': '', 'op': o, 'ok': False, 'why': res['why']}
            self.warnings.append('%s 没有写入：%s' % (self.xref(cid), res['why']))

    # ------------------------------------------------------------ 审核说明
    def review_rows(self, plan, unv, pend):
        fills = {}
        for f in plan['report'].get('fills', []):
            fills.setdefault(f.get('target', ''), f)
        glinks, alinks, ctext = {}, {}, {}
        for o in plan['ops']:
            if o['op'] != 'comment':
                continue
            if o.get('group') and o.get('links'):
                glinks.setdefault((o['anchor'].split('.')[0], o['group']), o['links'])
            if o.get('links'):
                alinks.setdefault(o['anchor'], o['links'])
        rows, rowno = [], {}
        for cid in self.order:
            w = self.writes[cid]
            o = w['op']
            tid, _r, _c = cid_parse(cid)
            r = _r
            g = o.get('group') or ''
            f = fills.get(cid) or (fills.get('T%d 第%d行' % (tid, r)) if g.startswith('spec:') else None) or {}
            link = None
            m = re.match(r'^(S\d+):(.+)$', f.get('source') or '')
            if m:
                link = self.src_link(m.group(1), m.group(2).split(';')[0])
            if link is None and o.get('src'):
                link = self.src_link(o['src'][0].get('alias'), o['src'][0].get('ref'))
            if link is None:
                lk = glinks.get(('T%d' % tid, g)) if g else None
                lk = lk or alinks.get(cid)
                if lk:
                    link = self.src_link(lk[0].get('alias'), lk[0].get('ref'))
            how = f.get('how') or {'set_text': '人工指定' if not g else '字段', 'append_runs': '表头说明（取自源表表头）',
                                   'fill_blanks': '派生'}.get(o['op'], '规范表逐行复制' if g.startswith('spec:') else '人工指定')
            chk = f.get('check') or ''
            if not w['ok']:
                chk = '⚠ 没有写入：' + w.get('why', '')
            elif w.get('dv'):
                chk = (chk + '；' if chk else '') + w['dv']
            old = w.get('old') or ''
            note = '原有“%s”已被替换' % short(old, 30) if (w['ok'] and old.strip() and old != w['value']) else ''
            rowno[cid] = len(rows) + 3
            rows.append([{'v': str(len(rowno))}, self.jump(cid), {'v': w['value'] if w['ok'] else '（未写入）'},
                         link or {'v': f.get('source') or '—'}, {'v': how}, {'v': f.get('raw') or ''},
                         {'v': chk, 's': 'warn' if ('⚠' in chk or (chk and chk not in ('ok', '通过'))) else 'body'},
                         {'v': old}, {'v': note, 's': 'warn' if note else 'body'}])
        stale = [x for x in unv if x.get('kind') == 'stale']
        other = [x for x in unv if x.get('kind') in ('source', 'unread')]
        if stale or other:
            rows.append([{'v': '本批未核（定稿前必须处理：清除或改正这些格，或回答对应问题；旧值确认仍适用才可带着定稿）', 's': 'sec'}])
            for x in stale:
                try:
                    j = self.jump(x['cell'])
                except DIError:
                    j = {'v': x.get('cell', '')}
                rows.append([{'v': '未核'}, j, {'v': ''}, {'v': ''}, {'v': ''}, {'v': ''},
                             {'v': '⚠ ' + (x.get('why') or '原有值，本批未核'), 's': 'warn'}, {'v': x.get('text', '')},
                             {'v': '%s %s' % (x.get('param', ''), ('样品%s#' % x['sample']) if x.get('sample') else '')}])
            for x in other:
                rows.append([{'v': '未核'}, None, None, None, None, None, {'v': '⚠ 未核', 's': 'warn'}, None,
                             {'v': x.get('detail') or x.get('name', '')}])
        if pend:
            rows.append([{'v': '按推荐默认处理（不等于已确认；用户正式回答前 final 会拦截）', 's': 'sec'}])
            for q in pend:
                rows.append([{'v': '默认'}] + [None] * 5 + [{'v': '⚠ 待确认', 's': 'warn'}, None,
                                                           {'v': '%s → %s' % (short(q['question'], 80), q['options'][q.get('recommended', 0)]['label'])}])
        rep = plan['report']
        if rep.get('unfilled'):
            rows.append([{'v': '未填项（需人工补充）', 's': 'sec'}])
            rows += [[{'v': '未填'}] + [None] * 7 + [{'v': x}] for x in rep['unfilled']]
        if rep.get('warnings') or self.warnings:
            rows.append([{'v': '警告', 's': 'sec'}])
            rows += [[{'v': '注意'}] + [None] * 7 + [{'v': x}] for x in list(rep.get('warnings', [])) + self.warnings]
        return rows, rowno

    # ------------------------------------------------------------ 批注
    def comments(self, plan, rowno):
        buf, order = {}, []
        for o in plan['ops']:
            if o['op'] != 'comment':
                continue
            a = o.get('anchor', '')
            if a not in buf:
                buf[a] = {'text': [], 'links': [], 'warn': False}
                order.append(a)
            buf[a]['text'].append(o.get('text', ''))
            buf[a]['links'] += o.get('links', [])
            buf[a]['warn'] = buf[a]['warn'] or bool(o.get('warn'))
        for cid in self.order:      # 覆盖了原有内容的格：没有别的批注也要说明，不能静默替换
            w = self.writes[cid]
            if w['ok'] and (w.get('old') or '').strip() and w['old'] != w['value'] and cid not in buf:
                buf[cid] = {'text': ['【替换】原有“%s”已替换为“%s”' % (short(w['old'], 30), short(w['value'], 30))], 'links': [], 'warn': False}
                order.append(cid)
        per = {}
        for a in order:
            try:
                s, r, c = cid_parse(a)
            except DIError:
                self.warnings.append('批注锚点不存在 %s' % a)
                continue
            b = buf[a]
            text = '\n'.join(t for t in b['text'] if t)
            w = self.writes.get(a)
            if w and w['ok'] and (w.get('old') or '').strip() and w['old'] != w['value'] and '【替换】' not in text:
                text += '\n原有“%s”已替换为本批的值' % short(w['old'], 30)
            if b['links']:
                text += '\n来源：' + '；'.join('%s %s:%s' % (x.get('label', ''), x.get('alias', ''), x.get('ref', ''))
                                             for x in b['links'][:4]) + ('…' if len(b['links']) > 4 else '')
            text += '\n可点击的来源链接在“审核说明”表%s' % (('第 %d 行' % rowno[a]) if a in rowno else '')
            per.setdefault(s, []).append((r, c, ('⚠' if b['warn'] and not text.startswith('⚠') else '') + text))
        skipped = []
        for s, items in sorted(per.items()):
            sh = self.sheet(s)
            if sh is None:
                self.warnings.append('批注所在工作表不存在（T%d）' % s)
                continue
            _added, sk = add_comments(self.pkg, sh, items, s)
            skipped += ['%s!%s' % (sh.name, x) for x in sk]
        if skipped:
            self.warnings.append('%d 个格原来就有批注（模板的编制要求），助手说明没有写进批注，只写在“审核说明”表：%s' % (
                len(skipped), '、'.join(skipped[:5])))

    # ------------------------------------------------------------ 执行
    def apply(self, plan, unv, pend, ver):
        for o in plan['ops']:
            if o['op'] not in WRITE_OPS + ('comment',):
                self.warnings.append('Excel 目标不支持操作 %s（%s），已跳过' % (o['op'], o.get('cell') or o.get('anchor') or ''))
        order = {'set_cell': 1, 'set_text': 1, 'append_runs': 2, 'fill_blanks': 2}
        for o in sorted([o for o in plan['ops'] if o['op'] in WRITE_OPS], key=lambda o: order[o['op']]):
            self.write_op(o)
        rows, rowno = self.review_rows(plan, unv, pend)
        self.comments(plan, rowno)
        written = {}
        for cid in self.order:
            if self.writes[cid]['ok']:
                s, r, c = cid_parse(cid)
                written.setdefault(self.metas[s]['name'], set()).add((r, c))
        ninv = invalidate_dependents(self.pkg, self.metas, written)
        if ninv:
            full_calc_on_load(self.pkg)
            self.warnings.append('%d 个公式引用了本次填写的格，旧的计算结果已清掉，打开时重新计算' % ninv)
        for sh in self._sheets.values():
            sh.fix_dimension()
        self.sst.finish()
        title = ('审核说明（草稿 v%d）：黄色底=本次填写；点“单元格”跳到该格，点“来源”打开源文件快照的对应位置（Excel 源定位到单元格区域，'
                 'Word 源定位到书签）；带⚠的要重点核对。定稿时底色、助手批注和本表会自动删除。') % ver
        review = add_review_sheet(self.pkg, self.styles, rows, title)
        drop_calc_chain(self.pkg)
        self.snap.build()
        self.warnings += list(getattr(self.snap, 'warns', []) or [])
        self.pkg.save(self.draft)
        fills = {}
        for sh in self._sheets.values():
            for ref, lg in sh.log.items():
                fills['%s!%s' % (sh.name, ref)] = lg['fill']
        tmpl = []
        try:
            for s in read_workbook(self.work, self.job.cache)['sheets']:
                for ref, cm in (s.get('comments') or {}).items():
                    tmpl.append([s['name'], ref, cm['author'], cm['text']])
        except DIError as e:
            self.warnings.append('读取模板原有批注失败（%s）：定稿时无法区分模板批注和审核人批注' % e)
        return {'review_sheet': review, 'fills': fills, 'template_comments': tmpl,
                'written': {self.xref(c): self.writes[c]['value'] for c in self.order if self.writes[c]['ok']},
                'replaced': sum(1 for c in self.order if self.writes[c]['ok'] and (self.writes[c].get('old') or '').strip()
                                and self.writes[c]['old'] != self.writes[c]['value'])}


def readback(path, written):
    '''草稿回读：计划写入的格，草稿里真的是这个值（写入失败、被后面的操作覆盖都会暴露）。返回不一致的登记项。'''
    from .plan_xlsx import _parse
    got = {}
    for s in _parse(path, '')['sheets']:
        for ref, v in (s.get('cells') or {}).items():
            got['%s!%s' % (s['name'], ref)] = v[1]
    bad = []
    for x, want in written.items():
        have = got.get(x, '')
        if not same_text(have, want):
            bad.append({'kind': 'source', 'verify': True, 'key': 'verify:' + x, 'name': x,
                        'detail': '草稿回读不一致：%s 计划写入“%s”，草稿里实际是“%s”' % (x, short(want, 20), short(have, 20))})
    return bad


def same_text(a, b):
    '''两个单元格值是否相同：文字相同，或都是数且相等（Excel 另存后 1.25 与 1.2500000000000001 这类写法差异不算改动）。'''
    if (a or '') == (b or ''):
        return True
    try:
        x, y = float(a), float(b)
    except (TypeError, ValueError):
        return False
    return abs(x - y) <= 1e-9 * max(1.0, abs(x), abs(y))

# @@SEC:final@@
