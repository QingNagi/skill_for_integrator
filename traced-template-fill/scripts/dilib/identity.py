"""读取模板与试验记录明确标注的身份字段。表内数值及样品列不作为批号。"""
import re

MODEL = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2,}[A-Z0-9-]*\d[A-Z0-9-]*(?![A-Za-z0-9])")
LABELS = {
    "model": r"(?:器件|产品|规格)?型号|model|part\s*(?:number|no\.?)",
    "batch": r"(?:产品|生产|试验|测试|检验)?批(?:次号|号|次)|lot(?:\s*(?:id|no\.?|number))?|batch(?:\s*(?:id|no\.?|number))?",
    "date": r"(?:试验|测试|检验)日期|test\s*date",
}


def model_codes(text):
    return {code for code in MODEL.findall(str(text)) if len(code) >= 6}


def model_rows(model):
    for block in model.m.get("blocks", []):
        if block["t"] == "tbl":
            rows = {}
            for c in block.get("cells", []):
                rows.setdefault(c["r"], {})[c["c"]] = c.get("text", "")
            for row in rows.values():
                yield [row[k] for k in sorted(row)]
        elif block["t"] == "p":
            yield [block.get("text", "")]


def identity_fields(rows):
    fields = {key: set() for key in LABELS}
    for row in rows:
        for index, cell in enumerate(row):
            text = str(cell or "").strip()
            if not text:
                continue
            for key, pattern in LABELS.items():
                match = re.search(r"(?<![\w])(?:" + pattern + r")(?:\s*[：:=]\s*|\s+)([A-Za-z0-9][A-Za-z0-9./_-]*)", text, re.I)
                if match:
                    raw = match[1]
                elif re.fullmatch(r"(?:" + pattern + r")\s*[：:]?", text, re.I) and index + 1 < len(row):
                    raw = str(row[index + 1] or "").strip()
                else:
                    continue
                if key == "model":
                    fields[key].update(model_codes(raw))
                elif raw and len(raw) <= 80:
                    fields[key].add(raw)
    return fields


def document_identity(model):
    fields = identity_fields(model_rows(model))
    for field in ("title", "name"):
        fields["model"].update(model_codes(model.m.get(field, "")))
    # A specification cover often contains the product code without a field label.
    cover = [b.get("text", "") for b in model.m.get("blocks", []) if b["t"] == "p"][:8]
    for line in cover:
        if len(line) <= 160:
            fields["model"].update(model_codes(line))
    fields["model"].difference_update(fields["batch"])
    return fields


def sheet_identity(book, sheet, template=None):
    name = sheet.get("name", "")
    fields = identity_fields([[name], *sheet.get("rows", [])[:12]])
    fields["model"].update(model_codes(book.m.get("name", "")))
    fields["model"].update(model_codes(name))
    fields["model"].difference_update(fields["batch"])
    if template:
        fields["model"].difference_update(template["batch"])
        if not fields["batch"] and len(template["batch"]) == 1 and len(fields["model"]) == 1 and fields["model"] == template["model"]:
            batch = next(iter(template["batch"]))
            token = r"(?<![A-Za-z0-9])" + re.escape(batch) + r"(?![A-Za-z0-9])"
            if re.search(token, name) and re.search(token, book.m.get("name", "")):
                fields["batch"].add(batch)
    return fields
