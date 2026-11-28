"""Create five synthetic Word/Excel cases, seal their expected cells, then score actual drafts.

Developer dependencies for fixture creation only: python-docx and openpyxl.
generate and score are separate so an independent reviewer can replace and seal their own key.
"""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

# Sample IDs 1 through 5. Expectations are specified separately from workbook records.
PARAMETERS = [
    ("参考电压", "VREF", "2", "5", "V", ["2.41", "2.53", "2.62", "2.78", "2.89"]),
    ("工作电流", "ICC", "2", "15", "mA", [""] * 5),
    ("通道电阻", "RIN", "50", "120", "Ω", ["81~82", "84~85", "", "93~94", "97~99"]),
    ("低电平阈值", "VNEG", "-5", "-1", "V", [""] * 5),
    ("上升延时", "TRISE", "1", "18", "ns", [""] * 5),
    ("下降延时", "TFALL", "1", "18", "ns", [""] * 5),
    ("采样率", "RATE", "800", "1200", "Mbps", [""] * 5),
    ("时钟读数", "FCLK", "50", "110", "MHz", ["75", "76", "77", "78", "79"]),
]
READINGS = [
    ("VREF_Vnorm", [2.41, 2.53, 2.62, 2.78, 2.89]),
    ("VREF_Vmax", [3.21, 3.22, 3.23, 3.24, 3.25]),
    ("ICC", [4, 5, 6, 7, 8]),
    ("RIN_Vnorm", [81, 84, 89, 93, 97]), ("", [82, 85, "FAILED", 94, 99]),
    ("VNEG_Vnorm", [3, 3.1, 3.2, 3.3, 3.4]),
    ("TRISE_TFALL_SKEW_Vnorm", [2, 3, 4, 5, 6]), ("", [7, 8, 9, 10, 11]),
    ("RATE_Vnorm", [1, 0, 1, 1, 1]),
    ("FCLK_Vnorm", [75, 76, 77, 78, 79]),
]
TASK = "填写电特性记录；按样品号，只使用末尾 _Vnorm 的中间电压数据，重复值写最小值~最大值。未知条件、归属、极性以及仅有状态的实测位置留空。"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def rotated(items, shift):
    return items[shift:] + items[:shift]


def generate(root):
    from docx import Document
    from openpyxl import Workbook
    root.mkdir(parents=True, exist_ok=False)
    key = {}
    for index in range(5):
        name = "示例" + str(index + 1)
        incoming = root / name
        incoming.mkdir()
        model = "DEMO" + str(4100 + index)
        batch = "L" + str(120 + index)
        identity = "型号 " + model + "  批号 " + batch
        cols = rotated([1, 2, 3, 4, 5], index)
        order = rotated(PARAMETERS, index)
        target, spec = Document(), Document()
        for doc, title in ((target, "电特性验证记录"), (spec, "电特性详细规范")):
            doc.add_heading(model + " " + title, 0)
            doc.add_paragraph(identity)
        spec_table = spec.add_table(rows=1, cols=6)
        for cell, text in zip(spec_table.rows[0].cells, ["测试参数", "符号", "测试条件", "最小值", "最大值", "单位"]):
            cell.text = text
        fields = ["测试参数", "测试条件", "最小值", "最大值", "单位"]
        table = target.add_table(rows=2, cols=len(fields) + len(cols))
        for i, title in enumerate(fields):
            table.cell(0, i).merge(table.cell(1, i)).text = title
        table.cell(0, len(fields)).merge(table.cell(0, len(fields) + len(cols) - 1)).text = "实测值"
        for i, sample in enumerate(cols, len(fields)):
            table.cell(1, i).text = str(sample) + "#"
        cells = []
        for label, symbol, lo, hi, unit, expected in order:
            values = [label, symbol, "中间电压", lo, hi, unit]
            for cell, text in zip(spec_table.add_row().cells, values):
                cell.text = text
            table.add_row()
            cells.append([expected[n - 1] for n in cols])
        for doc, fname in ((target, "模板.docx"), (spec, "详细规范.docx")):
            doc.save(incoming / fname)
        book = Workbook()
        sheet = book.active
        sheet.title = model + "_" + batch
        positions = list(reversed(cols))
        sheet.append(["测试项"] + positions)
        for label, values in READINGS:
            sheet.append([label] + [values[i - 1] for i in positions])
        book.save(incoming / (model + " " + batch + " 测量表.xlsx"))
        key[name] = cells
    data = json.dumps(key, ensure_ascii=False, indent=2).encode("utf-8")
    (root / "expected.json").write_bytes(data)
    (root / "expected.sha256").write_text(hashlib.sha256(data).hexdigest() + "\n", encoding="ascii")
    (root / "任务.md").write_text(TASK + "\n", encoding="utf-8")


def score(root, outputs):
    data = (root / "expected.json").read_bytes()
    if hashlib.sha256(data).hexdigest() != (root / "expected.sha256").read_text(encoding="ascii").strip():
        raise ValueError("封存的答案摘要不匹配")
    counts = Counter({k: 0 for k in ("正确填写", "错误填写", "正确留空", "错误留空")})
    differences = []
    for name, key in json.loads(data).items():
        docs = list((outputs / name / "草稿").glob("*_v1.docx"))
        if len(docs) != 1:
            raise ValueError("需要一份实际草稿：" + name)
        with ZipFile(docs[0]) as archive:
            table = ET.fromstring(archive.read("word/document.xml")).find(W + "body").find(W + "tbl")
            rows = table.findall(W + "tr")[2:]
            if len(rows) != len(key):
                raise ValueError("目标表行数发生变化：" + name)
            for r, (row, expected) in enumerate(zip(rows, key), 2):
                cells = row.findall(W + "tc")
                if len(cells) != 5 + len(expected):
                    raise ValueError("目标表列数发生变化：" + name)
                for c, (cell, want) in enumerate(zip(cells[5:], expected), 5):
                    got = "".join(t.text or "" for t in cell.iter(W + "t"))
                    counts[("正确" if got == want else "错误") + ("填写" if got else "留空")] += 1
                    if got != want:
                        differences.append([name, r, c, got, want])
    print(json.dumps({"counts": counts, "differences": differences}, ensure_ascii=False))
    return bool(differences)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("generate").add_argument("directory", type=Path)
    scoring = sub.add_parser("score")
    scoring.add_argument("directory", type=Path)
    scoring.add_argument("outputs", type=Path)
    args = parser.parse_args()
    raise SystemExit(generate(args.directory) if args.action == "generate" else score(args.directory, args.outputs))
