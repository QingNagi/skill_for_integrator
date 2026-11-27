"""A term read by OCR must be retrievable through the ordinary skill search."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "traced-template-fill" / "scripts"))
from dilib.content import Model, search


class OfflineSearchTests(unittest.TestCase):
    def test_english_word_in_recognized_line(self):
        model = Model({"type": "pdf", "blocks": [
            {"id": "P2", "t": "p", "page": 2, "ocr": True, "text": "OFFLINE DOCUMENT 2026"}
        ]}, "S1")
        results = search({"S1": model}, "offline")
        self.assertEqual([(r[1], r[2]) for r in results], [("S1", "P2")])
        self.assertIn("OCR", results[0][3])
