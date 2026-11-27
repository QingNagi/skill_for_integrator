# -*- coding: utf-8 -*-
"""命令行入口。所有命令输出精简结果 + “下一步”提示，便于大/小模型按步骤执行。"""
from __future__ import annotations

import argparse
import csv
import datetime
import glob
import json
import os
import shutil
import sys

from . import office
from .common import DIError, TOOL_AUTHOR, ensure_dir, out, read_json, setup_console, short, write_json
from .content import Model, load_model, outline, parse_ref, render_block, render_sheet, search
from .job import Job

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY = "python \"%s\"" % os.path.join(SKILL_DIR, "scripts", "di.py")
_portable_python = os.path.join(SKILL_DIR, "runtime", "python", "python.exe")
if os.name == "nt" and os.path.isfile(_portable_python):
    PY = '& "%s" "%s"' % (_portable_python, os.path.join(SKILL_DIR, "scripts", "di.py"))


def nxt(*steps):
    out("\n下一步：")
    for s in steps:
        out("  - " + s)


def load_aliases():
    a = read_json(os.path.join(SKILL_DIR, "rules", "aliases.json"), {}) or {}
    a = {k: v for k, v in a.items() if not k.startswith("_")}
    return a


def load_learned():
    d = read_json(os.path.join(SKILL_DIR, "rules", "learned.json"), {}) or {}
    return d.get("answers", {})


