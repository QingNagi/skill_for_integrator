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
