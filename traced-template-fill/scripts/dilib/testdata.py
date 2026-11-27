# -*- coding: utf-8 -*-
"""测试数据引擎：把“A列为测试项标签、首行为样品号”的测试数据表映射到记录表的参数行。

规则（rules，可在 plan/answers 中覆盖）：
  header_row      样品号所在行（1起；默认自动识别）
  label_col       测试项标签列（默认自动识别，通常 A）
  cond_pattern    条件后缀正则（默认 _(Vnorm|Vmin|Vmax|Vnom|Vtyp)$）
  cond_keep       保留的条件后缀列表，如 ["Vnorm"]；"ask" 表示需询问；"all" 表示全部合并
  no_suffix       无条件后缀的测试项：keep / drop
  multi           同一样品多值时：range / max / min / mean / median / worst / ask
  range_sep       范围写法分隔符，默认 "~"
  pass_text / fail_text   PASSED/FAILED 的填写文字
  decimals / min_sig      数字格式：保留小数位、最少有效数字
"""
from __future__ import annotations

import hashlib
import itertools
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .common import DIError, norm_text
from .read_other import col_index, col_letter, measurement_value

DEFAULT_RULES = {
    "header_row": None, "label_col": None,
    "cond_keep": "ask", "no_suffix": "ask",
    "multi": "ask", "range_sep": "~",
    "pass_text": "合格", "fail_text": "不合格",
    "decimals": 2, "min_sig": 3, "precision": "source", "symbol_formats": {},
}
with (Path(__file__).resolve().parents[2] / "rules" / "test-data.json").open(encoding="utf-8") as stream:
    DEFAULT_RULES.update(json.load(stream))


def load_rules(path):
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8-sig") as stream:
            rules = json.load(stream)
    except (ValueError, OSError) as exc:
        raise DIError("无法读取测试规则 %s：%s" % (path, exc)) from exc
    if not isinstance(rules, dict) or set(rules) - set(DEFAULT_RULES):
        raise DIError("测试规则必须是 JSON 对象；请核对规则名：%s" % path)
    validate_rules(rules)
    return rules


def validate_rules(rules):
    for key, value in rules.items():
        if key in ("cond_pattern", "qualifier_pattern", "token_separator"):
            try:
                rx = re.compile(value, re.I)
            except (TypeError, re.error) as exc:
                raise DIError("测试规则 %s 正则无效：%s" % (key, exc)) from exc
            valid = (key != "cond_pattern" or not value or rx.groups == 1) and (
                key != "token_separator" or (not rx.groups and not rx.search("")))
        elif key in ("preferred_conditions", "delta_prefixes"):
            valid = isinstance(value, list) and all(isinstance(s, str) and s for s in value)
        elif key in ("decimals", "min_sig"):
            valid = (type(value) is int and 0 <= value <= 28) or (key == "decimals" and value == "limit")
        elif key == "symbol_formats":
            valid = isinstance(value, dict) and all(
                symbol and isinstance(fmt, dict) and set(fmt) <= {"decimals", "min_sig", "precision"}
                for symbol, fmt in value.items())
            if valid:
                for fmt in value.values():
                    validate_rules(fmt)
        elif key == "header_row":
            valid = value is None or (type(value) is int and value > 0)
        elif key == "label_col":
            valid = value is None or (type(value) is int and value >= 0) or (
                isinstance(value, str) and re.fullmatch("[A-Za-z]{1,3}", value))
        elif key == "cond_keep":
            valid = (isinstance(value, str) and bool(value)) or (
                isinstance(value, list) and all(isinstance(s, str) and s for s in value))
        elif key == "split_compound":
            valid = isinstance(value, bool)
        elif key in ("no_suffix", "multi", "precision"):
            choices = {"no_suffix": ("ask", "keep", "drop"), "multi": ("ask", "range", "min", "max", "mean", "median"),
                       "precision": ("source", "trim", "fixed")}
            valid = value in choices[key]
        else:
            valid = isinstance(value, str) and bool(value)
        if not valid:
            raise DIError("测试规则 %s 的值无效：%s" % (key, value))
PASS_WORDS = {"PASSED", "PASS", "OK", "合格", "通过", "TRUE", "P"}
FAIL_WORDS = {"FAILED", "FAIL", "NG", "不合格", "失败", "FALSE", "F"}
SAMPLE_RE = re.compile(r"^(#?\s*\d+\s*#?|样品\s*\d+|S\d+|No\.?\s*\d+|DUT\s*\d+|\d+号)$", re.I)


