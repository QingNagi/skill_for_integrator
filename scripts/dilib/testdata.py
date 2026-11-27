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

import itertools
import re

from .common import fmt_number, norm_text, to_number
from .read_other import col_letter

DEFAULT_RULES = {
    "header_row": None, "label_col": None,
    "cond_pattern": r"_(Vnorm|Vnom|Vtyp|Vmin|Vmax)$",
    "cond_keep": "ask", "no_suffix": "keep",
    "multi": "ask", "range_sep": "~",
    "pass_text": "合格", "fail_text": "不合格",
    "decimals": 2, "min_sig": 3,
    "qualifier_pattern": r"^(IN|OUT|CH|CHN|CHANNEL|PIN|P|N)\d+$|^\d+$|^\d+(\.\d+)?(MV|V|MA|UA|NA|MHZ|KHZ|GHZ|MBPS|GBPS|PS|NS|US|C)$"
                         r"|^\d+V\d+$|^(N|P|PH|NH|PL|NL|H|L|HI|LO|HIGH|LOW|VCC|VDD|GND|VSS|AC|DC|DIFF|SE|SEARCH|"
                         r"VICMLOW|VICMHIGH|VICM|MIN|MAX|NORM|NOM|TYP|POS|NEG|RISE|FALL|TEST|T\d+)$",
}
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
    lc = (ord(lc.upper()) - 65) if isinstance(lc, str) and lc.isalpha() else lc
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
        for key, words in (("unit", ("单位", "unit", "units")), ("min", ("min", "lsl", "下限", "最小", "lolimit", "lowlimit")),
                           ("max", ("max", "usl", "上限", "最大", "hilimit", "highlimit"))):
            if any(k == w_ or k.startswith(w_) for w_ in words):
                meta[c] = key
    return hi, lc, sample_cols, meta


def parse_sheet(sheet, rules):
    hi, lc, sample_cols, meta = detect_layout(sheet, rules)
    rows = sheet["rows"]
    blocks, cur = [], None
    for r in range(hi + 1, len(rows)):
        row = rows[r]
        lab = row[lc] if lc < len(row) else None
        vals = {sid: (row[c] if c < len(row) else None) for c, sid in sample_cols.items()}
        if lab not in (None, "") and str(lab).strip():
            cur = {"label": str(lab).strip(), "rows": [r], "values": {}, "meta": {}}
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
    occ = {}
    for b in blocks:
        k = b["label"]
        b["occ"] = occ.get(k, 0)
        occ[k] = b["occ"] + 1
    return {"sheet": sheet["name"], "header_row": hi + 1, "label_col": col_letter(lc),
            "samples": [sample_cols[c] for c in sorted(sample_cols)], "sample_cols": {sample_cols[c]: col_letter(c) for c in sample_cols},
            "blocks": blocks}


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


def token_variants(tok):
    t = tok.upper()
    out = [t]
    for p in ("DOT", "DELTA", "DLT"):
        if t.startswith(p) and len(t) > len(p) + 1:
            out.append("Δ" + t[len(p):])
    return out


def match_token(tok, syms, aliases):
    """返回 [(sym, score, how)]；syms: {norm_sym: 原符号}。"""
    res = []
    for v in token_variants(tok):
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
    return ",".join(fmt_number(v, 6, 3) if isinstance(v, float) else str(v) for v in vals[:6]) + ("…" if len(vals) > 6 else "")


def aggregate(vals, rules, mode=None):
    """vals: 原始值列表 -> (填写文字, 说明, 判定 pass/fail/None, 数值列表)"""
    mode = mode or rules.get("multi") or "range"
    nums, stats = [], []
    for v in vals:
        n = to_number(v)
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
    f = lambda x: fmt_number(x, rules.get("decimals", 2), rules.get("min_sig", 3))
    if len(nums) == 1:
        return f(nums[0]), "单值", None, nums
    lo, hi = min(nums), max(nums)
    if mode == "max":
        return f(hi), "%d个值取最大" % len(nums), None, nums
    if mode == "min":
        return f(lo), "%d个值取最小" % len(nums), None, nums
    if mode == "mean":
        return f(sum(nums) / len(nums)), "%d个值取平均" % len(nums), None, nums
    if mode == "median":
        s = sorted(nums)
        k = len(s) // 2
        return f(s[k] if len(s) % 2 else (s[k - 1] + s[k]) / 2), "%d个值取中位数" % len(nums), None, nums
    a, b = f(lo), f(hi)
    if a == b:
        return a, "%d个值相同" % len(nums), None, nums
    return a + rules.get("range_sep", "~") + b, "%d个值取范围" % len(nums), None, nums


