# -*- coding: utf-8 -*-
'''重新 draft 时的三方合并：把审核人在上一版草稿里的直接修改和批注带到新版。

base   = 上一版草稿的 .原始 副本（生成时的原样）
theirs = 上一版草稿（审核人改过）
ours   = 本次新规划刚生成的草稿
比对单位：顶层段落（P12）和表格单元格（T0.R5.C3）；只比对文字和批注，不比对字体等格式。
规则：人改过而新规划没变 -> 保留人改；两边都改且不同 -> 保留人改并加 ⚠ 批注；
      对应不上的 -> 不静默丢弃，登记为“未核”，定稿时拦截。
'''
from __future__ import annotations

import copy
import datetime
import difflib
import os
import re
from xml.etree import ElementTree as ET

from .common import TOOL_AUTHOR, TOOL_INITIALS, lock_file_of, short
from .ooxml import CT_COMMENTS, NS, RT_COMMENTS, W_P, W_R, W_T, make_rpr, make_run, qn, w
from .read_docx import DocxReader

R_PREFIX = '{%s}' % NS['r']
W14 = 'http://schemas.microsoft.com/office/word/2010/wordml'
CELL_RE = re.compile(r'^(T\d+)\.R(\d+)\.C(\d+)')
PARA_RE = re.compile(r'^P\d+$')


# ======================================================================= 读取与比对
def _n(s):
    return re.sub(r'\s+', ' ', s or '').strip()


def read_units(path):
    '''读一份草稿：返回 (阅读器, 结构)。结构里 text 是规整后的文字（用于比对），raw 是原文（用于登记）。'''
    rd = DocxReader(path)
    m = rd.read()
    st = {'paras': [], 'tables': {}, 'text': {}, 'raw': {}, 'nested': set()}
    for b in m['blocks']:
        if b.get('in'):
            continue
        if b['t'] == 'p':
            st['paras'].append(b['id'])
            st['text'][b['id']] = _n(b['text'])
            st['raw'][b['id']] = b['text']
        elif b['t'] == 'tbl':
            st['tables'][b['id']] = (b['nrows'], b['ncols'])
            for c in b['cells']:
                uid = '%s.R%d.C%d' % (b['id'], c['r'], c['c'])
                st['text'][uid] = _n(c['text'])
                st['raw'][uid] = c['text']
                if c.get('nested'):
                    st['nested'].add(uid)
    return rd, st


def map_unit(uid, src, ours):
    '''src 坐标系里的单元 ID -> ours 里对应的 ID；对应不上返回 None（不猜）。'''
    mc = CELL_RE.match(uid)
    if mc:
        tid = mc.group(1)
        same = src['tables'].get(tid) is not None and src['tables'].get(tid) == ours['tables'].get(tid)
        return uid if same and uid in ours['text'] else None
    if src['paras'] == ours['paras']:
        return uid if uid in ours['text'] else None
    want = src['text'].get(uid)
    hit = [k for k in ours['paras'] if want and ours['text'][k] == want]
    return hit[0] if len(hit) == 1 else None


