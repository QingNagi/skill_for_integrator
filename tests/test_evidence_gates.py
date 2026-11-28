import unittest

from test_measurement_identity import mapping, value
from test_measurement_tables import table
from dilib.content import Model
from dilib.planner import Planner


class EvidenceGates(unittest.TestCase):
    def test_78_synthetic_positions_before_and_after_scoped_evidence(self):
        # 30 bare + 3 single + 30 compound + 6 polarity + 6 ambiguous segments + 3 binary rate cells.
        rows = [["label", 1, 2, 3]]
        targets, expected = [], {}
        def target(sym, label, *, negative=False, rate=False):
            index = len(targets) + 1
            targets.append({"row": index, "sym": sym, "hi": "-1" if negative else ("900" if rate else "200"),
                            "lo": "-200" if negative else ("800" if rate else "0"),
                            "unit": "Mbps" if rate else "mV", "column_type": "measurement"})
            readings = [1, 0, 1] if rate else [index * 3 + s for s in (1, 2, 3)]
            rows.append([label, *readings])
            for sample, raw in enumerate(readings, 1):
                expected[index, str(sample)] = "" if rate else str(-raw if negative else raw)
        for i in range(10):
            target("BARE%d" % i, "BARE%d" % i)
        target("ONE", "ONE_Vnorm")
        for i in range(5):
            pair = ["PX%d" % i, "PY%d" % i]
            for sym in pair:
                target(sym, "_".join(pair) + "_Vnorm")
        for sym in ("HIGH", "LOW"):
            target(sym, sym + "_Vnorm", negative=True)
        for sym in ("JITX", "JITY"):
            target(sym, "JITX_JITY_AUX_Vnorm")
        target("RATE", "RATE_Vnorm", rate=True)
        rules = {"cond_keep": ["Vnorm"], "multi": "range"}
        unresolved = mapping(rows, targets, rules=rules)
        grid = lambda res: {(r, s): value(res, r, s) for r, s in expected}
        self.assertEqual(set(grid(unresolved).values()), {""})
        self.assertLessEqual(len(unresolved["questions"]), 7)
        answers = {"td:no_suffix": {"no_suffix": "keep"}, "td:map:JITX_JITY_AUX": {"map": ["JITX", "JITY"]},
                   "td:val:LOW": {"mode": "negate"}, "td:val:HIGH": {"mode": "negate"},
                   "td:val:RATE": {"mode": "blank"}}
        known = mapping(rows, targets, rules=rules, answers=answers)
        for q in known["questions"]:
            if q["key"].startswith("td:units:"):
                answers[q["key"]] = q["options"][1]["effect"]
        filled = mapping(rows, targets, rules=rules, answers=answers)
        self.assertEqual(grid(filled), expected)
        self.assertTrue(all("取负号" in filled["fills"][r]["1"]["how"] for r in (22, 23)))
        self.assertEqual(len(expected), 78)

    def test_explicit_sheet_condition_confirms_an_unlabelled_voltage(self):
        target = [{"sym": "R", "row": 1, "cond": "中间电压", "hi": "10"}]
        rows = [["标签", "condition", 1], ["R", "中间电压", 4], ["R_Vmax", "最大电压", 9]]
        res = mapping(rows, target, rules={"cond_keep": ["Vnorm"]})
        self.assertEqual(value(res), "4")
        self.assertFalse(any(q["key"] == "td:no_suffix" for q in res["questions"]))
        rows[1][1] = "最小电压"
        res = mapping(rows, target, rules={"cond_keep": ["Vnorm"]},
                      answers={"td:no_suffix": {"no_suffix": "keep"}})
        self.assertEqual(value(res), "")

    def test_rule_pack_alias_cites_dictionary_entry(self):
        rows = [["label", 1], ["ALT_Vnorm", 6]]
        rules = {"cond_keep": ["Vnorm"], "aliases": {"ALT": {"symbol": "R", "evidence": "程序表第3项"}}}
        result = mapping(rows, rules=rules)
        self.assertEqual(value(result), "6")
        self.assertIn("程序表第3项", " ".join(result["fills"][1]["1"]["hows"]))

    def test_declared_segment_order_and_unresolved_mismatch(self):
        targets = [{"row": i, "sym": sym} for i, sym in enumerate(["A", "B", "C"], 1)]
        rows = [["label", 1], ["A_B_C", 5], ["A_B_C", 8]]
        result = mapping(rows, targets)
        q = next(q for q in result["questions"] if q["key"] == "td:map:A_B_C")
        self.assertFalse(any(o["effect"].get("map") for o in q["options"]))
        result = mapping(rows, targets, rules={"segment_order": {"A_B_C": ["B", "A"]}})
        self.assertEqual((value(result), value(result, 2)), ("8", "5"))

    def test_rate_flags_never_become_rate(self):
        rows = [["label", "unit", 1], ["RATE", "Mbps", 1]]
        target = [{"row": 1, "sym": "RATE", "lo": "800", "unit": "Mbps", "column_type": "measurement"}]
        for ans in ({"mode": "flags", "pass_code": 1}, {"mode": "raw"}):
            self.assertEqual(value(mapping(rows, target, answers={"td:val:RATE": ans})), "")
        target[0]["column_type"] = "judgment"
        self.assertEqual(value(mapping(rows, target, answers={"td:val:RATE": {"mode": "flags", "pass_code": 1}})), "合格")

    def test_decimal_ties_and_rounding_flip_are_auditable(self):
        rows = [["label", 1], ["A", "2.665"]]
        target = [{"row": 1, "sym": "A", "hi": "2.66"}]
        for rounding, expected in [("half_up", "2.67"), ("half_even", "2.66")]:
            rules = {"rounding": rounding, "comparison": "rounded", "precision": "fixed"}
            res = mapping(rows, target, rules=rules)
            if rounding == "half_even":
                self.assertTrue(any("修约" in item for item in res["review"]))
                self.assertEqual(value(res), "")
            else:
                self.assertEqual(value(res), expected)
        res = mapping(rows, target, rules={"rounding": "half_even", "comparison": "full"})
        self.assertTrue(any("修约" in item for item in res["review"]))

    def test_scope_count_excludes_exempt_parameter_and_spare_template_rows(self):
        p = object.__new__(Planner)
        p.task = ""
        spec = table("spec", [["符号", "最大值", "单位"], ["A", "20", "V"], ["B", "20", "V"], ["C", "20", "V"]])
        spec.by["T0"]["foot"] = ["a 设计保证，不测试"]
        cell = next(c for c in spec.by["T0"]["cells"] if c["r"] == 2 and c["c"] == 1)
        cell["runs"] = [["20", ""], ["a", "^"]]
        p.src = {"S1": spec}
        p.T = table("T", [["符号", "最大值", "单位", "1#"]] + [[""]*4 for i in range(8)])
        cols = [{"c": i, "path": [s]} for i,s in enumerate(["符号", "最大值", "单位", "1#"])]
        rg = {"id": "T0.D0", "cols": cols, "rows": [1,8]}
        selected = p.affected_spec_rows({"id": "T0"}, rg)
        self.assertEqual([r["sym"] for r in selected if not r["not_tested"]], ["A", "C"])

    def test_missing_spec_model_requires_task_level_confirmation(self):
        p = object.__new__(Planner)
        p.task, p.questions, p.answers, p.report = "", [], {}, {"warnings": []}
        p.T = table("T", [["型号", "DEMO72LX903E"]])
        p.src = {"S1": table("规范", [["x"]])}
        self.assertIs(p.spec_evidence({"DEMO72LX903E"}), False)
        self.assertEqual(p.questions[0]["topic"], "规范适用")

    def test_explicit_cover_fields_match_only_complete_batch_tokens(self):
        from dilib.identity import document_identity
        target = Model({"type": "docx", "name": "T", "blocks": [
            {"id": "P1", "t": "p", "text": "型号 DEMO4100  批号 L120"}]}, "T")
        self.assertEqual(document_identity(target)["batch"], {"L120"})
        p = object.__new__(Planner)
        p.T, p.task, p.questions, p.answers, p.report = target, "", [], {}, {"warnings": []}
        p.src = {"S1": target}
        book = Model({"type": "xlsx", "name": "DEMO4100 L120.xlsx"}, "S2")
        for name, expected in (("DEMO4100_L1201", False), ("DEMO4100_L120", True)):
            self.assertIs(p.verify_data_source("S2", book, {"name": name, "rows": []}), expected)

    def test_matching_model_without_batch_does_not_release_measurements(self):
        p = object.__new__(Planner)
        p.task, p.questions, p.answers, p.report = "", [], {}, {"warnings": []}
        p.T = table("T", [["型号", "AA1901"], ["批号", "LOT22"]])
        p.src = {"S1": table("AA1901", [["x"]])}
        book = Model({"type": "xlsx", "name": "AA1901.xlsx"}, "S2")
        self.assertIs(p.verify_data_source("S2", book, {"name": "Data", "rows": [["item", 1]]}), False)
        self.assertTrue(any(q["key"].startswith("src:verify:S2") for q in p.questions))

    def test_conflicting_template_model_gates_even_after_batch_confirmation(self):
        p = object.__new__(Planner)
        p.task, p.questions, p.answers, p.report = "", [], {}, {"warnings": []}
        p.T = table("T", [["型号", "DEMO72LX903E"], ["批号", "202602"]])
        p.src = {"S1": table("DEMO72LX903N1", [["x"]])}
        book = Model({"type": "xlsx", "name": "DEMO72LX903N1.xlsx"}, "S2")
        sheet = {"name": "Data", "rows": [["批号", "202602"], ["项目", 1]]}
        self.assertIs(p.verify_data_source("S2", book, sheet), False)
        q = next(q for q in p.questions if q["key"].startswith("src:spec:"))
        self.assertIn("DEMO72LX903E", q["question"])
        p.answers[q["key"]] = {"effect": q["options"][1]["effect"]}
        self.assertIs(p.verify_data_source("S2", book, sheet), True)
