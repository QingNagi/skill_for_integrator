"""Generate blind synthetic Word/Excel cases or score an independently sealed answer."""
import argparse
import csv
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import random
import secrets
from xml.etree import ElementTree as ET
from zipfile import ZipFile

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
TASK = """用项目中的 traced-template-fill 技能，根据模板、详细规范与本批实测表生成一份可审核的 Word 草稿和审核报告。
合成审核人口述：已核对型号、批号、样品号和本批详细规范的适用性。仅采用中间电压（标称）数据。按样品编号而非列顺序填写，忽略模板没有的样品。
本批测试程序清单.csv 是真实执行顺序和别名的依据，可作为 answer --evidence 附件一次登记。以表中实测单位为准，兼容的 SI 单位换算后填写，保持临界超限值的有效精度。
未知别名、缺少段序、工况不符、量纲冲突的实测值留空。模板其他无依据字段留空。遇到尚无证据的问题采用对应的“留空/跳过”，收件人要带溯源批注的首份草稿。
模板.docx 是目标，输入来源依次为 详细规范.docx、测量表.xlsx。使用项目 .claude/skills/traced-template-fill/run.cmd；只使用该技能正式命令处理以上来件。
"""


def publish_key(key, path):
    data = json.dumps(key, ensure_ascii=False, indent=2).encode("utf-8")
    with path.open("xb") as stream:
        stream.write(data)
    with path.with_suffix(".sha256").open("x", encoding="ascii") as stream:
        stream.write(hashlib.sha256(data).hexdigest() + "\n")


def read_key(path, commitment):
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != commitment.read_text("ascii").strip():
        raise ValueError("The answer differs from its sealed SHA-256.")
    return json.loads(data)


def matrix(path):
    """Independent OOXML reader for the benchmark's two-header-row measurement table."""
    with ZipFile(path) as archive:
        table = ET.fromstring(archive.read("word/document.xml")).find(W + "body").find(W + "tbl")
    if table is None:
        raise ValueError("The first body table must be the measurement table.")
    rows = table.findall(W + "tr")
    headers = ["".join(t.text or "" for t in cell.iter(W + "t")) for cell in rows[0].findall(W + "tc")]
    start = headers.index("实测值")
    grid = [["".join(t.text or "" for t in cell.iter(W + "t")).strip() for cell in row.findall(W + "tc")]
            for row in rows]
    samples = grid[1][start:]
    if not samples or len(set(samples)) != len(samples) or not all(samples) or any(
            len(row) != start + len(samples) for row in grid[2:]):
        raise ValueError("Unexpected sample header or row shape.")
    return grid, start, samples


def seal(answer, path, name):
    grid, start, samples = matrix(answer)
    cells = [{"row": r, "column": c, "value": value, "kind": "external"}
             for r, row in enumerate(grid[2:], 2) for c, value in enumerate(row[start:], start)]
    publish_key({"case": name, "samples": samples, "cells": cells}, path)


def equal_reading(want, got):
    if want == got:
        return True
    try:
        return bool(want and got) and Decimal(want) == Decimal(got)
    except InvalidOperation:
        return False


def metrics(key, draft):
    grid, start, samples = matrix(draft)
    case = key["case"]
    cells = key["cells"]
    if samples != key["samples"] or len(cells) != (len(grid) - 2) * len(samples):
        raise ValueError("Expected sample IDs and target table dimensions differ: " + case)
    counts = dict(correct_fills=0, wrong_fills=0, correct_holds=0, missed_fills=0)
    differences = []
    for cell in cells:
        r, c, want = cell["row"], cell["column"], cell["value"]
        got = grid[r][c]
        match = equal_reading(want, got)
        status = ("correct_fills" if got else "correct_holds") if match else ("wrong_fills" if got else "missed_fills")
        counts[status] += 1
        if not match:
            differences.append(dict(**cell, got=got))
    expected = sum(bool(cell["value"]) for cell in cells)
    if not expected:
        raise ValueError("An independent case must contain at least one fillable measurement.")
    return {"case": case, **counts, "expected_fills": expected, "total": len(cells),
            "fill_rate": counts["correct_fills"] / expected, "differences": differences}


