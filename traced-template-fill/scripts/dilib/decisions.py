"""任务内部的人工决策记录。记录 CLI 收到的原话，不推断填写者的身份。"""
import datetime
import json
import os
import re
import tempfile
import unicodedata
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


def numbered_reply(words, refs, choice, multiple=False):
    selected = re.findall(r"(?<![a-z0-9])([qb]\d+)\s*[=:选]\s*([0-9]+)(?![0-9])", words)
    if selected:
        values = {v for ref, v in selected if ref.upper() in refs}
        if values:
            return (choice is not None and values == {str(choice)}
                    and not re.search(r"是否|暂不|不要|如果|比如|例如|示例|[?？]|\b(?:whether|if|not|example)\b", words))
    ordinal = re.fullmatch(r"((?:两项|两题|以上|全部)?(?:均|都)|both\s+|all\s+)?(?:我?选(?:择)?\s*|option\s*|choice\s*)?第?([1-9一二三四五六七八九])(?:项|个选项|个)?", words.strip("。.! "))
    if ordinal:
        s = ordinal[2]
        return (not multiple or bool(ordinal[1])) and choice == (int(s) if s.isdecimal() else "一二三四五六七八九".index(s) + 1)
    return None


def source_confirmation(key, quote, refs, choice, multiple):
    """Check what the quoted reply affirms for this question. Provenance remains agent_relay."""
    words = unicodedata.normalize("NFKC", quote or "").casefold().strip()
    short = words.strip(" \t\r\n.。!！,，")
    numbered = numbered_reply(words, refs, choice, multiple)
    if numbered is not None:
        return numbered
    if re.fullmatch(r"(?:yes[, ]+)?(?:both|all)(?: (?:are )?(?:confirmed|correct|verified|applicable))?", short):
        return True
    if re.fullmatch(r"(?:(?:我|两项|两题|两份|两者|这两项|以上|全部)(?:都|均)?|都)?(?:是的?|对的?|没错|没问题|可以|同意|"
                    r"确认|已(?:核对|核实|确认|复核)(?:过|了)?|(?:再次|第[二三]次)核实)|"
                    r"(?:yes|yeah|yep|correct|ok(?:ay)?|confirmed|verified|agreed|i (?:agree|confirm))(?:,? (?:both|please proceed))?", short):
        return not multiple or bool(re.search(r"都|均|两项|两题|两份|两者|全部|以上|\b(?:both|all)\b", short))

    spec = key.startswith("src:spec:")
    subject = (r"规范|规格|标准|细则|\bspec(?:ification)?s?\b|\bstandard\b" if spec else
               r"本批|这批|该批|同批|批号|批次|样品|原始记录|实测记录|记录(?:都|均)?已|来源|数据表|读数表|\.xlsx?\b|"
               r"\b(?:batch|lot|samples?|spreadsheet|workbook|source (?:records?|data)|measurements?)\b")
    reviewed = r"(?:已经?|确已|经|本人|我|均已|都已)(?:逐份|逐项|认真|再次)?(?:核对|核实|确认|核查|复核|核验)|核(?:实|对)?过|核对(?:了|无误)|已核|\b(?:checked|verified|confirmed|validated)\b"
    finding = (r"(?:适用|适合|可以使用|可用于)|\b(?:appl(?:y|ies|icable)|suitable|valid for)\b" if spec else
               r"(?:属于|系|确为|确属|就是|是|来自|取自|对应|匹配|一致).{0,12}(?:本批|这批|该批|同批|同一批|同一组)|"
               r"(?:本批|这批|该批|同批)(?:的)?(?:原始|实测|采集|测试|数据|记录|样品)|(?:确认|核实)(?:为)?本批|"
               r"\b(?:belong(?:s)? to|(?:is|are|comes?|came) from|(?:is|are) (?:for|part of)|matches?|same (?:batch|lot))\b")
    uncertain = (r"是否|能否|(?:(?:不|未|尚未|无法|不能|还没|没有|待)(?:能|曾|经|敢|予以)?(?:确定|确认|核实|核对|适用|属于|是|对应|匹配))|"
                 r"不属|不同批|[?？]|\b(?:not|never|unverified|unknown|uncertain|whether|isn't|aren't|doesn't|cannot)\b")
    instruction = r"^(?:(?:请|麻烦|需要|务必|先|要|待|将|把|根据|按照)|(?:please|check|verify|confirm whether|use|copy|fill|populate)\b)"
    clauses = re.split(r"[。;；,，\n]|\.\s+|(?:但是|不过|然而|\bbut\b)|(?:并|再|进一步)(?=将|把)", words)
    direct_finding = r"(?:本任务|本次|本批)?适用|applicable|it applies"
    affirmed = spec and any(re.fullmatch(direct_finding, c.strip()) for c in clauses)
    for clause in (s.strip() for s in clauses):
        if not clause or re.search(instruction, clause) or not re.search(subject, clause):
            continue
        if re.search(uncertain, clause):
            return False
        if re.search(reviewed, clause) or re.search(finding, clause.replace("适用范围", "")):
            affirmed = True
    if affirmed:
        return True
    # Common direct replies omit the subject supplied by the displayed question.
    if spec:
        return bool(re.fullmatch(r"(?:(?:本任务|本次|本批|可以)?适用|适用于(?:本任务|本次|本批)|applicable|it applies)", short))
    return bool(re.fullmatch(r"(?:同一批(?:次)?|本批(?:适用|无误)|属于本批|(?:我|本人)?核过这份[：:].{1,15}对应|same (?:batch|lot))", short))


