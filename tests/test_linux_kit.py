"""Validate Linux runtime archive transformations on any build host."""
import io
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import build_linux_agent_kit as kit


class LinuxRuntimeTests(unittest.TestCase):
    def test_ocr_cli_is_archived_as_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = Path(tmp) / "tesseract"
            engine.write_bytes(b"synthetic executable")
            payload = Path(tmp) / "payload.tar.gz"
            with kit.compressed_tar(payload) as archive:
                kit.put_file(archive, "runtime/ocr/tesseract", engine, 0o755)
            with tarfile.open(payload) as archive:
                self.assertEqual(archive.getmember("runtime/ocr/tesseract").mode, 0o755)

    def test_release_tiers_select_only_their_locked_dependencies(self):
        self.assertEqual(list(kit.wheel_specs("source")), [])
        lite = {name for _, name, _ in kit.wheel_specs(None)}
        self.assertEqual(lite, {"pypdf", "typing_extensions"})
        for arch in kit.PYTHONS:
            platform = "manylinux_" + arch
            wheels = list(kit.wheel_specs(platform))
            self.assertTrue({"pdfplumber", "pdfminer.six", "pypdfium2"} <= {name for _, name, _ in wheels})
            self.assertTrue(all(rel.startswith(("py/", "wheels/" + platform + "/")) for rel, _, _ in wheels))

    def test_verified_cached_input_needs_no_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model"
            path.write_bytes(b"local")
            with patch.object(kit.request, "build_opener", side_effect=AssertionError("used network")):
                kit.cached_asset(path, "https://example.invalid/model", 5, kit.digest(path))
            self.assertEqual(path.read_bytes(), b"local")

    def test_python_binary_and_relative_symlink_survive_relocation(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "python.tar.gz"
            with tarfile.open(src, "w:gz") as ar:
                binary = tarfile.TarInfo("python/bin/python3.14")
                binary.mode, binary.size = 0o755, 3
                ar.addfile(binary, io.BytesIO(b"bin"))
                link = tarfile.TarInfo("python/bin/python3")
                link.type, link.linkname = tarfile.SYMTYPE, "python3.14"
                ar.addfile(link)
            payload = Path(tmp) / "payload.tar.gz"
            with kit.compressed_tar(payload) as ar:
                kit.copy_runtime(ar, src)
            with tarfile.open(payload) as ar:
                binary = ar.getmember("runtime/python/bin/python3.14")
                self.assertEqual(binary.mode & 0o111, 0o111)
                self.assertEqual(ar.getmember("runtime/python/bin/python3").linkname, "python3.14")

    def test_runtime_archive_link_cannot_leave_its_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "python.tar.gz"
            with tarfile.open(src, "w:gz") as ar:
                link = tarfile.TarInfo("python/bin/link")
                link.type, link.linkname = tarfile.SYMTYPE, "../../outside"
                ar.addfile(link)
            with kit.compressed_tar(Path(tmp) / "payload.tar.gz") as ar:
                with self.assertRaises(ValueError):
                    kit.copy_runtime(ar, src)
