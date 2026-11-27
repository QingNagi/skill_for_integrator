# -*- coding: utf-8 -*-
'''selfcheck 回归测试：全部用合成文件（不含任何真实公司或人员信息），每条检查至少有一个故意损坏的样本必须被报出。
运行：python tests/test_selfcheck.py（只用标准库 unittest，Python 3.8 及以上）。'''
import os
import shutil
import sys
import tempfile
import unittest
import warnings
import zipfile

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'traced-template-fill', 'scripts'))
from dilib import selfcheck as sc  # noqa: E402

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
CTW = 'application/vnd.openxmlformats-officedocument.wordprocessingml.'
CT = (HEAD + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
      '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
      '<Default Extension="xml" ContentType="application/xml"/>'
      '<Override PartName="/word/document.xml" ContentType="' + CTW + 'document.main+xml"/>'
      '<Override PartName="/word/comments.xml" ContentType="' + CTW + 'comments+xml"/></Types>')
PR = 'http://schemas.openxmlformats.org/package/2006/relationships'
ROOT_RELS = (HEAD + '<Relationships xmlns="' + PR + '"><Relationship Id="rId1" Type="' + R +
             '/officeDocument" Target="word/document.xml"/></Relationships>')
DOC_RELS = (HEAD + '<Relationships xmlns="' + PR + '"><Relationship Id="rId1" Type="' + R +
            '/comments" Target="comments.xml"/></Relationships>')
COMMENTS = (HEAD + '<w:comments xmlns:w="' + W + '"><w:comment w:id="0" w:author="t" w:initials="t">'
            '<w:p><w:r><w:t>note</w:t></w:r></w:p></w:comment></w:comments>')
BODY = ('<w:p><w:pPr><w:jc w:val="left"/></w:pPr><w:bookmarkStart w:id="1" w:name="bm_a"/>'
        '<w:commentRangeStart w:id="0"/><w:r><w:t>hello</w:t></w:r><w:commentRangeEnd w:id="0"/>'
        '<w:r><w:commentReference w:id="0"/></w:r><w:bookmarkEnd w:id="1"/></w:p>'
        '<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/></w:tblPr><w:tblGrid><w:gridCol w:w="1000"/></w:tblGrid>'
        '<w:tr><w:tc><w:tcPr><w:tcW w:w="1000" w:type="dxa"/></w:tcPr><w:p/></w:tc></w:tr></w:tbl>')


def doc_xml(body=BODY, ns=''):
    return (HEAD + '<w:document xmlns:w="' + W + '" xmlns:r="' + R + '"' + ns + '><w:body>' + body +
            '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/></w:sectPr></w:body></w:document>')


def base_parts():
    return {'[Content_Types].xml': CT, '_rels/.rels': ROOT_RELS, 'word/_rels/document.xml.rels': DOC_RELS,
            'word/document.xml': doc_xml(), 'word/comments.xml': COMMENTS}


def make_docx(path, mods=None, drop=(), order=None, dups=(), stored=False):
    '''mods：{部件名: 新内容}；drop：去掉的部件；order：条目顺序；dups：再写一遍的同名条目；stored：不压缩（便于改字节）。'''
    parts = base_parts()
    parts.update(mods or {})
    for k in drop:
        parts.pop(k, None)
    names = list(order) if order else list(parts)
    comp = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        with zipfile.ZipFile(path, 'w', comp) as z:
            for n in names:
                z.writestr(n, parts[n])
            for n in dups:
                z.writestr(n, parts[n])


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix='di_sc_')

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def check(self, name='t.docx', **kw):
        '''造文件并自检；自检自己出错（selfcheck-incomplete）本身就是测试失败。'''
        p = os.path.join(self.d, name)
        make_docx(p, **kw)
        rep = sc.check_file(p)
        self.assertNotIn('selfcheck-incomplete', rep.codes(), '自检自身出错：%s' % [x['msg'] for x in rep.items])
        return rep

    def assertHas(self, rep, code, sev=None):
        got = [(x['sev'], x['code']) for x in rep.items]
        self.assertTrue(any(c == code and (sev is None or s == sev) for s, c in got), '应报出 %s（%s），实际：%s' % (code, sev, got))

    def assertNot(self, rep, code):
        self.assertNotIn(code, rep.codes(), '不该报 %s：%s' % (code, [x['msg'] for x in rep.items if x['code'] == code]))


class BaselineTests(T):
    def test_clean_docx_passes(self):
        rep = self.check()
        self.assertEqual(rep.items, [], [x['msg'] for x in rep.items])
        self.assertEqual(sc.render(rep)[0].split('：')[0], '结构自检通过')
        self.assertEqual(sc.final_blockers(rep), [])

    def test_missing_file(self):
        rep = sc.check_file(os.path.join(self.d, 'nope.docx'))
        self.assertHas(rep, 'file-missing', sc.ERROR)


class ZipTests(T):
    def test_not_a_zip(self):
        p = os.path.join(self.d, 'bad.docx')
        with open(p, 'wb') as f:
            f.write(b'this is not a zip')
        self.assertHas(sc.check_file(p), 'zip-bad', sc.ERROR)

    def test_empty_zip(self):
        p = os.path.join(self.d, 'empty.docx')
        zipfile.ZipFile(p, 'w').close()
        self.assertHas(sc.check_file(p), 'zip-empty', sc.ERROR)

    def test_duplicate_entry(self):
        self.assertHas(self.check(dups=('word/comments.xml',)), 'zip-duplicate', sc.ERROR)

    def test_content_types_not_first(self):
        names = [n for n in base_parts() if n != '[Content_Types].xml'] + ['[Content_Types].xml']
        self.assertHas(self.check(order=names), 'zip-order', sc.ERROR)

    def test_content_types_missing(self):
        rep = self.check(drop=('[Content_Types].xml',))
        self.assertHas(rep, 'ct-missing', sc.ERROR)

    def test_root_rels_missing(self):
        self.assertHas(self.check(drop=('_rels/.rels',)), 'rels-missing', sc.ERROR)

    def test_crc_damage(self):
        p = os.path.join(self.d, 'crc.docx')
        make_docx(p, mods={'word/comments.xml': COMMENTS.replace('note', 'CRCMARK')}, stored=True)
        with open(p, 'rb') as f:
            data = f.read()
        self.assertIn(b'CRCMARK', data)
        with open(p, 'wb') as f:
            f.write(data.replace(b'CRCMARK', b'CRCMARX'))
        self.assertHas(sc.check_file(p), 'zip-crc', sc.ERROR)