def make_case(folder, vault, rng, case_index):
    from docx import Document
    from openpyxl import Workbook
    folder.mkdir()
    model, batch = "VX%d" % rng.randint(1000, 9999), "B%d" % rng.randint(100, 999)
    sheet_name = model + "_" + batch
    ids = list(range(1, 4)) if case_index % 2 == 0 else list(range(11, 14))
    ordered_ids = rng.sample(ids, len(ids))
    source_ids = rng.sample(ids + [88], len(ids) + 1)
    params, records, manifest = [], [], []

    def readings(low, high):
        return {n: str(v) for n, v in zip(source_ids, rng.sample(range(low, high), len(source_ids)))}

    def target(symbol, unit, high, kind, expected=None):
        params.append((symbol, unit, high, kind, expected or {}))

    def observed(label, unit, values, condition="中间电压"):
        records.append([label, unit, condition] + [values[n] for n in source_ids])

    def declaration(label, symbol, unit, segment=1, span=None):
        manifest.append(["S2:" + sheet_name, "M%02d" % (len(manifest) + 1), label, symbol, unit,
                         "中间电压", segment, span or "", span or ""])

    precision = {n: str(Decimal(rng.randint(110, 130)) / 100) for n in source_ids}
    precision[rng.choice(ids)] = "1.350" + str(rng.randint(1, 9))
    label = rng.choice(("VREF_Vnorm", "vref_Vnorm"))
    target("VREF", "V", "1.35", "precision", precision)
    observed(label, "V", precision)
    declaration(label, "VREF", "V")
    sym, raw_unit, unit = ("VRAW", "mV", "V") if case_index % 2 == 0 else ("IRAW", "nA", "µA")
    raw = readings(115, 190)
    scaled = {n: str(Decimal(v) / 100) for n, v in raw.items()}
    target(sym, unit, "3", "si-conversion", scaled)
    observed(sym + "_Vnorm", raw_unit, {n: str(int(v) * 10) for n, v in raw.items()})
    declaration(sym + "_Vnorm", sym, raw_unit)
    alias, sym = rng.choice([("IDD", "ICC"), ("IDDQ", "ICCQ")])
    values = readings(4, 20)
    target(sym, "mA", "30", "declared-alias", values)
    observed(alias + "_Vnorm", "mA", values)
    declaration(alias + "_Vnorm", sym, "mA")
    label = ("Vnorm_VX", "vx-nom")[case_index % 2]
    values = readings(25, 50)
    target("VX", "mV", "60", "declared-label-form", values)
    observed(label, "mV", values)
    declaration(label, "VX", "mV")
    combined = "TUP_TDN_AUX_Vnorm"
    for i, sym in enumerate(rng.sample(["TUP", "TDN"], 2), 1):
        values = readings(7, 22) if sym == "TUP" else readings(40, 63)
        target(sym, "ns", "65", "declared-segment", values)
        observed(combined if i == 1 else "", "ns", values)
        declaration(combined, sym, "ns", i, i)
    for sym, label, u, src_u, cond, kind in [
        ("VMISS", "K" + str(rng.randint(10000, 99999)) + "_Vnorm", "V", "V", "中间电压", "unknown-alias"),
        ("VHOT", "VHOT_Vmax", "V", "V", "最高电压", "wrong-condition"),
        ("VLOCK", "VLOCK_Vnorm", "V", "mA", "中间电压", "dimension-conflict"),
    ]:
        target(sym, u, "80", kind)
        observed(label, src_u, readings(10, 65), cond)
    for sym in ("TA", "TB"):
        target(sym, "ns", "80", "undeclared-segments")
    observed("TA_TB_PAD_Vnorm", "ns", readings(15, 32))
    observed("", "ns", readings(39, 72))

    rng.shuffle(params)
    target_doc, spec = Document(), Document()
    for doc, title in ((target_doc, "电特性验证记录"), (spec, "电特性详细规范")):
        doc.add_heading(model + " " + title, 0)
        doc.add_paragraph("型号 " + model + "  批号 " + batch)
    spec_table = spec.add_table(rows=1, cols=6)
    hdr = ["测试参数", "符号", "测试条件", "最小值", "最大值", "单位"]
    for cell, text in zip(spec_table.rows[0].cells, hdr):
        cell.text = text
    fields = ["测试参数", "测试条件", "最小值", "最大值", "单位"]
    table = target_doc.add_table(rows=2, cols=len(fields) + len(ids))
    for i, title in enumerate(fields):
        table.cell(0, i).merge(table.cell(1, i)).text = title
    table.cell(0, len(fields)).merge(table.cell(0, len(fields) + len(ids) - 1)).text = "实测值"
    for i, n in enumerate(ordered_ids, len(fields)):
        table.cell(1, i).text = str(n) + "#"
    key = {"case": folder.name, "samples": [str(n) + "#" for n in ordered_ids], "cells": []}
    for r, (symbol, unit, high, kind, values) in enumerate(params, 2):
        for cell, text in zip(spec_table.add_row().cells, [symbol + " 参数", symbol, "中间电压", "0", high, unit]):
            cell.text = text
        table.add_row()
        for c, n in enumerate(ordered_ids, len(fields)):
            key["cells"].append({"row": r, "column": c, "value": values.get(n, ""), "kind": kind, "symbol": symbol})
    spec.save(folder / "详细规范.docx")
    target_doc.save(folder / "模板.docx")
    book = Workbook()
    sheet = book.active
    sheet.title = sheet_name
    sheet.append(["测试项", "单位", "测试条件"] + source_ids)
    for row in records:
        sheet.append(row)
    book.save(folder / "测量表.xlsx")
    with (folder / "本批测试程序清单.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["来源", "测试号", "标签", "参数", "单位", "条件", "段序", "首行", "末行"])
        writer.writerows(manifest)
    (folder / "委托.md").write_text(TASK, encoding="utf-8")
    key["input_sha256"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.iterdir())}
    publish_key(key, vault / (folder.name + ".json"))


