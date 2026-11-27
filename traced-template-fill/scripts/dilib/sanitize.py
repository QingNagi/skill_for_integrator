# -*- coding: utf-8 -*-
'''源文件快照净化。

快照是把源 docx 整包重写后放进“草稿/源文件快照/”，供审核人点批注链接跳转。整包重写会带出源文件里的
原批注、作者、修订、隐藏文字、customXml 和外部链接，这里在写出前去掉；正文、书签、黄色高亮保留。
只用标准库；每一步单独保护，出错登记为警告，不让草稿失败。
'''
from __future__ import annotations

import posixpath
import re

from .ooxml import NS, W_P, W_R, qn, w

CONTENT = re.compile(r'^word/(document|header\d*|footer\d*|footnotes|endnotes)\.xml$')
COMMENT_PARTS = ('word/comments.xml', 'word/commentsExtended.xml', 'word/commentsIds.xml',
                 'word/commentsExtensible.xml', 'word/people.xml')
OFF = ('0', 'false', 'off')


def _pm(root):
    return {c: p for p in root.iter() for c in p}


def _content_parts(pkg):
    return [n for n in sorted(pkg.parts) if CONTENT.match(n)]


def _rels_owner(rn):
    '''rels 部件名 -> 它所属的部件名（根 rels 为空串）。'''
    if rn == '_rels/.rels':
        return ''
    return posixpath.join(posixpath.dirname(posixpath.dirname(rn)), posixpath.basename(rn)[:-5])


def drop_part(pkg, name):
    '''从包里删掉一个部件：连同它自己的 rels、[Content_Types] 里的覆盖项、别处指向它的关系。'''
    if name not in pkg.parts:
        return False
    for key in (name, pkg.rels_name(name)):
        pkg.parts.pop(key, None)
        if key in pkg.order:
            pkg.order.remove(key)
        pkg._xml.pop(key, None)
        pkg._rels.pop(key, None)
    if pkg.has('[Content_Types].xml'):
        ct = pkg.xml('[Content_Types].xml')
        for el in list(ct):
            if el.tag.endswith('Override') and el.get('PartName') == '/' + name:
                ct.remove(el)
    for rn in [x for x in pkg.parts if x.endswith('.rels')]:
        owner = _rels_owner(rn)
        rel = pkg.rels(owner)
        for el in list(rel.root):
            if el.get('TargetMode') != 'External' and pkg.resolve(owner, el.get('Target') or '') == name:
                rel.root.remove(el)
                rel.dirty = True
    return True


def strip_comments(pkg):
    '''去掉全部批注（正文里的范围标记、批注部件、people.xml 里的作者名单），返回去掉的批注数。'''
    n = 0
    if pkg.has('word/comments.xml'):
        n = len(pkg.xml('word/comments.xml').findall(w('comment')))
    for name in _content_parts(pkg):
        root = pkg.xml(name)
        pm = _pm(root)
        for el in list(root.iter()):
            if el.tag in (w('commentRangeStart'), w('commentRangeEnd')):
                pm[el].remove(el)
            elif el.tag == w('commentReference'):
                r = pm[el]
                r.remove(el)
                if r.tag == W_R and all(ch.tag == w('rPr') for ch in r):
                    pm[r].remove(r)
    for name in COMMENT_PARTS:
        drop_part(pkg, name)
    return n


def accept_all(pkg):
    '''接受全部修订（复用定稿的实现）。'''
    from .finalize import accept_revisions
    return accept_revisions(pkg)


def _vanish(rpr):
    if rpr is None:
        return False
    v = rpr.find(w('vanish'))
    return v is not None and v.get(w('val')) not in OFF


def strip_hidden(pkg):
    '''去掉隐藏文字：隐藏的 run（含字符样式、段落样式隐藏）整个删除；段落标记上的隐藏属性只去掉属性。'''
    hid_char, hid_para = set(), set()
    if pkg.has('word/styles.xml'):
        for st in pkg.xml('word/styles.xml').findall(w('style')):
            if _vanish(st.find(w('rPr'))):
                (hid_para if st.get(w('type')) == 'paragraph' else hid_char).add(st.get(w('styleId')))
    n = 0
    for name in _content_parts(pkg):
        root = pkg.xml(name)
        pm = _pm(root)
        for ppr in root.iter(w('pPr')):
            mark = ppr.find(w('rPr'))
            if mark is not None:
                for tag in ('vanish', 'specVanish'):
                    el = mark.find(w(tag))
                    if el is not None:
                        mark.remove(el)
        for r in list(root.iter(W_R)):
            rpr = r.find(w('rPr'))
            rs = rpr.find(w('rStyle')) if rpr is not None else None
            hide = _vanish(rpr) or (rs is not None and rs.get(w('val')) in hid_char)
            if not hide and hid_para:
                p = pm.get(r)
                while p is not None and p.tag != W_P:
                    p = pm.get(p)
                pst = p.find(w('pPr')) if p is not None else None
                pst = pst.find(w('pStyle')) if pst is not None else None
                hide = pst is not None and pst.get(w('val')) in hid_para
            if hide and pm.get(r) is not None:
                try:
                    pm[r].remove(r)
                    n += 1
                except ValueError:
                    pass
    return n


