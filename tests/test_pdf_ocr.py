"""Exercise offline OCR with searchable, scanned, and mixed PDF pages."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'traced-template-fill' / 'scripts'))
from dilib import _bootstrap, content
from dilib.cli import do_plan, write_report
from dilib.job import Job
from dilib.read_other import read_pdf
from test_selfcheck import make_docx
from pdf_samples import make_scan


class PdfOcrTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _bootstrap.activate_vendor()

    def test_tsv_marks_only_low_confidence_numbers_without_altering_the_source(self):
        from dilib.pdf_ocr import parse_tsv
        rows = ['level page_num block_num par_num line_num word_num left top width height conf text',
                '4 1 1 1 1 0 20 30 300 15 -1 ',
                '5 1 1 1 1 1 20 30 80 15 64.5 -0.125',
                '5 1 1 1 1 2 120 30 70 15 94 1.250',
                '5 1 1 1 1 3 210 30 30 15 60 mV']
        line, = parse_tsv('\n'.join(r.replace(' ', '\t') for r in rows))
        self.assertEqual(line['text'], '-0.125 1.250 mV')
        self.assertEqual(line['confidence'], 60)
        self.assertEqual(line['bbox'], [20, 30, 240, 45])
        self.assertEqual(line['numeric_warnings'], [{'text': '-0.125', 'confidence': 64.5}])
        model = content.Model({'type': 'pdf', 'blocks': [dict(line, t='p', id='P0', ocr=True, page=2)]}, 'S1')
        shown = content.render_block(model, 'P0')
        self.assertIn('⚠', shown)
        self.assertIn('-0.125（64.5', shown)
        self.assertTrue(shown.endswith('-0.125 1.250 mV'))

    def test_scan_ocr_preserves_source_pixel_grid_and_skips_native_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '测试扫描页.pdf'
            make_scan(path)
            seen = []
            def record(image, options):
                seen.append(image.size)
                return []
            with patch('dilib.pdf_reader.recognize_image', record):
                read_pdf(path, ocr={'available': True})
            self.assertEqual(seen, [(700, 250), (700, 250)])

    def test_ocr_failure_is_reported_and_other_pages_survive(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'scan.pdf'
            make_scan(path)
            for outcome in ([], ValueError('recognition failed')):
                def recognize(*args):
                    if isinstance(outcome, Exception):
                        raise outcome
                    return list(outcome)
                with self.subTest(outcome=str(outcome)), patch('dilib.pdf_reader.recognize_image', recognize):
                    model = read_pdf(str(path), ocr={'available': True})
                    self.assertEqual([u['page'] for u in model['unread']], [1, 2])
                    self.assertEqual(model['ocr_pages'], [])
                    self.assertTrue(any(b['text'] == 'Native content only' for b in model['blocks']))

    def test_toggle_reparses_same_pdf_and_preserves_native_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'scan.pdf'
            make_scan(path)
            with patch.dict(os.environ, DI_OCR='off'):
                disabled = content.load_model(str(path), tmp)
            with patch.dict(os.environ, DI_OCR='on'):
                result = content.load_model(str(path), tmp)
                self.assertTrue(_bootstrap.ocr_hint(probe=True)['available'])
                target = Path(tmp) / 'target.docx'
                make_docx(target)
                job = Job.create(str(target), [str(path)], out_root=tmp)
                plan = do_plan(job, quiet=True)
                q = next(q for q in plan['questions'] if q['topic'] == 'OCR 原页复核')
                write_report(job, plan, 'review.docx', 1, [])
                report = Path(job.p('审核报告.md')).read_text(encoding='utf-8')
                self.assertIn('第 1、2 页使用 OCR', report)
                self.assertIn('单位', q['question'])
                key = q['key']
                self.assertTrue(any(x['key'] == key for x in plan['report']['unverified_sources']))
                job.write('answers.json', {key: {'effect': {'ok': True}, 'fp': job.fingerprint()}})
                confirmed = do_plan(job, quiet=True)['report']
                self.assertFalse(any(x['key'] == key for x in confirmed.get('unverified_sources', [])))
            self.assertEqual([u['page'] for u in disabled['unread']], [1, 2])
            self.assertEqual(result['unread'], [])
            native = lambda m: [(b['id'], b['text']) for b in m['blocks'] if not b.get('ocr')]
            self.assertEqual(native(disabled), native(result))
            recognized = [b for b in result['blocks'] if b.get('ocr')]
            self.assertEqual({b['page'] for b in recognized}, {1, 2})
            for pno in (1, 2):
                joined = ' '.join(b['text'] for b in recognized if b['page'] == pno)
                self.assertIn('2026', joined)
                self.assertIn('中文离线识别', joined.replace(' ', ''))
                self.assertNotIn('Native header', joined)
            self.assertTrue(all(0 <= b['confidence'] <= 100 and len(b['bbox']) == 4 for b in recognized))
            shown = content.render_block(content.Model(result, 'S1'), recognized[0]['id'])
            self.assertIn('OCR', shown)
            self.assertIn('第 1 页', shown)


if __name__ == '__main__':
    unittest.main()
