# -*- coding: utf-8 -*-
"""定稿：读取（人工审核后的）草稿 -> 删除助手批注及其回复、接受修订、比对人工修改 -> 输出终稿（docx/doc/pdf/md）。"""
from __future__ import annotations

import difflib
import os
import re
import shutil

from . import office
from .common import TOOL_AUTHOR, DIError, ensure_dir, lock_file_of, short
from .ooxml import W_P, W_R, W_T, Package, qn, w

W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
W15 = "http://schemas.microsoft.com/office/word/2012/wordml"
W16CID = "http://schemas.microsoft.com/office/word/2016/wordml/cid"
REV_UNWRAP = {w("ins"), w("moveTo")}
REV_REMOVE = {w("del"), w("moveFrom"), w("moveFromRangeStart"), w("moveFromRangeEnd"), w("moveToRangeStart"),
              w("moveToRangeEnd")}
REV_PROPS = {w("rPrChange"), w("pPrChange"), w("sectPrChange"), w("tblPrChange"), w("trPrChange"), w("tcPrChange"),
             w("tblGridChange"), w("numberingChange"), w("cellIns"), w("cellDel"), w("cellMerge")}
CONTENT_PARTS = re.compile(r"^word/(document|header\d*|footer\d*|footnotes|endnotes|comments)\.xml$")


def comments_info(pkg):
    """返回 [{id, author, text, paraIds}]"""
    out = []
    if not pkg.has("word/comments.xml"):
        return out
    for c in pkg.xml("word/comments.xml").findall(w("comment")):
        paras = c.findall(W_P)
        out.append({"id": c.get(w("id")), "author": c.get(w("author")) or "",
                    "text": "\n".join("".join(t.text or "" for t in p.iter(W_T)) for p in paras),
                    "paraIds": [p.get("{%s}paraId" % W14) for p in paras if p.get("{%s}paraId" % W14)], "el": c})
    return out


def _parent_map(root):
    return {c: p for p in root.iter() for c in p}


def remove_comments(pkg, ids):
    ids = set(ids)
    if not ids:
        return 0
    croot = pkg.xml("word/comments.xml")
    removed_para = set()
    for c in list(croot.findall(w("comment"))):
        if c.get(w("id")) in ids:
            for p in c.findall(W_P):
                pid = p.get("{%s}paraId" % W14)
                if pid:
                    removed_para.add(pid)
            croot.remove(c)
    for name in list(pkg.parts):
        if not CONTENT_PARTS.match(name) or name == "word/comments.xml":
            continue
        root = pkg.xml(name)
        pm = _parent_map(root)
        for el in list(root.iter()):
            if el.tag in (w("commentRangeStart"), w("commentRangeEnd")) and el.get(w("id")) in ids:
                pm[el].remove(el)
            elif el.tag == w("commentReference") and el.get(w("id")) in ids:
                r = pm[el]
                r.remove(el)
                if r.tag == W_R and all(ch.tag == w("rPr") for ch in r):
                    pm[r].remove(r)
    # commentsExtended / commentsIds / commentsExtensible
    durable = set()
    if pkg.has("word/commentsIds.xml"):
        root = pkg.xml("word/commentsIds.xml")
        for el in list(root):
            if el.get("{%s}paraId" % W16CID) in removed_para:
                durable.add(el.get("{%s}durableId" % W16CID))
                root.remove(el)
    if pkg.has("word/commentsExtended.xml"):
        root = pkg.xml("word/commentsExtended.xml")
        for el in list(root):
            if el.get("{%s}paraId" % W15) in removed_para:
                root.remove(el)
    if pkg.has("word/commentsExtensible.xml") and durable:
        root = pkg.xml("word/commentsExtensible.xml")
        for el in list(root):
            if any(v in durable for v in el.attrib.values()):
                root.remove(el)
    return len(ids)


