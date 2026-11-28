"""Seal planned inputs, then authorize the existing batch writer from a local terminal."""
import datetime
import json
import sys
import uuid
from contextlib import ExitStack
from functools import wraps
from pathlib import Path

from . import evidence
from .common import DIError, ReviewBlocked, out, read_json, write_json
from .decisions import SOURCES, answer_binding, ledger, protected_question, record_decision, verify_receipts
from .job import Job
from .preferences import locked_preferences, project_dir


class _Delivery:
    def __init__(self, manifest, rows):
        self.manifest = manifest
        self.rows = rows


def draft_lock(generate):
    @wraps(generate)
    def serialized(job, **options):
        with locked_preferences(Path(job.dir), "draft.lock"):
            return generate(job, **options)
    return serialized


def require_delivery(authority, job, plan):
    if not isinstance(authority, _Delivery) or job.dir not in authority.rows:
        raise ReviewBlocked("本任务使用独立终端交付。请从当前交接目录运行出稿脚本或 deliver。")
    row, receipt = authority.rows[job.dir]
    check_inputs(job, row, authority.manifest["batch"])
    if (not plan or evidence.digest(plan["ops"]) != row["ops_sha256"]
            or evidence.digest(job.read("answers.json", {})) != evidence.digest(receipt["answers"])):
        raise ReviewBlocked("交付授权与当前规划或决定不匹配，请重新核对。")
    verify_receipts(job, "", job.fingerprint(files_only=True), require_signed=True)


def enable(path, state):
    jobs = [Job(p) for p in state["jobs"]]
    with ExitStack() as locks:
        for job in sorted(jobs, key=lambda j: j.dir):
            locks.enter_context(locked_preferences(Path(job.dir), "draft.lock"))
        if state.get("drafts") or any(j.drafts() or j.data.get("handoff_batch") not in (None, str(path)) for j in jobs):
            raise ReviewBlocked("已有草稿或任务已归属其他交接批次，请在新输出目录建立交接批次。")
        state["handoff"] = True
        write_json(path, state)
        for job in jobs:
            latest = Job(job.dir)
            write_json(latest.p("handoff-owner.json"), {"batch": str(path)})
            latest.data["handoff_batch"] = str(path)
            latest.save()


def file_record(path, expected=None):
    from .batch_workflow import file_sha
    p = Path(path).resolve()
    if not p.is_file():
        raise ReviewBlocked("交接所需文件缺失：" + str(p))
    sha = file_sha(p)
    if expected and sha != expected:
        raise ReviewBlocked("交接文件的 SHA-256 与登记内容不符：" + str(p))
    return {"path": str(p), "sha256": sha}


def inputs(job, batch):
    from . import cli, profile
    if job.data.get("handoff_batch") != str(batch):
        raise ReviewBlocked("任务与交接批次不匹配：" + job.dir)
    meta = {key: job.data.get(key) for key in ("workspace", "task", "profile", "formats", "rules", "skill_version")}
    pack = evidence.rule_pack(job)
    rules_path = job.data.get("rules_source")
    if rules_path:
        from .testdata import load_rules
        if load_rules(rules_path) != job.data.get("rules"):
            raise ReviewBlocked("外部测试规则已变化，请使用更新后的规则重新建立交接批次：" + rules_path)
    return {"job": job.dir, "template": file_record(job.data["target"]["path"]),
            "sources": [dict(file_record(src["path"]), alias=src["alias"]) for src in job.data["sources"]],
            "task": meta["task"], "metadata_sha256": evidence.digest(meta), "rule_pack": pack,
            "rules_file": file_record(rules_path) if rules_path else None,
            "profile_sha256": evidence.digest(profile.load(meta["profile"], cli.SKILL_DIR, project_dir(job))),
            "manual_ops_sha256": evidence.digest(job.read("manual_ops.json", []))}


def check_inputs(job, row, batch):
    if inputs(job, batch) != row["inputs"]:
        raise ReviewBlocked("模板、来源、规则、排版或手工操作已变化，请重新规划封存：" + job.dir)
    for item in row["attachments"]:
        file_record(item["path"], item["sha256"])
    for item in row["shared"]:
        file_record(item["path"], item["sha256"])


