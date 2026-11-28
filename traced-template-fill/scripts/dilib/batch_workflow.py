"""Stateful batch planning, scoped answers, and delivery of the actual draft paths."""
import argparse
import csv
import datetime
import hashlib
import json
import os
from pathlib import Path

from . import evidence
from .common import DIError, out, read_json, write_json
from .decisions import protected_question, require_quote
from .draft_review import pending
from .input_paths import split_paths
from .job import Job
from .preferences import locked_preferences
from .workflow import ask_form, plan_result


def read_rows(path):
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream)) if path.suffix.casefold() == ".csv" else json.load(stream)
    except (ValueError, csv.Error) as exc:
        raise DIError("无法读取批量清单：" + str(exc)) from exc
    if not isinstance(rows, list) or not rows or not all(isinstance(row, dict) for row in rows):
        raise DIError("来件清单应包含至少一行目标和源文件。继续已有批次请使用返回的批量任务文件。")
    return rows


def create_jobs(a, rows, path, state):
    base = Path(a.manifest).resolve().parent if a.action == "run" else Path.cwd()
    for i, row in enumerate(rows[len(state["jobs"]):], len(state["jobs"]) + 1):
        target = row.get("目标") or row.get("target")
        sources = row.get("源文件") or row.get("sources")
        if isinstance(sources, str):
            sources = split_paths(sources)
        if not isinstance(target, str) or not isinstance(sources, list) or not all(isinstance(s, str) for s in sources):
            raise DIError("清单第 %d 行需要一个目标路径和一个来源列表。" % i)
        rules = row.get("测试规则") or row.get("test_rules") or a.test_rules
        formats = row.get("格式") or row.get("formats") or ""
        job = Job.create(str(base / target), [str(base / s) for s in sources], out_root=str(path.parent),
                         task=row.get("任务") or row.get("task") or a.task or "",
                         profile=row.get("排版") or row.get("profile"), test_rules=str(base / rules) if rules else None,
                         formats=formats if isinstance(formats, list) else list(filter(None, formats.split(","))),
                         handoff_batch=path if state.get("handoff") else None)
        state["jobs"].append(job.dir)
        attached = row.get("证据") or row.get("evidence")
        if attached:
            state.setdefault("evidence", {})[job.dir] = str((base / attached).resolve())
        write_json(path, state)
        out("建立：" + job.dir)


def find_questions(pairs, state):
    merged = {}
    ids = state.setdefault("question_ids", {})
    # Reserve identifiers issued by previous versions on the first replan.
    last_id = max([int(it["id"][1:]) for it in state.get("questions", [])] + list(ids.values()) + [0])
    for job, qs in pairs:
        for q in pending(qs):
            signature = evidence.digest([q["key"], q["question"], q["options"], bool(q.get("multi")),
                                         q.get("recommended", 0), q["input_fp"] if protected_question(q["key"]) else None])
            if signature not in ids:
                last_id += 1
                ids[signature] = last_id
            item = merged.setdefault(signature, {"id": "B%d" % ids[signature], "key": q["key"], "topic": q["topic"],
                "question": q["question"], "multi": bool(q.get("multi")), "recommended": q.get("recommended", 0),
                "options": [o["label"] for o in q["options"]], "option_details": q["options"],
                "input_fps": {}, "rule_shas": {}, "jobs": []})
            item["jobs"].append([job.dir, q["id"]])
            item["input_fps"][job.dir] = q["input_fp"]
            item["rule_shas"][job.dir] = q.get("rule_sha256")
    return list(merged.values())


def delivery_stamp(job):
    return [job.fingerprint(files_only=True), evidence.decision_digest(job)]


