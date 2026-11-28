import contextlib
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from test_selfcheck import COMMENTS, doc_xml, make_docx
from dilib import cli


class RunDraftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / "交接模板.docx"
        self.source = self.root / "研制任务书.docx"
        self.heading = '<w:pPr><w:pStyle w:val="Heading1"/><w:outlineLvl w:val="0"/></w:pPr>'
        body = ('<w:p>' + self.heading + '<w:commentRangeStart w:id="0"/>'
                '<w:r><w:t>一、使用缘由</w:t></w:r><w:commentRangeEnd w:id="0"/>'
                '<w:r><w:commentReference w:id="0"/></w:r></w:p><w:p/>')
        make_docx(self.target, {"word/document.xml": doc_xml(body),
                               "word/comments.xml": COMMENTS.replace("note", "研制任务书中的项目背景")})

    def source_section(self, title):
        body = ('<w:p>' + self.heading + '<w:r><w:t>1 ' + title + '</w:t></w:r></w:p>'
                '<w:p><w:r><w:t>标识板用于区分两条路线。</w:t></w:r></w:p>')
        make_docx(self.source, {"word/document.xml": doc_xml(body)})

    def invoke(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(cli.main(list(map(str, args))), 0)
        return output.getvalue()

    def run_job(self, name, *options):
        return self.invoke("run", "-t", self.target, "-s", self.source, "--task",
                           "按批注摘录", "--out", self.root, "--name", name, *options)

    def test_one_plan_and_the_same_verified_draft_as_separate_commands(self):
        self.source_section("项目背景")
        self.run_job("分步")
        self.assertFalse((self.root / "分步/草稿").exists())
        self.invoke("draft", self.root / "分步")
        with patch.object(cli, "do_plan", wraps=cli.do_plan) as planner:
            output = self.run_job("合并", "--draft")
        self.assertEqual(planner.call_count, 1)
        self.assertIn("审核报告", output)
        self.assertIn("S1:H0", output)
        self.assertIn("未填 0", output)
        self.assertTrue((self.root / "合并/审核报告.md").is_file())
        def document(name):
            path, = (self.root / name / "草稿").glob("*.docx")
            with zipfile.ZipFile(path) as archive:
                self.assertIsNone(archive.testzip())
                return archive.read("word/document.xml")
        self.assertIn("标识板用于区分两条路线。".encode(), document("合并"))
        self.assertEqual(document("合并"), document("分步"))

    def test_unresolved_source_stops_with_choices_and_no_draft(self):
        self.source_section("运输包装")
        output = self.run_job("缺依据", "--draft")
        self.assertIn("Q1", output)
        self.assertFalse((self.root / "缺依据/草稿").exists())
        self.assertFalse((self.root / "缺依据/answers.json").exists())

    def test_json_run_returns_actionable_questions_and_separate_timing(self):
        self.source_section("运输包装")
        output, diagnostics = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(diagnostics), patch.dict(os.environ, {"DI_TIMING": "1"}):
            rc = cli.main(["run", "-t", str(self.target), "-s", str(self.source),
                           "--out", str(self.root), "--name", "交互", "--draft", "--json"])
        self.assertEqual(rc, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "needs_answers")
        self.assertEqual(result["next_action"], "ask_user")
        self.assertTrue(result["questions"][0]["options"])
        self.assertLessEqual(len(result["questions"]), 4)
        self.assertEqual(result["all_questions"], result["questions"])
        self.assertEqual(Path(result["job"]), self.root / "交互")
        self.assertIn("plan", diagnostics.getvalue())

    def test_long_form_exposes_every_question_and_option_with_conditional_groups(self):
        from types import SimpleNamespace
        questions = [{"id": "Q%d" % n, "key": "k%d" % n, "topic": "符号", "question": "选择对应",
                      "options": [{"label": str(i)} for i in range(7)]} for n in range(1, 7)]
        questions[4]["conditional_on"] = "k1"
        result = cli.plan_result(SimpleNamespace(dir="job", p=lambda x: x), {"questions": questions})
        self.assertEqual([len(result[name]) for name in ("questions", "all_questions")], [4, 6])
        self.assertEqual(result["question_groups"], {"Q1": ["Q5"]})
        self.assertEqual(len(result["questions"][0]["options"]), 4)
        self.assertEqual(len(result["all_questions"][0]["options"]), 7)

    def test_json_strict_exit_and_blocked_actions(self):
        self.source_section("运输包装")
        for name, strict, expected in (("普通", [], 0), ("严格", ["--strict-exit"], 3)):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                code = cli.main(["run", "-t", str(self.target), "-s", str(self.source),
                                 "--out", str(self.root), "--name", name, "--draft", "--json", *strict])
            self.assertEqual(code, expected)
            self.assertEqual(json.loads(output.getvalue())["next_action"], "ask_user")
        from dilib.common import ReviewBlocked
        for strict, expected in (([], 0), (["--strict-exit"], 4)):
            with patch.object(cli, "cmd_final", side_effect=ReviewBlocked("待审核")), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(cli.main(["final", str(self.root / "普通"), "--json", *strict]), expected)
            self.assertEqual(json.loads(output.getvalue())["next_action"], "resolve_blockers")

    def test_batch_run_returns_scoped_paths_and_reuses_completed_drafts_on_retry(self):
        import shutil
        self.source_section("项目背景")
        rows = []
        for name in ("甲 项目", "乙 项目"):
            folder = self.root / name
            folder.mkdir()
            for source in (self.target, self.source):
                shutil.copy2(source, folder / source.name)
            rows.append({"target": str(Path(name) / self.target.name), "sources": [str(Path(name) / self.source.name)],
                         "task": "按批注摘录"})
        listing = self.root / "来件清单.json"
        listing.write_text(json.dumps(rows), encoding="utf-8")
        args = ("batch", "run", listing, "--out", self.root / "整合输出", "--draft", "--json")
        result = json.loads(self.invoke(*args))
        self.assertEqual(result["status"], "draft_ready")
        self.assertEqual([Path(j["template"]).parent.name for j in result["jobs"]], ["甲 项目", "乙 项目"])
        drafts = {j["draft"] for j in result["jobs"]}
        self.assertEqual(len(drafts), 2)
        self.assertTrue(all(Path(path).is_file() for path in drafts))
        for call in (args, ("batch", "answer", result["batch"], "--draft", "--json")):
            retry = json.loads(self.invoke(*call))
            self.assertEqual({j["draft"] for j in retry["jobs"]}, drafts)
        self.assertEqual(len(list((self.root / "整合输出").glob("*/草稿/*.docx"))), 2)
        changed = result["jobs"][0]
        for field in ("draft", "report"):
            path = Path(changed[field])
            original = path.read_bytes()
            path.write_bytes(original + "审核人修改".encode())
            blocked = json.loads(self.invoke(*args))
            self.assertEqual(blocked["status"], "blocked")
            self.assertEqual(blocked["jobs"][0]["next_action"], "review_existing_draft")
            self.assertEqual(path.read_bytes(), original + "审核人修改".encode())
            path.write_bytes(original)

    def test_parallel_batch_uses_one_restricted_pool_and_keeps_ordered_statuses(self):
        from dilib import child_process
        self.source_section("项目背景")
        job_paths = []
        for name in ("第一项", "带 空格", "第三项", "第四项"):
            self.run_job(name)
            job_paths.append(str(self.root / name))
        manifest = self.root / "batch.json"
        manifest.write_text(json.dumps({"jobs": job_paths}), "utf8")
        with patch.object(child_process, "run_child", wraps=child_process.run_child) as launch:
            output = self.invoke("batch", "draft", manifest, "--jobs", 2)
        self.assertEqual(launch.call_count, 1)
        self.assertTrue(launch.call_args.kwargs["worker"])
        result_lines = [line.split(' {', 1) for line in output.splitlines()]
        self.assertEqual([name for name, _ in result_lines], [Path(p).name for p in job_paths])
        statuses = [json.loads("{" + result) for _, result in result_lines]
        self.assertTrue(all(s["status"] == "draft_ready" and Path(s["draft"]).is_file() for s in statuses))
        manifest.write_text(json.dumps({"jobs": [job_paths[0], job_paths[0]]}), "utf8")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["batch", "draft", str(manifest), "--jobs", "2"]), 2)

    def test_pool_reuses_processes_and_times_out_each_job_independently(self):
        from multiprocessing import get_context
        from batch_pool import pool_results
        self.source_section("运输包装")
        for name in ("A", "B", "C"):
            self.run_job(name)
        paths = [str(self.root / name) for name in ("A", "B", "C")]
        ctx = get_context("spawn")
        with patch.object(ctx, "Process", wraps=ctx.Process) as processes:
            results = pool_results(paths, workers=2)
        self.assertEqual(processes.call_count, 2)
        self.assertEqual([rc for rc, _ in results], [3, 3, 3])
        self.assertEqual([json.loads(s)["job"] for _, s in results], paths)
        failed = pool_results(paths, workers=2, timeout=0)
        self.assertEqual([rc for rc, _ in failed], [2, 2, 2])
        self.assertTrue(all("超过" in msg for _, msg in failed))

    def test_answer_and_draft_share_the_new_plan(self):
        self.source_section("运输包装")
        self.run_job("待答", "--draft")
        job = self.root / "待答"
        q = json.loads((job / "questions.json").read_text(encoding="utf-8"))[0]
        with patch.object(cli, "do_plan", wraps=cli.do_plan) as planner, contextlib.redirect_stdout(io.StringIO()) as output:
            rc = cli.main(["answer", str(job), q["id"] + "=1", "--draft", "--json"])
        self.assertEqual(planner.call_count, 1)
        result = json.loads(output.getvalue())
        self.assertEqual(rc, 0)
        if result["status"] == "draft_ready":
            self.assertTrue(Path(result["draft"]).is_file())
        elif result["status"] == "needs_answers":
            self.assertTrue(result["questions"])
        else:
            self.assertEqual(result["status"], "blocked")

    def test_from_designated_document_is_not_a_requested_heading(self):
        self.source_section("项目背景")
        body = '<w:p>' + self.heading + '<w:commentRangeStart w:id="0"/><w:r><w:t>说明</w:t></w:r><w:commentRangeEnd w:id="0"/></w:p><w:p/>'
        make_docx(self.target, {"word/document.xml": doc_xml(body),
                               "word/comments.xml": COMMENTS.replace("note", "请从指定任务书摘入项目背景。")})
        self.run_job("自然批注", "--draft")
        plan = json.loads((self.root / "自然批注/plan.json").read_text(encoding="utf-8"))
        self.assertFalse(plan["questions"])
        self.assertTrue(list((self.root / "自然批注/草稿").glob("*.docx")))

    def test_template_without_an_actionable_mapping_is_not_delivered_as_filled(self):
        self.source_section("项目背景")
        make_docx(self.target, {"word/document.xml": doc_xml('<w:p><w:r><w:t>待填写</w:t></w:r></w:p>')})
        output = self.run_job("待定位", "--draft")
        self.assertIn("请指定", output)
        self.assertFalse((self.root / "待定位/草稿").exists())
        self.assertTrue((self.root / "待定位/plan.json").is_file())