# ======================================================================= 解析测试数据表
def _sample_id(v):
    if v is None:
        return None
    if isinstance(v, (int, float)) and float(v).is_integer() and 0 < v < 10000:
        return str(int(v))
    s = str(v).strip()
    if SAMPLE_RE.match(s):
        return re.sub(r"\D", "", s) or s
    return None


def detect_layout(sheet, rules):
    rows = sheet["rows"]
    hr = rules.get("header_row")
    lc = rules.get("label_col")
    lc = col_index(lc) if isinstance(lc, str) and lc.isalpha() else lc
    if hr is None:
        for r in range(min(8, len(rows))):
            ids = [c for c, v in enumerate(rows[r]) if _sample_id(v) is not None]
            if len(ids) >= 1 and (lc is None or lc not in ids):
                if len(ids) >= 2 or (len(rows[r]) <= 3):
                    hr = r + 1
                    break
        if hr is None:
            hr = 1
    hi = hr - 1
    header = rows[hi] if hi < len(rows) else []
    sample_cols = {}
    for c, v in enumerate(header):
        sid = _sample_id(v)
        if sid is not None and c != lc:
            sample_cols[c] = sid
    if lc is None:
        best, bc = -1, 0
        for c in range(len(header)):
            if c in sample_cols:
                continue
            n = sum(1 for r in rows[hi + 1:] if c < len(r) and isinstance(r[c], str) and r[c].strip())
            if n > best:
                best, bc = n, c
        lc = bc
    meta = {}
    for c, v in enumerate(header):
        if c in sample_cols or c == lc or v is None:
            continue
        k = norm_text(str(v))
        for key, words in (("unit", ("单位", "unit", "units")), ("condition", ("测试条件", "条件", "condition")), ("min", ("min", "lsl", "下限", "最小", "lolimit", "lowlimit")),
                           ("max", ("max", "usl", "上限", "最大", "hilimit", "highlimit"))):
            if any(k == w_ or k.startswith(w_) for w_ in words):
                meta[c] = key
    return hi, lc, sample_cols, meta


def parse_sheet(sheet, rules):
    hi, lc, sample_cols, meta = detect_layout(sheet, rules)
    rows = sheet["rows"]
    columns = {}
    for c, sid in sample_cols.items():
        columns.setdefault(sid, []).append(col_letter(c))
    duplicates = {sid: cols for sid, cols in columns.items() if len(cols) > 1}
    sample_cols = {c: sid for c, sid in sample_cols.items() if sid not in duplicates}
    blocks, cur = [], None
    for r in range(hi + 1, len(rows)):
        row = rows[r]
        lab = row[lc] if lc < len(row) else None
        vals = {sid: measurement_value(sheet, r, c) for c, sid in sample_cols.items()}
        if not str(lab or "").strip() and not any(v is not None and str(v).strip() for v in vals.values()):
            continue
        if lab not in (None, "") and str(lab).strip():
            cur = {"label": str(lab).strip(), "rows": [r], "values": {}, "meta": {}, "units": [], "conditions": []}
            blocks.append(cur)
        elif cur is None:
            continue
        else:
            cur["rows"].append(r)
        for sid, v in vals.items():
            if v is not None and str(v).strip() != "":
                cur["values"].setdefault(sid, []).append(v)
        for c, k in meta.items():
            if c < len(row) and row[c] is not None:
                cur["meta"].setdefault(k, row[c])
                group = {"unit": "units", "condition": "conditions"}.get(k)
                if group and str(row[c]).strip() and str(row[c]).strip() not in cur[group]:
                    cur[group].append(str(row[c]).strip())
    occ = {}
    for b in blocks:
        k = b["label"]
        b["occ"] = occ.get(k, 0)
        occ[k] = b["occ"] + 1
    return {"sheet": sheet["name"], "header_row": hi + 1, "label_col": col_letter(lc),
            "samples": [sample_cols[c] for c in sorted(sample_cols)], "sample_cols": {sample_cols[c]: col_letter(c) for c in sample_cols},
            "blocks": blocks, "duplicate_samples": duplicates}


# ======================================================================= 符号匹配
def norm_sym(s):
    s = norm_text(s).upper()
    s = s.replace("Δ", "Δ")
    s = re.sub(r"[^0-9A-ZΔ+\-]", "", s)
    return s


def extract_symbol(text):
    """'TTL输入高电平电压VIH' -> 'VIH'；'差分输出电压变化△VOD' -> '△VOD'"""
    m = re.search(r"([△ΔA-Za-z][A-Za-z0-9△Δ+\-]*)\s*$", text or "")
    return m.group(1) if m else ""


