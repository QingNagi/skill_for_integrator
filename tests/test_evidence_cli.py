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
                    rc = cli.main(["final", second.dir, "--json"])
                self.assertNotEqual(rc, 0)
                self.assertIn("决定或填写规则已变化", output.getvalue())
                book.write_text(book.read_text(encoding="utf-8").replace("12", "14"), encoding="utf-8")
                invoke("plan", third.dir)
                batch(third)
                remap = next(q for q in third.read("questions.json") if q["key"].startswith("td:map:"))
                self.assertFalse(remap["answered"])
            finally:
                os.chdir(previous)
