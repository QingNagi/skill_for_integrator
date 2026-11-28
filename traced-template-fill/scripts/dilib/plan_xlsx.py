# -*- coding: utf-8 -*-
'''Excel 目标的规划（骨架，分段填充）。

解析目标工作簿（单元格/合并/批注/样式）-> 识别“标签+右侧空格”字段与空白数据区 -> 复用 Planner 的配方（规范表、测试数据、字段）出填写操作。
操作里的位置沿用 docx 的写法 T<工作表序号>.R<行>.C<列>（均从0起），由 write_xlsx 写回 sheet XML。'''
from __future__ import annotations

import json
import os
import re
import zipfile

from .ooxml import validate_package_limits
from .safe_xml import parse_xml

from . import tablemap
from .common import TOOL_AUTHOR, DIError, ensure_dir, file_sha1, short
from .content import Model
from .planner import Planner
from .read_other import col_index, col_letter, si_text, split_ref
from .target import is_label

S = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
RNS = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
PKG_RELNS = '{http://schemas.openxmlformats.org/package/2006/relationships}'
PARSE_VERSION = 3
_MEM = {}
XLSX_TARGET_EXT = ('.xlsx', '.xlsm', '.xls', '.et', '.ett', '.ods', '.xlsb')


def is_xlsx_target(job):
    '''目标是 Excel（xlsx/xlsm，或要先经办公软件转换的 xls/et/ods/xlsb）。csv/tsv 不算：没有格式和批注，不能当目标写。'''
    return os.path.splitext(job.data['target']['path'])[1].lower() in XLSX_TARGET_EXT

import posixpath  # noqa: E402


def part_rels(z, names, part):
    '''某部件的关系：[(Id, Type, 内部部件名或外部目标, 是否外部)]。'''
    rn = posixpath.join(posixpath.dirname(part), '_rels', posixpath.basename(part) + '.rels')
    if rn not in names:
        return []
    out = []
    for e in parse_xml(z.read(rn)):
        tg = e.get('Target') or ''
        ext = e.get('TargetMode') == 'External'
        if not ext:
            tg = tg[1:] if tg.startswith('/') else posixpath.normpath(posixpath.join(posixpath.dirname(part), tg))
        out.append((e.get('Id'), e.get('Type') or '', tg, ext))
    return out


def comment_text(te, author):
    '''批注文字：Excel 会在开头加一段加粗的“作者:”，去掉它，只留正文。'''
    txt = si_text(te) if te is not None else ''
    a = (author or '').strip()
    if a and txt.lstrip().startswith(a):
        rest = txt.lstrip()[len(a):]
        if rest[:1] in (':', '：'):
            txt = rest[1:].lstrip('\r\n')
    return txt


def _parse_sheet(z, names, ss, part):
    x = parse_xml(z.read(part))
    cells, hrows, rn = {}, [], 0
    er = ec = -1      # 有边框/底色等样式的空格子也算版面范围（空白数据区常常只有边框没有内容）
    for row in x.iter(S + 'row'):
        rn = int(row.get('r') or rn + 1)
        if row.get('hidden') in ('1', 'true'):
            hrows.append(rn)
        cn = -1
        for c in row.findall(S + 'c'):
            ref = c.get('r')
            cn = split_ref(ref)[1] if ref else cn + 1
            ref = ref or '%s%d' % (col_letter(cn), rn)
            if c.get('s') not in (None, '0') or len(c):
                er, ec = max(er, rn - 1), max(ec, cn)
            t = c.get('t') or 'n'
            v, f = c.find(S + 'v'), c.find(S + 'f')
            if t == 'inlineStr':
                node = c.find(S + 'is')
                txt = si_text(node) if node is not None else ''
            elif v is None or v.text is None:
                txt = ''
            elif t == 's':
                k = int(v.text)
                txt = ss[k] if 0 <= k < len(ss) else ''
            elif t == 'b':
                txt = 'TRUE' if v.text.strip() == '1' else 'FALSE'
            else:
                txt = v.text
            if txt != '' or f is not None:
                cells[ref] = [t, txt, int(c.get('s') or 0), 1 if f is not None else 0]
    hcols = [[int(c.get('min')), int(c.get('max'))] for c in x.iter(S + 'col') if c.get('hidden') in ('1', 'true')]
    rels = part_rels(z, names, part)
    comments = {}
    for _i, ty, tg, ext in rels:
        if ty.endswith('/comments') and not ext and tg in names:
            cr = parse_xml(z.read(tg))
            authors = [(a.text or '') for a in cr.iter(S + 'author')]
            for cm in cr.iter(S + 'comment'):
                ai = int(cm.get('authorId') or 0)
                au = authors[ai] if ai < len(authors) else ''
                comments[cm.get('ref')] = {'author': au, 'text': comment_text(cm.find(S + 'text'), au)}
    return {'part': part, 'cells': cells, 'merges': [m.get('ref') for m in x.iter(S + 'mergeCell')], 'ext': [er, ec],
            'hidden_rows': hrows, 'hidden_cols': hcols, 'comments': comments,
            'validations': sum(1 for _ in x.iter(S + 'dataValidation')),
            'rels': [[ty, tg] for _i, ty, tg, ext in rels]}