def replies_of(pkg, parent_ids, infos):
    """Word 2013+ 的回复：commentsExtended 中 paraIdParent 指向被删批注。"""
    if not pkg.has("word/commentsExtended.xml"):
        return set()
    para2id = {}
    for c in infos:
        for pid in c["paraIds"]:
            para2id[pid] = c["id"]
    parents = {pid for c in infos if c["id"] in parent_ids for pid in c["paraIds"]}
    out = set()
    changed = True
    while changed:
        changed = False
        for el in pkg.xml("word/commentsExtended.xml"):
            pp = el.get("{%s}paraIdParent" % W15)
            me = el.get("{%s}paraId" % W15)
            if pp in parents and me in para2id and para2id[me] not in out:
                out.add(para2id[me])
                parents.add(me)
                changed = True
    return out


def accept_revisions(pkg):
    n = 0
    for name in list(pkg.parts):
        if not CONTENT_PARTS.match(name):
            continue
        root = pkg.xml(name)
        changed = True
        while changed:
            changed = False
            pm = _parent_map(root)
            for el in list(root.iter()):
                if el.tag in REV_REMOVE or el.tag in REV_PROPS:
                    par = pm.get(el)
                    if par is not None and el in list(par):
                        par.remove(el)
                        n += 1
                        changed = True
                elif el.tag in REV_UNWRAP:
                    par = pm.get(el)
                    if par is None or el not in list(par):
                        continue
                    if par.tag in (w("rPr"), w("trPr")):     # 段落标记/行的插入标记
                        par.remove(el)
                    else:
                        idx = list(par).index(el)
                        for k, ch in enumerate(list(el)):
                            par.insert(idx + k, ch)
                        par.remove(el)
                    n += 1
                    changed = True
            # 已删除的表格行
            pm = _parent_map(root)
            for tr in list(root.iter(w("tr"))):
                trpr = tr.find(w("trPr"))
                if trpr is not None and trpr.find(w("del")) is not None:
                    pm[tr].remove(tr)
                    n += 1
                    changed = True
            if changed:
                break
    return n


def texts_by_id(path):
    from .read_docx import DocxReader
    m = DocxReader(path).read()
    out = []
    for b in m["blocks"]:
        if b["t"] == "p":
            out.append((b["id"], b["text"]))
        elif b["t"] == "tbl":
            for c in b["cells"]:
                out.append(("%s.R%d.C%d" % (b["id"], c["r"], c["c"]), c["text"]))
    return out


def human_changes(pristine, final, limit=60):
    if not pristine or not os.path.exists(pristine):
        return []
    a, b = texts_by_id(pristine), texts_by_id(final)
    da, db = dict(a), dict(b)
    out = []
    if [x for x, _ in a] == [x for x, _ in b]:
        for k, v in a:
            if db.get(k, "") != v and not k.count(".P") > 1:
                out.append("%s：“%s” → “%s”" % (k, short(v, 40), short(db.get(k, ""), 40)))
    else:
        la = ["%s" % v for _, v in a if v.strip()]
        lb = ["%s" % v for _, v in b if v.strip()]
        for line in difflib.unified_diff(la, lb, lineterm="", n=0):
            if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
                out.append(short(line, 90))
    return out[:limit]


NL = chr(10)
LOCAL_PATH = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]:[/\\]"       # drive letter
    r"|(?<![:/\\])[/\\]{2}[\w.-]+[/\\]"     # UNC share
    r"|/(?:Users|home)/"                     # POSIX home directory
    r"|(?i:\bfile:/{2})"                     # file URI
)
MARKERS = ('待补充', '⚠', '原有值', '本批未核', '按推荐默认')


