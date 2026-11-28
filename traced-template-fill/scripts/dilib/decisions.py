"""任务内部的人工决策记录。记录 CLI 收到的原话，不推断填写者的身份。"""
import datetime
import json
import os
import re
import tempfile
from pathlib import Path

from .common import DIError, ReviewBlocked, read_json, write_json
from .evidence import digest
from .preferences import locked_preferences, project_dir

GENESIS = "0" * 64
SOURCES = {"agent_relay": "Agent 转述", "confirm_cli": "终端交互", "word_reply": "Word 署名回复"}
BROKEN = "决定记录缺失或损坏，请从任务备份恢复原始 decisions.jsonl 及对应的 decisions.head.json 后重新定稿。"
HEAD_LABEL = "- 决定日志 SHA-256："


def protected_question(key):
    return key.startswith(("src:verify:", "src:spec:", "src:ocr:", "src:unread:")) or key.split(":")[:2] == ["td", "samplemap"]


def copied_request(task, quote):
    """Recognize both a continuous quotation and clauses copied from different parts of the task."""
    original = "".join((task or "").casefold().split())
    clauses = ["".join(part.casefold().split()).strip(".") for part in re.split(r"[。！？!?；;，,\n]+", quote or "")]
    clauses = [s for s in clauses if s]
    return bool(clauses) and all(clause in original for clause in clauses)


def needs_reviewer_reply(key, effect, task, quote, *, requires_program=False):
    """A request to fill a form does not attest to the identities or conventions subsequently queried."""
    if not isinstance(effect, dict):
        return False
    convention = key.startswith(("td:interpret:", "td:interpret_if:", "td:val:"))
    if convention and (effect.get("kind") or effect.get("mode")) in ("blank", None):
        return False
    context = bool(effect.get("units") or effect.get("conditions") or effect.get("no_suffix") == "keep")
    omit_segments = requires_program and "map" in effect and effect["map"] is None
    if not (convention or context or omit_segments or (protected_question(key) and (effect.get("ok") or effect.get("applicable")))):
        return False
    if not quote:
        return convention or context or omit_segments
    plain = "".join(quote.split()).strip("。；;，,.")
    if not plain or not copied_request(task, quote):
        return False
    # Accept a pre-existing explicit review statement. A generic instruction is already in the job.
    if key.startswith("src:spec:"):
        explicit = ("本任务适用", "适用于本任务", "适用于本次", "已核对", "已核实")
    elif key.startswith("src:verify:"):
        explicit = ("属于本批", "本批原始记录", "已核对", "已核实")
    elif omit_segments:
        explicit = ("没有清单时留空", "缺少清单时留空", "未测项留空", "省略这些段")
    elif context:
        explicit = ("已核对单位", "实测单位为", "原始单位为", "实际工况为", "实际测试条件", "均在", "下测得")
    elif convention:
        explicit = ("填判定", "写判定", "按判定", "通过标志", "表示通过", "绝对值", "原始实测值", "带符号")
    else:
        explicit = ("已复核", "已核对", "已核实")
    return not any(word in plain for word in explicit)


def require_quote(quote, action):
    if not quote or not quote.strip():
        raise DIError('%s 需要 --user-quote "用户原话"；请先让用户本人确认。' % action)
    if "--defaults" in action:
        words = quote.casefold().strip()
        affirmative = re.search(r"(?:按|采用|使用|接受|同意|允许|选用|用)(?:全部|所有|本次|这些|其余|剩余的?|的|系统|程序|建议的?)*(?:默认|推荐)"
                                r"|\b(?:use|apply|accept|approve)\s+(?:(?:the|all|remaining)\s+)*(?:defaults?|recommended)\b", words)
        qualified = re.search(r"(?:不(?:要|能|可|许)?|拒绝|禁止|别|是否|不能确定|请先询问)[^。；;\n]{0,12}(?:默认|推荐)"
                              r"|\b(?:not|never|do not|don't|whether|ask if)\b[^.;\n]{0,28}\b(?:defaults?|recommended)\b", words)
        if not affirmative or qualified:
            raise DIError("按推荐项出稿须引用用户明确同意采用默认或推荐选项的原话；普通的出稿要求不足以确认待答问题。")


def answer_binding(answer):
    return digest([answer["effect"], answer.get("evidence"), answer.get("rule_sha256")])