# ======================================================================= 规划
def do_plan(job, quiet=False, extra_answers=None):
    """extra_answers：仅本次使用、不保存的回答（--defaults 时的推荐项）。"""
    from .planner import Planner
    answers = job.read("answers.json", {}) or {}
    manual = job.read("manual_ops.json", []) or []
    fp = job.fingerprint()
    stale_ans = [k for k, v in answers.items() if isinstance(v, dict) and v.get("fp") and v["fp"] != fp]
    answers = {k: v for k, v in answers.items() if k not in stale_ans}
    merged = dict(answers)
    merged.update(extra_answers or {})
    learned = load_learned()
    pl = Planner(job, answers=merged, learned={}, aliases=load_aliases(), manual_ops=manual,
                 rules_override=job.data.get("rules", {}))
    plan = pl.run()
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
    n = max([int(q["id"][1:]) for q in qs if q["id"][1:].isdigit()] + [0])
    for q in plan["questions"]:
        if q["key"] not in used:
            n += 1
            qs.append(dict(q, id="Q%d" % n))
    for q in qs:
        q["answered"] = q["key"] in answers
        q["learned"] = q["key"] in learned and q["key"] not in answers
        lk = learned.get(q["key"]) if q["learned"] else None
        if lk:      # 记住的上次选择只作推荐项，本次仍需确认（文件/批次可能不同）
            for i, o in enumerate(q["options"]):
                if o["label"] == lk.get("label"):
                    q["recommended"] = i
                    break
        q["defaulted"] = q["key"] in (extra_answers or {})
    plan["questions"] = qs
    plan["stale_answers"] = stale_ans
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
             "回答：`%s answer \"%s\" Q1=1 Q2=1,3`（数字=选项序号，多选用逗号；自由回答：Q3=\"文字\"）" % (PY, job.dir), ""]
    for q in qs:
        flag = "（已回答）" if q.get("answered") else ("（已按记住的规则）" if q.get("learned") else "")
        lines.append("## %s [%s]%s %s" % (q["id"], q["topic"], flag, "（可多选）" if q.get("multi") else ""))
        lines.append(q["question"])
        for i, o in enumerate(q["options"]):
            lines.append("  %d) %s%s" % (i + 1, o["label"], "  ← 推荐" if i == q.get("recommended", 0) else ""))
        lines.append("")
    with open(job.p("questions.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def pending(qs):
    return [q for q in qs if not q.get("answered")]


def print_plan(job, plan):
    out("规划完成：%s" % job.dir)
    if plan.get("stale_answers"):
        out("  [注意] 目标/源文件在回答之后被改过：%d 个旧回答已失效，需要重新确认（%s）" % (len(plan["stale_answers"]), "、".join(short(k, 24) for k in plan["stale_answers"][:5])))
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
        for q in pend:
            out("  %s [%s] %s" % (q["id"], q["topic"], short(q["question"], 150)))
            for i, o in enumerate(q["options"]):
                out("      %d) %s%s" % (i + 1, short(o["label"], 60), "  ← 推荐" if i == q.get("recommended", 0) else ""))
        nxt("把上面的问题交给用户选择（Claude Code 可用 AskUserQuestion；也可直接文字询问），不要替用户决定",
            "记录回答：%s answer \"%s\" Q1=1 Q2=2 ...（多选 Q3=1,2；自由文本 Q4=\"...\"）" % (PY, job.dir),
            "全部回答后：%s draft \"%s\"" % (PY, job.dir))
    else:
        nxt("无待确认问题，生成草稿：%s draft \"%s\"" % (PY, job.dir))


# ======================================================================= 命令实现
def cmd_doctor(a):
    from . import selfcheck
    for ln in selfcheck.environment_lines():
        out(ln)
    try:
        from . import _bootstrap as bs
        if bs.use_pymupdf() is None and bs.use_pypdf() is None:
            out("如需读取 PDF：pip install pymupdf    （内网可先在外网 pip download pymupdf 后拷贝 whl 安装）")
    except Exception:
        pass
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
        srcs += [x for x in s.split(";") if x.strip()]
    return srcs


def cmd_new(a):
    job = Job.create(a.target, _src_list(a), task=a.task or "", out_root=a.out, name=a.name, profile=a.profile,
                     formats=[f for f in (a.format or "").split(",") if f])
    out("已建立任务：%s" % job.dir)
    out("源文件：" + "；".join("%s=%s" % (s["alias"], os.path.basename(s["path"])) for s in job.data["sources"]))
    out("")
    for al, md in job.source_models().items():
        out(outline(md, al, max_lines=a.lines, max_depth=a.depth))
    from .target import analyze, summary
    out("")
    out(summary(analyze(job.target_model())))
    if a.plan:
        out("")
        do_plan(job)
    else:
        nxt("自动规划：%s plan \"%s\"" % (PY, job.dir))


def _job_or_file(path):
    if os.path.isdir(path):
        return Job(path), None
    return None, path


def cmd_outline(a):
    job, f = _job_or_file(a.path)
    if job:
        models = job.all_models() if not a.src else {a.src: job.model(a.src)}
        for al, md in models.items():
            if al == "T" and not a.src:
                from .target import analyze, summary
                out(summary(analyze(md)))
            else:
                out(outline(md, al, max_lines=a.lines, max_depth=a.depth, section=a.section))
    else:
        cache = os.path.join(os.path.dirname(os.path.abspath(f)), "整合输出", "_缓存")
        out(outline(Model(load_model(f, cache), "F"), "F", max_lines=a.lines, max_depth=a.depth, section=a.section))


def cmd_show(a):
    job = Job(a.job)
    alias, ref = parse_ref(a.ref)
    md = job.model(alias)
    if md.m["type"] == "xlsx":
        out(render_sheet(md, ref if ref != "*" else None, max_rows=a.rows_max))
    else:
        if a.rows and ref.startswith("T"):
            from .content import render_table
            out(render_table(md, ref, rows=a.rows, full=a.full))
        else:
            out(render_block(md, ref, full=a.full))


def cmd_search(a):
    job = Job(a.job)
    models = job.source_models() if not a.src else {a.src: job.model(a.src)}
    if a.target:
        models["T"] = job.target_model()
    res = search(models, a.query, top=a.top)
    if not res:
        out("未找到与“%s”相关的内容。可换个说法，或用 outline 查看提纲。" % a.query)
    for sc, al, bid, desc in res:
        out("%.2f  %s:%s  %s" % (sc, al, bid, desc))


def cmd_figure(a):
    job = Job(a.job)
    alias, ref = parse_ref(a.ref)
    md = job.model(alias)
    f = md.by.get(ref)
    if not f or "imgs" not in f:
        raise DIError("%s 不是图（F编号）" % a.ref)
    imgs = md.m.get("images", {})
    outdir = ensure_dir(job.p("图片导出"))
    pairs, outs = [], []
    for iid in f["imgs"]:
        info = imgs.get(iid, {})
        fp = info.get("file")
        if not fp:
            continue
        if info.get("ext") in ("emf", "wmf"):
            dst = os.path.join(outdir, "%s_%s_%s.png" % (alias, ref, iid))
            pairs.append((fp, dst))
            outs.append(dst)
        else:
            dst = os.path.join(outdir, "%s_%s_%s.%s" % (alias, ref, iid, info.get("ext")))
            shutil.copy2(fp, dst)
            outs.append(dst)
        if info.get("text"):
            out("%s 图中文字（矢量提取）：%s" % (iid, short(info["text"].replace("\n", " / "), 300)))
    if pairs:
        ok = office.metafile_to_png(pairs)
        for s, d in pairs:
            if d not in ok:
                out("  [注意] %s 转 PNG 失败（可直接打开原图 %s）" % (os.path.basename(d), s))
    out("图片已导出：\n  " + "\n  ".join(outs))
    out("（有视觉能力的模型可直接查看这些图片识别内容；矢量图的文字已在上方提取）")


def cmd_plan(a):
    do_plan(Job(a.job))


def cmd_questions(a):
    job = Job(a.job)
    qs = job.read("questions.json") or do_plan(job, quiet=True)["questions"]
    pend = pending(qs) if not a.all else qs
    if a.ask_json:
        batch = []
        for q in pend:
            opts = list(enumerate(q["options"]))
            rec = q.get("recommended", 0)
            opts.sort(key=lambda x: 0 if x[0] == rec else 1)
            opts = opts[:4]
            if len(opts) < 2:
                opts.append((len(q["options"]), {"label": "其他（我来说明）"}))
            batch.append({"question": "%s %s" % (q["id"], q["question"])[:400], "header": q["topic"][:12],
                          "multiSelect": bool(q.get("multi")),
                          "options": [{"label": short(o["label"], 40) + ("（推荐）" if i == rec else ""),
                                       "description": "选项%d" % (i + 1)} for i, o in opts]})
        groups = [batch[i:i + 4] for i in range(0, len(batch), 4)]
        out(json.dumps(groups, ensure_ascii=False, indent=1))
        out("\n提示：AskUserQuestion 每次最多4题；回答后用 description 中的“选项N”记录：answer Qx=N")
        return
    if not pend:
        out("没有待确认的问题。")
        return
    out(open(job.p("questions.md"), encoding="utf-8").read())


def cmd_answer(a):
    job = Job(a.job)
    qs = job.read("questions.json") or do_plan(job, quiet=True)["questions"]
    byid = {q["id"]: q for q in qs}
    answers = job.read("answers.json", {}) or {}
    learned_path = os.path.join(SKILL_DIR, "rules", "learned.json")
    learned = read_json(learned_path, {"answers": {}}) or {"answers": {}}
    for item in a.items:
        if "=" not in item:
            raise DIError("格式应为 Q1=2，收到：%s" % item)
        qid, val = item.split("=", 1)
        qid = qid.strip().upper()
        q = byid.get(qid)
        if not q:
            raise DIError("没有问题 %s（先运行 plan 或 questions 查看）" % qid)
        val = val.strip().strip('"').strip("“”")
        rec = {"label": "", "answered_at": datetime.datetime.now().isoformat(timespec="seconds"), "fp": job.fingerprint()}
        if all(x.strip().isdigit() for x in val.split(",")) and val:
            idx = [int(x) - 1 for x in val.split(",")]
            for i in idx:
                if not 0 <= i < len(q["options"]):
                    raise DIError("%s 没有选项 %d" % (qid, i + 1))
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
        answers[q["key"]] = rec
        out("%s ← %s" % (qid, short(rec["label"], 80)))
        if a.remember and q["key"].startswith("td:") and not rec["effect"].get("free") if isinstance(rec["effect"], dict) else False:
            learned["answers"][q["key"]] = {"effect": rec["effect"], "label": rec["label"]}
    job.write("answers.json", answers)
    if a.remember:
        write_json(learned_path, learned)
        out("已记住数据对应规则（rules/learned.json），以后遇到同名测试项不再询问。")
    plan = do_plan(job, quiet=True)
    pend = pending(plan["questions"])
    free = [q for q in plan["questions"] if isinstance(answers.get(q["key"], {}).get("effect"), dict)
            and answers[q["key"]]["effect"].get("free")]
    if free:
        out("\n以下为自由文本回答，需要你（模型）据此手工调整（set / add-op 命令）：")
        for q in free:
            out("  %s %s → “%s”" % (q["id"], short(q["question"], 60), answers[q["key"]]["text"]))
    if pend:
        out("\n仍有 %d 个问题未回答：%s" % (len(pend), "、".join(q["id"] for q in pend)))
        nxt("继续回答：%s answer \"%s\" ..." % (PY, job.dir))
    else:
        nxt("生成草稿：%s draft \"%s\"" % (PY, job.dir))


def cmd_set(a):
    job = Job(a.job)
    ops = job.read("manual_ops.json", []) or []
    op = {"op": "set_text" if not a.runs else "set_cell", "cell": a.ref}
    if a.runs:
        op["runs"] = json.loads(a.value)
    else:
        op["text"] = a.value
    ops.append(op)
    if a.note or a.src:
        links = []
        if a.src:
            al, ref = parse_ref(a.src)
            links.append({"label": "打开原文", "alias": al, "ref": ref})
        ops.append({"op": "comment", "anchor": a.ref, "text": "【人工指定】" + (a.note or ""), "links": links})
    job.write("manual_ops.json", ops)
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
    ops = job.read("manual_ops.json", []) or []
    ops.append({"op": "add_section", "anchor": sec["start"], "alias": alias, "sec": ref, "title": a.title,
                "mode": "replace" if a.replace else "append", "subheading": not a.no_subheading})
    job.write("manual_ops.json", ops)
    out("已记录：目标“%s” %s 源 %s“%s”" % (sec["title"], "替换为" if a.replace else "追加", a.src, short(md.by[ref]["title"], 30)))
    nxt("重新生成草稿：%s draft \"%s\"" % (PY, job.dir))


def cmd_find(a):
    """按用户描述中的文件名（可不完整）在当前目录、桌面及指定目录中查找文件。"""
    from .common import contains_score, sim
    from .job import DOC_TYPES
    home = os.path.expanduser("~")
    dirs = [os.getcwd(), os.path.join(home, "Desktop"), os.path.join(home, "桌面"),
            os.path.join(home, "OneDrive", "Desktop"), os.path.join(home, "OneDrive", "桌面")] + (a.dir or [])
    seen, res = set(), []
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for root, subdirs, files in os.walk(d):
            subdirs[:] = [x for x in subdirs if x not in ("整合输出", "_缓存", ".git", "node_modules", ".claude")]
            if root.count(os.sep) - d.count(os.sep) > a.depth:
                subdirs[:] = []
            for f in files:
                p = os.path.join(root, f)
                if f.startswith("~$") or os.path.splitext(f)[1].lower() not in DOC_TYPES or p in seen:
                    continue
                seen.add(p)
                stem = os.path.splitext(f)[0]
                sc = 0.0
                for q in a.query:
                    qs, qe = os.path.splitext(q) if os.path.splitext(q)[1].lower() in DOC_TYPES else (q, "")
                    if f.lower() == q.lower() or (qe and stem.lower() == qs.lower()):
                        sc = 1.0
                        break
                    sc = max(sc, contains_score(qs, stem) * 0.7 + sim(qs, stem) * 0.3)
                if sc >= 0.35:
                    res.append((sc, p))
    res.sort(key=lambda x: -x[0])
    if not res:
        out("没找到匹配文件。请让用户提供完整路径。")
    for sc, p in res[:a.top]:
        out("%.2f  %s" % (sc, p))
    if len(res) > 1 and res[0][0] - res[1][0] < 0.1:
        out("（前几个匹配度接近：如不确定用哪个，请向用户确认）")


def cmd_add_op(a):
    job = Job(a.job)
    ops = job.read("manual_ops.json", []) or []
    data = json.loads(open(a.op[1:], encoding="utf-8").read()) if a.op.startswith("@") else json.loads(a.op)
    ops += data if isinstance(data, list) else [data]
    job.write("manual_ops.json", ops)
    out("已追加 %d 个手工操作（manual_ops.json）" % (len(data) if isinstance(data, list) else 1))


def cmd_draft(a):
    from .profile import load as load_profile
    from .write_docx import DocxWriter
    job = Job(a.job)
    plan = do_plan(job, quiet=True)
    pend = pending(plan["questions"])
    if pend and not a.defaults:
        print_plan(job, plan)
        out("\n还有 %d 个问题未回答，暂不生成草稿。若用户同意全部按推荐处理，可加 --defaults。" % len(pend))
        return
    if pend:
        extra = {}
        for _ in range(4):      # 按推荐回答后可能出现新问题，迭代至稳定
            extra.update(default_answers([q for q in pend if q["key"] not in extra]))
            plan = do_plan(job, quiet=True, extra_answers=extra)
            pend = [q for q in pending(plan["questions"]) if q["key"] not in extra]
            if not pend:
                break
        pend = [q for q in plan["questions"] if q.get("defaulted")]
    tm = job.target_model().m
    work = tm["work"]
    if os.path.splitext(work)[1].lower() != ".docx":
        raise DIError("目前草稿只能写入 Word 目标（docx/doc/wps/rtf）。Excel 目标暂不支持写入（没有 xlsx-fill 这个命令），Excel 目前只能作源文件。")
    draft, ver = job.next_draft()
    ensure_dir(os.path.dirname(draft))
    prof = load_profile(job.data.get("profile"), SKILL_DIR)
    tmp = ensure_dir(job.p("草稿", ".tmp"))
    prev_draft = (job.drafts() or [None])[-1]       # 上一版草稿（要在新版文件生成之前取）
    job.raw_snapshot = bool(getattr(a, "raw_snapshot", False))
    wr = DocxWriter(work, job, prof, draft)
    warns = wr.apply(plan["ops"], tmp)
    # 先留下新规划的原样副本（定稿时据此认出人工修改），上一版的人工修改在后面并入
    pristine = ensure_dir(job.p("草稿", ".原始"))
    shutil.copy2(draft, os.path.join(pristine, os.path.basename(draft)))
    # 数字回溯校验：填入的数值和文字回到源文件核对，还原不出来的登记为未核，定稿会拦截；没读全的低置信度引用同样登记
    try:
        from . import verify
        vres = verify.verify_draft(job, plan, draft)
        unv_src = plan["report"].setdefault("unverified_sources", [])
        unv_src.extend(vres["items"])
        unv_src.extend(verify.full_read_items(verify.full_read_pending(job, plan["report"])))
        plan["report"]["verify_summary"] = verify.summary(vres)
    except Exception as e:      # 校验自己出错不能让 draft 崩溃，但要如实登记“没跑完”，不能当作通过
        plan["report"].setdefault("unverified_sources", []).append({"kind": "unread", "key": "verify:crash", "name": "数字回溯校验", "detail": "数字回溯校验没跑完（%s: %s），填入的数值没有回源核对" % (type(e).__name__, e)})
        plan["report"]["verify_summary"] = "数字回溯校验没跑完，不能当作通过"
    # 登记“本批未核”的项（原有值/数据来源未核对/未读取内容）：定稿时逐项检查，没处理的会拦截
    unv = [dict(x) for x in plan["report"].get("stale", [])] + [dict(x) for x in plan["report"].get("unverified_sources", [])]
    # 按推荐默认处理的问题也登记：用户没有正式回答之前，定稿会拦截（默认推荐不等于已确认）
    unv += [{"kind": "default", "key": q["key"], "name": short(q["question"], 40), "effect": q["options"][q.get("recommended", 0)].get("effect"), "detail": "%s → %s" % (short(q["question"], 60), q["options"][q.get("recommended", 0)]["label"])} for q in pend]
    job.data.setdefault("unverified", {})[os.path.basename(draft)] = unv
    job.save()
    # 重新草稿：并入上一版草稿里审核人的直接修改和人工批注（--discard-edits 则丢弃）；合并出错也要登记，不能当作“没有人工修改”
    discard = bool(getattr(a, "discard_edits", False))
    try:
        from . import merge as _mg
        mres = _mg.merge_previous(job, prev_draft, draft, discard=discard)
        mlines = _mg.summary_lines(mres, discard)
    except Exception as e:
        mres, mlines = {"items": [], "notes": []}, []
        if prev_draft and not discard:
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
    ncom = sum(1 for o in plan["ops"] if o["op"] in ("comment", "insert_section"))
    out("草稿已生成（v%d）：%s" % (ver, draft))
    out("  源文件快照（批注链接指向这里，已用黄色高亮被引用处）：%s" % os.path.join(os.path.dirname(draft), "源文件快照"))
    out("  审核报告：%s" % job.p("审核报告.md"))
    if pend:
        out("  [注意] %d 个问题按推荐默认处理，已在批注中标“⚠”，请审核时确认" % len(pend))
    if unv:
        out("  [未核] %d 项需要确认（原有值本批未核 / 数据来源未核对 / 有未读取内容），已在批注标“⚠”；没处理之前 final 会拦截" % len(unv))
    for wmsg in warns[:10]:
        out("  [警告] " + wmsg)
    if a.check:
        chk = check_with_office(draft)
        out("  办公软件打开校验：" + chk)
    run_selfcheck(draft)
    nxt("请用户打开草稿审核：每处填写都有批注，点批注里的“▶打开原文”可跳到源文件对应位置",
        "用户可直接改草稿内容，或在草稿里加批注提修改意见；改完保存关闭后告诉助手",
        "有修改意见：%s review \"%s\"  → 按意见处理后再 draft 生成新版本" % (PY, job.dir),
        "用户确认无误：%s final \"%s\"" % (PY, job.dir))


def run_selfcheck(path):
    '''草稿结构自检：只提示，不拦截。自检本身出错也不能让 draft 崩溃，但要如实说“没跑完”，不能当作通过。'''
    try:
        from . import selfcheck
        rep = selfcheck.check_file(path)
    except Exception as e:
        out('  [结构自检] 没跑完（%s: %s），不能当作通过' % (type(e).__name__, e))
        return None
    for ln in selfcheck.render(rep):
        out('  ' + ln)
    if rep.has_errors():
        out('  [注意] 草稿有结构问题：在 Word/WPS 里可能打不开或批注链接失效；定稿时严重项会拦截')
    return rep


def check_with_office(path):
    d = office.detect()
    if not (d["word"] or d["wps_word"] or d["soffice"]):
        return "未检测到办公软件，跳过"
    tmp = os.path.join(os.path.dirname(path), ".tmp", "check.pdf")
    try:
        res = office.convert_many([(path, tmp, "pdf")])
        return "通过（可正常打开并导出）" if not res.get(tmp) else "失败：%s" % res.get(tmp)
    except Exception as e:
        return "失败：%s" % e


def write_report(job, plan, draft, ver, warns, used_defaults=False):
    rep = plan["report"]
    answers = job.read("answers.json", {}) or {}
    L = ["# 审核报告：%s（草稿 v%d）" % (os.path.basename(job.data["target"]["path"]), ver), "",
         "- 生成时间：%s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
         "- 草稿：%s" % draft, "- 任务描述：%s" % (job.data.get("task") or "（无）"),
         "- 源文件：" + "；".join("%s=%s" % (s["alias"], s["path"]) for s in job.data["sources"]), "",
         "## 审核方法", "1. 打开草稿，每处填写的内容都带批注；批注中的“▶打开原文/▶1#”等链接可直接跳到源文件快照中的对应位置（已黄色高亮）。",
         "2. 带“⚠”的批注表示需要重点确认（近似匹配、按推荐默认处理、数值可疑等）。",
         "3. 可直接修改草稿内容，或在草稿中添加批注写修改意见；保存并关闭后告诉助手“确认定稿”或“按批注修改”。", "",
         "## 规划摘要"] + ["- " + s for s in plan["summary"]] + [""]
    if answers:
        L += ["## 已确认的问题"]
        for q in plan["questions"]:
            if q["key"] in answers:
                L.append("- %s → %s" % (short(q["question"], 80), answers[q["key"]].get("label")))
        L.append("")
    if used_defaults:
        L += ["## 按推荐默认处理（待确认）"] + ["- %s → %s" % (short(q["question"], 80), q["options"][q.get("recommended", 0)]["label"])
                                       for q in pending(plan["questions"])] + [""]
    L += ["## 逐项来源（%d 项）" % len(rep["fills"]), "", "| 位置 | 填入值 | 来源 | 方式/规则 | 原始值 | 检查 |", "|---|---|---|---|---|---|"]
    for f in rep["fills"]:
        L.append("| %s | %s | %s | %s | %s | %s |" % tuple(str(f.get(k, "")).replace("|", "\\|").replace("\n", " ")
                                                       for k in ("target", "value", "source", "how", "raw", "check")))
    stale = rep.get("stale", [])
    unv_src = rep.get("unverified_sources", [])
    if stale or unv_src:
        L += ["", "## ⚠ 本批未核项（定稿前必须处理）", ""]
        L += ["- 原有值，本批未核：%s %s%s=“%s”（%s）" % (x.get("param", ""), x.get("sample", ""), "#" if x.get("sample") else "", short(x.get("text", ""), 24), x.get("cell", "")) for x in stale]
        L += ["- " + (x.get("detail") or x.get("name", "")) for x in unv_src]
        L += ["", "处理方式：清除或改正这些格后保存；确认旧值仍适用请 final --accept-unverified（会登记到 终稿遗留项.md）；来源/未读取项请回答对应问题。", ""]
    L += ["", "## 未填项（需人工补充）"] + ["- " + x for x in rep["unfilled"]] + [""]
    if rep.get("filler"):
        L += ["## 已跳过的模板占位文字（不当正文摘抄，对应位置按“源内容为空”处理）"] + ["- " + x for x in dict.fromkeys(rep["filler"])] + [""]
    if rep["ignored"]:
        L += ["## 忽略的数据项"] + ["- " + x for x in rep["ignored"]] + [""]
    if rep["warnings"] or warns:
        L += ["## 警告"] + ["- " + x for x in rep["warnings"] + list(warns)] + [""]
    with open(job.p("审核报告.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))


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
    out("草稿：%s" % draft)
    if rev:
        out("审核人批注 %d 条：" % len(rev))
        for c in rev:
            out("  #%s [%s] %s" % (c["id"], c["author"], short(c["text"], 120)))
    else:
        out("没有审核人批注。")
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
                   accept_unverified=getattr(a, "accept_unverified", False))
    out("终稿已生成：")
    for f in res["files"]:
        out("  " + f)
    out("  删除助手批注 %d 条；保留模板原有批注 %d 条；接受修订 %d 处" % (res["removed_comments"], res["kept_template_comments"],
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


def cmd_status(a):
    job = Job(a.job)
    qs = job.read("questions.json", []) or []
    out("任务：%s" % job.dir)
    out("目标：%s" % job.data["target"]["path"])
    out("源文件：" + "；".join("%s=%s" % (s["alias"], os.path.basename(s["path"])) for s in job.data["sources"]))
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
            path = os.path.join(SKILL_DIR, "profiles", a.save if a.save.endswith(".json") else a.save + ".json")
            write_json(path, prof)
            out("已保存：%s（建任务时用 --profile %s）" % (path, os.path.splitext(os.path.basename(path))[0]))
        return
    prof = load(a.name, SKILL_DIR)
    out(prof.get("name", "") + "：" + describe(prof))


def cmd_convert(a):
    dst = office.convert(a.file, a.fmt, dst=a.out)
    out("已转换：%s" % dst)


def cmd_run(a):
    a.plan = True
    cmd_new(a)


# ----------------------------------------------------------------------- 批量
def cmd_batch(a):
    a.manifest = a.manifest or getattr(a, "manifest_opt", None)
    if a.action != "init" and not a.manifest:
        raise DIError("请提供清单文件或批量任务 json 路径")
    if a.action == "init":
        rows = []
        targets = sorted(set(sum([glob.glob(t) for t in a.targets.split(";")], [])))
        common = [s for s in (a.sources or "").split(";") if s.strip()]
        for t in targets:
            stem = os.path.splitext(os.path.basename(t))[0]
            srcs = list(common)
            if a.pair:
                srcs += glob.glob(a.pair.replace("{stem}", stem))
            rows.append({"目标": t, "源文件": ";".join(srcs), "任务": a.task or "", "格式": a.format or "", "排版": ""})
        path = a.manifest or "批量清单.csv"
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            wr = csv.DictWriter(f, fieldnames=["目标", "源文件", "任务", "格式", "排版"])
            wr.writeheader()
            wr.writerows(rows)
        out("已生成批量清单（可用 Excel 编辑）：%s，共 %d 个目标" % (path, len(rows)))
        nxt("%s batch new \"%s\"" % (PY, path))
        return
    if a.action == "new":
        with open(a.manifest, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f)) if a.manifest.lower().endswith(".csv") else json.load(f)
        out_root = a.out or os.path.join(os.path.dirname(os.path.abspath(a.manifest)), "整合输出")
        jobs = []
        for r in rows:
            tgt = r.get("目标") or r.get("target")
            srcs = [x for x in (r.get("源文件") or r.get("sources") or "").split(";") if x.strip()]
            job = Job.create(tgt, srcs, task=r.get("任务") or r.get("task") or a.task or "", out_root=out_root,
                             profile=(r.get("排版") or r.get("profile") or None),
                             formats=[x for x in (r.get("格式") or r.get("formats") or "").split(",") if x])
            jobs.append(job.dir)
            out("  建立：%s" % job.dir)
        bpath = os.path.join(out_root, "批量_%s.json" % datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        write_json(bpath, {"jobs": jobs})
        out("批量任务文件：%s" % bpath)
        nxt("%s batch plan \"%s\"" % (PY, bpath))
        return
    b = read_json(a.manifest)
    jobs = [Job(d) for d in b["jobs"]]
    if a.action == "plan":
        merged = {}
        for job in jobs:
            plan = do_plan(job, quiet=True)
            for q in pending(plan["questions"]):
                k = (q["key"], q["question"])
                merged.setdefault(k, {"q": q, "jobs": []})["jobs"].append((job.dir, q["id"]))
            out("  %s：%d 个操作，%d 个待答" % (os.path.basename(job.dir), len(plan["ops"]), len(pending(plan["questions"]))))
        items = []
        for i, (k, v) in enumerate(merged.items()):
            items.append({"id": "B%d" % (i + 1), "key": k[0], "question": v["q"]["question"], "topic": v["q"]["topic"],
                          "options": [o["label"] for o in v["q"]["options"]], "recommended": v["q"].get("recommended", 0),
                          "jobs": v["jobs"]})
        b["questions"] = items
        write_json(a.manifest, b)
        if items:
            out("\n合并后待确认问题 %d 个（相同问题只问一次）：" % len(items))
            for it in items:
                out("  %s [%s] %s（涉及%d个任务）" % (it["id"], it["topic"], short(it["question"], 120), len(it["jobs"])))
                for i, o in enumerate(it["options"]):
                    out("      %d) %s%s" % (i + 1, short(o, 60), "  ← 推荐" if i == it["recommended"] else ""))
            nxt("%s batch answer \"%s\" B1=1 B2=2 ..." % (PY, a.manifest))
        else:
            nxt("%s batch draft \"%s\"" % (PY, a.manifest))
        return
    if a.action == "answer":
        items = {it["id"]: it for it in b.get("questions", [])}
        for kv in a.items:
            bid, val = kv.split("=", 1)
            it = items.get(bid.strip().upper())
            if not it:
                raise DIError("没有批量问题 %s" % bid)
            for jd, _ in it["jobs"]:
                qs = read_json(os.path.join(jd, "questions.json"), []) or []
                q = next((x for x in qs if x["key"] == it["key"]), None)      # 按问题键匹配，避免编号变化
                if not q:
                    continue
                sub = argparse.Namespace(job=jd, items=["%s=%s" % (q["id"], val)], remember=False)
                saved = sys.stdout
                try:
                    sys.stdout = open(os.devnull, "w", encoding="utf-8")
                    cmd_answer(sub)
                finally:
                    sys.stdout.close()
                    sys.stdout = saved
            out("%s → 已应用到 %d 个任务" % (bid, len(it["jobs"])))
        return
    for job in jobs:
        out("== %s" % os.path.basename(job.dir))
        try:
            if a.action == "draft":
                cmd_draft(argparse.Namespace(job=job.dir, defaults=a.defaults, check=False))
            elif a.action == "final":
                cmd_final(argparse.Namespace(job=job.dir, draft=None, format=a.format, reviewer_comments=a.reviewer_comments,
                                             strip_template_comments=False))
        except DIError as e:
            out("  [失败] %s" % e)


# ======================================================================= 参数
def main(argv=None):
    setup_console()
    p = argparse.ArgumentParser(prog="di.py", description="文档整合助手：多格式源文件 -> 目标文档（带批注草稿 -> 审核 -> 终稿）")
    sp = p.add_subparsers(dest="cmd")

    s = sp.add_parser("doctor", help="检查运行环境；加 --selfcheck 文件 可对 docx/xlsx 做结构自检")
    s.add_argument("--selfcheck", metavar="文件", help="对指定的 docx/xlsx 做 OOXML 结构自检（不依赖办公软件）")
    s.set_defaults(fn=cmd_doctor)

    for name, fn in (("new", cmd_new), ("run", cmd_run)):
        s = sp.add_parser(name, help="建立任务（run=建立并立即规划）")
        s.add_argument("--target", "-t", required=True, help="目标文件（要填写/整合的文档）")
        s.add_argument("--source", "-s", action="append", required=True, help="源文件，可多次；也可用分号分隔或文件夹/通配符")
        s.add_argument("--task", default="", help="用户原话（任务描述，用于识别取值规则）")
        s.add_argument("--out", help="输出根目录（默认 目标所在目录/整合输出）")
        s.add_argument("--name", help="任务目录名")
        s.add_argument("--profile", help="排版规范名或 json 路径（默认=文档摘抄提示词）")
        s.add_argument("--format", help="终稿额外格式，如 doc,pdf,md")
        s.add_argument("--plan", action="store_true", help="建立后立即规划")
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

    s = sp.add_parser("show", help="按ID查看局部内容，如 S1:H5 / S1:T3 / S2:Sheet1!A1:D20")
    s.add_argument("job")
    s.add_argument("ref")
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
    s.set_defaults(fn=cmd_questions)

    s = sp.add_parser("answer", help="记录用户回答，如 Q1=1 Q2=1,3 Q3=\"文字\"")
    s.add_argument("job")
    s.add_argument("items", nargs="+")
    s.add_argument("--remember", action="store_true", help="把数据对应类回答记为长期规则")
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

    s = sp.add_parser("find", help="按描述查找文件（当前目录/桌面/指定目录），如 find DEMO-100 详细规范")
    s.add_argument("query", nargs="+")
    s.add_argument("--dir", action="append")
    s.add_argument("--depth", type=int, default=2)
    s.add_argument("--top", type=int, default=6)
    s.set_defaults(fn=cmd_find)

    s = sp.add_parser("add-op", help="追加手工操作（JSON 或 @文件）")
    s.add_argument("job")
    s.add_argument("op")
    s.set_defaults(fn=cmd_add_op)

    s = sp.add_parser("draft", help="生成带批注的草稿")
    s.add_argument("job")
    s.add_argument("--defaults", action="store_true", help="未回答的问题按推荐处理（批注中标⚠）")
    s.add_argument("--check", action="store_true", help="用办公软件打开校验")
    s.add_argument("--discard-edits", action="store_true", help="丢弃上一版草稿里的人工修改和人工批注，纯按新规划生成")
    s.add_argument("--raw-snapshot", action="store_true", help="源文件快照保留原样（默认净化：去掉原批注、作者、修订、隐藏文字、customXml、外部链接）")
    s.set_defaults(fn=cmd_draft)

    s = sp.add_parser("review", help="读取审核人对草稿的批注与修改")
    s.add_argument("job")
    s.set_defaults(fn=cmd_review)

    s = sp.add_parser("final", help="审核确认后生成终稿")
    s.add_argument("job")
    s.add_argument("--draft", help="指定草稿文件（默认最新版本）")
    s.add_argument("--format", help="额外输出格式：doc,pdf,md")
    s.add_argument("--reviewer-comments", choices=["ask", "drop", "keep"], default="ask")
    s.add_argument("--strip-template-comments", action="store_true", help="同时删除模板原有批注")
    s.add_argument("--accept-unverified", action="store_true", help="明知有“本批未核”项仍要定稿（登记到 终稿遗留项.md）")
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

    s = sp.add_parser("batch", help="批量：init/new/plan/answer/draft/final")
    s.add_argument("action", choices=["init", "new", "plan", "answer", "draft", "final"])
    s.add_argument("manifest", nargs="?", help="清单csv/json（init/new）或批量任务json（其余）")
    s.add_argument("items", nargs="*")
    s.add_argument("--manifest", dest="manifest_opt", help="同位置参数 manifest")
    s.add_argument("--targets", help="init：目标文件通配，如 \"记录/*.doc\"")
    s.add_argument("--sources", help="init：公共源文件，分号分隔")
    s.add_argument("--pair", help="init：按目标名配对的源文件，如 \"数据/{stem}.xlsx\"")
    s.add_argument("--task")
    s.add_argument("--format")
    s.add_argument("--out")
    s.add_argument("--defaults", action="store_true")
    s.add_argument("--reviewer-comments", choices=["ask", "drop", "keep"], default="ask")
    s.set_defaults(fn=cmd_batch)

    a = p.parse_args(argv)
    if not getattr(a, "fn", None):
        p.print_help()
        return 1
    try:
        rc = a.fn(a)      # doctor --selfcheck 等命令用返回值表示结果
        return rc if isinstance(rc, int) else 0
    except DIError as e:
        out("错误：%s" % e)
        return 2