def _parse(path, sha):
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise DIError('不是有效的 xlsx（压缩包损坏）：%s' % os.path.basename(path))
    try:
        validate_package_limits(z)
        names = set(z.namelist())
        if 'xl/workbook.xml' not in names:
            raise DIError('不是有效的 xlsx（缺少 xl/workbook.xml）：%s' % os.path.basename(path))
        wbrels = {i: (ty, tg) for i, ty, tg, ext in part_rels(z, names, 'xl/workbook.xml')}
        sst = next((tg for ty, tg in wbrels.values() if ty.endswith('/sharedStrings')), None)
        ss = []
        if sst and sst in names:
            ss = [si_text(si) for si in parse_xml(z.read(sst)).findall(S + 'si')]
        wb = parse_xml(z.read('xl/workbook.xml'))
        shs = wb.find(S + 'sheets')
        sheets = []
        for sh in (list(shs) if shs is not None else []):
            ty, tg = wbrels.get(sh.get(RNS + 'id'), ('', ''))
            d = {'name': sh.get('name'), 'state': sh.get('state') or 'visible',
                 'kind': 'sheet' if ty.endswith('/worksheet') and tg in names else 'other'}
            if d['kind'] == 'sheet':
                d.update(_parse_sheet(z, names, ss, tg))
            sheets.append(d)
        defined = [[dn.get('name'), (dn.text or '')[:200], dn.get('localSheetId')] for dn in wb.iter(S + 'definedName')]
        return {'_v': PARSE_VERSION, 'sha1': sha, 'sheets': sheets, 'defined_names': defined,
                'external': [tg for ty, tg in wbrels.values() if ty.endswith('/externalLink')]}
    finally:
        z.close()


def read_workbook(path, cache_dir=None):
    '''解析目标工作簿（单元格/合并/批注/隐藏行列/定义名称），按文件 sha1 缓存：同一文件不重复解析。'''
    sha = file_sha1(path)
    if sha in _MEM:
        return _MEM[sha]
    cp = os.path.join(ensure_dir(os.path.join(cache_dir, 'xlsx_target')), sha + '.json') if cache_dir else None
    if cp and os.path.exists(cp):
        try:
            with open(cp, encoding='utf-8') as f:
                d = json.load(f)
            if d.get('_v') == PARSE_VERSION:
                _MEM[sha] = d
                return d
        except (OSError, ValueError):
            pass
    d = _parse(path, sha)
    _MEM[sha] = d
    if cp:
        try:
            with open(cp + '.tmp', 'w', encoding='utf-8') as f:
                json.dump(d, f, ensure_ascii=False, separators=(',', ':'))
            os.replace(cp + '.tmp', cp)
        except OSError:
            pass
    return d


def target_xlsx(job):
    '''Excel 目标的可写 xlsx 路径。.xls/.et 等旧格式一律先经办公软件转成 xlsx（即使装了 xlrd 也不能直接写旧格式）。'''
    from . import office
    src = job.data['target']['path']
    if os.path.splitext(src)[1].lower() in ('.xlsx', '.xlsm'):
        return src
    try:
        return office.convert(src, 'xlsx', cache_dir=ensure_dir(os.path.join(job.cache, 'conv')))
    except DIError as e:
        raise DIError('Excel 目标是旧格式，需要办公软件（Excel/WPS/LibreOffice）先转成 xlsx，或请用户另存为 xlsx。%s' % e)