def token_variants(tok, prefixes):
    t = tok.upper()
    out = [t]
    for p in prefixes:
        if t.startswith(p.upper()) and len(t) > len(p):
            out.append("Δ" + t[len(p):])
    return out


def match_token(tok, syms, aliases, prefixes):
    """返回 [(sym, score, how)]；syms: {norm_sym: 原符号}。"""
    res = []
    for v in token_variants(tok, prefixes):
        if v in aliases:
            tgt = aliases[v]
            for t in (tgt if isinstance(tgt, list) else [tgt]):
                ns = norm_sym(t)
                if ns in syms:
                    res.append((syms[ns], 0.95, "别名"))
        if v in syms:
            res.append((syms[v], 1.0, "精确"))
    if not res:
        t = tok.upper()
        for ns, orig in syms.items():
            if len(ns) >= 2 and t != ns:
                if t.startswith(ns) and len(t) - len(ns) == 1 and not t[len(ns):].isdigit():
                    res.append((orig, 0.8, "近似(%s≈%s)" % (tok, orig)))
                elif t.endswith(ns) and len(t) - len(ns) == 1:
                    res.append((orig, 0.7, "近似(%s≈%s)" % (tok, orig)))
    res.sort(key=lambda x: -x[1])
    return res


# ======================================================================= 映射与汇总
def _fmt_list(vals):
    return ",".join(map(str, vals[:6])) + ("…" if len(vals) > 6 else "")


def measurement_number(value):
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def display_measurement(value, rules, lo, hi):
    """Keep a source value on its original side of each inclusive specification limit."""
    mode = rules.get("precision", "source")
    decimals = rules.get("decimals", 2)
    if decimals == "limit":
        decimals = max((max(0, -v.as_tuple().exponent) for v in (lo, hi) if v is not None), default=2)
    if value:
        decimals = max(decimals, rules.get("min_sig", 3) - 1 - value.adjusted())
    source_scale = -value.as_tuple().exponent
    if mode == "source" and source_scale > 0 and value.as_tuple().digits[-1] == 0:
        decimals = max(decimals, source_scale)
    if abs(value.adjusted()) > 308 or decimals > 308:
        return str(value)
    rounded = format(value, ".%df" % decimals)
    shown = Decimal(rounded)
    if (lo is not None and (shown < lo) != (value < lo)
            or hi is not None and (shown > hi) != (value > hi)):
        rounded = format(value, "f")
    if mode == "trim" or (mode == "source" and (source_scale <= 0 or value.as_tuple().digits[-1] != 0)):
        if "." in rounded:
            rounded = rounded.rstrip("0").rstrip(".")
    elif mode == "source" and source_scale < decimals:
        # Extra digits introduced solely by the display rule do not express source precision.
        rounded = format(Decimal(rounded), ".%df" % max(0, source_scale))
    return "0" if rounded == "-0" else rounded


def aggregate(vals, rules, mode=None, lo=None, hi=None):
    """vals: 原始值列表 -> (填写文字, 说明, 判定 pass/fail/None, 数值列表)"""
    mode = mode or rules.get("multi") or "range"
    nums, stats = [], []
    for v in vals:
        n = measurement_number(v)
        if n is not None:
            nums.append(n)
        else:
            stats.append(str(v).strip().upper())
    if not nums and stats:
        if all(s in PASS_WORDS for s in stats):
            return rules["pass_text"], "%d个结果全部%s" % (len(stats), stats[0]), "pass", []
        if any(s in FAIL_WORDS for s in stats):
            return rules["fail_text"], "存在 FAILED", "fail", []
        return stats[0] if len(set(stats)) == 1 else "/".join(sorted(set(stats))), "文本结果", None, []
    if not nums:
        return "", "无数据", None, []
    if stats:
        return "", "源区域混有数值与状态或文本：" + "、".join(sorted(set(stats))), "unknown", nums

    def f(value):
        return display_measurement(value, rules, lo, hi)

    if len(nums) == 1:
        return f(nums[0]), "单值", None, nums
    smallest, largest = min(nums), max(nums)
    if mode == "max":
        return f(largest), "%d个值取最大" % len(nums), None, nums
    if mode == "min":
        return f(smallest), "%d个值取最小" % len(nums), None, nums
    if mode == "mean":
        return f(sum(nums) / len(nums)), "%d个值取平均" % len(nums), None, nums
    if mode == "median":
        s = sorted(nums)
        k = len(s) // 2
        return f(s[k] if len(s) % 2 else (s[k - 1] + s[k]) / 2), "%d个值取中位数" % len(nums), None, nums
    a, b = f(smallest), f(largest)
    if smallest == largest:
        return a, "%d个值相同" % len(nums), None, nums
    if Decimal(a) == Decimal(b):
        a, b = str(smallest), str(largest)
    return a + rules.get("range_sep", "~") + b, "%d个值取范围" % len(nums), None, nums


