# -*- coding: utf-8 -*-
"""命令行入口。所有命令输出精简结果 + “下一步”提示，便于大/小模型按步骤执行。"""
from __future__ import annotations

import argparse
import contextlib
import csv
import datetime
import glob
import json
import os
import shlex
import shutil
import sys
import tempfile

from . import office
from . import evidence
from . import preferences
from .cleanup import cmd_clean
from .common import DIError, TOOL_AUTHOR, ensure_dir, out, read_json, setup_console, short, write_json
from .content import outline, parse_ref
from .source_queries import cmd_figure, cmd_outline, cmd_search, cmd_show
from .decisions import (VALUE_QUESTIONS, answer_binding, protected_question, record_decision, require_quote,
                        stored_reply_needed, value_option)
from .draft_review import check_with_office, pending, run_selfcheck, write_report
from .input_paths import cmd_find, expand_pattern, join_paths, split_paths
from .job import Job
from .handoff import draft_lock
from .workflow import ask_form, plan_result, planned_text, review_excerpts, review_references, review_summary, timed
from .manual_ops import record_ops, validate_ops

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY = "python \"%s\"" % os.path.join(SKILL_DIR, "scripts", "di.py")
_portable_python = os.path.join(SKILL_DIR, "runtime", "python", "python.exe")
if os.name == "nt" and os.path.isfile(_portable_python):
    PY = '& "%s" "%s"' % (_portable_python, os.path.join(SKILL_DIR, "scripts", "di.py"))
elif os.name == "posix":
    _portable_python = os.path.join(SKILL_DIR, "runtime", "python", "bin", "python3")
    if os.path.isfile(_portable_python):
        PY = shlex.join([_portable_python, "-E", "-s", "-B", os.path.join(SKILL_DIR, "scripts", "di.py")])


def nxt(*steps):
    out("\n下一步：")
    for s in steps:
        out("  - " + s)


def load_aliases():
    from .engineering import symbol_aliases
    return symbol_aliases(read_json(os.path.join(SKILL_DIR, "config", "rules", "aliases.json"), {}))


def load_learned(job):
    return preferences.mappings(SKILL_DIR, job)


# ======================================================================= 规划
@timed("plan")
def do_plan(job, quiet=False, extra_answers=None):
    """extra_answers：仅本次使用、不保存的回答（--defaults 时的推荐项）。"""
    from .planner import Planner
    answers = job.read("answers.json", {}) or {}
    manual = job.read("manual_ops.json", []) or []
    if manual:
        validate_ops(manual, job.target_model())
    fp = job.fingerprint()
    rule_sha = evidence.rule_pack(job)["sha256"]
    stale_ans = [k for k, v in answers.items() if isinstance(v, dict)
                 and ((v.get("fp") and v["fp"] != fp) or v.get("rule_sha256", rule_sha) != rule_sha)]
    known_ids = job.data.get("question_ids", {})
    answers = {k: v for k, v in answers.items() if k not in stale_ans and not stored_reply_needed(k, v, job.data.get("task"), known_ids.get(k))
               and not (isinstance(v, dict) and v.get("effect") == {"await_evidence": True})}
    manifest = answers.pop("_program_manifest", None)
    if manifest and evidence.program_manifest_issue(manifest.get("path"), manifest.get("rows", []), job):
        stale_ans.append("_program_manifest")
        manifest = None
    # Recheck the labels currently contributing to a result; previous selections may have changed.
    submitted = {k: v for k, v in answers.items() if isinstance(v, dict) and v.get("from_manifest")}
    answers = {k: v for k, v in answers.items() if k not in submitted}
    merged = dict(answers)
    merged.update(extra_answers or {})
    learned = load_learned(job)
    def plan_with(current):
        return Planner(job, answers=current, learned={}, aliases=load_aliases(), manual_ops=manual,
                       rules_override=job.data.get("rules", {})).run()

    plan = plan_with(merged)
    reused, confirmed = {}, {}
    while True:
        pending_facts = [q for q in plan["questions"] if q["key"] not in merged]
        additions = evidence.reusable_answers(job, pending_facts)
        additions = {k: v for k, v in additions.items() if not stored_reply_needed(k, v, job.data.get("task"), known_ids.get(k))}
        if manifest:
            local = evidence.manifest_answers(manifest, pending_facts)
            for key, item in local.items():
                if submitted.get(key, {}).get("effect") == item["effect"]:
                    local[key] = submitted[key]
            additions.update(local)
        # Resolve mappings before units and conditions, since they determine the set of source labels.
        mappings = {k: v for k, v in additions.items() if not k.startswith(("td:units:", "td:conditions:"))}
        additions = {k: v for k, v in (mappings or additions).items() if k not in merged}
        if not additions:
            break
        confirmed.update((q["key"], q) for q in pending_facts if q["key"] in additions)
        reused.update(additions)
        merged.update(additions)
        plan = plan_with(merged)
    current_keys = {q["key"] for q in plan["questions"]}
    plan["questions"].extend(q for key, q in confirmed.items() if key not in current_keys)
    # 问题编号在任务内保持稳定：已回答的问题仍保留原编号，新问题顺延编号
    prev = job.read("questions.json", []) or []
    cur = {q["key"]: q for q in plan["questions"]}
    qs, used = [], set()
    for pq in prev:
        if pq["key"] in cur:
            q = dict(cur[pq["key"]], id=pq["id"])
        elif pq["key"] in merged:          # 已回答（或本次按推荐默认）的问题保留原编号，便于之后正式回答
            q = dict(pq)
        else:
            continue
        qs.append(q)
        used.add(q["key"])
    known = dict(job.data.get('question_ids', {}))
    known.update((q['key'], q['id']) for q in prev)
    n = max([int(qid[1:]) for qid in known.values() if qid[1:].isdigit()] + [0])
    for q in plan["questions"]:
        if q["key"] not in used:
            if q['key'] not in known:
                n += 1
                known[q['key']] = 'Q%d' % n
            qs.append(dict(q, id=known[q['key']]))
    if known != job.data.get('question_ids', {}):
        job.data['question_ids'] = known
        job.save()
    for q in qs:
        q['input_fp'] = fp
        q['rule_sha256'] = rule_sha
        q["answered"] = q["key"] in answers or q["key"] in reused
        q["learned"] = q["key"] in learned and q["key"] not in answers
        lk = learned.get(q["key"]) if q["learned"] else None
        if lk:      # 记住的上次选择只作推荐项，本次仍需确认（文件/批次可能不同）
            for i, o in enumerate(q["options"]):
                if o["label"] == lk.get("label"):
                    q["recommended"] = i
                    break
        q["defaulted"] = q["key"] in (extra_answers or {})
    qs.sort(key=lambda q: -q.get("impact_count", 0))
    plan["questions"] = qs
    plan["stale_answers"] = stale_ans
    evidence.save_plan_facts(job, plan, {**reused, **answers}, manifest)
    report = plan["report"]
    if "measurement_targets" in report:
        report["measurement_written"] = {cell: value for cell, value in planned_text(plan["ops"]).items()
                                         if cell in report["measurement_targets"]}
    job.write("plan.json", plan)
    job.write("questions.json", qs)
    write_questions_md(job, qs)
    if not quiet:
        print_plan(job, plan)
    return plan


def default_answers(qs):
    out = {}
    for q in qs:
        i = q.get("recommended", 0)
        eff = q["options"][i]["effect"]
        out[q["key"]] = {"effect": [eff] if q.get("multi") else eff, "label": q["options"][i]["label"], "default": True}
    return out


