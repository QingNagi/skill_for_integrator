# -*- coding: utf-8 -*-
'''结构自检：不依赖办公软件，只用标准库检查 docx/xlsx 的 OOXML 结构。
入口：check_file(路径)->Report；render(rep) 出简短中文；final_blockers(rep) 供终稿拦截；
environment_lines() 供 doctor 汇总环境。
严重度：error=Word 可能报“无法读取的内容”或批注/链接失效，终稿拦截；
warn=有兼容风险，只提示；info=登记（用到扩展部件/命名空间，不算错）。
自检本身没跑完按 error 登记，不能把“没检查完”当成“通过”。
兼容 Python 3.8 及以上；不导入第三方包。'''
from __future__ import annotations

import os
import posixpath
import re
import zipfile
from urllib.parse import unquote

from .environment import environment_lines
from .common import TOOL_AUTHOR
from .safe_xml import parse_xml

ERROR, WARN, INFO = 'error', 'warn', 'info'
SEV_NAME = {ERROR: '严重', WARN: '注意', INFO: '登记'}
SEV_ORDER = (ERROR, WARN, INFO)
MAX_PER_CODE = 30      # 同一类问题最多存 30 条明细，其余只计数

NS_W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
NS_R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
NS_WP = 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'
NS_MC = 'http://schemas.openxmlformats.org/markup-compatibility/2006'
NS_CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
NS_PR = 'http://schemas.openxmlformats.org/package/2006/relationships'
NS_X = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
KIND_BY_EXT = {'.docx': 'docx', '.docm': 'docx', '.dotx': 'docx', '.xlsx': 'xlsx', '.xlsm': 'xlsx', '.xltx': 'xlsx'}
TITLES = {}            # 类别代码 -> 中文名，见文件末尾
XLSX_CHECKS = []       # write_xlsx 线补 xlsx 专有检查时往这里登记：函数签名 fn(pkg, rep)


def q(ns, local):
    return '{%s}%s' % (ns, local)


def W(local):
    return q(NS_W, local)


def _local(tag):
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else ''


class Report(object):
    '''自检结果：items 是明细（每类最多 MAX_PER_CODE 条），over 记录超出部分的数量。'''

    def __init__(self, path):
        self.path = path
        self.items = []
        self.over = {}
        self._seen = set()
        self._n = {}
        self.comment_counts = None

    def add(self, sev, code, part, msg):
        key = (sev, code, part, msg)
        if key in self._seen:
            return
        self._seen.add(key)
        n = self._n.get(code, 0) + 1
        self._n[code] = n
        if n > MAX_PER_CODE:
            self.over[(sev, code)] = self.over.get((sev, code), 0) + 1
            return
        self.items.append({'sev': sev, 'code': code, 'part': part or '', 'msg': msg})

    def count(self, sev):
        return sum(1 for x in self.items if x['sev'] == sev) + sum(v for (s, c), v in self.over.items() if s == sev)

    def has_errors(self):
        return self.count(ERROR) > 0

    def codes(self, sev=None):
        return sorted({x['code'] for x in self.items if sev is None or x['sev'] == sev})

    def groups(self):
        '''按（严重度, 类别）分组：[(sev, code, [明细], 超出数量)]，严重的在前。'''
        by = {}
        for x in self.items:
            by.setdefault((x['sev'], x['code']), []).append(x)
        for k in self.over:
            by.setdefault(k, [])
        keys = sorted(by, key=lambda k: (SEV_ORDER.index(k[0]), k[1]))
        return [(s, c, by[(s, c)], self.over.get((s, c), 0)) for s, c in keys]


class Pkg(object):
    '''只读的 OOXML 包：按需读取、解析并缓存；解析失败登记 xml-bad，不抛异常。'''

    def __init__(self, path, rep):
        self.path, self.rep = path, rep
        self.zf = None
        self.infos, self.names, self.index = [], [], set()
        self.main, self.kind = None, None
        self._xml, self._rels, self._raw = {}, {}, {}
        self.namespaces = {}

    def open(self):
        self.zf = zipfile.ZipFile(self.path)
        self.infos = self.zf.infolist()
        self.names = [i.filename for i in self.infos]
        self.index = set(self.names)

    def close(self):
        if self.zf is not None:
            try:
                self.zf.close()
            except Exception:
                pass

    def has(self, name):
        return name in self.index

    def read(self, name):
        if name not in self._raw:
            try:
                self._raw[name] = self.zf.read(name)
            except Exception as e:
                self._raw[name] = None
                self.rep.add(ERROR, 'zip-read', name, '部件读不出来：%s' % e)
        return self._raw[name]

    def xml(self, name):
        if name in self._xml:
            return self._xml[name]
        root = None
        data = self.read(name) if name in self.index else None
        if data is not None:
            try:
                declarations, root_declarations = {}, {}
                root = parse_xml(data, declarations, root_declarations)
                self.namespaces[name] = root_declarations, declarations
            except Exception as e:
                self.rep.add(ERROR, 'xml-bad', name, 'XML 不良构：%s' % e)
        self._xml[name] = root
        return root

    def rels_name(self, part):
        d, b = posixpath.split(part)
        return posixpath.join(d, '_rels', b + '.rels')

    def rels(self, part):
        '''part 的关系列表 [{id,type,target,mode}]；part='' 表示包根 _rels/.rels。'''
        rn = self.rels_name(part)
        if rn in self._rels:
            return self._rels[rn]
        out = []
        root = self.xml(rn) if rn in self.index else None
        if root is not None:
            for el in root:
                if _local(el.tag) == 'Relationship':
                    out.append({'id': el.get('Id'), 'type': el.get('Type') or '', 'target': el.get('Target'),
                                'mode': el.get('TargetMode') or 'Internal'})
        self._rels[rn] = out
        return out

    def resolve(self, part, target):
        '''相对 part 的内部 Target -> 包内部件名（已做百分号解码）；越出包根返回 None。'''
        t = unquote((target or '').split('#')[0])
        if t.startswith('/'):
            r = posixpath.normpath(t[1:])
        else:
            r = posixpath.normpath(posixpath.join(posixpath.dirname(part), t))
        return None if r.startswith('..') else r

    def detect_kind(self, hint=None):
        for r in self.rels(''):
            if r['type'].endswith('/officeDocument') and r['mode'] != 'External' and r['target']:
                self.main = self.resolve('', r['target'])
        if self.main:
            self.kind = 'docx' if self.main.startswith('word/') else ('xlsx' if self.main.startswith('xl/') else None)
        if not self.kind and hint:
            self.kind = hint
        return self.kind

    def content_parts(self):
        '''docx 的正文类部件（正文、页眉页脚、脚注尾注），主文档在前。'''
        pat = re.compile('^word/(document|header[0-9]*|footer[0-9]*|footnotes|endnotes)[.]xml$')
        out = [n for n in self.index if pat.match(n)]
        if self.main and self.main.startswith('word/') and self.main in self.index and self.main not in out:
            out.append(self.main)
        return sorted(out, key=lambda n: (n != self.main, n))


