import hashlib
import json
from pathlib import Path
import shutil
import unittest
import zipfile

import test_run_draft as fixtures
from dilib.common import ReviewBlocked, TOOL_AUTHOR
from dilib.job import Job
from dilib.ooxml import Package, w
from dilib.finalize import comments_info


class GeneratedSourceTests(unittest.TestCase):
    setUp = fixtures.RunDraftTests.setUp
    invoke = fixtures.RunDraftTests.invoke
    run_job = fixtures.RunDraftTests.run_job
    source_section = fixtures.RunDraftTests.source_section

    def test_generated_outputs_block_as_sources_and_are_identified_after_rename(self):
        self.source_section("项目背景")
        self.run_job("原任务", "--draft")
        job = self.root / "原任务"
        draft, = (job / "草稿").glob("*.docx")
        self.invoke("final", job, "--template-comments", "drop")
        final, = (job / "终稿").glob("*.docx")
        for label, original in (("draft", draft), ("final", final)):
            with self.subTest(kind=label):
                copied = self.root / (label + ".docx")
                shutil.copyfile(original, copied)
                p = Package(copied)
                root = p.xml("docProps/custom.xml")
                field, = [el for el in root if el.get("name") == "TTF-Generated"]
                value = json.loads(field[0].text)
                self.assertEqual(value["kind"], label)
                self.assertEqual(value["body_sha256"], hashlib.sha256(p.parts["word/document.xml"]).hexdigest())
                with self.assertRaisesRegex(ReviewBlocked, "原始规范"):
                    Job.create(self.target, [copied], out_root=self.root, name="回灌")
        self.assertFalse((self.root / "回灌/job.json").exists())
        with zipfile.ZipFile(draft) as z, zipfile.ZipFile(self.root / "old.docx", "w") as old:
            for n in z.namelist():
                if n != "docProps/custom.xml":
                    old.writestr(n, z.read(n))
        with self.assertRaises(ReviewBlocked):
            Job.create(self.target, [self.root / "old.docx"], out_root=self.root, name="旧版来源")
        original_hash = hashlib.sha256(draft.read_bytes()).hexdigest()
        replacement = Job.create(draft, [self.source], out_root=self.root, name="重填")
        self.assertTrue(any("已生成" in v for v in replacement.target_model().m["read_warnings"]))
        self.assertFalse(any(a[0] == TOOL_AUTHOR for a in replacement.data["template_comments"]))
        self.invoke("draft", replacement.dir, "--defaults", "--user-quote", "按推荐生成审核稿")
        second = Package(replacement.drafts()[0])
        self.assertEqual(hashlib.sha256(draft.read_bytes()).hexdigest(), original_hash)
        from dilib.write_docx import DocxWriter
        writer = DocxWriter(str(draft), replacement, {}, replacement.next_draft()[0])
        info = comments_info(writer.pkg)
        self.assertTrue(any(c["author"] == "t" for c in info))
        self.assertFalse(any(c["author"] == TOOL_AUTHOR for c in info))
        self.assertTrue(second.has("docProps/custom.xml"))