def file_sha(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def output_digests(result):
    return {k: file_sha(result[k]) for k in ("draft", "report") if os.path.isfile(result[k])}


def changed_deliveries(jobs, state):
    changed = {}
    for job in jobs:
        saved = state.get("drafts", {}).get(job.dir)
        if not saved:
            continue
        result = saved["result"]
        if (output_digests(result) != saved.get("sha256") or not os.path.isdir(result["snapshots"])
                or job.drafts()[-1:] != [result["draft"]]):
            changed[job.dir] = dict(status="blocked", job=job.dir, next_action="review_existing_draft",
                                    reason="已交付草稿、审核报告或版本发生变化，请按单项 review 核对现有文件。",
                                    draft=result["draft"], report=result["report"])
    return changed


def advance(jobs, state, path, draft, blocked, replanned=(), *, _authority=None):
    from . import cli
    pairs, results = [], []
    cache = state.setdefault("drafts", {})
    for i, job in enumerate(jobs, 1):
        saved = cache.get(job.dir, {})
        cached = saved.get("result", {})
        if job.dir in blocked:
            result = blocked[job.dir]
        elif cached.get("status") == "draft_ready" and saved["stamp"] == delivery_stamp(job):
            result = cached
        else:
            plan = job.read("plan.json") if job.dir in replanned else cli.do_plan(job, quiet=True)
            pairs.append((job, plan["questions"]))
            if draft and not pending(plan["questions"]):
                result = cli.generate_draft(job, plan=plan, stop_if_unmapped=True, _authority=_authority)
                if result["status"] == "draft_ready":
                    cache[job.dir] = {"result": result, "stamp": delivery_stamp(job), "sha256": output_digests(result)}
                    write_json(path, state)
            else:
                result = plan_result(job, plan)
        results.append({k: v for k, v in result.items() if k not in ("questions", "all_questions", "question_groups")})
        results[-1].update(id="J%d" % i, template=job.data["target"]["path"],
                           sources=[s["path"] for s in job.data["sources"]])
    items = find_questions(pairs, state)
    state["questions"] = items
    write_json(path, state)
    for item in items:
        out("%s [%s] %s（涉及%d个任务）" % (item["id"], item["topic"], item["question"], len(item["jobs"])))
        for i, label in enumerate(item["options"], 1):
            out("  %d) %s" % (i, label))
    ready = sum(r["status"] == "draft_ready" for r in results)
    statuses = {r["status"] for r in results}
    if items:
        status, action = "needs_answers", "ask_user"
    elif "blocked" in statuses:
        status, action = "blocked", "resolve_blockers"
    elif results and ready == len(results):
        status, action = "draft_ready", "deliver_drafts"
    else:
        status, action = "planned", "batch_draft"
    questions = [dict(it, options=it["option_details"]) for it in items]
    forms = ask_form(questions, full=True)
    for form, item in zip(forms, items):
        form["id"] = item["id"]
        form["jobs"] = [jd for jd, _ in item["jobs"]]
    summary = {k: sum(r.get(k, 0) for r in results) for k in ("fills", "held_measurements", "unfilled", "unread", "unverified")}
    summary.update(total=len(results), drafts=ready, blocked=sum(r["status"] == "blocked" for r in results))
    response = {"status": status, "next_action": action, "batch": str(path), "jobs": results, "pending": len(items),
                "questions": forms[:4], "all_questions": forms, "summary": summary}
    if status == "draft_ready":
        from .batch_delivery import write_delivery
        delivery = write_delivery(path, jobs, results)
        summary.update(measurement_fills=delivery["measurement_fills"], measurement_holds=delivery["measurement_holds"])
        response.update(delivery_report=delivery["delivery_report"], message=delivery["message"])
        out(delivery["message"])
    return response


def attachments(a, jobs):
    selected = {}
    if a.evidence:
        src = Path(a.evidence).resolve()
        if src.suffix.casefold() == ".json":
            mapping = read_json(src)
            by_id = {"J%d" % i: job.dir for i, job in enumerate(jobs, 1)}
            if not isinstance(mapping, dict) or not mapping or not set(mapping) <= set(by_id):
                raise DIError("批量证据目录须为 JSON 对象，使用输出中的任务编号 J1、J2 等作为键。")
            if not all(isinstance(v, str) and v for v in mapping.values()):
                raise DIError("批量证据目录的值须为文件路径。")
            selected.update({by_id[k]: str((src.parent / v).resolve()) for k, v in mapping.items()})
        else:
            selected.update({job.dir: str(src) for job in jobs})
    return selected


def submit(a, path, state, jobs, blocked, provided):
    from . import cli
    files = {**state.get("evidence", {}), **{jd: rec["file"] for jd, rec in state.get("registered_evidence", {}).items()},
             **provided}
    files = {jd: src for jd, src in files.items() if jd not in blocked}
    if any(not Path(src).is_file() for src in files.values()):
        raise DIError("清单中的项目证据文件缺失，请检查独立项目的文件路径。")
    hashes = {jd: file_sha(p) for jd, p in files.items()}
    token = evidence.digest([a.items, a.user_quote, hashes])
    receipt = state.get("submission", {})
    same = receipt.get("token") == token
    items = {it["id"]: it for it in (receipt["questions"] if same else state.get("questions", []))}
    grouped = {}
    seen = set()
    for kv in a.items:
        bid, sep, val = kv.partition("=")
        bid = bid.strip().upper()
        it = items.get(bid)
        if bid in seen or not it or (not sep and not files):
            raise DIError("未知、重复的批量问题或答案格式有误：" + kv)
        seen.add(bid)
        if protected_question(it["key"]):
            require_quote(a.user_quote, "%s %s" % (bid, it["topic"]))
        for jd, _ in it["jobs"]:
            grouped.setdefault(jd, []).append((it, val if sep else None))
    commands = []
    processed = set()
    for job in jobs:
        jd = job.dir
        if jd in blocked:
            continue
        selected, doc = grouped.get(jd, []), files.get(jd)
        fp, rules = job.fingerprint(files_only=True), evidence.rule_pack(job)["sha256"]
        current = [fp, rules, evidence.digest(job.read("answers.json", {}))]
        if same and jd in receipt["completed"]:
            if receipt["completed"][jd] != current:
                raise DIError("上次提交后任务输入或决定已变化，请重新 batch plan。")
            continue
        old = job.read("answers.json", {}).get("_program_manifest") or {}
        if not selected and (not doc or (old.get("sha256") == hashes[jd] and old.get("fp") == fp
                                        and old.get("rule_sha256") == rules)):
            continue
        qs = job.read("questions.json", [])
        by_key = {q["key"]: q for q in qs}
        choices = []
        for it, val in selected:
            q = by_key.get(it["key"])
            if (not q or q.get("input_fp") != fp or it.get("input_fps", {}).get(jd) != fp
                    or q.get("rule_sha256", rules) != rules or it.get("rule_shas", {}).get(jd, rules) != rules
                    or q["question"] != it["question"] or [o["label"] for o in q["options"]] != it["options"]
                    or it.get("option_details") != q["options"]):
                raise DIError("批量问题的来源或选项已变化。请重新运行 batch plan 并核对选项：" + jd)
            if val is None:
                if not doc:
                    raise DIError("任务缺少这项回答所需的测试程序清单：" + jd)
                choices.append(q["id"])
            else:
                cli._choice_indices(q, val.strip().strip('"').strip("“”"))
                choices.append(q["id"] + "=" + val)
        if doc:
            evidence.program_answers(qs, choices, doc, job=job)
        commands.append((job, argparse.Namespace(job=jd, items=choices, remember=False, user_quote=a.user_quote, evidence=doc)))
    if not same:
        receipt = {"token": token, "questions": list(items.values()), "completed": {}}
        state["submission"] = receipt
        write_json(path, state)
    reasons = {}
    for job, command in commands:
        result = cli.cmd_answer(command)
        jd = job.dir
        if result and result.get("reason"):
            reasons[jd] = result["reason"]
            continue
        processed.add(jd)
        if command.evidence:
            state.setdefault("registered_evidence", {})[jd] = {"file": command.evidence, "sha256": hashes[jd]}
        receipt["completed"][jd] = [job.fingerprint(files_only=True), evidence.rule_pack(job)["sha256"],
                                     evidence.digest(job.read("answers.json", {}))]
        write_json(path, state)
    return processed, reasons


def run(a):
    source = Path(a.manifest).resolve()
    if a.action in ("new", "run"):
        rows = read_rows(source)
        folder = Path(a.out or "整合输出").resolve()
        token = evidence.digest([str(source), rows, a.task, a.test_rules])[:20] if a.action == "run" else datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path = folder / ("批量_%s.json" % token)
    else:
        path = source
        if not path.is_file():
            raise DIError("找不到批量任务文件：" + str(path))
    with locked_preferences(path.parent, path.name + ".lock"):
        try:
            state = read_json(path, {"jobs": []})
        except ValueError as exc:
            raise DIError("无法解析批量任务文件：" + str(path)) from exc
        if not isinstance(state, dict) or not isinstance(state.get("jobs"), list):
            raise DIError("请使用 batch run 或 batch new 建立的批量任务文件。")
        if getattr(a, "handoff", False) and not state.get("handoff"):
            from .handoff import enable
            enable(path, state)
        if a.action in ("new", "run"):
            create_jobs(a, rows, path, state)
            out("批量任务文件：" + str(path))
        jobs = [Job(d) for d in state["jobs"]]
        if a.action == "new":
            return {"status": "created", "batch": str(path), "jobs": state["jobs"], "next_action": "batch_plan"}
        blocked = changed_deliveries(jobs, state)
        if state.get("handoff") and state.get("local_delivery"):
            # An interrupted approval can be superseded explicitly before the first document is written.
            if a.action == "plan" and not state.get("drafts") and not any(Path(j.p("草稿")).exists() for j in jobs):
                for key in ("active_handoff", "local_delivery", "local_delivery_sha256"):
                    state.pop(key, None)
                write_json(path, state)
            else:
                from .handoff import resume_message
                return resume_message(state, path)
        provided = attachments(a, jobs) if a.action == "answer" and a.evidence else {}
        for jd, attachment in state.get("registered_evidence", {}).items():
            src = Path(attachment["file"])
            if jd not in provided and jd not in blocked and (not src.is_file() or file_sha(src) != attachment["sha256"]):
                blocked[jd] = dict(status="blocked", job=jd, next_action="review_evidence",
                                   reason="已登记的项目证据文件缺失或变化。请核对后使用 --evidence 重新提交。")
        replanned, reasons = submit(a, path, state, jobs, blocked, provided) if a.action == "answer" else (set(), {})
        if a.action == "answer" and not (a.json or a.draft or state.get("handoff")):
            out("已处理 %d 份任务的回答。" % len(replanned))
            return
        draft = not state.get("handoff") and (a.draft or a.action == "draft")
        result = advance(jobs, state, path, draft, blocked, replanned)
        for row in result["jobs"]:
            if row["job"] in reasons:
                row["reason"] = reasons[row["job"]]
        if state.get("handoff"):
            from .handoff import finish_plan
            return finish_plan(path, state, jobs, result)
        return result