def _run(fn, pkg, rep):
    try:
        return fn(pkg, rep)
    except Exception as e:
        rep.add(ERROR, 'selfcheck-incomplete', '', '自检步骤“%s”没跑完（工具自身出错：%s: %s），不能当作通过' % (fn.__name__, type(e).__name__, e))
        return None


# ======================================================================= 检查段（逐段填充）
# ---- zip 层
def check_zip(pkg, rep):
    '''zip 层：能打开、条目不重复、[Content_Types].xml 是第一个条目、条目名合规、数据无损坏。返回 False 表示后面的检查没法做。'''
    try:
        pkg.open()
    except zipfile.BadZipFile as e:
        rep.add(ERROR, 'zip-bad', '', '不是有效的 zip/OOXML 文件：%s' % e)
        return False
    except Exception as e:
        rep.add(ERROR, 'zip-bad', '', '打不开：%s: %s' % (type(e).__name__, e))
        return False
    if not pkg.names:
        rep.add(ERROR, 'zip-empty', '', '压缩包里没有任何条目')
        return False
    seen, low = {}, {}
    for n in pkg.names:
        seen[n] = seen.get(n, 0) + 1
    for n in seen:
        low.setdefault(n.lower(), []).append(n)
    for n, c in seen.items():
        if c > 1:
            rep.add(ERROR, 'zip-duplicate', n, '压缩包里有 %d 个同名条目' % c)
    for v in low.values():
        if len(v) > 1:
            rep.add(ERROR, 'zip-duplicate', v[0], '条目名仅大小写不同：%s' % '、'.join(v))
    if '[Content_Types].xml' not in seen:
        rep.add(ERROR, 'ct-missing', '[Content_Types].xml', '缺少 [Content_Types].xml')
    elif pkg.names[0] != '[Content_Types].xml':
        rep.add(ERROR, 'zip-order', '[Content_Types].xml', '[Content_Types].xml 不是第一个条目（第一个是 %s），部分软件会打不开' % pkg.names[0])
    if '_rels/.rels' not in seen:
        rep.add(ERROR, 'rels-missing', '_rels/.rels', '缺少包根关系文件 _rels/.rels')
    for n in pkg.names:
        if '\\' in n or n.startswith('/') or '..' in n.split('/'):
            rep.add(ERROR, 'zip-name', n, '条目名不合规（含反斜杠、绝对路径或 ..）')
    try:
        bad = pkg.zf.testzip()
    except Exception as e:
        bad = None
        rep.add(ERROR, 'zip-crc', '', '校验压缩数据时出错：%s' % e)
    if bad:
        rep.add(ERROR, 'zip-crc', bad, '条目数据损坏（CRC 校验失败）')
    return True


# ---- XML 良构
def check_wellformed(pkg, rep):
    '''所有 .xml / .rels 部件都要能解析；失败由 pkg.xml 登记为 xml-bad。'''
    n = 0
    for name in pkg.names:
        if name.lower().endswith(('.xml', '.rels')) and not name.endswith('/'):
            n += 1
            pkg.xml(name)
    return n


# ---- [Content_Types].xml
CT_WML = 'application/vnd.openxmlformats-officedocument.wordprocessingml.'
CT_SML = 'application/vnd.openxmlformats-officedocument.spreadsheetml.'
MAIN_CT = {
    'docx': {CT_WML + 'document.main+xml', CT_WML + 'template.main+xml',
             'application/vnd.ms-word.document.macroEnabled.main+xml',
             'application/vnd.ms-word.template.macroEnabledTemplate.main+xml'},
    'xlsx': {CT_SML + 'sheet.main+xml', CT_SML + 'template.main+xml',
             'application/vnd.ms-excel.sheet.macroEnabled.main+xml',
             'application/vnd.ms-excel.template.macroEnabled.main+xml'},
}
EXPECT_CT = [
    (re.compile('^word/comments[.]xml$'), CT_WML + 'comments+xml'),
    (re.compile('^word/styles[.]xml$'), CT_WML + 'styles+xml'),
    (re.compile('^word/numbering[.]xml$'), CT_WML + 'numbering+xml'),
    (re.compile('^word/settings[.]xml$'), CT_WML + 'settings+xml'),
    (re.compile('^word/footnotes[.]xml$'), CT_WML + 'footnotes+xml'),
    (re.compile('^word/endnotes[.]xml$'), CT_WML + 'endnotes+xml'),
    (re.compile('^word/header[0-9]*[.]xml$'), CT_WML + 'header+xml'),
    (re.compile('^word/footer[0-9]*[.]xml$'), CT_WML + 'footer+xml'),
    (re.compile('^word/fontTable[.]xml$'), CT_WML + 'fontTable+xml'),
    (re.compile('^xl/sharedStrings[.]xml$'), CT_SML + 'sharedStrings+xml'),
    (re.compile('^xl/styles[.]xml$'), CT_SML + 'styles+xml'),
    (re.compile('^xl/worksheets/[^/]+[.]xml$'), CT_SML + 'worksheet+xml'),
]


def effective_ct(pkg, name):
    '''部件的实际内容类型：Override 优先，其次按扩展名的 Default；都没有返回 None。'''
    t = getattr(pkg, 'ct_over', {}).get('/' + name.lower())
    if t is not None:
        return t
    base = name.rsplit('/', 1)[-1]
    ext = base.rsplit('.', 1)[-1].lower() if '.' in base else ''      # 不用 splitext：它把 .rels 当成无扩展名的隐藏文件
    return getattr(pkg, 'ct_def', {}).get(ext)


