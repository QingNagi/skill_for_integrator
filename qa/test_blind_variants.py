import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest


class ClaudeTraceTests(unittest.TestCase):
    def test_status_messages_do_not_count_as_tools_after_the_last_draft(self):
        from run_blind_variants import after_draft
        with tempfile.TemporaryDirectory() as tmp:
            events = [
                {"type": "system", "message": "project skill loaded"},
                {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "draft"}]}},
                {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "draft",
                                                           "content": '{"status":"draft_ready"}'}]}},
                {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "review"}]}}]
            Path(tmp, "turn-01.jsonl").write_text("\n".join(map(json.dumps, events)), encoding="utf-8")
            self.assertEqual(after_draft(Path(tmp)), (2, 1))


@unittest.skipUnless(importlib.util.find_spec("docx") and importlib.util.find_spec("openpyxl"),
                     "Generating independent fixtures requires python-docx and openpyxl")
class BlindVariants(unittest.TestCase):
    def test_batch_evidence_keeps_two_programs_separate_across_rounds_and_retries(self):
        import subprocess
        import sys
        from blind_variants import generate, metrics, read_key
        local = Path(__file__).resolve().parents[1] / "traced-template-fill/scripts/di.py"
        entry = Path(os.environ.get("DI_TEST_BATCH_ENTRY", local))
        command = [sys.executable, "-X", "utf8", str(entry)] if entry.suffix == ".py" else [str(entry)]
        def run(*args):
            cmd = subprocess.run([*command, "batch", *map(str, args), "--json"], capture_output=True,
                                 encoding="utf-8", env=dict(os.environ, DI_UTF8="1"))
            self.assertEqual(cmd.returncode, 0, cmd.stdout + cmd.stderr)
            return json.loads(cmd.stdout)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            public, vault = root / "programs with spaces", root / "keys"
            generate(public, vault, 88726, 2)
            cases = sorted(public.iterdir())
            listing = public / "list.json"
            listing.write_text(json.dumps([{"target": c.name + "/模板.docx", "sources": [c.name + "/详细规范.docx",
                c.name + "/测量表.xlsx"], "task": "只取中间电压，重复值写范围"} for c in cases]), encoding="utf-8")
            bindings = public / "evidence.json"
            bindings.write_text(json.dumps({"J%d" % i: c.name + "/本批测试程序清单.csv"
                                             for i, c in enumerate(cases, 1)}), encoding="utf-8")
            step = run("run", listing, "--out", root / "output", "--draft")
            self.assertEqual(step["status"], "needs_answers")
            batch = step["batch"]
            self.assertEqual(len(step["all_questions"]), 2)
            for _ in range(4):
                if step["status"] == "draft_ready":
                    break
                answers = [q["id"] + ("=2" if q["header"] in ("来源核对", "实测记录核对") else "=1")
                           for q in step["all_questions"]]
                submission = ("answer", batch, *answers, "--evidence", bindings, "--user-quote",
                              "合成记录已逐份核对，按相同样品号，缺少证据或量纲冲突的位置留空。", "--draft")
                step = run(*submission)
                self.assertEqual(run(*submission), step)
            self.assertEqual(step["summary"]["drafts"], 2)
            handoff = Path(step["delivery_report"]).read_text("utf-8")
            self.assertIn("实测已填 36 格、留空 30 格", handoff)
            self.assertTrue(all(j["review_summary"]["hold_reasons"] for j in step["jobs"]))
            for j, c in zip(step["jobs"], cases):
                key = read_key(vault / (c.name + ".json"), vault / (c.name + ".sha256"))
                scored = metrics(key, Path(j["draft"]))
                self.assertEqual((scored["correct_fills"], scored["wrong_fills"]), (18, 0))
                self.assertEqual(len(list(Path(j["job"], "草稿").glob("*.docx"))), 1)
            changed = cases[1] / "本批测试程序清单.csv"
            changed.write_bytes(changed.read_bytes() + b"\n")
            stale = run("run", listing, "--out", root / "output", "--draft")
            self.assertEqual([j["status"] for j in stale["jobs"]], ["draft_ready", "blocked"])
            self.assertEqual(stale["jobs"][1]["next_action"], "review_evidence")
            renewed = run("answer", batch, "--evidence", bindings, "--draft")
            self.assertEqual(renewed["status"], "draft_ready")
            self.assertEqual(renewed["jobs"][0]["draft"], step["jobs"][0]["draft"])
            self.assertNotEqual(renewed["jobs"][1]["draft"], step["jobs"][1]["draft"])

    def test_sealed_blank_and_wrong_fills_have_separate_metrics(self):
        from blind_variants import generate, metrics, seal, read_key
        from docx import Document
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            public, vault = root / "public", root / "vault"
            generate(public, vault, 41, 2)
            self.assertFalse(any("key" in p.name or "seed" in p.name for p in public.rglob("*")))
            case = public / "case-01"
            key = read_key(vault / "case-01.json", vault / "case-01.sha256")
            blank = metrics(key, case / "模板.docx")
            self.assertEqual((blank["wrong_fills"], blank["correct_fills"], blank["fill_rate"]), (0, 0, 0))
            self.assertGreater(blank["missed_fills"], 0)
            self.assertGreater(blank["correct_holds"], 0)
            document = Document(case / "模板.docx")
            for cell in key["cells"]:
                document.tables[0].cell(cell["row"], cell["column"]).text = cell["value"] or "99"
            draft = root / "draft.docx"
            document.save(draft)
            result = metrics(key, draft)
            self.assertEqual(result["fill_rate"], 1)
            self.assertEqual(result["wrong_fills"], blank["correct_holds"])
            seal(draft, root / "external.json", key["case"])
            external = read_key(root / "external.json", root / "external.sha256")
            self.assertEqual(metrics(external, draft)["wrong_fills"], 0)
            (root / "external.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_key(root / "external.json", root / "external.sha256")
