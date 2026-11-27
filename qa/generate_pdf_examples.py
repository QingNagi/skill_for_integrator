"""Generate synthetic PDFs and a Word template for manual offline acceptance."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from pdf_samples import make_scan, make_table
from test_selfcheck import COMMENTS, doc_xml, make_docx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="New directory for the sample documents")
    root = parser.parse_args().destination
    root.mkdir(parents=True, exist_ok=False)
    make_table(root / "量程规范.pdf")
    make_scan(root / "扫描交接单.pdf")
    heading = ('<w:p><w:pPr><w:outlineLvl w:val="0"/></w:pPr>'
               '<w:commentRangeStart w:id="0"/><w:r><w:t>一、量程记录</w:t></w:r>'
               '<w:commentRangeEnd w:id="0"/><w:r><w:commentReference w:id="0"/></w:r></w:p><w:p/>')
    make_docx(root / "验收模板.docx", {"word/document.xml": doc_xml(heading),
              "word/comments.xml": COMMENTS.replace("note", "按量程规范摘录限值和单位，注明页码")})
    (root / "怎么测试.md").write_text(
        "# PDF 与 OCR 离线自测\n\n"
        "在这个目录启动 Agent，给它这条任务。\n\n"
        "> 用 traced-template-fill，以验收模板.docx 为目标、量程规范.pdf 和扫描交接单.pdf 为来源。"
        "先列出合并表头的上下限和单位，按批注填写。核对扫描来源的页码，保留待确认问题让我审核。\n\n"
        "| 核对项目 | 预期 |\n|---|---|\n"
        "| 量程规范第 1 页 | 表头 Measured limits 跨两列，Unit 跨两行。"
        "数据行为 -0.125、1.250、mV，注意负号和末尾零。 |\n"
        "| 量程规范第 2 页 | 故意留空。审核报告应指出没有读到内容。 |\n"
        "| 扫描交接单第 1、2 页 | 包含相同的中英文扫描文字，标注 OCR 和原页。"
        "第 2 页的 Native header for mixed page 为原生文字。 |\n"
        "| 扫描交接单第 3 页 | Native content only 为原生文字。 |\n"
        "| 定稿 | 来源或扫描值仍待确认时停下来，保留原件。 |\n\n"
        "可让 Agent 用同一任务的 show 一次回读 S1:page1、S2:page1、S2:page2、S2:page3。"
        "别名按上面两份来源的输入顺序分配。\n", encoding="utf-8")
    print(root.resolve())


if __name__ == "__main__":
    main()