def doc_rels(extra=''):
    return DOC_RELS.replace('</Relationships>', extra + '</Relationships>')


def rel(rid, typ, target, external=False):
    t = typ if typ.startswith('http') else R + '/' + typ
    return '<Relationship Id="%s" Type="%s" Target="%s"%s/>' % (rid, t, target, ' TargetMode="External"' if external else '')


def ct_with(extra):
    return CT.replace('</Types>', extra + '</Types>')


class XmlTests(T):
    def test_comments_part_truncated(self):
        self.assertHas(self.check(mods={'word/comments.xml': COMMENTS[:-15]}), 'xml-bad', sc.ERROR)

    def test_document_part_truncated(self):
        self.assertHas(self.check(mods={'word/document.xml': doc_xml()[:-20]}), 'xml-bad', sc.ERROR)

    def test_rels_file_not_wellformed(self):
        self.assertHas(self.check(mods={'word/_rels/document.xml.rels': '<Relationships'}), 'xml-bad', sc.ERROR)


class ContentTypeTests(T):
    def test_part_without_content_type(self):
        self.assertHas(self.check(mods={'word/extra.bin': 'x'}), 'ct-part', sc.ERROR)

    def test_wrong_type_for_comments(self):
        bad = CT.replace(CTW + 'comments+xml', 'application/xml')
        self.assertHas(self.check(mods={'[Content_Types].xml': bad}), 'ct-wrong', sc.ERROR)

    def test_main_document_type_wrong(self):
        bad = CT.replace(CTW + 'document.main+xml', 'application/xml')
        self.assertHas(self.check(mods={'[Content_Types].xml': bad}), 'ct-main', sc.ERROR)

    def test_duplicate_override(self):
        extra = '<Override PartName="/word/comments.xml" ContentType="%scomments+xml"/>' % CTW
        self.assertHas(self.check(mods={'[Content_Types].xml': ct_with(extra)}), 'ct-duplicate', sc.ERROR)

    def test_duplicate_default(self):
        extra = '<Default Extension="xml" ContentType="text/xml"/>'
        self.assertHas(self.check(mods={'[Content_Types].xml': ct_with(extra)}), 'ct-duplicate', sc.ERROR)

    def test_override_partname_without_slash(self):
        extra = '<Override PartName="word/x.xml" ContentType="%sstyles+xml"/>' % CTW
        self.assertHas(self.check(mods={'[Content_Types].xml': ct_with(extra)}), 'ct-bad', sc.ERROR)

    def test_wrong_root_namespace(self):
        bad = CT.replace('http://schemas.openxmlformats.org/package/2006/content-types', 'urn:wrong')
        self.assertHas(self.check(mods={'[Content_Types].xml': bad}), 'ct-root', sc.ERROR)

    def test_dangling_override_is_warning_only(self):
        extra = '<Override PartName="/word/ghost.xml" ContentType="%sstyles+xml"/>' % CTW
        rep = self.check(mods={'[Content_Types].xml': ct_with(extra)})
        self.assertHas(rep, 'ct-dangling', sc.WARN)
        self.assertEqual(sc.final_blockers(rep), [], '警告不应拦截终稿')


class RelsTests(T):
    def test_target_missing(self):
        rep = self.check(mods={'word/_rels/document.xml.rels': doc_rels(rel('rId2', 'styles', 'styles.xml'))})
        self.assertHas(rep, 'rels-target', sc.ERROR)

    def test_target_escapes_package(self):
        rep = self.check(mods={'word/_rels/document.xml.rels': doc_rels(rel('rId2', 'styles', '../../outside.xml'))})
        self.assertHas(rep, 'rels-target', sc.ERROR)

    def test_target_empty(self):
        rep = self.check(mods={'word/_rels/document.xml.rels': doc_rels(rel('rId2', 'styles', ''))})
        self.assertHas(rep, 'rels-target', sc.ERROR)

    def test_duplicate_id(self):
        rep = self.check(mods={'word/_rels/document.xml.rels': doc_rels(rel('rId1', 'comments', 'comments.xml'))})
        self.assertHas(rep, 'rels-dup-id', sc.ERROR)

    def test_missing_type_attribute(self):
        rep = self.check(mods={'word/_rels/document.xml.rels': doc_rels('<Relationship Id="rId2" Target="comments.xml"/>')})
        self.assertHas(rep, 'rels-bad', sc.ERROR)

    def test_wrong_root_namespace(self):
        rep = self.check(mods={'word/_rels/document.xml.rels': DOC_RELS.replace(PR, 'urn:wrong')})
        self.assertHas(rep, 'rels-root', sc.ERROR)

    def test_orphan_rels_file_is_warning(self):
        rep = self.check(mods={'word/_rels/ghost.xml.rels': DOC_RELS})
        self.assertHas(rep, 'rels-orphan', sc.WARN)

    def test_external_target_is_not_resolved(self):
        rep = self.check(mods={'word/_rels/document.xml.rels': doc_rels(rel('rId2', 'hyperlink', 'http://example.com/', True))})
        self.assertNot(rep, 'rels-target')


