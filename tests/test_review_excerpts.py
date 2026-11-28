"""Review excerpts retain all text in explicitly cited cells and never synthesize a source."""
import unittest
from types import SimpleNamespace

import test_selfcheck  # Set up the source skill import path.
from dilib.content import Model
from dilib.workflow import review_excerpts, review_references


class ReviewExcerptTests(unittest.TestCase):
    def test_long_cell_text_survives_json_review_excerpt(self):
        long_text = "前段记录" + "0123456789" * 180 + "末尾唯一编号-X901"
        sheet = {"name": "实测", "nrows": 3, "ncols": 2,
                 "rows": [["物料", "批次"], ["短格", long_text], ["非引用行", "排除标记"]]}
        model = Model({"type": "xlsx", "sheets": [sheet]}, "S2")
        job = SimpleNamespace(model={"S2": model}.__getitem__)
        row = {"target": "T0.R1.C2", "source": "S2:实测!B2:B2", "check": "超出限值"}
        refs = review_references({"fills": [row]})
        self.assertEqual(refs, ["S2:实测!B2:B2"])
        excerpt, = review_excerpts(job, refs)
        self.assertEqual(excerpt["ref"], refs[0])
        self.assertIn(long_text, excerpt["source_text"])
        self.assertNotIn("排除标记", excerpt["source_text"])

    def test_report_only_warning_has_no_source_reference_or_excerpt(self):
        report = {"warnings": ["审核报告.md"], "unfilled": ["待补无来源字段"],
                  "fills": [{"source": "", "check": "没有可回读的原始单元格"}]}
        refs = review_references(report)
        self.assertEqual(refs, [])
        self.assertEqual(review_excerpts(SimpleNamespace(), refs), [])


class HumanReviewTurnTests(unittest.TestCase):
    def test_original_fill_request_is_not_a_new_confirmation(self):
        from dilib.decisions import needs_reviewer_reply
        instructions = "将 N100 规范填写进记录。进一步将 B.xlsx 结果填入记录。仅需提取中间电压，多次数据填写范围。"
        self.assertTrue(needs_reviewer_reply("src:spec:1", {"applicable": True}, instructions, "将 N100 规范填写进记录。"))
        self.assertTrue(needs_reviewer_reply("src:verify:S2", {"ok": True}, instructions, "进一步将 B.xlsx 结果填入记录。"))
        self.assertTrue(needs_reviewer_reply("td:interpret_if:x", {"kind": "measurement"}, instructions, "仅需提取中间电压，多次数据填写范围。"))
        self.assertTrue(needs_reviewer_reply("td:interpret_if:x", {"kind": "measurement"}, instructions, None))
        self.assertFalse(needs_reviewer_reply("td:interpret_if:x", {"kind": "blank"}, instructions, None))
        self.assertFalse(needs_reviewer_reply("td:interpret_if:x", {"kind": "limits", "sign": "negate"}, instructions, "按负值比较，填判定。"))
        self.assertFalse(needs_reviewer_reply("src:spec:1", {"applicable": True}, instructions + "本任务适用所供规范。", "本任务适用所供规范。"))