VALUE_QUESTIONS = ("td:interpret:", "td:interpret_if:", "td:val:")


def value_terms(text):
    terms = set()
    for part in re.split(r"[。;；,，\n]|\.\s+", unicodedata.normalize("NFKC", text or "").casefold()):
        if re.search(r"是否|未核|待核|不能确认|不确定|不要|并非|不是|[?？]|\b(?:not|unknown|whether)\b", part):
            continue
        for digit, result in re.findall(r"(?<!\d)([01])\s*(?:均|都)?(?:表示|代表|为|是|即|对应|填|=|means?|is|indicates?)\s*[“\"']?"
                                       r"(通过|合格|成功|失败|不通过|不合格|pass(?:ed)?|fail(?:ed)?)", part):
            passed = result in ("通过", "合格", "成功", "pass", "passed")
            terms.add("pass%d" % (int(digit) if passed else 1-int(digit)))
        if re.search(r"实测(?:读数|数值|值)|原始(?:测量|实测|读数|数值)|测量(?:数值|值)|原样(?:填|记录)|"
                     r"\b(?:raw|measured|measurement) (?:values?|readings?|numbers?|measurements?)\b", part):
            terms.add("measurement")
        if re.search(r"相反极性|按负值|取负|取反|反转极性|绝对值|\b(?:negat(?:e|ed)|invert|absolute value)\b", part):
            terms.add("negate")
        if re.search(r"带符号|保留.{0,5}(?:符号|正负)|原(?:始)?符号|\bsigned\b", part):
            terms.add("signed")
        if re.search(r"(?:填|记|写|做|算)(?:写)?判定|与限值比较|按限值|\b(?:judg(?:e)?ment|compare.{1,12}limits?)\b", part):
            terms.add("limits")
    return terms


def value_confirmation(effect, quote, refs, choice):
    mode = effect.get("kind") or effect.get("mode")
    if mode == "blank":
        return True
    words = unicodedata.normalize("NFKC", quote or "").strip().casefold()
    numbered = numbered_reply(words, refs, choice)
    if numbered is not None:
        return numbered
    if effect.get("free"):
        required = value_terms(effect.get("text"))
    elif mode == "flags":
        required = {"pass%s" % effect.get("pass_code")}
    elif mode == "limits":
        required = {"limits", effect.get("sign")}
    elif mode in ("raw", "measurement"):
        required = {"measurement"}
    elif mode == "negate":
        required = {"negate"}
    else:
        return False
    confirmed = value_terms(words)
    for code in ("pass0", "pass1"):
        if code in required and ({'pass0', 'pass1'} - {code}) & confirmed:
            return False
    return bool(required) and required <= confirmed


def value_option(options, text):
    hold = text.strip("。.! ").casefold() in ("留空", "暂留空", "先留空", "跳过", "不填", "leave blank", "skip")
    matches = []
    for i, opt in enumerate(options):
        effect = opt["effect"]
        blank = (effect.get("mode") or effect.get("kind")) == "blank"
        if hold == blank and value_confirmation(effect, text, (), None):
            matches.append(i)
    return matches[0] if len(matches) == 1 else None


def stored_reply_needed(key, rec, task, question_id):
    if not isinstance(rec, dict) or not key.startswith((*VALUE_QUESTIONS, "src:spec:", "src:verify:")):
        return False
    if rec.get("source") in ("confirm_cli", "word_reply") or rec.get("word_draft") or rec.get("evidence"):
        return False
    if key.startswith(VALUE_QUESTIONS) and isinstance(rec.get("effect"), dict) and rec["effect"].get("free"):
        return True
    indices = rec.get("choice") or []
    choice = indices[0] + 1 if len(indices) == 1 and isinstance(indices[0], int) else None
    return needs_reviewer_reply(key, rec.get("effect"), task, rec.get("user_quote"),
                                refs=(question_id, rec.get("quoted_id")), choice=choice)


def needs_reviewer_reply(key, effect, task, quote, *, requires_program=False, refs=(), choice=None, multiple=False):
    """A request to fill a form does not attest to the identities or conventions subsequently queried."""
    if not isinstance(effect, dict):
        return False
    if ((key.startswith("src:spec:") and effect.get("applicable") is True)
            or (key.startswith("src:verify:") and effect.get("ok") is True)):
        return not source_confirmation(key, quote, refs, choice, multiple)
    if key.startswith(VALUE_QUESTIONS):
        return not value_confirmation(effect, quote, refs, choice)
    context = bool(effect.get("units") or effect.get("conditions") or effect.get("no_suffix") == "keep")
    omit_segments = requires_program and "map" in effect and effect["map"] is None
    if not (context or omit_segments or (protected_question(key) and (effect.get("ok") or effect.get("applicable")))):
        return False
    if not quote:
        return context or omit_segments
    plain = "".join(quote.split()).strip("。；;，,.")
    if not plain or not copied_request(task, quote):
        return False
    # Accept a pre-existing explicit review statement. A generic instruction is already in the job.
    if omit_segments:
        explicit = ("没有清单时留空", "缺少清单时留空", "未测项留空", "省略这些段")
    elif context:
        explicit = ("已核对单位", "实测单位为", "原始单位为", "实际工况为", "实际测试条件", "均在", "下测得")
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