def check_content_types(pkg, rep):
    pkg.ct_over, pkg.ct_def = {}, {}
    name = '[Content_Types].xml'
    if name not in pkg.index:
        return None      # zip 层已登记缺失
    root = pkg.xml(name)
    if root is None:
        return None      # 已登记 xml-bad
    if root.tag != q(NS_CT, 'Types'):
        rep.add(ERROR, 'ct-root', name, '根元素不是 Types（命名空间应为 %s）' % NS_CT)
    lower_names = {n.lower() for n in pkg.names}
    for el in root:
        t = _local(el.tag)
        ct = el.get('ContentType')
        if t == 'Default':
            ext = (el.get('Extension') or '').lower()
            if not ext or not ct:
                rep.add(ERROR, 'ct-bad', name, 'Default 缺少 Extension 或 ContentType')
            elif ext in pkg.ct_def:
                rep.add(ERROR, 'ct-duplicate', name, '扩展名 %s 的 Default 重复' % ext)
            else:
                pkg.ct_def[ext] = ct
        elif t == 'Override':
            pn = el.get('PartName') or ''
            if not pn.startswith('/') or not ct:
                rep.add(ERROR, 'ct-bad', name, 'Override 的 PartName 须以 / 开头且必须有 ContentType：%s' % pn)
            elif pn.lower() in pkg.ct_over:
                rep.add(ERROR, 'ct-duplicate', name, '部件 %s 的 Override 重复' % pn)
            else:
                pkg.ct_over[pn.lower()] = ct
                if pn[1:].lower() not in lower_names:
                    rep.add(WARN, 'ct-dangling', name, 'Override 指向包里不存在的部件：%s' % pn)
    for n in sorted(set(pkg.names)):
        if n == name or n.endswith('/'):
            continue
        ct = effective_ct(pkg, n)
        if ct is None:
            rep.add(ERROR, 'ct-part', n, '部件没有内容类型（既没有 Override，也没有该扩展名的 Default）')
            continue
        for pat, want in EXPECT_CT:
            if pat.match(n) and ct != want:
                rep.add(ERROR, 'ct-wrong', n, '内容类型应为 …%s，实际是 %s' % (want.rsplit('.', 1)[-1], ct))
                break
    kind = pkg.detect_kind(None)
    if pkg.main and pkg.main in pkg.index and kind in MAIN_CT:
        ct = effective_ct(pkg, pkg.main)
        if ct not in MAIN_CT[kind]:
            rep.add(ERROR, 'ct-main', pkg.main, '主文档的内容类型不对：%s（Word/Excel 可能拒绝打开）' % ct)
    return None


# ---- 关系文件
def rels_source(rels_name):
    '''word/_rels/document.xml.rels -> word/document.xml；包根 _rels/.rels -> 空串。'''
    d = posixpath.dirname(posixpath.dirname(rels_name))
    return posixpath.join(d, posixpath.basename(rels_name)[:-5])


def check_rels(pkg, rep):
    '''每个 .rels：根元素正确、Id 唯一、必填属性齐全、内部目标存在且不越出包根；外部目标只查非空。'''
    lower = {}
    for n in pkg.names:
        lower.setdefault(n.lower(), n)
    for rn in sorted(n for n in set(pkg.names) if n.endswith('.rels')):
        root = pkg.xml(rn)
        if root is None:
            continue
        if root.tag != q(NS_PR, 'Relationships'):
            rep.add(ERROR, 'rels-root', rn, '根元素不是 Relationships（命名空间应为 %s）' % NS_PR)
            continue
        src = rels_source(rn)
        if src and src not in pkg.index and src.lower() not in lower:
            rep.add(WARN, 'rels-orphan', rn, '关系文件对应的部件不存在：%s' % src)
        ids = set()
        for el in root:
            if _local(el.tag) != 'Relationship':
                rep.add(ERROR, 'rels-bad', rn, '关系文件里有非 Relationship 元素：%s' % _local(el.tag))
                continue
            rid, typ, tgt = el.get('Id'), el.get('Type'), el.get('Target')
            if not rid or not typ or tgt is None:
                rep.add(ERROR, 'rels-bad', rn, '关系缺少 Id、Type 或 Target（Id=%s）' % rid)
                continue
            if rid in ids:
                rep.add(ERROR, 'rels-dup-id', rn, '关系 Id 重复：%s' % rid)
            ids.add(rid)
            mode = el.get('TargetMode') or 'Internal'
            if mode not in ('Internal', 'External'):
                rep.add(ERROR, 'rels-bad', rn, '关系 %s 的 TargetMode 不合法：%s' % (rid, mode))
            if not tgt.strip():
                rep.add(ERROR, 'rels-target', rn, '关系 %s 的 Target 为空' % rid)
                continue
            if mode == 'External':
                continue
            dst = pkg.resolve(src, tgt)
            if dst is None:
                rep.add(ERROR, 'rels-target', rn, '关系 %s 的目标越出包根：%s' % (rid, tgt[:60]))
            elif dst in pkg.index:
                continue
            elif dst.lower() in lower:
                rep.add(WARN, 'rels-case', rn, '关系 %s 的目标大小写与部件名不一致：%s' % (rid, dst))
            else:
                rep.add(ERROR, 'rels-target', rn, '关系 %s 指向的部件不存在：%s' % (rid, tgt[:60]))
    return None


R_PFX = '{' + NS_R + '}'
REL_TYPE_OF = {'hyperlink': '/hyperlink', 'headerReference': '/header', 'footerReference': '/footer'}


def check_rid_refs(pkg, rep):
    '''部件里的 r:id / r:embed 等引用必须在该部件的关系文件里有对应项；超链接、页眉页脚引用的关系类型要对。'''
    for name in sorted(set(pkg.names)):
        if not name.endswith('.xml') or name == '[Content_Types].xml':
            continue
        root = pkg.xml(name)
        if root is None:
            continue
        rels = {}
        for r in pkg.rels(name):
            rels.setdefault(r['id'], r)
        for el in root.iter():
            for k, v in el.attrib.items():
                if not k.startswith(R_PFX):
                    continue
                rel = rels.get(v)
                if rel is None:
                    rep.add(ERROR, 'rid-missing', name, '%s 引用了不存在的关系 %s（属性 r:%s）' % (_local(el.tag), v, _local(k)))
                    continue
                want = REL_TYPE_OF.get(_local(el.tag))
                if want and _local(k) == 'id' and not rel['type'].endswith(want):
                    rep.add(ERROR, 'rid-type', name, '%s 引用的关系 %s 类型不对：%s' % (_local(el.tag), v, rel['type'].rsplit('/', 1)[-1]))
    return None


# ---- 扩展部件与扩展命名空间（w14/w15 等：用到必须登记；声明错误才算严重）
EXT_URI = 'http://schemas.microsoft.com/office/'
EXT_PART = re.compile('^word/(commentsExtended|commentsIds|commentsExtensible|people|intelligence[0-9]*)[.]xml$|^word/webextensions/|^xl/(threadedComments|persons)/')
MC_IGNORABLE = q(NS_MC, 'Ignorable')


