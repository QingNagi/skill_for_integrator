"""Page accounting when pdfplumber supplies text and tables independently."""
import sys
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from dilib.read_other import _pdf_plumber


class PdfPlumberTests(unittest.TestCase):
    def test_only_unread_or_partially_read_pages_are_reported(self):
        def page(text, tables):
            return SimpleNamespace(extract_text=Mock(return_value=text), extract_tables=Mock(return_value=tables))

        pages = [page('short', []), page(None, [[['table value']]]), page('', [[[None, ' ']]]),
                 page(None, [[['still available']]]), page('  ', [])]
        pages[3].extract_text.side_effect = ValueError('text failed')
        pdf = SimpleNamespace(pages=pages)
        with patch.dict(sys.modules, pdfplumber=SimpleNamespace(open=lambda path: nullcontext(pdf))):
            model = _pdf_plumber('source.pdf')
        self.assertEqual(model['stats']['pages'], 5)
        self.assertEqual([item['page'] for item in model['unread']], [3, 4, 5])
        self.assertIn('text failed', model['unread'][1]['why'])
        self.assertIn('still available', [cell['text'] for block in model['blocks']
                                         if block['t'] == 'tbl' for cell in block['cells']])


if __name__ == '__main__':
    unittest.main()
