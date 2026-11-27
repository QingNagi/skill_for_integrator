#!/usr/bin/env python3
"""Exercise the deployed interpreter, both PDF backends and the bilingual OCR reader."""
import os
import sys
import tempfile
from pathlib import Path

from dilib import _bootstrap
from dilib.common import setup_console
from dilib.read_other import read_pdf


NATIVE_TEXT_ORIGIN = (50, 80)
NATIVE_FONT_POINTS = 16
IMAGE_PIXELS_PER_POINT = 2


def check():
    root = Path(__file__).resolve().parents[1]
    runtime = root / "runtime" / "python"
    interpreter = runtime / ("python.exe" if os.name == "nt" else "bin/python3")
    if Path(sys.executable).resolve() != interpreter.resolve():
        raise RuntimeError("请用本技能随附的 Python 运行验收。")
    fitz = _bootstrap.use_pymupdf()
    fallback = _bootstrap.use_pypdf()
    if fitz is None or fallback is None:
        raise RuntimeError("PDF 后端加载失败：" + "；".join(_bootstrap.notes()))

    # The image contains only synthetic sample text. Generating the PDF locally
    # lets the test distinguish native glyphs from recognized raster text.
    image = (root / "tests" / "fixtures" / "offline-ocr.png").read_bytes()
    with tempfile.TemporaryDirectory(prefix="di_check_") as tmp:
        pdf_path = Path(tmp) / "bilingual.pdf"
        with fitz.open() as pdf:
            page = pdf.new_page()
            page.insert_text(NATIVE_TEXT_ORIGIN, "Native voltage 4.75 V", fontsize=NATIVE_FONT_POINTS)
            pix = fitz.Pixmap(image)
            page = pdf.new_page(width=pix.width / IMAGE_PIXELS_PER_POINT, height=pix.height / IMAGE_PIXELS_PER_POINT)
            page.insert_image(page.rect, stream=image)
            pdf.save(str(pdf_path), deflate=True)
        with pdf_path.open("rb") as stream:
            pages = fallback.PdfReader(stream).pages
            if len(pages) != 2 or pages[1].extract_text().strip():
                raise RuntimeError("自检 PDF 的第二页应为仅含图像的扫描页。")
        result = read_pdf(str(pdf_path))
        native = " ".join(b.get("text", "") for b in result["blocks"] if b.get("page") == 1)
        recognized = "".join(b.get("text", "") for b in result["blocks"]
                             if b.get("page") == 2 and b.get("ocr"))
        text = "".join(recognized.split())
        if "4.75 V" not in native or result.get("ocr_pages") != [2]:
            raise RuntimeError("原生文本或扫描页页码校验失败。")
        if "中文离线识别" not in text or "OFFLINEDOCUMENT2026" not in text.upper():
            raise RuntimeError("简体中文/英文 OCR 自检未通过：" + recognized)
        print("离线自检通过：文本页 4.75 V；第 2 页中英文 OCR；两个 PDF 后端均可读取。")


if __name__ == "__main__":
    setup_console()
    try:
        check()
    except Exception as exc:
        print("离线自检失败：%s" % exc, file=sys.stderr)
        sys.exit(1)
