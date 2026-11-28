"""任务内部的人工决策记录。记录 CLI 收到的原话，不推断填写者的身份。"""
import datetime
import json

from .common import DIError


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
