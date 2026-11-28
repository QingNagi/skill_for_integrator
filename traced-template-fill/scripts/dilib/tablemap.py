# -*- coding: utf-8 -*-
"""表格列语义识别与“源表 -> 目标填写区”的列映射、行数据抽取。"""
from __future__ import annotations

import re

from .common import norm_text

ROLE_WORDS = [
    ("symbol", ["参数符号", "符号", "代号", "symbol"]),
    ("cond", ["测试条件", "试验条件", "条件", "condition", "conditions"]),
    ("min", ["最小值", "最小", "下限", "min", "minimum", "lsl"]),
    ("max", ["最大值", "最大", "上限", "max", "maximum", "usl"]),
    ("typ", ["典型值", "典型", "typ", "typical"]),
    ("unit", ["单位", "unit", "units"]),
    ("method", ["测试方法", "试验方法", "方法", "method"]),
    ("group", ["a组分组", "分组", "组别", "subgroup"]),
    ("measured", ["实测值", "测试结果", "测量值", "测试值", "实测", "结果", "measured", "result"]),
    ("device", ["仪器名称及型号", "仪器名称", "设备编号", "设备名称", "有效期限", "有效期", "校准"]),
    ("remark", ["备注", "说明", "remark", "note"]),
    ("name", ["参数名称", "测试参数", "测试项目", "检验项目", "试验项目", "指标名称", "参数", "特性", "项目", "名称",
              "parameter", "characteristic", "item"]),
    ("limit", ["极值", "极限值", "规范值", "限值", "指标", "limits", "limit"]),
]
SAMPLE_RE = re.compile(r"^(#?\s*(\d+)\s*#|样品\s*(\d+)|(\d+)\s*号|S(\d+)|No\.?\s*(\d+)|#\s*(\d+))$", re.I)


def role_of(path):
    """path: 表头路径（从上到下）。返回 (role, sample_id, note)。"""
    if not path:
        return None, None, ""
    leaf = path[-1].strip()
    m = SAMPLE_RE.match(re.sub(r"\s+", "", leaf))
    if m:
        sid = next(g for g in m.groups()[1:] if g)
        return "sample", sid, ""
    for p in reversed(path):
        n = norm_text(p)
        for role, words in ROLE_WORDS:
            for w_ in words:
                if n.startswith(w_):
                    note = p.strip()[len(w_):] if p.strip().lower().startswith(w_) else ""
                    note = re.sub(r"^[\s：:（(]+|[）)\s]+$", "", note) if note else ""
                    if role == "limit" and len(path) > 1:
                        break
                    return role, None, note
    return None, None, ""


def map_columns(src_cols, tgt_cols):
    """src_cols/tgt_cols: [{'c', 'path'}]。返回 {目标c: {'from': [源c...], 'role'}} 与未映射信息。"""
    src_roles = {}
    for c in src_cols:
        role, sid, note = role_of(c["path"])
        c["role"], c["sid"], c["note"] = role, sid, note
        if role and role not in src_roles:
            src_roles[role] = c
    mapping, unmapped = {}, []
    tgt_roles = set()
    for c in tgt_cols:
        role, sid, note = role_of(c["path"])
        c["role"], c["sid"], c["note"] = role, sid, note
        tgt_roles.add(role)
    for c in tgt_cols:
        role = c["role"]
        if role == "name":
            frm = [src_roles["name"]["c"]] if "name" in src_roles else []
            if "symbol" in src_roles and "symbol" not in tgt_roles:
                frm.append(src_roles["symbol"]["c"])         # 目标无“符号”列时并入参数列（如“TTL输入高电平电压VIH”）
            if frm:
                mapping[c["c"]] = {"from": frm, "role": role}
        elif role in src_roles and role not in ("sample", "measured", "remark"):
            mapping[c["c"]] = {"from": [src_roles[role]["c"]], "role": role}
        elif role in ("sample", "measured"):
            mapping[c["c"]] = {"from": [], "role": "sample", "sid": c["sid"]}
        else:
            unmapped.append(c["c"])
    return mapping, unmapped


