"""Repeatable OCR evidence and region-cache regressions."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_selfcheck as fx
from pdf_samples import make_pdf, make_scan
from dilib import _bootstrap
from dilib.draft_review import write_report
from dilib.job import Job
from dilib.pdf_ocr import parse_tsv, review_groups
from dilib.read_other import read_pdf


def line_result():
    return [{"text": "17.8 mV", "bbox": [80, 30, 145, 65], "words": [],
             "confidence": 96, "numeric_warnings": [], "text_warnings": []}]


class OcrEvidence(unittest.TestCase):
    def test_low_confidence_measurement_parts_are_named_separately(self):
        header = "level page_num block_num par_num line_num word_num left top width height conf text"
        rows = [header] + ["5 1 1 1 1 %d 10 10 10 6 51 %s" % (i, text) for i, text in
                           enumerate(["-0.125", "µA", "−", ".", "方法"], 1)]
        line, = parse_tsv("\n".join(r.replace(" ", "\t") for r in rows))
        groups = {name: [w["text"] for w in words] for name, words in review_groups(line)}
        self.assertEqual(groups.get("数字"), ["-0.125"])
        self.assertEqual(groups.get("单位"), ["µA"])
        self.assertEqual(groups.get("小数点"), ["-0.125", "."])
        self.assertEqual(groups.get("正负号"), ["-0.125", "−"])
        self.assertEqual(groups["文字"], ["方法"])

    def test_mixed_threshold_changes_signature_and_ambiguous_overlap_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "mixed.pdf")
            make_pdf(path, [('q 350 0 0 125 30 64 cm /Im Do Q '
                             'BT /F1 14 Tf 33 110 Td (Digital label inside image) Tj ET', '')], with_image=True)
            with patch.dict(os.environ, DI_OCR_MIXED_MIN_AREA="0.25"):
                first = _bootstrap.ocr_hint()
            with patch.dict(os.environ, DI_OCR_MIXED_MIN_AREA="0.4"):
                second = _bootstrap.ocr_hint()
            self.assertNotEqual(first["signature"], second["signature"])
            m = read_pdf(path, ocr=dict(first, available=True))
            self.assertIn("重叠", str(m["unread"]))

    def test_cache_scopes_post_mask_pixels_engine_language_and_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, other = Path(tmp, "sample.pdf"), Path(tmp, "other.pdf")
            make_scan(source)
            # Only two native glyphs overlay the scan. Masking them changes the actual OCR pixels.
            make_pdf(other, [('q 350 0 0 125 30 64 cm /Im Do Q BT /F1 14 Tf 55 108 Td (A1) Tj ET','')], with_image=True)
            opts = {"available": True, "ocr_identity": "engine-model-A", "language": "eng",
                    "ocr_cache_dir": str(Path(tmp, "cache"))}
            with patch("dilib.pdf_reader.recognize_image", side_effect=lambda *_: line_result()) as ocr:
                m = read_pdf(source, str(Path(tmp, "images")), ocr=opts)
                self.assertEqual(ocr.call_count, 1)
                self.assertEqual(len([b for b in m["blocks"] if b.get("ocr")]), 2)
                read_pdf(source, ocr=opts)
                self.assertEqual(ocr.call_count, 1)
                for changed in (dict(opts, ocr_identity="engine-model-B"), dict(opts, language="chi_sim"),
                                dict(opts, dpi=96)):
                    read_pdf(source, ocr=changed)
                self.assertEqual(ocr.call_count, 4)
                read_pdf(other, ocr=opts)
                self.assertEqual(ocr.call_count, 5)

    def test_cache_cleanup_removes_only_derived_ocr_results(self):
        import contextlib
        import io
        from types import SimpleNamespace
        from dilib.cleanup import cmd_clean
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp, "_缓存", "ocr")
            folder.mkdir(parents=True)
            cache = folder / ("f" * 64 + ".json")
            unrelated = folder / "mine.json"
            cache.write_text("[]")
            unrelated.write_text("keep")
            with contextlib.redirect_stdout(io.StringIO()):
                cmd_clean(SimpleNamespace(out=tmp, apply=True))
            self.assertFalse(cache.exists())
            self.assertEqual(unrelated.read_text(), "keep")

    def test_report_keeps_unmasked_pixel_crop_in_source_snapshot(self):
        from PIL import Image, ImageChops
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp, "scan.pdf"), Path(tmp, "target.docx")
            make_scan(source)
            fx.make_docx(target)
            opts = {"available": True, "signature": "fixture-v1", "ocr_identity": "fixture-engine-model",
                    "language": "eng", "pinned_reader": False}
            with patch.object(_bootstrap, "ocr_hint", return_value=opts), patch(
                    'dilib.pdf_reader.recognize_image', side_effect=lambda *_: line_result()):
                job = Job.create(str(target), [str(source)], out_root=tmp)
                model = next(iter(job.source_models().values()))
                block = next(b for b in model.m['blocks'] if b.get('ocr'))
                ref = block.get("ocr_region")
                self.assertTrue(ref, "OCR line must keep its exact source image and pixel box")
                plan = {"report": {"fills": [], "unfilled": [], "ignored": [], "warnings": []},
                        "summary": [], "questions": []}
                write_report(job, plan, job.p("草稿", "draft.docx"), 1, [])
                report = Path(job.p("审核报告.md")).read_text(encoding="utf-8")
                snapshot = Path(job.p("草稿", "源文件快照", "OCR"))
                crop = next(snapshot.glob("*-" + block["id"] + "-*.png"))
                self.assertIn(crop.name, report)
                with Image.open(model.m['images'][ref['image']]['file']) as original, Image.open(crop) as result:
                    expected = original.crop(ref['pixels'])
                    self.assertIsNone(ImageChops.difference(expected, result).getbbox())