MAX_ROWS, MAX_COLS = 3000, 100
CID_RE = re.compile(r'^T(\d+)\.R(\d+)\.C(\d+)$')


def cid_parse(cid):
    '''Excel 目标的位置写法沿用 docx：T<工作表序号>.R<行>.C<列>，三个数都从 0 起。'''
    m = CID_RE.match(cid or '')
    if not m:
        raise DIError('不是 Excel 目标的单元格位置：%s' % cid)
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def cid_rc(cid):
    return cid_parse(cid)[1:]


def cid_ref(cid):
    '''位置 -> (工作表序号, 'B3')。'''
    s, r, c = cid_parse(cid)
    return s, '%s%d' % (col_letter(c), r + 1)


def ref_cid(sheet_idx, ref):
    r, c = split_ref(ref)
    return 'T%d.R%d.C%d' % (sheet_idx, r, c)


def sheet_grid(sh):
    '''工作表 -> docx 风格表格块：每个“原点”单元格一项 {r,c,text,rs,cs,f}，空格子也补上（否则认不出空白数据区和标签右侧的空格），
    合并区只留左上角；公式格带 f=1，且不算空。返回 (表格块, 是否被行列上限截断)。'''
    pos, merges, covered = {}, {}, set()
    mr = mc = -1
    for ref, v in sh['cells'].items():
        try:
            r, c = split_ref(ref)
        except DIError:
            continue
        pos[(r, c)] = v
        mr, mc = max(mr, r), max(mc, c)
    for m in sh['merges']:
        a, _, b = m.partition(':')
        try:
            r0, c0 = split_ref(a)
            r1, c1 = split_ref(b or a)
        except DIError:
            continue
        merges[(r0, c0)] = (r1, c1)
        mr, mc = max(mr, r1), max(mc, c1)
    ext = sh.get('ext') or [-1, -1]
    mr, mc = max(mr, ext[0]), max(mc, ext[1])
    cut = mr >= MAX_ROWS or mc >= MAX_COLS
    mr, mc = min(mr, MAX_ROWS - 1), min(mc, MAX_COLS - 1)
    for (r0, c0), (r1, c1) in merges.items():
        for r in range(r0, min(r1, mr) + 1):
            for c in range(c0, min(c1, mc) + 1):
                if (r, c) != (r0, c0):
                    covered.add((r, c))
    cells = []
    for r in range(mr + 1):
        for c in range(mc + 1):
            if (r, c) in covered:
                continue
            v = pos.get((r, c))
            txt = (v[1] if v else '') or ''
            f = bool(v and v[3])
            d = {'r': r, 'c': c, 'text': txt if (txt or not f) else '＝公式'}
            if (r, c) in merges:
                r1, c1 = merges[(r, c)]
                if r1 > r:
                    d['rs'] = min(r1, mr) - r + 1
                if c1 > c:
                    d['cs'] = min(c1, mc) - c + 1
            if f:
                d['f'] = 1
            cells.append(d)
    return {'t': 'tbl', 'nrows': mr + 1, 'ncols': mc + 1, 'hdr': 1, 'cells': cells}, cut


def _fields_in_rows(tb, rows, formula):
    '''与 target.analyze_table 相同的“标签+右侧空格”规则，只对指定的行重新找（被误判成数据区表头的表单行）。'''
    byrow = {}
    for c in tb['cells']:
        if c['r'] in rows:
            byrow.setdefault(c['r'], []).append(c)
    out = []
    for r in sorted(byrow):
        own = sorted(byrow[r], key=lambda c: c['c'])
        for i, c in enumerate(own):
            if (c['r'], c['c']) in formula or i + 1 >= len(own) or own[i + 1]['text'].strip() or not is_label(c['text']):
                continue
            nxt = own[i + 1]
            out.append({'label': re.sub(r'\s+', '', c['text']), 'cell': '%s.R%d.C%d' % (tb['id'], nxt['r'], nxt['c']),
                        'label_cell': '%s.R%d.C%d' % (tb['id'], c['r'], c['c'])})
    return out


