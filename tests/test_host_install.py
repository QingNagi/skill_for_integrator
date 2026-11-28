"""Exercise the platform installation entry point in a disposable project."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / "tools"
NAME = "traced-template-fill"
sys.path.insert(0, str(TOOLS / "offline"))
import deploy


class HostInstallTests(unittest.TestCase):
    def test_install_twice_with_legacy_rules_and_shared_claude_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project with spaces"
            legacy = project / ".claude/skills/doc-integrator"
            legacy.mkdir(parents=True)
            (legacy / "SKILL.md").write_text("---\nname: doc-integrator\n---\n", "utf-8")
            (legacy / "custom.json").write_text('{"saved":true}', "utf-8")
            other = root / "unrelated/.claude/skills/doc-integrator"
            other.mkdir(parents=True)
            release = root / "release"
            release.mkdir()
            archive = release / (NAME + "-test-source.zip")
            source = {p: (TOOLS.parent / NAME / p).read_bytes() for p in ["run.cmd", "run.sh"]}
            source.update({"SKILL.md": b"---\nname: traced-template-fill\n---\n",
                           "scripts/di.py": b"import sys\nassert sys.argv[1:] == ['doctor']\nprint('doctor ok')\n"})
            with zipfile.ZipFile(archive, "w") as z:
                for rel, body in source.items():
                    z.writestr(NAME + "/" + rel, body)
            helper = release / "deploy.py"
            shutil.copyfile(TOOLS / "offline/deploy.py", helper)
            shell = "ps1" if os.name == "nt" else "sh"
            script = release / ("install." + shell)
            content = (TOOLS / ("offline/deploy-install." + shell)).read_text("utf-8").replace("@TAG@", "test")
            script.write_bytes(content.encode())
            manifest = release / "SHA256SUMS-test.txt"
            manifest.write_text("".join(hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name + "\n"
                                        for p in (archive, helper, script)), encoding="ascii")
            cmd = (["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                    "-ProjectRoot", str(project), "-SourceOnly"] if os.name == "nt" else
                   ["sh", str(script), "--source-only", "--project-root", str(project)])
            for _ in range(2):
                result = subprocess.run(cmd, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
                self.assertIn(b"doctor ok", result.stdout)
            canonical = project / ".agents/skills" / NAME
            self.assertEqual((project / ".claude/skills" / NAME).resolve(), canonical)
            self.assertEqual({p.relative_to(canonical).as_posix(): p.read_bytes()
                              for p in canonical.rglob("*") if p.is_file()}, source)
            backups = list((project / ".traced-template-fill/backups").rglob("custom.json"))
            self.assertEqual([p.read_text("utf-8") for p in backups], ['{"saved":true}'])
            self.assertTrue(other.is_dir())

    def test_legacy_profile_name_display_and_path_refer_to_ascii_asset(self):
        skill = TOOLS.parent / NAME
        sys.path.insert(0, str(skill / "scripts"))
        from dilib import profile
        builtin = skill / "config/profiles"
        expected = profile.load(str(builtin / "default-excerpt.json"), skill)
        for name in ("默认_文档摘抄提示词", "默认_文档摘抄提示词.json", profile.DEFAULT["name"], expected["name"],
                     builtin / "默认_文档摘抄提示词.json", "default-excerpt"):
            with self.subTest(name=name):
                self.assertEqual(profile.load(name, skill), expected)

    def test_failed_doctor_restores_both_legacy_entry_and_local_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = root / ".claude/skills/doc-integrator"
            legacy.mkdir(parents=True)
            (legacy / "SKILL.md").write_text("---\nname: doc-integrator\n---\n")
            (legacy / "my-rule.json").write_text("custom")
            source = root / "source"
            source.mkdir()
            (source / "SKILL.md").write_text("---\nname: traced-template-fill\n---\n")
            with patch.object(deploy, "doctor", side_effect=ValueError("doctor failed")):
                with self.assertRaisesRegex(ValueError, "doctor failed"):
                    deploy.host_install(root, source, None, "win64")
            self.assertEqual((legacy / "my-rule.json").read_text(), "custom")
            for host in (".agents", ".claude"):
                self.assertFalse(os.path.lexists(root / host / "skills" / NAME))

    def test_linked_host_parent_never_migrates_another_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, other, source = root / "project", root / "other", root / "source"
            for path in (project, other, source):
                path.mkdir()
            (other / "skills").mkdir()
            deploy.link(other, project / ".claude")
            with self.assertRaisesRegex(ValueError, "Linked parent"):
                deploy.host_install(project, source, None, "win64")
            self.assertEqual(list((other / "skills").iterdir()), [])