class RidRefTests(T):
    LINK = '<w:p><w:hyperlink r:id="%s"><w:r><w:t>x</w:t></w:r></w:hyperlink></w:p>'

    def test_missing_relationship(self):
        rep = self.check(mods={'word/document.xml': doc_xml(BODY + self.LINK % 'rId99')})
        self.assertHas(rep, 'rid-missing', sc.ERROR)

    def test_hyperlink_points_to_wrong_relationship_type(self):
        rep = self.check(mods={'word/document.xml': doc_xml(BODY + self.LINK % 'rId1')})
        self.assertHas(rep, 'rid-type', sc.ERROR)

    def test_valid_external_hyperlink_passes(self):
        rep = self.check(mods={'word/_rels/document.xml.rels': doc_rels(rel('rId2', 'hyperlink', 'http://example.com/', True)),
                               'word/document.xml': doc_xml(BODY + self.LINK % 'rId2')})
        self.assertEqual(rep.items, [], [x['msg'] for x in rep.items])


NS_MC = ' xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
NS_W14 = ' xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml"'
NS_WP = ' xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"'
CM_OVERRIDE = '<Override PartName="/word/comments.xml" ContentType="' + CTW + 'comments+xml"/>'


def doc_mod(body, ns=''):
    return {'word/document.xml': doc_xml(body, ns)}


class ExtTests(T):
    def test_extension_part_registered_as_warning(self):
        extra = '<Override PartName="/word/commentsExtended.xml" ContentType="' + CTW + 'commentsExtended+xml"/>'
        part = HEAD + '<w15:commentsEx xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml"/>'
        rep = self.check(mods={'[Content_Types].xml': ct_with(extra), 'word/commentsExtended.xml': part})
        self.assertHas(rep, 'ext-part', sc.WARN)
        self.assertEqual(sc.final_blockers(rep), [], '扩展部件只登记，不拦截终稿')

    def test_extension_relationship_type_registered(self):
        typ = 'http://schemas.microsoft.com/office/2007/relationships/stylesWithEffects'
        mods = {'word/stylesWithEffects.xml': HEAD + '<w:styles xmlns:w="' + W + '"/>',
                'word/_rels/document.xml.rels': doc_rels(rel('rId2', typ, 'stylesWithEffects.xml'))}
        self.assertHas(self.check(mods=mods), 'ext-part', sc.WARN)

    def test_namespace_not_ignorable_warns(self):
        rep = self.check(mods=doc_mod(BODY + '<w:p w14:paraId="0000000A"/>', NS_W14))
        self.assertHas(rep, 'ext-not-ignorable', sc.WARN)
        self.assertEqual(sc.final_blockers(rep), [])

    def test_namespace_listed_in_ignorable_is_info_only(self):
        ns = NS_MC + NS_W14 + ' mc:Ignorable="w14"'
        rep = self.check(mods=doc_mod(BODY + '<w:p w14:paraId="0000000A"/>', ns))
        self.assertHas(rep, 'ext-ns', sc.INFO)
        self.assertNot(rep, 'ext-not-ignorable')
        self.assertEqual(rep.count(sc.WARN), 0)

    def test_ignorable_prefix_undeclared(self):
        rep = self.check(mods=doc_mod(BODY, NS_MC + ' mc:Ignorable="w14"'))
        self.assertHas(rep, 'mc-ignorable', sc.ERROR)

    def test_choice_requires_undeclared_prefix(self):
        body = BODY + '<w:p><mc:AlternateContent><mc:Choice Requires="wps"><w:r><w:t>a</w:t></w:r></mc:Choice><mc:Fallback><w:r><w:t>b</w:t></w:r></mc:Fallback></mc:AlternateContent></w:p>'
        self.assertHas(self.check(mods=doc_mod(body, NS_MC)), 'mc-requires', sc.ERROR)

    def test_alternate_content_without_fallback(self):
        body = BODY + '<w:p><mc:AlternateContent><mc:Choice Requires="w14"><w:r><w:t>a</w:t></w:r></mc:Choice></mc:AlternateContent></w:p>'
        self.assertHas(self.check(mods=doc_mod(body, NS_MC + NS_W14)), 'mc-nofallback', sc.WARN)

    def test_namespace_inside_alternate_content_is_info(self):
        body = BODY + '<w:p><mc:AlternateContent><mc:Choice Requires="w14"><w:r w14:x="1"><w:t>a</w:t></w:r></mc:Choice><mc:Fallback><w:r><w:t>b</w:t></w:r></mc:Fallback></mc:AlternateContent></w:p>'
        rep = self.check(mods=doc_mod(body, NS_MC + NS_W14))
        self.assertHas(rep, 'ext-ns', sc.INFO)
        self.assertNot(rep, 'ext-not-ignorable')


