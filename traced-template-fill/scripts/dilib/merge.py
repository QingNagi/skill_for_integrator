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
import shutil
from xml.etree import ElementTree as ET

from .common import TOOL_AUTHOR, TOOL_INITIALS, lock_file_of, short
from .ooxml import CT_COMMENTS, NS, RT_COMMENTS, W_P, W_R, W_T, Package, make_rpr, make_run, qn, w
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
        same = (src['tables'].keys() == ours['tables'].keys() and tid in src['tables']
                and src['tables'][tid] == ours['tables'][tid])
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
    tables = base['tables']
    if tables.keys() != th['tables'].keys():
        notes.append('表格数或编号发生变化（%s → %s），表格坐标不能逐格对应；请对照原审核稿核对增删和修改。'
                     % ('、'.join(tables) or '无', '、'.join(th['tables']) or '无'))
        tables = {}
    for tid, dim in tables.items():
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
            else:
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


def _unchanged_context(base, ours):
    """Compare every dependency of the main body; creation dates vary per draft."""
    if base.parts.keys() != ours.parts.keys():
        return False
    for part, data in base.parts.items():
        if part == 'word/document.xml' or data == ours.parts[part]:
            continue
        if part != 'word/comments.xml':
            return False
        shapes = []
        for pkg in (base, ours):
            root = copy.deepcopy(pkg.xml(part))
            for comment in root.findall(w('comment')):
                if comment.get(w('author')) == TOOL_AUTHOR:
                    comment.attrib.pop(w('date'), None)
            shapes.append(ET.tostring(root))
        if shapes[0] != shapes[1]:
            return False
    return True


def _reviewed_body_compatible(base, theirs, ours):
    # If Office changed the body, keep the reviewed body only when the plan
    # generated precisely the same body again. Otherwise edits need a merge.
    part = 'word/document.xml'
    if base.parts[part] == ours.parts[part]:
        return True
    if theirs.parts[part] != base.parts[part]:
        return False
    # Only one-paragraph anchors are comparable this way. A multi-paragraph
    # range could contain changed content outside the paragraphs with markers.
    def ranges(pkg):
        locations = {}
        for p in pkg.xml(part).iter(W_P):
            for el in p.iter():
                if el.tag in MARK_TAGS or el.tag == w('commentReference'):
                    cid = el.get(w('id'))
                    if cid in locations and locations[cid] is not p:
                        return None
                    locations[cid] = p
        return {cid: ET.tostring(p) for cid, p in locations.items()}
    previous = ranges(base)
    if previous is None or previous != ranges(ours):
        return False
    used = {v for el in ours.xml(part).iter() for k, v in el.attrib.items() if k.startswith(R_PREFIX)}
    theirs_rels = {r[0]: r[1:] for r in theirs.rels(part).items()}
    return all(theirs_rels.get(r[0]) == r[1:] for r in ours.rels(part).items() if r[0] in used)


def _complex_review(info, pkg):
    if info['extension'] is not None or has_rel_attr(info['el']):
        return True
    if any(pkg.has('word/' + part + '.xml') for part in ('commentsIds', 'commentsExtensible', 'people')):
        return True
    return any(el.tag in {w('drawing'), w('object'), w('pict'), w('altChunk'),
                          w('sdt'), w('customXml'), w('tbl')} for el in info['el'].iter())


def _recorded_replies(job, version, infos):
    questions = {q['key']: q for q in job.data.get('word_questions', {}).get(version, [])}
    if not questions:
        return set()
    by_id = {c['id']: c for c in infos}
    archived = set()
    for key, answer in (job.read('answers.json', {}) or {}).items():
        if key not in questions or answer.get('word_draft') != version:
            continue
        root = by_id.get(answer.get('word_parent'))
        reply = by_id.get(answer.get('word_reply'))
        if not root or not reply or root['author'] != TOOL_AUTHOR or root['text'] != questions[key]['text']:
            continue
        if reply['parent'] != root['id'] or reply['text'] != answer['user_quote'] or reply['author'] != answer['confirmed_by']:
            continue
        if has_rel_attr(reply['el']) or any(el.tag in (w('drawing'), w('object'), w('pict'), w('tbl'))
                                           for el in reply['el'].iter()):
            continue
        if not any(c['parent'] == reply['id'] for c in infos):
            archived.add(reply['id'])
            if all(c['id'] in archived for c in infos if c['parent'] == root['id']):
                archived.add(root['id'])
    return archived


def _structural_revisions(reader):
    """Revisions of a row, cell or paragraph mark have no comparable text of their own."""
    from .finalize import REV_TAGS
    pm = _pmap(reader.doc)
    locations = {el: uid for uid, el in reader.el_by_id.items() if not isinstance(el, list)}
    marks = {}
    for el in reader.doc.iter():
        par = pm.get(el)
        if el.tag not in REV_TAGS or par is None or not par.tag.endswith('Pr'):
            continue
        owner = pm.get(par)
        if owner is not None and owner.tag == w('tr'):
            owner = owner.find(w('tc'))
        while owner is not None and owner not in locations:
            owner = pm.get(owner)
        uid = locations.get(owner, '正文')
        marks.setdefault(uid, []).append(ET.tostring(el))
    return marks