def parse_limit(s):
    if s is None:
        return None
    t = str(s).strip().replace("＋", "+").replace("－", "-").replace("—", "").replace("–", "")
    return to_number(t)


def plausibility(nums, lo, hi):
    """返回 (状态, 说明)：ok / out(轻微超限) / implausible(量级或符号明显不符，疑似映射或单位错误)"""
    if not nums or (lo is None and hi is None):
        return "ok", ""
    vmin, vmax = min(nums), max(nums)
    scale = max(abs(x) for x in (lo, hi) if x is not None) or 1.0
    if lo is not None and hi is not None and lo >= 0 and vmax < 0:
        return "implausible", "实测为负值而限值为正"
    if hi is not None and hi <= 0 and (lo is None or lo < 0) and vmin > 0:
        return "implausible", "实测为正值而限值≤0"
    if lo is not None and lo > 0 and vmax < lo / 20:
        return "implausible", "实测(%s)远小于下限(%s)，疑似状态码/单位不符" % (fmt_number(vmax), fmt_number(lo))
    if vmax > 0 and scale and max(abs(vmin), abs(vmax)) > scale * 20:
        return "implausible", "实测量级远超限值，疑似单位不符"
    bad = [x for x in nums if (lo is not None and x < lo) or (hi is not None and x > hi)]
    if bad:
        return "out", "有%d个值超出限值[%s, %s]" % (len(bad), fmt_number(lo) if lo is not None else "", fmt_number(hi) if hi is not None else "")
    return "ok", ""


