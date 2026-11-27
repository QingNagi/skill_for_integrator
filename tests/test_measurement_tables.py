import unittest

from test_selfcheck import make_docx
from dilib.content import Model
from dilib.planner import Planner
from dilib import verify, testdata


def table(alias, rows):
    cells = [{"r": r, "c": c, "text": text} for r, row in enumerate(rows) for c, text in enumerate(row)]
    block = {"t": "tbl", "id": "T0", "nrows": len(rows), "ncols": len(rows[0]), "hdr": 1, "cells": cells, "cap": alias}
    return Model({"type": "docx", "name": alias, "blocks": [block]}, alias)


class SpecSelectionTests(unittest.TestCase):
    def test_matching_another_document_model_does_not_verify_selected_model(self):
        p = object.__new__(Planner)
        p.task, p.questions, p.report = "", [], {"warnings": []}
        p.src = {a: table(model, [["x"]]) for a, model in [("S1", "AA1901"), ("S2", "BB1902")]}
        p.answers = {"field:产品型号": {"effect": {"alias": "S2", "value": "BB1902"}}}
        book = Model({"name": "AA1901.xlsx", "type": "xlsx"}, "S3")
        p.verify_data_source("S3", book, {"rows": [["item", 1]]})
        self.assertTrue(p.report.get("unverified_sources"))
        self.assertIn("BB1902", p.questions[0]["question"])

    def test_verification_rejects_value_written_under_another_sample_header(self):
        target = table("T", [["项目", "1#", "2#"], ["R", "", ""]])
        book = Model({"type": "xlsx", "sheets": [{"name": "S", "rows": [["item", 1, 2], ["R", 3, 3]]}]}, "S1")
        fill = {"target": "T0.R1.C2", "source": "S1:S!B2", "value": "3", "source_sample": "1",
                "target_sample": "1", "target_header": [0, 0], "header_row": 1, "limits": [None, "4"]}
        checked = {"checked": 0, "items": []}
        verify._testdata({"report": {"fills": [fill]}}, {"S1": book}, testdata.DEFAULT_RULES, checked, target)
        self.assertTrue(checked["items"])

    def plan(self, filled=False, effect=None):
        p = object.__new__(Planner)
        p.task, p.n = "", 0
        p.questions, p.ops, p.stale, p.summary, p.missing_regions = [], [], [], [], []
        p.row_shift = {}
        p.answers = {} if effect is None else {"tbl:src:T0.D0": effect}
        p.report = {"fills": [], "unfilled": [], "warnings": []}
        header = ["参数名称", "符号", "最大值", "单位"]
        p.src = {a: table(a, [header, ["速度", "S", x, "V"], ["压力", "P", x, "V"]])
                 for a, x in (("S1", "7"), ("S2", "9"))}
        target_rows = [["速度", "S", "8", "V"], ["压力", "P", "8", "V"]] if filled else [[""] * 4] * 2
        p.T = table("T", [header] + target_rows)
        cols = [{"c": i, "path": [v]} for i, v in enumerate(header)]
        region = {"id": "T0.D0", "rows": [1, 2], "header": [0, 0], "cols": cols, "filled": filled}
        p.plan_region({"id": "T0"}, region)
        return p

    def test_tied_sources_require_choice_for_blank_and_prefilled_tables(self):
        for filled in (False, True):
            p = self.plan(filled)
            self.assertTrue(any(q["key"] == "tbl:src:T0.D0" for q in p.questions))
            self.assertFalse(any(op["op"] == "set_cell" for op in p.ops))
            self.assertFalse(any(op.get("links") for op in p.ops))

    def test_skip_does_not_use_first_table(self):
        p = self.plan(effect={"skip": True})
        self.assertFalse(any(op["op"] == "set_cell" for op in p.ops))

    def test_explicit_second_source_controls_fill_and_existing_value_review(self):
        p = self.plan(effect={"table": "S2:T0"})
        self.assertTrue(any(op.get("cell") == "T0.R1.C2" and op["runs"] == [["9", ""]] for op in p.ops))
        p = self.plan(filled=True, effect={"table": "S2:T0"})
        self.assertTrue(any("9" in item.get("why", "") for item in p.stale))
        self.assertFalse(any(op["op"] == "set_cell" for op in p.ops))