def _ns_usage(root):
    '''统计扩展命名空间在元素名、属性名里的使用：{uri: [总数, 其中在 mc:AlternateContent 内的数量]}。'''
    use = {}
    ac = q(NS_MC, 'AlternateContent')
    stack = [(root, False)]
    while stack:
        el, in_ac = stack.pop()
        for nm in [el.tag] + list(el.attrib):
            if isinstance(nm, str) and nm.startswith('{' + EXT_URI):
                u = nm[1:].split('}', 1)[0]
                c = use.setdefault(u, [0, 0])
                c[0] += 1
                if in_ac:
                    c[1] += 1
        sub = in_ac or el.tag == ac
        for ch in el:
            stack.append((ch, sub))
    return use


def check_ext(pkg, rep):
    '''1) 扩展部件（commentsExtended/commentsIds/people 等）：登记为 warn。
    2) 正文类部件里用到的扩展命名空间：在 mc:AlternateContent 内或已列入 mc:Ignorable 只登记；否则 warn。
    3) mc:Ignorable / Choice 的 Requires 引用了没声明的前缀：error。'''
    seen = set()

    def reg(part, how):
        if part in seen:
            return
        seen.add(part)
        rep.add(WARN, 'ext-part', part, '用到扩展部件（%s）：Word 2007 不认，WPS/LibreOffice 兼容性未验证，已登记' % how)
    for rn in sorted(n for n in set(pkg.names) if n.endswith('.rels')):
        src = rels_source(rn)
        for r in pkg.rels(src):
            if r['mode'] != 'External' and r['type'].startswith(EXT_URI) and r['target']:
                dst = pkg.resolve(src, r['target'])
                if dst and dst in pkg.index:
                    reg(dst, '关系类型 ' + r['type'].rsplit('/', 1)[-1])
    for n in pkg.names:
        if EXT_PART.search(n):
            reg(n, '按部件名识别')
    for name in sorted(set(pkg.names)):
        if not name.endswith('.xml') or not name.startswith(('word/', 'xl/')) or name in seen:
            continue
        root = pkg.xml(name)
        if root is None:
            continue
        decl, alld = pkg.namespaces[name]
        by_uri = {}
        for p, u in decl.items():
            by_uri.setdefault(u, p)
        ign = set()
        for p in (root.get(MC_IGNORABLE) or '').split():
            if p not in decl:
                rep.add(ERROR, 'mc-ignorable', name, 'mc:Ignorable 列了没声明的前缀“%s”，Word 会报“无法读取的内容”' % p)
            else:
                ign.add(decl[p])
        nofb = 0
        for ac in root.iter(q(NS_MC, 'AlternateContent')):
            if ac.find(q(NS_MC, 'Fallback')) is None:
                nofb += 1
            for ch in ac.findall(q(NS_MC, 'Choice')):
                for p in (ch.get('Requires') or '').split():
                    if p in decl:
                        continue
                    if p not in alld:
                        rep.add(ERROR, 'mc-requires', name, 'mc:Choice 的 Requires 用了没声明的前缀“%s”' % p)
        if nofb:
            rep.add(WARN, 'mc-nofallback', name, '%d 处 mc:AlternateContent 没有 Fallback，不认该扩展的软件会丢掉这部分内容' % nofb)
        for u, (tot, inac) in sorted(_ns_usage(root).items()):
            lab = by_uri.get(u) or u.rstrip('/').rsplit('/', 1)[-1]
            if tot == inac:
                rep.add(INFO, 'ext-ns', name, '用到扩展命名空间 %s（%d 处，都在 mc:AlternateContent 内，有降级写法）' % (lab, tot))
            elif u in ign:
                rep.add(INFO, 'ext-ns', name, '用到扩展命名空间 %s（%d 处，已列入 mc:Ignorable，旧版 Word 会忽略）' % (lab, tot))
            else:
                rep.add(WARN, 'ext-not-ignorable', name, '用到扩展命名空间 %s（%d 处）但没列入 mc:Ignorable，Word 2007 可能打不开' % (lab, tot - inac))
    return None


# ---- 批注 / 书签 / 绘图 ID
def _walk(pkg, part):
    '''正文部件的元素按文档顺序成表（缓存），跳过 mc:Fallback 子树：Choice 和 Fallback 里同一对象会各出现一次，不算重复。'''
    cache = pkg.__dict__.setdefault('_walk_cache', {})
    if part not in cache:
        out, root = [], pkg.xml(part)
        if root is not None:
            fb = q(NS_MC, 'Fallback')
            stack = [root]
            while stack:
                e = stack.pop()
                out.append(e)
                stack.extend(reversed([k for k in e if k.tag != fb]))
        cache[part] = out
    return cache[part]


def _isnum(v):
    return v is not None and re.match('^[0-9]+$', v) is not None