def table_columns(model, tid):
    """源表列：[{'c','path'}]（按表头行拼路径）。"""
    t = model.by[tid]
    grid, runs, origin = model.table_grid(tid)
    cols = []
    for c in range(t["ncols"]):
        path, seen = [], set()
        for r in range(t.get("hdr", 1)):
            o = origin[r][c]
            if o and o not in seen and grid[r][c] and grid[r][c].strip():
                seen.add(o)
                path.append(grid[r][c].strip())
        cols.append({"c": c, "path": path})
    # 合并的列（同一原点）只保留第一列
    out, seen = [], set()
    for c in cols:
        o = origin[t.get("hdr", 1) - 1][c["c"]] if t.get("hdr", 1) else None
        key = (o, tuple(c["path"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def header_runs(model, tid, col):
    """源表某列表头的 runs（用于把“条件：除另有规定外…”中的说明带到目标表头）。"""
    t = model.by[tid]
    grid, runs, origin = model.table_grid(tid)
    for r in range(t.get("hdr", 1)):
        if runs[r][col]:
            return runs[r][col]
    return []


def logical_rows(model, tid):
    """源表数据行（含续表、去表头与表注行），每行为 {c: (text, runs)}，纵向合并向下填充。"""
    ids = [tid] + [b["id"] for b in model.m["blocks"] if b.get("cont") == tid]
    rows = []
    for x in ids:
        t = model.by[x]
        grid, runs, origin = model.table_grid(x)
        for r in range(t.get("hdr", 1), t["nrows"]):
            if r == t.get("foot_row"):
                continue
            row = {}
            for c in range(t["ncols"]):
                row[c] = (grid[r][c] or "", runs[r][c] or [])
            # 跳过整行合并的说明行
            texts = {row[c][0] for c in row}
            if len(texts) == 1 and t["ncols"] > 2 and r == t["nrows"] - 1:
                continue
            rows.append({"table": x, "r": r, "cells": row})
    return rows, ids


def footnotes(model, tid):
    ids = [tid] + [b["id"] for b in model.m["blocks"] if b.get("cont") == tid]
    out = {}
    for x in ids:
        for f in model.by[x].get("foot", []) or []:
            for part in re.split(r"\n", f):
                m = re.match(r"^\s*([a-z])\s*[.:：、)）]?\s*(.+)$", part.strip())
                if m:
                    out[m.group(1)] = m.group(2).strip()
    body = [b for b in model.m["blocks"] if not b.get("in") and (b["t"] != "p" or b.get("text","").strip())]
    for i, block in enumerate(body):
        if block["id"] not in ids or i + 1 >= len(body):
            continue
        note = body[i + 1]
        match = re.match(r"^\s*注\s*[：:]\s*([a-z])\s+(.+)$", note.get("text", "")) if note["t"] == "p" else None
        if match:
            out.setdefault(match[1], match[2].strip())
    return out


def split_symbol_mark(symbol, footnotes):
    """Strip a leading plain-text footnote only when the corresponding note is present."""
    if len(symbol) >= 2 and symbol[0] in footnotes and symbol[1].isupper():
        return symbol[1:], symbol[0]
    return symbol, ""


def strip_marks(runs):
    """去掉表注上标（如 'a'），返回 (runs, marks)。"""
    out, marks = [], []
    for t, f in runs:
        if "^" in f and re.fullmatch(r"[a-z,，]+", t.strip() or "-"):
            marks.extend(x for x in re.split(r"[,，]", t.strip()) if x)
            continue
        out.append([t, f])
    return out, marks


def table_score(model, tid, tgt_cols, keywords=""):
    """评估源表与目标填写区的匹配度（列语义覆盖 + 题注关键词）。"""
    from .common import contains_score
    cols = table_columns(model, tid)
    mapping, unmapped = map_columns([dict(c) for c in cols], [dict(c) for c in tgt_cols])
    filled = sum(1 for v in mapping.values() if v["from"])
    need = sum(1 for c in tgt_cols if role_of(c["path"])[0] not in ("sample", "measured", None))
    s = filled / max(1, need)
    cap = model.by[tid].get("cap", "")
    if keywords and cap:
        s += 0.5 * contains_score(keywords, cap)
    return s
