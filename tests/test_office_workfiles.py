"""Word conversion uses private scratch and preserves user environment and cache permissions."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from dilib import office


class WordWorkfileTests(unittest.TestCase):
    def test_creates_missing_content_word_under_existing_user_cache(self):
        with tempfile.TemporaryDirectory() as cache:
            with patch.object(office, "_user_internet_cache", return_value=cache):
                self.assertIsNone(office.word_workfile_error())
            self.assertEqual(list((Path(cache) / 'Content.Word').iterdir()), [])

    def test_denied_cache_stops_word_before_com_open(self):
        with patch.object(office, "_user_internet_cache", return_value="unused"), \
             patch.object(office.os, "mkdir", side_effect=PermissionError("denied")), \
             patch.object(office, "_engines", return_value=[("com", "Word.Application")]), \
             patch.object(office, "_run_ps", side_effect=AssertionError("must not launch COM")):
            result = office.convert_many([("example.doc", "result.docx", "docx")])
        self.assertIn("Content.Word", result["result.docx"])
        self.assertIn("另存", result["result.docx"])


    def test_missing_registered_cache_tree_is_created_under_existing_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            configured = Path(tmp) / "new user cache" / "Cache"
            with patch.object(office, "_user_internet_cache", return_value=str(configured)):
                self.assertIsNone(office.word_workfile_error())
                self.assertIsNone(office.word_workfile_error())
            self.assertEqual(list((configured / "Content.Word").iterdir()), [])

    def test_bad_host_temp_uses_one_private_scratch_for_the_entire_com_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "normal user cache"
            invalid = root / "ordinary file"
            invalid.write_bytes(b"not a directory")
            outputs = [root / "output" / (name + ".docx") for name in ("first", "second")]
            work_paths = []

            def run_child(command, **kwargs):
                work = Path(kwargs["env"]["TEMP"])
                work_paths.append(work)
                self.assertEqual(work.parent, outputs[0].parent)
                self.assertTrue(work.is_dir())
                self.assertEqual(kwargs["env"]["TMP"], str(work))
                self.assertEqual(os.environ["TMP"], str(invalid))
                self.assertTrue((work / "run.ps1").is_file())
                self.assertEqual(Path(command[-4]).parent, work)
                self.assertEqual(len(Path(command[-4]).read_text(encoding="utf-8-sig").splitlines()), 2)
                for output in outputs:
                    output.write_bytes(b"example")
                Path(command[-3]).write_text("".join("OK\t%s\n" % output for output in outputs), encoding="utf-8")

            with patch.dict(os.environ, TMP=str(invalid), TEMP=str(invalid)), \
                    patch.object(office, "_engines", return_value=[("com", "Word.Application")]), \
                    patch.object(office, "_user_internet_cache", return_value=str(cache)), \
                    patch.object(office, "detect", return_value={"powershell":"powershell.exe"}), \
                    patch.object(office.subprocess, "run", side_effect=run_child):
                result = office.convert_many([("source.doc", str(output), "docx") for output in outputs])
                self.assertEqual(result, {str(output): None for output in outputs})
                self.assertEqual(os.environ["TEMP"], str(invalid))
            self.assertEqual(len(work_paths), 1)
            self.assertFalse(work_paths[0].exists())

    def test_cache_denial_tries_only_legacy_docx_recovery_once(self):
        from dilib import word_converter
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dst, pdf = str(root / "legacy.docx"), str(root / "export.pdf")
            msg = "Content.Word cache unavailable"
            with patch.object(office, "_engines", return_value=[("com", "Word.Application")]), \
                    patch.object(office, "word_workfile_error", return_value=msg) as checked, \
                    patch.object(word_converter, "try_convert_doc", return_value=True) as fallback, \
                    patch.object(office, "_com_batch", side_effect=AssertionError("No COM activation")):
                result = office.convert_many([("local.doc", dst, "docx"), ("local.doc", pdf, "pdf")])
            self.assertEqual(result, {dst: None, pdf: msg})
            checked.assert_called_once_with()
            fallback.assert_called_once_with("local.doc", dst)

    def test_word_open_rejection_never_uses_workfile_recovery(self):
        from dilib import word_converter
        with tempfile.TemporaryDirectory() as tmp:
            dst = str(Path(tmp) / "out.docx")
            denied = "Word refused to open the document"
            with patch.object(office, "_engines", return_value=[("com", "Word.Application")]), \
                    patch.object(office, "word_workfile_error", return_value=None), \
                    patch.object(office, "_com_batch", return_value={"*": denied}), \
                    patch.object(word_converter, "try_convert_doc") as fallback:
                self.assertEqual(office.convert_many([("source.doc", dst, "docx")]), {dst: denied})
            fallback.assert_not_called()

    def test_concurrent_libreoffice_conversions_have_separate_profiles_and_outputs(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "有空格 #"
            out.mkdir()
            inputs = [root / label / "same.doc" for label in ("one", "two")]
            targets = [out / (p.parent.name + ".pdf") for p in inputs]
            barrier = Barrier(2)
            works = []

            def convert_process(cmd, **kw):
                work = Path(kw["cwd"])
                works.append(work)
                self.assertEqual(work.parent, out)
                self.assertIn("-env:UserInstallation=" + (work / "profile").as_uri(), cmd)
                self.assertEqual({kw["env"][k] for k in ("TMPDIR", "TEMP", "TMP")}, {str(work)})
                stage = Path(cmd[cmd.index("--outdir") + 1])
                self.assertEqual(stage.parent, work)
                barrier.wait(timeout=5)
                (stage / "same.pdf").write_bytes(Path(cmd[-1]).read_bytes())
                return office.subprocess.CompletedProcess(cmd, 0)

            invalid = root / "not-a-temp-dir"
            invalid.write_bytes(b"x")
            for src in inputs:
                src.parent.mkdir()
                src.write_bytes(src.parent.name.encode())
            with patch.dict(os.environ, TEMP=str(invalid), TMP=str(invalid), TMPDIR=str(invalid)), \
                    patch.object(office, "_engines", return_value=[("lo", "test-soffice")]), \
                    patch.object(office.subprocess, "run", side_effect=convert_process), ThreadPoolExecutor(2) as pool:
                jobs = [pool.submit(office.convert_many, [(str(src), str(dst), "pdf")])
                        for src, dst in zip(inputs, targets)]
                self.assertEqual([job.result() for job in jobs], [{str(dst): None} for dst in targets])
            self.assertEqual([dst.read_bytes() for dst in targets], [b"one", b"two"])
            self.assertEqual(len(set(works)), 2)
            self.assertFalse(any(work.exists() for work in works))

    def test_lo_rejects_missing_empty_and_nonzero_results_preserving_existing_dest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "same.pdf"
            target.write_bytes(b"existing")
            for code, write in ((0, None), (0, b""), (7, b"incomplete")):
                def run(cmd, **kw):
                    if write is not None:
                        (Path(cmd[cmd.index("--outdir") + 1]) / "same.pdf").write_bytes(write)
                    return office.subprocess.CompletedProcess(cmd, code)
                with self.subTest(code=code, output=write), patch.object(office.subprocess, "run", side_effect=run):
                    self.assertTrue(office._lo_convert("soffice", root / "same.doc", "pdf", target))
                    self.assertEqual(target.read_bytes(), b"existing")
                    self.assertEqual(list(root.iterdir()), [target])

    def test_com_timeout_advances_to_lo_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "final.pdf"
            calls = []
            def run(cmd, **kw):
                calls.append(cmd[0])
                self.assertGreater(kw["timeout"], 0)
                if cmd[0] == "test-powershell":
                    raise office.subprocess.TimeoutExpired(cmd, kw["timeout"])
                (Path(cmd[cmd.index("--outdir") + 1]) / "source.pdf").write_bytes(b"valid replacement")
                return office.subprocess.CompletedProcess(cmd, 0)
            with patch.object(office, "word_workfile_error", return_value=None), \
                    patch.object(office, "detect", return_value={"powershell": "test-powershell"}), \
                    patch.object(office, "_engines", return_value=[("com", "Word.Application"), ("lo", "soffice")]), \
                    patch.object(office.subprocess, "run", side_effect=run):
                result = office.convert_many([("source.doc", str(target), "pdf")])
            self.assertEqual(calls, ["test-powershell", "soffice"])
            self.assertIsNone(result[str(target)])
            self.assertEqual(target.read_bytes(), b"valid replacement")
            self.assertEqual(list(root.iterdir()), [target])

    def test_metafile_lo_writes_requested_filename_and_lists_only_successes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good, bad = str(root / "renamed.png"), str(root / "old.png")
            Path(bad).write_bytes(b"previous")
            def run(cmd, **kw):
                if Path(cmd[-1]).name == "first.emf":
                    (Path(cmd[cmd.index("--outdir") + 1]) / "first.png").write_bytes(b"new")
                return office.subprocess.CompletedProcess(cmd, 0)
            with patch.object(office, "detect", return_value={"powershell": None, "soffice": "soffice"}), \
                    patch.object(office.subprocess, "run", side_effect=run):
                result = office.metafile_to_png([("first.emf", good), ("missing.wmf", bad)])
            self.assertEqual(result, [good])
            self.assertEqual(Path(good).read_bytes(), b"new")
            self.assertEqual(Path(bad).read_bytes(), b"previous")

    def test_previous_backend_denial_prevents_wordconv_recovery(self):
        from dilib import word_converter
        with tempfile.TemporaryDirectory() as tmp:
            dst = str(Path(tmp, "out.docx"))
            with patch.object(office, "_engines", return_value=[("com", "KWps.Application"), ("com", "Word.Application")]), \
                    patch.object(office, "_com_batch", return_value={"*":"WPS 拒绝打开"}), \
                    patch.object(office, "word_workfile_error", return_value="Content.Word 不可写"), \
                    patch.object(word_converter, "try_convert_doc") as alternative:
                result = office.convert_many([("source.doc", dst, "docx")])
            self.assertEqual(result[dst], "WPS 拒绝打开")
            alternative.assert_not_called()
