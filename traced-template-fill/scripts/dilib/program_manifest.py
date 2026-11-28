"""Read a test-program manifest and bind its declarations to the selected measurement sheet."""
import hashlib
import io
from pathlib import Path
import re
from .engineering import unit_key
import zipfile
from xml.etree.ElementTree import ParseError

from .common import DIError, norm_text
from .ooxml import validate_package_limits
from .read_other import _read_xlsx
from .testdata import norm_sym, unit_exponent


LABEL_HEADER = re.compile(r"测试名[（(]([^()（）/\\]+?\.xlsx)\s+([A-Z]+)\s*列标签[）)]", re.I)
SPAN = re.compile(r"第\s*(\d+)\s*(?:[~～\-–至到]\s*第?\s*(\d+)\s*)?行")


def workbook_rows(path, job):
    try:
        raw = Path(path).read_bytes()
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            validate_package_limits(archive)
            book = _read_xlsx(str(path), archive)
    except (OSError, ValueError, IndexError, KeyError, ParseError, zipfile.BadZipFile) as exc:
        raise DIError("无法读取测试程序工作簿：%s" % exc) from exc
    candidates = []
    for sheet in book["sheets"]:
        visible = [row for row in sheet["rows"] if any(cell is not None and cell != "" for cell in row)]
        if visible and "测试号" in visible[0]:
            candidates.append((sheet["name"], visible))
    if len(candidates) != 1:
        raise DIError("程序清单必须有一个带“测试号”表头的可见工作表。")
    sheet_name, values = candidates[0]
    if any(issue["location"].split("!", 1)[0] == sheet_name for issue in book["unread"]):
        raise DIError("程序清单含缺少缓存的公式或未能读取的内容。")
    headers = [str(h or "").strip() for h in values[0]]
    bound = [m for h in headers if (m := LABEL_HEADER.fullmatch(h))]
    binding = None
    if bound:
        if len(bound) != 1:
            raise DIError("测试程序清单的标签来源有歧义。")
        match = bound[0]
        headers[headers.index(match[0])] = "标签"
        matches = [s for s in (job.data["sources"] if job else [])
                   if Path(s["path"]).name.casefold() == match[1].strip().casefold()]
        aliases = {s["alias"] for s in matches}
        inferred = None
        if "来源" not in headers:
            if len(matches) != 1:
                raise DIError("清单所指工作簿缺失或重名，请在清单“来源”列写明 S别名:工作表。")
            alias = matches[0]["alias"]
            sheets = job.model(alias).m.get("sheets", [])
            if len(sheets) != 1:
                raise DIError("源工作簿有多个工作表，请在清单“来源”列明确指定。")
            inferred = alias + ":" + sheets[0]["name"]
        binding = {"inferred": inferred, "aliases": sorted(aliases), "column": match[2].upper()}
    if len(set(headers)) != len(headers) or not all(headers):
        raise DIError("程序清单表头为空或重名。")
    rows = [{key: "" if i >= len(row) or row[i] is None else str(row[i]).strip()
             for i, key in enumerate(headers)} for row in values[1:]]
    if binding and binding["inferred"]:
        for row in rows:
            row["来源"] = binding["inferred"]
    return rows, hashlib.sha256(raw).hexdigest(), binding


def same_symbol(value, candidates):
    matches = [s for s in candidates if norm_sym(s) == norm_sym(value)]
    if len(matches) != 1:
        raise DIError("清单中的参数 %s 与当前待核参数无法唯一对应。" % value)
    return matches[0]


def declared_span(row):
    start, end = row.get("首行", ""), row.get("末行", "")
    if start or end:
        if not start.isdecimal() or not end.isdecimal():
            raise DIError("段的首行和末行须成对提供正整数。")
        return int(start), int(end)
    matches = SPAN.findall(row.get("备注", ""))
    if len(matches) > 1:
        raise DIError("同一段有多个行号范围。")
    if matches:
        first, last = matches[0]
        return int(first), int(last or first)
    return None