def build(parsed, targets, rules, aliases, answers):
    """parsed: parse_sheet 结果；targets: [{row, sym, name, lo, hi, unit, not_tested}]
    answers: {key: effect}；返回 dict(fills, questions, ignored, unmatched, verdict, notes)"""
    rules = dict(DEFAULT_RULES, **{k: v for k, v in rules.items() if v is not None})
    qpat = re.compile(rules["qualifier_pattern"], re.I)
    cpat = re.compile(rules["cond_pattern"], re.I) if rules.get("cond_pattern") else None
    questions, notes = [], []
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
        mid = next((c for c in names if c.lower() in ("vnorm", "vnom", "vtyp")), names[0])
        questions.append({
            "key": "td:cond", "topic": "测试条件",
            "question": "测试数据含多个条件后缀（%s）。填写时取哪种条件的结果？" % "、".join("%s(%d项)" % (c, conds[c]) for c in names),
            "options": [{"label": "仅 %s" % mid, "effect": {"rules": {"cond_keep": [mid]}}}] +
                       [{"label": "仅 %s" % c, "effect": {"rules": {"cond_keep": [c]}}} for c in names if c != mid] +
                       [{"label": "全部条件合并取范围", "effect": {"rules": {"cond_keep": "all"}}}],
        })
        keep = [mid]
    active = []
    for b in parsed["blocks"]:
        if b["cond"] is None:
            if rules.get("no_suffix", "keep") == "keep":
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
        toks = [t for t in re.split(r"[_\s]+", base) if t]
        matched, unexplained, alts = [], [], {}
        for tok in toks:
            cands = match_token(tok, syms, aliases)
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
    pending = []
    is_nt = lambda s: tested_targets.get(norm_sym(s), {}).get("not_tested")
    # 第一遍：用户已回答的、以及置信的对应（精确/别名；单参数或“参数个数=数据段数”的复合标签）
    for base, info in label_map.items():
        bl, matched = info["blocks"], info["matched"]
        ans = answers.get("td:map:" + base)
        if ans is not None:
            order = ans.get("map")      # list[sym or None] 按出现顺序
            if order is None:
                ignored.append((base, "按回答不填"))
                continue
            how, sc = ("按推荐默认(待确认)", 0.5) if ans.get("_default") else ("按回答", 1.0)
            for b, s in zip(bl, itertools.cycle(order) if len(order) == 1 else order):
                if s:
                    assign.setdefault(s, []).append((b, how, sc))
            continue
        if not matched:
            ignored.append((base, "记录中无对应参数"))
            continue
        if info.get("shadowed_by"):
            ignored.append((base, "含额外测量词“%s”，与 %s 重复，视为其他测量项" % ("/".join(info["unexplained"]), info["shadowed_by"])))
            continue
        syms_in = [m[0] for m in matched]
        fuzzy = [m for m in matched if m[1] < 0.9]
        nt = [s for s in syms_in if is_nt(s)]
        if nt or info["unexplained"] or (len(syms_in) > 1 and len(syms_in) != len(bl)):
            pending.append(base)
            continue
        if len(syms_in) == 1:
            for b in bl:
                assign.setdefault(syms_in[0], []).append((b, matched[0][2], matched[0][1]))
            if fuzzy:
                notes.append(("fuzzy", base, syms_in))
            continue
        confirm_default = bool((answers.get("td:confirm") or {}).get("_default"))
        for b, s in zip(bl, syms_in):
            assign.setdefault(s, []).append((b, "复合标签按顺序拆分", 0.5 if confirm_default else (0.9 if not fuzzy else 0.8)))
        notes.append(("split", base, syms_in))
    # 第二遍：不确定的标签——排除已被置信分配的参数后给出候选并询问；草稿先按推荐填写
    confident = {norm_sym(s) for s in assign}
    for base in pending:
        info = label_map[base]
        bl = info["blocks"]
        syms_in = []
        for m in info["matched"]:
            alt = [c for c in info["alts"].get(m[0], [m]) if norm_sym(c[0]) not in confident]
            if alt and alt[0][0] not in syms_in:
                syms_in.append(alt[0][0])
        q = _map_question(base, info, targets, syms_in, tested_targets, confident)
        if q is None:
            ignored.append((base, "候选参数均已有数据"))
            continue
        questions.append(q)
        rec = q["options"][q["recommended"]]["effect"]["map"] or []
        for b, s in zip(bl, rec if len(rec) > 1 else itertools.cycle(rec)):
            if s:
                assign.setdefault(s, []).append((b, "待确认(按推荐)", 0.5))
    # 汇总确认：复合拆分/近似匹配
    if notes and "td:confirm" not in answers:
        desc = []
        for kind, base, ss in notes:
            desc.append("%s→%s" % (base, "、".join(ss)))
        questions.append({"key": "td:confirm", "topic": "标签对应",
                          "question": "以下测试项标签按“复合标签按出现顺序拆分/近似匹配”对应到记录参数，是否正确？\n" + "；".join(desc),
                          "options": [{"label": "正确，按此填写", "effect": {}},
                                      {"label": "有误，我来说明", "effect": {"free": True}}]})
    # ---- 逐行汇总
    fills, unmatched, verdict = {}, [], {s: "pass" for s in parsed["samples"]}
    for t in targets:
        if not t.get("sym"):
            continue
        key = norm_sym(t["sym"])
        hits = None
        for s, lst in assign.items():
            if norm_sym(s) == key:
                hits = lst
                break
        if not hits:
            if not t.get("not_tested"):
                unmatched.append(t["sym"])
            continue
        vans = answers.get("td:val:" + t["sym"])
        row_fill = {}
        for sid in parsed["samples"]:
            vals, src_rows = [], []
            for b, how, sc in hits:
                v = b["values"].get(sid, [])
                vals.extend(v)
                src_rows.append((b["rows"][0], b["rows"][-1], b["label"]))
            text, how_txt, status, nums = aggregate(vals, rules)
            lo, hi = parse_limit(t.get("lo")), parse_limit(t.get("hi"))
            pst, pmsg = plausibility(nums, lo, hi)
            col = parsed["sample_cols"][sid]
            cell = {"value": text, "how": how_txt, "n": len(vals),
                    "labels": sorted({b["label"] for b, _, _ in hits}),
                    "ranges": ["%s!%s%d:%s%d" % (parsed["sheet"], col, a + 1, col, z + 1) for a, z, _ in src_rows],
                    "conf": min(sc for _, _, sc in hits), "hows": sorted({h for _, h, _ in hits}),
                    "check": pst, "check_msg": pmsg, "raw": _fmt_list(vals)}
            if vans:
                mode = vans.get("mode")
                if mode == "pass":
                    cell["value"], cell["how"] = rules["pass_text"], "按回答填合格"
                    pst = "ok"
                elif mode == "blank":
                    cell["value"], cell["how"] = "", "按回答留空"
                    pst = "ok"
                elif mode == "const":
                    cell["value"], cell["how"] = vans.get("const", ""), "按回答填写固定值"
                    pst = "ok"
                elif mode == "negate" and nums:
                    text2, how2, _, nums2 = aggregate([-x for x in nums], rules)
                    cell["value"], cell["how"] = text2, how2 + "（取负号）"
                    pst, pmsg = plausibility(nums2, lo, hi)
                elif mode == "raw":
                    pst = "out" if pst == "implausible" else pst
                cell["check"] = pst
                if vans.get("_default"):
                    cell["how"] += "（按推荐默认，待确认）"
                    cell["conf"] = min(cell["conf"], 0.5)
                cell["check_msg"] = "" if pst == "ok" else cell["check_msg"]
            if status == "fail" or pst == "out":
                verdict[sid] = "fail"
            row_fill[sid] = cell
        bad = [c for c in row_fill.values() if c["check"] == "implausible"]
        if bad and not vans:
            questions.append(_value_question(t, row_fill, bad[0]))
        fills[t["row"]] = row_fill
    return {"fills": fills, "questions": questions, "ignored": ignored, "unmatched": unmatched,
            "verdict": verdict, "rules": rules, "samples": parsed["samples"]}


