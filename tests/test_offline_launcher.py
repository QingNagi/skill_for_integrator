"""Exercise the Windows offline shell without touching installed skills.

From the source tree, set DI_TEST_EMBEDDED_PYTHON to the directory containing
the kit's python.exe, then run python tests/test_offline_launcher.py.
"""

import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / "tools"))
from build_win64_agent_kit import render_launcher, runtime_files

RUNTIME = Path(os.environ.get("DI_TEST_EMBEDDED_PYTHON", SOURCE / "runtime" / "python"))
FAKE_CLI = """import json, os, sys
if sys.argv[1:] in (['--help'], ['doctor']):
    print('test CLI ready')
elif sys.argv[1:] == ['check-parent-env']:
    print('present' if 'DI_TEST_PARENT_ONLY' in os.environ else 'absent')
elif sys.argv[1:] == ['check-user-options']:
    print(os.environ['DI_OCR'] + '/' + os.environ['DI_NO_VENDOR'])
elif sys.argv[1:] == ['entry-path']:
    print(json.dumps(__file__))
else:
    print(json.dumps([os.getcwd(), sys.argv[1:]], ensure_ascii=True))
    sys.exit(17 if 'test-exit' in sys.argv else 0)
"""
FAKE_SMOKE = """import os, time
if os.environ.get('DI_OCR') == 'off' or os.environ.get('DI_NO_VENDOR'):
    raise SystemExit(8)
with open(os.environ['DI_TEST_INSTALL_LOG'], 'a', encoding='utf-8') as log:
    log.write('smoke\\n')
time.sleep(0.35)
if os.environ.get('DI_TEST_FAIL_SMOKE'):
    raise SystemExit(9)
if os.environ.get('DI_TEST_STAGE_LOCK'):
    import subprocess, sys
    from pathlib import Path
    ready = Path(os.environ['DI_TEST_STAGE_LOCK'])
    lock = "import sys,time; f=open(sys.argv[1],'rb'); open(sys.argv[2],'w').close(); time.sleep(0.8)"
    subprocess.Popen([sys.executable, '-B', '-c', lock, __file__, str(ready)],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 5
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    if not ready.exists():
        raise SystemExit(10)
"""


@unittest.skipUnless(os.name == "nt" and (RUNTIME / "python.exe").is_file(),
                     "Requires Windows and embedded kit Python (DI_TEST_EMBEDDED_PYTHON)")