def protected_answers(job, qs, answers):
    fp = job.fingerprint(files_only=True)
    shared = evidence.reusable_answers(job, [q for q in qs if q["key"] not in answers])
    relevant = {**shared, **{k: v for k, v in answers.items() if isinstance(v, dict)
                             and v.get("fp") == fp}}
    questions = {q["key"]: q for q in qs if q.get("answered")}
    if any(protected_question(k) and k not in questions for k in relevant):
        raise ReviewBlocked("受保护决定缺少当前问题及选项，请重新规划核对：" + job.dir)
    items = []
    for key, q in questions.items():
        if not protected_question(key):
            continue
        answer = relevant.get(key)
        if not answer or "effect" not in answer or answer.get("default"):
            raise ReviewBlocked("受保护问题缺少可复核决定：" + q["id"])
        items.append({"key": key, "id": q["id"], "question": q["question"], "options": q["options"],
                      "multi": bool(q.get("multi")), "current": answer})
    return items, relevant


def snapshot(job, batch, state):
    verify_receipts(job, "", job.fingerprint(files_only=True))
    answers = job.read("answers.json", {})
    plan = job.read("plan.json")
    confirmations, effective = protected_answers(job, plan["questions"], answers)
    files = {}
    registered = state.get("registered_evidence", {}).get(job.dir, {})
    path = registered.get("file") or state.get("evidence", {}).get(job.dir)
    if path:
        item = file_record(path, registered.get("sha256"))
        files[item["path"]] = item
    records = [answers.get("_program_manifest")] + [a.get("evidence") for a in effective.values()]
    for rec in records:
        if not isinstance(rec, dict) or not rec.get("sha256"):
            continue
        path = rec.get("path") or next((p for p, item in files.items() if item["sha256"] == rec["sha256"]), None)
        if not path:
            raise ReviewBlocked("旧决定的附件缺少可复核路径，请重新提交原附件：" + str(rec.get("file", "")))
        item = file_record(path, rec["sha256"])
        files[item["path"]] = item
    shared = [file_record(project_dir(job) / "facts" / (key + ".json"))
              for key in sorted(job.read("facts.json", {}).get("shared", []))]
    return {"inputs": inputs(job, batch), "attachments": list(files.values()), "shared": shared,
            "answers_sha256": evidence.digest(answers), "questions_sha256": evidence.digest(plan["questions"]),
            "ops_sha256": evidence.digest(plan["ops"]), "decision_head": ledger(Path(job.p("decisions.jsonl")), True)[1],
            "confirmations": confirmations}


def handoff_message(path, pointer):
    from .handoff_launcher import write_launchers
    launchers = write_launchers(pointer["path"])
    message = "规划已封存。请在本机打开 %s，逐项核对后生成草稿和交付清单。" % launchers["preferred"]
    return {"status": "handoff_ready", "next_action": "handoff_to_user", "batch": str(path),
            "sealed_manifest": pointer["path"], "launchers": launchers, "launcher": launchers["preferred"],
            "message": message}


def finish_plan(path, state, jobs, result):
    for item in result["jobs"]:
        if item["status"] == "planned":
            item["next_action"] = "await_local_delivery"
    if result["status"] != "planned":
        if state.pop("active_handoff", None):
            write_json(path, state)
        return result
    content = {"schema": 1, "batch": str(path), "jobs": [snapshot(job, path, state) for job in jobs]}
    sha = evidence.digest(content)
    pointer = state.get("active_handoff", {})
    if pointer.get("content_sha256") != sha:
        folder = path.parent / ("交接_" + path.stem + "_" + uuid.uuid4().hex[:12])
        folder.mkdir()
        manifest = folder / "handoff.json"
        write_json(manifest, content)
        pointer = {"path": str(manifest), "content_sha256": sha, "file_sha256": file_record(manifest)["sha256"]}
        state["active_handoff"] = pointer
        write_json(path, state)
    file_record(pointer["path"], pointer["file_sha256"])
    result.update(handoff_message(path, pointer))
    out(result["message"])
    return result


def resume_message(state, path):
    response = handoff_message(path, state["active_handoff"])
    out(response["message"])
    return response


def read_choice(prompt):
    try:
        value = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        value = ""
    if not value or value == "0":
        raise ReviewBlocked("已取消交付。")
    return value


def display(value):
    # Render externally supplied labels as a single line in the terminal.
    return "".join(c if c.isprintable() else " " for c in str(value))


