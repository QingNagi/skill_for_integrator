"""Offline bundle and dependency integrity regressions."""
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
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
                for name in archive.namelist():
                    archive.read(name).decode("utf-8")
        self.assertIn("SKILL.md", paths)
        self.assertIn("config/wheels.lock.json", paths)
        self.assertFalse(any(p.startswith(("assets/", "vendor/")) for p in paths))
        self.assertFalse(any(p.endswith(".whl") or p.startswith(("runtime/", "tools/", "tests/", "qa/")) for p in paths))

    def test_unlisted_files_and_nontext_are_excluded_or_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / bundle.TOP
            shutil.copytree(bundle.ROOT, skill)
            (skill / "scripts/dilib/local_helper.py").write_text("print(1)", encoding="utf-8")
            with patch.object(bundle, "ROOT", str(skill)):
                self.assertNotIn("scripts/dilib/local_helper.py", {rel for _, rel in bundle.source_files()})
                (skill / "scripts/di.py").write_bytes(b"hidden\x00binary")
                with self.assertRaisesRegex(ValueError, "Non-text"):
                    list(bundle.source_files())

    def test_lite_delivery_is_independent_of_skill_code(self):
        if not (Path(fetch.VENDOR) / "py").is_dir():
            self.skipTest("Requires verified wheels")
        with tempfile.TemporaryDirectory() as tmp:
            output, _ = bundle.build(tmp, "test", None)
            with zipfile.ZipFile(output) as archive:
                names = archive.namelist()
                top = bundle.TOP + "-dependencies/"
                self.assertTrue(all(p.startswith(top) for p in names))
                self.assertEqual(sum(p.endswith(".whl") for p in names), 2)
                self.assertIn(top + "SHA256SUMS", names)
                self.assertIn(top + "requirements.txt", names)
                self.assertIn(top + "advisories.json", names)
                self.assertEqual(sum(p.endswith("LICENSE.txt") for p in names), 2)
                self.assertFalse(any(p.endswith(("SKILL.md", ".py")) for p in names))

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

    def test_transient_windows_share_denial_does_not_abort_verified_publication(self):
        spec = fetch.PURE_WHEELS["pdfplumber"]
        path = Path(fetch.VENDOR) / "py" / spec[0]
        if not path.exists():
            self.skipTest("Prepare verified wheel inputs")
        rename = bs.os.rename
        denied = PermissionError("Temporary share denial")
        denied.winerror = 5
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            calls = []

            def initially_busy(src, dst):
                calls.append(dst)
                if len(calls) == 1:
                    raise denied
                rename(src, dst)

            with patch.object(bs.os, "rename", side_effect=initially_busy):
                dest = Path(bs._unpack(path, spec, cache))
            self.assertEqual(calls, [dest, dest])
            self.assertTrue((dest / "pdfplumber/__init__.py").is_file())

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