def _refine(tb, an, sheet_comments, sheet_idx):
    '''Excel 版面与 Word 表格不同：分隔用的空行会被认成“空白数据区”，它上面的表单行被当成表头吞掉。
    列语义（参数/符号/最小/最大/样品号…）少于 2 列的“数据区”去掉，并对这些行重新找字段；
    空格子上的批注（编制要求）本身就说明了这里要填什么：没被认成字段的，用批注文字当标签补成字段。'''
    formula = {(c['r'], c['c']) for c in tb['cells'] if c.get('f')}
    keep, redo = [], set()
    for rg in an['regions']:
        if sum(1 for c in rg['cols'] if tablemap.role_of(c['path'])[0]) >= 2:
            keep.append(rg)
        else:
            redo.update(range(max(rg['header'][0], 0), rg['rows'][1] + 1))
    for k, rg in enumerate(keep):
        rg['id'] = '%s.D%d' % (tb['id'], k)
    an['regions'] = keep
    fields = [f for f in an['fields'] if cid_rc(f['label_cell']) not in formula]
    have = {f['cell'] for f in fields}
    used = set()
    for rg in keep:
        used.update(range(max(rg['header'][0], 0), rg['rows'][1] + 1))
    for f in _fields_in_rows(tb, redo - used, formula):
        if f['cell'] not in have:
            fields.append(f)
            have.add(f['cell'])
    texts = {(c['r'], c['c']): c['text'] for c in tb['cells']}
    for ref, cm in sheet_comments:
        r, c = split_ref(ref)
        cid = 'T%d.R%d.C%d' % (sheet_idx, r, c)
        if cid in have or (r, c) not in texts or texts[(r, c)].strip() or r in used:
            continue
        lab = re.sub(r'\s+', '', cm['text'])[:24]
        if lab:
            fields.append({'label': lab, 'cell': cid, 'label_cell': cid, 'req': True})
            have.add(cid)
    an['fields'] = fields
    return an


def analyze_workbook(wb, name=''):
    '''目标工作簿分析：每个可见工作表转成一个表格块 T<序号>，复用 target.analyze_table 找“标签+右侧空格”字段、空白数据区、
    空白待填、勾选框；Excel 批注当作编制要求（本工具自己写的批注不算）。超出行列上限没读全的工作表登记在 truncated。'''
    from .target import analyze_table
    blocks, tables, sheets, reqs, cut = [], [], [], [], []
    for i, sh in enumerate(wb['sheets']):
        info = {'name': sh['name'], 'index': i, 'fields': []}
        sheets.append(info)
        if sh['kind'] != 'sheet':
            info['skipped'] = '不是普通工作表（图表页/宏表），未处理'
            continue
        if sh['state'] != 'visible':
            info['skipped'] = '隐藏工作表，不填写'
            continue
        tb, truncated = sheet_grid(sh)
        tb['id'] = 'T%d' % i
        blocks.append(tb)
        if truncated:
            cut.append(sh['name'])
        cms = []
        for ref, cm in sorted(sh['comments'].items(), key=lambda kv: split_ref(kv[0])):
            if cm['author'] == TOOL_AUTHOR or not cm['text'].strip():
                continue
            cms.append((ref, cm))
            reqs.append({'sheet': sh['name'], 'cell': ref_cid(i, ref), 'ref': ref, 'text': cm['text'].strip(),
                         'author': cm['author']})
        an = _refine(tb, analyze_table(tb), cms, i)
        an['sheet'] = sh['name']
        tables.append(an)
        info['fields'] = [{'label': f['label'], 'cell': '%s!%s' % (sh['name'], cid_ref(f['cell'])[1])} for f in an['fields']]
    return {'name': name, 'type': 'xlsx', 'xtarget': True, 'comments': reqs, 'sections': [], 'tables': tables,
            'placeholders': [], 'sheets': sheets, 'truncated': cut, 'blocks': blocks}


