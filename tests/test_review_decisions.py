import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_selfcheck import BODY, COMMENTS, CT, DOC_RELS, W, doc_xml, make_docx
from dilib import cli, selfcheck
from dilib.common import TOOL_AUTHOR
from dilib.job import Job
from dilib.ooxml import Package, w
from dilib.planner import Planner


W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
W15 = "http://schemas.microsoft.com/office/word/2012/wordml"
CID = "http://schemas.microsoft.com/office/word/2016/wordml/cid"
CEX = "http://schemas.microsoft.com/office/word/2018/wordml/cex"


class ReviewDecisionsTests(unittest.TestCase):
    def test_reordered_instructions_are_not_a_new_batch_confirmation(self):
        from dilib.decisions import needs_reviewer_reply
        request = "把表格填入记录。按样品计算范围。仅处理本任务。"
        self.assertTrue(needs_reviewer_reply("src:verify:S2", {"ok": True}, request,
                                             "把表格填入记录。仅处理本任务。"))
        self.assertFalse(needs_reviewer_reply("src:verify:S2", {"ok": True}, request,
                                              "已核对，该表属于本批。"))

    def test_draft_request_cannot_authorize_defaults(self):
        for instruction in ("先生成带来源批注的草稿和报告。", "不要使用默认值", "请先询问是否采用推荐项。"):
            self.invoke("draft", self.job.dir, "--defaults", "--user-quote", instruction, rc=2)
        self.assertFalse(self.job.drafts())

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        target, source = self.root / "test.docx", self.root / "source.docx"
        make_docx(target)
        make_docx(source)
        self.job = Job.create(str(target), [str(source)], out_root=temp.name, name="job")
        self.draft = Path(self.job.next_draft()[0])
        self.draft.parent.mkdir()

    def invoke(self, *args, rc=0):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            actual = cli.main(list(map(str, args)))
        self.assertEqual(actual, rc, output.getvalue())
        return output.getvalue()

    def modern_draft(self):
        comments = '<w:comments xmlns:w="%s" xmlns:w14="%s">' % (W, W14)
        for i, author in enumerate([TOOL_AUTHOR, "审核甲", "审核乙"]):
            comments += ('<w:comment w:id="%d" w:author="%s"><w:p w14:paraId="%08X">'
                         '<w:r><w:t>comment %d</w:t></w:r></w:p></w:comment>') % (i, author, 10+i, i)
        mods = {"word/comments.xml": comments + "</w:comments>"}
        ex = ('<w15:commentsEx xmlns:w15="%s"><w15:commentEx w15:paraId="0000000A" w15:done="1"/>'
              '<w15:commentEx w15:paraId="0000000B" w15:paraIdParent="0000000A"/>'
              '<w15:commentEx w15:paraId="0000000C" w15:paraIdParent="0000000B"/></w15:commentsEx>') % W15
        ids = '<c:commentsIds xmlns:c="%s">' % CID
        ext = ('<e:commentsExtensible xmlns:e="%s" xmlns:test="urn:test" '
               'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" mc:Ignorable="test">') % CEX
        for i in range(3):
            ids += '<c:commentId c:paraId="%08X" c:durableId="%08X"/>' % (10+i, 100+i)
            ext += '<e:commentExtensible e:durableId="%08X" test:foreign="00000064"/>' % (100+i)
        ct, rels = CT, DOC_RELS
        for version, part, data in [("2011", "commentsExtended", ex), ("2016/09", "commentsIds", ids + "</c:commentsIds>"),
                                    ("2018/08", "commentsExtensible", ext + "</e:commentsExtensible>")]:
            mods["word/%s.xml" % part] = data
            ct = ct.replace("</Types>", '<Override PartName="/word/%s.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.%s+xml"/></Types>' % (part, part))
            rels = rels.replace("</Relationships>", '<Relationship Id="r%s" Target="%s.xml" Type="http://schemas.microsoft.com/office/%s/relationships/%s"/></Relationships>' % (part, part, version, part))
        mods.update({"[Content_Types].xml": ct, "word/_rels/document.xml.rels": rels})
        make_docx(self.draft, mods)

    def test_review_shows_resolved_thread_and_reply_parents(self):
        self.modern_draft()
        output = self.invoke("review", self.job.dir)
        self.assertIn("已解决", output)
        self.assertIn("回复 #0", output)
        self.assertIn("回复 #1", output)
        self.assertIn("comment 0", output)

    def test_final_keep_promotes_reply_and_preserves_unrelated_extension_entries(self):
        self.modern_draft()
        self.invoke("final", self.job.dir, "--reviewer-comments", "keep")
        pkg = Package(self.job.p("终稿", "test_终稿.docx"))
        comments = pkg.xml("word/comments.xml").findall(w("comment"))
        self.assertEqual([c.get(w("id")) for c in comments], ["1", "2"])
        refs = [c.get(w("id")) for c in pkg.xml("word/document.xml").iter(w("commentReference"))]
        self.assertEqual(refs, ["1"])
        ex = list(pkg.xml("word/commentsExtended.xml"))
        self.assertEqual([e.get("{%s}paraIdParent" % W15) for e in ex], [None, "0000000B"])
        self.assertEqual([e.get("{%s}durableId" % CEX) for e in pkg.xml("word/commentsExtensible.xml")],
                         ["00000065", "00000066"])
        self.assertEqual(len(list(pkg.xml("word/commentsIds.xml"))), 2)

    def test_final_asks_about_replies_and_drop_removes_entire_thread(self):
        self.modern_draft()
        self.assertIn("审核甲", self.invoke("final", self.job.dir, rc=2))
        self.invoke("final", self.job.dir, "--reviewer-comments", "drop")
        pkg = Package(self.job.p("终稿", "test_终稿.docx"))
        for part in ["comments", "commentsIds", "commentsExtended", "commentsExtensible"]:
            self.assertEqual(len(pkg.xml("word/%s.xml" % part)), 0)
        self.assertEqual(list(pkg.xml("word/document.xml").iter(w("commentReference"))), [])

    def test_structural_check_distinguishes_valid_reply_and_missing_parent(self):
        self.modern_draft()
        self.assertNotIn("comment-orphan", selfcheck.check_file(str(self.draft)).codes())
        pkg = Package(self.draft)
        list(pkg.xml("word/commentsExtended.xml"))[1].set("{%s}paraIdParent" % W15, "77777777")
        pkg.save(self.draft)
        self.assertIn("comment-parent", selfcheck.check_file(str(self.draft)).codes(selfcheck.ERROR))

    def attach_image(self, pkg):
        from xml.etree import ElementTree as ET
        root = pkg.xml("word/comments.xml")
        drawing = ET.SubElement(root[1].find(".//" + w("r")), w("drawing"))
        rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        image = ET.SubElement(drawing, "{http://schemas.openxmlformats.org/drawingml/2006/main}blip")
        image.set("{%s}embed" % rel, pkg.rels("word/comments.xml").add(rel + "/image", "media/test.png"))
        sample = Path(__file__).resolve().parents[1] / "traced-template-fill/assets/offline-ocr.png"
        pkg.add_part("word/media/test.png", sample.read_bytes())
        pkg.ensure_override("word/media/test.png", "image/png")

    def test_stripping_template_preserves_reviewer_replies_and_attached_media(self):
        self.modern_draft()
        pkg = Package(self.draft)
        pkg.xml("word/comments.xml")[0].set(w("author"), "模板作者")
        self.job.data["template_comments"] = [["模板作者", "comment 0"]]
        self.job.save()
        self.attach_image(pkg)
        pkg.save(self.draft)
        self.invoke("final", self.job.dir, "--strip-template-comments", "--reviewer-comments", "keep")
        saved = Package(self.job.p("终稿", "test_终稿.docx"))
        self.assertEqual([c.get(w("id")) for c in saved.xml("word/comments.xml")], ["1", "2"])
        self.assertEqual([r[2] for r in saved.rels("word/comments.xml").items()], ["media/test.png"])

    def test_batch_carries_one_quote_into_each_source_answer(self):
        other = Job.create(self.job.data["target"]["path"], [self.job.data["sources"][0]["path"]],
                           out_root=str(self.root), name="job2")
        q = {"id": "Q1", "topic": "来源核对", "key": "src:verify:S1", "question": "核对来源",
             "options": [{"label": "已核对", "effect": {"ok": True}}], "recommended": 0}
        for job in (self.job, other):
            job.write("questions.json", [dict(q, input_fp=job.fingerprint())])
        pending = dict(q, id="B1", jobs=[[self.job.dir, "Q1"], [other.dir, "Q1"]], options=["已核对"])
        manifest = self.root / "batch.json"
        manifest.write_text(json.dumps({"jobs": [self.job.dir, other.dir], "questions": [pending]}), "utf-8")
        self.assertIn("--user-quote", self.invoke("batch", "answer", manifest, "B1=1", rc=2))
        self.assertIn('batch plan', self.invoke('batch', 'answer', manifest, 'B1=1', '--user-quote', '原答复', rc=2))
        pending.update(option_details=q['options'], input_fps={j.dir: j.fingerprint() for j in (self.job, other)})
        manifest.write_text(json.dumps({'jobs': [self.job.dir, other.dir], 'questions': [pending]}), 'utf8')
        self.invoke("batch", "answer", manifest, "B1=1", "--user-quote", "这两份来源已核实")
        for job in (self.job, other):
            record = json.loads(Path(job.p("decisions.jsonl")).read_text("utf-8"))
            self.assertEqual(record["user_quote"], "这两份来源已核实")
            self.assertEqual(record["answers"][0]["key"], "src:verify:S1")

    def test_sensitive_answers_require_and_preserve_original_quote(self):
        question = {"id": "Q1", "topic": "来源核对", "key": "src:verify:S1", "question": "核对来源",
                    "options": [{"label": "已核对", "effect": {"ok": True}}], "recommended": 0}
        for key in ["src:verify:S1", "td:samplemap", "src:unread:S1", "src:ocr:S1:abcdef"]:
            question["key"] = key
            self.job.write("questions.json", [dict(question, input_fp=self.job.fingerprint())])
            self.assertIn("--user-quote", self.invoke("answer", self.job.dir, "Q1=1", rc=2))
            self.assertNotIn(key, self.job.read("answers.json", {}))
            quote = '我核过这份：“1 号”对应。'
            self.invoke("answer", self.job.dir, "Q1=1", "--user-quote", quote)
            self.assertEqual(self.job.read("answers.json")[key]["user_quote"], quote)
        records = [json.loads(line) for line in Path(self.job.p("decisions.jsonl")).read_text("utf-8").splitlines()]
        self.assertEqual(len(records), 4)
        self.assertTrue(all(r["user_quote"] == quote for r in records))

    def relay_and_draft(self):
        q = {"id": "Q1", "key": "src:verify:S1", "topic": "来源核对", "question": "是否已核对来源？",
             "options": [{"label": "待核对", "effect": {"ok": False}}, {"label": "已核对", "effect": {"ok": True}}],
             "input_fp": self.job.fingerprint(), "recommended": 0}
        self.job.write("questions.json", [q])
        self.invoke("answer", self.job.dir, "Q1=2", "--user-quote", "来源已核对", "--reviewer", "转述姓名")
        self.invoke("draft", self.job.dir)

    def test_strict_final_requires_independent_confirmation_of_relay(self):
        self.relay_and_draft()
        args = ("final", self.job.dir, "--template-comments", "drop", "--reviewer-comments", "drop")
        self.assertIn("agent_relay", self.invoke(*args))
        self.assertIn("确认 N", self.invoke(*args, "--require-signed", "--accept-unverified", "--user-quote", "接受", rc=2))
        prefs = self.root / "preferences.json"
        prefs.write_text('{"require_signed": true}', encoding="utf-8")
        with patch("dilib.decisions.project_dir", return_value=self.root):
            self.assertIn("确认 N", self.invoke(*args, rc=2))

    def test_terminal_confirmation_requires_tty_and_records_entered_option(self):
        self.relay_and_draft()
        with patch("sys.stdin", io.StringIO("2\n")):
            self.assertIn("交互", self.invoke("confirm", self.job.dir, rc=2))
        with patch("sys.stdin", io.StringIO("2\n")) as terminal, patch.object(terminal, "isatty", return_value=True):
            self.invoke("confirm", self.job.dir)
        self.invoke("final", self.job.dir, "--require-signed", "--template-comments", "drop")
        record = json.loads(Path(self.job.p("decisions.jsonl")).read_text("utf-8").splitlines()[-1])
        self.assertEqual(record["source"], "confirm_cli")
        self.assertTrue(record["author"])
        report = Path(self.job.p("终稿", "终稿审核报告.md")).read_text("utf-8")
        for value in ("confirm_cli", record["author"], record["hash"], "src:verify:S1", record["time"]):
            self.assertIn(value, report)

    def test_signed_word_reply_reaffirms_effect_and_keeps_chain_on_retry(self):
        from xml.etree import ElementTree as ET
        from dilib import finalize
        self.relay_and_draft()
        pkg = Package(self.draft)
        comments = pkg.xml("word/comments.xml")
        roots = [el for el in comments if "【Word 核对 Q1】" in "".join(el.itertext())]
        self.assertEqual(len(roots), 1)
        list(roots[0].iter(w("p")))[-1].set(finalize.PARA_ID, "000000AB")
        reply = ET.SubElement(comments, w("comment"), {w("author"): "复核员", w("id"): "989"})
        p = ET.SubElement(reply, w("p"), {finalize.PARA_ID: "000000AC"})
        ET.SubElement(ET.SubElement(p, w("r")), w("t")).text = "确认 2"
        ext = ET.Element("{%s}commentsEx" % W15)
        ET.SubElement(ext, "{%s}commentEx" % W15, {finalize.EX_ID: "000000AB"})
        ET.SubElement(ext, "{%s}commentEx" % W15, {finalize.EX_ID: "000000AC", finalize.EX_PARENT: "000000AB"})
        pkg.new_xml("word/commentsExtended.xml", ext, [("w15", W15)])
        pkg.save(self.draft)
        for _ in range(2):
            self.invoke("final", self.job.dir, "--require-signed", "--reviewer-comments", "drop", "--template-comments", "drop")
        log = [json.loads(line) for line in Path(self.job.p("decisions.jsonl")).read_text("utf-8").splitlines()]
        self.assertEqual([r["source"] for r in log], ["agent_relay", "word_reply"])
        self.assertEqual(log[1]["prev_hash"], log[0]["hash"])
        self.assertEqual(log[1]["author"], "复核员")
        self.assertIn(log[-1]["hash"], Path(self.job.p("审核报告.md")).read_text("utf-8"))

    def test_chain_checks_modified_middle_and_truncated_tail(self):
        self.relay_and_draft()
        path = Path(self.job.p("decisions.jsonl"))
        for quote in ("再次核实", "第三次核实"):
            self.invoke("answer", self.job.dir, "Q1=2", "--user-quote", quote)
        lines = path.read_text("utf-8").splitlines(True)
        for invalid in (lines[:1]+lines[2:], lines[:-1], [lines[0], lines[1].replace("再次核实","虚假"), lines[2]]):
            path.write_text("".join(invalid), encoding="utf-8")
            self.assertIn("决定记录", self.invoke("final", self.job.dir, "--template-comments", "drop", rc=2))

    def test_first_new_receipt_anchors_entire_legacy_prefix(self):
        path = Path(self.job.p("decisions.jsonl"))
        legacy = '{"action":"draft --defaults","user_quote":"legacy"}'
        path.write_text(legacy, encoding="utf-8")
        self.relay_and_draft()
        self.invoke("final", self.job.dir, "--template-comments", "drop")
        records = path.read_text("utf-8")
        self.assertTrue(records.startswith(legacy + "\n"))
        path.write_text(records.replace("legacy", "edited"), encoding="utf-8")
        self.assertIn("决定记录", self.invoke("final", self.job.dir, "--template-comments", "drop", rc=2))

    def test_interrupted_append_is_recovered_exactly_once(self):
        from dilib import decisions
        path = Path(self.job.p("decisions.jsonl"))
        self.relay_and_draft()
        def interrupt(dest, data):
            with dest.open("ab") as stream:
                stream.write(data[:13])
            raise OSError("interrupted")
        with patch.object(decisions, "_append_bytes", interrupt), self.assertRaises(OSError):
            decisions.record_decision(self.job, "draft --defaults", "允许默认", draft=self.draft.name)
        for _ in range(2):
            events, head = decisions.ledger(path)
            self.assertEqual([event["action"] for event in events], ["answer", "draft --defaults"])
            self.assertEqual(head, events[-1]["hash"])

    def test_final_requires_matching_receipt_even_with_override(self):
        self.modern_draft()
        q = {"id": "Q1", "key": "src:verify:S1", "topic": "来源核对", "question": "来源适用？",
             "options": [{"label": "确认", "effect": {"ok": True}}], "recommended": 0}
        self.job.write("questions.json", [dict(q, input_fp=self.job.fingerprint())])
        self.invoke("answer", self.job.dir, "Q1=1", "--user-quote", "本批适用")
        path = Path(self.job.p("decisions.jsonl"))
        original = path.read_text("utf-8")
        edited = json.loads(original)
        edited["answers"][0]["binding"] = "0" * 64
        args = ("final", self.job.dir, "--reviewer-comments", "drop", "--accept-unverified",
                "--user-quote", "仅接受列出的遗留项", "--json", "--strict-exit")
        for changed in ("", "{", json.dumps(edited), original.replace("本批适用", "其他批次"),
                        original.replace("src:verify:S1", "src:verify:S2")):
            with self.subTest(receipt=changed):
                path.write_text(changed, encoding="utf-8")
                self.assertIn("决定记录", self.invoke(*args, rc=4))
                self.assertFalse(Path(self.job.p("终稿", "test_终稿.docx")).exists())
        path.write_text(original, encoding="utf-8")
        self.invoke(*args)

    def test_draft_exception_needs_its_original_receipt(self):
        self.invoke("draft", self.job.dir, "--defaults", "--user-quote", "先按推荐生成")
        Path(self.job.p("decisions.jsonl")).write_text("", encoding="utf-8")
        self.assertIn("决定记录", self.invoke("final", self.job.dir, "--accept-unverified",
                      "--user-quote", "接受列出的遗留项", "--json", "--strict-exit", rc=4))

    def test_reused_confirmation_requires_original_remember_receipt(self):
        from dilib.decisions import verify_receipts
        from dilib.common import ReviewBlocked
        key, original = "src:spec:S2", "src:spec:S1"
        answer = {"label": "适用", "effect": {"ok": True}, "fp": "source-fingerprint",
                  "user_quote": "此规范适用", "remember": True}
        origin = self.root / "origin"
        origin.mkdir()
        facts = self.root / "facts"
        facts.mkdir()
        shared = "a" * 64
        (facts / (shared + ".json")).write_text(json.dumps(
            {"question": original, "origin_job": str(origin), "answer": answer}), encoding="utf-8")
        self.job.write("facts.json", {"facts": [{"scope": {"question": key, "shared": shared}}]})
        # A 1.1.x CLI receipt did not store the effect digest.
        receipt = {"action": "answer --remember", "user_quote": answer["user_quote"], "answers": [
            {"key": original, "answer": answer["label"], "fp": answer["fp"]}]}
        ledger = origin / "decisions.jsonl"
        ledger.write_text(json.dumps(receipt), encoding="utf-8")
        with patch("dilib.decisions.project_dir", return_value=self.root):
            verify_receipts(self.job, self.draft.name, "other-job-inputs")
            ledger.write_text(json.dumps(dict(receipt, action="answer")), encoding="utf-8")
            with self.assertRaises(ReviewBlocked):
                verify_receipts(self.job, self.draft.name, "other-job-inputs")

    def test_batch_applies_multiple_answers_with_one_replan_per_job(self):
        questions = [{'key': 'selection' + str(i), 'id': 'Q' + str(i), 'topic': '选择', 'question': '位置' + str(i),
                      'options': [{'label': '保留', 'effect': {'keep': True}}],
                      'input_fp': self.job.fingerprint()} for i in (1, 2)]
        self.job.write('questions.json', questions)
        items = [dict(q, id='B' + str(i), jobs=[[self.job.dir, q['id']]], options=['保留'],
                      option_details=q['options'], input_fps={self.job.dir: q['input_fp']})
                 for i, q in enumerate(questions, 1)]
        path = self.root / 'batch.json'
        path.write_text(json.dumps({'jobs': [self.job.dir], 'questions': items}), encoding='utf8')
        self.invoke('batch', 'answer', path, 'B1=1', 'B2=999', rc=2)
        self.assertFalse(self.job.read('answers.json'))
        with patch.object(cli, 'do_plan', wraps=cli.do_plan) as replan:
            self.invoke('batch', 'answer', path, 'B1=1', 'B2=1')
            self.assertEqual(replan.call_count, 1)
        self.assertEqual(set(self.job.read('answers.json')), {'selection1', 'selection2'})

    def test_source_replaced_after_question_and_after_draft(self):
        q = {"id": "Q1", "topic": "来源核对", "key": "src:verify:S1", "question": "核对来源",
             "options": [{"label": "已核对", "effect": {"ok": True}}], "recommended": 0,
             "input_fp": self.job.fingerprint()}
        self.job.write("questions.json", [q])
        make_docx(self.root / 'source.docx', {'word/document.xml': doc_xml(BODY.replace('hello', '第二批'))})
        message = self.invoke('answer', self.job.dir, 'Q1=1', '--user-quote', '已核对第一批', rc=2)
        self.assertIn('重新', message)
        self.assertFalse(self.job.read('answers.json'))
        self.invoke('draft', self.job.dir)
        make_docx(self.root / 'source.docx', {'word/document.xml': doc_xml(BODY.replace('hello', '第三批'))})
        message = self.invoke('final', self.job.dir, rc=2)
        self.assertIn('重新', message)
        self.assertFalse(list((self.root / 'job/终稿').glob('*.docx')))

    def test_empty_adjacent_sections_only_offer_available_content(self):
        heading = '<w:pPr><w:outlineLvl w:val="0"/></w:pPr>'
        target = self.root / '验收模板.docx'
        anchor = ('<w:p>' + heading + '<w:commentRangeStart w:id="0"/><w:r><w:t>一、质量说明</w:t></w:r>'
                  '<w:commentRangeEnd w:id="0"/><w:r><w:commentReference w:id="0"/></w:r></w:p><w:p/>')
        make_docx(target, {'word/document.xml': doc_xml(anchor),
                           'word/comments.xml': COMMENTS.replace('note', '查看研制任务书可靠性章节')})
        source = self.root / '研制任务书.docx'
        titles = ['1 可靠性', '2 抽检要求', '3 序列号']
        headings = ['<w:p>' + heading + '<w:r><w:t>' + title + '</w:t></w:r></w:p>' for title in titles]
        for name, evidence in [('empty', ''), ('present', '<w:p><w:r><w:t>逐台检查密封圈。</w:t></w:r></w:p>')]:
            make_docx(source, {'word/document.xml': doc_xml(headings[0] + headings[1] + evidence + headings[2])})
            job = Job.create(str(target), [str(source)], out_root=str(self.root), name=name)
            plan = cli.do_plan(job, quiet=True)
            self.assertTrue(any(op['op'] == 'insert_section' for op in plan['ops']))
            questions = [q for q in plan['questions'] if q['topic'] == '章节取舍']
            if evidence:
                self.assertEqual(len(questions), 1)
                self.assertIn('抽检要求', questions[0]['options'][0]['label'])
            else:
                self.assertEqual(questions, [])

    def test_product_and_basis_cite_selected_document_among_multiple_sources(self):
        paths = [self.root / s for s in ('说明.docx', 'AB1234详细规范.docx', 'AB1234X详细规范.docx')]
        for path in paths:
            make_docx(path)
        job = Job.create(self.job.data['target']['path'], list(map(str, paths)), out_root=str(self.root), name='multi')
        planner = Planner(job)
        for field, method in [('产品型号', planner.field_product), ('依据文件', planner.field_basis)]:
            method(field, 'T0.R1.C1', planner.doc_sources())
            q = next(x for x in planner.questions if x['key'] == 'field:' + field)
            selected = next(o for o in q['options'] if 'AB1234X' in o['label'])
            self.assertFalse(planner.ops)
            planner.answers[q['key']] = {'effect': selected['effect']}
            method(field, 'T0.R1.C1', planner.doc_sources())
            self.assertIn('AB1234X', planner.ops[0]['text'])
            self.assertEqual(planner.ops[-1]['links'][0]['alias'], 'S3')
            planner.ops.clear()


    def test_flags_and_batch_require_quotes_before_writing(self):
        make_docx(self.draft)
        manifest = self.root / "batch.json"
        manifest.write_text(json.dumps({"jobs": [self.job.dir]}), "utf-8")
        for args in [("draft", self.job.dir, "--defaults"), ("draft", self.job.dir, "--discard-edits"),
                     ("final", self.job.dir, "--accept-unverified"), ("answer", self.job.dir, "Q1=1", "--remember"),
                     ("batch", "draft", manifest, "--defaults")]:
            self.assertIn("--user-quote", self.invoke(*args, rc=2))
        self.assertEqual(len(self.job.drafts()), 1)
        self.assertFalse(Path(self.job.p("decisions.jsonl")).exists())
        self.invoke("batch", "draft", manifest, "--defaults", "--user-quote", "本次全部按推荐出草稿")
        record = json.loads(Path(self.job.p("decisions.jsonl")).read_text("utf-8"))
        self.assertEqual(record["user_quote"], "本次全部按推荐出草稿")
        self.assertIn("--defaults", str(record))

    def test_renamed_review_copy_cannot_bypass_registered_checks(self):
        make_docx(self.draft)
        other = self.root / "另存审核稿.docx"
        other.write_bytes(self.draft.read_bytes())
        pending = {"kind": "source", "key": "src:verify:S1", "detail": "待核来源 S1"}
        self.job.data["unverified"] = {self.draft.name: [pending]}
        self.job.save()
        args = ["final", self.job.dir, "--draft", other, "--reviewer-comments", "keep", "--template-comments", "keep"]
        self.assertIn("待核来源 S1", self.invoke(*args, rc=2))
        self.assertFalse(Path(self.job.p("终稿", "test_终稿.docx")).exists())
        self.job.data["unverified"]["test_草稿_v2.docx"] = []
        self.job.save()
        self.assertIn("版本", self.invoke(*args, rc=2))

    def test_template_choice_is_requested_only_when_present(self):
        make_docx(self.draft)
        self.assertIn("--template-comments", self.invoke("final", self.job.dir, rc=2))
        self.invoke("final", self.job.dir, "--template-comments", "keep")
        saved = Package(self.job.p("终稿", "test_终稿.docx"))
        self.assertEqual(len(saved.xml("word/comments.xml")), 1)
        manifest = self.root / "batch.json"
        manifest.write_text(json.dumps({"jobs": [self.job.dir]}), "utf-8")
        self.invoke("batch", "final", manifest, "--template-comments", "drop")
        saved = Package(self.job.p("终稿", "test_终稿.docx"))
        self.assertEqual(len(saved.xml("word/comments.xml")), 0)

    def test_redraft_preserves_entire_review_when_generated_plan_matches(self):
        self.invoke("draft", self.job.dir)
        self.modern_draft()
        original = Package(self.draft)
        self.attach_image(original)
        original.save(self.draft)
        reviewed = self.draft.read_bytes()
        self.invoke("draft", self.job.dir)
        newest = Path(self.job.drafts()[-1])
        self.assertEqual(self.draft.read_bytes(), reviewed)
        output = self.invoke("review", self.job.dir)
        self.assertIn("回复 #1", output)
        self.assertIn("已解决", output)
        current = Package(newest)
        self.assertEqual(len(current.xml("word/commentsExtensible.xml")), 3)
        self.assertEqual(current.parts["word/media/test.png"], original.parts["word/media/test.png"])
        self.assertEqual(list(current.rels("word/comments.xml").items()), list(original.rels("word/comments.xml").items()))
        self.invoke("final", self.job.dir, "--reviewer-comments", "keep")

    def test_unmappable_thread_remains_blocked_across_drafts(self):
        self.invoke("draft", self.job.dir)
        self.modern_draft()
        reviewed = self.draft.read_bytes()
        make_docx(self.job.data["target"]["path"], {"word/document.xml": doc_xml(BODY.replace("hello", "新版要求"))})
        output = self.invoke("draft", self.job.dir)
        self.assertIn("comment 1", output)
        self.invoke("draft", self.job.dir)
        output = self.invoke("final", self.job.dir, "--template-comments", "keep", "--reviewer-comments", "keep", rc=2)
        self.assertIn("原审核稿", output)
        self.assertEqual(self.draft.read_bytes(), reviewed)

    def test_redraft_maps_intact_anchors_across_an_unrelated_body_change(self):
        self.invoke("draft", self.job.dir)
        pristine = Package(self.draft)
        self.modern_draft()
        reviewed = Package(self.draft)
        reviewed.parts["word/document.xml"] = pristine.parts["word/document.xml"]
        reviewed.save(self.draft)
        # Append a paragraph outside the reviewed range.
        new_text = '<w:p><w:r><w:t>新增独立段落</w:t></w:r></w:p>'
        make_docx(self.job.data["target"]["path"], {"word/document.xml": doc_xml(BODY + new_text)})
        self.invoke("draft", self.job.dir)
        output = self.invoke("review", self.job.dir)
        self.assertIn("回复 #1", output)
        self.assertIn("已解决", output)
        newest = Package(self.job.drafts()[-1])
        self.assertIn("新增独立段落", "".join(newest.xml("word/document.xml").itertext()))
