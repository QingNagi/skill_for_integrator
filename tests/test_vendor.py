"""Offline bundle and dependency integrity regressions."""
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "traced-template-fill" / "scripts"))
sys.path.insert(0, str(ROOT / "tools"))
from dilib import _bootstrap as bs
import fetch_vendor as fetch
import build_offline_bundle as bundle


class VendorIntegrityTests(unittest.TestCase):
    def test_source_zip_requires_no_wheel_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            output, _ = bundle.build(tmp, "test", "source", Path(tmp) / "no-vendor-directory")
            with zipfile.ZipFile(output) as archive:
                paths = [item[len(bundle.TOP) + 1:] for item in archive.namelist()]
        self.assertIn("SKILL.md", paths)
        self.assertIn("vendor/wheels.lock.json", paths)
        self.assertFalse(any(p.endswith(".whl") or p.startswith(("runtime/", "tools/", "tests/", "qa/")) for p in paths))

    def test_pinned_native_wheel_layout_for_each_platform(self):
        self.assertEqual(set(fetch.WHEELS), {"win_amd64", "manylinux_x86_64", "manylinux_aarch64"})
        for tag, packages in fetch.WHEELS.items():
            self.assertEqual(set(packages), {"cffi", "cryptography", "Pillow", "pypdfium2"})
            if not (Path(fetch.VENDOR) / "wheels" / tag).exists():
                continue
            for spec in packages.values():
                name, length, digest, _ = spec
                self.assertTrue(bs.is_good(Path(fetch.VENDOR) / "wheels" / tag / name, length, digest))

    def test_lite_wheels_have_pinned_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("pypdf", "typing_extensions"):
                wheel, size, digest, _ = fetch.PURE_WHEELS[name]
                source = Path(fetch.VENDOR) / "py" / wheel
                if not source.is_file():
                    self.skipTest("Prepare the optional lite inputs with tools/fetch_vendor.py --platform lite")
                self.assertTrue(fetch.is_good(source, size, digest))
                data = bytearray(source.read_bytes())
                data[-1] ^= 1
                changed = Path(tmp) / wheel
                changed.write_bytes(data)
                self.assertFalse(fetch.is_good(changed, size, digest))

    def test_untrusted_wheel_and_modified_extracted_code_are_rejected(self):
        spec = fetch.PURE_WHEELS["pypdf"]
        path = Path(fetch.VENDOR) / "py" / spec[0]
        if not path.exists():
            self.skipTest("Prepare verified wheel inputs")
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            dest = Path(bs._unpack(path, spec, cache))
            self.assertEqual(Path(bs._unpack(path, spec, cache)), dest)
            (dest / "unexpected.py").write_text("x=1", encoding="utf-8")
            with self.assertRaises(ValueError):
                bs._unpack(path, spec, cache)
            (dest / "unexpected.py").unlink()
            module = dest / "pypdf/__init__.py"
            module.write_bytes(b"#" * module.stat().st_size)
            with self.assertRaises(ValueError):
                bs._unpack(path, spec, cache)
            modified = Path(tmp) / path.name
            modified.write_bytes(b"x" * spec[1])
            with self.assertRaises(ValueError):
                bs._unpack(modified, spec, cache)

    def test_download_rejects_insecure_or_external_redirects(self):
        base = "https://files.pythonhosted.org/packages/"
        for url in ("http://files.pythonhosted.org/a", "file:///sample", "https://example.org/a"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                fetch.checked_url(url, {"files.pythonhosted.org"})
        self.assertEqual(fetch.checked_url(base, {"files.pythonhosted.org"}).hostname,
                         "files.pythonhosted.org")
