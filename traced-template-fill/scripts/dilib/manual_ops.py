"""Bind positional instructions to their Word template and reject stale anchors before planning."""
import re

from .common import DIError, short


def target_guard(op, model):
    if not isinstance(op, dict):
        raise DIError("手工操作必须是 JSON 对象")
    typ = op.get("op")
    if typ == "set_text":
        targets = [(op.get("cell") or op.get("block"), ("p", "cell"))]
    elif typ in ("set_cell", "append_runs", "fill_blanks"):
        targets = [(op.get("cell"), ("cell",))]
    elif typ in ("insert_section", "add_section"):
        targets = [(op.get("anchor"), ("p",))]
        if not isinstance(op.get("clear", []), list):
            raise DIError("章节操作的 clear 必须是目标位置数组")
        targets.extend((ref, ("p", "tbl")) for ref in op.get("clear", []))
    elif typ == "comment":
        targets = [(op.get("anchor"), ("p", "cell", "tbl", "row"))]
    elif typ == "format_heading":
        targets = [(op.get("block"), ("p",))]
    else:
        raise DIError("未知手工操作类型：%s" % typ)
    refs, originals = [], []
    for ref, allowed in targets:
        if not isinstance(ref, str):
            raise DIError("手工操作 %s 缺少目标位置" % typ)
        obj = model.get(ref)
        kind = "cell" if ref in model.cells else (obj or {}).get("t")
        if kind is None and "row" in allowed:
            row = re.fullmatch(r"(.+)\.R(\d+)", ref)
            table = model.by.get(row[1]) if row else None
            if table and table.get("t") == "tbl" and int(row[2]) < table["nrows"]:
                kind = "row"
        if kind not in allowed:
            raise DIError("手工操作 %s 找不到适用的模板位置 %s，请用 outline 核对。" % (typ, ref))
        refs.append(ref)
        originals.append(short((obj or {}).get("text", ""), 64))
    return {"template_sha1": model.m["sha1"], "refs": refs, "original_text": originals}


def record_ops(job, ops, replace=False):
    model = job.target_model()
    if not replace:
        validate_ops([op for op in ops if isinstance(op, dict) and "target_guard" in op], model)
    bound = [dict(op, target_guard=target_guard(op, model)) for op in ops]
    previous = [] if replace else job.read("manual_ops.json", [])
    job.write("manual_ops.json", previous + bound)


def validate_ops(ops, model):
    for op in ops:
        expected = target_guard(op, model)
        if op.get("target_guard") != expected:
            raise DIError("手工操作 %s 的模板版本或位置未经核对。请用 outline 核对位置，再用 "
                          "add-op --replace @文件 重新登记整组操作。" % "、".join(expected["refs"]))
