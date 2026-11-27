from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from threading import Barrier, Lock
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from dilib.common import DIError, file_sha1, write_json
from dilib.finalize import LOCAL_PATH
from dilib.job import Job


class EntryChecks(unittest.TestCase):
    def test_unnamed_jobs_for_same_name_templates_keep_both_source_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "来源.txt"
            source.write_text("正文", encoding="utf-8")
            jobs = []
            with patch("dilib.job.datetime.datetime") as clock:
                clock.now.return_value = datetime(2026, 11, 27, 12, 0, 0)
                for batch in ("甲", "乙"):
                    folder = root / batch
                    folder.mkdir()
                    target = folder / "交接.txt"
                    target.write_text(batch, encoding="utf-8")
                    jobs.append(Job.create(str(target), [str(source)], out_root=str(root / "输出")))
            self.assertNotEqual(jobs[0].dir, jobs[1].dir)
            self.assertEqual([Path(Job(j.dir).data["target"]["path"]).parent.name for j in jobs], ["甲", "乙"])

    def test_concurrent_model_cache_writes_publish_one_complete_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "解析缓存.json"
            ready = Barrier(2)
            commit = Lock()
            replace = os.replace

            def publish(src, dst):
                ready.wait(timeout=5)
                with commit:
                    replace(src, dst)

            with patch("dilib.common.os.replace", side_effect=publish), ThreadPoolExecutor(2) as pool:
                futures = [pool.submit(write_json, str(dest), {"value": v}) for v in ("甲", "乙")]
                for future in futures:
                    future.result()
            self.assertIn(json.loads(dest.read_text(encoding="utf-8"))["value"], ("甲", "乙"))
            self.assertEqual(list(Path(tmp).iterdir()), [dest])

    def test_failed_source_parse_allows_retry_and_preserves_existing_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, source, out = root / "目标.txt", root / "来源.docx", root / "输出"
            target.write_text("目标", encoding="utf-8")
            source.write_bytes(b"not a DOCX archive")
            with self.assertRaisesRegex(DIError, "来源.docx"):
                Job.create(str(target), [str(source)], out_root=str(out), name="同一任务")
            self.assertFalse((out / "同一任务" / "job.json").exists())
            fixed = root / "修订来源.txt"
            fixed.write_text("有效原文", encoding="utf-8")
            job = Job.create(str(target), [str(fixed)], out_root=str(out), name="同一任务")
            with self.assertRaises(DIError):
                Job.create(str(target), [str(fixed)], out_root=str(out), name="同一任务")
            self.assertEqual(Job(job.dir).data["sources"][0]["path"], str(fixed))

    def test_remote_document_url_and_local_paths(self):
        self.assertIsNone(LOCAL_PATH.search("https://example.org/document"))
        for path in (r"Z:\folder\file", "//server/share", r"\\server\share",
                     "/%s/sample" % "Users", "/%s/sample" % "home", "file:///tmp/doc"):
            with self.subTest(path=path):
                self.assertIsNotNone(LOCAL_PATH.search(path))

    def test_input_validation_precedes_job_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.txt"
            source.write_text("sample", encoding="utf-8")
            output = root / "out"
            with self.assertRaises(DIError):
                Job.create(tmp, [str(source)], out_root=str(output))
            self.assertFalse(output.exists())
            target = root / "target.txt"
            target.touch()
            with self.assertRaises(DIError):
                Job.create(str(target), [str(source), str(root / "missing*.txt")], out_root=str(output))
            self.assertFalse(output.exists())

    def test_cache_keys_match_existing_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "sample.txt"
            src.write_bytes(b"abc")
            self.assertEqual(file_sha1(src), "a9993e364706816a")
