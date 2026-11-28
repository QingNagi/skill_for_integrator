"""任务内部的人工决策记录。记录 CLI 收到的原话，不推断填写者的身份。"""
import datetime
import json
from pathlib import Path

from .common import DIError, ReviewBlocked, read_json
from .evidence import digest
from .preferences import project_dir


def protected_question(key):
    return key.startswith(("src:verify:", "src:spec:", "src:ocr:", "src:unread:")) or key.split(":")[:2] == ["td", "samplemap"]


def require_quote(quote, action):
    if not quote or not quote.strip():
        raise DIError('%s 需要 --user-quote "用户原话"；请先让用户本人确认。' % action)


def record_decision(job, action, quote, **details):
    require_quote(quote, action)
    event = {"time": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
             "action": action, "user_quote": quote, **details}
    with open(job.p("decisions.jsonl"), "a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + "\n")


def answer_binding(answer):
    return digest([answer["effect"], answer.get("evidence"), answer.get("rule_sha256")])


def _events(path):
    try:
        with path.open(encoding="utf-8") as stream:
            events = [json.loads(line) for line in stream]
        if all(isinstance(e, dict) and isinstance(e.get("action"), str)
               and isinstance(e.get("user_quote"), str) and e["user_quote"].strip() for e in events):
            return events
    except (OSError, ValueError):
        pass
    raise ReviewBlocked("决定记录缺失或损坏，请从任务备份恢复原始 decisions.jsonl 后重新定稿。")


def _matches(events, key, answer):
    for event in events:
        if event["user_quote"] != answer.get("user_quote"):
            continue
        if answer.get("word_draft"):
            fields = {"key": key, "option": answer["label"], "fp": answer["fp"],
                      "draft": answer["word_draft"], "author": answer.get("confirmed_by"),
                      "comment": answer.get("word_parent"), "reply": answer.get("word_reply")}
            if event["action"] == "Word 核对" and all(event.get(k) == v for k, v in fields.items()):
                if _binding_matches(event, answer):
                    return True
        elif event["action"] == ("answer --remember" if answer.get("remember") else "answer"):
            choices = event.get("answers")
            if isinstance(choices, list) and any(isinstance(c, dict) and c.get("key") == key
                    and c.get("answer") == answer.get("label") and c.get("fp") == answer.get("fp")
                    and _binding_matches(c, answer) for c in choices):
                return True
    return False


def _binding_matches(entry, answer):
    if "binding" in entry or answer.get("receipt_version"):
        return entry.get("binding") == answer_binding(answer)
    return True  # 1.1 receipts bound the original quote, question, option and input fingerprint.


def verify_receipts(job, draft, input_fp):
    local = Path(job.p("decisions.jsonl"))
    required = [(local, key, answer) for key, answer in job.read("answers.json", {}).items()
                if isinstance(answer, dict) and (protected_question(key) or answer.get("remember"))
                and not answer.get("default") and (not input_fp or answer.get("fp") == input_fp)]
    facts = job.read("facts.json", {})
    for fact in facts.get("facts", []):
        scope = fact["scope"]
        if not isinstance(scope, dict) or not scope.get("shared"):
            continue
        shared = read_json(project_dir(job) / "facts" / (scope["shared"] + ".json"), {})
        if not shared.get("origin_job") or not shared.get("answer"):
            raise ReviewBlocked("复用的决定记录缺失，请在本任务重新确认。")
        required.append((Path(shared["origin_job"]) / "decisions.jsonl",
                         shared.get("question", scope["question"]), shared["answer"]))
    exception = job.data.get("draft_exceptions", {}).get(draft)
    legacy_defaults = not exception and any(item.get("kind") == "default"
        for item in job.data.get("unverified", {}).get(draft, []))
    paths = {item[0] for item in required}
    if exception or legacy_defaults:
        paths.add(local)
    ledgers = {path: _events(path) for path in paths}
    for path, key, answer in required:
        if not _matches(ledgers[path], key, answer):
            raise ReviewBlocked("决定记录与 %s 的用户原话、选项或输入版本不符，请核对后重新确认。" % key)
    if exception and not any(e["action"] == exception["action"] and e.get("draft") == draft and
                             digest(e["user_quote"]) == exception["quote_sha256"] for e in ledgers[local]):
        raise ReviewBlocked("草稿例外操作缺少对应的决定记录，请恢复原始记录或重新生成草稿。")
    if legacy_defaults and not any(e["action"] in ("draft --defaults", "draft --defaults --discard-edits")
                                   and e.get("draft") == draft for e in ledgers[local]):
        raise ReviewBlocked("旧草稿的默认选项缺少原始决定记录，请重新生成草稿并核对。")
