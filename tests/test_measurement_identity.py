import unittest
from types import SimpleNamespace

from test_selfcheck import make_docx
from dilib import testdata, verify
from dilib.content import Model
from dilib.planner import Planner


def mapping(rows, targets=None, rules=None, aliases=None, answers=None):
    rules = {"multi": "range", **(rules or {})}
    sheet = {"name": "Data", "rows": rows}
    parsed = testdata.parse_sheet(sheet, rules)
    return testdata.build(parsed, targets or [{"sym": "R", "row": 1}], rules, aliases or {}, answers or {})


def value(result, row=1, sample="1"):
    return result["fills"].get(row, {}).get(sample, {}).get("value", "")


class MeasurementIdentityTests(unittest.TestCase):
    def fixture_plan(self, sample_ids, rows, extra_sheets=()):
        sheet = {"name": "Readings", "rows": rows, "ncols": len(rows[0]), "nrows": len(rows)}
        planner = object.__new__(Planner)
        planner.questions, planner.answers, planner.summary, planner.stale, planner.ops = [], {}, [], [], []
        planner.n, planner.aliases, planner.rules, planner.row_shift = 0, {}, {"multi": "range"}, {}
        planner.report = {"fills": [], "unfilled": [], "warnings": [], "ignored": []}
        planner.src = {"S1": Model({"type": "xlsx", "name": "source", "sheets": [sheet, *extra_sheets]}, "S1")}
        planner.T = SimpleNamespace(by={"T0": {"cells": []}})
        planner.verify_data_source = lambda *args: None
        planner.fill_testdata({"id": "T0"}, {"id": "T0.D0"},
                              [{"c": i, "path": [sid]} for i, sid in enumerate(sample_ids)],
                              [{"sym": "R", "row": 2, "hi": "10"}])
        return planner

    def test_duplicate_target_columns_are_excluded_and_reported(self):
        p = self.fixture_plan(["1#", "1#", "2#"], [["item", 1, 2], ["R", 3, 5]])
        writes = {op["cell"] for op in p.ops if op["op"] == "set_cell"}
        self.assertEqual(writes, {"T0.R2.C2"})
        self.assertTrue(any("重复" in s for s in p.report["unfilled"]))

    def test_text_notes_sheet_does_not_compete_with_unique_measurement_layout(self):
        notes = {"name": "说明", "rows": [["工况", "记录方式"], ["常温", "操作员已检查"]], "ncols": 2, "nrows": 2}
        p = self.fixture_plan(["1#"], [["item", 1], ["R", 5]], [notes])
        self.assertEqual([f["value"] for f in p.report["fills"]], ["5"])

    def test_matching_sample_ids_can_change_column_order(self):
        p = self.fixture_plan(["2#", "1#"], [["item", 1, 2], ["R", 3, 5]])
        self.assertEqual([(f["target"], f["value"], f.get("source_sample")) for f in p.report["fills"]],
                         [("T0.R2.C1", "3", "1"), ("T0.R2.C0", "5", "2")])

    def test_suspect_values_listed_as_withheld_separately_from_writes(self):
        p = self.fixture_plan(["1#"], [["item", 1], ["R", 1000]])
        self.assertFalse(p.report["fills"])
        self.assertEqual(p.report["held_measurements"][0]["source"], "S1:Readings!B2:B2")

    def test_repeated_alias_shares_one_review_for_the_same_correspondence(self):
        rows = [["item", 1], ["ALT_CH1", 3], ["ALT_CH2", 6]]
        res = mapping(rows, aliases={"ALT": "R"})
        qs = [q for q in res["questions"] if q["topic"] == "数据对应"]
        self.assertEqual(len(qs), 1)
        self.assertIn("ALT_CH1", qs[0]["question"])
        self.assertIn("ALT_CH2", qs[0]["question"])

    def test_duplicate_sample_is_not_taken_from_last_column(self):
        result = mapping([["item", "S1", "1#", "S2"], ["R", 12, 14, 18]])
        self.assertEqual(value(result), "")
        self.assertEqual(value(result, sample="2"), "18")

    def test_compound_needs_positive_confirmation(self):
        rows = [["item", 1], ["VA_VB", 2], ["VA_VB", 3]]
        targets = [{"sym": "VA", "row": 1}, {"sym": "VB", "row": 2}]
        for answers in ({}, {"td:confirm": {"free": True}}, {"td:confirm": {"_default": True}}):
            self.assertEqual(value(mapping(rows, targets, answers=answers)), "")
        result = mapping(rows, targets, answers={"td:map:VA_VB": {"map": ["VB", "VA"]}})
        self.assertEqual([value(result), value(result, 2)], ["3", "2"])

    def test_one_to_many_alias_needs_choice_even_for_one_block(self):
        result = mapping([["item", 1], ["DELAY", 3]],
                         [{"sym": "R", "row": 1}, {"sym": "T", "row": 2}], aliases={"DELAY": ["R", "T"]})
        self.assertEqual(value(result), "")
        q = next(q for q in result["questions"] if q["key"] == "td:map:DELAY")
        self.assertIn({"map": ["T"]}, [o["effect"] for o in q["options"]])

    def test_explicit_condition_does_not_include_unknown_condition(self):
        rows = [["item", 1], ["R", 9], ["R_Vnorm", 4], ["R_Vmin", 1]]
        res = mapping(rows, rules={"cond_keep": ["Vnorm"]})
        self.assertEqual(value(res), "4")
        self.assertIn("td:no_suffix", [q["key"] for q in res["questions"]])

    def test_unknown_sample_unit_and_conflicting_unit(self):
        target = [{"sym": "R", "row": 1, "unit": "mV", "hi": "100"}]
        for unit in ("V", ""):
            res = mapping([["item", "unit", 1], ["R", unit, 4]], target)
            self.assertEqual(value(res), "")
        self.assertEqual(value(mapping([["item", "unit", 1], ["R", "mV", 4]], target)), "4")

    def test_both_source_labels_need_approval_to_combine(self):
        rows = [["item", 1], ["R_CH1", 4], ["R_CH2", 7]]
        res = mapping(rows)
        self.assertEqual(value(res), "")
        self.assertIn("td:merge:R", [q["key"] for q in res["questions"]])
        self.assertEqual(value(mapping(rows, answers={"td:merge:R": {"labels": ["R_CH2"]}})), "7")

    def test_voltage_suffix_does_not_prove_other_required_conditions(self):
        target = [{"sym": "R", "row": 1, "cond": "RL=100 Ω", "hi": "10"}]
        rows = [["item", 1], ["R_Vnorm", 3]]
        res = mapping(rows, target, rules={"cond_keep": ["Vnorm"]})
        self.assertEqual(value(res), "")
        self.assertTrue(any(q["topic"] == "测试工况核对" for q in res["questions"]))
        rows = [["item", "condition", 1], ["R_Vnorm", "RL=100 Ω", 3]]
        self.assertEqual(value(mapping(rows, target, rules={"cond_keep": ["Vnorm"]})), "3")

    def test_implausible_flag_and_polarity_need_evidence(self):
        for reads, target in (([1, 1], {"lo": "80"}), ([3, 7], {"lo": "-10", "hi": "0"})):
            rows = [["item", 1], ["R", reads[0]], ["", reads[1]]]
            res = mapping(rows, [{"sym": "R", "row": 1, **target}])
            self.assertEqual(value(res), "")
            q = next(q for q in res["questions"] if q["key"] == "td:val:R")
            self.assertFalse(any(o["effect"].get("mode") in ("pass", "const") for o in q["options"]))

    def test_flags_keep_per_sample_failure(self):
        res = mapping([["item", 1, 2], ["R", 1, 0], ["", 1, 1]],
                      [{"sym": "R", "row": 1, "lo": "80"}],
                      answers={"td:val:R": {"mode": "flags", "pass_code": 1}})
        self.assertEqual((value(res), value(res, sample="2")), ("合格", "不合格"))
        self.assertEqual(res["verdict"], {"1": "pass", "2": "fail"})

    def test_mixed_and_incomplete_repeats_do_not_become_valid_ranges(self):
        for second in ("FAILED", None):
            res = mapping([["item", 1, 2], ["R", 3, 4], ["", second, 6]])
            self.assertEqual(value(res), "")
            self.assertFalse(res["verdict"]["1"] == "pass")
            self.assertEqual(value(res, sample="2"), "4~6")

    def test_distinct_range_endpoints_never_become_single_measurement(self):
        self.assertEqual(value(mapping([["item", 1], ["R", "1.231"], ["", "1.232"]])), "1.231~1.232")

    def test_unparseable_specification_is_not_a_pass(self):
        res = mapping([["item", 1], ["R", 5]], [{"sym": "R", "row": 1, "hi": "< 5"}])
        self.assertNotEqual(res["verdict"]["1"], "pass")

    def test_verification_checks_sample_header_even_when_readings_equal(self):
        md = Model({"type": "xlsx", "sheets": [{"name": "Data", "rows": [["item", 1, 2], ["R", 4, 4]]}]}, "S1")
        fill = {"target": "T0.R2.C1", "source": "S1:Data!C2", "value": "4", "limits": [None, None],
                "source_sample": "1", "header_row": 1}
        checked = {"items": [], "checked": 0}
        verify._testdata({"report": {"fills": [fill]}}, {"S1": md}, testdata.DEFAULT_RULES, checked)
        self.assertTrue(checked["items"])