def write_questions_md(job, qs):
    lines = ["# 待确认问题（%s）" % os.path.basename(job.dir), "",
             "回答：`%s answer \"%s\" Q1=1 Q2=1,3`（数字=选项序号，多选用逗号；自由回答：Q3=\"文字\"）" % (PY, job.dir),
             '来源核对、未读取内容、OCR 原页复核、样品对应和 --remember 须附 --user-quote "用户原话"。', ""]
    for q in qs:
        flag = "（已回答）" if q.get("answered") else ("（已按记住的规则）" if q.get("learned") else "")
        lines.append("## %s [%s]%s %s" % (q["id"], q["topic"], flag, "（可多选）" if q.get("multi") else ""))
        lines.append(q["question"])
        for i, o in enumerate(q["options"]):
            lines.append("  %d) %s%s" % (i + 1, o["label"], "  ← 推荐" if i == q.get("recommended", 0) else ""))
        lines.append("")
    with open(job.p("questions.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def print_plan(job, plan):
    out("规划完成：%s" % job.dir)
    if plan.get("stale_answers"):
        out("  [注意] 输入文件或取值规则在回答之后发生变化：%d 个旧回答已失效，需要重新确认（%s）" % (len(plan["stale_answers"]), "、".join(short(k, 24) for k in plan["stale_answers"][:5])))
    for s in plan["summary"]:
        out("  " + s)
    rep = plan["report"]
    out("  填写操作 %d 个；已有来源的填写 %d 处" % (len(plan["ops"]), len(rep["fills"])))
    if rep["unfilled"]:
        out("  未找到来源（需人工或提问）：" + "；".join(short(x, 40) for x in rep["unfilled"][:12]))
    if rep["ignored"]:
        out("  忽略的数据项：" + "；".join(short(x, 50) for x in rep["ignored"][:8]))
    for wmsg in rep["warnings"][:6]:
        out("  [注意] " + wmsg)
    pend = pending(plan["questions"])
    if pend:
        out("\n待确认问题 %d 个（已写入 questions.md）：" % len(pend))
        for q in pend[:4]:
            out("  %s [%s] %s" % (q["id"], q["topic"], short(q["question"], 150)))
            for i, o in enumerate(q["options"]):
                out("      %d) %s%s" % (i + 1, short(o["label"], 60), "  ← 推荐" if i == q.get("recommended", 0) else ""))
        nxt("把上面的问题交给用户选择（Claude Code 可用 AskUserQuestion；也可直接文字询问），不要替用户决定",
            "记录回答：%s answer \"%s\" Q1=1 Q2=2 ...（多选 Q3=1,2；自由文本 Q4=\"...\"；来源或样品确认须附 --user-quote \"用户原话\"）" % (PY, job.dir),
            "全部回答后：%s draft \"%s\"" % (PY, job.dir))
    else:
        nxt("无待确认问题，生成草稿：%s draft \"%s\"" % (PY, job.dir))


# ======================================================================= 命令实现
def cmd_doctor(a):
    from . import selfcheck
    from . import runtime_maintenance as maintenance
    if getattr(a, "apply", False) and not getattr(a, "prune", False):
        raise DIError("doctor --apply 须与 --prune 一起使用。")
    if getattr(a, "prune", False):
        result = maintenance.prune(apply=a.apply)
        if result["note"]:
            out(result["note"])
        for version, why in result["kept"].items():
            out("保留 %s：%s" % (version, why))
        for version in result["eligible"]:
            out("可清理 %s；应用请运行 doctor --prune --apply" % version)
        for version in result["removed"]:
            out("已清理 " + version)
        return 0
    hint = maintenance.execution_hint(maintenance.configured_cache())
    if hint:
        out(hint)
    for ln in selfcheck.environment_lines():
        out(ln)
    target = getattr(a, "selfcheck", None)
    if target:
        out("")
        rep = selfcheck.check_file(target)
        for ln in selfcheck.render(rep, per_group=8):
            out(ln)
        return 2 if rep.has_errors() else 0
    return 0


def _src_list(a):
    srcs = []
    for s in a.source or []:
        srcs.extend(split_paths(s))
    return srcs


def cmd_new(a):
    with timed("parse"):
        job = Job.create(a.target, _src_list(a), task=a.task or "", out_root=a.out, name=a.name, profile=a.profile,
                     formats=[f for f in (a.format or "").split(",") if f], test_rules=getattr(a, "test_rules", None))
    out("已建立任务：%s" % job.dir)
    out("源文件：" + "；".join("%s=%s" % (s["alias"], s["path"]) for s in job.data["sources"]))
    if getattr(a, "draft", False):
        return generate_draft(job, stop_if_unmapped=True)
    if a.plan and getattr(a, "json", False):
        return plan_result(job, do_plan(job, quiet=True))
    out("")
    for al, md in job.source_models().items():
        out(outline(md, al, max_lines=a.lines, max_depth=a.depth))
    from .target import analyze, summary
    out("")
    out(summary(analyze(job.target_model())))
    if a.plan:
        out("")
        return plan_result(job, do_plan(job))
    else:
        nxt("自动规划：%s plan \"%s\"" % (PY, job.dir))


def cmd_plan(a):
    do_plan(Job(a.job))


def cmd_questions(a):
    job = Job(a.job)
    if getattr(a, "csv", None):
        plan = do_plan(job, quiet=True)
        evidence.export_review_sheet(job, plan, a.csv)
        out("待核清单：" + os.path.abspath(a.csv))
        return
    qs = job.read("questions.json")
    if (not qs or qs[0].get('input_fp') != job.fingerprint(files_only=True)
            or qs[0].get('rule_sha256') != evidence.rule_pack(job)['sha256']):
        qs = do_plan(job, quiet=True)["questions"]
    pend = pending(qs) if not a.all else qs
    if a.ask_json:
        groups = [ask_form(pend[i:i + 4]) for i in range(0, len(pend), 4)]
        out(json.dumps(groups, ensure_ascii=False, indent=1))
        out("\n提示：AskUserQuestion 每次最多4题；回答后用 description 中的“选项N”记录：answer Qx=N")
        return
    if not pend:
        out("没有待确认的问题。")
        return
    out(open(job.p("questions.md"), encoding="utf-8").read())


def _choice_indices(q, val):
    if not val:
        raise DIError('%s 的回答为空。' % q['id'])
    parts = val.split(',')
    if not all(part.strip().isdecimal() for part in parts):
        return None
    indices = [int(part) - 1 for part in parts]
    if len(indices) > 1 and not q.get('multi'):
        raise DIError('%s 是单选题，请只提交一个选项。' % q['id'])
    for index in indices:
        if not 0 <= index < len(q['options']):
            raise DIError('%s 没有选项 %d' % (q['id'], index + 1))
    return indices


def cmd_confirm(a):
    if not sys.stdin.isatty():
        raise DIError("confirm 需要审核人本人在交互式终端输入选项。")
    job = Job(a.job)
    questions = do_plan(job, quiet=True)["questions"]
    answers = job.read("answers.json", {}) or {}
    items = []
    for q in questions:
        if not protected_question(q["key"]):
            continue
        out("%s %s" % (q["id"], q["question"]))
        current = answers.get(q["key"], {})
        if current and current.get("fp") == q["input_fp"]:
            out("当前决定：%s；%s" % (current["label"], json.dumps(current["effect"], ensure_ascii=False)))
        for i, opt in enumerate(q["options"], 1):
            out("  %d. %s" % (i, opt["label"]))
        try:
            value = input("请输入本人核对后的选项序号（回车跳过）：").strip()
        except (EOFError, KeyboardInterrupt):
            raise DIError("确认已取消，本轮没有登记。") from None
        if value:
            if not value.isdecimal() or not 1 <= int(value) <= len(q["options"]):
                raise DIError("无效选项，本轮没有登记。")
            items.append(q["id"] + "=" + value)
    if not items:
        out("本轮没有登记受保护决定。")
        return
    author = terminal_account()
    submission = argparse.Namespace(job=job.dir, items=items, remember=False, reviewer=author, user_quote=" ".join(items))
    return cmd_answer(submission, confirmation=(questions, answers))


def terminal_account():
    if os.name == "nt":
        return os.getlogin()
    import pwd
    return pwd.getpwuid(os.geteuid()).pw_name


def cmd_answer(a, *, confirmation=None):
    job = Job(a.job)
    quote = getattr(a, "user_quote", None)
    if a.remember:
        require_quote(quote, "answer --remember")
    decisions = []
    qs = job.read("questions.json") or do_plan(job, quiet=True)["questions"]
    fp = job.fingerprint(files_only=True)
    rule_sha = evidence.rule_pack(job)['sha256']
    if any(q.get('input_fp') != fp or q.get('rule_sha256', rule_sha) != rule_sha for q in qs):
        do_plan(job, quiet=True)
        raise DIError('问题依据的输入文件已变化或属于旧版任务。已重新规划，请查看当前 questions 后再回答。')
    byid = {q["id"]: q for q in qs}
    answers = job.read("answers.json", {}) or {}
    if confirmation is not None and confirmation != (qs, answers):
        raise DIError("交互期间问题或已有决定发生变化，请重新运行 confirm。")
    remembered = {}
    items = a.items
    attachment = None
    sheet_metadata = {}
    if getattr(a, "evidence", None):
        items, attachment = evidence.program_answers(qs, items, a.evidence, job=job)
    elif getattr(a, "from_sheet", None):
        if items:
            raise DIError("--from-sheet 与命令行回答不能同时使用。")
        items, sheet_metadata = evidence.sheet_submission(job, qs, a.from_sheet)
    if attachment:
        old = answers.get("_program_manifest", {})
        if old.get("sha256") != attachment["sha256"]:
            answers = {key: rec for key, rec in answers.items() if not (isinstance(rec, dict) and rec.get("from_manifest"))}
        answers["_program_manifest"] = dict(fp=fp, rule_sha256=rule_sha,
                                            **{k: v for k, v in attachment.items() if k != "effects"})
    if not items and not attachment:
        raise DIError("请提供至少一个有效回答。")
    protected_ids = {item.partition("=")[0].strip().upper() for item in items}
    multiple = getattr(a, "multiple_protected", False) or sum(
        protected_question(byid[i]["key"]) for i in protected_ids if i in byid) > 1
    selected_ids = set()
    for item in items:
        if "=" not in item:
            raise DIError("格式应为 Q1=2，收到：%s" % item)
        qid, val = item.split("=", 1)
        qid = qid.strip().upper()
        if qid in selected_ids:
            raise DIError("同一问题不能在一次提交中回答两次：" + qid)
        selected_ids.add(qid)
        q = byid.get(qid)
        if not q:
            raise DIError("没有问题 %s（先运行 plan 或 questions 查看）" % qid)
        if protected_question(q["key"]):
            require_quote(quote, "%s %s" % (qid, q["topic"]))
        val = val.strip().strip('"').strip("“”")
        rec = {"label": "", "answered_at": datetime.datetime.now().isoformat(timespec="seconds"), "fp": fp, "rule_sha256": rule_sha}
        if a.remember:
            rec["remember"] = True
        if q["key"].startswith((*VALUE_QUESTIONS, "src:spec:", "src:verify:")):
            rec["quoted_id"] = getattr(a, "batch_refs", {}).get(qid, qid)
        if getattr(a, "reviewer", None):
            rec["confirmed_by"] = a.reviewer
        idx = _choice_indices(q, val)
        if idx is not None:
            effs = [q["options"][i]["effect"] for i in idx]
            rec["choice"] = idx
            rec["label"] = "；".join(q["options"][i]["label"] for i in idx)
            rec["effect"] = effs if q.get("multi") else effs[0]
        else:
            rec["text"] = val
            eff = {"free": True, "text": val}
            if q["key"] == "fields:missing":
                vals = {}
                for part in val.replace("；", ";").replace("，", ";").split(";"):
                    if "=" in part or "：" in part or ":" in part:
                        k, v = (part.split("=", 1) if "=" in part else part.replace("：", ":").split(":", 1))
                        vals[k.strip()] = v.strip()
                eff = {"values": vals}
            rec["effect"] = eff
            rec["label"] = "自由回答：" + val
            if q["key"].startswith(VALUE_QUESTIONS):
                selected_value = value_option(q["options"], val)
                if selected_value is not None:
                    opt = q["options"][selected_value]
                    idx = [selected_value]
                    rec.update(effect=opt["effect"], label=opt["label"], choice=idx)
        if qid in sheet_metadata:
            rec["evidence"] = sheet_metadata[qid]
        if attachment and qid in attachment["effects"]:
            rec["effect"] = attachment["effects"][qid]
            rec["evidence"] = {k: attachment[k] for k in ("file", "path", "sha256")}
            rec["from_manifest"] = True
        current = answers.get(q["key"], {})
        if (confirmation is not None and current.get("fp") == fp and current.get("rule_sha256", rule_sha) == rule_sha
                and current.get("choice") == idx and current.get("label") == rec["label"] and current.get("evidence")):
            rec.update(effect=current["effect"], evidence=current["evidence"])
        unresolved_value = q["key"].startswith(VALUE_QUESTIONS) and rec["effect"].get("free")
        if unresolved_value or (confirmation is None and not rec.get("evidence")):
            from .decisions import needs_reviewer_reply
            choice = idx[0] + 1 if idx and len(idx) == 1 else None
            if unresolved_value or needs_reviewer_reply(q["key"], rec["effect"], job.data.get("task"), quote,
                                    requires_program=q.get("requires_program", False), refs=(qid, rec.get("quoted_id")),
                                    choice=choice, multiple=multiple):
                reason = "%s 需审核人就当前问题补充核对结果；请展示待答题并等待回复，本次提交尚未登记。" % qid
                out(reason)
                return dict(plan_result(job, {"questions": qs}), reason=reason)
        answers[q["key"]] = rec
        if quote:
            rec["source"] = "confirm_cli" if confirmation is not None else "agent_relay"
            rec["user_quote"] = quote
            rec["receipt_version"] = 2
            decisions.append({"id": qid, "key": q["key"], "question": q["question"],
                              "answer": rec["label"], "fp": rec["fp"], "binding": answer_binding(rec)})
        out("%s ← %s" % (qid, short(rec["label"], 80)))
        if a.remember and q["key"].startswith("td:") and isinstance(rec["effect"], dict) and not rec["effect"].get("free"):
            remembered[q["key"]] = {"effect": rec["effect"], "label": rec["label"]}
    if decisions:
        action = "confirm" if confirmation is not None else ("answer --remember" if a.remember else "answer")
        record_decision(job, action, quote, source="confirm_cli" if confirmation is not None else "agent_relay",
                        author=getattr(a, "reviewer", None), answers=decisions)
    job.write("answers.json", answers)
    if remembered:
        out("已保存项目规则：%s。后续任务会把已有对应作为推荐项。" % preferences.remember(job, remembered))
    plan = do_plan(job, quiet=True)
    pend = pending(plan["questions"])
    free = [q for q in plan["questions"] if isinstance(answers.get(q["key"], {}).get("effect"), dict)
            and answers[q["key"]]["effect"].get("free")]
    if free:
        out("\n以下为自由文本回答，需要你（模型）据此手工调整（set / add-op 命令）：")
        for q in free:
            out("  %s %s → “%s”" % (q["id"], short(q["question"], 60), answers[q["key"]]["text"]))
    if getattr(a, "draft", False):
        return generate_draft(job, plan=plan, stop_if_unmapped=True)
    if pend:
        out("\n仍有 %d 个问题未回答：%s" % (len(pend), "、".join(q["id"] for q in pend)))
        nxt("继续回答：%s answer \"%s\" ..." % (PY, job.dir))
    else:
        nxt("生成草稿：%s draft \"%s\"" % (PY, job.dir))


    return plan_result(job, plan)


def cmd_set(a):
    job = Job(a.job)
    op = {"op": "set_text" if not a.runs else "set_cell", "cell": a.ref}
    if a.runs:
        op["runs"] = json.loads(a.value)
    else:
        op["text"] = a.value
    ops = [op]
    if a.note or a.src:
        links = []
        if a.src:
            al, ref = parse_ref(a.src)
            links.append({"label": "打开原文", "alias": al, "ref": ref})
        ops.append({"op": "comment", "anchor": a.ref, "text": "【人工指定】" + (a.note or ""), "links": links})
    record_ops(job, ops)
    out("已记录：%s = %s" % (a.ref, short(a.value, 60)))
    nxt("重新生成草稿：%s draft \"%s\"" % (PY, job.dir))


def cmd_add_section(a):
    from .common import contains_score
    from .target import analyze
    job = Job(a.job)
    an = analyze(job.target_model())
    sec = None
    if a.to.startswith("P") and a.to[1:].isdigit():
        sec = next((s for s in an["sections"] if s["start"] == a.to), None)
    else:
        cands = sorted(an["sections"], key=lambda s: -contains_score(a.to, s["title"]))
        if cands and contains_score(a.to, cands[0]["title"]) >= 0.5:
            sec = cands[0]
    if not sec:
        raise DIError("目标中找不到章节“%s”。可用章节：%s" % (a.to, "；".join("%s %s" % (s["start"], s["title"]) for s in an["sections"])))
    alias, ref = parse_ref(a.src)
    md = job.model(alias)
    if ref not in md.by or "blocks" not in md.by[ref]:
        raise DIError("%s 不是章节ID（应为 H 开头，用 outline 查看）" % a.src)
    record_ops(job, [{"op": "add_section", "anchor": sec["start"], "alias": alias, "sec": ref, "title": a.title,
                      "mode": "replace" if a.replace else "append", "subheading": not a.no_subheading}])
    out("已记录：目标“%s” %s 源 %s“%s”" % (sec["title"], "替换为" if a.replace else "追加", a.src, short(md.by[ref]["title"], 30)))
    nxt("重新生成草稿：%s draft \"%s\"" % (PY, job.dir))


def cmd_add_op(a):
    job = Job(a.job)
    try:
        if a.op.startswith("@"):
            with open(a.op[1:], encoding="utf-8-sig") as stream:
                data = json.load(stream)
        else:
            data = json.loads(a.op)
    except ValueError as exc:
        raise DIError("手工操作 JSON 无效：%s" % exc) from exc
    ops = data if isinstance(data, list) else [data]
    record_ops(job, ops, replace=a.replace)
    out("已登记 %d 个手工操作（manual_ops.json）" % len(ops))


def cmd_draft(a):
    return generate_draft(Job(a.job), defaults=a.defaults, check=a.check,
                          raw_snapshot=getattr(a, "raw_snapshot", False),
                          discard_edits=getattr(a, "discard_edits", False), user_quote=getattr(a, "user_quote", None))


@draft_lock
def generate_draft(job, *, defaults=False, check=False, raw_snapshot=False, discard_edits=False,
                   stop_if_unmapped=False, user_quote=None, plan=None, _authority=None):
    if Job(job.dir).data.get("handoff_batch") or os.path.isfile(job.p("handoff-owner.json")):
        from .handoff import require_delivery
        require_delivery(_authority, job, plan)
    from .profile import load as load_profile
    from .write_docx import DocxWriter
    flags = [flag for enabled, flag in ((defaults, "--defaults"), (discard_edits, "--discard-edits")) if enabled]
    if flags:
        require_quote(user_quote, "draft " + " ".join(flags))
    if plan is None:
        plan = do_plan(job, quiet=True)
    pend = pending(plan["questions"])
    if pend and not defaults:
        print_plan(job, plan)
        out('\n还有 %d 个问题未回答，暂不生成草稿。用户同意全部按推荐处理时，可加 --defaults --user-quote "用户原话"。' % len(pend))
        return plan_result(job, plan)
    if stop_if_unmapped and all(op["op"] == "format_heading" for op in plan["ops"]):
        out("尚未找到可填写的目标与来源。请指定模板位置和对应的来源章节或字段，然后在本任务中继续。")
        return {"status": "blocked", "job": job.dir, "reason": "缺少可填写的目标与来源对应", "next_action": "resolve_blockers"}
    if pend:
        extra = {}
        for _ in range(4):      # 按推荐回答后可能出现新问题，迭代至稳定
            extra.update(default_answers(pend))
            plan = do_plan(job, quiet=True, extra_answers=extra)
            pend = [q for q in pending(plan["questions"]) if q["key"] not in extra]
            if not pend:
                break
        if pend:
            print_plan(job, plan)
            raise DIError('出现了新的待确认问题。请核对 questions 后再生成草稿。')
        pend = [q for q in plan["questions"] if q.get("defaulted")]
    tm = job.target_model().m
    work = tm["work"]
    if os.path.splitext(work)[1].lower() != ".docx":
        raise DIError("目前草稿只能写入 Word 目标（docx/doc/wps/rtf）。Excel 目标暂不支持写入（没有 xlsx-fill 这个命令），Excel 目前只能作源文件。")
    draft, ver = job.next_draft()
    if flags:
        action = "draft " + " ".join(flags)
        job.data.setdefault("draft_exceptions", {})[os.path.basename(draft)] = {
            "action": action, "quote_sha256": evidence.digest(user_quote)}
        record_decision(job, action, user_quote, draft=os.path.basename(draft),
                        defaulted=[q["key"] for q in pend])
    ensure_dir(os.path.dirname(draft))
    prof = load_profile(job.data.get("profile"), SKILL_DIR, preferences.project_dir(job))
    tmp = ensure_dir(job.p("草稿", ".tmp"))
    prev_draft = (job.drafts() or [None])[-1]       # 上一版草稿（要在新版文件生成之前取）
    job.raw_snapshot = raw_snapshot
    wr = DocxWriter(work, job, prof, draft)
    if job.read("manual_ops.json", []):
        from .common import file_sha1
        if file_sha1(job.data["target"]["path"]) != tm["sha1"]:
            raise DIError("目标模板在生成草稿期间被修改，请重新核对手工操作的位置。")
    wr.prepare_word_questions(plan["questions"])
    with timed("write"):
        warns = wr.apply(plan["ops"], tmp)
    # 先留下新规划的原样副本（定稿时据此认出人工修改），上一版的人工修改在后面并入
    pristine = ensure_dir(job.p("草稿", ".原始"))
    shutil.copy2(draft, os.path.join(pristine, os.path.basename(draft)))
    # 数字回溯校验：填入的数值和文字回到源文件核对，还原不出来的登记为未核，定稿会拦截；没读全的低置信度引用同样登记
    try:
        from . import verify
        with timed("verify_sources"):
            vres = verify.verify_draft(job, plan, draft)
        unv_src = plan["report"].setdefault("unverified_sources", [])
        unv_src.extend(vres["items"])
        unv_src.extend(verify.full_read_items(verify.full_read_pending(job, plan["report"])))
        plan["report"]["verify_summary"] = verify.summary(vres)
        delivery_check = {"source_checks": vres["checked"], "source_skipped": len(vres["skipped"]),
                          "readback_cells": vres.get("readback", 0), "discrepancies": len(vres["items"]),
                          "notes": vres["notes"]}
    except Exception as e:      # 校验自己出错不能让 draft 崩溃，但要如实登记“没跑完”，不能当作通过
        plan["report"].setdefault("unverified_sources", []).append({"kind": "unread", "key": "verify:crash", "name": "数字回溯校验", "detail": "数字回溯校验没跑完（%s: %s），填入的数值没有回源核对" % (type(e).__name__, e)})
        plan["report"]["verify_summary"] = "数字回溯校验没跑完，不能当作通过"
        delivery_check = {"error": plan["report"]["verify_summary"]}
    # 登记“本批未核”的项（原有值/数据来源未核对/未读取内容）：定稿时逐项检查，没处理的会拦截
    unv = [dict(x) for x in plan["report"].get("stale", [])] + [dict(x) for x in plan["report"].get("unverified_sources", [])]
    # 按推荐默认处理的问题也登记：用户没有正式回答之前，定稿会拦截（默认推荐不等于已确认）
    unv += [{"kind": "default", "key": q["key"], "name": short(q["question"], 40), "effect": q["options"][q.get("recommended", 0)].get("effect"), "detail": "%s → %s" % (short(q["question"], 60), q["options"][q.get("recommended", 0)]["label"])} for q in pend]
    job.data.setdefault("unverified", {})[os.path.basename(draft)] = unv
    effective_defaults = {item['key']: item['effect'] for item in unv if item.get('kind') == 'default'}
    job.data.setdefault('draft_decisions', {})[os.path.basename(draft)] = evidence.decision_digest(job, effective_defaults)
    job.data.setdefault('draft_inputs', {})[os.path.basename(draft)] = job.fingerprint()
    job.save()
    # 重新草稿：并入上一版草稿里审核人的直接修改和人工批注（--discard-edits 则丢弃）；合并出错也要登记，不能当作“没有人工修改”
    try:
        from . import merge as _mg
        mres = _mg.merge_previous(job, prev_draft, draft, discard=discard_edits)
        mlines = _mg.summary_lines(mres, discard_edits)
    except Exception as e:
        mres, mlines = {"items": [], "notes": []}, []
        if prev_draft and not discard_edits:
            msg = "重新草稿时合并上一版的人工修改没跑完（%s: %s），人工修改没有带到新版" % (type(e).__name__, e)
            mres["items"].append({"kind": "unread", "key": "merge:crash", "name": "上一版人工修改", "detail": msg})
            mres["notes"].append("合并：" + msg)
    for it in mres["items"]:
        plan["report"].setdefault("stale" if "cell" in it else "unverified_sources", []).append(it)
        unv.append(dict(it))
    warns = list(warns) + list(getattr(wr.snap, "warns", [])) + list(mres["notes"])
    if mres["items"]:
        job.data["unverified"][os.path.basename(draft)] = unv
        job.save()
    for ln in mlines:
        out("  " + ln)
    write_report(job, plan, draft, ver, warns, used_defaults=bool(pend))
    out("草稿已生成（v%d）：%s" % (ver, draft))
    out("  源文件快照（批注链接指向这里，已用黄色高亮被引用处）：%s" % os.path.join(os.path.dirname(draft), "源文件快照"))
    out("  审核报告：%s" % job.p("审核报告.md"))
    report = plan["report"]
    unread = sum(item.get("kind") == "unread" for item in report.get("unverified_sources", []))
    out("  审核记录 %d 项，未填 %d 项，原有值本批未核 %d 项，未读取 %d 项。"
        % (len(report["fills"]), len(report["unfilled"]), len(report.get("stale", [])), unread))
    for fill in report["fills"][:6]:
        out("    %s ← %s：%s" % (fill.get("target", ""), fill.get("source", ""), short(str(fill.get("value", "")), 64)))
    if pend:
        out("  [注意] %d 个问题按推荐默认处理，已在批注中标“⚠”，请审核时确认" % len(pend))
    unresolved_count = sum(not item.get("spec_question") for item in unv)
    if unresolved_count:
        out("  [未核] %d 项需要确认（原有值本批未核 / 数据来源未核对 / 有未读取内容），已在批注标“⚠”；没处理之前 final 会拦截" % unresolved_count)
    for wmsg in (report["warnings"] + warns)[:10]:
        out("  [警告] " + wmsg)
    if check:
        chk = check_with_office(draft)
        out("  办公软件打开校验：" + chk)
    with timed("check_structure"):
        checked = run_selfcheck(draft)
    nxt("请用户打开草稿审核：每处填写都有批注，点批注里的“▶打开原文”可跳到源文件对应位置",
        "用户可直接改草稿内容，或在草稿里加批注提修改意见；改完保存关闭后告诉助手",
        "有修改意见：%s review \"%s\"  → 按意见处理后再 draft 生成新版本" % (PY, job.dir),
        "用户确认无误：%s final \"%s\"" % (PY, job.dir))


    result = {"status": "draft_ready", "job": job.dir, "draft": draft, "report": job.p("审核报告.md"),
            "snapshots": os.path.join(os.path.dirname(draft), "源文件快照"),
            "template": job.data["target"]["path"], "held_measurements": len(report.get("held_measurements", [])),
            "stale": len(report.get("stale", [])), "unread": unread,
            "fills": len(report["fills"]), "unfilled": len(report["unfilled"]), "unverified": unresolved_count,
            "structure": "incomplete" if checked is None else "failed" if checked.has_errors() else "passed",
            "verification": delivery_check, "review_summary": review_summary(report, unv),
            "warnings": list(dict.fromkeys(report["warnings"] + warns))}
    if not result["warnings"] and not any((unread, unv, result["stale"], result["unfilled"])) and result["structure"] == "passed":
        result["next_action"] = "deliver_draft"
    else:
        refs = review_references(report)
        result.update(next_action="review_then_deliver", review_refs=refs, review_excerpts=review_excerpts(job, refs))
    if result["structure"] != "passed":
        result.update(status="blocked", next_action="resolve_blockers", reason="草稿已保存，结构自检失败或未完成，请核对审核报告。")
    return result


def cmd_review(a):
    from .finalize import comments_info, human_changes
    from .ooxml import Package
    job = Job(a.job)
    ds = job.drafts()
    if not ds:
        raise DIError("还没有草稿")
    draft = ds[-1]
    from .common import lock_file_of
    if lock_file_of(draft):
        out("[注意] 草稿仍在 Word/WPS 中打开，未保存的修改读取不到。请先保存。")
    infos = comments_info(Package(draft))
    tmpl = {(x, y) for x, y in job.data.get("template_comments", [])}
    rev = [c for c in infos if c["author"] != TOOL_AUTHOR and (c["author"], c["text"]) not in tmpl]
    by_id = {c["id"]: c for c in infos}
    out("草稿：%s" % draft)
    reviewer_ids = {c["id"] for c in rev}
    if rev:
        out("审核人批注 %d 条：" % len(rev))
        for c in rev:
            parent = by_id.get(c["parent"])
            context = " 回复 #%s [%s] %s" % (parent["id"], parent["author"], short(parent["text"], 80)) if parent else ""
            status = "（已解决）" if c["done"] else ""
            out("  #%s [%s]%s %s%s" % (c["id"], c["author"], status, short(c["text"], 120), context))
    else:
        out("没有审核人批注。")
    for c in infos:
        if c["done"] and c["id"] not in reviewer_ids:
            out("  #%s [%s]（已解决） %s" % (c["id"], c["author"], short(c["text"], 120)))
    ch = human_changes(os.path.join(os.path.dirname(draft), ".原始", os.path.basename(draft)), draft)
    if ch:
        out("审核人直接修改 %d 处：" % len(ch))
        for c in ch:
            out("  " + c)
    else:
        out("未发现对草稿内容的直接修改。")
    nxt("若批注提出修改：用 set/answer/add-op 调整后 %s draft \"%s\" 生成新版本，核对上版人工修改的合并结果和冲突" % (PY, job.dir),
        "若确认无误：%s final \"%s\" [--reviewer-comments drop|keep]" % (PY, job.dir))


def cmd_final(a):
    from .finalize import finalize
    job = Job(a.job)
    ds = job.drafts()
    if not ds:
        raise DIError("还没有草稿，先运行 draft")
    draft = a.draft or ds[-1]
    res = finalize(job, draft, [f for f in (a.format or "").split(",") if f] or None,
                   reviewer_comments=a.reviewer_comments, strip_template_comments=a.strip_template_comments,
                   accept_unverified=getattr(a, "accept_unverified", False), user_quote=getattr(a, "user_quote", None),
                   template_comments=a.template_comments, require_signed=getattr(a, "require_signed", False))
    out("终稿已生成：")
    for f in res["files"]:
        out("  " + f)
    out("  删除批注 %d 条；保留模板原有批注 %d 条；接受修订 %d 处" % (res["removed_comments"], res["kept_template_comments"],
                                                          res["revisions"]))
    if res["changes"]:
        out("  相对草稿的人工修改 %d 处（已保留）：" % len(res["changes"]))
        for c in res["changes"][:20]:
            out("    " + c)
    for wmsg in res.get("warnings", [])[:8]:
        out("  [提示] " + wmsg)
    for b in res.get("blockers", []):
        out("  [带着问题定稿] " + short(b, 100))
    for e in res["errors"]:
        out("  [转换失败] " + e)
    out("草稿已保留：%s" % draft)
    out("终稿审核报告：%s" % res["report"])
    return {"status": "partial" if res["errors"] else "final_ready", "job": job.dir, "files": res["files"],
            "next_action": "review_export_errors" if res["errors"] else "deliver_final",
            "errors": res["errors"], "warnings": res.get("warnings", []), "blockers": res.get("blockers", []), "report": res["report"]}


def cmd_status(a):
    job = Job(a.job)
    qs = job.read("questions.json", []) or []
    out("任务：%s" % job.dir)
    out("目标：%s" % job.data["target"]["path"])
    out("源文件：" + "；".join("%s=%s" % (s["alias"], s["path"]) for s in job.data["sources"]))
    out("问题：共 %d，未答 %d" % (len(qs), len(pending(qs))))
    out("草稿：" + ("；".join(os.path.basename(x) for x in job.drafts()) or "无"))
    fins = glob.glob(job.p("终稿", "*"))
    out("终稿：" + ("；".join(os.path.basename(x) for x in fins) or "无"))


def cmd_profile(a):
    from .profile import DEFAULT, describe, from_text, load
    if a.text:
        prof = from_text(a.text)
        notes = prof.pop("_notes", [])
        out("识别结果：" + describe(prof))
        if a.save:
            path = preferences.project_dir() / "profiles" / preferences.profile_name(a.save)
            write_json(path, prof)
            out("已保存：%s（建任务时用 --profile %s）" % (path, os.path.splitext(os.path.basename(path))[0]))
        return
    prof = load(a.name, SKILL_DIR)
    out(prof.get("name", "") + "：" + describe(prof))


def cmd_convert(a):
    dst = a.out
    if not dst:
        if not os.path.isfile(a.file) or a.fmt not in office.WORD_FMT.keys() | office.EXCEL_FMT.keys():
            raise DIError("请指定存在的文件和支持的输出格式")
        root = ensure_dir(os.path.abspath("整合输出"))
        folder = tempfile.mkdtemp(prefix="转换_", dir=root)
        dst = os.path.join(folder, os.path.splitext(os.path.basename(a.file))[0] + "." + a.fmt)
    dst = office.convert(a.file, a.fmt, dst=dst)
    out("已转换：%s" % dst)


def cmd_run(a):
    a.plan = True
    return cmd_new(a)


# ----------------------------------------------------------------------- 批量
def cmd_batch(a):
    workers = getattr(a, "jobs", 1)
    if getattr(a, "handoff", False) and a.action not in ("run", "answer"):
        raise DIError("--handoff 适用于 batch run 或 batch answer。")
    if workers < 1 or (workers != 1 and a.action != "draft"):
        raise DIError("--jobs 需要正整数，且仅用于 batch draft。")
    if a.defaults:
        if a.action != "draft":
            raise DIError("--defaults 仅适用于 batch draft")
        require_quote(a.user_quote, "batch draft --defaults")
    a.manifest = a.manifest or getattr(a, "manifest_opt", None)
    if a.action != "init" and not a.manifest:
        raise DIError("请提供清单文件或批量任务 json 路径")
    if a.action == "init":
        rows = []
        if not a.targets:
            raise DIError("批量建清单需要 --targets 指定目标文件或通配符")
        targets = sorted({p for t in split_paths(a.targets) for p in expand_pattern(t) if os.path.isfile(p)})
        if not targets:
            raise DIError("没有找到匹配的目标文件：%s" % a.targets)
        common = split_paths(a.sources)
        for t in targets:
            stem = os.path.splitext(os.path.basename(t))[0]
            srcs = list(common)
            if a.pair:
                srcs += expand_pattern(a.pair.replace("{stem}", stem))
            rows.append({"目标": os.path.abspath(t), "源文件": join_paths([os.path.abspath(s) for s in srcs]),
                         "任务": a.task or "", "格式": a.format or "", "排版": ""})
        path = a.manifest or "批量清单.csv"
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            wr = csv.DictWriter(f, fieldnames=["目标", "源文件", "任务", "格式", "排版"])
            wr.writeheader()
            wr.writerows(rows)
        out("已生成批量清单（可用 Excel 编辑）：%s，共 %d 个目标" % (path, len(rows)))
        nxt("%s batch new \"%s\"" % (PY, path))
        return {"status": "created", "manifest": os.path.abspath(path), "targets": len(rows), "next_action": "batch_run"}
    if a.action in ("new", "run", "plan", "answer") or (a.action == "draft" and a.json):
        from .batch_workflow import run
        if a.action == "draft" and a.json and (a.defaults or workers != 1):
            raise DIError("批量 JSON 草稿逐项核对；并行或默认草稿请使用 batch draft 的文本入口。")
        return run(a)
    if a.json:
        raise DIError("--json 适用于 batch new/run/plan/answer/draft。")
    b = read_json(a.manifest)
    jobs = [Job(d) for d in b["jobs"]]
    if a.action == "draft" and workers > 1:
        from subprocess import TimeoutExpired
        from .child_process import run_child
        from batch_pool import JOB_TIMEOUT
        paths = [os.path.normcase(os.path.realpath(job.dir)) for job in jobs]
        if len(set(paths)) != len(paths):
            raise DIError("批量清单有重复任务目录，无法并行生成草稿。")
        if not jobs:
            return 0
        request = {"directories": [job.dir for job in jobs], "workers": workers,
                   "defaults": a.defaults, "quote": a.user_quote}
        command = [sys.executable, "-B", "-X", "utf8", os.path.join(SKILL_DIR, "scripts", "batch_pool.py")]
        try:
            proc = run_child(command, timeout=(JOB_TIMEOUT + 10) * len(jobs) + 60, worker=True,
                             input_bytes=json.dumps(request, ensure_ascii=False).encode("utf-8"))
        except (OSError, TimeoutExpired) as exc:
            raise DIError("批量草稿调度失败：%s" % exc) from exc
        if proc.returncode:
            raise DIError("批量草稿调度失败：%s" % proc.stderr.decode("utf-8", "replace").strip())
        try:
            results = json.loads(proc.stdout)
        except (UnicodeError, ValueError) as exc:
            raise DIError("没有收到完整的批量草稿结果。") from exc
        if len(results) != len(jobs):
            raise DIError("批量草稿结果数与清单不一致。")
        for job, (_, result) in zip(jobs, results):
            out(os.path.basename(job.dir), result)
        codes = {rc for rc, _ in results}
        if codes - {0, 3, 4}:
            return 2
        return 4 if 4 in codes else 3 if 3 in codes else 0
    failed = False
    for job in jobs:
        out("== %s" % os.path.basename(job.dir))
        try:
            if a.action == "draft":
                cmd_draft(argparse.Namespace(job=job.dir, defaults=a.defaults, check=False, user_quote=a.user_quote))
            elif a.action == "final":
                cmd_final(argparse.Namespace(job=job.dir, draft=None, format=a.format, reviewer_comments=a.reviewer_comments,
                                             strip_template_comments=False, template_comments=a.template_comments,
                                             require_signed=a.require_signed))
        except DIError as e:
            out("  [失败] %s" % e)
            failed = True
    return 2 if failed else 0


# ======================================================================= 参数
def main(argv=None):
    setup_console()
    p = argparse.ArgumentParser(prog="di.py", description="文档整合助手：多格式源文件 -> 目标文档（带批注草稿 -> 审核 -> 终稿）")
    sp = p.add_subparsers(dest="cmd")

    s = sp.add_parser("clean", help="预览解析缓存；加 --apply 清理解析结果、提取媒体与格式转换副本")
    s.add_argument("--out", default="整合输出", help="任务输出根目录")
    s.add_argument("--apply", action="store_true", help="清理列入预览的缓存文件")
    s.set_defaults(fn=cmd_clean)

    s = sp.add_parser("doctor", help="检查运行环境；加 --selfcheck 文件 可对 docx/xlsx 做结构自检")
    s.add_argument("--selfcheck", metavar="文件", help="对指定的 docx/xlsx 做 OOXML 结构自检（不依赖办公软件）")
    s.add_argument("--prune", action="store_true", help="预览旧版私有离线运行时，保留当前、最近和定制版本")
    s.add_argument("--apply", action="store_true", help="清理能够验证且未被使用的旧版运行时")
    s.set_defaults(fn=cmd_doctor)

    for name, fn in (("new", cmd_new), ("run", cmd_run)):
        s = sp.add_parser(name, help="建立任务（run=建立并立即规划）")
        s.add_argument("--target", "-t", required=True, help="目标文件（要填写/整合的文档）")
        s.add_argument("--source", "-s", action="append", required=True, help="源文件，可多次；也可用分号分隔或文件夹/通配符")
        s.add_argument("--task", default="", help="用户原话（任务描述，用于识别取值规则）")
        s.add_argument("--out", help="输出根目录（默认 当前工作目录/整合输出）")
        s.add_argument("--name", help="任务目录名")
        s.add_argument("--profile", help="排版规范名或 json 路径（默认=文档摘抄提示词）")
        s.add_argument("--test-rules", help="产品测试规则 JSON 路径；建立任务时保存一份规则")
        s.add_argument("--format", help="终稿额外格式，如 doc,pdf,md")
        s.add_argument("--plan", action="store_true", help="建立后立即规划")
        if name == "run":
            s.add_argument("--draft", action="store_true", help="直接生成审核草稿；有待确认问题时列出选项并暂停")
        s.add_argument("--lines", type=int, default=40)
        s.add_argument("--depth", type=int, default=2)
        s.set_defaults(fn=fn)

    s = sp.add_parser("outline", help="查看提纲（任务目录或单个文件）")
    s.add_argument("path")
    s.add_argument("--src")
    s.add_argument("--section")
    s.add_argument("--lines", type=int, default=80)
    s.add_argument("--depth", type=int, default=3)
    s.set_defaults(fn=cmd_outline)

    s = sp.add_parser("show", help="一次查看多个章节、表格或 PDF 页，如 S1:H5 S1:T3 S2:page1")
    s.add_argument("job")
    s.add_argument("ref", nargs="+", help="一个或多个来源引用；相同引用只显示一次")
    s.add_argument("--full", action="store_true")
    s.add_argument("--rows", help="表格行范围，如 0-10")
    s.add_argument("--rows-max", type=int, default=40)
    s.set_defaults(fn=cmd_show)

    s = sp.add_parser("search", help="在源文件中检索关键词")
    s.add_argument("job")
    s.add_argument("query")
    s.add_argument("--src")
    s.add_argument("--top", type=int, default=8)
    s.add_argument("--target", action="store_true", help="同时检索目标文件")
    s.set_defaults(fn=cmd_search)

    s = sp.add_parser("figure", help="导出图片（EMF/WMF 转 PNG）供查看识别")
    s.add_argument("job")
    s.add_argument("ref", help="如 S1:F2")
    s.set_defaults(fn=cmd_figure)

    s = sp.add_parser("plan", help="自动规划并列出待确认问题")
    s.add_argument("job")
    s.set_defaults(fn=cmd_plan)

    s = sp.add_parser("questions", help="列出待确认问题")
    s.add_argument("job")
    s.add_argument("--all", action="store_true")
    s.add_argument("--ask-json", action="store_true", help="输出 AskUserQuestion 可用的 JSON")
    s.add_argument("--csv", metavar="路径", help="导出供审核人填写的待核清单")
    s.set_defaults(fn=cmd_questions)

    s = sp.add_parser("answer", help="记录用户回答，如 Q1=1 Q2=1,3 Q3=\"文字\"")
    s.add_argument("job")
    s.add_argument("items", nargs="*")
    inputs = s.add_mutually_exclusive_group()
    inputs.add_argument("--from-sheet", metavar="CSV", help="导入当前任务的待核清单")
    inputs.add_argument("--evidence", metavar="文件", help="从测试程序清单读取问题所需证据")
    s.add_argument("--reviewer", help="提供回答的审核人署名")
    s.add_argument("--remember", action="store_true", help="把数据对应类回答记为长期规则")
    s.add_argument("--user-quote", help="用户确认本次选择的原话（来源核对、样品对应和记住规则时必填）")
    s.set_defaults(fn=cmd_answer)

    s = sp.add_parser("set", help="人工指定某单元格/段落的内容")
    s.add_argument("job")
    s.add_argument("ref", help="目标位置ID，如 T0.R1.C1 或 P5")
    s.add_argument("value")
    s.add_argument("--runs", action="store_true", help="value 为 runs JSON（含上下标）")
    s.add_argument("--src", help="来源引用，如 S1:P3（会生成带链接批注）")
    s.add_argument("--note", help="批注说明")
    s.set_defaults(fn=cmd_set)

    s = sp.add_parser("add-section", help="把源章节加入目标章节，如 --to \"2、性能指标\" --src S1:H5")
    s.add_argument("job")
    s.add_argument("--to", required=True, help="目标章节标题（可部分）或段落ID如 P3")
    s.add_argument("--src", required=True, help="源章节ID，如 S1:H5")
    s.add_argument("--title", help="小标题文字（默认用源章节标题）")
    s.add_argument("--replace", action="store_true", help="替换该章节已自动摘抄的内容（默认追加）")
    s.add_argument("--no-subheading", action="store_true", help="不加（n）小标题")
    s.set_defaults(fn=cmd_add_section)

    s = sp.add_parser("find", help="在指定目录或当前工作目录查找文件，如 find DEMO-100 详细规范")
    s.add_argument("query", nargs="+")
    s.add_argument("--dir", action="append")
    s.add_argument("--exact", action="store_true", help="在指定目录或当前工作区递归列出所有同名文件，供用户确认")
    s.add_argument("--depth", type=int, help="搜索深度，模糊查找默认 2，精确查找默认不限")
    s.add_argument("--top", type=int, help="最多显示几项，精确查找默认显示全部")
    s.set_defaults(fn=cmd_find)

    s = sp.add_parser("add-op", help="追加手工操作（JSON 或 @文件）")
    s.add_argument("job")
    s.add_argument("op")
    s.add_argument("--replace", action="store_true", help="以核对过的完整操作清单替换旧清单，重新绑定当前模板")
    s.set_defaults(fn=cmd_add_op)

    s = sp.add_parser("draft", help="生成带批注的草稿")
    s.add_argument("job")
    s.add_argument("--defaults", action="store_true", help="未回答的问题按推荐处理（批注中标⚠）")
    s.add_argument("--check", action="store_true", help="用办公软件打开校验")
    s.add_argument("--discard-edits", action="store_true", help="丢弃上一版草稿里的人工修改和人工批注，纯按新规划生成")
    s.add_argument("--user-quote", help="用户同意按推荐出草稿或丢弃人工修改的原话")
    s.add_argument("--raw-snapshot", action="store_true", help="源文件快照保留原样（默认净化：去掉原批注、作者、修订、隐藏文字、customXml、外部链接）")
    s.set_defaults(fn=cmd_draft)

    s = sp.add_parser("review", help="读取审核人对草稿的批注与修改")
    s.add_argument("job")
    s.set_defaults(fn=cmd_review)

    s = sp.add_parser("confirm", help="由审核人在交互终端逐项确认受保护决定")
    s.add_argument("job")
    s.set_defaults(fn=cmd_confirm)

    s = sp.add_parser("final", help="审核确认后生成终稿")
    s.add_argument("job")
    s.add_argument("--require-signed", action="store_true", help="受保护决定必须有 Word 署名回复或终端交互确认")
    s.add_argument("--draft", help="指定草稿文件（默认最新版本）")
    s.add_argument("--format", help="额外输出格式：doc,pdf,md")
    s.add_argument("--reviewer-comments", choices=["ask", "drop", "keep"], default="ask")
    s.add_argument("--strip-template-comments", action="store_true", help="同时删除模板原有批注")
    s.add_argument("--template-comments", choices=["keep", "drop"], default="ask", help="模板原有批注的去留")
    s.add_argument("--accept-unverified", action="store_true", help="明知有“本批未核”项仍要定稿（登记到 终稿遗留项.md）")
    s.add_argument("--user-quote", help="用户同意带着未核项定稿的原话")
    s.set_defaults(fn=cmd_final)

    s = sp.add_parser("status", help="查看任务状态")
    s.add_argument("job")
    s.set_defaults(fn=cmd_status)

    s = sp.add_parser("profile", help="查看/生成排版规范，如 --text \"正文仿宋三号…\" --save 公文")
    s.add_argument("--name")
    s.add_argument("--text")
    s.add_argument("--save")
    s.set_defaults(fn=cmd_profile)

    s = sp.add_parser("convert", help="格式转换（借助已安装的办公软件）")
    s.add_argument("file")
    s.add_argument("fmt")
    s.add_argument("--out")
    s.set_defaults(fn=cmd_convert)

    from .handoff import cmd_deliver
    s = sp.add_parser("deliver", help="在本地终端核对封存清单并交付审核草稿")
    s.add_argument("manifest", help="交接目录中的 handoff.json")
    s.set_defaults(fn=cmd_deliver)

    s = sp.add_parser("batch", help="批量：init/new/plan/answer/draft/final")
    s.add_argument("action", choices=["init", "new", "run", "plan", "answer", "draft", "final"])
    s.add_argument("manifest", nargs="?", help="清单csv/json（init/new）或批量任务json（其余）")
    s.add_argument("items", nargs="*")
    s.add_argument("--manifest", dest="manifest_opt", help="同位置参数 manifest")
    s.add_argument("--targets", help="init：目标文件通配，如 \"记录/*.doc\"")
    s.add_argument("--test-rules", help="batch new：未单独指定测试规则的任务使用此 JSON 文件")
    s.add_argument("--sources", help="init：公共源文件，分号分隔")
    s.add_argument("--pair", help="init：按目标名配对的源文件，如 \"数据/{stem}.xlsx\"")
    s.add_argument("--task")
    s.add_argument("--format")
    s.add_argument("--out")
    s.add_argument("--defaults", action="store_true")
    s.add_argument("--user-quote", help="用户确认本次批量选择的原话（记入每个受影响的任务）")
    s.add_argument("--template-comments", choices=["keep", "drop"], default="ask", help="批量定稿时模板原有批注的去留")
    s.add_argument("--reviewer-comments", choices=["ask", "drop", "keep"], default="ask")
    s.add_argument("--jobs", type=int, default=1, help="batch draft 同时处理的独立任务数，默认 1")
    s.add_argument("--require-signed", action="store_true", help="批量定稿要求受保护决定有署名确认")
    s.add_argument("--draft", action="store_true", help="为本批已答齐的项目生成草稿")
    s.add_argument("--handoff", action="store_true", help="答齐后封存本批，交由审核人在独立终端启动交付")
    s.add_argument("--evidence", help="批量回答的共同程序清单，或 J1、J2 到独立证据路径的 JSON 目录")
    s.set_defaults(fn=cmd_batch)

    for command in ("run", "answer", "draft", "final", "batch"):
        sp.choices[command].add_argument("--json", action="store_true", help="输出单个 JSON；待答、拦截按 status 区分")
        sp.choices[command].add_argument("--strict-exit", action="store_true", help="配合 --json，待答返回 3、拦截返回 4")
    sp.choices["answer"].add_argument("--draft", action="store_true", help="用本轮规划生成草稿；仍有问题时返回选项")
    a = p.parse_args(argv)
    if not getattr(a, "fn", None):
        p.print_help()
        return 1
    machine = getattr(a, "json", False)
    try:
        if machine:
            with open(os.devnull, "w", encoding="utf-8") as sink, contextlib.redirect_stdout(sink):
                result = a.fn(a)
            out(json.dumps(result, ensure_ascii=False))
            return {"needs_answers": 3 if a.strict_exit else 0, "blocked": 4 if a.strict_exit else 0, "partial": 2}.get(result["status"], 0)
        rc = a.fn(a)      # doctor --selfcheck 等命令用返回值表示结果
        return rc if isinstance(rc, int) else 0
    except DIError as e:
        if machine:
            from .common import ReviewBlocked
            blocked = isinstance(e, ReviewBlocked)
            out(json.dumps({"status": "blocked" if blocked else "error", "next_action": "resolve_blockers" if blocked else "correct_input", "reason": str(e)}, ensure_ascii=False))
            return (4 if a.strict_exit else 0) if blocked else 2
        out("错误：%s" % e)
        return 2
    except OSError as e:
        out(json.dumps({"status": "error", "reason": str(e), "next_action": "check_files"}, ensure_ascii=False) if machine else "文件访问失败：%s" % e)
        return 2
