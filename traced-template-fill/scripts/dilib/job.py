# -*- coding: utf-8 -*-
"""任务目录管理。

<输出根目录>/                       默认 = 发起命令的工作目录/整合输出
  _缓存/                            解析结果与格式转换缓存（多任务共享，按内容哈希）
  <目标名>_<时间>/                  一个任务
    job.json                        任务信息（目标、源文件、任务描述、排版规范、输出格式）
    plan.json  questions.json  questions.md  answers.json  manual_ops.json
    草稿/<目标名>_草稿_v1.docx       带批注草稿（批注含可点击的原文链接）
    草稿/源文件快照/                 源文件副本（含定位书签），批注链接指向这里
    草稿/.原始/                      草稿生成时的原样副本（定稿时用于比对人工修改）
    终稿/<目标名>_终稿.docx (.doc/.pdf/.md)
    审核报告.md                      逐项来源、未填项、警告
"""
from __future__ import annotations

import datetime
import glob
import json
import os
import re
import zipfile

from . import __version__
from .common import DIError, ReviewBlocked, TOOL_AUTHOR, cache_hasher, ensure_dir, file_sha1, read_json, safe_name, write_json
from .content import Model, load_model

DOC_TYPES = {".docx", ".doc", ".docm", ".wps", ".rtf", ".odt", ".xlsx", ".xlsm", ".xls", ".et", ".csv", ".tsv", ".md",
             ".markdown", ".txt", ".pdf"}