def interview(rows):
    from .cli import terminal_account
    author = terminal_account()
    choices = {}
    changed = False
    out("逐项输入当前选项编号确认。输入其他有效选项将登记更改并停止本次出稿，输入 0 或回车可取消。")
    for n, row in enumerate(rows, 1):
        out("J%d %s" % (n, display(row["inputs"]["template"]["path"])))
        selected = {}
        for q in row["confirmations"]:
            current = q["current"]
            options = list(q["options"])
            indices = current.get("choice", [])
            if not indices or any(i < 0 or i >= len(options) for i in indices):
                indices = [i for i, opt in enumerate(options) if opt["effect"] == current["effect"]]
            if not indices:
                options.append({"label": current["label"], "effect": current["effect"]})
                indices = [len(options) - 1]
            shown = ",".join(str(i + 1) for i in indices)
            out("J%d %s %s" % (n, q["id"], display(q["question"])))
            for i, opt in enumerate(options, 1):
                out("  %d. %s" % (i, display(opt["label"])))
            out("当前决定 [%s] %s；实际效果 %s" % (shown, display(current["label"]),
                                               display(json.dumps(current["effect"], ensure_ascii=False))))
            source = current.get("source") or ("word_reply" if current.get("word_draft") else "agent_relay")
            out("原确认渠道：" + display(SOURCES.get(source, source)))
            if current.get("user_quote"):
                out("原记录所引用户原话：" + display(current["user_quote"]))
            raw = read_choice("请输入核对后的选项编号（0 或回车取消）：")
            parts = raw.replace("，", ",").split(",")
            if (not all(p.strip().isascii() and p.strip().isdecimal() for p in parts)
                    or (len(parts) > 1 and not q["multi"])):
                raise ReviewBlocked("选项无效，已取消交付。")
            chosen = [int(p) - 1 for p in parts]
            if len(chosen) != len(set(chosen)) or any(i < 0 or i >= len(options) for i in chosen):
                raise ReviewBlocked("选项无效，已取消交付。")
            corrected = sorted(chosen) != sorted(indices)
            changed |= corrected
            value = dict(current)
            if corrected:
                selected_options = [options[i] for i in chosen]
                value = {"choice": chosen, "label": "；".join(o["label"] for o in selected_options),
                         "effect": [o["effect"] for o in selected_options] if q["multi"] else selected_options[0]["effect"]}
            selected[q["key"]] = {"value": value, "reply": q["id"] + "=" + raw, "corrected": corrected}
        choices[row["inputs"]["job"]] = selected
    if changed:
        out("选项有变更，本次仅登记修正。请重新规划封存后再次交付。")
    elif read_choice("核对完成，输入 交付 或 DELIVER 开始出稿（其他输入取消）：") not in ("交付", "DELIVER"):
        raise ReviewBlocked("已取消交付。")
    return author, choices, changed


def prepare(job, row, chosen, author, corrected):
    answers = job.read("answers.json", {})
    details = []
    quote = " ".join(it["reply"] for it in chosen.values())
    for q in row["confirmations"]:
        item = chosen[q["key"]]
        if corrected and not item["corrected"]:
            continue
        rec = dict(item["value"])
        for key in ("word_draft", "word_parent", "word_reply", "shared_key", "remember"):
            rec.pop(key, None)
        rec.update(fp=job.fingerprint(files_only=True), rule_sha256=evidence.rule_pack(job)["sha256"],
                   source="confirm_cli", confirmed_by=author, user_quote=quote, receipt_version=2,
                   answered_at=datetime.datetime.now().astimezone().isoformat(timespec="seconds"))
        answers[q["key"]] = rec
        details.append({"id": q["id"], "key": q["key"], "question": q["question"], "answer": rec["label"],
                        "fp": rec["fp"], "binding": answer_binding(rec)})
    return {"answers": answers, "details": details, "quote": quote}


def checkpoint(job, row, txn, author, seal):
    """Accept only the original state or this transaction's single recorded successor."""
    log = Path(job.p("decisions.jsonl"))
    events, head = ledger(log, True)
    original = row["decision_head"]
    if head != original:
        event = events[-1]
        expected = {"prev_hash": original, "action": "confirm", "source": "confirm_cli", "handoff": seal,
                    "user_quote": txn["quote"], "answers": txn["details"], "author": author}
        if not txn["details"] or any(event.get(k) != v for k, v in expected.items()):
            raise ReviewBlocked("确认期间决定日志发生变化：" + job.dir)
    current = evidence.digest(job.read("answers.json", {}))
    after = evidence.digest(txn["answers"])
    if current not in (row["answers_sha256"], after) or (current != row["answers_sha256"] and head == original):
        raise ReviewBlocked("确认期间已有答案发生变化：" + job.dir)
    if txn["details"] and head == original:
        record_decision(job, "confirm", txn["quote"], author=author, source="confirm_cli",
                        handoff=seal, answers=txn["details"])
    if current != after:
        job.write("answers.json", txn["answers"])