class CommentTests(T):
    def test_range_end_missing(self):
        rep = self.check(mods=doc_mod(BODY.replace('<w:commentRangeEnd w:id="0"/>', '')))
        self.assertHas(rep, 'comment-unpaired', sc.ERROR)

    def test_range_start_missing(self):
        rep = self.check(mods=doc_mod(BODY.replace('<w:commentRangeStart w:id="0"/>', '')))
        self.assertHas(rep, 'comment-unpaired', sc.ERROR)

    def test_reference_missing(self):
        rep = self.check(mods=doc_mod(BODY.replace('<w:r><w:commentReference w:id="0"/></w:r>', '')))
        self.assertHas(rep, 'comment-unpaired', sc.ERROR)

    def test_end_before_start(self):
        body = '<w:p><w:commentRangeEnd w:id="0"/><w:commentRangeStart w:id="0"/><w:r><w:t>a</w:t></w:r><w:r><w:commentReference w:id="0"/></w:r></w:p>'
        self.assertHas(self.check(mods=doc_mod(body)), 'comment-order', sc.ERROR)

    def test_range_marker_repeated(self):
        body = BODY.replace('<w:commentRangeStart w:id="0"/>', '<w:commentRangeStart w:id="0"/><w:commentRangeStart w:id="0"/>')
        self.assertHas(self.check(mods=doc_mod(body)), 'comment-dup-range', sc.ERROR)

    def test_body_references_nonexistent_comment(self):
        rep = self.check(mods={'word/comments.xml': COMMENTS.replace('w:id="0"', 'w:id="5"')})
        self.assertHas(rep, 'comment-dangling', sc.ERROR)
        self.assertHas(rep, 'comment-orphan', sc.WARN)

    def test_no_comments_part_but_body_references(self):
        mods = {'word/_rels/document.xml.rels': HEAD + '<Relationships xmlns="' + PR + '"/>',
                '[Content_Types].xml': CT.replace(CM_OVERRIDE, '')}
        rep = self.check(mods=mods, drop=('word/comments.xml',))
        self.assertHas(rep, 'comment-dangling', sc.ERROR)

    def test_duplicate_comment_id(self):
        dup = COMMENTS.replace('</w:comments>', '<w:comment w:id="0" w:author="t"><w:p/></w:comment></w:comments>')
        self.assertHas(self.check(mods={'word/comments.xml': dup}), 'comment-dup-id', sc.ERROR)

    def test_comment_id_not_numeric(self):
        rep = self.check(mods={'word/comments.xml': COMMENTS.replace('w:id="0"', 'w:id="abc"')})
        self.assertHas(rep, 'comment-id-bad', sc.ERROR)

    def test_range_id_not_numeric(self):
        rep = self.check(mods=doc_mod(BODY.replace('<w:commentRangeStart w:id="0"/>', '<w:commentRangeStart w:id="x"/>')))
        self.assertHas(rep, 'comment-id-bad', sc.ERROR)

    def test_comment_without_paragraph(self):
        empty = HEAD + '<w:comments xmlns:w="' + W + '"><w:comment w:id="0" w:author="t"/></w:comments>'
        self.assertHas(self.check(mods={'word/comments.xml': empty}), 'comment-empty', sc.ERROR)

    def test_comment_without_anchor_is_warning(self):
        rep = self.check(mods=doc_mod('<w:p><w:r><w:t>x</w:t></w:r></w:p>'))
        self.assertHas(rep, 'comment-orphan', sc.WARN)
        self.assertEqual(sc.final_blockers(rep), [])

    def test_reference_only_is_warning(self):
        rep = self.check(mods=doc_mod('<w:p><w:r><w:commentReference w:id="0"/></w:r></w:p>'))
        self.assertHas(rep, 'comment-noRange', sc.WARN)
        self.assertEqual(sc.final_blockers(rep), [])


class BookmarkTests(T):
    def bm(self, *items):
        return ''.join('<w:p>%s</w:p>' % x for x in items)

    def test_duplicate_id(self):
        body = self.bm('<w:bookmarkStart w:id="1" w:name="a"/><w:bookmarkEnd w:id="1"/>', '<w:bookmarkStart w:id="1" w:name="b"/><w:bookmarkEnd w:id="1"/>')
        self.assertHas(self.check(mods=doc_mod(body)), 'bookmark-dup-id', sc.ERROR)

    def test_duplicate_name_ignoring_case(self):
        body = self.bm('<w:bookmarkStart w:id="1" w:name="Bm"/><w:bookmarkEnd w:id="1"/>', '<w:bookmarkStart w:id="2" w:name="bm"/><w:bookmarkEnd w:id="2"/>')
        self.assertHas(self.check(mods=doc_mod(body)), 'bookmark-dup-name', sc.ERROR)

    def test_start_without_end(self):
        self.assertHas(self.check(mods=doc_mod(self.bm('<w:bookmarkStart w:id="1" w:name="a"/>'))), 'bookmark-unpaired', sc.ERROR)

    def test_end_without_start(self):
        self.assertHas(self.check(mods=doc_mod(self.bm('<w:bookmarkEnd w:id="7"/>'))), 'bookmark-unpaired', sc.ERROR)

    def test_end_before_start(self):
        body = self.bm('<w:bookmarkEnd w:id="3"/><w:bookmarkStart w:id="3" w:name="o"/>')
        self.assertHas(self.check(mods=doc_mod(body)), 'bookmark-order', sc.ERROR)

    def test_name_missing(self):
        body = self.bm('<w:bookmarkStart w:id="1"/><w:bookmarkEnd w:id="1"/>')
        self.assertHas(self.check(mods=doc_mod(body)), 'bookmark-name', sc.ERROR)

    def test_name_too_long_and_with_space_warn(self):
        body = self.bm('<w:bookmarkStart w:id="1" w:name="%s"/><w:bookmarkEnd w:id="1"/>' % ('x' * 41),
                       '<w:bookmarkStart w:id="2" w:name="a b"/><w:bookmarkEnd w:id="2"/>')
        rep = self.check(mods=doc_mod(body))
        self.assertHas(rep, 'bookmark-name', sc.WARN)
        self.assertEqual(rep.count(sc.ERROR), 0)

    def test_id_not_numeric(self):
        body = self.bm('<w:bookmarkStart w:id="abc" w:name="a"/><w:bookmarkEnd w:id="abc"/>')
        self.assertHas(self.check(mods=doc_mod(body)), 'bookmark-id-bad', sc.ERROR)


