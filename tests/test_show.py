"""Batched show references and PDF page readback."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from dilib import cli
from dilib.content import Model


class ShowTests(unittest.TestCase):
    def pdf(self):
        return Model({
            "type": "pdf", "stats": {"pages": 2},
            "blocks": [
                {"id": "P0", "t": "p", "page": 1, "text": "native first"},
                {"id": "P7", "t": "p", "page": 1, "text": "OCR second", "ocr": True},
                {"id": "P1", "t": "p", "page": 1, "text": "native third"},
            ],
            "unread": [{"page": 2, "why": "no text layer"}],
        }, "S1")

    def show(self, *args):
        with patch.object(cli, "Job") as job, patch.object(cli, "out") as out:
            job.return_value.model.return_value = self.pdf()
            rc = cli.main(["show", "job", *args])
            job.assert_called_once_with("job")
            return rc, "\n".join(str(c.args[0]) for c in out.call_args_list)

    def test_multiple_refs_preserve_request_order_and_deduplicate(self):
        rc, rendered = self.show("S1:P1", "S1:P0", "S1:P1", "--full")
        self.assertEqual(rc, 0)
        self.assertLess(rendered.index("[S1:P1]"), rendered.index("[S1:P0]"))
        self.assertEqual(rendered.count("native third"), 1)

    def test_rows_and_full_are_applied_per_table_in_a_batch(self):
        cell = lambda row, text: {"r": row, "c": 0, "text": text}
        value = "untruncated measured result across a long cell"
        table = {"t": "tbl", "ncols": 1, "nrows": 2,
                 "cells": [cell(0, "row zero"), cell(1, value)]}
        model = Model({"type": "docx", "blocks": [
            dict(table, id="T0"), dict(table, id="T1")]}, "S1")
        with patch.object(cli, "Job") as job, patch.object(cli, "out") as out:
            job.return_value.model.return_value = model
            rc = cli.main(["show", "job", "S1:T0", "S1:T1.R1", "--rows", "1-1", "--full"])
        result = "\n".join(c.args[0] for c in out.call_args_list)
        self.assertEqual(rc, 0)
        self.assertEqual(result.count(value), 2)
        self.assertNotIn("row zero", result)

    def test_single_reference_keeps_previous_output(self):
        self.assertEqual(self.show("S1:P0"), (0, "P0: native first"))

    def test_page_uses_model_reading_order_and_exposes_ocr_provenance(self):
        rc, rendered = self.show("S1:page1")
        self.assertEqual(rc, 0)
        for ref in ("S1:P0", "S1:P7", "S1:P1", "[OCR，第 1 页]"):
            self.assertIn(ref, rendered)
        self.assertLess(rendered.index("native first"), rendered.index("OCR second"))
        self.assertLess(rendered.index("OCR second"), rendered.index("native third"))

    def test_unread_and_nonexistent_pages_are_distinct(self):
        rc, rendered = self.show("S1:page2")
        self.assertEqual(rc, 0)
        self.assertIn("no text layer", rendered)
        self.assertNotEqual(self.show("S1:page3")[0], 0)


if __name__ == "__main__":
    unittest.main()
