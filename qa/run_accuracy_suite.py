"""Exercise the sealed synthetic examples with a scripted reviewer and score the actual Word files."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from accuracy_suite import TASK, score

# Answers are limited to the known acquisition contract of these five invented fixtures.
DECISIONS = {"来源核对": 2, "测量单位": 2, "测试工况核对": 2, "实测记录核对": 2,
             "未注明条件": 1, "数据对应": 1, "数值合理性": 1, "值类型与极性": 1}


def run(entry, inputs, outputs):
    command = ([sys.executable, str(entry)] if entry.suffix == ".py" else [str(entry)])
    results = []
    outputs.mkdir(parents=True, exist_ok=False)
    for folder in sorted(inputs.glob("示例*")):
        book, = folder.glob("*.xlsx")
        job = outputs / folder.name
        args = ["run", "--draft", "-t", folder / "模板.docx", "-s", folder / "详细规范.docx",
                "-s", book, "--task", TASK, "--out", outputs, "--name", folder.name]
        started = time.perf_counter()
        decisions = calls = 0
        while calls < 12:
            calls += 1
            cmd = subprocess.run(command + list(map(str, args)) + ["--json"], capture_output=True,
                                 encoding="utf-8", env=dict(os.environ, DI_UTF8="1"), timeout=120)
            response = json.loads(cmd.stdout)
            if cmd.returncode == 0 and response["status"] == "draft_ready":
                break
            if cmd.returncode != 3 or response["status"] != "needs_answers":
                raise ValueError((folder.name, cmd.returncode, response))
            args = ["answer", job]
            for q in response["questions"]:
                choice = DECISIONS[q["header"]]
                qid = q["question"].split()[0]
                if not any(o["description"] == "选项%d" % choice for o in q["options"]):
                    raise ValueError((folder.name, qid, q))
                args.append("%s=%d" % (qid, choice))
                decisions += 1
            args.extend(["--user-quote", "合成来件的采集说明确认本批、单位及标称工况；符号、状态或极性有歧义的格子留空。",
                         "--reviewer", "合成测试脚本", "--draft"])
        else:
            raise ValueError("问题没有收敛：" + folder.name)
        results.append({"case": folder.name, "cli_calls": calls, "decisions": decisions,
                        "seconds": round(time.perf_counter() - started, 3)})
    print(json.dumps(results, ensure_ascii=False))
    return score(inputs, outputs)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("entry", "inputs", "outputs"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    raise SystemExit(run(args.entry.resolve(), args.inputs.resolve(), args.outputs.resolve()))
