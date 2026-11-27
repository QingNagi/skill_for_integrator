# -*- coding: utf-8 -*-
"""任务目录管理。

<输出根目录>/                       默认 = 目标文件所在目录/整合输出
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
import os
import re

from .common import DIError, cache_hasher, ensure_dir, read_json, safe_name, write_json
from .content import Model, load_model

DOC_TYPES = {".docx", ".doc", ".docm", ".wps", ".rtf", ".odt", ".xlsx", ".xlsm", ".xls", ".et", ".csv", ".tsv", ".md",
             ".markdown", ".txt", ".pdf"}


class Job:
    def __init__(self, job_dir):
        self.dir = os.path.abspath(job_dir)
        jf = os.path.join(self.dir, "job.json")
        if not os.path.exists(jf):
            raise DIError("不是任务目录（缺少 job.json）：%s" % job_dir)
        self.data = read_json(jf)
        self.cache = self.data.get("cache") or os.path.join(os.path.dirname(self.dir), "_缓存")
        self._models = {}

    # ------------------------------------------------------------ 创建
    @classmethod
    def create(cls, target, sources, task="", out_root=None, name=None, profile=None, formats=None):
        target = os.path.abspath(target)
        if not os.path.isfile(target):
            raise DIError("请指定存在的目标文件：%s" % target)
        srcs = []
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
                        if os.path.isfile(fp) and os.path.splitext(f)[1].lower() in DOC_TYPES and not f.startswith("~$") and fp != target:
                            srcs.append(fp)
                elif p != target and not os.path.basename(p).startswith("~$"):
                    srcs.append(p)
        if not srcs:
            raise DIError("至少需要一个源文件（--source）")
        out_root = os.path.abspath(out_root or os.path.join(os.path.dirname(target), "整合输出"))
        stem = os.path.splitext(os.path.basename(target))[0]
        name = safe_name(name or "%s_%s" % (stem, datetime.datetime.now().strftime("%Y%m%d-%H%M%S")))
        d = os.path.join(out_root, name)
        if os.path.exists(os.path.join(d, "job.json")):
            raise DIError("任务目录已存在：%s" % d)
        ensure_dir(d)
        data = {"version": 1, "created": datetime.datetime.now().isoformat(timespec="seconds"), "task": task or "",
                "target": {"path": target}, "sources": [{"alias": "S%d" % (i + 1), "path": p} for i, p in enumerate(srcs)],
                "profile": profile or "default", "formats": formats or [], "cache": os.path.join(out_root, "_缓存")}
        write_json(os.path.join(d, "job.json"), data)
        job = cls(d)
        tm = job.target_model()
        job.data["template_comments"] = [[c.get("author", ""), c.get("text", "")] for c in tm.m.get("comments", [])]
        job.save()
        for a in job.data["sources"]:
            job.model(a["alias"])
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
            self._models[alias] = Model(load_model(p, self.cache), alias)
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
    def fingerprint(self):
        '''目标+全部源文件当前内容的指纹：回答只对当时的文件版本有效，文件被改过就失效。'''
        h = cache_hasher()
        for al in ['T'] + [s['alias'] for s in self.data['sources']]:
            h.update((al + ':' + self.model(al).m['sha1']).encode('utf-8'))
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
