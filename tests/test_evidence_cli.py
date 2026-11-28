"""检查从模板、批次确认和程序清单一直到复用的实际 CLI 流程。"""
import contextlib
import csv
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from test_selfcheck import doc_xml, make_docx
from dilib import cli, evidence
from dilib.job import Job


class EvidenceCLI(unittest.TestCase):
    def test_manifest_before_questions_converts_and_replays_each_original_range(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target, readings, declarations = (root / name for name in ("记录.docx", "读数.csv", "单位清单.csv"))
            rows = [["符号", "最大值", "单位", "1#"], ["A", "8", "V", ""], ["B", "7", "µA", ""],
                    ["C", "7", "V", ""], ["D", "7", "V", ""]]
            body = "<w:tbl><w:tblGrid>" + "<w:gridCol/>" * 4 + "</w:tblGrid>" + "".join(
                "<w:tr>" + "".join("<w:tc><w:p><w:r><w:t>%s</w:t></w:r></w:p></w:tc>" % v for v in row)
                + "</w:tr>" for row in rows) + "</w:tbl>"
            make_docx(target, {"word/document.xml": doc_xml(body)})
            readings.write_text("label,unit,1\nA,mV,2523\nB,,2891\nC,V,3\nD,V,4\n", "utf8")
            declarations.write_text("来源,测试号,标签,参数,单位\nS1:Sheet1,12,B,B,nA\n", "utf8")
            job = Job.create(target, [str(readings)], task="将读数表填进记录，多值填范围", out_root=str(root), name="job")
            questions = cli.do_plan(job, quiet=True)["questions"]
            batch = next(q["id"] for q in questions if q["key"] == "src:verify:S1")
            generated = Path(job.dir, "推导段序清单.csv")
            generated.write_bytes(declarations.read_bytes())
            provisional = root / "未审核清单.csv"
            provisional.write_text("来源,测试号,标签,参数,单位,备注\nS1:Sheet1,12,B,B,nA,推导，待审核\n", "utf8")
            for path in (generated, provisional):
                with self.subTest(path=path.name), contextlib.redirect_stdout(io.StringIO()) as output:
                    rc = cli.main(["answer", job.dir, "--evidence", str(path), "--json"])
                    self.assertEqual(rc, 0)
                    self.assertEqual(json.loads(output.getvalue())["status"], "blocked")
                    self.assertFalse(job.read("answers.json", {}))
            # A legacy session may already have registered a generated manifest.
            _, old = evidence.program_answers(questions, [], declarations, job)
            old.update(path=str(generated), fp=job.fingerprint(), rule_sha256=evidence.rule_pack(job)["sha256"])
            job.write("answers.json", {"_program_manifest": old})
            self.assertIn("_program_manifest", cli.do_plan(job, quiet=True)["stale_answers"])
            job.write("answers.json", {})
            rejected = io.StringIO()
            with contextlib.redirect_stdout(rejected):
                rc = cli.main(["answer", job.dir, batch + "=2", "--user-quote", "将读数表填进记录", "--draft", "--json"])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(rejected.getvalue())["next_action"], "ask_user")
            self.assertFalse(job.read("answers.json", {}))
            self.assertFalse(job.drafts())
            reply = io.StringIO()
            with contextlib.redirect_stdout(reply):
                rc = cli.main(["answer", job.dir, batch + "=2", "--user-quote", "已核对本批", "--evidence",
                               str(declarations), "--json"])
            self.assertEqual(rc, 0, reply.getvalue())
            self.assertEqual(json.loads(reply.getvalue())["status"], "planned", reply.getvalue())
            plan = job.read("plan.json")
            fills = {f["symbol"]: f for f in plan["report"]["fills"] if "unit_conversion" in f}
            self.assertEqual({k: f["value"] for k, f in fills.items()}, {"A":"2.52", "B":"2.89"})
            from dilib import verify
            self.assertFalse(verify.verify_draft(job, plan)["items"])
            # Alter the recorded source unit without changing the spreadsheet.
            fills["A"]["unit_conversion"]["sources"][0][0] = "V"
            self.assertTrue(verify.verify_draft(job, plan)["items"])
            self.assertTrue(any(r["type"] == "程序清单声明" for r in job.read("facts.json")["facts"]))
            # This job's first snapshot applies only to its original measurement file and rules.
            readings.write_text(readings.read_text("utf8").replace("2523", "2530"), "utf8")
            plan = cli.do_plan(Job(job.dir), quiet=True)
            self.assertIn("_program_manifest", plan["stale_answers"])
            self.assertFalse(any(r["type"] == "程序清单声明" for r in plan["report"]["facts"]))

    def test_batch_cannot_be_reused_and_changed_shared_dictionary_invalidates_draft(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            previous = os.getcwd()
            os.chdir(root)
            try:
                table_rows = [["符号", "最大值", "1#"], ["A", "20", ""], ["B", "20", ""],
                              ["C", "20", ""], ["D", "20", ""]]
                body = "<w:tbl><w:tblGrid>" + "<w:gridCol/>" * 3 + "</w:tblGrid>" + "".join(
                    "<w:tr>" + "".join("<w:tc><w:p><w:r><w:t>%s</w:t></w:r></w:p></w:tc>" % v for v in row)
                    + "</w:tr>" for row in table_rows) + "</w:tbl>"
                target = root / "记录.docx"
                make_docx(target, {"word/document.xml": doc_xml(body)})
                book = root / "测试.csv"
                book.write_text("label,1\nA_B_C_Vnorm,12\nA_B_C_Vnorm,8\nD_Vnorm,10\n", encoding="utf-8")
                rules = root / "规则.json"
                rules.write_text(json.dumps({"cond_keep": ["Vnorm"], "program_version": "2.1", "product": "TEST01"}),
                                 encoding="utf-8")
                manifest = root / "程序.csv"
                manifest.write_text("来源,标签,段序,测试号,参数,单位\nS1:Sheet1,A_B_C,1,110,B,\n"
                                    "S1:Sheet1,A_B_C,2,120,A,\n", encoding="utf-8")

                def invoke(*args):
                    with contextlib.redirect_stdout(io.StringIO()) as output:
                        rc = cli.main(list(map(str, args)))
                    self.assertEqual(rc, 0, output.getvalue())

                def new(name):
                    invoke("run", "-t", target, "-s", book, "--test-rules", rules, "--name", name, "--out", root)
                    return Job(root / name)

                def batch(job):
                    q = next(q for q in job.read("questions.json") if q["key"].startswith("src:verify:"))
                    self.assertEqual(q["impact_count"], 4)
                    self.assertFalse(any("!" in f["source"] for f in job.read("plan.json")["report"]["fills"]))
                    sheet = Path(job.dir, "待核清单.csv")
                    invoke("questions", job.dir, "--csv", sheet)
                    with sheet.open(encoding="utf-8-sig", newline="") as stream:
                        decisions = list(csv.DictReader(stream))
                    for decision in decisions:
                        if decision["问题"] == q["id"]:
                            decision.update({"决定": "2", "备注": "核过登记本"})
                    with sheet.open("w", encoding="utf-8-sig", newline="") as stream:
                        writer = csv.DictWriter(stream, fieldnames=list(decisions[0]))
                        writer.writeheader()
                        writer.writerows(decisions)
                    invoke("answer", job.dir, "--from-sheet", sheet, "--reviewer", "甲", "--user-quote", "核过本批原始记录")
                    proof = job.read("answers.json")[q["key"]]["evidence"]
                    self.assertEqual(proof["sha256"], hashlib.sha256(sheet.read_bytes()).hexdigest())
                    self.assertEqual(len(proof["notes"]), 4)

                first = new("第一批")
                batch(first)
                q = next(q for q in first.read("questions.json") if q["key"].startswith("td:map:"))
                self.assertEqual(q["impact_count"], 3)
                target_cell = next(c["target"] for c in q["affected_cells"] if c["symbol"] == "A")
                invoke("set", first.dir, target_cell, "999", "--src", "S1:Sheet1!B2:B2")
                invoke("plan", first.dir)
                self.assertFalse(any(op.get("text") == "999" for op in first.read("plan.json")["ops"]))
                invoke("answer", first.dir, q["id"] + "=2")
                current = next(item for item in first.read("questions.json") if item["key"] == q["key"])
                self.assertFalse(current["answered"], "承诺提供清单时应保留问题")
                invoke("answer", first.dir, q["id"], "--evidence", manifest, "--remember",
                       "--reviewer", "甲", "--user-quote", "此版本程序的段序已核")
                second = new("第二批")
                batch(second)
                plan = second.read("plan.json")
                measured = [f for f in plan["report"]["fills"] if "!" in f["source"]]
                self.assertEqual({f["symbol"]: f["value"] for f in measured}, {"A": "8", "B": "12", "D": "10"})
                facts = {f["id"]: f for f in second.read("facts.json")["facts"]}
                fact_types = {facts[k]["type"] for k in measured[0]["fact_ids"]}
                self.assertTrue(set(evidence.TYPES.values()) <= fact_types)
                self.assertTrue(any(f.get("evidence", {}).get("sha256") for f in facts.values()
                                    if isinstance(f.get("evidence"), dict)))
                invoke("draft", second.dir)
                self.assertTrue(second.drafts())
                before = evidence.decision_digest(second)
                manifest.write_text(manifest.read_text(encoding="utf-8").replace("110,B", "110,A").replace("120,A", "120,B"),
                                    encoding="utf-8")
                third = new("第三批")
                batch(third)
                q = next(q for q in third.read("questions.json") if q["key"].startswith("td:map:"))
                invoke("answer", third.dir, q["id"], "--evidence", manifest, "--remember",
                       "--reviewer", "乙", "--user-quote", "修正本程序段序")
                self.assertNotEqual(before, evidence.decision_digest(second))
                current = evidence.decision_digest(second)
                invoke("plan", first.dir)
                self.assertEqual(current, evidence.decision_digest(second), "阅读早期批次不能把共享段序改回旧版")
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    rc = cli.main(["final", second.dir, "--json", "--strict-exit"])
                self.assertNotEqual(rc, 0)
                self.assertIn("决定或填写规则已变化", output.getvalue())
                book.write_text(book.read_text(encoding="utf-8").replace("12", "14"), encoding="utf-8")
                invoke("plan", third.dir)
                batch(third)
                remap = next(q for q in third.read("questions.json") if q["key"].startswith("td:map:"))
                self.assertFalse(remap["answered"])
            finally:
                os.chdir(previous)
