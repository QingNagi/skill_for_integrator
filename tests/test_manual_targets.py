import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from test_selfcheck import W, doc_xml, make_docx
from dilib import cli
from dilib.job import Job
from dilib.planner import Planner
from dilib.profile import DEFAULT
from dilib.read_docx import DocxReader
from dilib.write_docx import DocxWriter


class ManualTargetTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.target = self.root / "template.docx"
        self.template("Heading", "Destination")
        source = self.root / "source.txt"
        source.write_text("Source", encoding="utf-8")
        self.job = Job.create(self.target, [source], out_root=self.root, name="job")

    def template(self, *paragraphs):
        body = "".join("<w:p><w:r><w:t>%s</w:t></w:r></w:p>" % t for t in paragraphs)
        make_docx(self.target, {"word/document.xml": doc_xml(body)})

    def invoke(self, *args):
        with contextlib.redirect_stdout(io.StringIO()):
            return cli.main([args[0], self.job.dir, *args[1:]])

    def test_itemized_report_matches_the_final_appended_or_replaced_sections(self):
        def heading(label, with_comment=False):
            start = '<w:commentRangeStart w:id="0"/>' if with_comment else ''
            end = '<w:commentRangeEnd w:id="0"/><w:r><w:commentReference w:id="0"/></w:r>' if with_comment else ''
            return ('<w:p><w:pPr><w:outlineLvl w:val="0"/></w:pPr>' + start + '<w:r><w:t>' +
                    label + '</w:t></w:r>' + end + '</w:p>')

        def body(text):
            return '<w:p><w:r><w:t>' + text + '</w:t></w:r></w:p>'

        make_docx(self.target, {"word/document.xml": doc_xml(heading("项目说明", True) + body("")),
                                "word/comments.xml": '<w:comments xmlns:w="' + W + '"><w:comment w:id="0" '
                                'w:author="编辑"><w:p><w:r><w:t>请从任务书摘入项目背景。</w:t></w:r></w:p>'
                                '</w:comment></w:comments>'})
        source = self.root / "任务书.docx"
        make_docx(source, {"word/document.xml": doc_xml(heading("项目背景") + body("蓝色卡标示工位。") +
                                                        heading("标识复核") + body("挂上橙色牌。"))})
        self.job = Job.create(self.target, [source], out_root=self.root, name="report-job")

        def reported():
            self.assertEqual(self.invoke("draft"), 0)
            return [(row["source"], row["value"]) for row in self.job.read("plan.json")["report"]["fills"]]

        self.assertEqual(reported(), [("S1:H0", "蓝色卡标示工位。")])
        self.assertEqual(self.invoke("add-section", "--to", "P0", "--src", "S1:H1", "--no-subheading"), 0)
        self.assertEqual(reported(), [("S1:H0", "蓝色卡标示工位。"), ("S1:H1", "挂上橙色牌。")])
        report = (Path(self.job.dir) / "审核报告.md").read_text(encoding="utf-8")
        self.assertEqual(report.count("| 项目说明 | 挂上橙色牌。 | S1:H1 |"), 1)
        self.assertEqual(self.invoke("add-section", "--to", "P0", "--src", "S1:H0", "--replace",
                                     "--no-subheading"), 0)
        self.assertEqual(reported(), [("S1:H0", "蓝色卡标示工位。")])
        self.assertEqual(self.invoke("set", "P0", "审核暂记"), 0)
        self.assertEqual(reported(), [("S1:H0", "蓝色卡标示工位。")])

    def test_shifted_anchor_stops_new_draft(self):
        self.assertEqual(self.invoke("set", "P1", "new text"), 0)
        self.assertEqual(self.invoke("draft"), 0)
        self.assertEqual(len(self.job.drafts()), 1)
        self.template("Added", "Heading", "Destination")
        self.assertEqual(self.invoke("draft"), 2)
        self.assertEqual(len(self.job.drafts()), 1)

    def test_legacy_operation_needs_review_and_can_be_rebound(self):
        ops = [{"op": "set_text", "block": "P1", "text": "new text"}]
        self.job.write("manual_ops.json", ops)
        self.assertEqual(self.invoke("draft"), 2)
        self.assertEqual(self.invoke("add-op", json.dumps(ops), "--replace"), 0)
        self.assertEqual(self.invoke("draft"), 0)

    def test_invalid_anchor_in_batch_keeps_prior_operations(self):
        self.assertEqual(self.invoke("set", "P0", "unchanged"), 0)
        prior = self.job.read("manual_ops.json")
        ops = [{"op": "set_text", "block": "P1", "text": "valid"},
               {"op": "insert_section", "anchor": "P0", "clear": ["P99"], "items": []}]
        self.assertEqual(self.invoke("add-op", json.dumps(ops)), 2)
        self.assertEqual(self.job.read("manual_ops.json"), prior)

    def test_manual_cell_and_comment_follow_original_row_after_automatic_insertion(self):
        body = ("<w:tbl><w:tblGrid><w:gridCol/></w:tblGrid>" +
                "".join("<w:tr><w:tc><w:p><w:r><w:t>%s</w:t></w:r></w:p></w:tc></w:tr>" % text
                        for text in ("header", "destination")) + "</w:tbl>")
        make_docx(self.target, {"word/document.xml": doc_xml(body)})
        self.assertEqual(self.invoke("set", "T0.R1.C0", "updated", "--note", "checked"), 0)
        job = Job(self.job.dir)
        planner = Planner(job, manual_ops=job.read("manual_ops.json"))
        planner.ops = [{"op": "add_rows", "table": "T0", "after_row": 0, "count": 2}]
        planner.row_shift = {"T0": (0, 2)}
        planner.apply_manual()
        draft = self.root / "result.docx"
        DocxWriter(str(self.target), job, DEFAULT, str(draft)).apply(planner.ops, str(self.root))
        model = DocxReader(str(draft)).read()
        table = next(b for b in model["blocks"] if b["id"] == "T0")
        self.assertEqual([cell["text"] for cell in table["cells"]], ["header", "", "", "updated"])
        self.assertEqual(next(op["anchor"] for op in planner.ops if op["op"] == "comment"), "T0.R3.C0")

    def test_replacing_commented_paragraph_keeps_reference_and_anchors_new_text(self):
        make_docx(self.target)
        self.assertEqual(self.invoke("set", "P0", "updated"), 0)
        self.assertEqual(self.invoke("draft"), 0)
        draft, = self.job.drafts()
        with ZipFile(draft) as z:
            w = "{%s}" % W
            para = next(ET.fromstring(z.read("word/document.xml")).iter(w+"p"))
            nodes = list(para.iter())
            ref, = [n for n in nodes if n.tag == w+"commentReference" and n.get(w+"id")=="0"]
            start = next(n for n in nodes if n.tag==w+"commentRangeStart" and n.get(w+"id")=="0")
            end = next(n for n in nodes if n.tag==w+"commentRangeEnd" and n.get(w+"id")=="0")
            value = next(n for n in nodes if n.tag==w+"t" and n.text=="updated")
            self.assertLess(nodes.index(start), nodes.index(value))
            self.assertLess(nodes.index(value), nodes.index(end))
            self.assertLess(nodes.index(end), nodes.index(ref))
