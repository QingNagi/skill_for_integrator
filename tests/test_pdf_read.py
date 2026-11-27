"""Page accounting when pdfplumber supplies text and tables independently."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'traced-template-fill' / 'scripts'))
from unittest.mock import patch
from dilib.read_other import read_pdf
from pdf_samples import make_pdf, make_table
import tempfile


class PdfPlumberTests(unittest.TestCase):
    def test_merged_cells_reading_order_and_page_accounting(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'source.pdf'
            make_table(path)
            from dilib.pdf_reader import read_page_words
            word = next(w for w in read_page_words(path, 1) if w['text'] == '-0.125')
            self.assertEqual(word['page'], 1)
            self.assertAlmostEqual(word['bbox'][0], 30)
            model = read_pdf(str(path), ocr={'available': False, 'hint': 'off'})
        self.assertEqual(model['stats']['pages'], 2)
        self.assertEqual([x['page'] for x in model['unread']], [2])
        self.assertEqual([b['t'] for b in model['blocks']], ['p', 'tbl'])
        table = model['blocks'][1]
        self.assertEqual((table['id'], table['page'], table['cap_id'], table['hdr']), ('T0', 1, 'P0', 2))
        cells = {(c['r'], c['c']): c for c in table['cells']}
        self.assertEqual((cells[0, 0]['cs'], cells[0, 2]['rs']), (2, 2))
        self.assertEqual([cells[2, c]['text'] for c in range(3)], ['-0.125', '1.250', 'mV'])
        self.assertEqual(table['bbox'], [20, 80, 320, 200])
        self.assertIn('bbox', model['blocks'][0])

    def test_text_columns_keep_distinct_paragraphs_in_reading_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'columns.pdf'
            text = [(20, 270, 'Left first'), (230, 270, 'Right first'),
                    (20, 250, 'Left second'), (230, 250, 'Right second')]
            make_pdf(path, [(' '.join('BT /F1 12 Tf %d %d Td (%s) Tj ET' % item for item in text), '')])
            model = read_pdf(path, ocr={'available': False})
        self.assertEqual([b['text'] for b in model['blocks']],
                         ['Left first', 'Left second', 'Right first', 'Right second'])

    def test_rotated_crop_preserves_visible_words_and_image_pixels(self):
        from dilib import _bootstrap
        _bootstrap.activate_vendor()
        from PIL import Image, ImageOps, ImageStat
        from dilib.pdf_reader import read_page_words
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'rotated.pdf'
            # The asymmetric CropBox retains the picture and excludes the outside label.
            make_pdf(path, [('q 140 0 0 50 120 40 cm /Im Do Q '
                             'BT /F1 12 Tf 321 65 Td (IN) Tj ET '
                             'BT /F1 12 Tf 82 65 Td (X) Tj ET',
                             '/Rotate 90 /CropBox [100 20 340 100]')], with_image=True)
            words = read_page_words(path, 1)
            self.assertEqual([w['text'] for w in words], ['IN'])
            model = read_pdf(path, tmp, ocr={'available': False, 'hint': 'off'})
            info = model['images']['I0']
            self.assertEqual(info['bbox'], [40, 120, 90, 260])
            with Image.open(info['file']) as im:
                self.assertEqual(im.size, (100, 280))
                self.assertGreater(ImageStat.Stat(ImageOps.grayscale(im)).stddev[0], 15)

    def test_text_failure_preserves_table_and_records_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'source.pdf'
            make_table(path)
            with patch('dilib.pdf_reader._native_paragraphs', side_effect=ValueError('text failed')):
                model = read_pdf(str(path), ocr={'available': False, 'hint': 'off'})
        self.assertEqual(model['blocks'][0]['cells'][0]['text'], 'Measured limits')
        self.assertEqual([u['page'] for u in model['unread']], [1, 2])
        self.assertIn('text failed', model['unread'][0]['why'])

    def test_pypdf_fallback_keeps_last_blank_page_and_native_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'source.pdf'
            make_table(path)
            with patch('dilib.pdf_reader.read_layout', side_effect=ImportError('pdfplumber')):
                model = read_pdf(str(path), ocr={'available': False})
        self.assertEqual(model['pdf_backend'], 'pypdf')
        self.assertEqual(model['stats']['pages'], 2)
        self.assertEqual([u['page'] for u in model['unread']], [2])
        self.assertIn('-0.125', ' '.join(b['text'] for b in model['blocks']))


if __name__ == '__main__':
    unittest.main()
