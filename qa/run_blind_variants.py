"""Run sealed cases in fresh Claude Code projects and score the generated Word drafts."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

from blind_variants import metrics, read_key

HERE = Path(__file__).resolve().parent


def save(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def invoke(argv, env, log):
    with log.open("ab") as stream:
        return subprocess.run(list(map(str, argv)), env=env, stdout=stream, stderr=subprocess.STDOUT).returncode


def after_draft(receipts):
    calls = 0
    draft_call = None
    positions = {}
    for path in sorted(receipts.glob("turn-*.jsonl")):
        for line in path.read_text("utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            message = event.get("message")
            if event.get("type") not in ("assistant", "user") or not isinstance(message, dict):
                continue
            content = message.get("content", [])
            for block in content if isinstance(content, list) else []:
                if block.get("type") == "tool_use":
                    calls += 1
                    positions[block["id"]] = calls
                elif block.get("type") == "tool_result" and block.get("tool_use_id") in positions:
                    output = str(block.get("content", ""))
                    if '"status"' in output and '"draft_ready"' in output:
                        draft_call = positions[block["tool_use_id"]]
    return calls, calls - draft_call if draft_call is not None else None


def run_one(args, case, repeat):
    name = case.name
    project = args.run_dir / "projects" / (name + "-%02d" % repeat)
    receipts = args.run_dir / "receipts" / project.name
    if args.rescore:
        turns = sorted(receipts.glob("turn-*.json"))
        if not turns:
            raise ValueError("The original turn receipt is missing.")
        turn = turns[-1]
        rc = json.loads(turn.read_text("utf-8"))["status"]
        events = [json.loads(line) for line in turn.with_suffix(".jsonl").read_bytes().splitlines() if line.strip()]
        end = next((event for event in reversed(events) if event.get("type") == "result"), {})
        return score_project(args, project, receipts, name, repeat, rc or int(not end or end.get("is_error", False)))
    receipts.mkdir(parents=True, exist_ok=False)
    project.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(case, project)
    env = dict(os.environ, DI_UTF8="1", PYTHONUTF8="1", DI_RUNTIME_DIR=str(args.run_dir / "runtime"),
               DI_USER_DATA_DIR=str(receipts / "user-data"))
    log = receipts / "runner.log"
    if args.kit:
        shutil.copytree(args.kit, project / ".claude/skills/traced-template-fill")
    else:
        status = invoke([sys.executable, args.installer, "--project-root", project, "--manifest", args.manifest], env, log)
        if status:
            raise RuntimeError("Deployment failed; see " + str(log))
    prompt = receipts / "operator-brief.md"
    shutil.copyfile(project / "委托.md", prompt)
    run = [sys.executable, HERE / "claude_review_turn.py", project, receipts, prompt,
           "--timeout", args.timeout, "--blind-project"]
    if args.model:
        run.extend(["--model", args.model])
    rc = invoke(run, env, log)
    drafts = [path for path in project.glob("**/草稿/*_v*.docx") if not path.name.startswith("~")]
    if not drafts and rc == 0:
        # The operator's statement has no answer values. Repeat it once when
        # Claude paused for options already resolved by the acquisition contract.
        prompt.write_text("请按委托.md 中已给的合成审核人口述继续。未提供证据的问题选留空，其他选项按明确依据。请交付草稿。", "utf-8")
        rc = invoke(run, env, log)
    return score_project(args, project, receipts, name, repeat, rc)


def score_project(args, project, receipts, name, repeat, rc):
    drafts = [p for p in project.glob("**/草稿/*_v*.docx") if not p.name.startswith("~")]
    key = read_key(args.vault / (name + ".json"), args.vault / (name + ".sha256"))
    report = {"case": name, "repeat": repeat, "returncode": rc, "draft_count": len(drafts)}
    altered = [filename for filename, digest in key.get("input_sha256", {}).items()
               if hashlib.sha256((project / filename).read_bytes()).hexdigest() != digest]
    if altered:
        report["altered_inputs"] = altered
    if len({p.parent for p in drafts}) == 1 and not altered:
        draft = max(drafts, key=lambda p: int(p.stem.rsplit("_v", 1)[1]))
        report.update(metrics(key, draft), draft=str(draft.relative_to(args.run_dir)))
    report["tool_calls"], report["calls_after_observed_draft"] = after_draft(receipts)
    turns = [json.loads(p.read_text("utf-8")) for p in sorted(receipts.glob("turn-*.json"))]
    report.update(turns=len(turns), seconds=round(sum(t["seconds"] for t in turns), 2),
                  denials=sum(t["denials"] for t in turns), models=sorted({m for t in turns for m in t["models"]}))
    save(receipts / ("rescored.json" if args.rescore else "score.json"), report)
    print(project.name, report.get("correct_fills"), report.get("wrong_fills"), report["seconds"], flush=True)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("public", "vault", "run_dir"):
        p.add_argument(name, type=lambda value: Path(value).resolve())
    release = p.add_mutually_exclusive_group(required=True)
    release.add_argument("--kit", type=Path, help="Path to the frozen extracted skill containing the self-contained payload")
    release.add_argument("--manifest", type=Path, help="SHA256SUMS for the split source/runtime release")
    p.add_argument("--installer", type=Path, help="Installer supplied by the split release")
    p.add_argument("--repeats", type=int, default=5)
    p.add_argument("--jobs", type=int, default=2)
    p.add_argument("--timeout", type=int, default=420)
    p.add_argument("--model")
    p.add_argument("--rescore", action="store_true", help="Use original projects, model traces and answer commitments, without calling a model")
    args = p.parse_args()
    if args.jobs < 1 or args.repeats < 1:
        p.error("Positive --jobs and --repeats are required.")
    if args.manifest and not args.installer:
        p.error("--manifest requires --installer.")
    if args.kit:
        payload = args.kit / "payload.zip"
        release_hash = hashlib.sha256(payload.read_bytes()).hexdigest()
        if release_hash != (args.kit / "payload.sha256").read_text("ascii").strip():
            p.error("The frozen payload digest does not match.")
    else:
        release_hash = hashlib.sha256(args.manifest.read_bytes()).hexdigest()
    if not args.rescore:
        args.run_dir.mkdir(parents=True, exist_ok=False)
    cases = sorted(path for path in args.public.glob("case-*") if path.is_dir())
    if not cases:
        p.error("No case directories.")
    baseline = {}
    for case in cases:
        key = read_key(args.vault / (case.name + ".json"), args.vault / (case.name + ".sha256"))
        baseline[case.name] = metrics(key, case / "模板.docx")
    precommit = {"payload_sha256" if args.kit else "release_manifest_sha256": release_hash,
                 "scorer": hashlib.sha256((HERE / "blind_variants.py").read_bytes()).hexdigest(),
                 "expected": {case.name: (args.vault / (case.name + ".sha256")).read_text("ascii").strip() for case in cases},
                 "repeats": args.repeats, "cases": len(cases), "model": args.model}
    if args.rescore:
        if precommit != json.loads((args.run_dir / "commitments.json").read_text("utf-8")):
            p.error("The evaluation commitments have changed.")
    else:
        save(args.run_dir / "baseline.json", baseline)
        save(args.run_dir / "commitments.json", precommit)
    def trial(case_repeat):
        case, repeat = case_repeat
        try:
            return run_one(args, case, repeat)
        except Exception as exc:
            return {"case": case.name, "repeat": repeat, "error": str(exc)}
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(trial, [(c, i) for i in range(1, args.repeats + 1) for c in cases]))
    by_case = {}
    for case in cases:
        runs = [r for r in results if r["case"] == case.name]
        scored = [r for r in runs if "fill_rate" in r]
        calls = [r["calls_after_observed_draft"] for r in scored if r.get("calls_after_observed_draft") is not None]
        by_case[case.name] = {"attempts": len(runs), "scored": len(scored),
                              "max_wrong_fills": max((r["wrong_fills"] for r in scored), default=None),
                              "min_fill_rate_among_scored": min((r["fill_rate"] for r in scored), default=None),
                              "max_observed_calls_after_draft": max(calls, default=None),
                              "p90_observed_calls_after_draft": sorted(calls)[math.ceil(.9 * len(calls))-1] if calls else None}
    correctness = all(r.get("returncode") == 0 and r.get("wrong_fills") == 0 and r.get("fill_rate", 0) >= .95
                      for r in results)
    overhead = all(r.get("calls_after_observed_draft") is not None and r["calls_after_observed_draft"] <= 5
                   for r in results)
    passed = correctness and overhead
    name = "rescored-summary.json" if args.rescore else "summary.json"
    save(args.run_dir / name, {"cases": by_case, "runs": results, "correctness_passed": correctness,
                               "every_run_within_call_target": overhead, "passed": passed})
    return int(not passed)


if __name__ == "__main__":
    raise SystemExit(main())
