import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from dilib.cli import main
from dilib.content import load_model
from dilib.job import Job
from dilib.write_docx import Snapshots


class InputPathsTests(unittest.TestCase):
    def test_default_output_uses_callers_directory_for_external_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs, project = root / "来件", root / "本项目"
            inputs.mkdir()
            project.mkdir()
            target, source = inputs / "目标.txt", inputs / "来源.txt"
            target.write_text("模板", encoding="utf-8")
            source.write_text("文本", encoding="utf-8")
            original = os.getcwd()
            try:
                os.chdir(project)
                job = Job.create(str(target), [str(source)], name="本次")
                other = Job.create(str(target), [str(source)], out_root="另存", name="本次")
            finally:
                os.chdir(original)
            self.assertEqual(Path(job.dir), project / "整合输出" / "本次")
            self.assertEqual(Path(other.dir), project / "另存" / "本次")
            self.assertFalse((inputs / "整合输出").exists())

    def test_identical_bytes_keep_the_selected_path_and_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for part in ("第一批", "第二批"):
                folder = root / part
                folder.mkdir()
                (folder / "记录.txt").write_text("条目,电压\nT1,5", encoding="utf-8")
            first, second = [root / n / "记录.txt" for n in ("第一批", "第二批")]
            cache = str(root / "cache")
            load_model(str(first), cache)
            actual = load_model(str(second), cache)
            self.assertEqual(Path(actual["path"]), second)
            self.assertEqual(Path(actual["work"]), second)
            sheet = second.with_suffix(".csv")
            sheet.write_bytes(second.read_bytes())
            self.assertEqual(load_model(str(sheet), cache)["type"], "xlsx")

    def test_exact_lookup_lists_deep_duplicates_and_asks_for_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i, n in enumerate(("第一批", "第二批", ".agents")):
                p = root / n / "记录" / "复测" / "最新" / "测量.csv"
                p.parent.mkdir(parents=True)
                p.write_text("电压\n" + str(i), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                main(["find", "测量.csv", "--dir", str(root), "--exact"])
            lines = output.getvalue()
            self.assertIn("第一批", lines)
            self.assertIn("第二批", lines)
            self.assertIn("确认", lines)
            self.assertNotIn(".agents", lines)

    def test_snapshot_names_are_unique_after_office_conversion(self):
        paths = [("A/报告.doc", "tmp/a.docx"), ("B/报告.doc", "tmp/b.docx"),
                 ("C/S1_报告.docx", "C/S1_报告.docx"), ("D/独立.docx", "D/独立.docx")]
        models = {"S%d" % i: SimpleNamespace(m={"path":p, "work":w})
                  for i,(p,w) in enumerate(paths, 1)}
        job = SimpleNamespace(data={"sources":[{"alias":a, "path":m.m["path"]} for a,m in models.items()]}, model=models.__getitem__)
        with tempfile.TemporaryDirectory() as tmp:
            job.p = lambda *parts: str(Path(tmp).joinpath(*parts))
            names = Snapshots(job, tmp)
            result = [names.file_for(alias) for alias in reversed(models)]
            self.assertEqual(len({x.casefold() for x in result}), len(models))
            self.assertEqual(names.file_for("S4"), "独立.docx")
