import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from dilib.common import DIError, file_sha1
from dilib.finalize import LOCAL_PATH
from dilib.job import Job


class EntryChecks(unittest.TestCase):
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
