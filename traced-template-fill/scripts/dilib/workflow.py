"""Machine-readable workflow results and opt-in phase timing."""
import json
import os
import sys
import time
from collections import Counter
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


def planned_text(operations):
    """Final plain text requested for each explicitly written block or cell, in operation order."""
    values = {}
    for op in operations:
        if op["op"] == "set_cell" and op.get("cell"):
            values[op["cell"]] = "".join(run[0] for run in op.get("runs", []))
        elif op["op"] == "set_text" and (op.get("cell") or op.get("block")):
            values[op.get("cell") or op["block"]] = op["text"]
    return values


def review_summary(report, unresolved):
    """Explain flagged measurements and account separately for retained template values."""
    filled = {row["target"] for row in report.get("fills", []) if "source_sample" in row and row.get("value")}
    held = report.get("held_measurements", [])
    targets = report.get("measurement_targets")
    reasons = Counter(item.get("detail") or item.get("name") or item.get("kind") or "待核"
                      for item in unresolved if not item.get("spec_question"))
    holds = Counter(row.get("check") or row.get("how") or "来源未确认" for row in held)
    coverage = "registered_only"
    retained = 0
    if targets is None:
        # Old plans only recorded cells explicitly held by a matching source.
        filled_count, blank_count = len(filled), len(held)
    else:
        coverage = "mapped_template_cells"
        writes = report.get("measurement_written", {})
        blanks = {cid for cid, original in targets.items() if not writes.get(cid, original).strip()}
        classified = {row["target"] for row in held}
        if blanks - classified:
            holds["没有可写入的对应实测值"] += len(blanks - classified)
        filled_count = sum(bool(value.strip()) for value in writes.values())
        blank_count = len(blanks)
        retained = len(targets.keys() - writes.keys() - blanks)
    return {"measurement_fills": filled_count, "measurement_holds": blank_count,
            "measurement_retained": retained, "coverage": coverage,
            "unverified_reasons": [{"reason": s, "count": n} for s, n in reasons.items()],
            "hold_reasons": [{"reason": s, "count": n} for s, n in holds.items()]}


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
