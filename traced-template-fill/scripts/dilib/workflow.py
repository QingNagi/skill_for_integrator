"""Machine-readable workflow results and opt-in phase timing."""
import json
import os
import sys
import time
from contextlib import contextmanager

from .common import short


@contextmanager
def timed(phase):
    if os.environ.get("DI_TIMING") != "1":
        yield
        return
    start = time.perf_counter()
    try:
        yield
    finally:
        print(json.dumps({"phase": phase, "seconds": round(time.perf_counter() - start, 6)}),
              file=sys.stderr, flush=True)


def ask_form(questions, *, full=False):
    form = []
    for q in questions:
        rec = q.get("recommended", 0)
        options = sorted(enumerate(q["options"]), key=lambda item: item[0] != rec)
        if not full:
            options = options[:4]
        choices = [{"label": (o["label"] if full else short(o["label"], 40)) + ("（推荐）" if i == rec else ""),
                    "description": "选项%d" % (i + 1)} for i, o in options]
        if len(choices) < 2:
            choices.append({"label": "其他（我来说明）", "description": "直接提供说明文字"})
        prompt = "%s %s" % (q["id"], q["question"])
        form.append({"question": prompt if full else prompt[:400], "header": q["topic"][:12],
                     "multiSelect": bool(q.get("multi")), "options": choices})
    return form


def review_references(report):
    refs = []
    entries = [*report.get("fills", []), *report.get("held_measurements", [])]
    def priority(row):
        check = row.get("check", "")
        return 0 if "按常用修约" in check else 1 if "超出限值" in check else 2
    for row in sorted(entries, key=priority):
        source = row.get("source", "")
        if row.get("check") and "!" in source:
            alias, _, locations = source.partition(":")
            refs.extend(alias + ":" + loc.strip() for loc in locations.split(";") if "!" in loc)
    for item in report.get("stale", []):
        if item.get("cell"):
            refs.append("T:" + item["cell"])
    return list(dict.fromkeys(refs))[:5]


def review_excerpts(job, references):
    """Read the selected original cells from the same source models used for this draft."""
    from .common import DIError
    from .content import parse_ref, render_block, render_sheet

    excerpts = []
    for reference in references:
        alias, address = parse_ref(reference)
        model = job.model(alias)
        try:
            if model.m["type"] == "xlsx":
                value = render_sheet(model, address, cell_chars=None)
            else:
                value = render_block(model, address, full=True)
        except (DIError, ValueError, IndexError) as exc:
            excerpts.append({"ref": reference, "error": str(exc)})
        else:
            excerpts.append({"ref": reference, "source_text": value})
    return excerpts


def plan_result(job, plan):
    remaining = [q for q in plan["questions"] if not q.get("answered")]
    ids = {q["key"]: q["id"] for q in remaining}
    groups = {}
    for q in remaining:
        parent = ids.get(q.get("conditional_on"))
        if parent:
            groups.setdefault(parent, []).append(q["id"])
    return {"status": "needs_answers" if remaining else "planned", "question_groups": groups, "job": job.dir,
            "next_action": "ask_user" if remaining else "draft",
            "pending": len(remaining), "questions": ask_form(remaining[:4]), "all_questions": ask_form(remaining, full=True),
            "details": job.p("questions.md")}
