"""A Word conversion should fail before COM activation when it cannot create work files."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from dilib import office


class WordWorkfileTests(unittest.TestCase):
    def test_creates_missing_content_word_under_existing_user_cache(self):
        with tempfile.TemporaryDirectory() as cache:
            with patch.object(office, "_user_internet_cache", return_value=cache):
                self.assertIsNone(office.word_workfile_error())
            self.assertEqual(list((Path(cache) / 'Content.Word').iterdir()), [])

    def test_denied_cache_stops_word_before_com_open(self):
        with patch.object(office, "_user_internet_cache", return_value="unused"), \
             patch.object(office.os, "mkdir", side_effect=PermissionError("denied")), \
             patch.object(office, "_engines", return_value=[("com", "Word.Application")]), \
             patch.object(office, "_run_ps", side_effect=AssertionError("must not launch COM")):
            result = office.convert_many([("example.doc", "result.docx", "docx")])
        self.assertIn("Content.Word", result["result.docx"])
        self.assertIn("另存", result["result.docx"])