def guarded_jobs(content, state):
    paths = [row["inputs"]["job"] for row in content["jobs"]]
    if not paths or paths != state["jobs"] or len(set(paths)) != len(paths):
        raise ReviewBlocked("批次任务目录与封存清单不一致。")
    jobs = [Job(jd) for jd in paths]
    from .batch_workflow import changed_deliveries
    untracked = [j for j in jobs if j.dir not in state.get("drafts", {}) and
                 (j.drafts() or Path(j.p("审核报告.md")).exists() or
                  (Path(j.p("草稿")).is_dir() and any(Path(j.p("草稿")).iterdir())))]
    if changed_deliveries(jobs, state) or untracked:
        raise ReviewBlocked("发现未登记、已修改或缺失的交付文件。请先人工核对现有草稿和报告。")
    for job, row in zip(jobs, content["jobs"]):
        check_inputs(job, row, content["batch"])
    return jobs


def check_sealed(jobs, content, state):
    for job, row in zip(jobs, content["jobs"]):
        if snapshot(job, content["batch"], state) != row:
            raise ReviewBlocked("封存后问题、决定、计划或附件发生变化，请重新规划封存：" + job.dir)


def cmd_deliver(a):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ReviewBlocked("deliver 需要在独立交互式终端显示问题并由审核人输入。")
    manifest = Path(a.manifest).resolve()
    try:
        content = read_json(manifest)
    except ValueError as exc:
        raise ReviewBlocked("封存清单损坏，请重新规划生成交接目录。") from exc
    if manifest.name != "handoff.json" or not isinstance(content, dict) or content.get("schema") != 1 or not isinstance(content.get("batch"), str):
        raise DIError("请指定本批交接目录中的 handoff.json。")
    path = Path(content["batch"])
    if not path.is_file() or manifest.parent.parent != path.parent:
        raise ReviewBlocked("清单与原始批次的位置不匹配。")
    with locked_preferences(path.parent, path.name + ".lock"):
        state = read_json(path)
        pointer = state.get("active_handoff") or {}
        if (not state.get("handoff") or pointer.get("path") != str(manifest)
                or pointer.get("content_sha256") != evidence.digest(content)):
            raise ReviewBlocked("清单已过期或不属于当前批次，请打开最新交接目录。")
        file_record(manifest, pointer["file_sha256"])
        jobs = guarded_jobs(content, state)
        rows = content["jobs"]
        txn = state.get("local_delivery")
        if not txn:
            check_sealed(jobs, content, state)
            author, choices, corrected = interview(rows)
            jobs = guarded_jobs(content, state)
            check_sealed(jobs, content, state)
            txn = {"seal": pointer["file_sha256"], "author": author, "corrected": corrected,
                   "jobs": [prepare(j, r, choices[j.dir], author, corrected) for j, r in zip(jobs, rows)]}
            state["local_delivery"] = txn
            state["local_delivery_sha256"] = evidence.digest(txn)
            write_json(path, state)
        if state.get("local_delivery_sha256") != evidence.digest(txn) or txn["seal"] != pointer["file_sha256"]:
            raise ReviewBlocked("交付进度属于另一份封存清单。")
        for job, row, change in zip(jobs, rows, txn["jobs"]):
            checkpoint(job, row, change, txn["author"], txn["seal"])
        if txn["corrected"]:
            del state["active_handoff"], state["local_delivery"], state["local_delivery_sha256"]
            write_json(path, state)
            raise ReviewBlocked("已登记更改。请重新运行 batch plan 并打开新的交接目录。")
        from . import cli
        from .batch_workflow import advance, delivery_stamp
        from .draft_review import pending
        for job, row in zip(jobs, rows):
            cached = state.get("drafts", {}).get(job.dir)
            if cached:
                if delivery_stamp(job) != cached["stamp"]:
                    raise ReviewBlocked("已交付任务的决定发生变化，请核对现有草稿。")
            else:
                plan = cli.do_plan(job, quiet=True)
                if pending(plan["questions"]) or evidence.digest(plan["ops"]) != row["ops_sha256"]:
                    raise ReviewBlocked("执行前复核发现规划已变化，已停止出稿。请重新规划封存。")
                if all(op["op"] == "format_heading" for op in plan["ops"]):
                    raise ReviewBlocked("缺少可填写的目标与来源对应：" + job.dir)
            verify_receipts(job, "", job.fingerprint(files_only=True), require_signed=True)
        authority = _Delivery(content, {j.dir: (row, change) for j, row, change in zip(jobs, rows, txn["jobs"])})
        result = advance(jobs, state, path, True, {}, [j.dir for j in jobs], _authority=authority)
        if result["status"] != "draft_ready":
            raise ReviewBlocked("本批仍需核对，请查看上方任务状态。")
        return result
