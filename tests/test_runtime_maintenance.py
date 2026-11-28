"""Private offline installations only. All pruning tests use synthetic temporary caches."""
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_selfcheck  # configure the same in-tree import path as the other CLI tests
from dilib import cli, runtime_maintenance as rm


class RuntimeMaintenance(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cache = Path(tmp.name) / "cache"
        self.cache.mkdir()
        (self.cache / ".traced-template-fill.install.lock").touch()

    def install(self, version, native=None, with_cli=False):
        digest = ("%02x" % version) * 32
        target = self.cache / digest[:24]
        stage = self.cache / ".stage"
        stage.mkdir()
        for name in ("scripts/di.py", "SKILL.md", "rules/learned.json", "runtime/python/python.exe",
                     "runtime/ocr/tesseract.exe", "runtime/python/bin/python3", "runtime/ocr/tesseract"):
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"test")
        if native:
            shutil.copy2(native, stage / ("runtime/python/python.exe" if os.name == "nt" else "runtime/python/bin/python3"))
        if with_cli:
            scripts = Path(rm.__file__).resolve().parents[1]
            shutil.copytree(scripts, stage / "scripts", dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
        rm.record_install(stage, target, digest)
        (stage / rm.MARKER).write_text(digest, "ascii")
        stage.rename(target)
        return target

    def test_preview_apply_preserve_current_previous_custom_busy_and_legacy(self):
        clean, custom, busy, legacy, previous, current = [self.install(i) for i in range(1, 7)]
        (legacy / rm.RECEIPT).unlink()
        (custom / "rules/learned.json").write_bytes(b"private selection")
        unknown = self.cache / "user-folder"
        unknown.mkdir()
        with rm.using_runtime(busy):
            preview = rm.prune(current)
            self.assertEqual(preview["eligible"], [clean.name], preview)
            self.assertTrue(clean.is_dir())
            applied = rm.prune(current, apply=True)
            self.assertEqual(applied["removed"], [clean.name], applied)
        self.assertFalse(clean.exists())
        self.assertTrue(all(p.is_dir() for p in (custom, busy, legacy, previous, current, unknown)))

    def test_changed_or_extra_files_and_untrusted_marker_are_preserved(self):
        modified, extra, wrong, older, current = [self.install(i) for i in range(1, 6)]
        (modified / "SKILL.md").write_text("local")
        (extra / "my-notes.txt").write_text("mine")
        (wrong / rm.MARKER).write_text("F" * 64)
        applied = rm.prune(current, apply=True)
        self.assertFalse(applied["removed"])
        self.assertTrue(all(p.is_dir() for p in (modified, extra, wrong, older, current)))
        self.assertFalse(rm.prune(self.cache, apply=True)["removed"])

    def test_symlink_cannot_redirect_a_version_to_another_directory(self):
        clean, prev, current = [self.install(i) for i in range(1, 4)]
        keep = self.cache.parent / "keep"
        clean.rename(keep)
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(keep), str(clean))
        else:
            clean.symlink_to(keep, target_is_directory=True)
        try:
            self.assertFalse(rm.prune(current, apply=True)["removed"])
            self.assertTrue((keep / "SKILL.md").is_file())
        finally:
            if os.name == "nt":
                clean.rmdir()
            else:
                clean.unlink()

    def test_direct_use_of_an_old_native_executable_keeps_its_whole_installation(self):
        if os.name == "nt":
            active = self.install(1, native=os.environ["COMSPEC"])
            args = [active / "runtime/python/python.exe", "/d", "/q"]
        else:
            active = self.install(1, native=shutil.which("sleep"))
            args = [active / "runtime/python/bin/python3", "30"]
        self.install(2)
        current = self.install(3)
        child = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            self.assertIsNone(child.poll())
            self.assertFalse(rm.prune(current, apply=True)["removed"])
            self.assertTrue((active / "rules/learned.json").is_file())
        finally:
            child.terminate()
            child.communicate(timeout=10)
        self.assertEqual(rm.prune(current, apply=True)["removed"], [active.name])

    def test_noexec_diagnosis_handles_nonexistent_cache_directory(self):
        with patch.object(rm, "_mount_is_noexec", return_value=True):
            self.assertIn("DI_RUNTIME_DIR", rm.execution_hint(self.cache / "new"))
            self.assertIn("XDG_DATA_HOME", rm.execution_hint(self.cache / "new"))

    def test_source_cli_prune_refuses_to_infer_current_version_from_an_arbitrary_cache(self):
        old = self.install(1)
        with patch.dict(os.environ, DI_RUNTIME_DIR=str(self.cache)), contextlib.redirect_stdout(io.StringIO()) as out:
            result = cli.main(["doctor", "--prune", "--apply"])
        self.assertEqual(result, 0)
        self.assertIn("离线", out.getvalue())
        self.assertTrue(old.exists())

    def test_installed_cli_previews_then_applies_only_to_the_verified_old_version(self):
        old, previous = self.install(1), self.install(2)
        current = self.install(3, with_cli=True)
        command = [sys.executable, "-B", "-X", "utf8", current / "scripts/di.py", "doctor", "--prune"]
        env = dict(os.environ, DI_UTF8="1")
        preview = subprocess.run(command, check=True, capture_output=True, encoding="utf-8", env=env)
        self.assertIn("可清理 " + old.name, preview.stdout)
        self.assertTrue(old.is_dir())
        result = subprocess.run(command + ["--apply"], check=True, capture_output=True, encoding="utf-8", env=env)
        self.assertIn("已清理 " + old.name, result.stdout)
        self.assertFalse(old.exists())
        self.assertTrue(previous.exists() and current.exists())