class DrawingTests(T):
    INLINE = '<w:p><w:r><w:drawing><wp:inline><wp:extent cx="1" cy="1"/>%s</wp:inline></w:drawing></w:r></w:p>'

    def test_docpr_missing(self):
        self.assertHas(self.check(mods=doc_mod(self.INLINE % '', NS_WP)), 'drawing-nodocpr', sc.ERROR)

    def test_docpr_id_not_numeric(self):
        body = self.INLINE % '<wp:docPr id="x" name="p"/>'
        self.assertHas(self.check(mods=doc_mod(body, NS_WP)), 'drawing-id-bad', sc.ERROR)

    def test_duplicate_id_is_warning_only(self):
        one = self.INLINE % '<wp:docPr id="1" name="p"/>'
        rep = self.check(mods=doc_mod(one + one, NS_WP))
        self.assertHas(rep, 'drawing-dup-id', sc.WARN)
        self.assertEqual(sc.final_blockers(rep), [])

    def test_choice_and_fallback_copies_are_not_duplicates(self):
        d = '<w:r><w:drawing><wp:inline><wp:docPr id="1" name="a"/></wp:inline></w:drawing></w:r>'
        body = '<w:p><mc:AlternateContent><mc:Choice Requires="wp">' + d + '</mc:Choice><mc:Fallback>' + d + '</mc:Fallback></mc:AlternateContent></w:p>'
        rep = self.check(mods=doc_mod(body, NS_MC + NS_WP))
        self.assertNot(rep, 'drawing-dup-id')


def comments_with_link(rid, anchor):
    attr = (' w:anchor="%s"' % anchor) if anchor is not None else ''
    link = '<w:hyperlink%s%s><w:r><w:t>go</w:t></w:r></w:hyperlink>' % ((' r:id="%s"' % rid) if rid else '', attr)
    return (HEAD + '<w:comments xmlns:w="' + W + '" xmlns:r="' + R + '"><w:comment w:id="0" w:author="t" w:initials="t">'
            '<w:p><w:r><w:t>note</w:t></w:r></w:p><w:p>' + link + '</w:p></w:comment></w:comments>')


def comment_rels(target, external=True):
    return HEAD + '<Relationships xmlns="' + PR + '">' + rel('rId1', 'hyperlink', target, external) + '</Relationships>'


def make_xlsx_snapshot(path, sheets=('Sheet1',)):
    wb = (HEAD + '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheets>' +
          ''.join('<sheet name="%s" sheetId="%d"/>' % (s, i + 1) for i, s in enumerate(sheets)) + '</sheets></workbook>')
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('xl/workbook.xml', wb)


class LinkTests(T):
    def comment_link_doc(self, anchor, target='源文件快照/src.docx', snap=None):
        '''批注里带一条指向快照文件的链接；snap 为 None 表示不造快照，否则是造快照的函数 fn(目录)。'''
        sd = os.path.join(self.d, '源文件快照')
        os.makedirs(sd, exist_ok=True)
        if snap:
            snap(sd)
        mods = {'word/comments.xml': comments_with_link('rId1', anchor), 'word/_rels/comments.xml.rels': comment_rels(target)}
        return self.check('draft.docx', mods=mods)

    def docx_snap(self, name='src.docx'):
        return lambda sd: make_docx(os.path.join(sd, name))

    def test_anchor_exists_in_snapshot_passes(self):
        rep = self.comment_link_doc('bm_a', snap=self.docx_snap())
        self.assertEqual(rep.items, [], [x['msg'] for x in rep.items])

    def test_anchor_missing_in_snapshot(self):
        rep = self.comment_link_doc('no_such_bm', snap=self.docx_snap())
        self.assertHas(rep, 'link-anchor', sc.ERROR)
        self.assertTrue(any(b.startswith('结构问题-') for b in sc.final_blockers(rep)))

    def test_snapshot_file_missing_is_warning(self):
        rep = self.comment_link_doc('bm_a')
        self.assertHas(rep, 'link-snap-missing', sc.WARN)
        self.assertEqual(sc.final_blockers(rep), [], '终稿里本来就不该有这类链接，不应因此误拦')

    def test_snapshot_file_corrupt_is_warning(self):
        def bad(sd):
            with open(os.path.join(sd, 'src.docx'), 'wb') as f:
                f.write(b'not a zip')
        rep = self.comment_link_doc('bm_a', snap=bad)
        self.assertHas(rep, 'link-snap-bad', sc.WARN)

    def test_windows_backslash_and_percent_encoded_target(self):
        rep = self.comment_link_doc('bm_a', target='源文件快照\\my%20src.docx', snap=self.docx_snap('my src.docx'))
        self.assertEqual(rep.items, [], [x['msg'] for x in rep.items])

    def test_xlsx_snapshot_sheet_exists(self):
        rep = self.comment_link_doc('Sheet1!A1:B2', target='源文件快照/data.xlsx', snap=lambda sd: make_xlsx_snapshot(os.path.join(sd, 'data.xlsx')))
        self.assertEqual(rep.items, [], [x['msg'] for x in rep.items])

    def test_xlsx_snapshot_sheet_missing(self):
        rep = self.comment_link_doc('Nope!A1', target='源文件快照/data.xlsx', snap=lambda sd: make_xlsx_snapshot(os.path.join(sd, 'data.xlsx')))
        self.assertHas(rep, 'link-anchor', sc.ERROR)

    def test_web_link_with_anchor_is_not_checked(self):
        mods = {'word/comments.xml': comments_with_link('rId1', 'x'), 'word/_rels/comments.xml.rels': comment_rels('http://example.com/a.docx')}
        rep = self.check('draft.docx', mods=mods)
        self.assertNot(rep, 'link-anchor')
        self.assertNot(rep, 'link-snap-missing')

    def test_internal_link_to_existing_bookmark(self):
        rep = self.check(mods=doc_mod(BODY + '<w:p><w:hyperlink w:anchor="bm_a"><w:r><w:t>x</w:t></w:r></w:hyperlink></w:p>'))
        self.assertEqual(rep.items, [], [x['msg'] for x in rep.items])

    def test_internal_link_to_missing_bookmark(self):
        rep = self.check(mods=doc_mod(BODY + '<w:p><w:hyperlink w:anchor="nope"><w:r><w:t>x</w:t></w:r></w:hyperlink></w:p>'))
        self.assertHas(rep, 'link-anchor', sc.ERROR)

    def test_hyperlink_without_target_or_anchor(self):
        rep = self.check(mods=doc_mod(BODY + '<w:p><w:hyperlink><w:r><w:t>x</w:t></w:r></w:hyperlink></w:p>'))
        self.assertHas(rep, 'link-empty', sc.WARN)


