import csv
import tempfile
import unittest
from pathlib import Path

from test_selfcheck import make_docx
from dilib.common import DIError
from dilib.job import Job


class EvidenceIO(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.job = Job(self.root, data={"workspace": str(self.root), "sources": [], "target": {"path": str(self.root / "T.docx")},
                                       "rules": {"product": "AA1901", "program_version": "V2"}})
        make_docx(self.root / "T.docx")
        self.q = {"id": "Q1", "input_fp": self.job.fingerprint(files_only=True),
                  "key": "td:map:A_B:S1:T0.D0:Data", "topic": "段序", "question": "段序？",
                  "source_label": "A_B", "segment_count": 2, "source_scope": "S1:Data",
                  "parameter_candidates": ["A", "B"], "parameter_units": {"A": "mV", "B": "V"},
                  "options": [{"label": "留空", "effect": {"map": None}},
                              {"label": "A后B", "effect": {"map": ["A", "B"]}}],
                  "affected_cells": [{"target": "T0.R1.C1", "symbol": "A", "sample": "1"}]}

    def test_program_order_uses_explicit_segment_numbers_and_checks_unit(self):
        from dilib import evidence
        path = self.root / "program.csv"
        rows = "来源,标签,段序,测试号,参数,单位\nS1:Data,A_B,2,101,B,V\nS1:Data,A_B,1,100,A,mV\n"
        path.write_text(rows, encoding="utf-8")
        items, meta = evidence.program_answers([self.q], ["Q1"], path)
        self.assertEqual(meta["effects"]["Q1"], {"map": ["A", "B"]})
        self.assertEqual(len(meta["sha256"]), 64)
        self.assertEqual(len(items), 1)
        for old, new in [("A,mV", "A,mA"), ("A_B,1,100", "A_B,2,100"), ("S1:Data", "S2:Data")]:
            path.write_text(rows.replace(old, new), encoding="utf-8")
            with self.assertRaises(DIError):
                evidence.program_answers([self.q], ["Q1"], path)

    def test_csv_bound_to_current_target_and_options(self):
        from dilib import evidence
        path = self.root / "review.csv"
        plan = {"questions": [self.q], "report": {}}
        evidence.export_review_sheet(self.job, plan, path)
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        rows[0]["决定"] = "采用"
        def save():
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        save()
        self.assertEqual(evidence.sheet_answers(self.job, [self.q], path), ["Q1=2"])
        rows[0]["位置"] = "T0.R5.C1"
        save()
        with self.assertRaises(DIError):
            evidence.sheet_answers(self.job, [self.q], path)

    def test_partial_csv_approval_never_resolves_other_blank_decisions(self):
        from dilib import evidence
        self.q["affected_cells"].append({"target": "T0.R2.C1", "symbol": "B", "sample": "1"})
        path = self.root / "review.csv"
        evidence.export_review_sheet(self.job, {"questions": [self.q]}, path)
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        rows[0]["决定"] = "2"
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        with self.assertRaises(DIError):
            evidence.sheet_answers(self.job, [self.q], path)

    def test_full_read_receipt_does_not_change_document_semantics(self):
        from dilib import evidence
        before = evidence.decision_digest(self.job)
        self.job.write("answers.json", {"fullread:S1:H1": {"effect": {"ok": True, "receipt": True}}})
        self.assertEqual(before, evidence.decision_digest(self.job))
        self.job.write("answers.json", {"td:map:X": {"effect": {"map": ["Y"]}}})
        self.assertNotEqual(before, evidence.decision_digest(self.job))

    def test_fact_reference_changes_when_decision_changes(self):
        from dilib import evidence
        rec = {"effect": {"map": ["B", "A"]}, "confirmed_by": "李", "answered_at": "2026-11-28",
               "fp": self.q["input_fp"], "remember": True, "evidence": {"sha256": "a"*64}}
        def save():
            fill = {"source": "S1:Data!B2:B2", "target": "T0.R1.C1", "symbol": "A",
                    "decision_keys": [self.q["key"]], "how": "按回答逐段对应"}
            plan = {"questions": [self.q], "report": {"fills": [fill]}, "rules": {}}
            evidence.save_plan_facts(self.job, plan, {self.q["key"]: rec})
            return fill["fact_ids"][0]
        first = save()
        self.assertEqual(evidence.reusable_answers(self.job, [self.q])[self.q["key"]]["effect"], rec["effect"])
        self.job.data["rules"]["program_version"] = "V3"
        self.assertFalse(evidence.reusable_answers(self.job, [self.q]))
        rec["effect"] = {"map": ["A", "B"]}
        self.assertNotEqual(first, save())
