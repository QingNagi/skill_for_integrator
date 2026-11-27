"""Offline preflight regression tests. Run with python -m unittest discover -s qa."""
import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path


SCRIPT = Path(__file__).with_name("run-tier1.ps1")
POWERSHELL = shutil.which("pwsh")


@unittest.skipUnless(POWERSHELL, "PowerShell 7.2+ required")
class ReviewEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "审查 evidence"

    def review(self, *args):
        return subprocess.run(
            [POWERSHELL, "-NoProfile", "-File", str(SCRIPT), "-OutputDirectory", str(self.output), *map(str, args)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
        )

    def evidence(self):
        return [json.loads(p.read_text("utf-8-sig")) for p in self.output.glob("*/evidence.json")]

    def test_repeated_bad_package_digest_preserves_two_failures(self):
        package = self.root / "kit.zip"
        package.write_bytes(b"unverified release")
        for _ in range(2):
            run = self.review("-Package", package, "-PackageSha256", "0" * 64)
            self.assertNotEqual(run.returncode, 0)
        records = self.evidence()
        self.assertEqual(len(records), 2)
        for record in records:
            self.assertIn("SHA-256 mismatch", record["error"])
            self.assertNotIn("evaluator_exit", record)

    def test_matching_outer_digest_still_rejects_changed_payload(self):
        package = self.root / "kit.zip"
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("doc-integrator/payload.zip", b"wrong internal bytes")
            archive.writestr("doc-integrator/payload.sha256", "0" * 64)
        digest = hashlib.sha256(package.read_bytes()).hexdigest()
        self.assertNotEqual(self.review("-Package", package, "-PackageSha256", digest).returncode, 0)
        record, = self.evidence()
        self.assertEqual(record["package_sha256"], digest)
        self.assertIn("payload.zip", record["error"])
        self.assertNotIn("evaluator_exit", record)

    def test_new_lite_zip_stages_exact_installable_files(self):
        package = self.root / "lite.zip"
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("traced-template-fill/SKILL.md", "---\nname: traced-template-fill\n---\n")
            archive.writestr("traced-template-fill/scripts/di.py", "print(1)\n")
        digest = hashlib.sha256(package.read_bytes()).hexdigest()
        self.review("-Package", package, "-PackageSha256", digest, "-Evaluator", "missing-test-evaluator")
        record, = self.evidence()
        self.assertEqual(record["skill_name"], "traced-template-fill")
        self.assertEqual(record["package_sha256"], digest)
        self.assertNotIn("payload_sha256", record)
        staged = Path(record["target"])
        self.assertEqual({p.relative_to(staged).as_posix() for p in staged.rglob("*") if p.is_file()},
                         {"SKILL.md", "scripts/di.py"})

    def test_deployed_tree_cannot_contain_the_audit_snapshot(self):
        installed = self.root / "doc-integrator"
        installed.mkdir()
        (installed / "SKILL.md").write_text("# Synthetic deployment", encoding="utf-8")
        self.output = installed / "reports"
        run = self.review("-InstalledDirectory", installed)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("outside", run.stderr)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
