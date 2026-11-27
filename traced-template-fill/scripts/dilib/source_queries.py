"""Inspect source material and extract figures from an existing task."""
import os
import shutil

from . import office
from .common import DIError, ensure_dir, out, short
from .content import Model, load_model, outline, parse_ref, render_block, render_sheet, search
from .job import Job


def _job_or_file(path):
    if os.path.isdir(path):
        return Job(path), None
    return None, path


def cmd_outline(a):
    job, f = _job_or_file(a.path)
    if job:
        models = job.all_models() if not a.src else {a.src: job.model(a.src)}
        for al, md in models.items():
            if al == "T" and not a.src:
                from .target import analyze, summary
                out(summary(analyze(md)))
            else:
                out(outline(md, al, max_lines=a.lines, max_depth=a.depth, section=a.section))
    else:
        cache = os.path.abspath(os.path.join("整合输出", "_缓存"))
        out(outline(Model(load_model(f, cache), "F"), "F", max_lines=a.lines, max_depth=a.depth, section=a.section))


def cmd_show(a):
    import re
    from .content import render_table

    job = Job(a.job)
    refs = list(dict.fromkeys(parse_ref(value) for value in a.ref))
    if a.rows:
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", a.rows)
        if not match or (match[2] and int(match[2]) < int(match[1])):
            raise DIError("--rows 应为从 0 起的行号或行范围，例如 2 或 2-5")

    def render(md, ref):
        row = re.fullmatch(r"(T\d+)\.R(\d+)", ref)
        if row and md.get(row[1]) is not None:
            if int(row[2]) >= md.get(row[1])["nrows"]:
                raise DIError("找不到表格行 " + ref)
            return render_table(md, row[1], rows=row[2], full=a.full)
        if a.rows and md.get(ref) is not None and md.get(ref).get("t") == "tbl":
            return render_table(md, ref, rows=a.rows, full=a.full)
        return render_block(md, ref, full=a.full)

    for alias, ref in refs:
        md = job.model(alias)
        page_ref = re.fullmatch(r"page(\d+)", ref) if md.m["type"] == "pdf" else None
        if len(a.ref) > 1 or page_ref:
            out("[%s:%s]" % (alias, ref))
        if md.m["type"] == "xlsx":
            out(render_sheet(md, ref if ref != "*" else None, max_rows=a.rows_max))
        elif page_ref:
            page = int(page_ref[1])
            blocks = [b for b in md.m["blocks"] if b.get("page") == page]
            unread = [u for u in md.m.get("unread", []) if u.get("page") == page]
            if page < 1 or (not blocks and not unread and page > md.m.get("stats", {}).get("pages", 0)):
                raise DIError("%s 找不到第 %d 页" % (alias, page))
            for b in blocks:
                out("%s:%s" % (alias, render(md, b["id"])))
            for item in unread:
                out("未读取：" + item["why"])
            if not blocks and not unread:
                out("本页没有可显示的内容。")
        else:
            out(render(md, ref))


def cmd_search(a):
    job = Job(a.job)
    models = job.source_models() if not a.src else {a.src: job.model(a.src)}
    if a.target:
        models["T"] = job.target_model()
    res = search(models, a.query, top=a.top)
    if not res:
        out("未找到与“%s”相关的内容。可换个说法，或用 outline 查看提纲。" % a.query)
    for sc, al, bid, desc in res:
        out("%.2f  %s:%s  %s" % (sc, al, bid, desc))


def cmd_figure(a):
    job = Job(a.job)
    alias, ref = parse_ref(a.ref)
    md = job.model(alias)
    f = md.by.get(ref)
    if not f or "imgs" not in f:
        raise DIError("%s 不是图（F编号）" % a.ref)
    imgs = md.m.get("images", {})
    outdir = ensure_dir(job.p("图片导出"))
    pairs, outs = [], []
    for iid in f["imgs"]:
        info = imgs.get(iid, {})
        fp = info.get("file")
        if not fp:
            continue
        if info.get("ext") in ("emf", "wmf"):
            dst = os.path.join(outdir, "%s_%s_%s.png" % (alias, ref, iid))
            pairs.append((fp, dst))
            outs.append(dst)
        else:
            dst = os.path.join(outdir, "%s_%s_%s.%s" % (alias, ref, iid, info.get("ext")))
            shutil.copy2(fp, dst)
            outs.append(dst)
        if info.get("text"):
            out("%s 图中文字（矢量提取）：%s" % (iid, short(info["text"].replace("\n", " / "), 300)))
    if pairs:
        ok = office.metafile_to_png(pairs)
        for s, d in pairs:
            if d not in ok:
                out("  [注意] %s 转 PNG 失败（可直接打开原图 %s）" % (os.path.basename(d), s))
    out("图片已导出：\n  " + "\n  ".join(outs))
    out("（有视觉能力的模型可直接查看这些图片识别内容；矢量图的文字已在上方提取）")