def _chain(data):
    head = GENESIS
    chained = False
    events = []
    for line in data.splitlines():
        event = json.loads(line)
        if not (isinstance(event, dict) and isinstance(event.get("action"), str)
                and isinstance(event.get("user_quote"), str) and event["user_quote"].strip()):
            raise ReviewBlocked(BROKEN)
        if "hash" in event:
            expected = digest({k: v for k, v in event.items() if k != "hash"})
            if event.get("prev_hash") != head or event["hash"] != expected or event.get("source") not in SOURCES:
                raise ReviewBlocked(BROKEN)
            head = expected
            chained = True
        elif chained or "source" in event or "prev_hash" in event:
            raise ReviewBlocked(BROKEN)
        else:
            # Preserve the original bytes of legacy receipts. The first new
            # event commits to their ordered contents as a single prefix.
            head = digest([head, event])
        events.append(event)
    return events, head


def _append_bytes(path, data):
    with path.open("ab") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _event_line(event, prefix):
    separator = b"\n" if prefix and not prefix.endswith(b"\n") else b""
    return separator + json.dumps(event, ensure_ascii=False).encode("utf-8") + b"\n"


def _load(path, optional=False):
    """Caller holds the ledger lock. Finish interrupted appends before reading."""
    checkpoint = path.with_suffix(".head.json")
    try:
        state = read_json(checkpoint)
        if state is None and checkpoint.exists():
            raise ReviewBlocked(BROKEN)
        if not path.exists() and not optional and state is None:
            raise ReviewBlocked(BROKEN)
        data = path.read_bytes() if path.exists() else b""
        pending = state.get("pending") if state is not None else None
        prefix = data[:state["offset"]] if pending is not None else data
        events, head = _chain(prefix)
        if state is None:
            if events and "hash" in events[-1]:
                raise ReviewBlocked(BROKEN)
        elif state["hash"] != head:
            raise ReviewBlocked(BROKEN)
        if pending is not None:
            line = _event_line(pending, prefix)
            suffix = data[len(prefix):]
            if len(prefix) != state["offset"] or not line.startswith(suffix):
                raise ReviewBlocked(BROKEN)
            events, head = _chain(prefix + line)
            _append_bytes(path, line[len(suffix):])
            write_json(checkpoint, {"hash": head})
            data = prefix + line
        return events, head, data
    except (AttributeError, ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise ReviewBlocked(BROKEN) from exc


def ledger(path, optional=False):
    if not path.parent.is_dir():
        raise ReviewBlocked(BROKEN)
    with locked_preferences(path.parent, "decisions.lock"):
        events, head, _ = _load(path, optional)
        return events, head


def record_decision(job, action, quote, *, source="agent_relay", **details):
    require_quote(quote, action)
    path = Path(job.p("decisions.jsonl"))
    checkpoint = path.with_suffix(".head.json")
    with locked_preferences(path.parent, "decisions.lock"):
        _, head, data = _load(path, optional=True)
        event = {"time": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                 "action": action, "source": source, "user_quote": quote, "prev_hash": head, **details}
        event["hash"] = digest(event)
        write_json(checkpoint, {"hash": head, "offset": len(data), "pending": event})
        _append_bytes(path, _event_line(event, data))
        write_json(checkpoint, {"hash": event["hash"]})
        report = Path(job.p("审核报告.md"))
        if report.exists():
            lines = report.read_text("utf-8").split("\n")
            for i, line in enumerate(lines):
                if line.startswith(HEAD_LABEL):
                    lines[i] = HEAD_LABEL + event["hash"]
                    break
            else:
                lines.insert(2, HEAD_LABEL + event["hash"])
            with tempfile.NamedTemporaryFile(mode="w", dir=report.parent, encoding="utf-8", delete=False) as tmp:
                tmp.write("\n".join(lines))
            try:
                os.replace(tmp.name, report)
            finally:
                Path(tmp.name).unlink(missing_ok=True)


def _receipt(events, key, answer):
    for event in reversed(events):
        if event["user_quote"] != answer.get("user_quote"):
            continue
        if answer.get("word_draft"):
            fields = {"key": key, "option": answer["label"], "fp": answer["fp"],
                      "draft": answer["word_draft"], "author": answer.get("confirmed_by"),
                      "comment": answer.get("word_parent"), "reply": answer.get("word_reply")}
            if (event["action"] == "Word 核对" and event.get("source", "word_reply") == "word_reply"
                    and all(event.get(k) == v for k, v in fields.items())):
                if _binding_matches(event, answer):
                    return event
        else:
            interactive = answer.get("source") == "confirm_cli"
            action = "confirm" if interactive else ("answer --remember" if answer.get("remember") else "answer")
            if event["action"] != action or event.get("source", "agent_relay") != ("confirm_cli" if interactive else "agent_relay"):
                continue
            choices = event.get("answers")
            if isinstance(choices, list) and any(isinstance(c, dict) and c.get("key") == key
                    and c.get("answer") == answer.get("label") and c.get("fp") == answer.get("fp")
                    and _binding_matches(c, answer) for c in choices):
                return event


def _binding_matches(entry, answer):
    if "binding" in entry or answer.get("receipt_version"):
        return entry.get("binding") == answer_binding(answer)
    return True  # 1.1 receipts bound the original quote, question, option and input fingerprint.


def verify_receipts(job, draft, input_fp, require_signed=False):
    local = Path(job.p("decisions.jsonl"))
    required = [(local, key, key, answer) for key, answer in job.read("answers.json", {}).items()
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
        origin = Path(shared["origin_job"]) / "decisions.jsonl"
        if not origin.is_file():
            raise ReviewBlocked("复用决定的原任务记录缺失或已移动。请恢复原任务路径，或在本任务重新确认并生成草稿。")
        required.append((origin, shared.get("question", scope["question"]), scope["question"], shared["answer"]))
    exception = job.data.get("draft_exceptions", {}).get(draft)
    legacy_defaults = not exception and any(item.get("kind") == "default"
        for item in job.data.get("unverified", {}).get(draft, []))
    paths = {item[0] for item in required}
    if exception or legacy_defaults:
        paths.add(local)
    ledgers = {path: ledger(path)[0] for path in paths}
    if local not in paths:
        ledger(local, optional=True)
    audit = []
    for path, origin_key, key, answer in required:
        event = _receipt(ledgers[path], origin_key, answer)
        if not event:
            raise ReviewBlocked("决定记录与 %s 的用户原话、选项或输入版本不符，请核对后重新确认。" % key)
        if protected_question(key):
            source = event.get("source", "word_reply" if event["action"] == "Word 核对" else "agent_relay")
            audit.append({"key": key, "label": answer["label"], "source": source, "fp": answer["fp"],
                          "time": event.get("time", "旧记录未登记时间"), "author": event.get("author") or "未登记姓名"})
    if exception and not any(e["action"] == exception["action"] and e.get("draft") == draft and
                             digest(e["user_quote"]) == exception["quote_sha256"] for e in ledgers[local]):
        raise ReviewBlocked("草稿例外操作缺少对应的决定记录，请恢复原始记录或重新生成草稿。")
    if legacy_defaults and not any(e["action"] in ("draft --defaults", "draft --defaults --discard-edits")
                                   and e.get("draft") == draft for e in ledgers[local]):
        raise ReviewBlocked("旧草稿的默认选项缺少原始决定记录，请重新生成草稿并核对。")
    confirmed = {row["key"] for row in audit}
    for item in job.data.get("unverified", {}).get(draft, []):
        key = item.get("key", "")
        if protected_question(key) and key not in confirmed:
            audit.append({"key": key, "label": item.get("detail", ""), "source": "未确认",
                          "fp": input_fp or "", "time": "—", "author": "—"})
            confirmed.add(key)
    unsigned = [row["key"] for row in audit if row["source"] not in ("word_reply", "confirm_cli")]
    if unsigned and require_signed:
        raise ReviewBlocked("以下决定需要署名确认：" + "、".join(unsigned)
                            + "。请在草稿的 Word 核对批注下回复“确认 N”，或由本人在交互终端运行 confirm。")
    return audit


def signed_preference(job):
    try:
        value = read_json(project_dir(job) / "preferences.json", {}).get("require_signed", False)
    except (ValueError, AttributeError) as exc:
        raise DIError("请检查项目偏好 preferences.json 的 JSON 格式。") from exc
    if not isinstance(value, bool):
        raise DIError("项目偏好 require_signed 须为 true 或 false。")
    return value