def parse_limit(s, bound=None, unit=None):
    if s is None:
        return None
    t = str(s).strip().replace("＋", "+").replace("－", "-").replace("−", "-")
    prefix = {"lo": r"^(?:≥|>=)\s*", "hi": r"^(?:≤|<=)\s*"}.get(bound)
    if prefix:
        t = re.sub(prefix, "", t)
    if unit and t.endswith(unit.strip()) and unit.strip():
        t = t[:-len(unit.strip())].rstrip()
    return measurement_number(t)


def plausibility(nums, lo, hi):
    """返回 (状态, 说明)：ok / out(轻微超限) / implausible(量级或符号明显不符，疑似映射或单位错误)"""
    if not nums or (lo is None and hi is None):
        return "ok", ""
    vmin, vmax = min(nums), max(nums)
    scale = max(abs(x) for x in (lo, hi) if x is not None) or 1.0
    if lo is not None and hi is not None and lo >= 0 and vmax < 0:
        return "implausible", "实测为负值而限值为正"
    if hi is not None and (hi < 0 or hi == 0 and lo is not None and lo < 0) and vmin > 0:
        return "implausible", "实测为正值而限值≤0"
    if lo is not None and lo > 0 and vmax < lo / 20:
        return "implausible", "实测(%s)远小于下限(%s)，疑似状态码/单位不符" % (vmax, lo)
    if vmax > 0 and scale and max(abs(vmin), abs(vmax)) > scale * 20:
        return "implausible", "实测量级远超限值，疑似单位不符"
    bad = [x for x in nums if (lo is not None and x < lo) or (hi is not None and x > hi)]
    if bad:
        return "out", "有%d个值超出限值[%s, %s]" % (len(bad), lo if lo is not None else "", hi if hi is not None else "")
    return "ok", ""


