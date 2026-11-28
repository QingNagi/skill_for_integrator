"""按来源表、记录行和实测数据规划表格填充。"""
import re

from . import identity, tablemap, testdata
from .common import norm_text, short


class TablePlanningMixin:
    # ================================================================= 配方：表格数据区
    def plan_region(self, t, rg):
        tcols = [{"c": c["c"], "path": c["path"]} for c in rg["cols"]]
        roles = [tablemap.role_of(c["path"])[0] for c in tcols]
        has_samples = any(r == "sample" for r in roles)
        need_spec = any(r in ("name", "cond", "min", "max", "unit") for r in roles)
        rows = []
        best = None
        if need_spec:
            best, ambiguous = self.select_table(rg, tcols)
            if ambiguous and best is None:
                self.report["unfilled"].append("区域 %s 的规范来源未选定，本区未写入" % rg["id"])
                if rg.get("filled"):
                    self.mark_region_stale(t, rg, tcols)
                    rows = self.rows_from_existing(t, rg, tcols)
                    for c in tcols:
                        role, sid, _ = tablemap.role_of(c["path"])
                        if role == "sample":
                            self.mark_stale(t, rows, {sid: c["c"]}, set())
                if has_samples:
                    if not hasattr(self, "region_verdicts"):
                        self.region_verdicts = {}
                    self.region_verdicts[rg["id"]] = ({}, ["规范来源未选定"])
                    self.verdict = (0, 0, ["规范来源未选定"])
                return
        if need_spec and not rg.get("filled"):
            if best and best[0] >= 0.5:
                rows = self.fill_from_table(t, rg, tcols, best[1], best[2])
                if best[0] < 0.99:
                    self.report["warnings"].append("数据区 %s 与源表 %s:%s 列匹配度一般(%.2f)，请重点核对" % (rg["id"], best[1], best[2], best[0]))
            elif rg.get("label") or not has_samples:
                self.missing_regions.append((re.sub(r"\s+", "", rg.get("label") or "表格区"), "%s.R%d" % (t["id"], rg["rows"][0])))
        elif not has_samples and not rg.get('filled'):
            self.missing_regions.append((re.sub(r"\s+", "", rg.get("label") or "表格区"), "%s.R%d" % (t["id"], rg["rows"][0])))
        if rg.get('filled'):
            # 目标里这一区域已经有内容（模板是上一批的记录）：不覆盖规范列，先核对，再处理实测值/其余内容
            if need_spec or has_samples:
                rows = self.rows_from_existing(t, rg, tcols)
            if need_spec and rows:
                self.check_spec_drift(t, rg, tcols, rows, best)
            if not has_samples:
                self.mark_region_stale(t, rg, tcols)
        if has_samples:
            if not rows:
                rows = self.rows_from_existing(t, rg, tcols)
            self.fill_testdata(t, rg, tcols, rows)

    def affected_spec_rows(self, t, rg):
        """Resolve prospective positions without creating write operations from an unconfirmed specification."""
        existing = self.rows_from_existing(t, rg, rg["cols"])
        ranked = self.rank_tables(rg["cols"])
        if not ranked or ranked[0][0] < 0.5 or (len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 0.15):
            return existing
        _, alias, tid = ranked[0]
        model = self.src[alias]
        source_columns = tablemap.table_columns(model, tid)
        roles = {tablemap.role_of(c["path"])[0]: c["c"] for c in source_columns}
        mapping, _ = tablemap.map_columns(source_columns, [dict(c) for c in rg["cols"]])
        field_cols = {c: m["from"][0] for c, m in mapping.items()
                      if m["role"] in ("cond", "min", "max", "unit") and len(m["from"]) == 1}
        target_cells = {(c["r"], c["c"]): c["text"] for c in self.T.by[t["id"]]["cells"]}
        foot = tablemap.footnotes(model, tid)
        logical, _ = tablemap.logical_rows(model, tid)
        prospective = []
        by_symbol = {}
        for i, src_row in enumerate(logical):
            cells = src_row["cells"]
            symbol = cells.get(roles.get("symbol"), ("", []))[0] or testdata.extract_symbol(cells.get(roles.get("name"), ("", []))[0])
            marks = [m for _, runs in cells.values() for m in tablemap.strip_marks(runs)[1]]
            symbol, mark = tablemap.split_symbol_mark(symbol, foot)
            if mark:
                marks.append(mark)
            exempt = any(re.search(r"不测试|不检验|设计保证|不考核", foot.get(m, "")) for m in marks)
            by_symbol.setdefault(testdata.norm_sym(symbol), []).append((src_row, exempt))
            prospective.append({"row": rg["rows"][0] + i, "sym": symbol, "not_tested": exempt})
        result = existing or prospective
        for r in result:
            hits = by_symbol.get(testdata.norm_sym(r["sym"]), [])
            r["in_spec"] = bool(hits)
            if not hits:
                continue
            r["not_tested"] = all(exempt for _, exempt in hits)
            if len(hits) != 1:
                continue
            source_row = hits[0][0]
            r["spec_cells"] = []
            for tc, sc in field_cols.items():
                source_text = source_row["cells"].get(sc, ("", []))[0]
                if not source_text.strip() or target_cells.get((r["row"], tc), "").strip():
                    continue
                r["spec_cells"].append({"target": "%s.R%d.C%d" % (t["id"], r["row"], tc), "symbol": r["sym"],
                                        "candidate": source_text, "kind": "规范字段",
                                        "source": "%s:%s.R%d.C%d" % (alias, source_row["table"], source_row["r"], sc)})
        return result

    def add_spec_impact(self, q, table, rows, sample_columns):
        positions = self.measurement_cells(table, [r for r in rows if r.get("in_spec", True)], sample_columns)
        for cell in positions:
            cell["kind"] = "规范对应实测位置"
        spec_cells = [cell for row in rows for cell in row.get("spec_cells", [])]
        self.add_impact(q, spec_cells + positions)
        counts = {kind: sum(c.get("kind") == kind for c in q["affected_cells"]) for kind in ("规范字段", "规范对应实测位置")}
        q["impact_by_kind"] = counts
        q["question"] = q["question"].split("（影响 ")[0] + "（影响 %s）" % "、".join(
            "%s %d 格，如 %s" % (kind, count, next(c["target"] for c in q["affected_cells"] if c["kind"] == kind))
            for kind, count in counts.items() if count)

    def fill_from_table(self, t, rg, tcols, alias, tid):
        md = self.src[alias]
        scols = tablemap.table_columns(md, tid)
        for c in scols:
            c["role"], c["sid"], c["note"] = tablemap.role_of(c["path"])
        mapping, unmapped = tablemap.map_columns([dict(c) for c in scols], [dict(c) for c in tcols])
        lrows, ids = tablemap.logical_rows(md, tid)
        foot = tablemap.footnotes(md, tid)
        cap = md.by[tid].get("cap", tid)
        r0, r1 = rg["rows"]
        cap_rows = r1 - r0 + 1
        extra = max(0, len(lrows) - cap_rows)
        if extra:
            self.ops.append({"id": self.oid(), "op": "add_rows", "table": t["id"], "after_row": r1, "count": extra})
            self.row_shift[t["id"]] = (r1, extra)
            self.report["warnings"].append("源表 %d 行多于目标空行 %d 行，已在 %s 末尾增加 %d 行" % (len(lrows), cap_rows, rg["id"], extra))
        self.summary.append("[规范表→记录] %s:%s %s（%d行） → %s 第%d-%d行；列：%s" % (
            alias, tid, cap, len(lrows), rg["id"], r0, r0 + len(lrows) - 1,
            "、".join("%s←%s" % ("/".join(c["path"]), "+".join(
                short("/".join(next(s["path"] for s in scols if s["c"] == x)).replace("\n", ""), 14)
                for x in mapping[c["c"]]["from"]))
                     for c in tcols if c["c"] in mapping and mapping[c["c"]]["from"])))
        # 表头说明（如“条件：除另有规定外，…”）带到目标表头
        for c in tcols:
            mp = mapping.get(c["c"])
            if mp and mp["role"] == "cond":
                sc = next(s for s in scols if s["c"] == mp["from"][0])
                note_runs = self._header_note_runs(md, tid, sc["c"])
                if note_runs and norm_text("".join(x[0] for x in note_runs)) not in norm_text("".join(c["path"])):
                    hdr_cell = "%s.R%d.C%d" % (t["id"], rg["header"][0], c["c"])
                    self.ops.append({"id": self.oid(), "op": "append_runs", "cell": hdr_cell,
                                     "runs": [["(", ""]] + note_runs + [[")", ""]],
                                     "src": [{"alias": alias, "ref": tid}]})
        out_rows = []
        for i, lr in enumerate(lrows):
            tr = r0 + i
            cells = lr["cells"]
            row_info = {"row": tr, "src_table": lr["table"], "src_row": lr["r"], "alias": alias}
            marks = []
            for c in tcols:
                mp = mapping.get(c["c"])
                if not mp or not mp["from"]:
                    continue
                runs = []
                for k, sc in enumerate(mp["from"]):
                    txt, rr = cells.get(sc, ("", []))
                    rr, mk = tablemap.strip_marks([list(x) for x in rr])
                    marks += mk
                    runs += rr
                runs = [[x[0].replace("\n", ""), x[1]] for x in runs if x[0]]
                if runs:
                    self.ops.append({"id": self.oid(), "op": "set_cell", "cell": "%s.R%d.C%d" % (t["id"], tr, c["c"]),
                                     "runs": runs, "group": "spec:%d" % tr})
                if mp["role"] == "name":
                    sym_col = [x for x in mp["from"] if next(s for s in scols if s["c"] == x).get("role") == "symbol"]
                    row_info["name"] = cells.get(mp["from"][0], ("", []))[0]
                    row_info["sym"] = cells.get(sym_col[0], ("", []))[0].strip() if sym_col else \
                        testdata.extract_symbol(row_info["name"])
                    row_info["name"] = re.sub(r"[a-z]$", "", row_info["name"].strip()) if marks else row_info["name"]
                elif mp["role"] == "symbol":
                    row_info["sym"] = "".join(x[0] for x in runs).strip()
                elif mp["role"] in ("min", "max", "unit", "cond"):
                    row_info[{"min": "lo", "max": "hi", "unit": "unit", "cond": "cond"}[mp["role"]]] = \
                        "".join(x[0] for x in runs)
            if row_info.get("sym"):
                row_info["sym"], plain_mark = tablemap.split_symbol_mark(row_info["sym"], foot)
                if plain_mark:
                    marks.append(plain_mark)
            notes = [foot[m] for m in marks if m in foot]
            row_info["not_tested"] = any(re.search(r"不测试|不检验|设计保证|不考核", n) for n in notes)
            row_info["foot"] = ["注%s：%s" % (m, foot[m]) for m in marks if m in foot]
            out_rows.append(row_info)
            ctext = "【规范】%s %s 第%d行%s" % (self.src[alias].m["name"], cap, i + 1,
                                            ("；" + "；".join(row_info["foot"])) if row_info["foot"] else "")
            self.ops.append({"id": self.oid(), "op": "comment", "anchor": "%s.R%d.C%d" % (t["id"], tr, tcols[0]["c"]),
                             "text": ctext, "links": [{"label": "打开原文", "alias": alias, "ref": "%s.R%d" % (lr["table"], lr["r"])}],
                             "group": "spec:%d" % tr})
            self.report["fills"].append({"target": "%s 第%d行" % (t["id"], tr), "value": "%s %s" % (row_info.get("name", ""), row_info.get("sym", "")),
                                         "source": "%s:%s.R%d" % (alias, lr["table"], lr["r"]), "how": "规范表逐行复制"})
        return out_rows

    def _header_note_runs(self, md, tid, col):
        runs = tablemap.header_runs(md, tid, col)
        txt = "".join(r[0] for r in runs)
        m = re.search(r"[：:]", txt)
        if not m:
            return []
        pos, out = m.end(), []
        acc = 0
        for t, f in runs:
            s, e = acc, acc + len(t)
            acc = e
            if e <= pos:
                continue
            piece = t[max(0, pos - s):].replace("\n", "")
            if piece:
                out.append([piece, f if not f.startswith("@") else ""])
        if out:
            out[-1][0] = out[-1][0].rstrip()
        return [x for x in out if x[0]]

    def rows_from_existing(self, t, rg, tcols):
        """目标表已有参数行（未从规范表复制）时，从目标单元格读取符号与限值。"""
        rows = []
        cells = {(c["r"], c["c"]): c for c in self.T.by[t["id"]]["cells"]}
        role = {c["c"]: tablemap.role_of(c["path"])[0] for c in tcols}
        for r in range(rg["rows"][0], rg["rows"][1] + 1):
            info = {"row": r}
            for c, ro in role.items():
                txt = cells.get((r, c), {}).get("text", "")
                if ro == "name":
                    info["name"] = txt
                    info["sym"] = testdata.extract_symbol(txt)
                    mk = re.search(r"[一-鿿]([a-e])([A-Z△Δ][A-Za-z0-9+\-]*)\s*$", txt)
                    if mk:  # 表注字母与符号连写（如“电容aCIN1”）：还原为符号CIN1，记下表注字母
                        info["sym"], info["mark"] = mk.group(2), mk.group(1)
                elif ro == "symbol":
                    info["sym"] = txt.strip()
                elif ro in ("min", "max", "unit", "cond"):
                    info[{"min": "lo", "max": "hi", "unit": "unit", "cond": "cond"}[ro]] = txt
            if info.get("sym"):
                rows.append(info)
        return rows

    def fill_testdata(self, t, rg, tcols, rows):
        if not hasattr(self, "region_verdicts"):
            self.region_verdicts = {}
        self.region_verdicts[rg["id"]] = ({}, ["实测数据区未完成"])
        self.verdict = (0, 0, ["实测数据区未完成"])
        columns = {}
        for c in tcols:
            ro, sid, _ = tablemap.role_of(c["path"])
            if ro == "sample":
                columns.setdefault(sid, []).append(c["c"])
        sid_col = {sid: cols[0] for sid, cols in columns.items() if len(cols) == 1}
        duplicates = {sid: cols for sid, cols in columns.items() if len(cols) > 1}
        for sid, cols in duplicates.items():
            detail = "目标区域 %s 样品 %s 重复出现在列 %s，已排除该样品" % (rg["id"], sid, cols)
            self.review_measurements("td:duplicate:" + rg["id"] + ":" + sid, detail)
            for col in cols:
                self.mark_stale(t, rows, {sid: col}, set())
        if not sid_col:
            return
        sheets = self.sheet_sources()
        if not sheets:
            self.report["unfilled"].append("实测值列（%s）：没有提供测试数据文件（属于“指定来源没提供”，不等于没有数据）" % rg["id"])
            self.mark_stale(t, rows, sid_col, set())
            return
        alias = None
        ans = self.answer_effect("td:source")
        if ans and not ans.get("_default") and ans.get("alias") in sheets:
            alias = ans["alias"]
        elif len(sheets) > 1:
            q = {"key": "td:source", "topic": "数据来源", "question": "提供了多个表格文件，哪个是本次的测试数据？",
                 "options": [{"label": "%s %s" % (a, m.m["name"]), "effect": {"alias": a}} for a, m in sheets.items()]}
            self.ask(q)
            self.add_impact(q, self.measurement_cells(t, rows, sid_col))
            self.mark_stale(t, rows, sid_col, set())
            return
        else:
            alias = next(iter(sheets))
        md = self.src[alias]
        nonempty = [s for s in md.m["sheets"] if s["rows"]]
        if not nonempty:
            self.report["unfilled"].append("测试数据文件没有非空工作表：" + md.m["name"])
            self.mark_stale(t, rows, sid_col, set())
            return
        if len(nonempty) > 1:
            candidates = [s for s in nonempty if testdata.detect_layout(s, self.rules)[2]]
            nonempty = candidates or nonempty
        sheet = nonempty[0]
        if len(nonempty) > 1:
            key = "td:sheet:" + alias + ":" + rg["id"]
            eff = self.answer_effect(key) or {}
            chosen = next((s for s in nonempty if s["name"] == eff.get("sheet") and not eff.get("_default")), None)
            if chosen is None:
                self.ask({"key": key, "topic": "测试数据工作表", "question": "%s 有多张候选工作表，哪张属于 %s？" % (md.m["name"], rg["id"]),
                          "options": [{"label": "先留空", "effect": {"skip": True}}] +
                                     [{"label": s["name"], "effect": {"sheet": s["name"]}} for s in nonempty]})
                self.mark_stale(t, rows, sid_col, set())
                return
            sheet = chosen
        if self.verify_data_source(alias, md, sheet) is False:
            cells = self.measurement_cells(t, rows, sid_col)
            self.__dict__.setdefault("measurement_write_checks", {}).update((c["target"], "") for c in cells)
            for q in self.questions:
                if q["key"].startswith("src:spec:") or q["key"] == "src:verify:" + alias:
                    self.add_impact(q, cells)
            self.mark_stale(t, rows, sid_col, set())
            self.report["unfilled"].append("%s：%d 个实测位置等待规范或批次确认" % (rg["id"], len(cells)))
            return
        scope = ":" + alias + ":" + rg["id"] + ":" + sheet["name"]
        sheet_scope = ":" + alias + ":" + sheet["name"]
        shared_questions = {"td:cond", "td:multi", "td:no_suffix"}

        def question_key(short_key):
            return short_key + (sheet_scope if short_key in shared_questions else scope)

        rules = dict(self.rules)
        for k in ("cond_keep", "multi"):
            eff = self.answer_effect(question_key("td:" + ("cond" if k == "cond_keep" else "multi")))
            if eff and eff.get("rules"):
                rules.update(eff["rules"])
        parsed = testdata.parse_sheet(sheet, rules)
        for sid, cols in parsed["duplicate_samples"].items():
            detail = "来源 %s:%s 样品 %s 重复出现在列 %s，已排除该样品" % (alias, sheet["name"], sid, "、".join(cols))
            self.review_measurements("td:duplicate" + scope + ":" + sid, detail)
        ds = parsed["samples"]
        recs = list(sid_col)
        source_sample = {sid: sid for sid in ds}
        if set(ds) != set(recs):
            key = "td:samplemap" + scope
            eff = self.answer_effect(key) or {}
            common = {sid: sid for sid in ds if sid in sid_col}
            options = [{"label": "仅按相同编号填写，其余留空" if common else "编号无交集，实测值留空",
                        "effect": {"samplemap": common}}]
            if ds and len(ds) == len(recs) and not (duplicates or parsed["duplicate_samples"]):
                ordered = dict(zip(ds, recs))
                options.append({"label": "已核对编号：" + "，".join("%s→%s" % item for item in ordered.items()),
                                "effect": {"samplemap": ordered}})
            if not eff:
                self.ask({"key": key, "topic": "样品对应", "question": "来源样品号 %s 与目标 %s 不一致。" %
                          ("、".join(ds) or "无", "、".join(recs)), "options": options})
            if not eff:
                self.add_impact(next(q for q in self.questions if q["key"] == key), self.measurement_cells(t, rows, sid_col))
            sm = common if not eff or eff.get("_default") else eff.get("samplemap") or {}
            if not isinstance(sm, dict) or not set(sm) <= set(ds) or not set(sm.values()) <= set(recs) or len(set(sm.values())) != len(sm):
                sm = {}
            if not sm:
                self.report["unfilled"].append("实测值列（%s）：没有已核对的样品对应，未填" % rg["id"])
                self.mark_stale(t, rows, sid_col, set())
                return
            source_sample = {target: source for source, target in sm.items()}
            parsed["samples"] = [sm[s] for s in ds if s in sm]
            parsed["sample_cols"] = {sm[s]: c for s, c in parsed["sample_cols"].items() if s in sm}
            for b in parsed["blocks"]:
                b["values"] = {sm[s]: v for s, v in b["values"].items() if s in sm}
            self.report["warnings"].append("样品号对应：" + "，".join("数据%s→记录%s" % kv for kv in sm.items()))
        judgment_cols = all(any(re.search(r"判定|结论|合格性", part) for part in c["path"])
                            for c in tcols if tablemap.role_of(c["path"])[0] == "sample")
        targets = [{"column_type": rules.get("column_types", {}).get(r.get("sym"), "judgment" if judgment_cols else "measurement"), "row": r["row"], "sym": r.get("sym"), "name": r.get("name", ""), "lo": r.get("lo"), "hi": r.get("hi"),
                    "unit": r.get("unit", ""), "cond": r.get("cond", ""), "not_tested": r.get("not_tested")} for r in rows]
        permitted_judgments = re.findall(r"(?:允许|约定)([一-鿿]{2,16}?)(?:使用|用|写|填)(?:判定|结论)", getattr(self, "task", ""))
        for target in targets:
            if any(name in target["name"] for name in permitted_judgments):
                target["column_type"] = "judgment"
        ans_map = {}
        for k, v in self.answers.items():
            if k.startswith("td:") and k.endswith(scope):
                e = self.answer_effect(k)
                if e is not None:
                    ans_map[k[:-len(scope)]] = e
        for key in shared_questions:
            effect = self.answer_effect(question_key(key))
            if effect is not None:
                ans_map[key] = effect
        res = testdata.build(parsed, targets, rules, self.aliases, ans_map)
        checks = self.__dict__.setdefault("measurement_write_checks", {})
        for row in rows:
            if row.get("sym") in res["program_symbols"]:
                for sid, col in sid_col.items():
                    cid = "%s.R%d.C%d" % (t["id"], row["row"], col)
                    checks[cid] = res["fills"].get(row["row"], {}).get(sid, {}).get("value", "")
        cells = self.measurement_cells(t, rows, sid_col)
        template_identity = identity.document_identity(self.T) if hasattr(self.T, "m") else identity.identity_fields([])
        book_fields = identity.sheet_identity(md, sheet, template_identity)
        blocks_by_base = {}
        for block in parsed["blocks"]:
            blocks_by_base.setdefault(block["base"], []).append(block)
        for q in res["questions"]:
            key = question_key(q["key"])
            question = next((existing for existing in self.questions if existing["key"] == key), None)
            if question is None:
                if q.get("conditional_on"):
                    q = dict(q, conditional_on=question_key(q["conditional_on"]))
                question = self.ask(dict(q, key=key, source_scope=alias + ":" + sheet["name"], source_label_col=parsed["label_col"],
                                         source_models=sorted(book_fields["model"] | template_identity["model"])))
            affected = [dict(c) for c in cells if c["symbol"] in q["symbols"] and c["sample"] in parsed["sample_cols"]]
            for c in affected:
                col = parsed["sample_cols"][c["sample"]]
                blocks = blocks_by_base.get(q.get("source_label"), [])
                if blocks:
                    c["source"] = "; ".join("%s:%s!%s%d:%s%d" % (alias, sheet["name"], col, b["rows"][0] + 1,
                                                                  col, b["rows"][-1] + 1) for b in blocks)
                    c["candidate"] = " / ".join(", ".join(map(str, b["values"].get(c["sample"], []))) for b in blocks)
                else:
                    row_num = int(c["target"].rsplit(".R", 1)[1].split(".")[0])
                    item = res["fills"].get(row_num, {}).get(c["sample"], {})
                    c["source"] = "; ".join(alias + ":" + r for r in item.get("ranges", []))
                    c["candidate"] = item.get("raw", "")
            self.add_impact(question, affected)
        if res["condition_gaps"]:
            detail = "；".join("%s 清单工况 %s，规范要求 %s" % (sym, actual, wanted)
                               for sym, (actual, wanted) in sorted(res["condition_gaps"].items()))
            self.block_source("verify:conditions" + scope, "实测工况适用范围",
                              "以下仅反映清单工况，规范要求未经完整核实，不能据此认定全部符合：" + detail)
        review = [s for s in res["review"] if "按常用修约会显示为" not in s]
        for detail in res["review"]:
            if "按常用修约会显示为" in detail:
                self.block_source("verify:rounding:" + detail, "修约前后限值判定", detail)
        if review:
            self.review_measurements("td:review" + scope, "；".join(review))
        missing_samples = [s for s in res["samples"] if s not in sid_col]
        self.summary.append("[测试数据] %s %s：样品%s（第%d行为样品号，%s列为测试项）；条件=%s；多值=%s；已对应 %d 个参数；忽略 %d 项%s" % (
            alias, md.m["name"], "、".join(s + "#" for s in res["samples"]), parsed["header_row"], parsed["label_col"],
            "全部" if res["rules"].get("cond_keep") == "all" else "/".join(res["rules"].get("cond_keep") or []) or "全部",
            {"range": "范围(最小%s最大)" % res["rules"].get("range_sep", "~"), "max": "最大值", "min": "最小值",
             "mean": "平均值", "median": "中位数"}.get(res["rules"]["multi"], res["rules"]["multi"]),
            len(res["fills"]), len(res["ignored"]), ("；数据有样品%s但记录无对应列" % "、".join(missing_samples)) if missing_samples else ""))
        if res["ignored"]:
            self.report["ignored"] += ["%s（%s）" % (b, why) for b, why in res["ignored"]]
        if res["unmatched"]:
            self.report["unfilled"].append("无测试数据的参数：" + "、".join(res["unmatched"]))
        filled = set()
        merged_lab = []
        sym_of = {r["row"]: (r.get("sym") or r.get("name") or "") for r in rows}
        target_by_row = {r["row"]: r for r in targets}
        template = getattr(self.T, "m", {})
        src_scope = {"source": alias, "sha1": md.m.get("sha1"), "sheet": sheet["name"]}
        spec_scope = {"template": template.get("sha1"), "specifications": {
            a: doc.m.get("sha1") for a, doc in self.doc_sources().items()}}
        book_identity = {k: sorted(v) for k, v in book_fields.items()}
        model_evidence = {"template": sorted(template_identity["model"]), "measurements": sorted(book_fields["model"]),
                          "specifications": {a: sorted(identity.document_identity(doc)["model"]) for a, doc in self.doc_sources().items()}}
        label_context = {b["label"]: {"suffix": b["cond"], "units": b["units"], "conditions": b["conditions"]}
                         for b in parsed["blocks"]}
        for row, fill in res["fills"].items():
            first_cell = None
            links, notes, warn = [], set(), False
            for sid, cell in fill.items():
                if sid not in sid_col:
                    continue
                cid = "%s.R%d.C%d" % (t["id"], row, sid_col[sid])
                first_cell = first_cell or cid
                if cell["value"] != "":
                    filled.add((row, sid))
                    self.ops.append({"id": self.oid(), "op": "set_cell", "cell": cid, "runs": [[cell["value"], ""]],
                                     "group": "data:%d" % row})
                links.extend({"label": "%s# %s" % (sid, ref), "alias": alias, "ref": ref} for ref in cell["ranges"])
                notes.add(cell["how"])
                if (cid == first_cell and cell["check"] != "ok") or cell["conf"] < 0.9:
                    warn = True
                if cid != first_cell and ((cell["value"] and cell["check"] == "out") or "按常用修约" in cell["check_msg"]):
                    self.ops.append({"id": self.oid(), "op":"comment", "anchor":cid, "warn":True,
                                     "text":"⚠【超限】%s：%s" % (sym_of[row], cell["check_msg"]),
                                     "links":[{"label":"打开实测原始读数","alias":alias,"ref":ref} for ref in cell["ranges"]]})
                entries = self.report["fills"] if cell["value"] else self.report.setdefault("held_measurements", [])
                if not cell["value"]:
                    self.report["unfilled"].append("%s %s 样品%s：%s" % (cid, sym_of[row], sid, cell["check_msg"] or cell["how"]))
                dependencies = [key for key in self.answers if key == "src:verify:" + alias or key.startswith("src:spec:")
                                or key in ("td:source", "td:samplemap" + scope, "td:sheet:" + alias + ":" + rg["id"])]
                dependencies += [question_key(k) for k, symbols in res["dependencies"].items() if sym_of[row] in symbols]
                target_row = target_by_row[row]
                context = {label: label_context[label] for label in cell["labels"]}
                observations = {
                    "批次身份": {"scope": src_scope, "value": book_identity, "evidence": "来源表头、工作表名和本批来源核对"},
                    "规范适用": {"scope": spec_scope, "value": model_evidence, "evidence": {"target_region": rg["id"]}},
                    "样品对应": {"scope": src_scope, "value": [source_sample[sid], sid], "evidence": {"header_row": parsed["header_row"], "target": rg["id"]}},
                    "测试条件": {"scope": src_scope, "value": cell["actual_condition"], "evidence": {"source_labels": context, "required": target_row.get("cond")}},
                    "参数归属": {"scope": src_scope, "value": sym_of[row], "evidence": {"labels": cell["labels"], "method": cell["hows"]}},
                    "值类型与极性": {"scope": spec_scope, "value": cell["effective_column_type"], "evidence": {"source": src_scope, "unit": target_row.get("unit"),
                              "polarity": "negate" if "取负号" in cell["how"] else rules.get("polarity", {}).get(sym_of[row], "signed"), "method": cell["how"]}}}

                entries.append({"decision_keys": dependencies, "observations": observations, "target": cid, "value": cell["value"], "source": "%s:%s" % (alias, ";".join(cell["ranges"])),
                                             "how": "%s；%s" % ("/".join(cell["hows"]), cell["how"]), "raw": cell["raw"],
                                             "check": cell["check_msg"], "limits": cell["limits"],
                                             "aggregation": res["rules"]["multi"], "symbol": sym_of[row],
                                             "source_sample": source_sample[sid], "target_sample": sid,
                                             "target_header": rg.get("header"),
                                             "header_row": parsed["header_row"], "label_col": parsed["label_col"],
                                             "label_rows": cell["label_rows"], **{k:cell[k] for k in ("value_kind","sign","unit_conversion") if k in cell},
                                             "range_labels": cell["range_labels"], **({"pass_code": cell["pass_code"]} if "pass_code" in cell else {})})
            if first_cell:
                c0 = next(iter(fill.values()))
                if len(c0["labels"]) > 1:
                    notes.add('由%d个不同标签合并取范围，请核对是否为同一工况/通道' % len(c0["labels"]))
                    warn = True
                    merged_lab.append('%s←%s' % (sym_of.get(row, row), '、'.join(c0["labels"][:3]) + ('…' if len(c0["labels"]) > 3 else '')))
                txt = "%s【实测】%s 标签 %s（%s）；%s%s" % (
                    "⚠" if warn else "", md.m["name"], "、".join(c0["labels"][:4]) + ("…" if len(c0["labels"]) > 4 else ""),
                    "/".join(c0["hows"]), "；".join(sorted(notes)),
                    ("；" + c0["check_msg"]) if c0["check_msg"] else "")
                self.ops.append({"id": self.oid(), "op": "comment", "anchor": first_cell, "text": txt, "links": links,
                                 "group": "data:%d" % row, "warn": warn})
        if merged_lab:
            self.report['warnings'].append('以下参数由多个不同标签合并成一个范围（多通道/多工况不等于同一条件的重复测量）：' + '；'.join(merged_lab[:8]) + '。请对照批注确认')
        if set(sid_col) != set(res['samples']):
            self.report['warnings'].append('样品号不一致：记录有 %s，数据文件有 %s；只写入两边都有的样品，其余样品在记录里的原有值已标“本批未核”' % ('、'.join(sorted(sid_col)) or '无', '、'.join(res['samples']) or '无'))
        self.mark_stale(t, rows, sid_col, filled)
        for r in rows:
            if r.get("not_tested") and r["row"] not in res["fills"]:
                cid = "%s.R%d.C%d" % (t["id"], r["row"], sid_col[sorted(sid_col)[0]])
                self.ops.append({"id": self.oid(), "op": "comment", "anchor": cid,
                                 "text": "【实测】未填：%s；数据文件中无此项" % ("；".join(r.get("foot") or ["规范注明不测试"])), "links": []})
        self.record_verdict(alias, rg['id'], rows, columns, res)

    @staticmethod
    def measurement_cells(t, rows, columns):
        return [{"target": "%s.R%d.C%d" % (t["id"], r["row"], c), "symbol": r.get("sym", ""), "sample": sid}
                for r in rows if not r.get("not_tested") for sid, c in columns.items()]

    @staticmethod
    def add_impact(question, cells):
        by_target = {c["target"]: c for c in question.get("affected_cells", [])}
        by_target.update((c["target"], c) for c in cells)
        question["affected_cells"] = list(by_target.values())
        question["impact_count"] = len(by_target)
        question["question"] = question["question"].split("（影响 ")[0] + "（影响 %d 格，如 %s）" % (
            len(by_target), "、".join(list(by_target)[:3]))

    def review_measurements(self, key, detail):
        self.report["unfilled"].append(detail)
        eff = self.answer_effect(key) or {}
        if not eff:
            self.ask({"key": key, "topic": "实测记录核对", "question": detail + "。请核对源表并重新规划，或确认这些格留空。",
                      "options": [{"label": "待核对", "effect": {"ok": False}},
                                  {"label": "已核对，接受所列实测格留空", "effect": {"ok": True}}]})
        if not eff.get("ok") or eff.get("_default"):
            self.report.setdefault("unverified_sources", []).append({"kind": "source", "key": key, "detail": detail,
                                                                     "name": "实测记录"})

    def record_verdict(self, alias, region, rows, sample_cols, res):
        """只汇总记录中实际出现的样品，待确认的测量或来源不能产生批次结论。"""
        reasons = []
        if any(i.get('key', '').split(':')[:3] == ['src', 'verify', alias] for i in self.report.get('unverified_sources', [])):
            reasons.append('数据来源尚未核实')
        relevant = {'tbl:src:' + region, 'spec:drift:' + region}
        for key in {q['key'] for q in self.questions} | set(self.answers):
            if not (key.startswith('td:') or key in relevant):
                continue
            effect = self.answer_effect(key)
            if effect is None or (isinstance(effect, dict) and (effect.get('_default') or effect.get('free'))):
                reasons.append('测试条件、映射或数值解释仍待确认')
                break
        verdict = res['verdict']
        required = [r['row'] for r in rows if r.get('sym') and not r.get('not_tested')]
        for sid in sample_cols:
            if verdict.get(sid) == 'fail':
                continue
            measured = [res['fills'].get(row, {}).get(sid, {}) for row in required]
            if (verdict.get(sid) not in ('pass', 'fail') or not measured
                    or any(c.get('value', '') == '' or c.get('how') == '文本结果' for c in measured)):
                reasons.append('存在缺少有效必测结果的样品')
                break
        if not hasattr(self, "region_verdicts"):
            self.region_verdicts = {}
        self.region_verdicts[region] = ({s: verdict.get(s, "unknown") for s in sample_cols}, reasons)
        reasons = list(dict.fromkeys(reason for _, issues in self.region_verdicts.values() for reason in issues))
        if any(set(statuses) != set(sample_cols) for statuses, _ in self.region_verdicts.values()):
            reasons.append("各实测区的样品集合不一致，请分区核对")
        fails = sum(any(statuses.get(s) == "fail" for statuses, _ in self.region_verdicts.values()) for s in sample_cols)
        passes = sum(all(statuses.get(s) == "pass" for statuses, _ in self.region_verdicts.values()) for s in sample_cols)
        self.verdict = passes, fails, reasons

    # ================================================================= 配方：表单字段
    def check_spec_drift(self, t, rg, tcols, rows, best):
        '''记录里已有规范列（限值/单位）时：不覆盖，只与最匹配的源表逐行核对；不一致的标⚠并登记，定稿前必须处理。'''
        if best is None or best[0] < 0.99:
            self.report['warnings'].append('⚠ 区域 %s 里已有规范值，但没有找到足够匹配的源表，这些规范值本批没有被核对' % rg['id'])
            return
        _sc, alias, tid = best
        md = self.src[alias]
        scols = tablemap.table_columns(md, tid)
        for c in scols:
            c['role'], c['sid'], c['note'] = tablemap.role_of(c['path'])
        mapping, _un = tablemap.map_columns([dict(c) for c in scols], [dict(c) for c in tcols])
        sym_col = next((c['c'] for c in scols if c.get('role') == 'symbol'), None)
        if sym_col is None:
            return
        lrows, _ids = tablemap.logical_rows(md, tid)
        foot = tablemap.footnotes(md, tid)
        src = {}
        for lr in lrows:
            raw = lr['cells'].get(sym_col, ('', []))[0].strip()
            symbol, mark = tablemap.split_symbol_mark(raw, foot)
            symbol = testdata.norm_sym(symbol)
            if symbol:
                src[symbol] = None if symbol in src else (lr, mark)
        tcol = {}
        for tc_, mp in mapping.items():
            if mp['role'] in ('cond', 'min', 'max', 'unit') and mp['from']:
                tcol[mp['role']] = (tc_, mp['from'][0])
        key_of = {'cond': 'cond', 'min': 'lo', 'max': 'hi', 'unit': 'unit'}
        label_of = {'cond':'条件', 'min': '最小', 'max': '最大', 'unit': '单位'}
        blank = lambda s: re.fullmatch(r'[\s—–\-]*', s or '') is not None
        same = lambda a, b: (blank(a) and blank(b)) or norm_text(a) == norm_text(b)
        cells = {(c['r'], c['c']): c for c in self.T.by[t['id']]['cells']}
        ndrift, notin = 0, []
        for r in rows:
            sym = r.get('sym') or ''
            lr = src.get(testdata.norm_sym(sym))
            if lr is None and len(sym) > 2 and sym[0].islower() and sym[1].isupper():
                lr = src.get(testdata.norm_sym(sym[1:]))  # 去掉表注字母，如 aRJ → RJ
            if lr is None:
                notin.append(sym or r.get('name') or '')
                continue
            lr, mark = lr
            marks = [mark] if mark else []
            for _c, (_t, _rr) in lr['cells'].items():
                marks += tablemap.strip_marks([list(x) for x in _rr])[1]
            if any(re.search(r'不测试|不检验|设计保证|不考核', foot[m]) for m in marks if m in foot):
                r['not_tested'] = True  # 规范表注标明“设计保证，不测试”：本批不应有实测值
            bad, written = [], []
            for role, (tc_, scol) in tcol.items():
                old = r.get(key_of[role]) or ''
                new = lr['cells'].get(scol, ('', []))[0]
                if not old.strip() and new.strip():
                    cell = '%s.R%d.C%d' % (t['id'], r['row'], tc_)
                    runs = lr['cells'][scol][1] or [[new, '']]
                    self.ops.append({'id': self.oid(), 'op':'set_cell', 'cell':cell, 'runs':runs,
                                     'from_cols':[scol], 'group': 'spec:%d' % r['row']})
                    r[key_of[role]] = new
                    written.append(cell)
                    self.report["fills"].append({"target":cell,"value":new,"source":"%s:%s.R%d" % (alias,lr["table"],lr["r"]),
                                                 "how":"按相同参数符号补充规范字段"})
                elif not same(old, new):
                    bad.append((role, tc_, old, new))
            if written:
                self.ops.append({'id':self.oid(), 'op':'comment','anchor':written[0], 'group':'spec:%d' % r['row'],
                                 'text':'【规范】%s 按符号 %s 匹配到源表第%d行' % (md.m['name'], sym, lr['r'] + 1),
                                 'links':[{'label':'打开原文','alias':alias,'ref':'%s.R%d' % (lr['table'],lr['r'])}]})
            if not bad:
                continue
            ndrift += 1
            for role, tc_, old, new in bad:
                self.stale.append({'kind': 'stale', 'cell': '%s.R%d.C%d' % (t['id'], r['row'], tc_), 'text': (cells.get((r['row'], tc_)) or {}).get('text', old), 'sample': '', 'param': sym, 'why': '与规范不一致（规范为“%s”）' % new})
            name_c = next((c['c'] for c in tcols if tablemap.role_of(c['path'])[0] == 'name'), bad[0][1])
            self.ops.append({'id': self.oid(), 'op': 'comment', 'anchor': '%s.R%d.C%d' % (t['id'], r['row'], name_c), 'warn': True, 'links': [{'label': '打开原文', 'alias': alias, 'ref': '%s.R%d' % (lr['table'], lr['r'])}], 'text': '⚠【与规范不一致】%s：%s；没有覆盖，请确认以哪个为准' % (sym, '；'.join('%s 记录原有“%s”，规范“%s”' % (label_of[role], old, new) for role, tc_, old, new in bad))})
        self.summary.append('[规范核对] 区域 %s 已有规范值，按符号与 %s:%s 逐行核对（没有覆盖）：%d 行与规范不一致，%d 行在规范表里找不到' % (rg['id'], alias, tid, ndrift, len(notin)))
        if notin:
            self.report['warnings'].append('记录里有、规范表里找不到的参数：' + '、'.join(notin[:8]))

    def rank_tables(self, tcols):
        '''所有候选源表按与目标数据区的匹配得分从高到低排序：[(得分, 别名, 表号)]；得分相同保持出现顺序。'''
        res = []
        for alias, md in self.doc_sources().items():
            for b in md.m['blocks']:
                if b['t'] == 'tbl' and 'in' not in b and not b.get('cont') and b['nrows'] > 2:
                    res.append((tablemap.table_score(md, b['id'], tcols, self.task), alias, b['id']))
        res.sort(key=lambda x: -x[0])
        return res

    def select_table(self, rg, tcols):
        """Use the same ranked sources when copying a table and when checking existing values."""
        ranked = self.rank_tables(tcols)
        if not ranked:
            return None, False
        key = "tbl:src:" + rg["id"]
        eff = self.answer_effect(key)
        if isinstance(eff, dict) and not eff.get("_default"):
            selected = next((r for r in ranked if "%s:%s" % (r[1], r[2]) == eff.get("table")), None)
            if selected:
                return (max(1.0, selected[0]), selected[1], selected[2]), False
            return None, True
        near = [r for r in ranked if r[0] >= 0.5 and ranked[0][0] - r[0] < 0.15]
        if len(near) < 2:
            return ranked[0], False
        options = [{"label": "先留空，这一区暂不采用任何候选表", "effect": {"skip": True}}]
        for score, alias, tid in near:
            source = self.src[alias]
            options.append({"label": "%s:%s %s %s（得分 %.2f）" %
                            (alias, tid, source.m["name"], short(source.by[tid].get("cap") or "无题注", 35), score),
                            "effect": {"table": "%s:%s" % (alias, tid)}})
        self.ask({"key": key, "topic": "规范表选择", "question": "多张源表与 %s 的列匹配，哪张是本数据区的规范依据？" %
                  rg["id"], "options": options, "recommended": 0})
        return None, True

    def mark_region_stale(self, t, rg, tcols):
        '''目标里这块区域已有内容、本批没有提供对应来源（如上一批的仪器清单/有效期）：不覆盖、不清除，标⚠登记，定稿前必须处理。'''
        cells = {(c['r'], c['c']): c for c in self.T.by[t['id']]['cells']}
        label = re.sub(r'\s+', '', rg.get('label') or '表格区')
        first, n = None, 0
        for r in range(rg['rows'][0], rg['rows'][1] + 1):
            for col in sorted(c['c'] for c in tcols):
                c = cells.get((r, col))
                if c and c['text'].strip():
                    cid = '%s.R%d.C%d' % (t['id'], r, col)
                    first = first or cid
                    n += 1
                    self.stale.append({'kind': 'stale', 'cell': cid, 'text': c['text'], 'sample': '', 'param': '%s第%d行' % (label, r), 'why': '原有值本批未核'})
                    break
        if n:
            self.ops.append({'id': self.oid(), 'op': 'comment', 'anchor': first, 'warn': True, 'links': [], 'text': '⚠【原有值，本批未核】%s 的 %d 行内容是模板/上一批留下的，本批没有提供对应来源；没有覆盖，也没有清除。请核对（例如仪器的有效期）后保留或改写' % (label, n)})
            self.report['warnings'].append('区域“%s”有 %d 行原有内容本批未核' % (label, n))

    def mark_stale(self, t, rows, sid_col, filled):
        '''目标样品列里原来就有值（模板/上一批留下的），本批没有对应的新数据：不覆盖、不清空，在草稿里标“原有值，本批未核”，登记到 stale，定稿前逐项检查。filled：本批已写入新值的 (行号, 样品号)。'''
        cells = {(c['r'], c['c']): c for c in self.T.by[t['id']]['cells']}
        for r in rows:
            row = r['row']
            old, first = [], None
            for sid in sorted(sid_col):
                if (row, sid) in filled:
                    continue
                c = cells.get((row, sid_col[sid]))
                txt = ((c or {}).get('text') or '').strip()
                if not txt:
                    continue
                cid = '%s.R%d.C%d' % (t['id'], row, sid_col[sid])
                first = first or cid
                old.append('%s#=%s' % (sid, short(txt, 14)))
                self.stale.append({'kind': 'stale', 'cell': cid, 'text': c['text'], 'sample': sid, 'param': r.get('sym') or r.get('name') or ''})
            if old:
                self.ops.append({'id': self.oid(), 'op': 'comment', 'anchor': first, 'warn': True, 'links': [], 'text': '⚠【原有值，本批未核】%s %s：这是模板/上一批留下的值，本批数据里没有对应结果；没有覆盖，也没有清除。请核对是否仍适用，不适用请清除或改正' % (r.get('sym') or r.get('name') or '', '；'.join(old))})

    def spec_evidence(self, data_models=()):
        target = getattr(self, "T", None)
        target_codes = identity.document_identity(target)["model"] if target and hasattr(target, "m") else set()
        docs = self.doc_sources()
        spec_codes = set().union(*(identity.document_identity(model)["model"] for model in docs.values()))
        data_codes = set(data_models)
        codes = target_codes | spec_codes | data_codes
        key = "src:spec:" + ",".join(sorted(codes) or ["未标明"])
        incomplete = bool(docs and (not target_codes or not spec_codes))
        if len(codes) <= 1 and not incomplete:
            return True
        effect = self.answer_effect(key) or {}
        if effect.get("applicable") is True and not effect.get("_default"):
            if self.answers[key].get("user_quote"):
                self.verified_spec = (key, target_codes, spec_codes)
            return True
        detail = "模板型号 %s，规范型号 %s，测试记录型号 %s。请核对所供规范是否适用于本任务。" % (
            "、".join(sorted(target_codes)) or "未标明", "、".join(sorted(spec_codes)) or "未标明",
            "、".join(sorted(data_codes)) or "未标明")
        self.ask({"key": key, "topic": "规范适用", "question": detail,
                  "options": [{"label": "暂未核对，留空", "effect": {"applicable": False}},
                              {"label": "已核对，本任务适用所供规范", "effect": {"applicable": True}}]})
        self.block_source(key, "规范型号", detail)
        return False

    def block_source(self, key, name, detail):
        blocked = self.report.setdefault("unverified_sources", [])
        if not any(item.get("key") == key for item in blocked):
            blocked.append({"kind": "source", "key": key, "name": name, "detail": detail})
            self.report["warnings"].append("⚠ " + detail)

    def verify_data_source(self, alias, md, sheet):
        target = getattr(self, "T", None)
        template = identity.document_identity(target) if target and hasattr(target, "m") else identity.identity_fields([])
        book = identity.sheet_identity(md, sheet, template)
        codes = self.expected_model_codes(self.doc_sources())
        if not codes:
            codes = sorted(set().union(*(identity.document_identity(model)["model"] for model in self.doc_sources().values())))
        spec_ok = self.spec_evidence(book["model"])
        model_matches = (not codes or set(codes) == book["model"])
        dates_match = not template["date"] or not book["date"] or template["date"] == book["date"]
        if len(template["batch"]) == 1 and template["batch"] == book["batch"] and model_matches and dates_match:
            return spec_ok
        key = "src:verify:" + alias
        effect = self.answer_effect(key) or {}
        if effect.get("ok") is True and not effect.get("_default"):
            return spec_ok
        describe = lambda values: "、".join(sorted(values)) or "未标明"
        detail = "%s：模板批号 %s，测试记录批号 %s；任务型号 %s，记录型号 %s；测试日期 %s / %s。请核对本批原始记录。" % (
            md.m["name"], describe(template["batch"]), describe(book["batch"]), describe(codes),
            describe(book["model"]), describe(template["date"]), describe(book["date"]))
        self.ask({"key": key, "topic": "来源核对", "question": detail,
                  "options": [{"label": "缺少本批记录，实测值暂留空", "effect": {"ok": False}},
                              {"label": "已核对原始记录，所用测试数据属于本批", "effect": {"ok": True}}]})
        self.block_source(key, md.m["name"], detail)
        return False