def _map_question(base, info, targets, syms_in, tested, confident):
    """不确定标签的询问。候选池 = 匹配到且可测、未被置信分配的参数 + 同族（末字符相同）尚无数据的参数。"""
    n = len(info["blocks"])
    is_nt = lambda s: tested.get(norm_sym(s), {}).get("not_tested")
    pool = [s for s in syms_in if not is_nt(s)]
    lasts = {norm_sym(x)[-1:] for x in syms_in} | {t[-1:].upper() for t in info["tokens"] if t}
    for t in targets:
        s = t.get("sym")
        if s and not t.get("not_tested") and s not in pool and norm_sym(s) not in confident \
                and norm_sym(s)[-1:] in lasts:
            pool.append(s)
    if not pool:
        return None
    k = min(n, len(pool))
    rec = [s if s in pool else None for s in syms_in][:n]
    rec += [None] * (n - len(rec))
    rest = [s for s in pool if s not in rec]
    rec = [s if s else (rest.pop(0) if rest else None) for s in rec]
    cands = [rec]
    for perm in itertools.permutations(pool, k):
        c = list(perm) + [None] * (n - k)
        if c not in cands:
            cands.append(c)
        if len(cands) >= 3:
            break
    opts = []
    for cand in cands:
        label = "，".join("第%d段→%s" % (i + 1, s or "不填") for i, s in enumerate(cand)) if n > 1 else "→%s" % cand[0]
        opts.append({"label": label, "effect": {"map": cand}})
    opts.append({"label": "该测试项不填", "effect": {"map": None}})
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
            "question": "测试项“%s”（%s）应填到哪个参数？%s" % (base, "；".join(segs), ("（" + "，".join(reason) + "）") if reason else ""),
            "options": opts, "recommended": 0}


def _value_question(t, row_fill, bad):
    eg = bad["value"]
    nums = []
    for c in row_fill.values():
        for x in (c.get("raw") or "").replace("…", "").split(","):
            n = to_number(x)
            if n is not None:
                nums.append(n)
    lo, hi = parse_limit(t.get("lo")), parse_limit(t.get("hi"))
    lim = t.get("lo") if lo is not None else t.get("hi")
    o_pass = {"label": "填“%s”" % "合格", "effect": {"mode": "pass"}}
    o_lim = {"label": "填规范限值 %s（视为达到指标）" % lim, "effect": {"mode": "const", "const": str(lim)}}
    o_neg = {"label": "取负号后填写", "effect": {"mode": "negate"}}
    o_raw = {"label": "照填原始数据", "effect": {"mode": "raw"}}
    o_blank = {"label": "留空", "effect": {"mode": "blank"}}
    if nums and set(nums) <= {0.0, 1.0}:
        # 0/1 通过标志：推荐“留空”，不把标志当实测值，也不把规范限值当实测值；“填规范限值”只作备选
        opts = [o_blank, o_pass, o_raw] + ([o_lim] if lim not in (None, "", "—") else [])
        why = "数据只有0/1，疑似通过标志"
    elif "正值" in bad["check_msg"] or "负值" in bad["check_msg"]:
        opts = [o_raw, o_neg, o_pass, o_blank]      # 推荐项必须是不掩盖原始数据的“照填原始数据”，不能默认写“合格”
        why = bad["check_msg"]
    else:
        opts = [o_raw, o_pass, o_blank]
        why = bad["check_msg"]
    return {"key": "td:val:" + t["sym"], "topic": "数值合理性",
            "question": "参数 %s（%s，规范 %s~%s %s）实测为“%s”，%s。如何填写？" % (
                t["sym"], t.get("name", ""), t.get("lo") or "—", t.get("hi") or "—", t.get("unit", ""), eg, why),
            "options": opts[:4], "recommended": 0}