def target_model(job):
    '''Excel 目标的 docx 风格模型（Planner 的表格/字段配方只认这种结构）。返回 (Model, 分析结果, 工作簿解析结果)。'''
    base = job.target_model().m
    path = target_xlsx(job)
    wb = read_workbook(path, job.cache)
    an = analyze_workbook(wb, base.get('name'))
    # _an：target.analyze 见到 xtarget 直接用它（已去掉空行误判的数据区、补上批注字段），不再按 Word 规则重算一遍
    m = {'type': 'xtarget', 'name': base.get('name'), 'title': base.get('title'), 'path': base.get('path'), 'work': path,
         'sha1': base.get('sha1'), 'blocks': an['blocks'], 'sections': [], 'figures': [], 'comments': [], 'stats': {},
         '_an': {'name': base.get('name'), 'type': 'xtarget', 'comments': [], 'sections': [], 'tables': an['tables'],
                 'placeholders': []}}
    return Model(m, 'T'), an, wb


XREF_RE = re.compile(r"^(?:'((?:[^']|'')+)'|([^!']+))!\$?([A-Za-z]{1,3})\$?(\d+)$")
RULE_KEYS_FROM_COMMENTS = ('cond_keep', 'multi', 'range_sep', 'decimals')

class _JobView:
    '''给 Planner 看的任务视图：目标模型换成 Excel 的 docx 风格模型，其余属性原样转发给真实任务。'''

    def __init__(self, job, tmodel):
        self._job = job
        self._tm = tmodel

    def target_model(self):
        return self._tm

    def __getattr__(self, name):
        return getattr(self._job, name)