def raw_doc(inner):
    return HEAD + '<w:document xmlns:w="' + W + '" xmlns:r="' + R + '"><w:body>' + inner + '</w:body></w:document>'


GRID = '<w:tblGrid><w:gridCol w:w="1000"/></w:tblGrid>'
TBLW = '<w:tblW w:w="0" w:type="auto"/>'


def tbl(tblpr=TBLW, grid=GRID, rows=None, tcpr='<w:tcW w:w="1000" w:type="dxa"/>'):
    if rows is None:
        rows = '<w:tr><w:tc><w:tcPr>%s</w:tcPr><w:p/></w:tc></w:tr>' % tcpr
    return '<w:tbl><w:tblPr>%s</w:tblPr>%s%s</w:tbl>' % (tblpr, grid, rows)


def par(ppr='', runs='<w:r><w:t>x</w:t></w:r>'):
    return '<w:p>%s%s</w:p>' % (('<w:pPr>%s</w:pPr>' % ppr) if ppr else '', runs)


class OrderTests(T):
    def body(self, extra):
        return doc_mod(BODY + extra)

    def test_valid_rich_formatting_passes(self):
        ppr = '<w:pStyle w:val="a"/><w:keepNext/><w:spacing w:before="0"/><w:ind w:left="0"/><w:jc w:val="center"/><w:rPr><w:b/></w:rPr>'
        run = ('<w:r><w:rPr><w:rFonts w:ascii="x"/><w:b/><w:color w:val="000000"/><w:sz w:val="21"/><w:vertAlign w:val="subscript"/></w:rPr>'
               '<w:t>x</w:t></w:r>')
        tcpr = ('<w:tcW w:w="1000" w:type="dxa"/><w:gridSpan w:val="1"/><w:vMerge/><w:tcBorders><w:top w:val="nil"/><w:left w:val="nil"/>'
                '<w:bottom w:val="nil"/><w:right w:val="nil"/></w:tcBorders><w:shd w:val="clear"/><w:vAlign w:val="center"/>')
        tblpr = (TBLW + '<w:jc w:val="center"/><w:tblBorders><w:top w:val="single"/><w:left w:val="single"/><w:bottom w:val="single"/>'
                 '<w:right w:val="single"/><w:insideH w:val="single"/><w:insideV w:val="single"/></w:tblBorders><w:tblLayout w:type="fixed"/><w:tblLook w:val="0"/>')
        sect = '<w:sectPr><w:pgSz w:w="1" w:h="1"/><w:pgMar w:top="1"/><w:cols w:space="1"/><w:docGrid w:linePitch="1"/></w:sectPr>'
        rep = self.check(mods={'word/document.xml': raw_doc(BODY + par(ppr, run) + tbl(tblpr, tcpr=tcpr) + sect)})
        self.assertEqual(rep.items, [], [x['msg'] for x in rep.items])

    def test_ppr_children_out_of_order(self):
        rep = self.check(mods=self.body(par('<w:jc w:val="left"/><w:spacing w:before="0"/>')))
        self.assertHas(rep, 'order-bad', sc.ERROR)

    def test_ppr_duplicate_child(self):
        rep = self.check(mods=self.body(par('<w:jc w:val="left"/><w:jc w:val="right"/>')))
        self.assertHas(rep, 'order-dup', sc.ERROR)

    def test_ppr_not_first_in_paragraph(self):
        rep = self.check(mods=self.body('<w:p><w:r><w:t>x</w:t></w:r><w:pPr/></w:p>'))
        self.assertHas(rep, 'order-bad', sc.ERROR)

    def test_rpr_not_first_in_run(self):
        rep = self.check(mods=self.body('<w:p><w:r><w:t>x</w:t><w:rPr><w:b/></w:rPr></w:r></w:p>'))
        self.assertHas(rep, 'order-bad', sc.ERROR)

    def test_rpr_tracked_insert_must_come_first(self):
        rep = self.check(mods=self.body(par(runs='<w:r><w:rPr><w:b/><w:ins w:id="9" w:author="a"/></w:rPr><w:t>x</w:t></w:r>')))
        self.assertHas(rep, 'order-bad', sc.ERROR)

    def test_rpr_change_must_be_last(self):
        rep = self.check(mods=self.body(par(runs='<w:r><w:rPr><w:rPrChange w:id="1" w:author="a"><w:rPr/></w:rPrChange><w:b/></w:rPr><w:t>x</w:t></w:r>')))
        self.assertHas(rep, 'order-bad', sc.ERROR)

    def test_rpr_ordinary_misorder_is_warning_only(self):
        rep = self.check(mods=self.body(par(runs='<w:r><w:rPr><w:sz w:val="20"/><w:rFonts w:ascii="x"/></w:rPr><w:t>x</w:t></w:r>')))
        self.assertHas(rep, 'order-rpr', sc.WARN)
        self.assertEqual(rep.count(sc.ERROR), 0)
        self.assertEqual(sc.final_blockers(rep), [])

    def test_tcpr_children_out_of_order(self):
        rep = self.check(mods=self.body(tbl(tcpr='<w:vAlign w:val="top"/><w:tcW w:w="1" w:type="dxa"/>')))
        self.assertHas(rep, 'order-bad', sc.ERROR)

    def test_tblpr_children_out_of_order(self):
        rep = self.check(mods=self.body(tbl(tblpr='<w:tblLook w:val="0"/>' + TBLW)))
        self.assertHas(rep, 'order-bad', sc.ERROR)

    def test_sectpr_children_out_of_order(self):
        rep = self.check(mods={'word/document.xml': raw_doc(BODY + '<w:sectPr><w:pgMar w:top="1"/><w:pgSz w:w="1" w:h="1"/></w:sectPr>')})
        self.assertHas(rep, 'order-bad', sc.ERROR)

    def test_body_with_two_sectpr(self):
        sect = '<w:sectPr><w:pgSz w:w="1" w:h="1"/></w:sectPr>'
        rep = self.check(mods={'word/document.xml': raw_doc(BODY + sect + sect)})
        self.assertHas(rep, 'order-bad', sc.ERROR)

    def test_body_sectpr_not_last(self):
        rep = self.check(mods={'word/document.xml': raw_doc(BODY + '<w:sectPr><w:pgSz w:w="1" w:h="1"/></w:sectPr><w:p/>')})
        self.assertHas(rep, 'order-bad', sc.ERROR)

    def test_tbl_without_grid(self):
        rep = self.check(mods=self.body(tbl(grid='')))
        self.assertHas(rep, 'tbl-struct', sc.ERROR)

    def test_tbl_without_rows(self):
        rep = self.check(mods=self.body(tbl(rows='')))
        self.assertHas(rep, 'tbl-struct', sc.ERROR)

    def test_tblpr_after_grid(self):
        rep = self.check(mods=self.body('<w:tbl>' + GRID + '<w:tblPr>' + TBLW + '</w:tblPr><w:tr><w:tc><w:p/></w:tc></w:tr></w:tbl>'))
        self.assertHas(rep, 'order-bad', sc.ERROR)

    def test_trpr_after_cells(self):
        rep = self.check(mods=self.body(tbl(rows='<w:tr><w:tc><w:p/></w:tc><w:trPr><w:cantSplit/></w:trPr></w:tr>')))
        self.assertHas(rep, 'order-bad', sc.ERROR)

    def test_cell_ends_with_table(self):
        inner = tbl()
        rep = self.check(mods=self.body(tbl(rows='<w:tr><w:tc>' + inner + '</w:tc></w:tr>')))
        self.assertHas(rep, 'tc-last-par', sc.ERROR)

    def test_cell_without_paragraph(self):
        rep = self.check(mods=self.body(tbl(rows='<w:tr><w:tc><w:tcPr/></w:tc></w:tr>')))
        self.assertHas(rep, 'tc-last-par', sc.ERROR)

    def test_tcpr_not_first_in_cell(self):
        rep = self.check(mods=self.body(tbl(rows='<w:tr><w:tc><w:p/><w:tcPr/></w:tc></w:tr>')))
        self.assertHas(rep, 'order-bad', sc.ERROR)

    def test_unknown_word_element_in_ppr_warns(self):
        rep = self.check(mods=self.body(par('<w:bogus/>')))
        self.assertHas(rep, 'order-unknown', sc.WARN)
        self.assertEqual(sc.final_blockers(rep), [])

    def test_extension_namespace_child_in_ppr_is_not_checked(self):
        rep = self.check(mods=doc_mod(BODY + par('<w14:x/>'), NS_W14))
        self.assertNot(rep, 'order-unknown')
        self.assertNot(rep, 'order-bad')