def check_comments(pkg, rep):
    '''批注：id 唯一；commentRangeStart/End/Reference 成对且开始在前；引用的批注存在；没有锚点的批注提示。'''
    cpart = 'word/comments.xml'
    if pkg.main:
        for r in pkg.rels(pkg.main):
            if r['type'].endswith('/comments') and r['mode'] != 'External' and r['target']:
                cpart = pkg.resolve(pkg.main, r['target']) or cpart
    cids = {}
    have = cpart in pkg.index
    root = pkg.xml(cpart) if have else None
    rep.comment_counts = [0, 0] if root is not None or not have else None
    for c in (root if root is not None else []):
        if _local(c.tag) != 'comment':
            continue
        rep.comment_counts[0] += 1
        if c.get(W('author')) == TOOL_AUTHOR:
            rep.comment_counts[1] += 1
        cid = c.get(W('id'))
        if not _isnum(cid):
            rep.add(ERROR, 'comment-id-bad', cpart, '批注 id 缺失或不是数字：%s' % cid)
            continue
        cids[cid] = cids.get(cid, 0) + 1
        if c.find(W('p')) is None and c.find(W('tbl')) is None:
            rep.add(ERROR, 'comment-empty', cpart, '批注 %s 里没有任何段落，Word 会报无法读取' % cid)
    for cid, n in sorted(cids.items(), key=lambda x: int(x[0])):
        if n > 1:
            rep.add(ERROR, 'comment-dup-id', cpart, '批注 id 重复：%s（%d 条）' % (cid, n))
    pos, kind = {}, {'commentRangeStart': 's', 'commentRangeEnd': 'e', 'commentReference': 'r'}
    for part in pkg.content_parts():
        for i, el in enumerate(_walk(pkg, part)):
            k = kind.get(_local(el.tag))
            if not k or not el.tag.startswith('{' + NS_W + '}'):
                continue
            cid = el.get(W('id'))
            if not _isnum(cid):
                rep.add(ERROR, 'comment-id-bad', part, '%s 的 id 缺失或不是数字：%s' % (_local(el.tag), cid))
                continue
            pos.setdefault(cid, {'s': [], 'e': [], 'r': []})[k].append((part, i))
    label = {'s': 'commentRangeStart', 'e': 'commentRangeEnd', 'r': 'commentReference'}
    for cid in sorted(pos, key=int):
        p = pos[cid]
        first = (p['s'] or p['e'] or p['r'])[0][0]
        for k in 'ser':
            if len(p[k]) > 1:
                rep.add(ERROR, 'comment-dup-range', p[k][0][0], '批注 %s 的 %s 出现了 %d 次' % (cid, label[k], len(p[k])))
        if p['s'] and not p['e']:
            rep.add(ERROR, 'comment-unpaired', first, '批注 %s 有 commentRangeStart 没有 commentRangeEnd' % cid)
        elif p['e'] and not p['s']:
            rep.add(ERROR, 'comment-unpaired', first, '批注 %s 有 commentRangeEnd 没有 commentRangeStart' % cid)
        elif p['s'] and p['e']:
            if p['s'][0][0] != p['e'][0][0]:
                rep.add(ERROR, 'comment-unpaired', first, '批注 %s 的开始和结束不在同一个部件里' % cid)
            elif p['e'][0][1] < p['s'][0][1]:
                rep.add(ERROR, 'comment-order', first, '批注 %s 的结束标记在开始标记之前' % cid)
        if (p['s'] or p['e']) and not p['r']:
            rep.add(ERROR, 'comment-unpaired', first, '批注 %s 有范围标记但没有 commentReference' % cid)
        elif p['r'] and not (p['s'] or p['e']):
            rep.add(WARN, 'comment-noRange', first, '批注 %s 只有 commentReference，没有范围标记（点状批注）' % cid)
        if cid not in cids:
            rep.add(ERROR, 'comment-dangling', first, '正文引用了批注 %s，但%s里没有这条批注' % (cid, '批注部件' if have else '包（包里没有批注部件）'))
    reply_ids = set()
    extended = 'word/commentsExtended.xml'
    if root is not None and cpart == 'word/comments.xml' and pkg.has(extended) and pkg.xml(extended) is not None:
        from .finalize import EX_ID, EX_PARENT, W15, comments_info
        comments = comments_info(pkg)
        para_ids = {c['para_id'] for c in comments if c['para_id']}
        reply_ids = {c['id'] for c in comments if c['parent'] is not None}
        for el in pkg.xml(extended).findall('{%s}commentEx' % W15):
            parent = el.get(EX_PARENT)
            if parent and parent.upper() not in para_ids:
                rep.add(ERROR, 'comment-parent', extended, '回复 %s 指向不存在的父批注段落 %s' % (el.get(EX_ID), parent))
    for cid in sorted(cids, key=int):
        if cid not in pos and cid not in reply_ids:
            rep.add(WARN, 'comment-orphan', cpart, '批注 %s 在正文里没有任何锚点，Word 里看不到它' % cid)
    return None


def check_bookmarks(pkg, rep):
    '''书签：同一部件内 id 唯一、开始结束成对且开始在前；名称不重复（Word 不分大小写）；顺便收集名称（pkg.bm_names）给 check_links 用。'''
    names = {}
    pkg.bm_names = set()
    for part in pkg.content_parts():
        st, en = {}, {}
        for i, el in enumerate(_walk(pkg, part)):
            t = _local(el.tag)
            if t not in ('bookmarkStart', 'bookmarkEnd') or not el.tag.startswith('{' + NS_W + '}'):
                continue
            bid = el.get(W('id'))
            if not _isnum(bid):
                rep.add(ERROR, 'bookmark-id-bad', part, '%s 的 id 缺失或不是数字：%s' % (t, bid))
                continue
            (st if t == 'bookmarkStart' else en).setdefault(bid, []).append(i)
            if t != 'bookmarkStart':
                continue
            nm = el.get(W('name'))
            if not nm:
                rep.add(ERROR, 'bookmark-name', part, '书签 %s 没有名称' % bid)
                continue
            pkg.bm_names.add(nm)
            names.setdefault(nm.lower(), []).append(nm)
            if len(nm) > 40:
                rep.add(WARN, 'bookmark-name', part, '书签名超过 40 个字符，Word 会截断或忽略：%s…' % nm[:20])
            if re.search(r'\s', nm):
                rep.add(WARN, 'bookmark-name', part, '书签名含空白字符：%s' % nm)
        for bid, v in sorted(st.items(), key=lambda x: int(x[0])):
            if len(v) > 1:
                rep.add(ERROR, 'bookmark-dup-id', part, '书签 id 重复：%s（%d 处）' % (bid, len(v)))
            if bid not in en:
                rep.add(ERROR, 'bookmark-unpaired', part, '书签 %s 有 bookmarkStart 没有 bookmarkEnd' % bid)
            elif min(en[bid]) < min(v):
                rep.add(ERROR, 'bookmark-order', part, '书签 %s 的结束标记在开始标记之前' % bid)
        for bid, v in sorted(en.items(), key=lambda x: int(x[0])):
            if bid not in st:
                rep.add(ERROR, 'bookmark-unpaired', part, '书签 %s 有 bookmarkEnd 没有 bookmarkStart' % bid)
            elif len(v) > 1:
                rep.add(ERROR, 'bookmark-dup-id', part, '书签 %s 的 bookmarkEnd 重复（%d 处）' % (bid, len(v)))
    for lw, v in sorted(names.items()):
        if len(v) > 1:
            rep.add(ERROR, 'bookmark-dup-name', '', '书签名重复：%s（%d 处）' % (v[0], len(v)))
    return None


def check_drawing_ids(pkg, rep):
    '''绘图：wp:inline / wp:anchor 必须有 wp:docPr 且 id 为数字；id 重复只提示（Word 多数情况能打开，但可能被修复）。'''
    seen = {}
    for part in pkg.content_parts():
        for el in _walk(pkg, part):
            if el.tag not in (q(NS_WP, 'inline'), q(NS_WP, 'anchor')):
                continue
            dp = el.find(q(NS_WP, 'docPr'))
            if dp is None:
                rep.add(ERROR, 'drawing-nodocpr', part, 'wp:%s 缺少必需的 wp:docPr' % _local(el.tag))
                continue
            did = dp.get('id')
            if not _isnum(did):
                rep.add(ERROR, 'drawing-id-bad', part, '绘图 docPr 的 id 缺失或不是数字：%s' % did)
                continue
            seen.setdefault(did, []).append(part)
    for did, parts in sorted(seen.items(), key=lambda x: int(x[0])):
        if len(parts) > 1:
            rep.add(WARN, 'drawing-dup-id', parts[0], '绘图 id 重复：%s（%d 处）' % (did, len(parts)))
    return None


