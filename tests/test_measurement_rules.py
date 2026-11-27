import contextlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from test_selfcheck import make_docx
from dilib import cli, testdata, verify
from dilib.content import Model
from dilib.job import Job
from dilib.read_other import read_csv, read_xlsx


class MeasurementTests(unittest.TestCase):
    def result(self, values, lo=None, hi=None, **rules):
        rows = [["label", "1"]] + [["V" if i == 0 else "", v] for i, v in enumerate(values)]
        parsed = testdata.parse_sheet({"name": "sheet", "rows": rows}, rules)
        return testdata.build(parsed, [{"sym": "V", "row": 0, "lo": lo, "hi": hi}], rules, {}, {})

    def test_visible_values_preserve_limit_comparisons(self):
        for values, lo, hi, expected, verdict in (
            (["1.3504"], None, "1.35", "1.3504", "fail"),
            (["-1.3504"], "-1.35", None, "-1.3504", "fail"),
            (["1.3451"], None, "1.346", "1.3451", "pass"),
            (["1.3499", "1.3504"], None, "1.35", "1.35~1.3504", "fail"),
            (["0.0000000004"], None, "0", "0.0000000004", "fail"),
            (["1.35000000000000001"], None, "1.35", "1.35000000000000001", "fail"),
        ):
            with self.subTest(values=values):
                result = self.result(values, lo, hi)
                self.assertEqual(result["fills"][0]["1"]["value"], expected)
                self.assertEqual(result["verdict"]["1"], verdict)

    def test_precision_survives_csv_loading(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "values.csv")
            path.write_text("label,1\nV,1.2300\n", encoding="utf-8")
            parsed = testdata.parse_sheet(read_csv(path)["sheets"][0], {})
        target = [{"sym": "V", "row": 0}]
        for mode, expected in (("source", "1.2300"), ("trim", "1.23")):
            result = testdata.build(parsed, target, {"precision": mode}, {}, {})
            self.assertEqual(result["fills"][0]["1"]["value"], expected)

    def test_upper_bound_written_with_operator_and_matching_unit(self):
        parsed = testdata.parse_sheet({"name": "sheet", "rows": [["label", "1"], ["V", "1.3504"]]}, {})
        result = testdata.build(parsed, [{"sym": "V", "row": 0, "hi": "≤ 1.35 V", "unit": "V"}], {}, {}, {})
        self.assertEqual(result["fills"][0]["1"]["value"], "1.3504")
        self.assertEqual(result["verdict"]["1"], "fail")

    def test_symbol_can_follow_decimal_places_in_its_specification(self):
        result = self.result(["1.234567"], hi="1.3560", symbol_formats={"V": {"decimals": "limit", "precision": "fixed"}})
        self.assertEqual(result["fills"][0]["1"]["value"], "1.2346")

    def test_custom_conditions_and_compound_separator(self):
        rules = {"cond_pattern": "@(cold|hot)$", "cond_keep": ["cold"], "token_separator": "/",
                 "delta_prefixes": ["SHIFT"]}
        sheet = {"name": "Sheet", "rows": [["item", "1"], ["VOLT/SHIFTR@hot", 9],
                                            ["VOLT/SHIFTR@cold", 2], ["VOLT/SHIFTR@cold", 3]]}
        result = testdata.build(testdata.parse_sheet(sheet, rules),
                                [{"sym": "VOLT", "row": 0}, {"sym": "ΔR", "row": 1}], rules, {}, {})
        self.assertEqual([result["fills"].get(row, {}).get("1", {}).get("value") for row in (0, 1)], ["2", "3"])

    def test_plain_decimal_xlsx_style_preserves_zeros_without_rounding_raw_measurement(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "values.xlsx")
            ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
            rel = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
            with zipfile.ZipFile(path, "w") as z:
                z.writestr("xl/workbook.xml", '<workbook %s %s><sheets><sheet name="S" r:id="r"/>'
                           '</sheets></workbook>' % (ns, rel))
                z.writestr("xl/_rels/workbook.xml.rels", '<Relationships><Relationship Id="r" Target="s.xml"/></Relationships>')
                z.writestr("xl/styles.xml", '<styleSheet %s><numFmts><numFmt numFmtId="164" formatCode="0.0000"/>'
                           '</numFmts><cellXfs><xf/><xf numFmtId="164"/></cellXfs></styleSheet>' % ns)
                z.writestr("xl/s.xml", '<worksheet %s><sheetData><row><c r="A1" t="inlineStr"><is><t>label</t>'
                           '</is></c><c r="B1"><v>1</v></c></row><row><c r="A2" t="inlineStr"><is><t>V</t></is></c>'
                           '<c r="B2" s="1"><v>1.23</v></c></row><row><c r="B3" s="1"><v>1.35004</v></c>'
                           '</row></sheetData></worksheet>' % ns)
            sheet = read_xlsx(path)["sheets"][0]
        parsed = testdata.parse_sheet(sheet, {})
        result = testdata.build(parsed, [{"sym": "V", "row": 0, "hi": "1.35"}], {}, {}, {})
        self.assertEqual(result["fills"][0]["1"]["value"], "1.2300~1.35004")

    def test_verifier_rejects_rounded_away_excursion(self):
        md = Model({"type": "xlsx", "sheets": [{"name": "Data", "rows": [[1.3504]]}]}, "S1")
        fill = {"target": "T0.R1.C2", "source": "S1:Data!A1", "value": "1.35",
                "limits": [None, "1.35"], "aggregation": "range"}
        plan = {"report": {"fills": [fill]}}
        checked = {"items": [], "checked": 0}
        verify._testdata(plan, {"S1": md}, testdata.DEFAULT_RULES, checked)
        self.assertEqual(len(checked["items"]), 1)
        fill["value"] = "1.3504"
        checked = {"items": [], "checked": 0}
        verify._testdata(plan, {"S1": md}, testdata.DEFAULT_RULES, checked)
        self.assertEqual(checked["items"], [])

    def test_cli_saves_rules_for_later_planning(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / "template.docx"
            make_docx(target)
            source = root / "source.txt"
            source.write_text("Source", encoding="utf-8")
            config = root / "rules.json"
            config.write_text(json.dumps({"decimals": 4, "token_separator": "/"}), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                rc = cli.main(["new", "-t", str(target), "-s", str(source), "--out", str(root),
                               "--name", "job", "--test-rules", str(config)])
            self.assertEqual(rc, 0)
            config.unlink()
            self.assertEqual(cli.do_plan(Job(root / "job"), quiet=True)["rules"]["decimals"], 4)
