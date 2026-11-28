import unittest
from test_measurement_identity import mapping, value
from test_measurement_tables import table
from dilib.planner import Planner


class ProgramMeasurement(unittest.TestCase):
    def test_single_block_uses_explicit_ranges_without_moving_samples(self):
        rows = [["item",1,2],["R_T_AUX_Vnorm",2,8],["",3,9],["",6,10]]
        targets = [{"sym":"R","row":1},{"sym":"T","row":2}]
        order = [{"start":1,"end":2,"symbol":"T"},{"start":3,"end":3,"symbol":"R"}]
        res = mapping(rows, targets, rules={"cond_keep":["Vnorm"]},answers={"td:map:R_T_AUX":{"segments":order}})
        self.assertEqual(value(res, 1), "6")
        self.assertEqual(value(res, 2, "2"), "8~9")
        self.assertEqual(res["fills"][2]["1"]["ranges"], ["Data!B2:B3"])
        order[1]["start"] = 2
        res = mapping(rows, targets, rules={"cond_keep":["Vnorm"]},answers={"td:map:R_T_AUX":{"segments":order}})
        self.assertFalse(value(res))

    def test_confirmed_judgment_column_compares_each_signed_raw_value(self):
        rows = [["item","unit",1,2],["LOW","mV","45","20"],["","mV","32","85"]]
        target = [{"sym":"LOW","row":1,"lo":"-80","unit":"mV","column_type":"measurement","name":"低阈值"}]
        res = mapping(rows,target,answers={"td:interpret:LOW":{"kind":"limits","sign":"negate"}})
        self.assertEqual((value(res),value(res,1,"2")), ("合格","不合格"))
        self.assertEqual(res["fills"][1]["1"]["value_kind"], "limits")
        self.assertEqual(res["fills"][1]["1"]["sign"], "negate")

    def test_qualified_table_fills_only_blank_specification_columns_by_symbol(self):
        p = object.__new__(Planner)
        p.task, p.n, p.answers, p.ops, p.summary, p.stale = "", 0, {}, [], [], []
        p.report = {"fills":[],"warnings":[]}
        hdr=["符号","测试条件","最大值","单位"]
        p.src={"S":table("source",[hdr,["B","dry","7","V"],["A","wet","9","V"]])}
        p.T=table("target",[hdr,["A","","","mV"],["X","","",""]])
        cols = [{"c":c,"path":[s]} for c,s in enumerate(hdr)]
        rg={"id":"T0.D1","rows":[1,2],"header":[0,0]}
        rows=p.rows_from_existing({"id":"T0"},rg,cols)
        p.check_spec_drift({"id":"T0"},rg,cols,rows,(1.0,"S","T0"))
        self.assertEqual([(o["cell"],o["runs"][0][0]) for o in p.ops if o["op"]=="set_cell"],
                         [("T0.R1.C1","wet"),("T0.R1.C2","9")])
        self.assertEqual((rows[0]["hi"],rows[0]["unit"]),("9","mV"))

    def test_partial_manifest_condition_records_actual_and_blocks_full_qualification(self):
        rows = [["item","unit",1],["R","mV",4]]
        targets = [{"sym":"R","row":1,"lo":"3","unit":"mV","cond":"VDD=3V, RL=2kΩ"}]
        res = mapping(rows,targets,answers={"td:conditions:proof":{"conditions":{"R":"VDD=3V"}}})
        self.assertEqual(value(res), "4")
        self.assertEqual(res["verdict"], {"1":"unknown"})
        self.assertEqual(res["condition_gaps"]["R"], ("VDD=3V", "VDD=3V, RL=2kΩ"))
        self.assertEqual(res["fills"][1]["1"]["actual_condition"], "VDD=3V")
        self.assertIn("未全部",res["fills"][1]["1"]["check_msg"])

    def test_status_decision_fact_describes_the_real_output_type(self):
        rows = [["item",1],["R",1]]
        targets = [{"sym":"R","row":1,"hi":"5","unit":"Mbps","column_type":"measurement"}]
        res = mapping(rows,targets,answers={"td:interpret:R":{"kind":"flags","pass_code":1}})
        self.assertEqual(value(res), "合格")
        self.assertEqual(res["fills"][1]["1"]["effective_column_type"], "judgment")

    def test_explicit_footnote_after_table_excludes_plain_marked_symbol(self):
        from dilib.content import Model
        p = object.__new__(Planner)
        p.task, p.n, p.answers, p.ops, p.summary, p.stale = "", 0, {}, [], [], []
        p.report = {"fills":[],"warnings":[]}
        hdr=["符号","测试条件","最大值","单位"]
        md=table("source",[hdr,["aRJ","dry","7","V"],["R","wet","9","V"]])
        md.m["blocks"].append({"t":"p","id":"P1","text":"注：a 由设计保证，不测试"})
        p.src={"S":Model(md.m,"S")}
        p.T=table("target",[hdr,["RJ","","",""],["R","","",""]])
        rg={"id":"T0.D1","rows":[1,2],"header":[0,0],"cols":[{"c":c,"path":[s]} for c,s in enumerate(hdr)]}
        p.rank_tables=lambda _:[(1,"S","T0")]
        before = p.affected_spec_rows({"id":"T0"},rg)
        self.assertEqual([(r["sym"],r.get("not_tested",False)) for r in before],[("RJ",True),("R",False)])
        p.check_spec_drift({"id":"T0"},rg,rg["cols"],before,(1,"S","T0"))
        self.assertEqual(before[0]["hi"],"7")
        self.assertTrue(before[0]["not_tested"])

    def test_explicit_cover_title_avoids_adjacent_metadata(self):
        p = object.__new__(Planner)
        p.n,p.rules,p.ops,p.report = 0,{},[],{"fills":[]}
        md=table("测试规范.docx",[["名称"]])
        md.m.update(title="测试规范封面声明",blocks=[{"t":"p","id":"P0","text":"测试规范"}])
        p.field_basis("依据","T0.R1.C0",{"S1":md})
        self.assertEqual(p.report["fills"][0]["value"],"测试规范")

    def test_unit_question_waits_for_the_decision_between_state_and_measurement(self):
        rows = [["item",1],["R",1]]
        targets = [{"sym":"R","row":1,"lo":"2","unit":"V","column_type":"measurement"}]
        res = mapping(rows,targets)
        self.assertTrue(any(q["key"] == "td:interpret:R" for q in res["questions"]))
        self.assertFalse(any(q["key"].startswith("td:units:") for q in res["questions"]))
        res = mapping(rows,targets,answers={"td:interpret:R":{"kind":"flags","pass_code":1}})
        self.assertEqual(value(res),"合格")
        self.assertFalse(any(q["key"].startswith("td:units:") for q in res["questions"]))

    def test_spec_confirmation_counts_blank_supported_fields_and_corresponding_measurements(self):
        p = object.__new__(Planner)
        p.task,p.ops = "",[]
        hdr=["符号","测试条件","最大值","单位"]
        p.src={"S":table("source",[hdr,["R","dry","7","V"],["T","wet","9","V"]])}
        p.T=table("target",[hdr+["1#"],["R","","7","",""],["T","","","",""],["X","","","",""]])
        rg={"id":"T0.D1","rows":[1,3],"header":[0,0],"cols":[{"c":c,"path":[s]} for c,s in enumerate(hdr+["1#"])]}
        rows=p.affected_spec_rows({"id":"T0"},rg)
        q={"question":"采用来源？"}
        p.add_spec_impact(q,{"id":"T0"},rows,{"1":4})
        self.assertEqual(q["impact_by_kind"],{"规范字段":5,"规范对应实测位置":2})
        self.assertEqual(len(q["affected_cells"]),7)
        self.assertFalse(p.ops)
        self.assertFalse(any(c["target"].startswith("T0.R3") for c in q["affected_cells"]))
        self.assertFalse(any(c["target"]=="T0.R1.C2" for c in q["affected_cells"]))

    def test_no_suffix_evidence_does_not_include_unrelated_source_labels(self):
        rows=[["item",1],["R",7],["UNRELATED",20],["A_Vnorm",3]]
        targets=[{"sym":"R","row":1},{"sym":"A","row":2}]
        res=mapping(rows,targets,rules={"cond_keep":["Vnorm"]})
        q=next(q for q in res["questions"] if q["key"]=="td:no_suffix")
        self.assertEqual(q["source_labels"],["R"])
