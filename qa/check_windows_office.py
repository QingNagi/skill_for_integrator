"""Run Windows Office compatibility checks using real conversions and synthetic review data."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from xml.etree import ElementTree as ET
from zipfile import ZipFile


def document_info(path):
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with ZipFile(path) as doc:
        body = ET.fromstring(doc.read("word/document.xml"))
        comments = ET.fromstring(doc.read("word/comments.xml")) if "word/comments.xml" in doc.namelist() else []
        app = ET.fromstring(doc.read("docProps/app.xml")) if "docProps/app.xml" in doc.namelist() else ET.Element("Properties")
        props = ET.fromstring(doc.read("docProps/custom.xml")) if "docProps/custom.xml" in doc.namelist() else []
        generated = next(("".join(item.itertext()) for item in props if item.get("name") == "TTF-Generated"), None)
    return {
        "generated": generated,
        "producer": app.findtext("{*}Application", ""),
        "body": "".join(p.text or "" for p in body.iter(ns + "t")),
        "review_notes": ["".join(t.text or "" for t in c.iter(ns + "t")).strip() for c in comments],
        "review_authors": [c.get(ns + "author", "") for c in comments],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entry", required=True, type=Path, help="Path to run.cmd or scripts/di.py")
    parser.add_argument("--backend", required=True, choices=["word", "libreoffice"])
    parser.add_argument("--native-edits", action="store_true", help="Use the actual editor to save a body edit and QA annotation")
    parser.add_argument("--soffice", type=Path, help="Isolated LibreOffice program/soffice.exe")
    parser.add_argument("--output", type=Path, required=True, help="New directory for synthetic evidence")
    args = parser.parse_args()
    target_dir = args.output.resolve()
    target_dir.mkdir(parents=True, exist_ok=False)
    entry = args.entry.resolve()
    launcher = [sys.executable, "-X", "utf8", str(entry)] if entry.suffix == ".py" else [str(entry)]
    env = dict(os.environ, DI_OFFICE=args.backend, DI_UTF8="1", PYTHONUTF8="1")
    if args.backend == "libreoffice":
        if not args.soffice or not args.soffice.is_file():
            parser.error("Provide the actual isolated LibreOffice executable")
        env["PATH"] = str(args.soffice.resolve().parent) + os.pathsep + env["PATH"]
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
    from test_selfcheck import COMMENTS, doc_xml, make_docx
    from pypdf import PdfReader

    heading = '<w:pPr><w:outlineLvl w:val="0"/></w:pPr>'
    template_xml = ('<w:p>' + heading + '<w:commentRangeStart w:id="0"/><w:r><w:t>一、交接记录</w:t></w:r>'
                    '<w:commentRangeEnd w:id="0"/><w:r><w:commentReference w:id="0"/></w:r></w:p><w:p/>')
    sample_xml = ('<w:p>' + heading + '<w:r><w:t>1 项目背景</w:t></w:r></w:p>'
                  '<w:p><w:r><w:t>TRACEVOLT 17.8 V 交接。</w:t></w:r></w:p>')
    target, source = target_dir / "模板.docx", target_dir / "研制任务书.docx"
    make_docx(target, {"word/document.xml": doc_xml(template_xml),
                       "word/comments.xml": COMMENTS.replace("note", "研制任务书中的项目背景")})
    make_docx(source, {"word/document.xml": doc_xml(sample_xml)})
    originals = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (target, source)]
    steps = {}
    def invoke(tag, *command):
        start = time.monotonic()
        result = subprocess.run(launcher + list(map(str, command)), cwd=target_dir, env=env, capture_output=True,
                                encoding="utf-8", errors="replace", timeout=220)
        (target_dir / (tag + ".log")).write_text(result.stdout + result.stderr, encoding="utf-8")
        if result.returncode:
            raise RuntimeError(f"{tag} exited {result.returncode}: see {target_dir / (tag + '.log')}")
        steps[tag] = round(time.monotonic() - start, 3)

    invoke("draft", "run", "--draft", "-t", target, "-s", source, "--task", "按批注摘录项目背景", "--name", "验收")
    job = target_dir / "整合输出" / "验收"
    draft, = (job / "草稿").glob("*.docx")
    saved_marker = None
    if args.native_edits:
        helpers = Path(__file__).resolve().parent
        editor = ([os.path.join(os.environ["SystemRoot"], "System32/WindowsPowerShell/v1.0/powershell.exe"),
                   "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(helpers / "edit_word_review.ps1")]
                  if args.backend == "word" else [str(args.soffice.with_name("python.exe")), str(helpers / "edit_lo_review.py")])
        subprocess.run(editor + [str(draft)], env=env, check=True, timeout=120)
        saved = document_info(draft)
        saved_marker = saved["generated"]
        assert "QA-EDIT-8642" in saved["body"] and any("QA reviewer annotation" in note for note in saved["review_notes"])
        assert "QA Editor" in saved["review_authors"]
    invoke("review", "review", job)
    invoke("final", "final", job, "--template-comments", "keep", "--reviewer-comments", "keep")
    final, = (job / "终稿").glob("*.docx")
    if args.native_edits:
        saved = document_info(final)
        assert "QA-EDIT-8642" in saved["body"] and any("QA reviewer annotation" in note for note in saved["review_notes"])
        assert "QA Editor" in saved["review_authors"]
    # Exercise both real readers/writers in an output path whose name contains non-ASCII characters.
    invalid_temp = target_dir / "模拟不可用的临时路径"
    invalid_temp.write_text("not a directory", encoding="ascii")
    env.update(TEMP=str(invalid_temp), TMP=str(invalid_temp))
    legacy, roundtrip, pdf = (target_dir / name for name in ("审阅稿.doc", "往返.docx", "交付.pdf"))
    invoke("docx_to_doc", "convert", draft, "doc", "--out", legacy)
    invoke("doc_to_docx", "convert", legacy, "docx", "--out", roundtrip)
    invoke("final_pdf", "convert", final, "pdf", "--out", pdf)
    assert legacy.read_bytes()[:8] == bytes.fromhex("d0cf11e0a1b11ae1")
    info = document_info(roundtrip)
    assert "17.8" in info["body"] and any("项目背景" in c for c in info["review_notes"])
    pdfdoc = PdfReader(pdf)
    assert "17.8" in "".join(p.extract_text() for p in pdfdoc.pages)
    producer = " ".join(str(pdfdoc.metadata.get(k, "")) for k in ("/Creator", "/Producer"))
    expected = "Word" if args.backend == "word" else "LibreOffice"
    assert expected.casefold() in info["producer"].casefold(), info["producer"]
    assert expected.casefold() in producer.casefold(), producer
    assert originals == [hashlib.sha256(p.read_bytes()).hexdigest() for p in (target, source)]
    report = {"backend":args.backend, "docx_producer":info["producer"], "pdf_producer":producer,
              "review_notes_after_doc_roundtrip":len(info["review_notes"]), "pdf_pages":len(pdfdoc.pages),
              "originals_unchanged":True, "native_edit_and_annotation_retained":args.native_edits,
              "generated_property_after_editor_save": saved_marker, "generated_property_after_doc_roundtrip": info["generated"],
              "seconds":steps}
    (target_dir / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