def build(parsed, targets, rules, aliases, answers):
    """parsed: parse_sheet 结果；targets: [{row, sym, name, lo, hi, unit, not_tested}]
    answers: {key: effect}；返回 dict(fills, questions, ignored, unmatched, verdict, notes)"""
    rules = dict(DEFAULT_RULES, **{k: v for k, v in rules.items() if v is not None})
    qpat = re.compile(rules["qualifier_pattern"], re.I)
    cpat = re.compile(rules["cond_pattern"], re.I) if rules.get("cond_pattern") else None
    questions = []
    syms = {}
    for t in targets:
        if t.get("sym"):
            syms.setdefault(norm_sym(t["sym"]), t["sym"])
    # ---- 条件后缀
    conds = {}
    for b in parsed["blocks"]:
        m = cpat.search(b["label"]) if cpat else None
        b["cond"] = m.group(1) if m else None
        b["base"] = b["label"][:m.start()] if m else b["label"]
        if b["cond"]:
            conds[b["cond"]] = conds.get(b["cond"], 0) + 1
    keep = rules.get("cond_keep")
    if isinstance(keep, str) and keep not in ("ask", "all"):
        keep = [keep]
    if conds and keep == "ask":
        names = sorted(conds)
        mid = next((c for pref in rules["preferred_conditions"] for c in names if c.lower() == pref.lower()), names[0])
        questions.append({
            "key": "td:cond", "topic": "测试条件",
            "question": "测试数据含多个条件后缀（%s）。填写时取哪种条件的结果？" % "、".join("%s(%d项)" % (c, conds[c]) for c in names),
            "options": [{"label": "仅 %s" % mid, "effect": {"rules": {"cond_keep": [mid]}}}] +
                       [{"label": "仅 %s" % c, "effect": {"rules": {"cond_keep": [c]}}} for c in names if c != mid] +
                       [{"label": "全部条件合并取范围", "effect": {"rules": {"cond_keep": "all"}}}],
        })
        keep = [mid]
    no_suffix = rules["no_suffix"]
    if no_suffix == "ask":
        bare = [b["label"] for b in parsed["blocks"] if not b["cond"]]
        no_suffix = "keep"
        if isinstance(keep, list) and bare:
            key = "td:no_suffix"
            ans = answers.get(key) or {}
            if not ans:
                questions.append({"key": key, "topic": "未注明条件",
                                  "question": "已选择 %s。%s 等标签未注明条件，是否有原始记录证明它们也属于所选工况？" %
                                              ("、".join(keep), "、".join(dict.fromkeys(bare))[:180]),
                                  "options": [{"label": "省略未注明条件的测试项", "effect": {"no_suffix": "drop"}},
                                              {"label": "已核对未注明条件的项目均适用，纳入填写", "effect": {"no_suffix": "keep"}}]})
            no_suffix = "keep" if ans.get("no_suffix") == "keep" and not ans.get("_default") else "drop"
    active = []
    for b in parsed["blocks"]:
        if b["cond"] is None:
            if no_suffix == "keep":
                active.append(b)
        elif keep == "all" or (isinstance(keep, list) and b["cond"].lower() in [k.lower() for k in keep]):
            active.append(b)
    # ---- 多值汇总方式
    multi = rules.get("multi")
    if multi == "ask":
        if any(len(v) > 1 for b in active for v in b["values"].values()):
            questions.append({
                "key": "td:multi", "topic": "多值取值",
                "question": "同一样品的同一测试项有多个数值。记录中填写：",
                "options": [{"label": "范围 最小~最大（如 1.2~1.5）", "effect": {"rules": {"multi": "range"}}},
                            {"label": "最大值", "effect": {"rules": {"multi": "max"}}},
                            {"label": "最小值", "effect": {"rules": {"multi": "min"}}},
                            {"label": "平均值", "effect": {"rules": {"multi": "mean"}}}],
            })
        multi = "range"
    rules["multi"] = multi
    # ---- 标签 -> 符号
    aliases = {norm_sym(k): v for k, v in (aliases or {}).items()}
    groups = {}
    for b in active:
        groups.setdefault(b["base"], []).append(b)
    label_map = {}      # base -> [(sym, score, how)] 或 split
    tested_targets = {norm_sym(t["sym"]): t for t in targets if t.get("sym")}
    for base, bl in groups.items():
        toks = [t for t in re.split(rules["token_separator"], base) if t]
        matched, unexplained, alts = [], [], {}
        for tok in toks:
            cands = match_token(tok, syms, aliases, rules["delta_prefixes"])
            if cands:
                if cands[0][0] not in [m[0] for m in matched]:
                    matched.append(cands[0])
                    alts[cands[0][0]] = cands
            elif not qpat.match(tok):
                unexplained.append(tok)
        label_map[base] = {"matched": matched, "unexplained": unexplained, "blocks": bl, "tokens": toks, "alts": alts}
    # 子集规则：A 的符号与 B 相同但 B 多出无法解释的词 -> B 视为其他测量项
    for base, info in label_map.items():
        if info["unexplained"] and info["matched"]:
            ms = [m[0] for m in info["matched"]]
            for b2, i2 in label_map.items():
                if b2 != base and not i2["unexplained"] and [m[0] for m in i2["matched"]] == ms:
                    info["shadowed_by"] = b2
                    break
    assign = {}     # sym -> [(block, how, score)]
    ignored = []
    alias_groups = {}
    for base, info in label_map.items():
        matched = info["matched"]
        if len(matched) == 1 and matched[0][2] == "别名" and not info["unexplained"] and not info.get("shadowed_by"):
            sym = matched[0][0]
            alias_tokens = [tok for tok in info["tokens"] if any(v in aliases for v in token_variants(tok, rules["delta_prefixes"]))]
            if len(info["alts"][sym]) == 1 and len(alias_tokens) == 1 and not tested_targets[norm_sym(sym)].get("not_tested"):
                alias_groups.setdefault((alias_tokens[0].upper(), sym), []).append(base)
    grouped_answers = {}
    for (token, sym), bases in alias_groups.items():
        if len(bases) < 2:
            continue
        key = "td:alias:" + token + ":" + sym
        ans = answers.get(key) or {}
        if ans.get("individual") and not ans.get("_default"):
            continue
        if not ans:
            questions.append({"key": key, "topic": "数据对应",
                              "question": "以下标签都使用别名 %s，是否在本批数据中代表 %s？%s" % (token, sym, "、".join(bases)),
                              "options": [{"label": "暂不对应，留空", "effect": {"map": None}},
                                          {"label": "已核对，上述标签均对应 " + sym, "effect": {"map": [sym]}},
                                          {"label": "逐个标签选择对应参数", "effect": {"individual": True}}]})
        grouped_answers.update({base: ans if ans and not ans.get("_default") else {"map": None} for base in bases})
    # Only exact single-parameter labels establish a correspondence without an answer.
    for base, info in label_map.items():
        blocks, matched = info["blocks"], info["matched"]
        if not matched or info.get("shadowed_by"):
            ignored.append((base, "记录无对应参数" if not matched else "含额外测量词，与 " + info["shadowed_by"] + " 不是同一项目"))
            continue
        symbols = list(dict.fromkeys(c[0] for m in matched for c in info["alts"][m[0]]))
        key = "td:map:" + base
        exact = (len(symbols) == 1 and len(matched) == 1 and matched[0][2] == "精确"
                 and not info["unexplained"] and not tested_targets[norm_sym(symbols[0])].get("not_tested"))
        if exact and key not in answers:
            for block in blocks:
                assign.setdefault(symbols[0], []).append((block, "精确", 1.0))
            continue
        q = _map_question(base, info, targets, symbols, tested_targets, set())
        if not q:
            ignored.append((base, "无可测参数候选"))
            continue
        if len(matched) > 1 and not rules["split_compound"]:
            q["options"] = q["options"][:1]  # 可以用自由回答明确给出每段符号
        ans = answers.get(key, grouped_answers.get(base))
        if not isinstance(ans, dict) or ans.get("_default"):
            questions.append(q)
            ignored.append((base, "待核对标签与参数逐段对应"))
            continue
        order = ans.get("map")
        if ans.get("free"):
            order = [x.strip() for x in re.split(r"[,，]", ans.get("text", ""))]
        if not order:
            ignored.append((base, "按回答省略"))
            continue
        if len(order) == 1:
            order = order * len(blocks)
        if len(order) != len(blocks) or any(sym is not None and (
                norm_sym(sym) not in tested_targets or tested_targets[norm_sym(sym)].get("not_tested")) for sym in order):
            ignored.append((base, "回答未给出与段数相符的可测参数，未填；请重新回答"))
            continue
        for block, sym in zip(blocks, order):
            if sym:
                actual = tested_targets[norm_sym(sym)]["sym"]
                assign.setdefault(actual, []).append((block, "按回答逐段对应", 1.0))
    # ---- 逐行汇总
    fills, unmatched, verdict = {}, [], {s: "pass" for s in parsed["samples"]}
    unit_map = {t["sym"]: str(t["unit"]).strip() for t in targets if t.get("sym") and t.get("unit")}
    identity = {"units": {}, "conditions": {}}
    needed = {"units": {}, "conditions": {}}
    for name in identity:
        for key, effect in answers.items():
            if key.startswith("td:" + name + ":") and isinstance(effect, dict) and not effect.get("_default"):
                identity[name].update(effect.get(name) or {})
    required_review = []
    for t in targets:
        if not t.get("sym"):
            continue
        symbol = t["sym"]
        hits = assign.get(symbol, [])
        if not hits:
            if not t.get("not_tested"):
                unmatched.append(symbol)
            continue
        if sum(norm_sym(other.get("sym") or "") == norm_sym(symbol) for other in targets) > 1:
            required_review.append("%s 在目标出现多行，按行核对工况或手工指定" % symbol)
            continue
        keys = list(dict.fromkeys(b["base"] for b, _, _ in hits))
        if len(keys) > 1:
            key = "td:merge:" + symbol
            ans = answers.get(key)
            if not isinstance(ans, dict) or ans.get("_default"):
                opts = [{"label": "先留空", "effect": {"labels": []}},
                        {"label": "已核对工况/通道，按选定取值方式汇总这些标签", "effect": {"labels": keys}}]
                opts += [{"label": "仅取 " + k, "effect": {"labels": [k]}} for k in keys]
                questions.append({"key": key, "topic": "跨标签汇总", "question": "%s 有多个来源标签：%s。哪些属于本记录行？" %
                                  (symbol, "、".join(keys)), "options": opts})
                ans = {}
            hits = [h for h in hits if h[0]["base"] in (ans.get("labels") or [])]
            if not hits:
                unmatched.append(symbol)
                continue
        vans = answers.get("td:val:" + symbol)
        if not isinstance(vans, dict) or vans.get("_default"):
            vans = {}
        format_rules = dict(rules, **rules["symbol_formats"].get(symbol, {}))
        lo, hi = (parse_limit(t.get(k), k, t.get("unit")) for k in ("lo", "hi"))
        unsupported = any(v is None and str(t.get(k) or "").strip() not in ("", "—", "–", "-", "/")
                          for k, v in (("lo", lo), ("hi", hi)))
        unit = unit_map.get(symbol)
        declared_units = {u for b, _, _ in hits for u in b.get("units", []) if u}
        unit_conflict = bool(unit and any(u != unit for u in declared_units))
        unit_missing = bool(unit and any(not b.get("units") for b, _, _ in hits))
        condition = str(t.get("cond") or "").strip()
        context_missing = condition not in ("", "—", "-", "/") and any(
            {norm_text(c) for c in b.get("conditions", [])} != {norm_text(condition)} for b, _, _ in hits)
        row_fill, bad = {}, None
        all_binary = all(measurement_number(v) in (0, 1) for b, _, _ in hits for vs in b["values"].values() for v in vs)
        for sid in parsed["samples"]:
            vals, ranges = [], []
            col = parsed["sample_cols"][sid]
            incomplete = False
            for b, _, _ in hits:
                values = b["values"].get(sid, [])
                incomplete |= len(values) != len(b["rows"])
                vals.extend(values)
                ranges.append("%s!%s%d:%s%d" % (parsed["sheet"], col, b["rows"][0] + 1, col, b["rows"][-1] + 1))
            text, how, status, nums = aggregate(vals, format_rules, lo=lo, hi=hi)
            pst, msg = plausibility(nums, lo, hi)
            if incomplete or status == "unknown" or how == "文本结果":
                msg = "样品有缺少读数的行" if incomplete else how
                text, pst, status = "", "unknown", "unknown"
                required_review.append("%s 样品%s (%s)：%s" % (symbol, sid, "；".join(ranges), msg))
            elif nums:
                if pst == "implausible" and not bad:
                    bad = {"value": text, "check_msg": msg}
                mode = vans.get("mode")
                if mode == "negate":
                    text, how, _, transformed = aggregate([n.copy_negate() for n in nums], format_rules, lo=lo, hi=hi)
                    how += "（取负号）"
                    pst, msg = plausibility(transformed, lo, hi)
                elif mode == "flags" and vans.get("pass_code") in (0, 1) and all_binary:
                    status = "pass" if all(n == vans["pass_code"] for n in nums) else "fail"
                    text, how = rules[status + "_text"], "按回答解码状态（%s 表示通过）" % vans["pass_code"]
                    pst, msg = "ok", ""
                elif mode == "raw" and pst == "implausible":
                    pst = "out"
                    how += "（已确认原始读数）"
                if mode == "blank" or pst == "implausible":
                    text, status = "", "unknown"
                    how = "按回答留空" if mode == "blank" else "疑似状态码、单位或符号异常，待确认"
                if text and status not in ("pass", "fail"):
                    if unit_conflict:
                        msg = "源单位 %s 与目标单位 %s 不一致" % ("、".join(sorted(declared_units)), unit)
                        required_review.append("%s：%s（%s）" % (symbol, msg, "；".join(ranges)))
                        text, pst, status = "", "unknown", "unknown"
                    elif unit_missing and identity["units"].get(symbol) != unit:
                        needed["units"][symbol] = unit
                        text, pst, status, msg = "", "unknown", "unknown", "源表未注明单位，记录单位为 " + unit
                    elif unsupported or (lo is None and hi is None):
                        status, msg = "unknown", "规范界限缺失或未能解析，无法判定"
            if context_missing and identity["conditions"].get(symbol) != condition:
                needed["conditions"][symbol] = condition
                text, pst, status = "", "unknown", "unknown"
                msg = (msg + "；" if msg else "") + "源表未证实记录所需工况：" + condition
            if status == "fail" or (text and pst == "out" and status != "unknown"):
                verdict[sid] = "fail"
            elif (status == "unknown" or not text) and verdict[sid] != "fail":
                verdict[sid] = "unknown"
            row_fill[sid] = {"value": text, "how": how, "n": len(vals), "labels": sorted({b["label"] for b, _, _ in hits}),
                             "ranges": ranges, "range_labels": [b["label"] for b, _, _ in hits], "conf": min(sc for _, _, sc in hits), "hows": sorted({h for _, h, _ in hits}),
                             "check": pst, "check_msg": msg, "raw": _fmt_list(vals),
                             "limits": [str(v) if v is not None else None for v in (lo, hi)]}
            if vans.get("mode") == "flags" and status in ("pass", "fail"):
                row_fill[sid]["pass_code"] = vans["pass_code"]
        if bad and not vans:
            questions.append(_value_question(t, all_binary, bad))
        fills[t["row"]] = row_fill
    for name, missing in needed.items():
        if not missing:
            continue
        desc = "、".join("%s (%s)" % (sym, v) for sym, v in sorted(missing.items()))
        signature = hashlib.sha256(json.dumps(missing, sort_keys=True).encode()).hexdigest()[:16]
        key = "td:" + name + ":" + signature
        if key not in answers:
            topic = "测量单位" if name == "units" else "测试工况核对"
            questions.append({"key": key, "topic": topic, "question": "源表未证实以下记录要求，请对照原始试验记录核对：" + desc,
                              "options": [{"label": "未核对，相关结果留空", "effect": {name: {}}},
                                          {"label": "已核对，所列要求与本批试验一致", "effect": {name: missing}}]})
    return {"fills": fills, "questions": questions, "ignored": ignored, "unmatched": unmatched,
            "verdict": verdict, "rules": rules, "samples": parsed["samples"], "review": required_review}


