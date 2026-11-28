"""Write one batch handoff for the recipient, preserving previous versions and human edits."""
import hashlib
import html
import os
from pathlib import Path
import re
from urllib.parse import quote


def markdown_text(value):
    text = html.escape(" ".join(str(value).split()), quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+.!|~>-])", r"\\\1", text)


def file_link(root, value):
    path = Path(value).resolve()
    try:
        url = quote(Path(os.path.relpath(path, root)).as_posix(), safe="/")
    except ValueError:
        url = path.as_uri()  # A template may be on a different Windows volume.
    return "[%s](%s)" % (markdown_text(path.name), url)


def save_exclusive(directory, stem, data):
    version = 1
    while True:
        name = stem + ("" if version == 1 else "_v%d" % version) + ".md"
        path = directory / name
        try:
            with path.open("xb") as stream:
                stream.write(data)
            return path
        except FileExistsError:
            if path.is_file() and path.stat().st_size == len(data) and path.read_bytes() == data:
                return path
            version += 1


def write_delivery(batch, jobs, results):
    from .workflow import review_summary
    directory = Path(jobs[0].dir).parent
    lines = ["# 批量草稿交付清单", ""]
    fills = holds = unverified = 0
    historical = False
    for job, row in zip(jobs, results):
        if "review_summary" not in row:
            report = job.read("plan.json", {}).get("report", {})
            unresolved = job.data.get("unverified", {}).get(Path(row["draft"]).name, [])
            row["review_summary"] = review_summary(report, unresolved)
        review = row["review_summary"]
        limited = review.get("coverage") == "registered_only"
        historical = historical or limited
        fills += review["measurement_fills"]
        holds += review["measurement_holds"]
        unverified += row.get("unverified", 0)
        lines.extend(["## %s · %s" % (row["id"], markdown_text(Path(row["template"]).name)), "",
                      "- 模板 " + file_link(directory, row["template"]),
                      "- 草稿 " + file_link(directory, row["draft"]),
                      "- 审核报告 " + file_link(directory, row["report"]),
                      "- 来源快照 " + file_link(directory, row["snapshots"]),
                      "- 实测已填 %d 格，留空 %d 格。" % (review["measurement_fills"], review["measurement_holds"])])
        if limited:
            lines.append("- 旧版计划仅能统计已登记的实测位置。")
        if review.get("measurement_retained"):
            lines.append("- 保留模板原有实测值 %d 格。" % review["measurement_retained"])
        for field, heading in (("unverified_reasons", "待核"), ("hold_reasons", "实测留空")):
            for item in review.get(field, []):
                lines.append("- %s（%d 处）%s" % (heading, item["count"], markdown_text(item["reason"])))
        if row.get("unverified") and not review.get("unverified_reasons"):
            lines.append("- 旧版登记有 %d 个未核项，明细请查本项目审核报告。" % row["unverified"])
        for warning in dict.fromkeys(row.get("warnings", [])):
            lines.append("- 提示：" + markdown_text(warning))
        lines.append("")
    totals = "共 %d 份草稿，实测已填 %d 格、留空 %d 格，待核 %d 项。" % (len(results), fills, holds, unverified)
    if historical:
        totals += "含旧版计划时，数量按已登记位置统计。"
    lines[2:2] = [totals, ""]
    batch_id = hashlib.sha256(str(batch.resolve()).encode("utf-8")).hexdigest()[:16]
    path = save_exclusive(directory, "批量交付清单_" + batch_id, "\n".join(lines).encode("utf-8"))
    return {"delivery_report": str(path), "message": totals + " 模板、草稿、报告、快照与待核原因见 " + path.name,
            "measurement_fills": fills, "measurement_holds": holds}
