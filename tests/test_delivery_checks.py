"""An empty cell and an SI prefix must survive the Word writer unchanged."""
import unittest
from unittest.mock import Mock, patch

import test_selfcheck  # Set up the source skill import path.
from dilib.verify import _readback
from dilib.workflow import planned_text, review_summary


class DeliveryChecks(unittest.TestCase):
    def test_counts_include_unmapped_blanks_and_explain_what_needs_review(self):
        report = {"measurement_targets": {"a": "", "b": "", "c": "", "d": "上批"},
                  "measurement_written": planned_text([{"op": "set_text", "cell": "a", "text": "4.50"}]),
                  "held_measurements": [{"target": "c", "check": "含状态码"}]}
        summary = review_summary(report, [{"detail": "同一源区域包含 FAILED"}])
        self.assertEqual((summary["measurement_fills"], summary["measurement_holds"],
                          summary["measurement_retained"]), (1, 2, 1))
        self.assertEqual(sum(item["count"] for item in summary["hold_reasons"]), 2)
        self.assertEqual(summary["unverified_reasons"], [{"reason": "同一源区域包含 FAILED", "count": 1}])

    def test_readback_preserves_case_and_distinguishes_missing_from_empty(self):
        plan = {"ops": [{"op": "set_cell", "cell": "T0.R0.C0", "runs": [["1 mV", ""]]}],
                "report": {"held_measurements": [{"target": "T0.R0.C1"}]}}
        template = Mock()
        template.get.return_value = {"text": ""}
        res = {"items": [], "notes": []}
        with patch("dilib.finalize.texts_by_id", return_value=[("T0.R0.C0", "1 MV")]):
            _readback(plan, "draft.docx", res, template)
        self.assertEqual({row["name"] for row in res["items"]}, {"T0.R0.C0", "T0.R0.C1"})
        self.assertEqual(res["readback"], 2)

    def test_held_cell_can_keep_preexisting_template_text(self):
        template = Mock()
        template.get.return_value = {"text": "上批值"}
        plan = {"ops": [], "report": {"held_measurements": [{"target": "T0.R1.C1"}]}}
        res = {"items": [], "notes": []}
        with patch("dilib.finalize.texts_by_id", return_value=[("T0.R1.C1", "上批值")]):
            _readback(plan, "draft.docx", res, template)
        self.assertEqual(res["items"], [])
        self.assertEqual(res["readback"], 1)