# ---- 超链接：anchor 必须指向真实存在的书签
URL_SCHEME = re.compile('^[A-Za-z][A-Za-z0-9+.-]+:')
_SNAP = {}


def _snapshot_info(path):
    '''读快照文件，返回 ('docx', 书签名集合) / ('xlsx', 工作表名集合) / ('other', None) / (None, 错误说明)；按路径+修改时间+大小缓存。'''
    try:
        st = os.stat(path)
        key = (path, st.st_mtime, st.st_size)
    except OSError:
        key = (path, 0, 0)
    if key in _SNAP:
        return _SNAP[key]
    res = ('other', None)
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in ('.docx', '.docm', '.dotx'):
            names = set()
            with zipfile.ZipFile(path) as z:
                for n in z.namelist():
                    if re.match('^word/(document|header[0-9]*|footer[0-9]*|footnotes|endnotes)[.]xml$', n):
                        with z.open(n) as fh:      # 显式关闭，否则 Windows 上快照文件被占用，并报 ResourceWarning
                            for el in parse_xml(fh).iter(W('bookmarkStart')):
                                if el.get(W('name')):
                                    names.add(el.get(W('name')))
            res = ('docx', names)
        elif ext in ('.xlsx', '.xlsm', '.xltx'):
            sheets = set()
            with zipfile.ZipFile(path) as z:
                root = parse_xml(z.read('xl/workbook.xml'))
            for el in root.iter(q(NS_X, 'sheet')):
                if el.get('name'):
                    sheets.add(el.get('name'))
            res = ('xlsx', sheets)
    except Exception as e:
        res = (None, '%s: %s' % (type(e).__name__, e))
    _SNAP[key] = res
    return res


def check_links(pkg, rep):
    '''超链接：无 r:id 的内部链接，anchor 要在本文档有同名书签；指向快照文件的链接，anchor 要在快照里存在（docx 查书签、xlsx 查工作表）。
    快照文件不存在或读不出来时没法核对，登记为 warn（不当作通过，也不误拦终稿：终稿里本来就不该有这类链接）。'''
    base = os.path.dirname(os.path.abspath(pkg.path))
    bms = getattr(pkg, 'bm_names', None)
    if bms is None:
        bms = set()
        for part in pkg.content_parts():
            for el in _walk(pkg, part):
                if el.tag == W('bookmarkStart') and el.get(W('name')):
                    bms.add(el.get(W('name')))
    parts = list(pkg.content_parts())
    cpart = 'word/comments.xml'
    if pkg.main:
        for r in pkg.rels(pkg.main):
            if r['type'].endswith('/comments') and r['mode'] != 'External' and r['target']:
                cpart = pkg.resolve(pkg.main, r['target']) or cpart
    if cpart in pkg.index and cpart not in parts:
        parts.append(cpart)
    for part in parts:
        root = pkg.xml(part)
        if root is None:
            continue
        rels = {}
        for r in pkg.rels(part):
            rels.setdefault(r['id'], r)
        els = list(root.iter()) if part == cpart else _walk(pkg, part)
        for el in els:
            if el.tag != W('hyperlink'):
                continue
            rid = el.get(q(NS_R, 'id'))
            anchor = el.get(W('anchor'))
            if not rid and not anchor:
                rep.add(WARN, 'link-empty', part, '超链接既没有目标也没有 anchor，点了没有反应')
                continue
            if not rid:
                if anchor not in bms:
                    rep.add(ERROR, 'link-anchor', part, '超链接 anchor“%s”在本文档里找不到同名书签' % anchor)
                continue
            rel = rels.get(rid)
            if rel is None or not anchor:
                continue      # 关系缺失由 check_rid_refs 登记；没有 anchor 的外部链接不属于本检查
            tgt = rel['target'] or ''
            if rel['mode'] != 'External' or URL_SCHEME.match(tgt):
                continue
            rp = unquote(tgt.split('#')[0]).replace(chr(92), '/')
            if not rp:
                continue
            snap = rp if os.path.isabs(rp) else os.path.normpath(os.path.join(base, rp))
            fn = os.path.basename(rp)
            if not os.path.isfile(snap):
                rep.add(WARN, 'link-snap-missing', part, '超链接指向的快照文件不存在，没法核对 anchor“%s”：%s' % (anchor, fn))
                continue
            kind, info = _snapshot_info(snap)
            if kind is None:
                rep.add(WARN, 'link-snap-bad', part, '快照文件读不出来，没法核对 anchor“%s”（%s）：%s' % (anchor, info, fn))
            elif kind == 'docx' and anchor not in info:
                rep.add(ERROR, 'link-anchor', part, '超链接 anchor“%s”在快照文件“%s”里找不到书签，点击会跳不到位置' % (anchor, fn))
            elif kind == 'xlsx' and '!' in anchor:
                sh = anchor.split('!')[0].strip(chr(39))
                if sh not in info:
                    rep.add(ERROR, 'link-anchor', part, '超链接 anchor“%s”指向的工作表在快照文件“%s”里不存在' % (anchor, fn))
    return None


# ---- 元素顺序（schema 子集）
PW = '{' + NS_W + '}'


def _rk(*names):
    '''按顺序给子元素编号；用 | 连接的名字同级（可互换位置）。'''
    d = {}
    for i, g in enumerate(names):
        for n in g.split('|'):
            d[n] = i
    return d


RPR_BASE = ('rStyle rFonts b bCs i iCs caps smallCaps strike dstrike outline shadow emboss imprint noProof snapToGrid vanish '
            'webHidden color spacing w kern position sz szCs highlight u effect bdr shd fitText vertAlign rtl cs em lang '
            'eastAsianLayout specVanish oMath').split()
