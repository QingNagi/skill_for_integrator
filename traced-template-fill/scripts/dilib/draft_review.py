# -*- coding: utf-8 -*-
"""生成草稿的结构检查、Office 打开检查和逐项审核报告。"""
from __future__ import annotations

import datetime
import os

from . import office
from .common import out, short


def pending(qs):
    return [q for q in qs if not q.get("answered")]


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
    if rep.comment_counts is not None:
        out('  实际 Word 批注共 %d 条，其中本助手署名 %d 条。' % tuple(rep.comment_counts))
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
    if os.path.isfile(job.p("decisions.jsonl")):
        L += ["本任务的关键确认原话和涉及的问题、草稿版本保存在同目录 decisions.jsonl。", ""]
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
        L += ["", '处理方式：清除或改正这些格后保存；确认旧值仍适用请 final --accept-unverified --user-quote "用户原话"（会登记到 终稿遗留项.md）；来源/未读取项请回答对应问题。', ""]
    L += ["", "## 未填项（需人工补充）"] + ["- " + x for x in rep["unfilled"]] + [""]
    if rep.get("filler"):
        L += ["## 已跳过的模板占位文字（不当正文摘抄，对应位置按“源内容为空”处理）"] + ["- " + x for x in dict.fromkeys(rep["filler"])] + [""]
    if rep["ignored"]:
        L += ["## 忽略的数据项"] + ["- " + x for x in rep["ignored"]] + [""]
    if rep["warnings"] or warns:
        L += ["## 警告"] + ["- " + x for x in rep["warnings"] + list(warns)] + [""]
    with open(job.p("审核报告.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))
