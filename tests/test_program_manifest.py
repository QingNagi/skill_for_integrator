from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from xml.sax.saxutils import escape

from test_selfcheck import make_xlsx
from dilib.common import DIError
from dilib.evidence import program_answers
from dilib.read_other import col_letter


class ProgramManifestTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.path = self.root / "清单.xlsx"
        self.job = SimpleNamespace(data={"sources": [{"alias": "S1", "path": str(self.root / "来源.xlsx")}]},
                                   model=lambda _: SimpleNamespace(m={"sheets": [{"name": "Data"}]}))
        self.q = dict(id="Q3", key="td:map:A_B:S1", source_label="A_B", source_labels=["A_B_Vnorm"],
                      block_lengths=[4], segment_count=1, source_scope="S1:Data", source_label_col="A",
                      parameter_candidates=["A", "B"], parameter_units={"A": "μA", "B": "mV"})
        self.rows = [["测试号", "测试名（来源.xlsx A 列标签）", "段序", "参数", "条件", "单位", "备注"],
                     ["11", "A_B_Vnorm", "2", "B", "VDD=3V", "mV", "第3~4行"],
                     ["11", "A_B_Vnorm", "1", "A", "VDD=3V", "uA", "第1~2行"]]

    def save(self):
        content = []
        for index, row in enumerate(self.rows, 1):
            cells = ['<c r="%s%d" t="inlineStr"><is><t>%s</t></is></c>' % (col_letter(col), index, escape(s))
                     for col, s in enumerate(row)]
            content.append('<row r="%d">%s</row>' % (index, "".join(cells)))
        xml = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>%s</sheetData></worksheet>'
        make_xlsx(self.path, {"xl/worksheets/sheet1.xml": xml % "".join(content)})

    def answer(self, q=None):
        self.save()
        return program_answers([q or self.q], ["Q3"], self.path, job=self.job)[1]["effects"]["Q3"]

    def test_repeated_test_number_different_explicit_spans(self):
        self.assertEqual(self.answer(), {"segments": [{"symbol": "A", "start": 1, "end": 2},
                                                     {"symbol": "B", "start": 3, "end": 4}]})
        for bad_range in ("第2~4行", "第4~4行", "第3~5行", "第1~2行", ""):
            self.rows[1][-1] = bad_range
            with self.subTest(bad_range=bad_range), self.assertRaises(DIError):
                self.answer()

    def test_implicit_binding_must_be_unique_and_use_registered_column(self):
        self.q["source_label_col"] = "B"
        with self.assertRaises(DIError):
            self.answer()
        self.q["source_label_col"] = "A"
        self.job.data["sources"].append({"alias": "S2", "path": str(self.root / "其他" / "来源.xlsx")})
        with self.assertRaises(DIError):
            self.answer()

    def test_one_submission_keeps_manual_decisions_separate_from_file_proof(self):
        self.save()
        answers, meta = program_answers([self.q], ["Q2=2", "Q3"], self.path, job=self.job)
        self.assertEqual(answers, ["Q2=2", "Q3=测试程序清单"])
        self.assertEqual(set(meta["effects"]), {"Q3"})
        self.assertEqual(program_answers([self.q], ["Q2=2"], self.path, job=self.job)[0], ["Q2=2"])

    def test_label_header_and_explicit_source_must_agree(self):
        self.rows[0].append("来源")
        for row in self.rows[1:]:
            row.append("S1:Data")
        self.assertIn("segments", self.answer())
        self.rows[0][1] = "测试名（另一个.xlsx A 列标签）"
        with self.assertRaises(DIError):
            self.answer()

    def test_actual_condition_is_retained_and_ambiguous_evidence_rejected(self):
        q = dict(self.q, key="td:conditions:1", options=[{"effect": {"conditions": {"A": "RL=100Ω"}}}],
                 labels_by_symbol={"A": ["A_B_Vnorm"]})
        self.rows.append(["12", "A_B_Vmax", "1", "A", "VDD=5V", "uA", ""])
        self.assertEqual(self.answer(q), {"conditions": {"A": "VDD=3V"}})
        self.rows[-1][1] = "A_B_Vnorm"
        with self.assertRaises(DIError):
            self.answer(q)

    def test_different_dimensions_are_rejected(self):
        self.rows[1][5] = "MA"
        with self.assertRaises(DIError):
            self.answer()

    def test_no_suffix_needs_consistent_voltage_evidence(self):
        q = dict(self.q, key="td:no_suffix:1", source_labels=["C"], conditions=["Vnorm"], condition_names={})
        self.rows.append(["13", "C", "1", "C", "VDD=3V", "μA", ""])
        self.assertEqual(self.answer(q), {"no_suffix": "keep"})
        self.rows[-1][4] = "VDD=5V"
        with self.assertRaises(DIError):
            self.answer(q)


    def test_explicit_source_allows_correct_sheet_in_multisheet_measurement_book(self):
        self.job.model=lambda _ : SimpleNamespace(m={"sheets":[{"name":"Wrong"},{"name":"Data"}]})
        with self.assertRaises(DIError):
            self.answer()
        self.rows[0].append("来源")
        for row in self.rows[1:]:
            row.append("S1:Data")
        self.rows.append(["99","A_B_Vnorm","1","B","other","mV","第1~4行","S1:Wrong"])
        self.assertEqual(self.answer()["segments"][1], {"symbol":"B","start":3,"end":4})
        self.q["source_scope"]="S1:Absent"
        with self.assertRaises(DIError):
            self.answer()

    def test_units_require_every_contributing_label(self):
        q = dict(self.q, key="td:units:a", labels_by_symbol={"A": ["A_B_Vnorm", "other"]},
                 options=[{"effect": {"units": {"A": "μA"}}}])
        with self.assertRaises(DIError):
            self.answer(q)

    def test_partial_unit_declarations_release_only_fully_supported_parameters(self):
        from dilib import evidence
        self.save()
        _, manifest = program_answers([self.q], [], self.path, job=self.job)
        manifest.update(fp="abc", rule_sha256="rules")
        q = dict(self.q, key="td:units:test:S1:T0.D0:Data",
                 labels_by_symbol={"A": ["A_B_Vnorm"], "B": ["other"]},
                 options=[{"effect": {"units": {"A": "μA", "B": "mV"}}}])
        answers = evidence.manifest_answers(manifest, [q])
        self.assertEqual([r["effect"] for r in answers.values()], [{"units": {"A": "μA"}}])
        self.assertNotIn(q["key"], answers)
