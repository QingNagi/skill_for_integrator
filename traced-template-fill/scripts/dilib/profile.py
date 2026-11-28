# -*- coding: utf-8 -*-
"""排版规范（profile）：默认值取自《文档摘抄提示词.docx》；也可用一句中文描述生成新规范。"""
from __future__ import annotations

import copy
from pathlib import Path
import os
import re

from .common import CN_SIZES, DIError, parse_size, read_json
from . import preferences

DEFAULT = {
    "name": "默认（文档摘抄提示词）",
    "source": "标题用黑体三号加粗，正文宋体小四，首行缩进2字符，行距1.5倍；增加图号、表号，表格内容全部居中，"
              "模式为根据内容匹配表格。图表题目及表格正文字体用五号",
    "heading": {"font": "黑体", "latin_font": "黑体", "size": "三号", "bold": True, "line_spacing": 1.0,
                "apply_to_existing": True},
    "subheading": {"font": "宋体", "latin_font": "宋体", "size": "小四", "bold": True, "line_spacing": 1.5,
                   "first_line_chars": 0, "numbering": "（{n}）"},
    "body": {"font": "宋体", "latin_font": "宋体", "size": "小四", "bold": False, "first_line_chars": 2,
             "line_spacing": 1.5},
    "caption": {"font": "宋体", "latin_font": "宋体", "size": "五号", "bold": True, "align": "center",
                "table_prefix": "表", "figure_prefix": "图", "sep": "  ", "table_position": "above",
                "figure_position": "below", "numbering": True},
    "table": {"font": "宋体", "latin_font": "宋体", "size": "五号", "align": "center", "valign": "center",
              "autofit": True, "header_bold": True, "border": "single", "line_spacing": 1.0, "repeat_header": True},
    "figure": {"align": "center", "max_width_cm": 15.0},
    "note": {"font": "宋体", "latin_font": "宋体", "size": "五号", "first_line_chars": 0, "line_spacing": 1.5},
}

_FONTS = ["微软雅黑", "黑体", "宋体", "仿宋_GB2312", "仿宋", "楷体_GB2312", "楷体", "隶书", "幼圆", "方正小标宋简体",
          "方正小标宋", "Times New Roman", "Arial", "Calibri", "Cambria"]
_ROLES = [("subheading", r"二级标题|小标题|子标题|次级标题"), ("caption", r"图表?标题|图表?题目|表题|图题|题注|图标题目"),
          ("table", r"表格(?:正文|内容|文字)?|表内"), ("heading", r"一级标题|标题"), ("note", r"注释|备注|注："),
          ("body", r"正文")]


def load(name_or_path=None, skill_dir=None, project=None):
    prof = copy.deepcopy(DEFAULT)
    if not name_or_path or name_or_path in ("default", "默认"):
        return prof
    path = name_or_path
    if not os.path.exists(path) and skill_dir:
        for folder in (project or preferences.project_dir(), preferences.user_dir(), Path(skill_dir) / "config", skill_dir):
            cand = os.path.join(folder, "profiles", preferences.profile_name(name_or_path))
            if os.path.isfile(cand):
                path = cand
                break
    if not os.path.exists(path):
        raise DIError("找不到排版规范：%s" % name_or_path)
    user = read_json(path)
    for k, v in user.items():
        if isinstance(v, dict) and isinstance(prof.get(k), dict):
            prof[k].update(v)
        else:
            prof[k] = v
    return prof


def size_pt(spec):
    return parse_size(spec.get("size")) if spec.get("size") is not None else None


