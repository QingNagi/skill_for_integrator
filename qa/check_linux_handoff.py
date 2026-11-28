#!/usr/bin/env python3
"""Exercise the shipped POSIX handoff from another directory using only synthetic inputs."""
import errno
import hashlib
import json
import os
from pathlib import Path
import pty
import select
import shlex
import shutil
import subprocess
import sys
import tempfile
import time


def read_document(path):
    return json.loads(path.read_text(encoding="utf-8"))


def terminal(launcher, cwd, reply, expected):
    master, slave = pty.openpty()
    with subprocess.Popen(["sh", launcher], cwd=cwd, stdin=slave, stdout=slave, stderr=slave) as proc:
        os.close(slave)
        log = bytearray()
        sent = False
        try:
            end = time.monotonic() + 75
            while time.monotonic() < end:
                if not select.select([master], [], [], 1)[0]:
                    continue
                try:
                    data = os.read(master, 65536)
                except OSError as exc:
                    if exc.errno != errno.EIO:
                        raise
                    break
                if not data:
                    break
                log.extend(data)
                if not sent and "DELIVER".encode() in log:
                    os.write(master, (reply + "\n").encode())
                    sent = True
            else:
                raise AssertionError("Terminal timed out: " + log.decode("utf-8", "replace"))
            assert sent and (proc.wait(timeout=5) == 0) == expected, log.decode("utf-8", "replace")
        finally:
            if proc.poll() is None:
                proc.kill()
            os.close(master)


def main(entry, fixture, installed):
    with tempfile.TemporaryDirectory(prefix="人审 QA !& ") as tmp:
        work = Path(tmp)
        inputs = work / "只读 原件"
        inputs.mkdir()
        for name in ("目标.docx", "研制任务书.docx"):
            shutil.copyfile(fixture / name, inputs / name)
            (inputs / name).chmod(0o444)
        original = {p.name: hashlib.sha256(p.read_bytes()).digest() for p in inputs.iterdir()}
        inputs.chmod(0o555)
        try:
            request = work / "requests.json"
            request.write_text(json.dumps([{"target": str(inputs / "目标.docx"),
                                            "sources": [str(inputs / "研制任务书.docx")],
                                            "task": "按批注摘录来源并交付草稿"}] * 2), encoding="utf-8")
            unrelated = work / "其他 位置"
            unrelated.mkdir()
            output = work / "交接 输出"
            planning = subprocess.run(["sh", entry, "batch", "run", request, "--handoff", "--json",
                                        "--out", output], check=True, capture_output=True, cwd=unrelated)
            result = json.loads(planning.stdout)
            assert result["status"] == "handoff_ready", result
            manifest = Path(result["sealed_manifest"])
            seal_digest = hashlib.sha256(manifest.read_bytes()).digest()
            launcher = Path(result["launcher"])
            assert launcher.parent == manifest.parent and launcher.name == "出稿.sh"
            assert os.access(launcher, os.X_OK) and launcher.read_bytes().startswith(b"#!/bin/sh\n")
            if installed is not None:
                body = launcher.read_text("utf-8")
                interpreter = installed / "runtime/python/bin/python3"
                command = installed / "scripts/di.py"
                assert interpreter.is_file() and command.is_file()
                assert f"exec {shlex.quote(str(interpreter))} -E -s -B -X utf8 {shlex.quote(str(command))} deliver " in body
            rows = read_document(manifest)["jobs"]
            assert len(rows) == 2 and all(not row["confirmations"] for row in rows)
            assert not list(output.rglob("草稿/*.docx")) and not list(output.glob("批量交付清单*.md"))
            piped = subprocess.run(["sh", launcher], cwd=unrelated, input=b"DELIVER\n", capture_output=True)
            assert piped.returncode and "终端".encode() in piped.stdout + piped.stderr
            master, slave = pty.openpty()
            try:
                hidden = subprocess.run(["sh", launcher], cwd=unrelated, stdin=slave, capture_output=True, timeout=10)
                assert hidden.returncode and "终端".encode() in hidden.stdout + hidden.stderr
            finally:
                os.close(slave)
                os.close(master)
            terminal(launcher, unrelated, "0", False)
            assert not list(output.rglob("草稿/*.docx")) and not list(output.glob("批量交付清单*.md"))
            terminal(launcher, unrelated, "DELIVER", True)
            drafts = [Path(row["inputs"]["job"]) / "草稿" for row in rows]
            assert all(len(list(d.glob("*.docx"))) == 1 for d in drafts)
            report, = output.glob("批量交付清单*.md")
            assert "共 2 份草稿" in report.read_text("utf-8")
            assert hashlib.sha256(manifest.read_bytes()).digest() == seal_digest
            assert {p.name: hashlib.sha256(p.read_bytes()).digest() for p in inputs.iterdir()} == original
            print("Handoff verified in a separate PTY: hidden output, piped input and cancellation left no drafts; two reviewer-launched drafts and one delivery report.")
        finally:
            inputs.chmod(0o755)


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve() if len(sys.argv) > 3 else None)
