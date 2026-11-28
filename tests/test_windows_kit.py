"""Build both Windows formats twice with different filesystem timestamps."""
from contextlib import ExitStack
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import build_win64_agent_kit as kit
import prepare_win64_runtime as prep


class WindowsKitTests(unittest.TestCase):
    def test_runtime_stage_retries_brief_sharing_denial(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as packed:
            packed.writestr("python.exe", b"synthetic")
        def original(_, name, *pins):
            return archive.getvalue() if name == prep.PYTHON_ZIP else b"synthetic"

        rename = os.rename
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "runtime"
            attempts = []
            def interrupted_once(source, dest):
                attempts.append(dest)
                if len(attempts) == 1:
                    denial = PermissionError("temporary sharing violation")
                    denial.winerror = 32
                    raise denial
                rename(source, dest)

            with patch.object(prep, "read_verified", side_effect=original), \
                    patch.object(prep.os, "rename", side_effect=interrupted_once), patch.object(prep.time, "sleep"):
                prep.stage_runtime(target, None)
            self.assertEqual(attempts, [target, target])
            self.assertEqual((target / "python/python.exe").read_bytes(), b"synthetic")

    def test_both_archives_repeat_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as overrides:
            root = Path(tmp)
            engine = root / "tesseract.exe"
            engine.write_bytes(b"synthetic OCR")
            overrides.enter_context(patch.object(kit, "ocr_binary", return_value=engine))
            overrides.enter_context(patch.object(kit, "runtime_bom", return_value=b"{}"))
            overrides.enter_context(patch.object(kit.bundle, "wheel_files", return_value=[]))
            for runtime_only in (False, True):
                candidates = []
                for timestamp in (1_000_000_000, 1_500_000_000):
                    def stage(runtime, _):
                        file = runtime / "python/python.exe"
                        file.parent.mkdir(parents=True)
                        file.write_bytes(b"synthetic executable")
                        os.utime(file, (timestamp, timestamp))

                    dest = root / ("candidate-" + str(timestamp)) / "windows.zip"
                    with patch.object(kit, "stage_runtime", side_effect=stage):
                        kit.build_kit(dest, root, root, runtime_only)
                    candidates.append(dest.read_bytes())
                self.assertEqual(len(candidates[0]), len(candidates[1]))
                self.assertTrue(candidates[0] == candidates[1], "Candidate bytes differ across timestamp changes")
                prefix = kit.bundle.TOP + ("/offline-runtime/" if runtime_only else "/")
                with zipfile.ZipFile(io.BytesIO(candidates[0])) as outer:
                    with zipfile.ZipFile(io.BytesIO(outer.read(prefix + "payload.zip"))) as payload:
                        for member in outer.infolist() + payload.infolist():
                            self.assertEqual(member.date_time, (1980, 1, 1, 0, 0, 0))