def prune_comment_rels(pkg):
    '''批注删除后，comments.xml.rels 里不再被引用的外部链接（指向“源文件快照”）要一并清掉，否则成品里仍带着内部相对路径。'''
    rn = pkg.rels_name('word/comments.xml')
    if rn not in pkg.parts:
        return 0
    rel = pkg.rels('word/comments.xml')
    used = set()
    if pkg.has('word/comments.xml'):
        for el in pkg.xml('word/comments.xml').iter():
            v = el.get(qn('r:id'))
            if v:
                used.add(v)
    gone = [el for el in list(rel.root) if el.get('Id') not in used]
    for el in gone:
        rel.root.remove(el)
    if gone:
        rel.dirty = True
    return len(gone)


def recipient_check(path):
    '''用收件人的视角重新打开刚生成的成品：扫描残留的待办标记、助手批注、本机路径/内部链接、修订、隐藏文字、文档属性。
    返回 (blockers, warnings)；blockers 是不处理就不该外发的问题。'''
    import posixpath
    pkg = Package(path)
    block, warn = [], []
    for name in sorted(pkg.parts):
        if not re.match('^word/(document|header[0-9]*|footer[0-9]*|footnotes|endnotes)[.]xml$', name):
            continue
        root = pkg.xml(name)
        txt = ''.join(t.text or '' for t in root.iter(W_T))
        base = name.split('/')[-1]
        for m in MARKERS:
            n = txt.count(m)
            if n:        # 同一标记出现多处要报全部数量，只报第一处会让审核人漏改其余几处
                i = txt.find(m)
                block.append('%s 含待办标记“%s”（%d 处）：…%s…' % (base, m, n, short(txt[max(0, i - 10): i + 24], 40)))
        mm = LOCAL_PATH.search(txt)
        if mm:
            block.append('%s 含疑似本机路径：%s' % (base, short(txt[max(0, mm.start() - 6): mm.end() + 24], 40)))
        nh = sum(1 for _ in root.iter(w('vanish')))
        if nh:
            warn.append('%s 含 %d 处隐藏文字，外发前确认' % (base, nh))
        nr = sum(1 for e in root.iter() if e.tag in (w('ins'), w('del'), w('moveFrom'), w('moveTo')))
        if nr:
            block.append('%s 仍有 %d 处未接受的修订' % (base, nr))
    infos = comments_info(pkg)
    tool = [c for c in infos if c['author'] == TOOL_AUTHOR]
    if tool:
        block.append('成品仍含 %d 条助手批注' % len(tool))
    other = [c for c in infos if c['author'] != TOOL_AUTHOR]
    if other:
        warn.append('成品仍保留 %d 条批注（作者：%s）' % (len(other), '、'.join(sorted({c['author'] for c in other}))))
    for name in sorted(pkg.parts):
        if not name.endswith('.rels') or name == '_rels/.rels':
            continue
        part = posixpath.join(posixpath.dirname(posixpath.dirname(name)), posixpath.basename(name)[:-5])
        for rid, rtype, target, mode in pkg.rels(part).items():
            if mode != 'External':
                continue
            t = target or ''
            if re.match('^cid:', t, re.I):
                continue  # 模板自带的内嵌图片引用（来自邮件/mht 转换），不是外部路径
            if re.match('^(https?|mailto):', t, re.I):
                warn.append('含外部链接 %s' % short(t, 50))
            else:
                block.append('%s 含指向本机/内部的外部链接：%s' % (posixpath.basename(part), short(t, 50)))
    core = pkg.parts.get('docProps/core.xml', b'').decode('utf-8', 'replace')
    for tag, label in (('dc:creator', '作者'), ('cp:lastModifiedBy', '最后修改者')):
        m = re.search('<%s[^>]*>([^<]+)</%s>' % (tag, tag), core)
        if m:
            warn.append('文档属性里的%s：%s（外发前确认是否可以带出）' % (label, m.group(1)))
    if pkg.has('customXml/item1.xml'):
        warn.append('含 customXml 部件（可能带有内部元数据）')
    return block, warn