def _map_question(base, info, targets, syms_in, tested, confident):
    """不确定标签的询问。候选池 = 匹配到且可测、未被置信分配的参数 + 同族（末字符相同）尚无数据的参数。"""
    n = len(info["blocks"])
    is_nt = lambda s: tested.get(norm_sym(s), {}).get("not_tested")
    pool = [s for s in syms_in if not is_nt(s)]
    lasts = {norm_sym(x)[-1:] for x in syms_in} | {t[-1:].upper() for t in info["tokens"] if t}
    for t in targets:
        s = t.get("sym")
        if (info["unexplained"] or any(is_nt(x) for x in syms_in)) and s and not t.get("not_tested") and s not in pool and norm_sym(s) not in confident \
                and norm_sym(s)[-1:] in lasts:
            pool.append(s)
    if not pool:
        return None
    k = min(n, len(pool))
    cands = []
    if len(pool) == 1:
        cands.append([pool[0]] * n)
    else:
        for perm in itertools.islice(itertools.permutations(pool, k), 3):
            cands.append(list(perm) + [None] * (n - k))
    opts = [{"label": "暂不对应，该项留空", "effect": {"map": None}}]
    for cand in cands:
        label = "，".join("第%d段→%s" % (i + 1, s or "不填") for i, s in enumerate(cand))
        opts.append({"label": label, "effect": {"map": cand}})
    segs = []
    for b in info["blocks"]:
        sample = next(iter(b["values"].values()), [])
        segs.append("第%d段=行%d-%d(如%s)" % (len(segs) + 1, b["rows"][0] + 1, b["rows"][-1] + 1, _fmt_list(sample[:2])))
    reason = []
    if info["unexplained"]:
        reason.append("含无法识别的词“%s”" % "/".join(info["unexplained"]))
    nt = [m[0] for m in info["matched"] if is_nt(m[0])]
    if nt:
        reason.append("近似匹配到的 %s 在规范中为“不测试”项" % "、".join(nt))
    if len(info["matched"]) != n:
        reason.append("标签含%d个参数名而数据为%d段" % (len(info["matched"]), n))
    return {"key": "td:map:" + base, "topic": "数据对应",
            "question": "测试项“%s”（%s）应填到哪个参数？其他顺序可按数据段输入用逗号分隔的参数符号。%s" % (base, "；".join(segs), ("（" + "，".join(reason) + "）") if reason else ""),
            "options": opts, "recommended": 0}