_B4 = ('top', 'left|start', 'bottom', 'right|end')
ORDER_STRICT = {
    'pPr': _rk(*('pStyle keepNext keepLines pageBreakBefore framePr widowControl numPr suppressLineNumbers pBdr shd tabs '
                 'suppressAutoHyphens kinsoku wordWrap overflowPunct topLinePunct autoSpaceDE autoSpaceDN bidi adjustRightInd '
                 'snapToGrid spacing ind contextualSpacing mirrorIndents suppressOverlap jc textDirection textAlignment '
                 'textboxTightWrap outlineLvl divId cnfStyle rPr sectPr pPrChange').split()),
    'tcPr': _rk(*('cnfStyle tcW gridSpan hMerge vMerge tcBorders shd noWrap tcMar textDirection tcFitText vAlign hideMark '
                  'headers cellIns cellDel cellMerge tcPrChange').split()),
    'tblPr': _rk(*('tblStyle tblpPr tblOverlap bidiVisual tblStyleRowBandSize tblStyleColBandSize tblW jc tblCellSpacing '
                   'tblInd tblBorders shd tblLayout tblCellMar tblLook tblCaption tblDescription tblPrChange').split()),
    'trPr': _rk('cnfStyle|divId|gridBefore|gridAfter|wBefore|wAfter|cantSplit|trHeight|tblHeader|tblCellSpacing|jc|hidden',
                'ins', 'del', 'trPrChange'),
    'sectPr': _rk(*('headerReference|footerReference footnotePr endnotePr type pgSz pgMar paperSrc pgBorders lnNumType '
                    'pgNumType cols formProt vAlign noEndnote titlePg textDirection bidi rtlGutter docGrid printerSettings '
                    'sectPrChange').split()),
    'pBdr': _rk('top', 'left|start', 'bottom', 'right|end', 'between', 'bar'),
    'tcBorders': _rk(*(_B4 + ('insideH', 'insideV', 'tl2br', 'tr2bl'))),
    'tblBorders': _rk(*(_B4 + ('insideH', 'insideV'))),
    'tcMar': _rk(*_B4),
    'tblCellMar': _rk(*_B4),
}
RPR_HARD = _rk('ins', 'del', 'moveFrom', 'moveTo', '|'.join(RPR_BASE), 'rPrChange')
RPR_SOFT = _rk(*RPR_BASE)
DUP_TAGS = {'pPr', 'tcPr', 'tblPr', 'sectPr', 'pBdr', 'tcBorders', 'tblBorders', 'tcMar', 'tblCellMar'}
MARKUP = {'bookmarkStart', 'bookmarkEnd', 'commentRangeStart', 'commentRangeEnd', 'permStart', 'permEnd', 'proofErr',
          'moveFromRangeStart', 'moveFromRangeEnd', 'moveToRangeStart', 'moveToRangeEnd'}


def _wkids(el):
    return [_local(c.tag) for c in el if isinstance(c.tag, str) and c.tag.startswith(PW)]


def _check_seq(el, ranks, part, rep, label, sev, code, unknown=True):
    last, lastn = -1, ''
    for ch in el:
        if not isinstance(ch.tag, str) or not ch.tag.startswith(PW):
            continue      # 扩展命名空间的子元素（w14 等）不在 schema 子集内，不核对
        n = _local(ch.tag)
        r = ranks.get(n)
        if r is None:
            if unknown:
                rep.add(WARN, 'order-unknown', part, '%s 里有顺序表之外的元素 w:%s，没核对它的位置' % (label, n))
            continue
        if r < last:
            rep.add(sev, code, part, '%s 里 w:%s 排在 w:%s 之后，顺序不符合 schema（Word 可能报“无法读取的内容”）' % (label, n, lastn))
        else:
            last, lastn = r, n


def check_order(pkg, rep):
    '''子元素顺序与结构位置：pPr/tcPr/tblPr/trPr/sectPr/各类边框按 schema 序列；rPr 的常规顺序只提示，修订标记和 rPrChange 的位置严格查；
    p/r 的 pPr/rPr 须在最前，tbl/tr/tc/body 的结构位置。'''
    parts = list(pkg.content_parts())
    for extra in ('word/comments.xml', 'word/styles.xml', 'word/numbering.xml'):
        if extra in pkg.index and extra not in parts:
            parts.append(extra)
    for part in parts:
        for el in _walk(pkg, part):
            if not isinstance(el.tag, str) or not el.tag.startswith(PW):
                continue
            t = _local(el.tag)
            if t in ORDER_STRICT:
                _check_seq(el, ORDER_STRICT[t], part, rep, 'w:' + t, ERROR, 'order-bad')
                if t in DUP_TAGS:
                    cnt = {}
                    for n in _wkids(el):
                        if n not in ('headerReference', 'footerReference'):
                            cnt[n] = cnt.get(n, 0) + 1
                    for n, k in sorted(cnt.items()):
                        if k > 1:
                            rep.add(ERROR, 'order-dup', part, 'w:%s 里 w:%s 重复出现 %d 次' % (t, n, k))
            elif t == 'rPr':
                _check_seq(el, RPR_HARD, part, rep, 'w:rPr', ERROR, 'order-bad')
                _check_seq(el, RPR_SOFT, part, rep, 'w:rPr', WARN, 'order-rpr', unknown=False)
            elif t in ('p', 'r'):
                tag = 'pPr' if t == 'p' else 'rPr'
                kids = _wkids(el)
                if tag in kids and kids.index(tag) != 0:
                    rep.add(ERROR, 'order-bad', part, 'w:%s 里 w:%s 必须是第一个子元素' % (t, tag))
                if kids.count(tag) > 1:
                    rep.add(ERROR, 'order-dup', part, 'w:%s 里 w:%s 重复出现' % (t, tag))
            elif t == 'tbl':
                kids = _wkids(el)
                for n in ('tblPr', 'tblGrid'):
                    if kids.count(n) != 1:
                        rep.add(ERROR, 'tbl-struct', part, 'w:tbl 里 w:%s 应有且仅有 1 个，实际 %d 个' % (n, kids.count(n)))
                if 'tblPr' in kids and 'tblGrid' in kids and kids.index('tblPr') > kids.index('tblGrid'):
                    rep.add(ERROR, 'order-bad', part, 'w:tbl 里 w:tblPr 排在 w:tblGrid 之后')
                if 'tr' in kids and 'tblGrid' in kids and kids.index('tr') < kids.index('tblGrid'):
                    rep.add(ERROR, 'order-bad', part, 'w:tbl 里有行排在 w:tblGrid 之前')
                if not any(k in kids for k in ('tr', 'sdt', 'customXml')):
                    rep.add(ERROR, 'tbl-struct', part, '表格没有任何行（w:tr），Word 会报无法读取的内容')
            elif t == 'tr':
                kids = _wkids(el)
                ftc = kids.index('tc') if 'tc' in kids else len(kids)
                for n in ('tblPrEx', 'trPr'):
                    if n in kids and kids.index(n) > ftc:
                        rep.add(ERROR, 'order-bad', part, 'w:tr 里 w:%s 排在单元格之后' % n)
                if 'tblPrEx' in kids and 'trPr' in kids and kids.index('tblPrEx') > kids.index('trPr'):
                    rep.add(ERROR, 'order-bad', part, 'w:tr 里 w:tblPrEx 排在 w:trPr 之后')
            elif t == 'tc':
                kids = _wkids(el)
                if 'tcPr' in kids and kids.index('tcPr') != 0:
                    rep.add(ERROR, 'order-bad', part, 'w:tc 里 w:tcPr 必须是第一个子元素')
                body = [k for k in kids if k not in MARKUP and k != 'tcPr']
                if not body or body[-1] == 'tbl' or ('p' not in body and 'sdt' not in body):
                    rep.add(ERROR, 'tc-last-par', part, '单元格没有以段落结尾（w:tc 必须以 w:p 结束，Word 会报无法读取的内容）')
            elif t == 'body':
                kids = _wkids(el)
                if kids.count('sectPr') > 1 or ('sectPr' in kids and kids[-1] != 'sectPr'):
                    rep.add(ERROR, 'order-bad', part, 'w:body 里的 w:sectPr 必须有且只有一个，并且排在最后')
    return None