class XPlanner(Planner):
    '''Excel 目标的规划器：复用 Planner 的配方（规范表、测试数据、字段、派生），只处理 Excel 与 Word 不同的地方。
    不加行（插行会破坏公式、合并区、数据验证），源表比数据区长时多出的行登记为“没处理”并拦截终稿；
    Excel 批注里的编制要求没有被任何配方处理的、工作表没读全的，同样登记。'''

    def __init__(self, job, answers=None, learned=None, aliases=None, manual_ops=None, rules_override=None):
        tm, an, wb = target_model(job)
        super().__init__(_JobView(job, tm), answers=answers, learned=learned, aliases=aliases,
                         manual_ops=manual_ops, rules_override=rules_override)
        self.job = job
        self.xan, self.wb = an, wb
        self._overflow = []
        self.rules_from_comments()

    def rules_from_comments(self):
        '''批注里的编制要求（如“只取中间电压”“填范围，写法为XX~XX”“保留2位小数”）也按任务描述的规则识别；
        任务描述和手工规则优先，批注只补它们没说的；只认取值类规则，版本号之类不从批注里猜。'''
        from .planner import task_rules
        prefix = {'cond_keep': '条件=', 'multi': '多值=', 'range_sep': '范围写法=', 'decimals': '小数位='}
        for cm in self.xan['comments']:
            got, why = task_rules(cm['text'])
            new = [k for k in RULE_KEYS_FROM_COMMENTS if k in got and k not in self.rules]
            for k in new:
                self.rules[k] = got[k]
            if new:
                said = [w_ for w_ in why if any(w_.startswith(prefix[k]) for k in new)]
                self.rule_why.append('批注 %s!%s：%s' % (cm['sheet'], cm['ref'], '、'.join(said)))

    def xref_cid(self, v):
        '''“Sheet1!B3” / “'My Sheet'!B3” -> T0.R2.C1；工作表名找不到返回 None。'''
        m = XREF_RE.match((v or '').strip())
        if not m:
            return None
        name = (m.group(1) or '').replace("''", "'") or m.group(2)
        sh = next((s for s in self.xan['sheets'] if s['name'] == name), None)
        if sh is None:
            return None
        return 'T%d.R%d.C%d' % (sh['index'], int(m.group(4)) - 1, col_index(m.group(3)))

    def apply_manual(self):
        for op in self.manual:      # set / add-op 里可以直接写 Excel 的位置（Sheet1!B3），这里统一换成 T0.R2.C1
            for k in ('cell', 'anchor', 'block'):
                cid = self.xref_cid(op.get(k)) if isinstance(op.get(k), str) and not CID_RE.match(op.get(k)) else None
                if cid:
                    op[k] = cid
        super().apply_manual()

    def fill_from_table(self, t, rg, tcols, alias, tid):
        n0 = len(self.ops)
        rows = super().fill_from_table(t, rg, tcols, alias, tid)
        sh = self.row_shift.pop(t['id'], None)       # Excel 不插行：也不能让后面的字段位置按“已插行”平移
        if not sh:
            return rows
        r1, extra = sh
        keep = []
        for i, o in enumerate(self.ops):
            if i >= n0:
                if o['op'] == 'add_rows' and o.get('table') == t['id']:
                    continue
                m = CID_RE.match(o.get('cell') or o.get('anchor') or '')
                if m and int(m.group(2)) > r1 and str(o.get('group', '')).startswith('spec:'):
                    continue
            keep.append(o)
        self.ops[:] = keep
        pat = re.compile(r'^%s 第(\d+)行$' % re.escape(t['id']))
        self.report['fills'] = [f for f in self.report['fills']
                                if not (pat.match(f.get('target', '')) and int(pat.match(f['target']).group(1)) > r1)]
        self.report['warnings'] = [x for x in self.report['warnings'] if '已在 %s 末尾增加' % rg['id'] not in x]
        self._overflow.append({'region': rg['id'], 'src': '%s:%s' % (alias, tid), 'extra': extra})
        return [x for x in rows if x['row'] <= r1]

    def _unhandled(self, key, topic, detail, ok_label, no_label):
        '''“没处理/没读全”不能当作“确实没有”：出题，没有用户本人的正式回答就登记为未核，定稿时拦截（推荐项永远是“先不处理”）。'''
        eff = self.answer_effect(key)
        if eff is None:
            self.ask({'key': key, 'topic': topic, 'question': detail + '。如何处理？', 'recommended': 0,
                      'options': [{'label': no_label, 'effect': {'ok': False}}, {'label': ok_label, 'effect': {'ok': True}}]})
            eff = {'ok': False}
        if not (isinstance(eff, dict) and eff.get('ok') and not eff.get('_default')):
            self.report.setdefault('unverified_sources', []).append({'kind': 'unread', 'key': key, 'name': topic, 'detail': detail})
        self.report['warnings'].append('⚠ ' + detail)

    def run(self):
        plan = super().run()
        for ov in self._overflow:
            detail = '源表 %s 的数据比目标数据区 %s 多 %d 行；Excel 目标不自动加行（会破坏公式和合并区），多出的行没有写入' % (ov['src'], ov['region'], ov['extra'])
            self.report['unfilled'].append(detail)
            self._unhandled('xl:overflow:' + ov['region'], '数据区行数不够', detail,
                            '我已手工补上多出的 %d 行' % ov['extra'], '先不处理，定稿前我再补')
        for name in self.xan['truncated']:
            detail = '工作表“%s”超过 %d 行或 %d 列，只读取了前面的部分，其余没读全' % (name, MAX_ROWS, MAX_COLS)
            self._unhandled('xl:truncated:' + name, '未读全', detail, '我已人工核对过超出的部分', '先不当作没有，定稿前我再核对')
        touched = set()
        for o in self.ops:
            ref = o.get('cell') or o.get('anchor') or ''
            if o['op'] != 'comment' and CID_RE.match(ref):
                touched.add(ref)
        fieldcells = {f['cell'] for t in self.xan['tables'] for f in t['fields']}
        todo = []
        for cm in self.xan['comments']:
            s, r, c = cid_parse(cm['cell'])
            near = {cm['cell'], 'T%d.R%d.C%d' % (s, r, c + 1), 'T%d.R%d.C%d' % (s, r + 1, c)}
            # 落在字段上（或字段左边标签上）的要求，已由字段配方处理：填上了，或已进“无来源字段”题，不再重复问
            if not (near & touched) and not ({cm['cell'], 'T%d.R%d.C%d' % (s, r, c + 1)} & fieldcells):
                todo.append('%s!%s“%s”' % (cm['sheet'], cm['ref'], short(cm['text'], 20)))
        if todo:
            detail = 'Excel 批注里有 %d 条编制要求没有被自动处理：%s' % (len(todo), '；'.join(todo[:3]) + ('…' if len(todo) > 3 else ''))
            self.report['unfilled'] += ['按批注要求处理（需人工）：' + x for x in todo]
            self._unhandled('xl:requirements', '批注要求', detail, '我已按这些要求自行处理或核对', '先不处理，批注保留，定稿前我再看')
        for s in self.xan['sheets']:
            if s.get('skipped'):
                self.report['warnings'].append('工作表“%s”：%s' % (s['name'], s['skipped']))
        return plan
