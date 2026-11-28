"""Run the discovered source suite and write one UTF-8 stdout log."""
import argparse
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import unittest

REPO = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-ocr", action="store_true", help="Check the installed PDF and bilingual OCR stack")
    args = parser.parse_args()
    # Keep expected negative-test diagnostics and native child output in the same PowerShell log.
    os.dup2(sys.stdout.fileno(), sys.stderr.fileno())
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    os.chdir(REPO)
    commit = os.environ.get("TTF_QA_COMMIT") or os.environ.get("GITHUB_SHA")
    if not commit and shutil.which("git") and (REPO / ".git").exists():
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    if not commit:
        commit = "unknown (source export; set TTF_QA_COMMIT to record its revision)"
    suite = unittest.defaultTestLoader.discover("tests")
    print(f"Python {platform.python_version()} | commit {commit} | discovered {suite.countTestCases()}", flush=True)
    if os.environ.get("TTF_QA_SOURCE_SHA256"):
        print("Source ZIP SHA-256:", os.environ["TTF_QA_SOURCE_SHA256"], flush=True)
    if args.require_ocr:
        sys.path.insert(0, str(REPO / "traced-template-fill/scripts"))
        from dilib import _bootstrap
        _bootstrap.activate_vendor()
        from PIL import Image
        import pdfplumber
        import pypdf
        import pypdfium2
        options = _bootstrap.ocr_hint(probe=True)
        if not options["available"]:
            raise RuntimeError(options["hint"])
        print(f"PDF {pdfplumber.__version__}, pypdf {pypdf.__version__}, Pillow {Image.__version__}, {options['hint']}", flush=True)
    result = unittest.TextTestRunner(stream=sys.stdout, verbosity=1).run(suite)
    for test, reason in result.skipped:
        print(f"SKIP {test.id()} | {reason}")
    print(f"Ran {result.testsRun}, skipped {len(result.skipped)}, failures {len(result.failures)}, errors {len(result.errors)}")
    return int(not result.wasSuccessful())


if __name__ == "__main__":
    sys.exit(main())
