"""Run one actual Claude Code turn in an isolated case and retain its trace outside the case."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("receipts", type=Path)
    parser.add_argument("prompt", type=Path, help="UTF-8 text provided by the test operator")
    parser.add_argument("--timeout", type=int, default=420)
    parser.add_argument("--model", help="Use the model configured for this evaluation")
    parser.add_argument("--blind-project", action="store_true", help="Limit preapproved tools to project reads and the skill launcher")
    args = parser.parse_args()
    project, receipts = args.project.resolve(), args.receipts.resolve()
    if receipts.is_relative_to(project) or args.prompt.resolve().is_relative_to(project):
        parser.error("Store operator prompts and receipts outside the tested project.")
    receipts.mkdir(parents=True, exist_ok=True)
    session = receipts / "session.json"
    start = not session.exists()
    if start:
        session.write_text(json.dumps({"id": str(uuid.uuid4()), "project": str(project)}), "utf-8")
    meta = json.loads(session.read_text("utf-8"))
    if meta["project"] != str(project):
        parser.error("The session belongs to another project.")
    turn = 1 + len(list(receipts.glob("turn-*.jsonl")))
    stem = "turn-%02d" % turn
    prompt = args.prompt.read_text("utf-8-sig")
    cli = shutil.which("claude.cmd" if os.name == "nt" else "claude")
    if not cli:
        parser.error("Claude Code is missing.")
    settings = {"disableAllHooks": True}
    allowed = "Read(./**),Glob,Bash,Skill(traced-template-fill)"
    if args.blind_project:
        settings["permissions"] = {"deny": ["Read(../**)", "Bash(*../*)", "Bash(*operator*)", "Bash(*.sha256*)"]}
        allowed = ("Read(./**),Glob,Bash(pwd),Bash(ls),Bash(ls *),Bash(*traced-template-fill*run.cmd*),"
                   "Bash(*traced-template-fill*run.sh*),Bash(*traced-template-fill*run.ps1*),Skill(traced-template-fill)")
    command = [cli, "-p", "--output-format", "stream-json", "--verbose",
               "--permission-mode", "dontAsk", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
               "--settings", json.dumps(settings, separators=(",", ":")), "--tools", "Read,Glob,Bash,Skill",
               "--allowedTools", allowed,
               "--session-id" if start else "--resume", meta["id"]]
    if args.model:
        command.extend(["--model", args.model])
    before = time.monotonic()
    try:
        run = subprocess.run(command, cwd=project, input=prompt.encode("utf-8"),
                             capture_output=True, timeout=args.timeout)
        stdout, stderr, status = run.stdout, run.stderr, run.returncode
    except subprocess.TimeoutExpired as exc:
        stdout, stderr, status = exc.stdout or b"", exc.stderr or b"", 124
    elapsed = time.monotonic() - before
    (receipts / (stem + ".jsonl")).write_bytes(stdout)
    (receipts / (stem + ".stderr")).write_bytes(stderr)
    events = []
    for line in stdout.splitlines():
        try:
            events.append(json.loads(line))
        except (ValueError, UnicodeError):
            pass
    result = next((e for e in reversed(events) if e.get("type") == "result"), {})
    assistant = [e["message"] for e in events if e.get("type") == "assistant"]
    calls = [b for msg in assistant for b in msg["content"] if b.get("type") == "tool_use"]
    usage = result.get("usage", {})
    report = {"turn": turn, "status": status, "seconds": round(elapsed, 2),
              "models": sorted({m["model"] for m in assistant if m.get("model")}),
              "model_usage_keys": list(result.get("modelUsage", {})),
              "calls": [{"tool": b["name"], "command": b.get("input", {}).get("command")} for b in calls],
              "denials": len(result.get("permission_denials", [])),
              "usage": {k: v for k, v in usage.items() if k.endswith("tokens")},
              "reply": result.get("result", ""), "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()}
    (receipts / (stem + ".json")).write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return status or int(not result or result.get("is_error", False))


if __name__ == "__main__":
    raise SystemExit(main())