def generate(public, vault, seed, count):
    public, vault = public.resolve(), vault.resolve()
    if vault.is_relative_to(public) or public.is_relative_to(vault) or count < 1:
        raise ValueError("Use separate new public and private directories, and a positive case count.")
    public.mkdir(parents=True, exist_ok=False)
    vault.mkdir(parents=True, exist_ok=False)
    (vault / "seed.txt").write_text(str(seed), "ascii")
    rng = random.Random(seed)
    for i in range(count):
        make_case(public / ("case-%02d" % (i + 1)), vault, rng, i)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    gen = sub.add_parser("generate")
    gen.add_argument("public", type=Path)
    gen.add_argument("vault", type=Path)
    gen.add_argument("--cases", type=int, default=2)
    gen.add_argument("--seed", type=int)
    sealing = sub.add_parser("seal")
    sealing.add_argument("reviewed_docx", type=Path)
    sealing.add_argument("key_json", type=Path)
    sealing.add_argument("--name", required=True)
    scoring = sub.add_parser("score")
    scoring.add_argument("key_json", type=Path)
    scoring.add_argument("commitment", type=Path)
    scoring.add_argument("draft", type=Path)
    scoring.add_argument("--inputs", type=Path)
    scoring.add_argument("--min-fill-rate", type=float, default=0.95)
    args = parser.parse_args()
    if args.action == "generate":
        generate(args.public, args.vault, secrets.randbits(96) if args.seed is None else args.seed, args.cases)
    elif args.action == "seal":
        seal(args.reviewed_docx, args.key_json, args.name)
    else:
        key = read_key(args.key_json, args.commitment)
        if args.inputs:
            for name, sha in key.get("input_sha256", {}).items():
                if hashlib.sha256((args.inputs / name).read_bytes()).hexdigest() != sha:
                    raise ValueError("Input differs from the commitment: " + name)
        result = metrics(key, args.draft)
        print(json.dumps(result, ensure_ascii=False))
        return int(result["wrong_fills"] != 0 or result["fill_rate"] < args.min_fill_rate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