def from_text(text, base=None):
    """把中文排版要求解析为 profile。无法识别的部分保持默认，并在 notes 中列出识别结果供核对。"""
    prof = copy.deepcopy(base or DEFAULT)
    prof["name"] = "自定义"
    prof["source"] = text
    notes = []
    t = text.replace("，", "，").replace("1,5", "1.5").replace("１", "1")
    clauses = re.split(r"[，,；;。\n]", t)
    last_role = None
    for cl in clauses:
        cl = cl.strip()
        if not cl:
            continue
        roles = []
        for role, pat in _ROLES:
            if re.search(pat, cl):
                roles.append(role)
                cl_check = re.sub(pat, "", cl)
        if "图表题目及正文" in cl or "图标题目及正文" in cl:
            roles = ["caption", "table"]
        roles = list(dict.fromkeys(roles)) or ([last_role] if last_role else [])
        if not roles:
            continue
        # 去重：“二级标题”匹配了 heading 时去掉 heading
        if "subheading" in roles and "heading" in roles:
            roles.remove("heading")
        last_role = roles[-1]
        for role in roles:
            spec = prof[role]
            for f in _FONTS:
                if f in cl:
                    if re.match(r"[A-Za-z]", f):
                        spec["latin_font"] = f
                    else:
                        spec["font"] = f
                        spec.setdefault("latin_font", f)
                        if spec.get("latin_font") in _FONTS and not re.match(r"[A-Za-z]", spec["latin_font"]):
                            spec["latin_font"] = f
                    notes.append("%s字体=%s" % (role, f))
                    break
            for nm in sorted(CN_SIZES, key=len, reverse=True):
                if nm in cl:
                    spec["size"] = nm
                    notes.append("%s字号=%s" % (role, nm))
                    break
            else:
                m = re.search(r"(\d+(?:\.\d+)?)\s*(磅|pt|号字)", cl, re.I)
                if m and "固定" not in cl and "行距" not in cl:
                    spec["size"] = float(m.group(1))
            if re.search(r"不加粗|非粗体|常规", cl):
                spec["bold"] = False
            elif re.search(r"加粗|粗体", cl):
                spec["bold"] = True
            m = re.search(r"首行缩进\s*(\d+(?:\.\d+)?)\s*(字符|字)", cl)
            if m:
                spec["first_line_chars"] = float(m.group(1))
            if re.search(r"不缩进|无缩进|顶格", cl):
                spec["first_line_chars"] = 0
            m = re.search(r"(\d+(?:\.\d+)?)\s*倍行距|行距\s*(\d+(?:\.\d+)?)\s*倍", cl)
            if m:
                spec["line_spacing"] = float(m.group(1) or m.group(2))
                spec.pop("line_rule", None)
            if re.search(r"单倍行距|行距单倍", cl):
                spec["line_spacing"] = 1.0
            m = re.search(r"(?:固定值|固定行距)\s*(\d+(?:\.\d+)?)\s*磅", cl)
            if m:
                spec["line_spacing"] = float(m.group(1))
                spec["line_rule"] = "exact"
            if re.search(r"全部居中|居中", cl) and role in ("table", "caption", "heading", "figure"):
                spec["align"] = "center"
            if re.search(r"左对齐", cl):
                spec["align"] = "left"
            if re.search(r"根据内容|自动调整|匹配表格|根据窗口", cl) and role == "table":
                spec["autofit"] = "window" not in cl
    if re.search(r"图号|表号|题注|编号", t):
        prof["caption"]["numbering"] = True
    if re.search(r"不(要|需)?(加|增加)?(图号|表号|编号)", t):
        prof["caption"]["numbering"] = False
    prof["_notes"] = notes
    return prof


def describe(prof):
    def d(role):
        s = prof[role]
        parts = [s.get("font", ""), str(s.get("size", "")) + ("" if isinstance(s.get("size"), str) else "磅")]
        if s.get("bold"):
            parts.append("加粗")
        if s.get("first_line_chars"):
            parts.append("首行缩进%g字符" % s["first_line_chars"])
        if s.get("line_spacing") and role in ("body", "heading", "subheading", "note"):
            parts.append(("固定值%g磅" % s["line_spacing"]) if s.get("line_rule") == "exact" else ("%g倍行距" % s["line_spacing"]))
        if s.get("align") and role in ("table", "caption"):
            parts.append("居中" if s["align"] == "center" else s["align"])
        return " ".join(p for p in parts if p)
    return ("标题：%s；小标题：%s；正文：%s；图表题注：%s（%s）；表格：%s%s" % (
        d("heading"), d("subheading"), d("body"), d("caption"),
        "自动编号 表N/图N" if prof["caption"].get("numbering") else "不编号", d("table"),
        "，根据内容自动调整" if prof["table"].get("autofit") else ""))
