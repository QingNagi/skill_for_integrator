import contextlib
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from test_selfcheck import COMMENTS, doc_xml, make_docx
from dilib import cli


class RunDraftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / "交接模板.docx"
        self.source = self.root / "研制任务书.docx"
        self.heading = '<w:pPr><w:pStyle w:val="Heading1"/><w:outlineLvl w:val="0"/></w:pPr>'
        body = ('<w:p>' + self.heading + '<w:commentRangeStart w:id="0"/>'
                '<w:r><w:t>一、使用缘由</w:t></w:r><w:commentRangeEnd w:id="0"/>'
                '<w:r><w:commentReference w:id="0"/></w:r></w:p><w:p/>')
        make_docx(self.target, {"word/document.xml": doc_xml(body),
                               "word/comments.xml": COMMENTS.replace("note", "研制任务书中的项目背景")})

    def source_section(self, title):
        body = ('<w:p>' + self.heading + '<w:r><w:t>1 ' + title + '</w:t></w:r></w:p>'
                '<w:p><w:r><w:t>标识板用于区分两条路线。</w:t></w:r></w:p>')
        make_docx(self.source, {"word/document.xml": doc_xml(body)})

    def invoke(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(cli.main(list(map(str, args))), 0)
        return output.getvalue()

    def run_job(self, name, *options):
        return self.invoke("run", "-t", self.target, "-s", self.source, "--task",
                           "按批注摘录", "--out", self.root, "--name", name, *options)

    def test_one_plan_and_the_same_verified_draft_as_separate_commands(self):
        self.source_section("项目背景")
        self.run_job("分步")
        self.assertFalse((self.root / "分步/草稿").exists())
        self.invoke("draft", self.root / "分步")
        with patch.object(cli, "do_plan", wraps=cli.do_plan) as planner:
            output = self.run_job("合并", "--draft")
        self.assertEqual(planner.call_count, 1)
        self.assertIn("审核报告", output)
        self.assertIn("S1:H0", output)
        self.assertIn("未填 0", output)
        self.assertTrue((self.root / "合并/审核报告.md").is_file())
        def document(name):
            path, = (self.root / name / "草稿").glob("*.docx")
            with zipfile.ZipFile(path) as archive:
                self.assertIsNone(archive.testzip())
                return archive.read("word/document.xml")
        self.assertIn("标识板用于区分两条路线。".encode(), document("合并"))
        self.assertEqual(document("合并"), document("分步"))

    def test_unresolved_source_stops_with_choices_and_no_draft(self):
        self.source_section("运输包装")
        output = self.run_job("缺依据", "--draft")
        self.assertIn("Q1", output)
        self.assertFalse((self.root / "缺依据/草稿").exists())
        self.assertFalse((self.root / "缺依据/answers.json").exists())

    def test_template_without_an_actionable_mapping_is_not_delivered_as_filled(self):
        self.source_section("项目背景")
        make_docx(self.target, {"word/document.xml": doc_xml('<w:p><w:r><w:t>待填写</w:t></w:r></w:p>')})
        output = self.run_job("待定位", "--draft")
        self.assertIn("请指定", output)
        self.assertFalse((self.root / "待定位/草稿").exists())
        self.assertTrue((self.root / "待定位/plan.json").is_file())
