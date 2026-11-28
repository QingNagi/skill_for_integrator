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


def ask_form(questions):
    form = []
    for q in questions:
        rec = q.get("recommended", 0)
        options = sorted(enumerate(q["options"]), key=lambda item: item[0] != rec)[:4]
        choices = [{"label": short(o["label"], 40) + ("（推荐）" if i == rec else ""),
                    "description": "选项%d" % (i + 1)} for i, o in options]
        if len(choices) < 2:
            choices.append({"label": "其他（我来说明）", "description": "直接提供说明文字"})
        form.append({"question": ("%s %s" % (q["id"], q["question"]))[:400], "header": q["topic"][:12],
                     "multiSelect": bool(q.get("multi")), "options": choices})
    return form


def plan_result(job, plan):
    remaining = [q for q in plan["questions"] if not q.get("answered")]
    return {"status": "needs_answers" if remaining else "planned", "job": job.dir,
            "next_action": "ask_user" if remaining else "draft",
            "pending": len(remaining), "questions": ask_form(remaining[:4]),
            "details": job.p("questions.md")}