CTS = 'application/vnd.openxmlformats-officedocument.spreadsheetml.'


def make_xlsx(path, mods=None, drop=()):
    parts = {
        '[Content_Types].xml': (HEAD + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                                '<Default Extension="xml" ContentType="application/xml"/>'
                                '<Override PartName="/xl/workbook.xml" ContentType="' + CTS + 'sheet.main+xml"/>'
                                '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="' + CTS + 'worksheet+xml"/></Types>'),
        '_rels/.rels': ROOT_RELS.replace('word/document.xml', 'xl/workbook.xml'),
        'xl/_rels/workbook.xml.rels': (HEAD + '<Relationships xmlns="' + PR + '">' + rel('rId1', 'worksheet', 'worksheets/sheet1.xml') +
                                       '</Relationships>'),
        'xl/workbook.xml': (HEAD + '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="' + R +
                            '"><sheets><sheet name="S1" sheetId="1" r:id="rId1"/></sheets></workbook>'),
        'xl/worksheets/sheet1.xml': HEAD + '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData/></worksheet>',
    }
    parts.update(mods or {})
    with zipfile.ZipFile(path, 'w') as z:
        for n, v in parts.items():
            if n not in drop:
                z.writestr(n, v)


class XlsxTests(T):
    def test_clean_xlsx_passes(self):
        p = os.path.join(self.d, 'a.xlsx')
        make_xlsx(p)
        rep = sc.check_file(p)
        self.assertEqual(rep.items, [], [x['msg'] for x in rep.items])

    def test_xlsx_missing_sheet_part(self):
        p = os.path.join(self.d, 'b.xlsx')
        make_xlsx(p, drop=('xl/worksheets/sheet1.xml',))
        rep = sc.check_file(p)
        self.assertHas(rep, 'rels-target', sc.ERROR)
        self.assertHas(rep, 'ct-dangling', sc.WARN)

    def test_xlsx_sheet_rid_missing(self):
        p = os.path.join(self.d, 'c.xlsx')
        make_xlsx(p, mods={'xl/_rels/workbook.xml.rels': HEAD + '<Relationships xmlns="' + PR + '"/>'})
        self.assertHas(sc.check_file(p), 'rid-missing', sc.ERROR)

    def test_xlsx_specific_hook_runs(self):
        '''write_xlsx 线补的专有检查通过 XLSX_CHECKS 登记；这里验证登记点会被调用，且自身出错时登记为“自检没跑完”。'''
        p = os.path.join(self.d, 'd.xlsx')
        make_xlsx(p)
        calls = []

        def ok(pkg, rep):
            calls.append(pkg.kind)

        def boom(pkg, rep):
            raise RuntimeError('demo')
        sc.XLSX_CHECKS[:] = [ok, boom]
        try:
            rep = sc.check_file(p)
        finally:
            sc.XLSX_CHECKS[:] = []
        self.assertEqual(calls, ['xlsx'])
        self.assertHas(rep, 'selfcheck-incomplete', sc.ERROR)