class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="di offline ")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.project = self.base / "测试 project"
        self.project.mkdir()
        self.kit = self.project / ".agents" / "skills" / "traced-template-fill"
        self.kit.mkdir(parents=True)
        shutil.copy2(SOURCE / "tools/offline/install.ps1", self.kit / "install.ps1")
        self.archive = self.kit / "payload.zip"
        with zipfile.ZipFile(self.archive, "w") as bundle:
            for file in ["python.exe", "python314.dll", "python314.zip", "python314._pth",
                         "vcruntime140.dll", "vcruntime140_1.dll", "zlib1.dll", "_sha2.pyd"]:
                if (RUNTIME / file).is_file():
                    bundle.write(RUNTIME / file, "traced-template-fill/runtime/python/" + file)
            for name, contents in {
                "scripts/di.py": FAKE_CLI, "scripts/dilib/cli.py": "# synthetic",
                "scripts/dilib/draft_review.py": "# synthetic",
                "scripts/dilib/office.py": "# synthetic office backend",
                "scripts/offline_check.py": FAKE_SMOKE, "SKILL.md": "test",
                "assets/offline-ocr.png": "test",
                "runtime/tessdata/eng.traineddata": "test",
                "runtime/tessdata/chi_sim.traineddata": "test",
                "vendor/wheels.lock.json": "{}", "vendor/ocr.lock.json": "{}",
                "runtime/ocr/tesseract.exe": "test",
                "vendor/wheels/win_amd64/pypdfium2-test.whl": "test",
                "vendor/py/pypdf-6.21.4-py3-none-any.whl": "test",
                "vendor/py/typing_extensions-4.16.0-py3-none-any.whl": "test",
                "rules/learned.json": "{}", "rules/test-data.json": "{}",
            }.items():
                bundle.writestr("traced-template-fill/" + name, contents)
            template = (SOURCE / "tools/offline/run.ps1").read_text(encoding="utf-8")
            (self.kit / "run.ps1").write_text(render_launcher(template, bundle.namelist()), encoding="utf-8-sig")
        self.digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        (self.kit / "payload.sha256").write_bytes((self.digest + "\r\n").encode("ascii"))
        with zipfile.ZipFile(self.archive) as archive:
            required = runtime_files(archive.namelist()) + ["runtime/tessdata/eng.traineddata"]
        template = (SOURCE / "tools/offline/run.cmd").read_text("ascii")
        (self.kit / "run.cmd").write_text(template.replace("@PAYLOAD_SHA256@", self.digest), encoding="ascii")
        (self.kit / "runtime-files.txt").write_text("\n".join(rel.replace("/", "\\") for rel in required), encoding="ascii")
        shutil.copyfile(SOURCE / "tools/offline/cmd_install.py", self.kit / "cmd_install.py")
        self.cache = self.base / "local-data"
        self.target = self.cache / "traced-template-fill" / "offline" / self.digest[:24]
        self.log = self.base / "smoke.txt"
        self.driver = self.base / "driver.ps1"
        self.driver.write_text(
            "$ErrorActionPreference = 'Stop'\n"
            "$inArgs = [IO.File]::ReadAllText($env:DI_TEST_ARGS) | ConvertFrom-Json\n"
            "& $env:DI_TEST_LAUNCH @inArgs\nexit $LASTEXITCODE\n", encoding="ascii")
        self.values = ["run", "-t", "中文 路径", "-s", "quoted \"value\"", "", "a b\\", "--task", "x; $y"]
        inputs = self.base / "args.json"
        inputs.write_text(json.dumps(self.values, ensure_ascii=False), encoding="utf-8")
        marker = patch.dict(os.environ, DI_TEST_PARENT_ONLY="synthetic-marker")
        marker.start()
        self.addCleanup(marker.stop)
        self.env = {key: os.environ[key] for key in
                    ("SystemRoot", "WINDIR", "ComSpec", "PATHEXT", "OS",
                     "ProgramFiles", "ProgramFiles(x86)", "USERPROFILE", "APPDATA")
                    if key in os.environ}
        system32 = Path(self.env["SystemRoot"]) / "System32"
        self.env.update(PATH=os.pathsep.join(map(str, (system32, system32 / "WindowsPowerShell/v1.0"))),
                        TEMP=str(self.base), TMP=str(self.base), LOCALAPPDATA=str(self.cache),
                        DI_TEST_INSTALL_LOG=str(self.log), DI_TEST_LAUNCH=str(self.kit / "run.ps1"),
                        DI_TEST_ARGS=str(inputs))

    def call(self, shell="powershell.exe", script=None, extra_args=(), **env):
        return subprocess.run([shutil.which(shell) or shell, "-NoProfile", "-File",
                               str(script or self.driver), *extra_args],
                              cwd=self.project, env=dict(self.env, **env),
                              capture_output=True, timeout=90)

    def verify_output(self, result, values=None):
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        self.assertEqual(json.loads(result.stdout.decode("utf-8-sig").strip()),
                         [str(self.project), self.values if values is None else values])

    def call_cmd(self, *args, **env):
        return subprocess.run([str(self.kit / "run.cmd"), *args], cwd=self.project,
                              env=dict(self.env, **env), capture_output=True, timeout=90)

    def test_two_cmd_processes_share_first_install_and_leave_existing_directory_alone(self):
        reserved = self.target.parent / '.cmd-1-1-1'
        reserved.mkdir(parents=True)
        sentinel = reserved / 'keep.txt'
        sentinel.write_text('keep', encoding='ascii')
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(self.call_cmd, 'run', value, RANDOM='1') for value in ('first', 'second')]
            for value, task in zip(('first', 'second'), jobs):
                self.verify_output(task.result(), ['run', value])
        self.assertEqual(self.log.read_text().splitlines(), ['smoke'])
        self.assertEqual(sentinel.read_text(), 'keep')

    def test_cmd_and_powershell_share_installation_and_repair(self):
        args = ["run", "中文 文件", "test-exit"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            cmd = pool.submit(self.call_cmd, *args)
            ps = pool.submit(self.call)
            self.verify_output(ps.result())
            invoked = cmd.result()
        self.assertEqual(invoked.returncode, 17, invoked.stderr)
        self.assertEqual(json.loads(invoked.stdout), [str(self.project), args])
        self.assertEqual(self.log.read_text().splitlines(), ["smoke"])
        (self.target / "rules/test-data.json").write_bytes(b"")
        rules = self.target / "rules/learned.json"
        rules.write_text("local")
        self.assertEqual(self.call_cmd("--help").returncode, 0)
        self.assertEqual(rules.read_text(), "local")
        self.assertEqual(self.log.read_text().splitlines(), ["smoke", "smoke"])
        self.archive.unlink()
        self.assertEqual(self.call_cmd("--help").returncode, 0)
        self.assertEqual(self.log.read_text().splitlines(), ["smoke", "smoke"])

    def test_cmd_rejects_damaged_payload_in_workspace_cache(self):
        with self.archive.open("ab") as stream:
            stream.write(b"changed")
        result = self.call_cmd("--help", DI_CACHE_DIR=str(self.project / "local cache"))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())
        self.assertFalse(self.cache.exists())
        self.assertFalse(list((self.project / "local cache").rglob(".offline-payload.sha256")))

    def test_child_receives_only_launcher_environment(self):
        Path(self.env["DI_TEST_ARGS"]).write_text('["check-parent-env"]')
        self.assertIn("DI_TEST_PARENT_ONLY", os.environ)
        result = self.call()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), b"absent")

    def test_first_use_checks_bundle_and_keeps_user_options(self):
        Path(self.env["DI_TEST_ARGS"]).write_text('["check-user-options"]')
        result = self.call(DI_OCR="off", DI_NO_VENDOR="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), b"off/1")

    def test_first_use_with_minimal_windows_environment(self):
        self.env.pop("OS", None)
        self.env.pop("PATHEXT", None)
        self.verify_output(self.call())

    def test_repair_cleanup_waits_for_transient_file_handle(self):
        self.verify_output(self.call())
        library = self.target / "scripts/dilib/office.py"
        library.unlink()
        marker = self.base / "file-opened"
        result = self.call(DI_TEST_STAGE_LOCK=str(marker))
        self.assertTrue(marker.is_file(), result.stderr)
        self.verify_output(result)
        self.assertTrue(library.is_file())
        self.assertEqual(list(self.target.parent.glob(".di-*")), [])

    def test_project_repairs_require_explicit_selection(self):
        self.verify_output(self.call())
        deploy = self.call(script=self.kit / "install.ps1", extra_args=[
            "-Agent", "Claude", "-ProjectRoot", str(self.project)])
        self.assertEqual(deploy.returncode, 0, deploy.stderr)
        project_skill = self.project / ".claude/skills/traced-template-fill"
        library = project_skill / "scripts/dilib/office.py"
        library.unlink()
        rules = project_skill / "rules/learned.json"
        rules.write_text("kept")
        self.verify_output(self.call())
        self.assertFalse(library.exists())
        repair = self.call(script=self.kit / "install.ps1", extra_args=["-RepairRoot", str(project_skill)])
        self.assertEqual(repair.returncode, 0, repair.stderr)
        self.assertEqual(library.read_text(), "# synthetic office backend")
        self.assertEqual(rules.read_text(), "kept")

    def test_first_use_race_and_warm_path(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(self.call) for _ in range(3)]
            for future in futures:
                self.verify_output(future.result())
        self.assertEqual(self.log.read_text().splitlines(), ["smoke"])
        self.assertFalse((self.kit / ".offline-payload.sha256").exists())
        rules = self.target / "rules" / "learned.json"
        rules.write_text("local edit")
        self.archive.rename(self.kit / "temporarily unavailable.zip")
        (self.kit / "install.ps1").rename(self.kit / "temporarily unavailable.ps1")
        for shell in ["powershell.exe", "pwsh.exe"]:
            if shutil.which(shell):
                self.verify_output(self.call(shell))
        Path(self.env["DI_TEST_ARGS"]).write_text('["test-exit"]')
        self.assertEqual(self.call().returncode, 17)
        self.assertEqual(self.log.read_text().splitlines(), ["smoke"])
        self.assertEqual(rules.read_text(), "local edit")

    def test_existing_project_install_and_explicit_warm_path(self):
        installer = self.kit / "install.ps1"
        options = ["-Agent", "Claude", "-ProjectRoot", str(self.project)]
        first = self.call(script=installer, extra_args=options)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.archive.unlink()
        second = self.call(script=installer, extra_args=options)
        self.assertEqual(second.returncode, 0, second.stderr)
        project_install = self.project / ".claude/skills/traced-template-fill"
        explicit = subprocess.run([project_install / "runtime/python/python.exe", project_install / "scripts/di.py",
                                   *self.values], cwd=self.project, env=self.env, capture_output=True)
        self.verify_output(explicit)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.log.read_text().splitlines(), ["smoke"])

    def test_both_wrappers_launch_only_the_selected_version_cache(self):
        self.verify_output(self.call())
        deploy = self.call(script=self.kit / "install.ps1", extra_args=[
            "-Agent", "Claude", "-ProjectRoot", str(self.project)])
        self.assertEqual(deploy.returncode, 0, deploy.stderr)
        self.archive.unlink()
        Path(self.env["DI_TEST_ARGS"]).write_text('["entry-path"]')
        for result in (self.call(), self.call_cmd('entry-path')):
            self.assertEqual(result.returncode, 0, result.stderr)
            entry = Path(json.loads(result.stdout.decode('utf-8-sig')))
            self.assertTrue(entry.samefile(self.target / "scripts/di.py"))
        self.assertEqual(self.log.read_text().splitlines(), ["smoke", "smoke"])

    def test_failed_first_use_and_missing_model(self):
        failed = self.call(DI_TEST_FAIL_SMOKE="1")
        self.assertNotEqual(failed.returncode, 0)
        self.assertFalse(self.target.exists())
        self.verify_output(self.call())
        model = self.target / "runtime" / "tessdata" / "eng.traineddata"
        model.write_bytes(b"")
        local = self.target / "rules" / "learned.json"
        local.write_text("user")
        self.verify_output(self.call())
        self.assertEqual(model.read_bytes(), b"test")
        self.assertEqual(local.read_text(), "user")
        self.assertEqual(self.log.read_text().splitlines(), ["smoke"] * 3)

        report_module = self.target / "runtime/ocr/tesseract.exe"
        report_module.unlink()
        self.verify_output(self.call())
        self.assertTrue(report_module.is_file())
        self.assertEqual(local.read_text(), "user")
        self.assertEqual(self.log.read_text().splitlines(), ["smoke"] * 4)


if __name__ == "__main__":
    unittest.main()