def _merge(job, prev, pristine, new, discard, res):
    from .finalize import comments_info
    base_rd, base = read_units(pristine)
    th_rd, theirs = read_units(prev)
    hum = collect_human_comments(job, th_rd)
    edits, snotes = find_edits(base, theirs)
    if discard:
        res['discarded'] = (len(edits), len(hum))
        return
    ours_rd, ours = read_units(new)
    human_ids = {info['id'] for info, _ in hum}
    infos = comments_info(th_rd.pkg)
    complex_comments = {info['id']: info for info in infos
                        if info['done'] or (info['id'] in human_ids and _complex_review(info, th_rd.pkg))}
    if (base_rd.pkg.parts != th_rd.pkg.parts and _unchanged_context(base_rd.pkg, ours_rd.pkg)
            and _reviewed_body_compatible(base_rd.pkg, th_rd.pkg, ours_rd.pkg)):
        # The old reviewed package contains the exact relationships, attachments,
        # anchors and extension namespaces written by Office. Keep it as a unit.
        tmp = new + '.merge.tmp'
        try:
            main = 'word/document.xml'
            if ours_rd.pkg.parts[main] == base_rd.pkg.parts[main]:
                shutil.copyfile(prev, tmp)
            else:
                merged = Package(prev)
                merged.parts[main] = ours_rd.pkg.parts[main]
                merged.save(tmp)
            os.replace(tmp, new)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        res.update(kept=len(edits), comments=len(hum))
        if hum or complex_comments or edits or snotes:
            res['notes'].append('合并：已保留审核稿内的修改、批注及其附件。')
        return
    archived = _recorded_replies(job, os.path.basename(prev), infos)
    if archived:
        res['notes'].append('Word 核对的原话和署名已登记在 decisions.jsonl，原讨论保存在 ' + os.path.basename(prev))
        hum = [(info, unit) for info, unit in hum if info['id'] not in archived]
    for cid, info in complex_comments.items():
        if cid in archived:
            continue
        msg = ('原审核稿 %s 的批注 #%s [%s] %s 含有讨论状态、回复或复杂内容，'
               '新版规划已变化，未完整转移；请处理原审核稿中的讨论，或从原审核稿定稿。'
               % (os.path.basename(prev), cid, info['author'], short(info['text'], 60)))
        res['items'].append(_unread('merge:discussion:%s:%s' % (os.path.basename(prev), cid), msg))
        res['notes'].append('合并：' + msg)
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
        if info['id'] in complex_comments:
            continue
        o_uid = map_unit(unit, theirs, ours) if unit else None
        p = first_para(el_o.get(o_uid)) if o_uid else None
        if p is None:
            msg = '原审核稿 %s 的批注 #%s [%s] %s 无法定位，未并入新版；请对照原审核稿处理。' % (
                os.path.basename(prev), info['id'], info['author'], short(info['text'], 60))
            res['items'].append(_unread('merge:discussion:%s:%s' % (os.path.basename(prev), info['id']), msg))
            res['notes'].append('合并：' + msg)
            continue
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
    old_marks = _structural_revisions(base_rd)
    reviewed_marks = _structural_revisions(th_rd)
    for uid in sorted(old_marks.keys() | reviewed_marks.keys()):
        if old_marks.get(uid) != reviewed_marks.get(uid):
            snotes.append('%s 的行、单元格或段落属性修订已变化，请对照原审核稿 %s 核对。' % (uid, os.path.basename(prev)))
    for i, n in enumerate(snotes):
        res['items'].append(_unread('merge:struct:%s:%d' % (os.path.basename(prev), i), n))
        res['notes'].append('合并：' + n)
    for g, uids in sorted(lost.items()):
        where = ('表格 %s' % g) if g != 'P' else '正文段落'
        msg = '上一版草稿%s有 %d 处人工修改没能并入新版（新规划改了版面，对应不上）：%s' % (where, len(uids), '、'.join(uids[:4]) + ('…' if len(uids) > 4 else ''))
        res['items'].append(_unread('merge:lost:%s' % g, msg))
        res['notes'].append('合并：' + msg)
    for uid, txt in blocked:
        detail = '原审核稿 %s 的 %s 含有未转移的人工修改，请对照原件核对。' % (os.path.basename(prev), uid)
        res['items'].append({'cell': uid, 'text': txt, 'why': '上一版人工修改未并入', 'param': uid, 'sample': '',
                             'key': 'merge:content:%s:%s' % (os.path.basename(prev), uid), 'detail': detail})
    if blocked:
        res['notes'].append('合并：上一版草稿有 %d 处人工修改含图片或嵌套表格，没能并入新版：%s' % (len(blocked), '、'.join(u for u, _ in blocked[:4])))


def _same_file_contents(left_path, right_path):
    # Office may rewrite a file with an unchanged size and timestamp.
    if os.path.getsize(left_path) != os.path.getsize(right_path):
        return False
    with open(left_path, 'rb') as left, open(right_path, 'rb') as right:
        while True:
            chunk = left.read(1024 * 1024)
            if chunk != right.read(1024 * 1024):
                return False
            if not chunk:
                return True


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
    if not discard:
        for item in (job.data.get('unverified') or {}).get(os.path.basename(prev), []):
            if item.get('key', '').startswith('merge:'):
                res['items'].append(dict(item))
                res['notes'].append('合并：' + item['detail'])
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
        if _same_file_contents(pristine, prev):
            if discard:
                res['discarded'] = (0, 0)
            return res
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
