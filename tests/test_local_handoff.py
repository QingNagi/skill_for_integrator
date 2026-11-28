import contextlib
import io
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import test_run_draft as fixtures
from dilib import cli


class Terminal(io.StringIO):
    def isatty(self):
        return True


class LocalHandoffTests(unittest.TestCase):
    setUp = fixtures.RunDraftTests.setUp
    source_section = fixtures.RunDraftTests.source_section
    invoke = fixtures.RunDraftTests.invoke

    def batch(self, title="项目背景"):
        self.source_section(title)
        listing = self.root / "来件.json"
        listing.write_text(json.dumps([{"target": str(self.target), "sources": [str(self.source)],
                                      "task": "按批注摘录"}]), encoding="utf-8")
        return json.loads(self.invoke("batch", "run", listing, "--handoff", "--draft", "--json",
                                      "--out", self.root / "整合输出"))

    def deliver(self, manifest, reply, tty=True, visible=True):
        with patch("sys.stdin", (Terminal if tty else io.StringIO)(reply)), contextlib.redirect_stdout((Terminal if visible else io.StringIO)()) as stream:
            rc = cli.main(["deliver", str(manifest)])
        return rc, stream.getvalue()

    def test_sticky_handoff_requires_tty_and_preserves_reviewed_bytes_on_retry(self):
        ready = self.batch()
        self.assertEqual(ready["status"], "handoff_ready")
        manifest = Path(ready["sealed_manifest"])
        self.assertEqual(manifest.name, "handoff.json")
        job = Path(ready["jobs"][0]["job"])
        replay = json.loads(self.invoke("batch", "plan", ready["batch"], "--draft", "--json"))
        self.assertEqual(replay["sealed_manifest"], str(manifest))
        direct = json.loads(self.invoke("draft", job, "--json"))
        self.assertEqual(direct["status"], "blocked")
        self.assertNotEqual(self.deliver(manifest, "DELIVER\n", tty=False)[0], 0)
        self.assertNotEqual(self.deliver(manifest, "DELIVER\n", visible=False)[0], 0)
        self.assertNotEqual(self.deliver(manifest, "0\n")[0], 0)
        self.assertFalse(list(job.glob("草稿/*.docx")))
        self.assertEqual(self.deliver(manifest, "DELIVER\n")[0], 0)
        draft, = job.glob("草稿/*.docx")
        self.assertEqual(self.deliver(manifest, "")[0], 0)
        draft.write_bytes(b"manual changes")
        self.assertNotEqual(self.deliver(manifest, "DELIVER\n")[0], 0)
        self.assertEqual(draft.read_bytes(), b"manual changes")
        self.assertEqual(list(job.glob("草稿/*.docx")), [draft])

    def test_modified_source_or_manual_operation_invalidates_seal_before_writing(self):
        ready = self.batch()
        manifest = ready["sealed_manifest"]
        source = self.source.read_bytes()
        self.source.write_bytes(source + b"updated")
        self.assertNotEqual(self.deliver(manifest, "DELIVER\n")[0], 0)
        self.source.write_bytes(source)
        job = Path(ready["jobs"][0]["job"])
        (job / "manual_ops.json").write_text('[{"op":"changed"}]', encoding="utf-8")
        self.assertNotEqual(self.deliver(manifest, "DELIVER\n")[0], 0)
        self.assertFalse(list(job.glob("草稿/*.docx")))

    def protected_batch(self):
        with patch.object(sys, "path", [str(Path(__file__).resolve().parents[1] / "qa"), *sys.path]):
            from blind_variants import generate
        from dilib.job import Job
        public = self.root / "inputs"
        generate(public, self.root / "keys", 88726, 1)
        case = public / "case-01"
        listing = self.root / "batch.json"
        listing.write_text(json.dumps([{"target": str(case / "模板.docx"), "sources": [
            str(case / "详细规范.docx"), str(case / "测量表.xlsx")], "task": "只取中间电压，重复值写范围"}]), "utf-8")
        run = ("batch", "run", listing, "--out", self.root / "output", "--draft", "--json")
        step = json.loads(self.invoke(*run, "--handoff"))
        self.assertEqual(step["status"], "needs_answers")
        batch = step["batch"]
        job = Job(step["jobs"][0]["job"])
        self.assertEqual(json.loads(self.invoke("draft", job.dir, "--json"))["status"], "blocked")
        self.assertFalse(job.drafts())
        evidence_file = case / "本批测试程序清单.csv"
        for _ in range(5):
            if step["status"] == "handoff_ready":
                break
            values = [q["id"] + ("=2" if q["header"] in ("来源核对", "实测记录核对") else "=1")
                      for q in step["all_questions"]]
            step = json.loads(self.invoke("batch", "answer", batch, *values, "--evidence", evidence_file, "--draft", "--json",
                                         "--user-quote", "已逐份核对原始记录，按相同样品号。"))
        self.assertEqual(step["status"], "handoff_ready", step)
        manifest = Path(step["sealed_manifest"])
        row = json.loads(manifest.read_text("utf-8"))["jobs"][0]
        self.assertTrue(row["attachments"])
        self.assertTrue(row["confirmations"])
        return batch, job, manifest, row, evidence_file

    @unittest.skipUnless(importlib.util.find_spec("docx") and importlib.util.find_spec("openpyxl"), "fixture generators")
    def test_protected_interview_rejects_stale_seals_and_resumes_after_a_recorded_receipt(self):
        from dilib.decisions import verify_receipts
        from dilib.job import Job
        _, job, manifest, row, evidence_file = self.protected_batch()
        answers = Path(job.p("answers.json"))
        initial = answers.read_bytes()
        rc, log = self.deliver(manifest, "0\n")
        self.assertNotEqual(rc, 0)
        self.assertIn("原确认渠道：Agent 转述", log)
        self.assertIn("原记录所引用户原话：已逐份核对", log)
        self.assertEqual(answers.read_bytes(), initial)
        raw = evidence_file.read_bytes()
        evidence_file.write_bytes(raw + b"\n")
        self.assertNotEqual(self.deliver(manifest, "DELIVER\n")[0], 0)
        evidence_file.write_bytes(raw)
        replies = [",".join(str(i+1) for i in q["current"]["choice"]) for q in row["confirmations"]]
        self.assertNotEqual(self.deliver(manifest, "\n".join([*replies, "0", ""]))[0], 0)
        self.assertFalse(job.drafts())
        self.assertEqual(answers.read_bytes(), initial)
        original = Job.write
        def interrupt(j, name, value):
            if name == "answers.json":
                raise OSError("模拟写答案前中断")
            return original(j, name, value)
        with patch.object(Job, "write", interrupt):
            rc, log = self.deliver(manifest, "\n".join([*replies, "DELIVER", ""]))
            self.assertEqual(rc, 2, log)
            self.assertIn("模拟写答案前中断", log)
        self.assertFalse(job.drafts())
        rc, log = self.deliver(manifest, "")
        self.assertEqual(rc, 0, log)
        self.assertEqual(len(job.drafts()), 1)
        audit = verify_receipts(job, Path(job.drafts()[0]).name, job.fingerprint(files_only=True), require_signed=True)
        self.assertEqual({r["source"] for r in audit}, {"confirm_cli"})
        self.assertEqual(self.deliver(manifest, "")[0], 0)
        events = [json.loads(s) for s in Path(job.p("decisions.jsonl")).read_text("utf-8").splitlines()]
        self.assertEqual(sum("handoff" in e for e in events), 1)

    @unittest.skipUnless(importlib.util.find_spec("docx") and importlib.util.find_spec("openpyxl"), "fixture generators")
    def test_a_valid_correction_retires_the_manifest_and_requires_fresh_review(self):
        batch, job, manifest, row, _ = self.protected_batch()
        replies = [",".join(str(i+1) for i in q["current"]["choice"]) for q in row["confirmations"]]
        first = row["confirmations"][0]
        alternate = next(i for i in range(len(first["options"])) if i not in first["current"]["choice"])
        replies[0] = str(alternate + 1)
        rc, log = self.deliver(manifest, "\n".join([*replies, ""]))
        self.assertEqual(rc, 2, log)
        self.assertIn("已登记更改", log)
        self.assertFalse(job.drafts())
        self.assertEqual(job.read("answers.json")[first["key"]]["choice"], [alternate])
        step = json.loads(self.invoke("batch", "plan", batch, "--json", "--draft"))
        self.assertIn(step["status"], ("needs_answers", "handoff_ready"))
        self.assertNotEqual(step.get("sealed_manifest"), str(manifest))
        self.assertEqual(self.deliver(manifest, "DELIVER\n")[0], 2)
        self.assertFalse(job.drafts())