def find_edits(base, th):
    '''返回 (edits, notes)。edits = [(base 单元ID, theirs 单元ID)]，即人改过文字的单元；
    notes = 无法逐项并入的结构性修改（增删行列、增删段落）的说明。'''
    edits, notes = [], []
    for tid, dim in base['tables'].items():
        got = th['tables'].get(tid)
        if got is None:
            notes.append('表格 %s 在上一版草稿里不见了，未并入，请人工核对' % tid)
        elif got != dim:
            notes.append('表格 %s 的行列数被改过（%dx%d → %dx%d），无法逐格并入，请人工核对' % ((tid,) + tuple(dim) + tuple(got)))
        else:
            for uid, bt in base['text'].items():
                m = CELL_RE.match(uid)
                if m and m.group(1) == tid and th['text'].get(uid, bt) != bt:
                    edits.append((uid, uid))
    la = [base['text'][k] for k in base['paras']]
    lb = [th['text'][k] for k in th['paras']]
    if base['paras'] == th['paras']:
        edits += [(k, k) for k in base['paras'] if base['text'][k] != th['text'][k]]
    else:
        sm = difflib.SequenceMatcher(None, la, lb, autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == 'equal':
                continue
            if tag == 'replace' and i2 - i1 == j2 - j1:
                edits += [(base['paras'][i1 + k], th['paras'][j1 + k]) for k in range(i2 - i1)]
            elif len(notes) < 8:
                notes.append('正文段落有增删（原 %d 段 → %d 段，如“%s”），未并入，请人工核对' % (i2 - i1, j2 - j1, short(' / '.join(lb[j1:j2]) or ' / '.join(la[i1:i2]), 40)))
    return edits, notes


BM_TAGS = (w('bookmarkStart'), w('bookmarkEnd'))
MARK_TAGS = (w('commentRangeStart'), w('commentRangeEnd'))
_PPR = w('pPr')
_TCPR = w('tcPr')


def _pmap(root):
    return {c: p for p in root.iter() for c in p}


def has_rel_attr(el):
    '''元素里有没有指向关系表的属性（图片 r:embed、链接 r:id）：这类内容跨文件拷贝会断，不拷，登记为未并入。'''
    for e in el.iter():
        for k in e.attrib:
            if k.startswith(R_PREFIX):
                return True
    return False


def strip_marks(el):
    '''去掉拷贝来的批注范围标记、批注引用和书签：它们的 ID 在新文件里没有意义，书签还可能重名。'''
    pm = _pmap(el)
    for e in list(el.iter()):
        par = pm.get(e)
        if par is None:
            continue
        if e.tag in MARK_TAGS or e.tag in BM_TAGS:
            par.remove(e)
        elif e.tag == w('commentReference'):
            par.remove(e)
            if par.tag == W_R and all(ch.tag == w('rPr') for ch in par) and pm.get(par) is not None:
                pm[par].remove(par)


def take_marks(el):
    '''摘下元素里的批注标记和书签，返回 (批注起点ID, 批注终点ID, 批注引用ID, 书签起点元素, 书签终点元素)。'''
    pm = _pmap(el)
    cs, ce, cr, bs, be = [], [], [], [], []
    for e in list(el.iter()):
        par = pm.get(e)
        if par is None:
            continue
        if e.tag == w('commentRangeStart'):
            cs.append(e.get(w('id')))
            par.remove(e)
        elif e.tag == w('commentRangeEnd'):
            ce.append(e.get(w('id')))
            par.remove(e)
        elif e.tag == w('commentReference'):
            cr.append(e.get(w('id')))
            par.remove(e)
            if par.tag == W_R and all(ch.tag == w('rPr') for ch in par) and pm.get(par) is not None:
                pm[par].remove(par)
        elif e.tag == w('bookmarkStart'):
            bs.append(e)
            par.remove(e)
        elif e.tag == w('bookmarkEnd'):
            be.append(e)
            par.remove(e)
    return cs, ce, cr, bs, be


def put_marks(p, marks):
    '''把 take_marks 摘下的东西放回段落 p：起点在段首（pPr 之后），终点和引用在段尾。'''
    cs, ce, cr, bs, be = marks
    pos = 1 if len(p) and p[0].tag == _PPR else 0
    for e in list(bs) + [ET.Element(w('commentRangeStart'), {w('id'): i}) for i in cs]:
        p.insert(pos, e)
        pos += 1
    for e in [ET.Element(w('commentRangeEnd'), {w('id'): i}) for i in ce] + list(be):
        p.append(e)
    for i in cr:
        r = ET.SubElement(p, W_R)
        ET.SubElement(r, w('commentReference'), {w('id'): i})


def copy_para_into(old_p, src_p):
    '''把 src_p 的内容换进 old_p（元素本身不换，后面的定位不受影响），保留 old_p 上助手批注的标记和书签。'''
    marks = take_marks(old_p)
    new = copy.deepcopy(src_p)
    strip_marks(new)
    for ch in list(old_p):
        old_p.remove(ch)
    for ch in list(new):
        old_p.append(ch)
    put_marks(old_p, marks)


def copy_cell_into(old_tc, src_tc):
    '''把 src_tc 的内容（不含单元格属性）换进 old_tc，保留 old_tc 的单元格属性、助手批注标记和书签。'''
    marks = take_marks(old_tc)
    new = copy.deepcopy(src_tc)
    strip_marks(new)
    for ch in [c for c in old_tc if c.tag != _TCPR]:
        old_tc.remove(ch)
    for ch in [c for c in new if c.tag != _TCPR]:
        old_tc.append(ch)
    if not len(old_tc) or old_tc[-1].tag != W_P:
        ET.SubElement(old_tc, W_P)      # 单元格必须以段落结尾
    put_marks(old_tc.find(W_P), marks)


def unwrap_rel_links(el):
    '''带关系 ID 的超链接去掉链接、保留文字（拷批注时用，关系表不跟着走）。'''
    pm = _pmap(el)
    for h in list(el.iter(w('hyperlink'))):
        par = pm.get(h)
        if par is None or h.get(qn('r:id')) is None:
            continue
        idx = list(par).index(h)
        for k, ch in enumerate(list(h)):
            par.insert(idx + k, ch)
        par.remove(h)


def _unit_of(pid):
    if not pid:
        return None
    m = CELL_RE.match(pid)
    return m.group(0) if m else pid


def _para_of(el, pm):
    '''批注范围标记所在的段落；标记直接挂在正文或单元格下时，取它后面的第一个段落。'''
    cur = el
    while True:
        par = pm.get(cur)
        if par is None:
            return None
        if par.tag == W_P:
            return par
        if par.tag in (w('body'), w('tc')):
            sib = list(par)
            for x in sib[sib.index(cur) + 1:]:
                if x.tag == W_P:
                    return x
            return None
        cur = par


def first_para(el):
    if el is None:
        return None
    if el.tag == W_P:
        return el
    ps = list(el.iter(W_P))
    return ps[0] if ps else None


def collect_human_comments(job, rd):
    '''上一版草稿里人加的批注：作者不是助手、也不是模板原有的。返回 [(批注信息, 锚点单元ID 或 None)]。'''
    from .finalize import comments_info
    tmpl = {(a, t) for a, t in (job.data.get('template_comments') or [])}
    hum = {}
    for c in comments_info(rd.pkg):
        if c['author'] != TOOL_AUTHOR and (c['author'], c['text']) not in tmpl:
            hum[c['id']] = c
    if not hum:
        return []
    pm = _pmap(rd.doc)
    puid = {el: k for k, el in rd.el_by_id.items() if not isinstance(el, list) and el.tag == W_P}
    where = {}
    for el in rd.doc.iter(w('commentRangeStart')):
        cid = el.get(w('id'))
        if cid in hum and cid not in where:
            where[cid] = _unit_of(puid.get(_para_of(el, pm)))
    return [(hum[i], where.get(i)) for i in hum]


class CommentAdder:
    '''往新草稿里加批注：带入人工批注（原样），或加助手的 ⚠ 批注（作者是助手，定稿时随助手批注一起删除）。'''

    def __init__(self, pkg, doc):
        self.pkg, self.doc = pkg, doc
        if pkg.has('word/comments.xml'):
            self.root = pkg.xml('word/comments.xml')
        else:
            self.root = ET.Element(w('comments'))
            pkg.new_xml('word/comments.xml', self.root, [('w', NS['w']), ('r', NS['r'])])
            pkg.rels('word/document.xml').add(RT_COMMENTS, 'comments.xml')
            pkg.ensure_override('word/comments.xml', CT_COMMENTS)
        ids = [int(c.get(w('id'))) for c in self.root.findall(w('comment')) if (c.get(w('id')) or '').isdigit()]
        ids += [int(c.get(w('id'))) for c in doc.iter(w('commentRangeStart')) if (c.get(w('id')) or '').isdigit()]
        self.nid = max(ids + [-1]) + 1

    def _anchor(self, p, cid):
        pos = 1 if len(p) and p[0].tag == _PPR else 0
        p.insert(pos, ET.Element(w('commentRangeStart'), {w('id'): str(cid)}))
        p.append(ET.Element(w('commentRangeEnd'), {w('id'): str(cid)}))
        rr = ET.SubElement(p, W_R)
        ET.SubElement(rr, w('commentReference'), {w('id'): str(cid)})

    def copy(self, src, p):
        '''带入人工批注：作者、日期、正文原样，ID 重新编号；批注里的超链接去掉链接、保留文字。'''
        c = copy.deepcopy(src)
        cid = self.nid
        self.nid += 1
        c.set(w('id'), str(cid))
        for e in c.iter():
            for k in ('{%s}paraId' % W14, '{%s}textId' % W14):
                e.attrib.pop(k, None)
        unwrap_rel_links(c)
        self.root.append(c)
        self._anchor(p, cid)

    def warn(self, text, p):
        now = datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ')
        cid = self.nid
        self.nid += 1
        c = ET.SubElement(self.root, w('comment'), {w('id'): str(cid), w('author'): TOOL_AUTHOR, w('date'): now,
                                                   w('initials'): TOOL_INITIALS})
        pp = ET.SubElement(c, W_P)
        ET.SubElement(ET.SubElement(pp, W_R), w('annotationRef'))
        pp.append(make_run(text, make_rpr(None, (), color='C00000')))
        self._anchor(p, cid)


def _group_of(uid):
    m = CELL_RE.match(uid)
    return m.group(1) if m else 'P'


def _put_content(o_el, t_el):
    '''把上一版草稿里改过的内容换进新版同一位置；带图片等关系引用的内容拷不过来，返回 False 且不改任何东西。'''
    s = copy.deepcopy(t_el)
    unwrap_rel_links(s)
    if has_rel_attr(s):
        return False
    if o_el.tag == W_P:
        copy_para_into(o_el, s)
    else:
        copy_cell_into(o_el, s)
    return True


def _unread(key, detail):
    return {'kind': 'unread', 'key': key, 'name': '上一版人工修改', 'detail': detail}


def _merge(job, prev, pristine, new, discard, res):
    _, base = read_units(pristine)
    th_rd, theirs = read_units(prev)
    hum = collect_human_comments(job, th_rd)
    edits, snotes = find_edits(base, theirs)
    if discard:
        res['discarded'] = (len(edits), len(hum))
        return
    ours_rd, ours = read_units(new)
    el_o, el_t = ours_rd.el_by_id, th_rd.el_by_id
    adder = None
    lost, blocked, conflicts = {}, [], []
    kept = 0
    for b_uid, t_uid in edits:
        o_uid = map_unit(b_uid, base, ours)
        if o_uid is None:
            lost.setdefault(_group_of(b_uid), []).append(b_uid)
            continue
        bt = base['text'].get(b_uid, '')
        tt = theirs['text'].get(t_uid, '')
        ot = ours['text'].get(o_uid, '')
        if ot == tt:
            continue
        o_el, t_el = el_o.get(o_uid), el_t.get(t_uid)
        if (o_el is None or t_el is None or isinstance(o_el, list) or isinstance(t_el, list)
                or o_uid in ours['nested'] or t_uid in theirs['nested'] or not _put_content(o_el, t_el)):
            blocked.append((o_uid, ours['raw'].get(o_uid, '')))
            continue
        if ot == bt:
            kept += 1
            continue
        if adder is None:
            adder = CommentAdder(ours_rd.pkg, ours_rd.doc)
        adder.warn('⚠ 与新规划冲突，已保留人工修改，新规划值为“%s”' % short(ours['raw'].get(o_uid, ''), 60),
                   first_para(o_el))
        conflicts.append(o_uid)
    ncom = 0
    for info, unit in hum:
        o_uid = map_unit(unit, theirs, ours) if unit else None
        p = first_para(el_o.get(o_uid)) if o_uid else None
        if p is None:
            p = next(iter(ours_rd.doc.iter(W_P)), None)
            if p is None:
                res['items'].append(_unread('merge:comment:%s' % info['id'], '上一版草稿里 %s 的批注没能带到新版：%s' % (info['author'], short(info['text'], 30))))
                continue
            res['notes'].append('合并：批注“%s”（%s）原来的位置在新版里对应不上，已原样挂在文档开头' % (short(info['text'], 20), info['author']))
        if adder is None:
            adder = CommentAdder(ours_rd.pkg, ours_rd.doc)
        adder.copy(info['el'], p)
        ncom += 1
    if kept or conflicts or ncom:
        tmp = new + '.merge.tmp'
        try:
            ours_rd.pkg.save(tmp)
            os.replace(tmp, new)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    res['kept'], res['conflicts'], res['comments'] = kept, len(conflicts), ncom
    res['conflict_ids'] = conflicts
    for i, n in enumerate(snotes):
        res['items'].append(_unread('merge:struct:%d' % i, n))
        res['notes'].append('合并：' + n)
    for g, uids in sorted(lost.items()):
        where = ('表格 %s' % g) if g != 'P' else '正文段落'
        msg = '上一版草稿%s有 %d 处人工修改没能并入新版（新规划改了版面，对应不上）：%s' % (where, len(uids), '、'.join(uids[:4]) + ('…' if len(uids) > 4 else ''))
        res['items'].append(_unread('merge:lost:%s' % g, msg))
        res['notes'].append('合并：' + msg)
    for uid, txt in blocked:
        res['items'].append({'cell': uid, 'text': txt, 'why': '上一版人工修改未并入', 'param': uid, 'sample': ''})
    if blocked:
        res['notes'].append('合并：上一版草稿有 %d 处人工修改含图片或嵌套表格，没能并入新版：%s' % (len(blocked), '、'.join(u for u, _ in blocked[:4])))


def merge_previous(job, prev, new, discard=False):
    '''把上一版草稿（prev）里的人工修改和人工批注并进新版草稿（new，已按新规划生成并存盘）。
    返回字典：kept 保留的人工修改数，conflicts 冲突数，comments 带入的人工批注数，conflict_ids，
    notes 要给用户看的说明，items 要登记为“未核”的项（没并进来的人工修改，定稿时拦截），
    discarded（只在 discard 时）丢弃的 (人工修改数, 人工批注数)，skipped/crashed 表示没做合并。'''
    res = {'kept': 0, 'conflicts': 0, 'comments': 0, 'conflict_ids': [], 'notes': [], 'items': [],
           'discarded': None, 'skipped': None, 'crashed': False}
    if not prev or not os.path.exists(prev):
        res['skipped'] = 'first'
        return res
    pristine = os.path.join(os.path.dirname(prev), '.原始', os.path.basename(prev))
    if not os.path.exists(pristine):
        res['skipped'] = 'nobase'
        if not discard:
            msg = '上一版草稿缺少 .原始 副本，无法判断审核人改过什么，人工修改没有带到新版，请对照上一版手工处理'
            res['items'].append(_unread('merge:nobase', msg))
            res['notes'].append('合并：' + msg)
        return res
    if not discard and lock_file_of(prev):
        msg = '上一版草稿仍在 Word/WPS 中打开，没保存的修改读不到；请保存关闭后重新 draft'
        res['items'].append(_unread('merge:locked', msg))
        res['notes'].append('合并：' + msg)
    try:
        _merge(job, prev, pristine, new, discard, res)
    except Exception as e:      # 合并出错不能让 draft 崩溃，也不能当作“没有人工修改”：登记未核，定稿会拦截
        res['crashed'] = True
        if not discard:
            msg = '重新草稿时合并上一版的人工修改没跑完（%s: %s），人工修改没有带到新版' % (type(e).__name__, e)
            res['items'].append(_unread('merge:crash', msg))
            res['notes'].append('合并：' + msg)
    return res


def summary_lines(res, discard=False):
    '''给用户看的合并摘要（0~1 行）。没做合并的情形（第一版、缺副本、出错）由 notes 说明，这里不写“保留 0 处”以免误导。'''
    if res.get('skipped') == 'first':
        return []
    if discard:
        d = res.get('discarded')
        if d is not None:
            return ['[合并] 已按 --discard-edits 丢弃上一版的人工修改 %d 处、人工批注 %d 条' % tuple(d)]
        return ['[合并] 已按 --discard-edits 纯按新规划生成']
    if res.get('skipped') or res.get('crashed'):
        return []
    line = '[合并] 保留人工修改 %d 处，冲突 %d 处' % (res['kept'], res['conflicts'])
    if res['conflicts']:
        ids = res['conflict_ids']
        line += '（冲突处同样保留了人工修改，批注里写了新规划值：%s%s）' % ('、'.join(ids[:5]), '…' if len(ids) > 5 else '')
    if res['comments']:
        line += '；带入人工批注 %d 条' % res['comments']
    return [line]
