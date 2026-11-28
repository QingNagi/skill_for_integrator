import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import test_selfcheck  # Set up the skill import path.
from dilib.common import DIError
from dilib import handoff_launcher


class LauncherTests(unittest.TestCase):
    def test_shell_launch_preserves_manifest_argument_and_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "交接 A & 'B %1 !"
            folder.mkdir()
            manifest = folder / "handoff.json"
            manifest.write_text("{}", encoding="utf-8")
            probe = folder / "核对 100% 参数.py"
            probe.write_text("import pathlib,sys\n"
                             "assert sys.argv[1:] == ['deliver', str(pathlib.Path(__file__).with_name('handoff.json'))]\n"
                             "raise SystemExit(17)\n", encoding="utf-8")
            windows = os.name == "nt"
            path = folder / ("出稿.cmd" if windows else "出稿.sh")
            path.write_bytes(handoff_launcher.launcher_text(sys.executable, str(probe), windows).encode("utf-8"))
            if windows:
                command = [os.environ["ComSpec"], "/d", "/c", path.name]
                # The caller does not interpolate the test directory into a command.
                cwd = folder
            else:
                command = ["sh", str(path)]
                cwd = root
            result = subprocess.run(command, cwd=cwd, input=b"\n", capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 17, result.stderr)

    def test_replay_preserves_reviewer_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "handoff.json"
            path.write_text("{}", encoding="utf8")
            first = handoff_launcher.write_launchers(path)
            self.assertEqual(first, handoff_launcher.write_launchers(path))
            launcher = Path(first["preferred"])
            launcher.write_bytes(b"custom reviewer command")
            with self.assertRaises(DIError):
                handoff_launcher.write_launchers(path)
            self.assertEqual(launcher.read_bytes(), b"custom reviewer command")
