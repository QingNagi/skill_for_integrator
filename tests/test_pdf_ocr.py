"""Exercise offline OCR with searchable, scanned, and mixed PDF pages."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from dilib import _bootstrap, content
from dilib.cli import do_plan, write_report
from dilib.job import Job
from dilib.read_other import read_pdf
from test_selfcheck import make_docx


def make_scan(engine, path):
    with engine.open() as sheet, engine.open() as pdf:
        scan = sheet.new_page(width=420, height=260)
        scan.insert_text((25, 90), 'Measured voltage 128 V', fontsize=27)
        scan.insert_text((25, 150), '测试电压', fontname='china-s', fontsize=28)
        raster = scan.get_pixmap(dpi=175).tobytes('png')
        for i in range(3):
            page = pdf.new_page(width=420, height=320)
            if i < 2:
                page.insert_image(engine.Rect(0, 40, 420, 300), stream=raster)
            if i == 1:
                page.insert_text((12, 20), 'Native header for mixed page', fontsize=13)
            if i == 2:
                page.insert_text((12, 80), 'Native content only', fontsize=18)
        pdf.save(path)


class PdfOcrTests(unittest.TestCase):
    def test_ocr_failure_is_reported_and_other_pages_survive(self):
        engine = _bootstrap.use_pymupdf()
        if not engine:
            self.skipTest('Native PDF runtime required')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'scan.pdf'
            make_scan(engine, path)
            for outcome in ([], ValueError('recognition failed')):
                def recognize(*args):
                    if isinstance(outcome, Exception):
                        raise outcome
                    return list(outcome)
                with self.subTest(outcome=str(outcome)), patch('dilib.read_other._recognize_pdf_page', recognize):
                    model = read_pdf(str(path), ocr={'available': True})
                    self.assertEqual([u['page'] for u in model['unread']], [1, 2])
                    self.assertEqual(model['ocr_pages'], [])
                    self.assertTrue(any(b['text'] == 'Native content only' for b in model['blocks']))

    def test_toggle_reparses_same_pdf_and_preserves_native_content(self):
        engine = _bootstrap.use_pymupdf()
        data_dir = Path(os.environ.get('DI_TEST_TESSDATA', Path(_bootstrap._ROOT) / 'runtime' / 'tessdata'))
        if engine is None or not all((data_dir / (name + '.traineddata')).is_file()
                                     for name in ('chi_sim', 'eng')):
            self.skipTest('Offline OCR runtime and language models required')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'scan.pdf'
            make_scan(engine, path)
            with patch.object(_bootstrap, '_ROOT', str(data_dir.parent.parent)):
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
                self.assertIn('128', joined)
                self.assertIn('电压', joined.replace(' ', ''))
                self.assertNotIn('Native header', joined)
            shown = content.render_block(content.Model(result, 'S1'), recognized[0]['id'])
            self.assertIn('OCR', shown)
            self.assertIn('第 1 页', shown)


if __name__ == '__main__':
    unittest.main()