class OutputTests(T):
    def test_render_and_blockers(self):
        rep = self.check(mods=doc_mod(BODY.replace('<w:commentRangeEnd w:id="0"/>', '') + '<w:p w14:paraId="1"/>', NS_W14))
        lines = sc.render(rep)
        self.assertTrue(lines[0].startswith('结构自检未通过'), lines[0])
        self.assertTrue(any('[严重]' in ln for ln in lines))
        self.assertTrue(any('[注意]' in ln for ln in lines))
        bl = sc.final_blockers(rep)
        self.assertTrue(bl and all(b.startswith('结构问题-') and '：' in b for b in bl), bl)
        self.assertFalse(any('扩展命名空间' in b for b in bl), '注意项不应进入终稿拦截')

    def test_many_items_are_capped_but_counted(self):
        body = ''.join('<w:p><w:bookmarkStart w:id="%d" w:name="b%d"/></w:p>' % (i, i) for i in range(1, 46))
        rep = self.check(mods=doc_mod(body))
        self.assertEqual(rep.count(sc.ERROR), 45)
        self.assertLessEqual(len([x for x in rep.items if x['code'] == 'bookmark-unpaired']), sc.MAX_PER_CODE)
        bl = sc.final_blockers(rep)
        self.assertTrue(any('另有 15 项' in b for b in bl), bl[-1:])

    def test_internal_error_is_reported_not_passed(self):
        orig = sc.check_order

        def broken(pkg, rep):
            raise ValueError('boom')
        sc.check_order = broken
        try:
            p = os.path.join(self.d, 'x.docx')
            make_docx(p)
            rep = sc.check_file(p)
        finally:
            sc.check_order = orig
        self.assertHas(rep, 'selfcheck-incomplete', sc.ERROR)
        self.assertTrue(sc.final_blockers(rep))

    def test_environment_lines(self):
        lines = sc.environment_lines()
        txt = '\n'.join(lines)
        for key in ('Python', '办公软件', 'PDF 库', 'OCR 后端', 'vendor'):
            self.assertIn(key, txt)


class CliTests(T):
    def run_cli(self, *args):
        import subprocess
        di = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'traced-template-fill', 'scripts', 'di.py')
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8', DI_UTF8='1')
        r = subprocess.run([sys.executable, '-B', di] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=300)
        return r.returncode, r.stdout.decode('utf-8', 'replace') + r.stderr.decode('utf-8', 'replace')

    def test_doctor_selfcheck_clean_and_broken(self):
        good = os.path.join(self.d, 'good.docx')
        make_docx(good)
        rc, txt = self.run_cli('doctor', '--selfcheck', good)
        self.assertEqual(rc, 0, txt)
        self.assertIn('结构自检通过', txt)
        bad = os.path.join(self.d, 'bad.docx')
        make_docx(bad, mods=doc_mod(BODY.replace('<w:commentRangeEnd w:id="0"/>', '')))
        rc, txt = self.run_cli('doctor', '--selfcheck', bad)
        self.assertEqual(rc, 2, txt)
        self.assertIn('结构自检未通过', txt)


class FinalizeIntegrationTests(T):
    '''终稿阶段：结构自检的严重项必须拦截，--accept-unverified 时登记到遗留项。用一个最小的假任务对象，不依赖真实任务目录。'''

    class FakeJob(object):
        def __init__(self, d):
            self.dir, self.stem = d, 'demo'
            self.data = {'target': {'path': os.path.join(d, 'demo.docx')}, 'template_comments': [], 'unverified': {}}

        def p(self, *a):
            return os.path.join(self.dir, *a)

        def read(self, name, default=None):
            return default

    def draft(self, body):
        dd = os.path.join(self.d, '草稿')
        os.makedirs(dd, exist_ok=True)
        p = os.path.join(dd, 'demo_草稿_v1.docx')
        mods = doc_mod(body)
        mods['word/comments.xml'] = COMMENTS.replace('w:author="t"', 'w:author="审核人"')
        make_docx(p, mods=mods)
        return p

    def test_structural_error_blocks_final(self):
        from dilib.common import DIError
        from dilib.finalize import finalize
        job = self.FakeJob(self.d)
        body = BODY + '<w:p><w:pPr><w:jc w:val="left"/><w:spacing w:before="0"/></w:pPr></w:p>'
        with self.assertRaises(DIError) as cm:
            finalize(job, self.draft(body), reviewer_comments='keep')
        self.assertIn('结构问题-', str(cm.exception))
        self.assertFalse(os.path.exists(os.path.join(self.d, '终稿', 'demo_终稿.docx')), '被拦截时不能产出正式稿')
        with open(os.path.join(self.d, '终稿拦截清单.md'), encoding='utf-8') as f:
            self.assertIn('元素顺序不对', f.read())

    def test_accept_unverified_records_structural_issue(self):
        from dilib.finalize import finalize
        job = self.FakeJob(self.d)
        body = BODY + '<w:p><w:pPr><w:jc w:val="left"/><w:spacing w:before="0"/></w:pPr></w:p>'
        res = finalize(job, self.draft(body), reviewer_comments='keep', accept_unverified=True,
                       user_quote='本次保留已列出的遗留项。')
        self.assertTrue(any(b.startswith('结构问题-') for b in res['blockers']), res['blockers'])
        with open(os.path.join(self.d, '终稿', '终稿遗留项.md'), encoding='utf-8') as f:
            self.assertIn('结构问题-', f.read())

    def test_clean_draft_is_not_blocked_by_selfcheck(self):
        from dilib.finalize import finalize
        job = self.FakeJob(self.d)
        res = finalize(job, self.draft(BODY), reviewer_comments='keep')
        self.assertTrue(os.path.exists(os.path.join(self.d, '终稿', 'demo_终稿.docx')))
        self.assertFalse(any('结构' in b for b in res['blockers']))


if __name__ == '__main__':
    unittest.main(verbosity=1)