def segment_map(q, rows, base_names=True):
    labels = q.get("source_labels") or [q["source_label"]]
    selected = [r for r in rows if r["标签"] in labels]
    if not selected and base_names:
        # Original manifests identify the whole group by its base name.
        selected = [r for r in rows if r["标签"] == q["source_label"]]
    if not selected or len({r["标签"] for r in selected}) != 1:
        raise DIError("清单中没有唯一匹配的标签与工况。")
    if {r["段序"] for r in selected} != {str(i) for i in range(1, len(selected) + 1)}:
        raise DIError("段序必须从 1 起连续且唯一。")
    ordered = sorted(selected, key=lambda r: int(r["段序"]))
    segments, symbols = [], []
    for row in ordered:
        sym = same_symbol(row["参数"], q["parameter_candidates"])
        unit = q.get("parameter_units", {}).get(sym)
        if unit and unit_exponent(row["单位"], unit) is None:
            raise DIError("参数 %s 的清单单位与规范不符。" % sym)
        symbols.append(sym)
        span = declared_span(row)
        if span:
            first, last = span
            expect = segments[-1]["end"] + 1 if segments else 1
            if first != expect or last < first:
                raise DIError("连续块的行段有重叠、缺口或倒序。")
            segments.append({"symbol": sym, "start": first, "end": last})
    if segments:
        if len(segments) != len(ordered) or q.get("block_lengths") != [segments[-1]["end"]]:
            raise DIError("声明的行段未完整覆盖当前标签的单一连续块。")
        return {"segments": segments}
    if len(ordered) != q["segment_count"]:
        raise DIError("声明的段数与工作表不符，请给出连续块内各段起止行。")
    return {"map": symbols}


def actual_values(q, rows, kind):
    col = "单位" if kind == "units" else "条件"
    expected = q["options"][-1]["effect"][kind]
    verified = {}
    for sym, target in expected.items():
        labels = q.get("labels_by_symbol", {}).get(sym)
        matching = [r for r in rows if norm_sym(r["参数"]) == norm_sym(sym) and
                    (labels is None or r.get("标签") in labels)]
        if (not matching or any(not r[col] for r in matching)
                or labels and set(labels) != {r.get("标签") for r in matching}):
            raise DIError("缺少参数 %s 的%s。" % (sym, col))
        if kind == "units":
            choices = {unit_key(r[col]) for r in matching}
            if len(choices) != 1 or unit_exponent(next(iter(choices)), target) is None:
                raise DIError("参数 %s 的声明单位不唯一或与要求量纲不符。" % sym)
            source = matching[0][col]
            verified[sym] = target if unit_exponent(source, target) == 0 else source
        else:
            choices = {r[col] for r in matching}
            if len(choices) != 1 or choices & {"—", "-", "未知"}:
                raise DIError("参数 %s 的清单工况有歧义或未提供。" % sym)
            verified[sym] = choices.pop()
    return {kind: verified}


def verify_no_suffix(q, rows):
    allowed = {norm_text(term) for cond in q["conditions"] for term in [cond, *q["condition_names"].get(cond, [])]}
    selected_suffixes = {"_" + c.casefold() for c in q["conditions"]}
    # Accept an explicit supply voltage when all rows for the selected suffix attest to one voltage.
    selected = {norm_text(r["条件"]) for r in rows if r["标签"].casefold().endswith(tuple(selected_suffixes))}
    if len(selected) == 1:
        voltage = next(iter(selected))
        if re.fullmatch(r"v(?:dd|cc)=[+-]?\d+(?:\.\d+)?v", voltage):
            allowed.add(voltage)
    for label in q["source_labels"]:
        measurements = [r for r in rows if r["标签"] == label]
        if not measurements or any(norm_text(r["条件"]) not in allowed for r in measurements):
            raise DIError("标签 %s 缺少所选工况的证据。" % label)
    return {"no_suffix": "keep"}


REQUIRED = {"map": {"标签", "段序", "参数", "单位"}, "no_suffix": {"标签", "条件"},
            "units": {"参数", "单位"}, "conditions": {"参数", "条件"}}


def effect(q, rows, binding):
    kind = q["key"].split(":")[1] if q["key"].startswith("td:") else ""
    if kind not in REQUIRED:
        raise DIError("该问题不支持测试程序清单回答。")
    needed = REQUIRED[kind] | {"来源", "测试号"}
    if not needed <= rows[0].keys():
        raise DIError("程序清单需要列：" + "、".join(sorted(needed)))
    scope = q["source_scope"]
    if binding and (scope.partition(":")[0] not in binding["aliases"] or
                    q.get("source_label_col") != binding["column"]):
        raise DIError("清单所指工作簿、工作表或标签列与当前问题不一致。")
    selected = [r for r in rows if r["来源"] == scope]
    if not selected or any(not r["测试号"].strip() for r in selected):
        raise DIError("程序清单缺少当前来源或明确的测试号。")
    if kind == "map":
        return segment_map(q, selected, base_names=binding is None)
    if kind == "no_suffix":
        return verify_no_suffix(q, selected)
    return actual_values(q, selected, kind)

