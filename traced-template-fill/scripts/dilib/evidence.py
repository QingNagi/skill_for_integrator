"""将取值前提、人工决定与对应文件版本一起登记，并支持可回放的离线确认。"""
import csv
import hashlib
import io
import json
from pathlib import Path

from .common import DIError, read_json, write_json
from .preferences import locked_preferences, project_dir

RULES = Path(__file__).resolve().parents[2] / "config" / "rules"
TYPES = {
    "src:verify": "批次身份", "src:spec": "规范适用", "td:source": "批次身份", "td:sheet": "批次身份",
    "td:samplemap": "样品对应", "td:duplicate": "样品对应", "td:no_suffix": "测试条件", "td:cond": "测试条件",
    "td:conditions": "测试条件", "td:map": "参数归属", "td:alias": "参数归属", "td:merge": "参数归属",
    "td:val": "值类型与极性", "td:interpret": "值类型与极性", "td:interpret_if": "值类型与极性", "td:units": "值类型与极性", "td:multi": "值类型与极性",
}
FIELDS = ["位置", "参数", "样品", "候选值", "原始单元格", "缺少的证据", "决定", "备注", "问题", "有效选项", "版本"]


def digest(obj):
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, separators=(",", ":"),
                                     sort_keys=True).encode("utf-8")).hexdigest()


def rule_pack(job):
    files = {name: hashlib.sha256((RULES / name).read_bytes()).hexdigest() for name in ("aliases.json", "test-data.json")}
    return {"version": read_json(RULES / "test-data.json")["version"],
            "content_sha256": digest([files, job.data.get("rules", {})]),
            "sha256": digest([files, job.data.get("rules", {}), job.data.get("task", "")])}


def decision_digest(job, effective_defaults=None):
    # Referenced shared decisions are checked against their current project records, including deletions.
    shared = job.read("facts.json", {}).get("shared", [])
    folder = project_dir(job) / "facts"
    effective = {key: read_json(folder / (key + ".json"), None) for key in shared}
    effects = dict(effective_defaults or {})
    attachments = {}
    for key, answer in job.read("answers.json", {}).items():
        if key.startswith("fullread:"):
            continue
        if isinstance(answer, dict) and "effect" in answer:
            effects[key] = answer["effect"]
            if answer.get("evidence"):
                attachments[key] = answer["evidence"].get("sha256")
    manifest = job.read("answers.json", {}).get("_program_manifest")
    payload = [rule_pack(job), effects, attachments, job.read("manual_ops.json", []), effective]
    if manifest:
        payload.append(digest(manifest))
    return digest(payload)


def prefix(q):
    return ":".join(q["key"].split(":")[:2])


def shared_key(job, q, pack):
    rules = job.data.get("rules", {})
    if not rules.get("product") or not rules.get("program_version") or prefix(q) not in ("td:map", "td:val", "td:interpret", "src:spec"):
        return None
    # The same ordinal Q number, filename or three-word label does not identify a program.
    # Bind reusable decisions to the schema, the specification and (for column conventions) the template.
    signature = {"type": prefix(q), "options": [o["effect"] for o in q["options"]],
                 "label": q.get("source_label"), "segments": q.get("segment_count"),
                 "block_lengths": q.get("block_lengths"), "source_labels": q.get("source_labels"),
                 "candidates": q.get("parameter_candidates"), "units": q.get("parameter_units"),
                 "key": q["key"].split(":S", 1)[0], "context": q.get("value_context"), "source_models": q.get("source_models")}
    if prefix(q) in ("td:val", "td:interpret"):
        signature["template"] = job.target_model().m["sha1"]
    if prefix(q) == "src:spec":
        signature["specifications"] = {alias: md.m["sha1"] for alias, md in job.source_models().items()
                                       if md.m["type"] != "xlsx"}
    return digest([rules["product"], rules["program_version"], pack["content_sha256"], signature])


def reusable_answers(job, questions):
    folder = project_dir(job) / "facts"
    reusable = {}
    if not folder.is_dir():
        return reusable
    pack = rule_pack(job)
    for q in questions:
        key = shared_key(job, q, pack)
        record = read_json(folder / (key + ".json"), {}) if key else {}
        if record and record.get("origin_job") == Path(job.dir).resolve().as_posix():
            original = record["answer"]
            if original.get("fp") != job.fingerprint() or original.get("rule_sha256", pack["sha256"]) != pack["sha256"]:
                continue
        if record and record.get("scope") == key:
            reusable[q["key"]] = dict(record["answer"], shared_key=key, remember=False)
    return reusable