def finalize(job, draft, out_formats=None, reviewer_comments="ask", strip_template_comments=False, accept_unverified=False):
    if lock_file_of(draft):
        raise DIError("草稿似乎仍在 Word/WPS 中打开（存在 ~$ 锁文件）。请先保存并关闭草稿后再定稿：%s" % draft)
    pkg = Package(draft)
    infos = comments_info(pkg)
    tmpl = {(a, t) for a, t in job.data.get("template_comments", [])}
    tool = {c["id"] for c in infos if c["author"] == TOOL_AUTHOR}
    tool |= replies_of(pkg, tool, infos)
    template = {c["id"] for c in infos if (c["author"], c["text"]) in tmpl}
    reviewer = [c for c in infos if c["id"] not in tool and c["id"] not in template]
    if reviewer and reviewer_comments == "ask":
        lines = ["草稿中有 %d 条审核人批注（不是助手生成的）：" % len(reviewer)]
        for c in reviewer[:15]:
            lines.append("  [%s] %s" % (c["author"], short(c["text"], 60)))
        lines.append("请先按批注修改（可让助手处理），然后用 --reviewer-comments drop(删除) 或 keep(保留) 重新定稿。")
        raise DIError("\n".join(lines))
    rm = set(tool)
    if reviewer_comments == "drop":
        rm |= {c["id"] for c in reviewer}
    if strip_template_comments:
        rm |= template
    remove_comments(pkg, rm)
    prune_comment_rels(pkg)
    nrev = accept_revisions(pkg)
    out_dir = ensure_dir(job.p("终稿"))
    final_docx = os.path.join(out_dir, "%s_终稿.docx" % job.stem)
    chk_dir = ensure_dir(os.path.join(out_dir, '.待检查'))
    chk_path = os.path.join(chk_dir, os.path.basename(final_docx))
    pkg.save(chk_path)      # 先存到暂存位置，检查通过才替换正式稿，避免旧的正式稿被未检查的内容覆盖
    # 收件人视角：重新打开刚生成的成品做外发检查，再核对草稿登记的“本批未核”项
    blockers, warns = recipient_check(chk_path)
    try:      # 结构自检：严重项拦截，注意项只提示；自检自己出错按严重项登记，不能当作通过
        from . import selfcheck
        srep = selfcheck.check_file(chk_path)
        blockers += selfcheck.final_blockers(srep)
        nw = srep.count(selfcheck.WARN)
        if nw:
            warns.append('结构自检有 %d 项提示（%s），可用 doctor --selfcheck 文件 查看' % (nw, '、'.join(selfcheck.TITLES.get(c, c) for c in srep.codes(selfcheck.WARN)[:4])))
    except Exception as e:
        blockers.append('结构问题-自检没跑完：%s: %s' % (type(e).__name__, e))
    unv = (job.data.get('unverified') or {}).get(os.path.basename(draft), [])
    grp = {}
    if unv:
        cur = dict(texts_by_id(chk_path))
        answers = job.read('answers.json', {}) or {}
        for it in unv:
            if it.get('kind') in ('source', 'unread'):
                eff = (answers.get(it.get('key')) or {}).get('effect')
                if not (isinstance(eff, dict) and eff.get('ok')):
                    blockers.append('未核：%s' % (it.get('detail') or it.get('name', '')))
            elif it.get('kind') == 'default':
                if any(x.get('key') == it.get('key') and x.get('kind') in ('source', 'unread') for x in unv):
                    continue      # 同一问题已按“未核”登记，不重复计数
                a = answers.get(it.get('key'))
                if not (isinstance(a, dict) and 'effect' in a and not a.get('default')):
                    blockers.append('按推荐默认未确认：%s' % it.get('detail', ''))
                elif it.get('effect') is not None and a.get('effect') != it.get('effect'):
                    blockers.append('回答与草稿采用的默认处理不同，需重新出草稿：%s' % it.get('detail', ''))
            elif cur.get(it.get('cell')) == it.get('text'):
                grp.setdefault((it.get('why') or '原有值本批未核', it.get('param', '')), []).append('%s%s=“%s”' % (it.get('sample', ''), '#' if it.get('sample') else '', short(it.get('text', ''), 12)))
    for (why, param), vals in grp.items():
        blockers.append('%s：%s %s' % (why, param, '、'.join(vals[:6]) + ('…共%d处' % len(vals) if len(vals) > 6 else '')))
    accepted = []
    legacy = os.path.join(out_dir, '终稿遗留项.md')
    if blockers:
        if not accept_unverified:
            os.remove(chk_path)
            cats = {}
            for b in blockers:
                head, _, rest = b.partition('：')
                if not rest:
                    head, rest = '其他', b
                else:
                    pm = re.search(r'（[^）]*）', head)
                    if pm:        # 括号里的可变说明（如“规范为1”）挪到明细里，避免同类问题被拆成多类
                        head = head.replace(pm.group(0), '')
                        rest = '%s %s' % (rest, pm.group(0))
                cats.setdefault(head, []).append(rest)
            full = job.p('终稿拦截清单.md')
            with open(full, 'w', encoding='utf-8') as f:
                f.write('# 终稿拦截清单（完整）' + NL)
                for h, v in cats.items():
                    f.write(NL + '## %s（%d 项）' % (h, len(v)) + NL + NL.join('- ' + x for x in v) + NL)
            lines = ['%s（%d 项）：%s%s' % (h, len(v), '；'.join(short(x, 40) for x in v[:2]), '…' if len(v) > 2 else '') for h, v in cats.items()]
            raise DIError('终稿未通过外发检查，没有生成正式稿（草稿已保留）。共 %d 类 %d 项：' % (len(cats), len(blockers)) + NL + '  - ' + (NL + '  - ').join(lines) + NL + '完整清单见 ' + full + NL + '处理后重新 final；确认可以带着这些问题发出，再加 --accept-unverified（会登记到 终稿遗留项.md）。')
        accepted = blockers
        with open(legacy, 'w', encoding='utf-8') as f:
            f.write('# 终稿遗留项（带着这些问题定稿；仅内部查看，不要外发）' + NL + NL + NL.join('- ' + b for b in blockers) + NL + NL + '## 其他提示' + NL + NL + NL.join('- ' + x for x in warns) + NL)
    elif os.path.exists(legacy):
        os.remove(legacy)
    os.replace(chk_path, final_docx)
    try:
        os.rmdir(chk_dir)
    except OSError:
        pass
    produced = [final_docx]
    fmts = [f.lower().lstrip(".") for f in (out_formats or job.data.get("formats") or [])]
    orig_ext = os.path.splitext(job.data["target"]["path"])[1].lower().lstrip(".")
    if not fmts and orig_ext not in ("docx",):
        fmts = [orig_ext] if orig_ext in ("doc", "wps", "rtf") else []
    errs = []
    conv = [(final_docx, os.path.join(out_dir, "%s_终稿.%s" % (job.stem, f)), f) for f in fmts if f in ("doc", "pdf", "rtf")]
    if conv:
        res = office.convert_many(conv)
        for s, d, f in conv:
            if res.get(d):
                errs.append("%s：%s" % (f, res[d]))
            else:
                produced.append(d)
    if "md" in fmts:
        from .export_md import docx_to_md
        md_path = os.path.join(out_dir, "%s_终稿.md" % job.stem)
        docx_to_md(final_docx, md_path)
        produced.append(md_path)
    pristine = os.path.join(os.path.dirname(draft), ".原始", os.path.basename(draft))
    changes = human_changes(pristine, final_docx)
    return {"files": produced, "removed_comments": len(rm), "kept_template_comments": len(template - rm),
            "reviewer_comments": len(reviewer), "revisions": nrev, "changes": changes, "errors": errs,
            "blockers": accepted, "warnings": warns}
