"""按来源表、记录行和实测数据规划表格填充。"""
import re

from . import tablemap, testdata
from .common import norm_text, short


class TablePlanningMixin:
    # ================================================================= 配方：表格数据区
    def plan_region(self, t, rg):
        tcols = [{"c": c["c"], "path": c["path"]} for c in rg["cols"]]
        roles = [tablemap.role_of(c["path"])[0] for c in tcols]
        has_samples = any(r == "sample" for r in roles)
        need_spec = any(r in ("name", "cond", "min", "max", "unit") for r in roles)
        rows = []
        if need_spec and not rg.get('filled'):
            best = None
            kw = self.task
            for alias, md in self.doc_sources().items():
                for b in md.m["blocks"]:
                    if b["t"] == "tbl" and "in" not in b and not b.get("cont") and b["nrows"] > 2:
                        sc = tablemap.table_score(md, b["id"], tcols, kw)
                        if best is None or sc > best[0]:
                            best = (sc, alias, b["id"])
            ans = self.answer_effect("tbl:src:%s" % rg["id"])
            if ans and ans.get("table"):
                al, tid = ans["table"].split(":")
                best = (9, al, tid)
            if best and best[0] >= 0.99:
                rows = self.fill_from_table(t, rg, tcols, best[1], best[2])
            elif best and best[0] >= 0.5:
                rows = self.fill_from_table(t, rg, tcols, best[1], best[2])
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
                self.check_spec_drift(t, rg, tcols, rows)
            if not has_samples:
                self.mark_region_stale(t, rg, tcols)
        if has_samples:
            if not rows:
                rows = self.rows_from_existing(t, rg, tcols)
            self.fill_testdata(t, rg, tcols, rows)

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
                elif mp["role"] in ("min", "max", "unit", "cond"):
                    row_info[{"min": "lo", "max": "hi", "unit": "unit", "cond": "cond"}[mp["role"]]] = \
                        "".join(x[0] for x in runs)
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
                elif ro in ("min", "max", "unit"):
                    info[{"min": "lo", "max": "hi", "unit": "unit"}[ro]] = txt
            if info.get("sym"):
                rows.append(info)
        return rows

    def fill_testdata(self, t, rg, tcols, rows):
        sid_col = {}
        for c in tcols:
            ro, sid, _ = tablemap.role_of(c["path"])
            if ro == "sample":
                sid_col[sid] = c["c"]
        sheets = self.sheet_sources()
        if not sheets:
            self.report["unfilled"].append("实测值列（%s）：没有提供测试数据文件（属于“指定来源没提供”，不等于没有数据）" % rg["id"])
            self.mark_stale(t, rows, sid_col, set())
            return
        alias = None
        ans = self.answer_effect("td:source")
        if ans and ans.get("alias"):
            alias = ans["alias"]
        elif len(sheets) > 1:
            q = {"key": "td:source", "topic": "数据来源", "question": "提供了多个表格文件，哪个是本次的测试数据？",
                 "options": [{"label": "%s %s" % (a, m.m["name"]), "effect": {"alias": a}} for a, m in sheets.items()]}
            self.ask(q)
            alias = next(iter(sheets))
        else:
            alias = next(iter(sheets))
        md = self.src[alias]
        sheet = max(md.m["sheets"], key=lambda s: s["nrows"] * max(1, s["ncols"]))
        self.verify_data_source(alias, md, sheet)
        rules = dict(self.rules)
        for k in ("cond_keep", "multi"):
            eff = self.answer_effect("td:" + ("cond" if k == "cond_keep" else "multi"))
            if eff and eff.get("rules"):
                rules.update(eff["rules"])
        parsed = testdata.parse_sheet(sheet, rules)
        if not (set(parsed['samples']) & set(sid_col)):
            # 样品号与记录完全对不上（如记录1#2#3#，数据4#5#6#）：不能猜着填，也不用再问后面的映射题
            key = 'td:samplemap'
            ds, recs = list(parsed['samples']), sorted(sid_col)
            eff = self.answer_effect(key)
            if eff is None:
                opts = []
                if ds and len(ds) == len(recs):
                    opts.append({'label': '按顺序对应：' + '，'.join('数据%s号→记录%s#' % (d, r) for d, r in zip(ds, recs)), 'effect': {'samplemap': dict(zip(ds, recs))}})
                opts.append({'label': '不对应：这不是记录里这几只样品的数据，实测值本次不填', 'effect': {'skip': True}})
                self.ask({'key': key, 'topic': '样品对应', 'question': '数据文件 %s 的样品号是 %s，记录里的样品号是 %s，两边没有一个相同。数据能对应到记录的哪几只？' % (md.m['name'], '、'.join(ds) or '无', '、'.join(recs) or '无'), 'options': opts, 'recommended': len(opts) - 1})
                eff = {'skip': True}
            sm = eff.get('samplemap') if isinstance(eff, dict) else None
            if not sm:
                self.report['unfilled'].append('实测值列（%s）：数据文件样品号 %s 与记录样品号 %s 没有交集，没有写入任何实测值' % (rg['id'], '、'.join(ds), '、'.join(recs)))
                self.summary.append('[测试数据] %s：样品号与记录无交集，未写入实测值（记录里的原有值已标“本批未核”）' % md.m['name'])
                self.mark_stale(t, rows, sid_col, set())
                return
            parsed['samples'] = [sm.get(s, s) for s in parsed['samples']]
            parsed['sample_cols'] = {sm.get(s, s): c for s, c in parsed['sample_cols'].items()}
            for b in parsed['blocks']:
                b['values'] = {sm.get(s, s): v for s, v in b['values'].items()}
            self.report['warnings'].append('⚠ 样品号按回答对应：' + '，'.join('数据%s号→记录%s#' % kv for kv in sm.items()))
        targets = [{"row": r["row"], "sym": r.get("sym"), "name": r.get("name", ""), "lo": r.get("lo"), "hi": r.get("hi"),
                    "unit": r.get("unit", ""), "not_tested": r.get("not_tested")} for r in rows]
        ans_map = {}
        for k, v in self.answers.items():
            if k.startswith("td:"):
                e = self.answer_effect(k)
                if e is not None:
                    ans_map[k] = e
        res = testdata.build(parsed, targets, rules, self.aliases, ans_map)
        for q in res["questions"]:
            self.ask(q)
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
        shift_r, shift_n = self.row_shift.get(t["id"], (10 ** 9, 0))
        filled = set()
        merged_lab = []
        sym_of = {r["row"]: (r.get("sym") or r.get("name") or "") for r in rows}
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
                links.append({"label": "%s#" % sid, "alias": alias, "ref": cell["ranges"][0] if len(cell["ranges"]) == 1
                              else cell["ranges"][0].split(":")[0] + ":" + cell["ranges"][-1].split(":")[-1]})
                notes.add(cell["how"])
                if cell["check"] != "ok" or cell["conf"] < 0.9:
                    warn = True
                self.report["fills"].append({"target": cid, "value": cell["value"], "source": "%s:%s" % (alias, ";".join(cell["ranges"])),
                                             "how": "%s；%s" % ("/".join(cell["hows"]), cell["how"]), "raw": cell["raw"],
                                             "check": cell["check_msg"]})
            if first_cell:
                c0 = next(iter(fill.values()))
                if len(c0["labels"]) > 1:
                    notes.add('由%d个不同标签合并取范围，请核对是否为同一工况/通道' % len(c0["labels"]))
                    warn = True
                    merged_lab.append('%s←%s' % (sym_of.get(row, row), '、'.join(c0["labels"][:3]) + ('…' if len(c0["labels"]) > 3 else '')))
                txt = "%s【实测】%s 标签 %s（%s）；%s%s" % (
                    "⚠" if warn else "", md.m["name"], "、".join(c0["labels"][:4]) + ("…" if len(c0["labels"]) > 4 else ""),
                    "/".join(c0["hows"]), "；".join(sorted(notes)),
                    ("；" + "；".join(sorted({c["check_msg"] for c in fill.values() if c["check_msg"]}))) if warn else "")
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
        self.record_verdict(alias, rg['id'], rows, sid_col, res)

    def record_verdict(self, alias, region, rows, sample_cols, res):
        """只汇总记录中实际出现的样品，待确认的测量或来源不能产生批次结论。"""
        reasons = []
        if any(i.get('key') == 'src:verify:' + alias for i in self.report.get('unverified_sources', [])):
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
            if (sid not in verdict or not measured
                    or any(c.get('value', '') == '' or c.get('how') == '文本结果' for c in measured)):
                reasons.append('存在缺少有效必测结果的样品')
                break
        passes = sum(verdict.get(sid) == 'pass' for sid in sample_cols)
        fails = sum(verdict.get(sid) == 'fail' for sid in sample_cols)
        self.verdict = passes, fails, reasons

    # ================================================================= 配方：表单字段
    def check_spec_drift(self, t, rg, tcols, rows):
        '''记录里已有规范列（限值/单位）时：不覆盖，只与最匹配的源表逐行核对；不一致的标⚠并登记，定稿前必须处理。'''
        best = None
        for alias, md in self.doc_sources().items():
            for b in md.m['blocks']:
                if b['t'] == 'tbl' and 'in' not in b and not b.get('cont') and b['nrows'] > 2:
                    sc = tablemap.table_score(md, b['id'], tcols, self.task)
                    if best is None or sc > best[0]:
                        best = (sc, alias, b['id'])
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
            s = testdata.norm_sym(lr['cells'].get(sym_col, ('', []))[0])
            if s:
                src.setdefault(s, lr)
        tcol = {}
        for tc_, mp in mapping.items():
            if mp['role'] in ('min', 'max', 'unit') and mp['from']:
                tcol[mp['role']] = (tc_, mp['from'][0])
        key_of = {'min': 'lo', 'max': 'hi', 'unit': 'unit'}
        label_of = {'min': '最小', 'max': '最大', 'unit': '单位'}
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
            marks = []
            for _c, (_t, _rr) in lr['cells'].items():
                marks += tablemap.strip_marks([list(x) for x in _rr])[1]
            if any(re.search(r'不测试|不检验|设计保证|不考核', foot[m]) for m in marks if m in foot):
                r['not_tested'] = True  # 规范表注标明“设计保证，不测试”：本批不应有实测值
            bad = []
            for role, (tc_, scol) in tcol.items():
                old = r.get(key_of[role]) or ''
                new = lr['cells'].get(scol, ('', []))[0]
                if not same(old, new):
                    bad.append((role, tc_, old, new))
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

    def table_tie(self, rg, ranked):
        '''得分最高的源表不能静默独占：第二张得分相差不到 0.15 时出题让用户选；
        差得多但仍像样（>=0.5）的，返回一句提示，由调用方放进批注。'''
        if len(ranked) < 2 or ranked[1][0] < 0.5:
            return None
        near = [x for x in ranked[:3] if x[0] >= 0.5 and ranked[0][0] - x[0] < 0.15]

        def desc(x):
            return '%s:%s %s（得分%.2f）' % (x[1], x[2], short(self.src[x[1]].by[x[2]].get('cap') or '无题注', 20), x[0])
        if len(near) >= 2:
            self.ask({'key': 'tbl:src:%s' % rg['id'], 'topic': '源表选择',
                      'question': '数据区 %s 有 %d 张源表得分相近：%s。用哪张填？' % (rg['id'], len(near), '；'.join(desc(x) for x in near)),
                      'options': [{'label': desc(x), 'effect': {'table': '%s:%s' % (x[1], x[2])}} for x in near] +
                                 [{'label': '都不用，这一区留空，我来填', 'effect': {'skip': True}}],
                      'recommended': 0})
            return None
        return '另有相近的源表 %s，本次采用 %s；要换表请告诉助手（可用 show 对比两张表）' % (desc(ranked[1]), desc(ranked[0]))

    def resolve_drift(self, t, rg, tcols, alias, drifts, tcol):
        '''记录里已有的规范值与规范表不一致：不再只告警，出“与规范不一致”问题，由用户定：
        以规范为准（覆盖，旧值写进批注，推荐）/ 保留原值（标⚠）/ 留空。回答绑定文件指纹，文件换版后重新问。
        返回处理方式的简短说明，给规划摘要用。'''
        if not drifts:
            return ''
        label_of = {'min': '最小', 'max': '最大', 'unit': '单位'}
        key_of = {'min': 'lo', 'max': 'hi', 'unit': 'unit'}
        key = 'spec:drift:%s' % rg['id']
        n = sum(len(d['bad']) for d in drifts)
        eg = '；'.join('%s%s 记录“%s”规范“%s”' % (d['sym'], label_of[d['bad'][0][0]], short(d['bad'][0][2], 8) or '空', short(d['bad'][0][3], 8) or '空') for d in drifts[:3])
        eff = self.answer_effect(key)
        waiting = eff is None
        if waiting:
            self.ask({'key': key, 'topic': '与规范不一致',
                      'question': '记录里已有的规范值有 %d 行与规范表不一致（共 %d 格，如 %s）。怎么处理？' % (len(drifts), n, eg),
                      'options': [{'label': '以规范为准，覆盖原值（旧值写进批注）', 'effect': {'mode': 'spec'}},
                                  {'label': '保留原值，批注标⚠', 'effect': {'mode': 'keep'}},
                                  {'label': '这些格留空，我来填', 'effect': {'mode': 'blank'}}],
                      'recommended': 0})
            eff = {'mode': 'spec'}
        if isinstance(eff, dict) and eff.get('mode'):
            mode = eff['mode']
        else:
            mode = 'keep' if isinstance(eff, dict) and eff.get('free') else 'spec'      # 自由文本回答不当作“以规范为准”，不动原值
        dn = self.default_note(eff)
        who = '暂按推荐（尚未回答）' if waiting else '按回答'
        tail = ('；' + dn) if dn else ''
        name_c = next((c['c'] for c in tcols if tablemap.role_of(c['path'])[0] == 'name'), tcols[0]['c'])
        blanks = []
        for d in drifts:
            r, lr, sym, bad = d['r'], d['lr'], d['sym'], d['bad']
            row = r['row']
            what = '；'.join('%s 记录原有“%s”，规范“%s”' % (label_of[role], old or '空', new or '空') for role, _tc, old, new in bad)
            for role, tc_, old, new in bad:
                cid = '%s.R%d.C%d' % (t['id'], row, tc_)
                if mode == 'spec':
                    scol = tcol[role][1]
                    rr = tablemap.strip_marks([list(x) for x in lr['cells'].get(scol, ('', []))[1]])[0]
                    runs = [[x[0].replace('\n', ''), x[1]] for x in rr if x[0]]
                    self.ops.append({'id': self.oid(), 'op': 'set_cell', 'cell': cid, 'runs': runs, 'group': 'drift:%d' % row, 'from_cols': [scol]})
                    r[key_of[role]] = new
                    self.report['fills'].append({'target': cid, 'value': ''.join(x[0] for x in runs), 'source': '%s:%s.R%d' % (alias, lr['table'], lr['r']),
                                                 'how': '与规范不一致：%s以规范为准覆盖' % who, 'raw': '记录原有“%s”' % old})
                elif mode == 'blank':
                    self.ops.append({'id': self.oid(), 'op': 'set_cell', 'cell': cid, 'runs': [], 'group': 'drift:%d' % row})
                    r[key_of[role]] = ''
            if mode == 'spec':
                text = '【与规范不一致】%s：%s；%s以规范为准，已覆盖，旧值记在此处%s' % (sym, what, who, tail)
            elif mode == 'blank':
                text = '⚠【与规范不一致】%s：%s；%s已清空这些格，待人工填写%s' % (sym, what, who, tail)
                blanks.append(sym)
            else:
                text = '⚠【与规范不一致】%s：%s；%s保留原值，没有覆盖%s' % (sym, what, who, tail)
            self.ops.append({'id': self.oid(), 'op': 'comment', 'anchor': '%s.R%d.C%d' % (t['id'], row, name_c), 'warn': mode != 'spec' or waiting or bool(dn),
                             'links': [{'label': '打开原文', 'alias': alias, 'ref': '%s.R%d' % (lr['table'], lr['r'])}], 'text': text, 'group': 'drift:%d' % row})
        if mode == 'keep':
            self.report['warnings'].append('区域 %s 有 %d 行规范值与规范表不一致，%s保留原值（批注已标⚠）' % (rg['id'], len(drifts), who))
        elif mode == 'blank':
            self.report['unfilled'].append('与规范不一致、%s已清空的规范值：%s（待人工填写）' % (who, '、'.join(blanks[:8])))
        return {'spec': '以规范为准', 'keep': '保留原值', 'blank': '留空'}.get(mode, mode) + ('（待回答）' if waiting else '')

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

    def verify_data_source(self, alias, md, sheet):
        '''填写前先核对来源：数据文件的文件名/内容里能不能找到型号。数字对得上不能证明来源对——找不到就问，并在定稿前拦截。'''
        codes = self._model_codes(self.doc_sources())
        if not codes:
            return
        strs = [str(v) for row in sheet['rows'][:300] for v in row if isinstance(v, str)]
        hay = (md.m.get('name', '') + ' ' + ' '.join(strs)).lower()
        if any(c.lower() in hay for c in codes[:3]):
            return
        key = 'src:verify:' + alias
        eff = self.answer_effect(key)
        if eff is None:
            self.ask({'key': key, 'topic': '来源核对', 'question': '测试数据文件 %s 的文件名和内容里都没有出现型号 %s（也没有批号/版本信息），无法自动确认它就是本批该产品的数据。数字对得上也不能证明来源正确，请确认：' % (md.m['name'], '/'.join(codes[:2])), 'options': [{'label': '已核对，该文件就是本批 %s 的数据' % codes[0], 'effect': {'ok': True}}, {'label': '暂未核对，先出草稿，定稿前我再核对', 'effect': {'ok': False}}], 'recommended': 1})
            eff = {'ok': False}
        if isinstance(eff, dict) and eff.get('ok') and not eff.get('_default'):
            return
        detail = '数据来源未核对：%s 的文件名和内容里没有型号 %s 的任何信息，无法确认属于本批该产品' % (md.m['name'], codes[0])
        self.report.setdefault('unverified_sources', []).append({'kind': 'source', 'key': key, 'name': md.m['name'], 'detail': detail})
        self.report['warnings'].append('⚠ ' + detail)
