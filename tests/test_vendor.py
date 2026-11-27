"""Offline bundle and dependency integrity regressions."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tools"))
from dilib import _bootstrap as bs
import fetch_vendor as fetch


class VendorIntegrityTests(unittest.TestCase):
    def test_lite_wheels_have_pinned_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("pypdf", "typing_extensions"):
                wheel, size, digest, _ = fetch.PURE_WHEELS[name]
                source = ROOT / "vendor" / "py" / wheel
                self.assertTrue(fetch.is_good(source, size, digest))
                data = bytearray(source.read_bytes())
                data[-1] ^= 1
                changed = Path(tmp) / wheel
                changed.write_bytes(data)
                self.assertFalse(fetch.is_good(changed, size, digest))

    def test_modified_native_wheel_is_rejected_before_extraction(self):
        tag = "win_amd64"
        name, size, _, _ = fetch.WHEELS[tag]
        with tempfile.TemporaryDirectory() as tmp:
            wheel = Path(tmp) / "wheels" / tag / name
            wheel.parent.mkdir(parents=True)
            with wheel.open("wb") as handle:
                handle.truncate(size)
            with patch.object(bs, "_VENDOR", tmp):
                self.assertIsNone(bs._find_wheel(tag))

    def test_cached_native_code_is_checked_before_import(self):
        tag = "win_amd64"
        name = fetch.WHEELS[tag][0]
        path = ROOT / "vendor" / "wheels" / tag / name
        if not path.is_file():
            self.skipTest("Optional Windows native wheel is absent")
        with tempfile.TemporaryDirectory() as tmp, patch.object(bs, "_cache_dirs", return_value=[tmp]):
            first = bs._unpack(str(path), tag)
            self.assertIsNotNone(first)
            mod = Path(first) / "pymupdf" / "utils.py"
            data = mod.read_bytes()
            mod.write_bytes(b"#" * len(data))
            second = bs._unpack(str(path), tag)
            self.assertTrue(second is None or (Path(second) / "pymupdf" / "utils.py").read_bytes() == data)

    def test_download_rejects_insecure_or_external_redirects(self):
        base = "https://files.pythonhosted.org/packages/"
        for url in ("http://files.pythonhosted.org/a", "file:///sample", "https://example.org/a"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                fetch.checked_url(url, {"files.pythonhosted.org"})
        self.assertEqual(fetch.checked_url(base, {"files.pythonhosted.org"}).hostname,
                         "files.pythonhosted.org")