class Job:
    def __init__(self, job_dir, *, data=None):
        self.dir = os.path.abspath(job_dir)
        if data is None:
            jf = os.path.join(self.dir, "job.json")
            if not os.path.exists(jf):
                raise DIError("不是任务目录（缺少 job.json）：%s" % job_dir)
            data = read_json(jf)
        self.data = data
        self.cache = self.data.get("cache") or os.path.join(os.path.dirname(self.dir), "_缓存")
        self._models = {}

    # ------------------------------------------------------------ 创建
    @classmethod
    def create(cls, target, sources, task="", out_root=None, name=None, profile=None, formats=None, test_rules=None,
               handoff_batch=None):
        target = os.path.abspath(target)
        if not os.path.isfile(target):
            raise DIError("请指定存在的目标文件：%s" % target)
        srcs = {}
        for s in sources:
            paths = [s] if os.path.exists(s) else sorted(glob.glob(s))
            if not paths:
                raise DIError("找不到匹配的源文件：%s" % s)
            for p in paths:
                p = os.path.abspath(p)
                if not os.path.exists(p):
                    raise DIError("源文件不存在：%s" % p)
                if os.path.isdir(p):
                    for f in sorted(os.listdir(p)):
                        fp = os.path.join(p, f)
                        if os.path.isfile(fp) and os.path.splitext(f)[1].lower() in DOC_TYPES and not f.startswith("~$"):
                            srcs.setdefault(os.path.normcase(fp), fp)
                elif not os.path.basename(p).startswith("~$"):
                    srcs.setdefault(os.path.normcase(p), p)
        srcs.pop(os.path.normcase(target), None)
        if not srcs:
            raise DIError("至少需要一个源文件（--source）")
        out_root = os.path.abspath(out_root or "整合输出")
        stem = os.path.splitext(os.path.basename(target))[0]
        base_name = safe_name(name or "%s_%s" % (stem, datetime.datetime.now().strftime("%Y%m%d-%H%M%S")))
        d = os.path.join(out_root, base_name)
        if name and os.path.exists(os.path.join(d, "job.json")):
            raise DIError("任务目录已存在：%s" % d)
        data = {"version": 1, "skill_version": __version__, "workspace": os.getcwd(), "created": datetime.datetime.now().isoformat(timespec="seconds"), "task": task or "",
                "target": {"path": target}, "sources": [{"alias": "S%d" % (i + 1), "path": p} for i, p in enumerate(srcs.values())],
                "profile": profile or "default", "formats": formats or [], "cache": os.path.join(out_root, "_缓存")}
        job = cls(d, data=data)
        if handoff_batch:
            job.data["handoff_batch"] = str(handoff_batch)
        if test_rules:
            from .testdata import load_rules
            job.data["rules"] = load_rules(test_rules)
            job.data["rules_source"] = os.path.abspath(test_rules)
        tm = job.target_model()
        job.data["template_comments"] = [[c.get("author", ""), c.get("text", "")] for c in tm.m.get("comments", [])]
        for a in job.data["sources"]:
            job.model(a["alias"])
        # Publish only after every input was read. Concurrent attempts must not replace a job.
        metadata = json.dumps(job.data, ensure_ascii=False, indent=1)
        index = 1
        while True:
            ensure_dir(job.dir)
            path = job.p("job.json")
            try:
                stream = open(path, "x", encoding="utf-8")
                break
            except FileExistsError:
                if name:
                    raise DIError("任务目录已存在：%s" % job.dir)
                index += 1
                job.dir = os.path.join(out_root, "%s_%d" % (base_name, index))
        try:
            with stream:
                stream.write(metadata)
        except OSError:
            os.unlink(path)
            raise
        return job

    def save(self):
        write_json(os.path.join(self.dir, "job.json"), self.data)

    # ------------------------------------------------------------ 模型
    def model(self, alias):
        if alias not in self._models:
            if alias == "T":
                p = self.data["target"]["path"]
            else:
                src = next((s for s in self.data["sources"] if s["alias"] == alias), None)
                if not src:
                    raise DIError("没有源别名 %s（可用：%s）" % (alias, ", ".join(s["alias"] for s in self.data["sources"])))
                p = src["path"]
            try:
                model = load_model(p, self.cache)
                if model.get("generated"):
                    if alias != "T":
                        raise ReviewBlocked("%s 是本工具生成的文档，不能作为来源。请提供原始规范或原始记录。" % p)
                    model["comments"] = [c for c in model["comments"] if c["author"] != TOOL_AUTHOR]
                    model.setdefault("read_warnings", []).append("模板为本工具已生成的文档；重填沿用正文并移除旧工具批注。")
                self._models[alias] = Model(model, alias)
            except zipfile.BadZipFile as exc:
                raise DIError("无法读取文件 %s：压缩结构已损坏或文件扩展名与内容不符" % p) from exc
        return self._models[alias]

    def target_model(self):
        return self.model("T")

    def source_models(self):
        return {s["alias"]: self.model(s["alias"]) for s in self.data["sources"]}

    def all_models(self):
        d = {"T": self.target_model()}
        d.update(self.source_models())
        return d

    # ------------------------------------------------------------ 路径
    def fingerprint(self, *, files_only=False):
        '''目标+全部源文件当前内容的指纹：回答只对当时的文件版本有效，文件被改过就失效。'''
        h = cache_hasher()
        for al, entry in [('T', self.data['target'])] + [(s['alias'], s) for s in self.data['sources']]:
            digest = file_sha1(entry['path']) if files_only else self.model(al).m['sha1']
            h.update((al + ':' + digest).encode('utf-8'))
        return h.hexdigest()[:12]

    def p(self, *parts):
        return os.path.join(self.dir, *parts)

    @property
    def stem(self):
        return os.path.splitext(os.path.basename(self.data["target"]["path"]))[0]

    def drafts(self):
        files = glob.glob(self.p("草稿", "%s_草稿_v*.docx" % glob.escape(self.stem)))
        def ver(f):
            m = re.search(r"_v(\d+)\.docx$", f)
            return int(m.group(1)) if m else 0
        return sorted(files, key=ver)

    def next_draft(self):
        ds = self.drafts()
        n = 1
        if ds:
            n = int(re.search(r"_v(\d+)\.docx$", ds[-1]).group(1)) + 1
        return self.p("草稿", "%s_草稿_v%d.docx" % (self.stem, n)), n

    def read(self, name, default=None):
        return read_json(self.p(name), default)

    def write(self, name, data):
        write_json(self.p(name), data)