# ---- 输出
TITLES.update({
    'file-missing': '文件不存在', 'zip-bad': '不是有效的包', 'zip-empty': '包是空的', 'zip-read': '部件读不出来',
    'zip-duplicate': '包内条目重复', 'zip-order': '条目顺序不对', 'zip-name': '条目名不合规', 'zip-crc': '压缩数据损坏',
    'xml-bad': 'XML 不良构', 'ct-missing': '缺内容类型清单', 'ct-root': '内容类型清单根元素错', 'ct-bad': '内容类型项不完整',
    'ct-duplicate': '内容类型重复', 'ct-dangling': '内容类型指向不存在的部件', 'ct-part': '部件没有内容类型',
    'ct-wrong': '部件内容类型不对', 'ct-main': '主文档内容类型不对', 'rels-missing': '缺包根关系文件',
    'rels-root': '关系文件根元素错', 'rels-orphan': '关系文件无对应部件', 'rels-bad': '关系项不完整',
    'rels-dup-id': '关系 Id 重复', 'rels-target': '关系目标不存在', 'rels-case': '关系目标大小写不符',
    'rid-missing': '引用了不存在的关系', 'rid-type': '引用的关系类型不对', 'ext-part': '用到扩展部件',
    'ext-ns': '用到扩展命名空间', 'ext-not-ignorable': '扩展命名空间未列入忽略', 'mc-ignorable': 'mc:Ignorable 前缀未声明',
    'mc-requires': 'mc:Choice 前缀未声明', 'mc-nofallback': '扩展内容无降级写法', 'comment-id-bad': '批注 id 不合法',
    'comment-empty': '批注内容为空', 'comment-dup-id': '批注 id 重复', 'comment-dup-range': '批注标记重复',
    'comment-parent': '未找到父批注',
    'comment-unpaired': '批注标记不成对', 'comment-order': '批注标记顺序反', 'comment-noRange': '批注无范围标记',
    'comment-dangling': '正文引用了不存在的批注', 'comment-orphan': '批注没有锚点', 'bookmark-id-bad': '书签 id 不合法',
    'bookmark-name': '书签名有问题', 'bookmark-dup-id': '书签 id 重复', 'bookmark-dup-name': '书签名重复',
    'bookmark-unpaired': '书签不成对', 'bookmark-order': '书签标记顺序反', 'drawing-nodocpr': '绘图缺 docPr',
    'drawing-id-bad': '绘图 id 不合法', 'drawing-dup-id': '绘图 id 重复', 'link-empty': '空超链接',
    'link-anchor': '超链接锚点找不到', 'link-snap-missing': '快照文件不存在', 'link-snap-bad': '快照文件读不出来',
    'order-bad': '元素顺序不对', 'order-dup': '元素重复', 'order-rpr': '字符格式顺序不规范', 'order-unknown': '未核对的元素',
    'tbl-struct': '表格结构不完整', 'tc-last-par': '单元格没有以段落结尾', 'kind-unknown': '无法判断文件类型',
    'selfcheck-incomplete': '自检没跑完',
})


def _short(s, n=70):
    s = ' '.join((s or '').split())
    return s if len(s) <= n else s[:n - 1] + '…'


def _line(x):
    return ('%s：%s' % (x['part'], x['msg'])) if x['part'] else x['msg']


def render(rep, per_group=2):
    '''简短中文汇总（返回行列表）：每类显示数量和前几条明细，严重的在前；全部通过只有一行。'''
    name = os.path.basename(rep.path)
    ne, nw, ni = rep.count(ERROR), rep.count(WARN), rep.count(INFO)
    if not (ne or nw or ni):
        return ['结构自检通过：%s' % name]
    lines = ['结构自检%s：%s（严重 %d，注意 %d，登记 %d）' % ('未通过' if ne else '有提示', name, ne, nw, ni)]
    for sev, code, items, over in rep.groups():
        n = len(items) + over
        eg = '；'.join(_short(_line(x)) for x in items[:per_group])
        lines.append('  [%s] %s（%d 项）：%s%s' % (SEV_NAME[sev], TITLES.get(code, code), n, eg, '…' if n > per_group else ''))
    return lines


def final_blockers(rep):
    '''终稿拦截用：只取 error，格式“结构问题-类别：明细”（finalize 按第一个“：”前的类别汇总）。warn 和 info 不拦截。'''
    out = []
    for sev, code, items, over in rep.groups():
        if sev != ERROR:
            continue
        t = TITLES.get(code, code)
        for x in items:
            out.append('结构问题-%s：%s' % (t, _line(x)))
        if over:
            out.append('结构问题-%s：…另有 %d 项同类问题未逐条列出' % (t, over))
    return out


def check_docx(pkg, rep):
    for fn in (check_comments, check_bookmarks, check_drawing_ids, check_links, check_order):
        _run(fn, pkg, rep)


def check_xlsx(pkg, rep):
    for fn in list(XLSX_CHECKS):
        _run(fn, pkg, rep)


def check_file(path, kind=None):
    '''返回 Report。文件不存在、不是 zip 也只登记为 error，不抛异常。'''
    rep = Report(path)
    if not os.path.isfile(path):
        rep.add(ERROR, 'file-missing', '', '文件不存在：%s' % os.path.basename(path))
        return rep
    pkg = Pkg(path, rep)
    try:
        if not _run(check_zip, pkg, rep):
            return rep
        _run(check_wellformed, pkg, rep)
        _run(check_content_types, pkg, rep)
        _run(check_rels, pkg, rep)
        pkg.detect_kind(kind or KIND_BY_EXT.get(os.path.splitext(path)[1].lower()))
        _run(check_rid_refs, pkg, rep)
        _run(check_ext, pkg, rep)
        if pkg.kind == 'docx':
            check_docx(pkg, rep)
        elif pkg.kind == 'xlsx':
            check_xlsx(pkg, rep)
        else:
            rep.add(WARN, 'kind-unknown', '', '无法判断是 docx 还是 xlsx，只做了通用检查')
    finally:
        pkg.close()
    return rep
