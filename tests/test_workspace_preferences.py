import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "traced-template-fill" / "scripts"))
from dilib import cleanup, cli, preferences
from dilib.common import DIError
from dilib.common import write_json
from dilib.content import load_model
from dilib.job import Job


class WorkspaceTests(unittest.TestCase):
    def test_overlapping_source_arguments_keep_one_alias_per_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / '模板.txt'
            target.write_text('表', encoding='utf8')
            sources = [root / folder / '记录.txt' for folder in ('一组', '二组')]
            for path in sources:
                path.parent.mkdir()
                path.write_text('同名同内容', encoding='utf8')
            job = Job.create(str(target), [str(sources[0]), str(sources[0].parent), str(sources[1])], out_root=tmp)
            self.assertEqual([s['path'] for s in job.data['sources']], list(map(str, sources)))

    def test_project_overrides_user_and_old_bundled_rules(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            job = SimpleNamespace(dir=str(root / "out/task"), data={"workspace": str(root / "proj")})
            with patch.dict(os.environ, {"DI_USER_DATA_DIR": str(root / "user")}):
                write_json(root / "skill/rules/learned.json", {"answers": {"a": 1, "b": 1}})
                write_json(root / "user/learned.json", {"answers": {"b": 2, "c": 2}})
                path = preferences.remember(job, {"c": 3})
                self.assertEqual(path, root / "proj/.doc-integrator/learned.json")
                self.assertEqual(preferences.mappings(root / "skill", job), {"a": 1, "b": 2, "c": 3})

    def test_clean_only_derived_files_and_only_after_apply(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "note.txt"
            source.write_text("original", encoding="utf-8")
            cache = root / "_缓存"
            load_model(str(source), str(cache))
            model, = (cache / "models").glob("*.json")
            other = model.parent / "personal.json"
            other.write_text("{}", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["clean", "--out", str(root)]), 0)
                self.assertTrue(model.exists())
                self.assertEqual(cli.main(["clean", "--out", str(root), "--apply"]), 0)
            self.assertFalse(model.exists())
            self.assertTrue(other.exists())
            self.assertTrue(source.exists())

    def test_default_fuzzy_search_does_not_open_the_desktop(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            work, desktop = root / "project", root / "Desktop"
            work.mkdir()
            desktop.mkdir()
            (desktop / "batch.txt").touch()
            with patch("os.getcwd", return_value=str(work)), patch("os.path.expanduser", return_value=str(root)):
                stream = io.StringIO()
                with contextlib.redirect_stdout(stream):
                    self.assertEqual(cli.main(["find", "batch.txt"]), 0)
                self.assertNotIn(str(desktop), stream.getvalue())

    def test_clean_rejects_a_link_above_the_output_root(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            real = base / "physical"
            derived = real / "out/_缓存/models" / ("a" * 16 + ".json")
            derived.parent.mkdir(parents=True)
            derived.write_text("{}", encoding="ascii")
            alias = base / "alias"
            try:
                alias.symlink_to(real, target_is_directory=True)
            except OSError as exc:
                if os.name != 'nt' or getattr(exc, 'winerror', None) != 1314:
                    raise
                # Standard Windows users can create a directory junction without
                # the privilege for symbolic links. Both are reparse points.
                import _winapi
                _winapi.CreateJunction(str(real), str(alias))
            try:
                with self.assertRaises(DIError):
                    cleanup.cmd_clean(SimpleNamespace(out=alias / "out", apply=True))
                self.assertTrue(derived.exists())
            finally:
                if alias.is_symlink():
                    alias.unlink()
                else:
                    alias.rmdir()