def _value_question(t, all_binary, bad):
    eg = bad["value"]
    o_neg = {"label": "已核原始记录，本项目按相反极性记录", "effect": {"mode": "negate"}}
    o_raw = {"label": "已核对，照填原始测量并如实标注超限", "effect": {"mode": "raw"}}
    o_blank = {"label": "留空，核对原始测试记录", "effect": {"mode": "blank"}}
    if all_binary:
        opts = [o_blank, o_raw] + [
            {"label": "已核对状态定义，%d 表示通过，%d 表示失败，仅填结论" % (code, 1 - code),
             "effect": {"mode": "flags", "pass_code": code}} for code in (1, 0)]
        why = "数据只有0/1，疑似状态码；规范限值不能作为实测值"
    else:
        opts = [o_blank, o_raw]
        if "正值" in bad["check_msg"] or "负值" in bad["check_msg"]:
            opts.append(o_neg)
        why = bad["check_msg"]
    return {"key": "td:val:" + t["sym"], "topic": "数值合理性",
            "question": "参数 %s（%s，规范 %s~%s %s）实测为“%s”，%s。如何填写？" % (
                t["sym"], t.get("name", ""), t.get("lo") or "—", t.get("hi") or "—", t.get("unit", ""), eg, why),
            "options": opts[:4], "recommended": 0}