def save_plan_facts(job, plan, answers, manifest=None):
    report = plan["report"]
    pack = rule_pack(job)
    report["rule_pack"] = pack
    if job.data.get("rule_pack") != pack:
        job.data["rule_pack"] = pack
        job.save()
    records, by_key, shared = {}, {}, set()
    previous = job.read("facts.json", {})
    published = previous.get("published", {})
    qs = {q["key"]: q for q in plan.get("questions", [])}

    def add(record):
        fid = "F-" + digest(record)[:24]
        records[fid] = dict(record, id=fid)
        return fid

    if manifest:
        for row in manifest["rows"]:
            add({"type": "程序清单声明", "scope": {"source": row.get("来源"), "test": row.get("测试号"),
                 "input_fp": manifest["fp"], "rule_sha256": manifest["rule_sha256"]},
                 "value": row, "evidence": {"file": manifest["file"], "sha256": manifest["sha256"]}})
    for key, answer in sorted(answers.items()):
        if not isinstance(answer, dict) or answer.get("default") or "effect" not in answer:
            continue
        q = qs.get(key, {"key": key, "options": []})
        typ = TYPES.get(prefix(q), "人工决定")
        source = answer.get("evidence") or {"question": key, "quote": answer.get("user_quote"), "label": answer.get("label")}
        by_key[key] = add({"type": typ, "scope": {"question": key, "input_fp": answer.get("fp"),
                               "shared": answer.get("shared_key"), "rule_sha256": answer.get("rule_sha256")}, "value": answer["effect"], "evidence": source,
                           "confirmed_by": answer.get("confirmed_by"), "time": answer.get("answered_at")})
        if answer.get("shared_key"):
            shared.add(answer["shared_key"])
        elif answer.get("remember") and key in qs:
            skey = shared_key(job, q, pack)
            if skey and published.get(skey) != by_key[key]:
                folder = project_dir(job) / "facts"
                with locked_preferences(folder):
                    write_json(folder / (skey + ".json"), {"scope": skey, "origin_job": Path(job.dir).resolve().as_posix(),
                                                          "question": key, "answer": answer})
                    published[skey] = by_key[key]

    for entry in report.get("fills", []):
        if "!" not in entry.get("source", ""):
            continue
        ids = [by_key[k] for k in entry.pop("decision_keys", []) if k in by_key]
        for typ, fact in entry.pop("observations", {}).items():
            ids.append(add({"type": typ, "scope": fact["scope"], "value": fact["value"],
                            "evidence": fact["evidence"], "confirmed_by": None, "time": job.data.get("created")}))
        entry["fact_ids"] = list(dict.fromkeys(ids))
    store = {"schema": 1, "rule_pack": pack, "facts": list(records.values()), "shared": sorted(shared), "published": published}
    archive = {r["id"]: r for r in previous.get("history", []) + previous.get("facts", []) if r["id"] not in records}
    store["history"] = list(archive.values())
    job.write("facts.json", store)
    report["facts"] = store["facts"]


def read_csv_evidence(path):
    try:
        raw = Path(path).read_bytes()
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise DIError("证据 CSV 编码须为 UTF-8 或 GB18030。")
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise DIError("CSV 表头缺失或有重名列。")
        rows = list(reader)
        if any(None in row or None in row.values() for row in rows):
            raise DIError("CSV 行与表头列数不同。")
        return rows, hashlib.sha256(raw).hexdigest()
    except (OSError, csv.Error) as exc:
        raise DIError("无法读取证据 CSV：%s" % exc) from exc


def program_answers(qs, items, path, job=None):
    from . import program_manifest

    ext = Path(path).suffix.casefold()
    binding = None
    if ext == ".xlsx":
        rows, sha, binding = program_manifest.workbook_rows(path, job)
    elif ext == ".csv":
        rows, sha = read_csv_evidence(path)
    else:
        raise DIError("测试程序清单须为 CSV 或 XLSX。")
    if not rows:
        raise DIError("测试程序清单没有测试项。")
    by_id = {q["id"]: q for q in qs}
    effects, answers = {}, []
    for item in items:
        if "=" in item:
            answers.append(item)
            continue
        qid = item.strip().upper()
        if qid not in by_id:
            raise DIError("请指定当前待回答的问题编号（例如 Q1）。")
        effects[qid] = program_manifest.effect(by_id[qid], rows, binding)
        answers.append(qid + "=测试程序清单")
    if not {"来源", "测试号"} <= rows[0].keys() or not ({"单位", "条件", "段序"} & rows[0].keys()):
        raise DIError("程序清单须注明来源、测试号及单位、条件或段序。")
    return answers, {"file": Path(path).name, "sha256": sha, "effects": effects, "rows": rows, "binding": binding}