def unwrap_ext_links(pkg):
    '''外部超链接：去掉链接、保留文字（链接可能指向本机路径）；只带 w:anchor 的内部链接不动。返回处理的链接数。'''
    n = 0
    for name in _content_parts(pkg):
        rel = pkg.rels(name)
        ext = {x[0] for x in rel.items() if x[3] == 'External' and (x[1] or '').endswith('/hyperlink')}
        if not ext:
            continue
        root = pkg.xml(name)
        pm = _pm(root)
        for h in list(root.iter(w('hyperlink'))):
            par = pm.get(h)
            if par is not None and h.get(qn('r:id')) in ext:
                idx = list(par).index(h)
                for k, ch in enumerate(list(h)):
                    par.insert(idx + k, ch)
                par.remove(h)
                n += 1
        for el in list(rel.root):
            if el.get('Id') in ext:
                rel.root.remove(el)
                rel.dirty = True
    return n


def strip_template_link(pkg):
    '''settings.xml 里的“附加模板”指向作者本机的模板路径，去掉。'''
    n = 0
    if pkg.has('word/settings.xml'):
        root = pkg.xml('word/settings.xml')
        for el in list(root.findall(w('attachedTemplate'))):
            root.remove(el)
            n += 1
        rel = pkg.rels('word/settings.xml')
        for el in list(rel.root):
            if el.get('TargetMode') == 'External' and (el.get('Type') or '').endswith('/attachedTemplate'):
                rel.root.remove(el)
                rel.dirty = True
    return n


def strip_custom_xml(pkg):
    '''去掉 customXml 部件、指向它们的数据绑定，以及正文里的 customXml 包装（里面的内容保留）。'''
    n = 0
    for name in [x for x in pkg.parts if x.startswith('customXml/') and not x.endswith('.rels')]:
        if drop_part(pkg, name):
            n += 1
    for name in _content_parts(pkg):
        root = pkg.xml(name)
        pm = _pm(root)
        for el in list(root.iter(w('dataBinding'))):
            if pm.get(el) is not None:
                pm[el].remove(el)
        for el in reversed(list(root.iter(w('customXml')))):      # 先内后外，嵌套时父节点才还在树上
            par = pm.get(el)
            if par is None:
                continue
            idx = list(par).index(el)
            for ch in [c for c in el if c.tag != w('customXmlPr')]:
                par.insert(idx, ch)
                idx += 1
            par.remove(el)
    return n


def blank_props(pkg):
    '''docProps：作者、最后修改者、公司、经理置空；删除自定义属性和缩略图。返回改动项数。'''
    n = 0
    for name, tags in (('docProps/core.xml', ('dc:creator', 'cp:lastModifiedBy')), ('docProps/app.xml', ('Company', 'Manager'))):
        data = pkg.parts.get(name)
        if not data or data[:2] in (b'\xff\xfe', b'\xfe\xff'):
            continue
        s = data.decode('utf-8', 'replace')
        for tag in tags:
            t = re.escape(tag)
            s2 = re.sub(r'(<%s(?:\s[^>]*)?>)[^<]*(</%s>)' % (t, t), r'\1\2', s)
            if s2 != s:
                n += 1
                s = s2
        pkg._xml.pop(name, None)
        pkg.parts[name] = s.encode('utf-8')
    for name in [x for x in pkg.parts if x == 'docProps/custom.xml' or x.startswith('docProps/thumbnail')]:
        if drop_part(pkg, name):
            n += 1
    return n


def bookmark_names(pkg):
    '''正文各部件里现有的书签名（净化后用来核对批注链接的目标还在不在）。'''
    out = set()
    for name in _content_parts(pkg):
        for b in pkg.xml(name).iter(w('bookmarkStart')):
            out.add(b.get(w('name')))
    return out


def other_external(pkg):
    '''去掉超链接后仍剩的外部引用（图片链接、OLE 链接等）数量：只登记提醒，不动它们。'''
    n = 0
    for rn in sorted(pkg.parts):
        if not rn.endswith('.rels'):
            continue
        for rid, rtype, target, mode in pkg.rels(_rels_owner(rn)).items():
            if mode == 'External' and not re.match(r'^cid:', target or '', re.I):
                n += 1
    return n


def sanitize_docx(pkg):
    '''净化一个已读入的源文件快照包，返回 (统计字典, 警告列表)。每一步单独保护：某步失败只登记警告，不让草稿失败。'''
    steps = (('comments', strip_comments, '批注'), ('revisions', accept_all, '修订'), ('hidden', strip_hidden, '隐藏文字'),
             ('customxml', strip_custom_xml, 'customXml'), ('links', unwrap_ext_links, '外部链接'),
             ('template', strip_template_link, '附加模板'), ('props', blank_props, '文档属性'))
    stat, warns = {}, []
    for key, fn, label in steps:
        try:
            stat[key] = fn(pkg)
        except Exception as e:
            stat[key] = 0
            warns.append('源文件快照净化“%s”失败（%s），这一项没有净化' % (label, e))
    try:
        stat['external_left'] = other_external(pkg)
    except Exception:
        stat['external_left'] = 0
    return stat, warns

