"""Word review, tracked table edits and delivery inspection on small OOXML files."""
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from test_selfcheck import BODY, COMMENTS, R, W, doc_xml, make_docx
from dilib import finalize, merge
from dilib.common import TOOL_AUTHOR
from dilib.job import Job
from dilib.ooxml import Package, w


def row(text, revision=""):
    prop = '<w:trPr><w:%s w:id="2" w:author="复核员"/></w:trPr>' % revision if revision else ""
    return '<w:tr>%s<w:tc><w:p><w:r><w:t>%s</w:t></w:r></w:p></w:tc></w:tr>' % (prop, text)


class WordRoundtrip(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.src = self.root / "记录.docx"
        make_docx(self.src)
        source = self.root / "来源.docx"
        make_docx(source)
        self.job = Job.create(str(self.src), [str(source)], out_root=str(self.root), name="复核")
        self.draft = Path(self.job.next_draft()[0])
        self.draft.parent.mkdir()
        self.pristine = self.draft.parent / ".原始" / self.draft.name
        self.pristine.parent.mkdir()

    def test_final_accepts_tracked_rows_deleted_cells_and_run_replacements(self):
        body = ('<w:tbl>' + row("已删除", "del") + row("已增加", "ins")
                + '<w:tr><w:tc><w:tcPr><w:cellDel w:id="3"/></w:tcPr><w:p><w:r><w:t>删列内容</w:t>'
                  '</w:r></w:p></w:tc><w:tc><w:p><w:del><w:r><w:delText>旧</w:delText></w:r></w:del>'
                  '<w:ins><w:r><w:t>新</w:t></w:r></w:ins></w:p></w:tc></w:tr></w:tbl>')
        make_docx(self.draft, {"word/document.xml": doc_xml(body)})
        pkg = Package(self.draft)
        finalize.accept_revisions(pkg)
        root = pkg.xml("word/document.xml")
        self.assertEqual([t.text for t in root.iter(w("t"))], ["已增加", "新"])
        self.assertEqual(len(list(root.iter(w("tr")))), 2)

    def test_draft_repairs_inherited_cell_order_without_changing_format_or_template(self):
        from dilib import selfcheck
        from dilib.write_docx import DocxWriter
        original = ('<w:tcW w:w="1000" w:type="dxa"/><w:vAlign w:val="center"/><w:gridSpan w:val="2"/>'
                    '<w:vMerge w:val="restart"/><w:shd w:fill="EDB000"/>')
        body = BODY.replace('<w:gridCol w:w="1000"/>', '<w:gridCol w:w="500"/>' * 2)
        body = body.replace('<w:tcW w:w="1000" w:type="dxa"/>', original)
        make_docx(self.src, {"word/document.xml": doc_xml(body)})
        source = self.src.read_bytes()
        self.assertIn("order-bad", selfcheck.check_file(self.src).codes(selfcheck.ERROR))
        writer = DocxWriter(str(self.src), self.job, {}, str(self.draft))
        writer.apply([], str(self.root))
        self.assertNotIn("order-bad", selfcheck.check_file(self.draft).codes(selfcheck.ERROR))
        self.assertEqual(source, self.src.read_bytes())
        before = next(Package(self.src).xml("word/document.xml").iter(w("tcPr")))
        after = next(Package(self.draft).xml("word/document.xml").iter(w("tcPr")))
        self.assertEqual([e.tag for e in after], [w(n) for n in ("tcW","gridSpan","vMerge","shd","vAlign")])
        self.assertEqual({e.tag:e.attrib for e in before}, {e.tag:e.attrib for e in after})

    def test_foreign_cell_properties_stay_in_place_and_already_valid_parts_stay_byte_identical(self):
        template = ('<w:tcW w:w="1000" w:type="dxa"/><w:vAlign w:val="bottom"/><w:shd w:fill="C0C0C0"/>'
                    '<foreign:vAlign xmlns:foreign="urn:cell-layout" foreign:hint="left"/>'
                    '<w:hideMark/><w:headers><w:header w:val="item"/></w:headers>')
        make_docx(self.src, {"word/document.xml":doc_xml(BODY.replace('<w:tcW w:w="1000" w:type="dxa"/>', template))})
        pkg = Package(self.src)
        self.assertEqual(pkg.normalize_cell_properties(), 1)
        pkg.save(self.draft)
        fixed = Package(self.draft)
        before = dict(fixed.parts)
        self.assertEqual(fixed.normalize_cell_properties(), 0)
        fixed.save(self.draft)
        self.assertEqual(Package(self.draft).parts, before)
        children = list(next(fixed.xml("word/document.xml").iter(w("tcPr"))))
        self.assertEqual([e.tag for e in children], [w("tcW"),w("shd"),w("vAlign"),"{urn:cell-layout}vAlign",w("hideMark"),w("headers")])
        self.assertEqual(children[3].get("{urn:cell-layout}hint"), "left")

    def test_redraft_preserves_reviewed_structure_without_a_reviewer_comment(self):
        new = self.draft.parent / "新版.docx"
        make_docx(self.pristine)
        new.write_bytes(self.pristine.read_bytes())
        make_docx(self.draft, {"word/document.xml": doc_xml(BODY.replace("</w:tbl>", row("审核新增", "ins") + "</w:tbl>"))})
        result = merge.merge_previous(self.job, str(self.draft), str(new))
        self.assertFalse(result["items"], result)
        self.assertIn("审核新增", "".join(Package(new).xml("word/document.xml").itertext()))

    def test_accepting_deletion_of_a_paragraph_mark_joins_the_reviewed_text(self):
        deleted_break = '<w:pPr><w:rPr><w:del w:id="4" w:author="审核甲"/></w:rPr></w:pPr>'
        body = '<w:p>' + deleted_break + '<w:r><w:t>上半段</w:t></w:r></w:p><w:p><w:r><w:t>下半段</w:t></w:r></w:p>'
        make_docx(self.draft, {"word/document.xml": doc_xml(body)})
        pkg = Package(self.draft)
        finalize.accept_revisions(pkg)
        paragraphs = pkg.xml("word/document.xml").find(w("body")).findall(w("p"))
        self.assertEqual(["".join(p.itertext()) for p in paragraphs], ["上半段下半段"])

    def test_structural_conflict_is_reported_on_all_following_drafts(self):
        new = self.draft.parent / "新版.docx"
        make_docx(self.pristine)
        make_docx(new, {"word/document.xml": doc_xml(BODY.replace("hello", "计划变化")
                  .replace("</w:tbl>", row("新规划行") + "</w:tbl>"))})
        make_docx(self.draft, {"word/document.xml": doc_xml(BODY.replace("</w:tbl>", row("审核新增", "ins") + "</w:tbl>"))})
        result = merge.merge_previous(self.job, str(self.draft), str(new))
        self.assertTrue(result["items"])
        self.job.data["unverified"] = {new.name: result["items"]}
        (self.pristine.parent / new.name).write_bytes(new.read_bytes())
        third = self.draft.parent / "第三版.docx"
        third.write_bytes(new.read_bytes())
        again = merge.merge_previous(self.job, str(new), str(third))
        self.assertTrue(again["items"])

    def test_identical_review_copy_skips_parsing_and_preserves_prior_findings(self):
        from unittest.mock import patch
        make_docx(self.pristine)
        self.draft.write_bytes(self.pristine.read_bytes())
        new = self.draft.parent / "新.docx"
        make_docx(new, {"word/document.xml": doc_xml(BODY.replace("hello", "planning"))})
        registered = {"key": "merge:struct:v0:1", "kind": "unread", "detail": "前一版有无法定位的审核行"}
        self.job.data["unverified"] = {self.draft.name:[registered]}
        with patch.object(merge, "read_units", wraps=merge.read_units) as parse:
            result = merge.merge_previous(self.job, str(self.draft), str(new))
        self.assertEqual(parse.call_count, 0)
        self.assertEqual(result["items"], [registered])
        self.assertIn("planning", "".join(Package(new).xml("word/document.xml").itertext()))

    def test_tracked_row_deletion_is_reported_when_the_new_plan_changes(self):
        old = BODY.replace("</w:tbl>", row("作废的行") + "</w:tbl>")
        new = self.draft.parent / "新版.docx"
        make_docx(self.pristine, {"word/document.xml": doc_xml(old)})
        make_docx(new, {"word/document.xml": doc_xml(old.replace("hello", "新计划"))})
        make_docx(self.draft, {"word/document.xml": doc_xml(old.replace(row("作废的行"), row("作废的行", "del")))})
        result = merge.merge_previous(self.job, str(self.draft), str(new))
        self.assertTrue(any("修订" in it["detail"] for it in result["items"]), result)

    def test_a_new_reviewer_table_is_reported_when_it_cannot_be_transferred(self):
        new = self.draft.parent / "新版.docx"
        make_docx(self.pristine)
        make_docx(new, {"word/document.xml": doc_xml(BODY.replace("hello", "新计划"))})
        make_docx(self.draft, {"word/document.xml": doc_xml(BODY + "<w:tbl>" + row("审核补表") + "</w:tbl>")})
        result = merge.merge_previous(self.job, str(self.draft), str(new))
        self.assertTrue(any("表格" in it["detail"] for it in result["items"]), result)

    def test_people_cleanup_preserves_authors_shared_by_retained_comments(self):
        """A person record should disappear only with their last selected comment."""
        people = ('<w15:people xmlns:w15="%s"><w15:person w15:author="%s"/>'
                  '<w15:person w15:author="审核甲"/><w15:person w15:author="旁注"/></w15:people>'
                  % (finalize.W15, TOOL_AUTHOR))
        comments = COMMENTS.replace('w:author="t"', 'w:author="%s"' % TOOL_AUTHOR).replace("</w:comments>",
                    '<w:comment w:id="1" w:author="审核甲"><w:p/></w:comment>'
                    '<w:comment w:id="2" w:author="审核甲"><w:p/></w:comment></w:comments>')
        make_docx(self.draft, {"word/comments.xml": comments, "word/people.xml": people})
        pkg = Package(self.draft)
        finalize.remove_comments(pkg, {"0", "1"}, finalize.comments_info(pkg))
        self.assertEqual([p.get("{%s}author" % finalize.W15) for p in pkg.xml("word/people.xml")], ["审核甲", "旁注"])

    def test_attachments_owned_by_removed_comments_do_not_leave_original_bytes_in_delivery(self):
        make_docx(self.draft, {"word/comments.xml": COMMENTS.replace('w:author="t"', 'w:author="%s"' % TOOL_AUTHOR)})
        pkg = Package(self.draft)
        comment = pkg.xml("word/comments.xml")[0]
        for part in ["private.png", "shared.png"]:
            rid = pkg.rels("word/comments.xml").add(R + "/image", "media/" + part)
            ET.SubElement(comment, w("drawing"), {"{%s}embed" % R: rid})
            pkg.add_part("word/media/" + part, b"fixture")
            pkg.ensure_override("word/media/" + part, "image/png")
        pkg.rels("word/document.xml").add(R + "/image", "media/shared.png")
        pkg.rels("word/comments.xml").add("urn:example:implicit", "unrelated.xml")
        pkg.add_part("word/unrelated.xml", b"<data/>")
        pkg.save(self.draft)
        finalize.finalize(self.job, self.draft, reviewer_comments="keep", template_comments="keep")
        saved = Package(self.job.p("终稿", "记录_终稿.docx"))
        self.assertNotIn("word/media/private.png", saved.parts)
        self.assertIn("word/media/shared.png", saved.parts)
        self.assertIn("word/unrelated.xml", saved.parts)
        self.assertEqual([r[2] for r in saved.rels("word/comments.xml").items()], ["unrelated.xml"])

    def test_registered_word_reply_records_exact_decision_and_requires_an_updated_draft(self):
        from dilib.common import ReviewBlocked
        from dilib.write_docx import DocxWriter
        import json
        question = {"id": "Q2", "key": "td:samplemap:S1:T0:记录", "topic": "样品对应",
                    "question": "来源 7、8 对应模板 1、2？", "defaulted": True,
                    "input_fp": self.job.fingerprint(),
                    "options": [{"label": "无交集则留空", "effect": {"samplemap": {}}},
                                {"label": "7→1，8→2", "effect": {"samplemap": {"7":"1", "8":"2"}}}]}
        writer = DocxWriter(str(self.src), self.job, {}, str(self.draft))
        writer.prepare_word_questions([question])
        writer.apply([], str(self.root))
        self.job.data["draft_inputs"] = {self.draft.name: question["input_fp"]}
        self.job.data["unverified"] = {self.draft.name: [{"kind":"default", "key":question["key"],
                                       "detail":"样品对应", "effect":{"samplemap":{}}}]}
        self.pristine.write_bytes(self.draft.read_bytes())
        pkg = Package(self.draft)
        comments = pkg.xml("word/comments.xml")
        root = comments[-1]
        self.assertIn("7→1", "".join(root.itertext()))
        list(root.iter(w("p")))[-1].set(finalize.PARA_ID, "000000A1")
        reply = ET.SubElement(comments, w("comment"), {w("author"): TOOL_AUTHOR, w("id"): "7"})
        para = ET.SubElement(reply, w("p"), {finalize.PARA_ID:"000000A2"})
        ET.SubElement(ET.SubElement(para, w("r")), w("t")).text = "确认"
        ext = ET.Element("{%s}commentsEx" % finalize.W15)
        ET.SubElement(ext, "{%s}commentEx" % finalize.W15, {finalize.EX_ID:"000000A1", finalize.EX_DONE:"1"})
        ET.SubElement(ext, "{%s}commentEx" % finalize.W15, {finalize.EX_ID:"000000A2", finalize.EX_PARENT:"000000A1"})
        pkg.new_xml("word/commentsExtended.xml", ext, [("w15", finalize.W15)])
        pkg.save(self.draft)
        with self.assertRaises(ReviewBlocked):
            finalize.finalize(self.job, self.draft, reviewer_comments="drop", template_comments="drop")
        self.assertFalse(self.job.read("answers.json"))
        reply.set(w("author"), "人工复核")
        pkg.save(self.draft)
        with self.assertRaisesRegex(ReviewBlocked, "重新"):
            finalize.finalize(self.job, self.draft, reviewer_comments="drop", template_comments="drop")
        answer = self.job.read("answers.json")[question["key"]]
        self.assertEqual(answer["effect"], {"samplemap":{"7":"1", "8":"2"}})
        self.assertEqual(answer["confirmed_by"], "人工复核")
        self.assertEqual(answer["user_quote"], "确认")
        record = json.loads(Path(self.job.p("decisions.jsonl")).read_text("utf-8").splitlines()[-1])
        self.assertEqual((record["user_quote"], record["draft"]), ("确认", self.draft.name))
        with self.assertRaisesRegex(ReviewBlocked, "重新"):
            finalize.finalize(self.job, self.draft, reviewer_comments="drop", template_comments="drop")
        self.assertFalse(Path(self.job.p("终稿", "记录_终稿.docx")).exists())
        # The archived Word decision must not block every subsequent draft as
        # an unresolved discussion merely because planning changed the body.
        new = self.draft.parent / "新版.docx"
        make_docx(new, {"word/document.xml": doc_xml(BODY.replace("hello", "审核后重排"))})
        result = merge.merge_previous(self.job, str(self.draft), str(new))
        self.assertFalse(result["items"], result)
        self.assertTrue(any("decisions.jsonl" in note for note in result["notes"]))

    def test_recipient_checks_property_variables_revisions_and_embedded_hidden_sheets(self):
        embedded = io.BytesIO()
        with zipfile.ZipFile(embedded, "w") as z:
            z.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                       '<sheets><sheet name="试验底稿" state="veryHidden"/></sheets></workbook>')
        make_docx(self.draft, {
            "docProps/custom.xml": '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties">'
                                   '<property name="内部项目编号" pid="2"/></Properties>',
            "word/settings.xml": '<w:settings xmlns:w="%s"><w:docVars><w:docVar w:name="内部变量" w:val="内容"/>'
                                 '</w:docVars></w:settings>' % W,
            "word/document.xml": doc_xml(BODY.replace("<w:r><w:t>hello</w:t></w:r>",
                                     '<w:ins w:id="1" w:author="合成修订者"><w:r><w:t>hello</w:t></w:r></w:ins>')),
            "word/embeddings/Workbook.xlsx": embedded.getvalue(),
        })
        block, warn = finalize.recipient_check(self.draft)
        report = " ".join(block + warn)
        for expected in ["内部项目编号", "内部变量", "合成修订者", "试验底稿"]:
            self.assertIn(expected, report)