def manifest_answers(manifest, questions):
    """Resolve only declarations supported for the current sheet, labels and target."""
    from . import program_manifest

    resolved = {}
    for q in questions:
        if prefix(q) not in {"td:units", "td:conditions", "td:map", "td:no_suffix"}:
            continue
        key = q["key"]
        kind = prefix(q).split(":")[1]
        if kind in ("units", "conditions"):
            expected = q["options"][-1]["effect"][kind]
            supported = {}
            for symbol, value in expected.items():
                probe = dict(q, options=[{"effect": {kind: {symbol: value}}}])
                try:
                    result = program_manifest.effect(probe, manifest["rows"], manifest["binding"])
                except DIError:
                    continue
                supported.update(result[kind])
            if not supported:
                continue
            result = {kind: supported}
            if supported.keys() != expected.keys():
                # Only the declared subset is answered. The next plan asks about the residual.
                _, _, _, scope = key.split(":", 3)
                key = "td:%s:manifest-%s:%s" % (kind, digest([key, supported])[:16], scope)
        else:
            try:
                result = program_manifest.effect(q, manifest["rows"], manifest["binding"])
            except DIError:
                continue
        resolved[key] = {"effect": result, "label": "测试程序清单",
                              "fp": manifest["fp"], "rule_sha256": manifest["rule_sha256"],
                              "evidence": {"file": manifest["file"], "sha256": manifest["sha256"]}}
    return resolved


def csv_safe(value):
    s = str(value or "")
    return "'" + s if s.lstrip().startswith(("=", "+", "-", "@")) or s.startswith(("\t", "\r")) else s


def review_rows(job, qs):
    rows = []
    pack = rule_pack(job)
    for q in qs:
        if q.get("answered"):
            continue
        sig = digest([Path(job.dir).resolve().as_posix(), q.get("input_fp"), q["key"], q["options"], q.get("affected_cells", []), pack])
        options = " / ".join("%d %s" % (i, o["label"]) for i, o in enumerate(q["options"], 1))
        for c in q.get("affected_cells") or [{}]:
            values = [c.get("target"), c.get("symbol"), c.get("sample"), c.get("candidate"), c.get("source"),
                      q["topic"], "", "", q["id"], options, sig]
            rows.append(dict(zip(FIELDS, map(csv_safe, values))))
    return rows


def export_review_sheet(job, plan, path):
    with open(path, "w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(review_rows(job, plan["questions"]))


def sheet_submission(job, qs, path):
    rows, sha = read_csv_evidence(path)
    expected = {(r["问题"], r["位置"]): r for r in review_rows(job, qs)}
    by_id = {q["id"]: q for q in qs}
    chosen, seen, answered, notes = {}, set(), set(), {}
    for row in rows:
        key = row.get("问题"), row.get("位置")
        ref = expected.get(key)
        if key in seen or not ref or set(row) != set(FIELDS) or any(row[k] != ref[k] for k in FIELDS if k not in ("决定", "备注")):
            raise DIError("清单与当前问题、位置或依据版本不一致，请重新导出。")
        seen.add(key)
        decision = row["决定"].strip()
        if not decision:
            continue
        qid = row["问题"]
        options = by_id[qid]["options"]
        if decision.isdecimal():
            num = int(decision)
        else:
            opts = [i for i, o in enumerate(options, 1) if o["label"] == decision.removeprefix("改为").strip()]
            if not opts and decision in ("留空", "采用"):
                negatives = [i for i, o in enumerate(options, 1) if any(w in o["label"] for w in ("留空", "缺少", "暂未", "未核对", "暂不", "省略"))]
                opts = negatives if decision == "留空" else [i for i in range(1, len(options) + 1) if i not in negatives]
            if len(opts) != 1:
                raise DIError("%s 请填写有效选项编号或完整选项文字。" % qid)
            num = opts[0]
        if not 1 <= num <= len(options) or (qid in chosen and chosen[qid] != num):
            raise DIError("%s 超出选项范围，或同一决定在多个位置有冲突。" % qid)
        chosen[qid] = num
        answered.add(key)
        if row["备注"].strip():
            notes.setdefault(qid, []).append({"target": row["位置"], "note": row["备注"]})
    for qid, pos in expected:
        if qid in chosen and (qid, pos) not in answered:
            raise DIError("%s 的决定覆盖整组，请核对每个位置并填写同一决定。"% qid)
    metadata = {qid: {"file": Path(path).name, "sha256": sha, "notes": notes.get(qid, [])} for qid in chosen}
    return ["%s=%d" % item for item in chosen.items()], metadata


def sheet_answers(job, qs, path):
    return sheet_submission(job, qs, path)[0]
