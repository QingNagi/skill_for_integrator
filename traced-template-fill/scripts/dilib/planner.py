# -*- coding: utf-8 -*-
"""自动规划：目标分析 + 源内容 + 任务描述 + 用户回答 -> 填写操作(ops) + 待确认问题(questions)。

配方（recipes）：
  sections   目标章节（批注=要求）<- 源文件章节/段落/表/图（“文档摘抄”类）
  table      目标表格数据区 <- 源文件表格（如 规范“电特性”表 -> 验证记录）
  testdata   目标数据区的样品列 <- 测试数据表（如 B.xlsx）
  fields     表单字段（产品型号、依据标准等）、派生字段（合格数）、无来源字段汇总询问
"""
from __future__ import annotations

import hashlib
import re

from . import identity, tablemap
from .plan_tables import TablePlanningMixin
from .common import contains_model_code, contains_score, norm_text, short, sim
from .read_docx import HEAD_PATTERNS
from .target import analyze

DOC_WORDS = ["研制任务书", "任务书", "产品定义书", "定义书", "详细规范", "规范", "说明书", "数据手册", "手册", "测试报告",
             "试验报告", "报告", "验证记录", "记录", "大纲", "方案", "细则", "测试数据", "数据表", "datasheet"]
FILLER = ["中的", "里的", "当中", "查看", "参看", "参考", "参见", "详见", "依据", "根据", "摘抄", "摘录", "引用", "填写",
          "章节", "部分内容", "相关内容", "内容", "相关", "有关", "部分", "中", "的", "见"]
CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


# ======================================================================= 任务描述 -> 规则
def task_rules(task):
    r, why = {}, []
    t = task or ""
    if re.search(r"(仅|只|本次).{0,12}(中间电压|中间值|Vnorm|标称电压|典型电压)|_?Vnorm\s*为中间值.{0,20}仅", t, re.I) or \
            (re.search(r"中间电压", t) and re.search(r"仅|只需", t)):
        r["cond_keep"] = ["Vnorm"]
        why.append("条件=仅Vnorm（任务描述“仅需提取中间电压”）")
    elif re.search(r"(仅|只).{0,8}(最小电压|Vmin)", t, re.I):
        r["cond_keep"] = ["Vmin"]
        why.append("条件=仅Vmin")
    elif re.search(r"(仅|只).{0,8}(最大电压|Vmax)", t, re.I):
        r["cond_keep"] = ["Vmax"]
        why.append("条件=仅Vmax")
    elif re.search(r"全部(电压|条件)|所有(电压|条件)", t):
        r["cond_keep"] = "all"
        why.append("条件=全部")
    if re.search(r"数值范围|XX\s*[~～\-至]\s*XX|最小值?\s*[~～至]\s*最大值?|填.{0,4}范围", t, re.I):
        r["multi"] = "range"
        why.append("多值=范围")
    elif re.search(r"(多个|多值|多次).{0,15}(取|填).{0,3}最大", t):
        r["multi"] = "max"
        why.append("多值=最大值")
    elif re.search(r"(多个|多值|多次).{0,15}(取|填).{0,3}最小", t):
        r["multi"] = "min"
        why.append("多值=最小值")
    elif re.search(r"(多个|多值|多次).{0,15}(取|填).{0,3}平均", t):
        r["multi"] = "mean"
        why.append("多值=平均值")
    m = re.search(r"写法为?\s*[“\"']?\s*XX\s*([~～\-－至])\s*XX", t, re.I)
    if m:
        r["range_sep"] = m.group(1)
        why.append("范围写法=XX%sXX" % m.group(1))
    m = re.search(r"\b([A-Z])\s*列.{0,12}(标题|测试项|符号|项目)", t)
    if m:
        r["label_col"] = m.group(1)
        why.append("标签列=%s" % m.group(1))
    m = re.search(r"第\s*([一二三1-3])\s*行.{0,12}样品", t)
    if m:
        r["header_row"] = CN_NUM.get(m.group(1)) or int(m.group(1))
        why.append("样品号行=%s" % r["header_row"])
    m = re.search(r"保留\s*(\d)\s*位小数", t)
    if m:
        r["decimals"] = int(m.group(1))
        why.append("小数位=%s" % m.group(1))
    m = re.search(r"[（(]([^（）()]{0,20}(?:备案|变更|版|修订|修改单)[^（）()]{0,20})[）)]", t)
    if m:
        r["doc_version"] = m.group(1).replace(" ", "")
        why.append("规范版本=“%s”" % r["doc_version"])
    return r, why


# ======================================================================= 标题/要求解析
def clean_title(t):
    s = (t or "").strip()
    s = re.sub(r"^[•●▪➢◆■√❖○◇·\-]+\s*", "", s)
    for _, pat in HEAD_PATTERNS:
        s2 = pat.sub("", s, count=1)
        if s2 != s:
            s = s2
            break
    s = re.sub(r"^\d+(\.\d+)*\s+", "", s)
    # 去掉模板中的填写说明括注，如“(包括…)”“（质量等级、通用质量特性、特殊质量要求）”
    s2 = re.sub(r"\s*[（(](包括|如|含|即|注|例如|可|适用)[^（）()]*[）)]\s*$", "", s)
    if s2 == s:
        s2 = re.sub(r"\s*[（(][^（）()]*[、；;][^（）()]*[）)]\s*$", "", s)
    return s2.strip(" ：:，,。")


def ordinal_of(text):
    m = re.search(r"第\s*([一二三四五六七八九十\d]+)\s*(部分|章|节|项|条|点)", text)
    if not m:
        return None
    v = m.group(1)
    return int(v) if v.isdigit() else CN_NUM.get(v) or (10 + CN_NUM.get(v[-1], 0) if v.startswith("十") else None)


def leading_number(title):
    s = title.strip()
    m = re.match(r"^[（(]?\s*(\d+|[一二三四五六七八九十]+)\s*[)）、.．]", s) or re.match(r"^第([一二三四五六七八九十\d]+)[章节部分]", s)
    if not m:
        return None
    v = m.group(1)
    return int(v) if v.isdigit() else CN_NUM.get(v)


def kv_items(paras):
    """“1）样品提交用户：2025.10.31” 这类列表 -> [(序号, 名称, 值)]；不全匹配返回 None。"""
    out = []
    for p in paras:
        t = p.strip()
        if not t:
            continue
        m = re.match(r"^\s*(\d+)\s*[)）.、]\s*(.+?)\s*[：:]\s*(.*)$", t)
        if not m:
            return None
        out.append((m.group(1), m.group(2).strip(), m.group(3).strip()))
    return out if len(out) >= 3 else None


class Planner(TablePlanningMixin):
    def __init__(self, job, answers=None, learned=None, aliases=None, manual_ops=None, rules_override=None):
        self.job = job
        self.T = job.target_model()
        self.src = job.source_models()
        self.task = job.data.get("task", "")
        self.answers = dict(learned or {})
        self.answers.update(answers or {})
        self.aliases = aliases or {}
        self.manual = manual_ops or []
        self.rules_override = rules_override or {}
        self.ops, self.questions, self.summary, self.report = [], [], [], {"fills": [], "unfilled": [], "ignored": [],
                                                                            "warnings": []}
        self.an = analyze(self.T)
        self.rules, self.rule_why = task_rules(self.task)
        self.rules.update(self.rules_override)
        self.n = 0
        self.row_shift = {}
        self.missing_regions = []
        self.stale = []  # 目标里原有值、本批没有对应数据/未核对的格

    # ------------------------------------------------------------ 工具
    def oid(self):
        self.n += 1
        return "o%d" % self.n

    def ask(self, q):
        if any(x["key"] == q["key"] for x in self.questions):
            return None
        q.setdefault("recommended", 0)
        self.questions.append(q)
        return q

    def answer_effect(self, key):
        """返回用户对 key 问题所选选项的 effect（多选返回列表）；未回答返回 None。"""
        a = self.answers.get(key)
        if a is None:
            return None
        if isinstance(a, dict) and "effect" in a:
            eff = a["effect"]
            if a.get("default"):        # --defaults 时按推荐项处理：照常生效，但标记为“待确认”
                if isinstance(eff, dict):
                    eff = dict(eff, _default=True, _label=a.get("label", ""))
                elif isinstance(eff, list):
                    eff = [dict(e, _default=True, _label=a.get("label", "")) for e in eff]
            return eff
        return a

    def default_note(self, eff):
        effs = eff if isinstance(eff, list) else [eff]
        for e in effs:
            if isinstance(e, dict) and e.get("_default"):
                return "按推荐默认处理（%s），待确认" % short(e.get("_label", ""), 30)
        return None

    def doc_sources(self):
        return {a: m for a, m in self.src.items() if m.m["type"] != "xlsx"}

    def sheet_sources(self):
        return {a: m for a, m in self.src.items() if m.m["type"] == "xlsx"}

    def src_names(self, alias):
        m = self.src[alias].m
        return [m.get("title", ""), m.get("name", ""), re.sub(r"\.\w+$", "", m.get("name", ""))]

    # ------------------------------------------------------------ 主流程
    def run(self):
        if self.rule_why:
            self.summary.append("从任务描述识别的规则：" + "；".join(self.rule_why))
        regions = [(t, rg) for t in self.an.get("tables", []) for rg in t["regions"]]
        measurements = [(t, rg) for t, rg in regions
                        if any(tablemap.role_of(c["path"])[0] == "sample" for c in rg["cols"])]
        books = self.sheet_sources()
        selection = self.answer_effect("td:source") or {}
        if selection.get("alias") in books and not selection.get("_default"):
            books = {selection["alias"]: books[selection["alias"]]}
        data_codes = set()
        if len(books) == 1 and measurements:
            for book in books.values():
                for sheet in book.m.get("sheets", []):
                    data_codes.update(identity.sheet_identity(book, sheet, identity.document_identity(self.T))["model"])
        if measurements and not self.spec_evidence(data_codes):
            q = next(q for q in self.questions if q["key"].startswith("src:spec:"))
            for t, rg in measurements:
                samples = {tablemap.role_of(c["path"])[1]: c["c"] for c in rg["cols"]
                           if tablemap.role_of(c["path"])[0] == "sample"}
                rows = self.affected_spec_rows(t, rg)
                if rows:
                    self.add_spec_impact(q, t, rows, samples)
                self.mark_stale(t, rows, samples, set())
            self.report["unfilled"].append("规范型号适用性待确认，已暂停按规范和测试记录填写。")
        else:
            for t, rg in regions:
                self.plan_region(t, rg)
            self.plan_sections()
            self.plan_fields()
            self.apply_manual()
            self.report_sections()
        self.check_unread()
        self.check_ocr()
        self.report['stale'] = self.stale
        return {"ops": self.ops, "questions": self.questions, "summary": self.summary, "report": self.report,
                "rules": self.rules}

    def check_unread(self):
        '''源文件里有没读到的内容（扫描页/无文本层等）：“没找到”不等于“没有”，必须问清并登记，定稿前拦截。'''
        self.report['warnings'].extend('模板：%s' % note for note in self.T.m.get('read_warnings', []))
        self.report['warnings'].extend('模板 %s：%s' % (item['location'], item['why'])
                                       for item in self.T.m.get('unread', []) if item.get('location'))
        for alias, md in self.src.items():
            if md.m.get('backend_note'):
                self.report['warnings'].append('%s：%s' % (md.m['name'], md.m['backend_note']))
            self.report['warnings'].extend('%s：%s' % (md.m['name'], note) for note in md.m.get('read_warnings', []))
            un = md.m.get('unread') or []
            if not un:
                continue
            key = 'src:unread:' + alias
            locations = '、'.join(u.get('location') or '第 %s 页' % u.get('page', '?') for u in un[:12])
            detail = '%s 有 %d 处内容没有读取到（%s：%s），这些部分的“没找到”不能当作“没有”' % (
                md.m['name'], len(un), locations, un[0].get('why', ''))
            eff = self.answer_effect(key)
            if eff is None:
                self.ask({'key': key, 'topic': '未读取内容', 'question': detail + '。如何处理？', 'options': [{'label': '先不当作没有：我会提供可读取的版本后重跑', 'effect': {'ok': False}}, {'label': '我已人工核对过这些位置，目标不需要其中的内容', 'effect': {'ok': True}}], 'recommended': 0})
                eff = {'ok': False}
            if not (isinstance(eff, dict) and eff.get('ok') and not eff.get('_default')):
                self.report.setdefault('unverified_sources', []).append({'kind': 'unread', 'key': key, 'name': md.m['name'], 'detail': detail})
            self.report['warnings'].append('⚠ ' + detail)

    def check_ocr(self):
        from .pdf_ocr import review_groups
        for alias, md in self.src.items():
            pages = md.m.get('ocr_pages')
            if not pages:
                continue
            # A previous review only applies to this source and this recognized text.
            digest = hashlib.sha256(md.m['sha1'].encode('ascii'))
            flagged = {}
            for block in md.m['blocks']:
                if block.get('ocr'):
                    anchor = '%s:%s（第 %s 页）' % (alias, block['id'], block['page'])
                    for label, words in review_groups(block):
                        for w in words:
                            flagged.setdefault(label, []).append((anchor, w))
                            digest.update(('%s %.1f' % (w['text'], w['confidence'])).encode('utf-8'))
                    digest.update(('%s\0%s\0' % (block['page'], block['text'])).encode('utf-8'))
            key = 'src:ocr:%s:%s' % (alias, digest.hexdigest()[:16])
            detail = '%s（%s）第 %s 页使用 OCR。请对照 PDF 原页核对本次采用的文字、数字、小数点、正负号和单位' % (
                md.m['name'], alias, '、'.join(map(str, pages)))
            for label, words in flagged.items():
                examples = '、'.join('%s %s（%.1f/100）' % (anchor, w['text'], w['confidence'])
                                    for anchor, w in words[:6])
                detail += '。⚠ 低置信%s共 %d 处：%s' % (label, len(words), examples)
            for block in md.m['blocks']:
                if block.get('metadata_title_hint'):
                    detail += '。%s:%s 第 1 页 OCR 原文「%s」，PDF 元数据标题「%s」' % (
                        alias, block['id'], block['text'], block['metadata_title_hint'])
            self.report['warnings'].append(detail)
            eff = self.answer_effect(key)
            if eff is None:
                self.ask({'key': key, 'topic': 'OCR 原页复核', 'question': detail, 'recommended': 0,
                          'options': [{'label': '待核对原页', 'effect': {'ok': False}},
                                      {'label': '已核对本次采用的识别结果，或这些页未被采用', 'effect': {'ok': True}}]})
            if not (isinstance(eff, dict) and eff.get('ok') and not eff.get('_default')):
                self.report.setdefault('unverified_sources', []).append(
                    {'kind': 'source', 'key': key, 'name': md.m['name'], 'detail': detail})

    FIELD_RE = re.compile('产品名称|规格型号|产品型号|依据|标准条款|样品批号|封装说明书编号|裸片LOGO|起始结束时间|试验日期|检验日期')

    def scan_existing_fields(self):
        '''目标表单里已经填着内容的字段（上一批留下的）：不覆盖，标“原有值，本批未核”；型号与源文件不一致时单独提示。'''
        codes = self.expected_model_codes(self.doc_sources())
        ver = self.rules.get('doc_version')
        for b in self.T.m.get('blocks', []):
            if b['t'] != 'tbl' or 'in' in b:
                continue
            rows = {}
            for c in b['cells']:
                rows.setdefault(c['r'], []).append(c)
            for r, cs in rows.items():
                cs.sort(key=lambda c: c['c'])
                for i in range(len(cs) - 1):
                    lab = re.sub(r'\s+', '', cs[i]['text'])
                    val = cs[i + 1]['text'].strip()
                    if not val or not lab or len(lab) > 20 or not self.FIELD_RE.search(lab) or re.fullmatch(r'[年月日至\s　]*', val):
                        continue
                    same_model = len(codes) == 1 and contains_model_code(val, codes[0])
                    if re.search('依据|标准条款', lab) and same_model and (not ver or ver in re.sub(r'\s+', '', val)):
                        continue
                    cid = '%s.R%d.C%d' % (b['id'], cs[i + 1]['r'], cs[i + 1]['c'])
                    bad = bool(re.search('产品名称|规格型号|产品型号', lab)) and len(codes) == 1 and not same_model
                    self.stale.append({'kind': 'stale', 'cell': cid, 'text': cs[i + 1]['text'], 'sample': '', 'param': lab, 'why': ('型号与源文件不一致（源文件为 %s）' % codes[0]) if bad else ''})
                    if bad:
                        msg = '⚠【型号不一致】记录里原有型号“%s”，源文件的型号是 %s；没有覆盖，请确认是否填错' % (short(val, 20), codes[0])
                    else:
                        msg = '⚠【原有值，本批未核】“%s”是模板/上一批留下的值，本批没有重新核对；没有覆盖，请核对是否仍适用' % short(val, 24)
                    self.ops.append({'id': self.oid(), 'op': 'comment', 'anchor': cid, 'warn': True, 'links': [], 'text': msg})
        if not hasattr(self, 'verdict') or self.verdict[2]:
            for b in self.T.m.get('blocks', []):
                if b['t'] != 'tbl' or 'in' in b:
                    continue
                for c in b['cells']:
                    if re.search(r'\d+\s*只合格', c['text']):
                        cid = '%s.R%d.C%d' % (b['id'], c['r'], c['c'])
                        self.stale.append({'kind': 'stale', 'cell': cid, 'text': c['text'], 'sample': '', 'param': '合格数量'})
                        self.ops.append({'id': self.oid(), 'op': 'comment', 'anchor': cid, 'warn': True, 'links': [], 'text': '⚠【原有值，本批未核】原有的合格/不合格数量缺少本批完整依据；没有覆盖，请核对或清除'})

    def plan_fields(self):
        self.scan_existing_fields()
        missing = list(self.missing_regions)
        docs = self.doc_sources()
        for t in self.an.get("tables", []):
            shift_r, shift_n = self.row_shift.get(t["id"], (10 ** 9, 0))

            def sh(cid):
                m = re.match(r"^(T\d+)\.R(\d+)\.C(\d+)$", cid)
                r = int(m.group(2))
                return "%s.R%d.C%s" % (m.group(1), r + shift_n if r > shift_r else r, m.group(3))
            for f in t["fields"]:
                lab = f["label"]
                cell = sh(f["cell"])
                if re.search(r"产品名称|规格型号|产品型号|型号|器件名称", lab):
                    self.field_product(lab, cell, docs)
                elif re.search(r"依据|标准条款|执行标准|引用标准|检验标准", lab):
                    self.field_basis(lab, cell, docs)
                else:
                    missing.append((lab, cell))
            for b in t["blanks"]:
                txt = b["text"]
                cell = sh(b["cell"])
                if re.search(r"只合格", txt) and hasattr(self, "verdict"):
                    npass, nfail, reasons = self.verdict
                    if reasons:
                        detail = '合格/不合格数量待核：' + '；'.join(reasons)
                        self.report['unfilled'].append(detail)
                        self.ops.append({'id': self.oid(), 'op': 'comment', 'anchor': cell, 'warn': True,
                                         'text': '⚠【派生】' + detail, 'links': []})
                        continue
                    self.ops.append({"id": self.oid(), "op": "fill_blanks", "cell": cell, "values": [str(npass), str(nfail)]})
                    self.ops.append({"id": self.oid(), "op": "comment", "anchor": cell, "warn": False,
                                     "text": "【派生】按实测值与规范限值逐项比对：%d只全部在限值内、%d只有超限" % (npass, nfail),
                                     "links": []})
                    self.report["fills"].append({"target": cell, "value": "%d只合格，%d只不合格" % (npass, nfail),
                                                 "source": "实测值比对", "how": "派生"})
                elif "□" not in txt and not re.search(r"只合格", txt):
                    missing.append((re.sub(r"\s+", "", txt)[:16], cell))
        for lab, cell in missing:
            self.report["unfilled"].append("%s（%s）" % (lab, cell))
        if missing:
            eff = self.answer_effect("fields:missing")
            vals = (eff or {}).get("values") if isinstance(eff, dict) else None
            if vals:
                for lab, cell in missing:
                    v = vals.get(lab) or vals.get(cell)
                    if v:
                        self.ops.append({"id": self.oid(), "op": "set_text", "cell": cell, "text": v})
            elif eff is None:
                self.ask({"key": "fields:missing", "topic": "无来源字段",
                          "question": "以下字段在源文件中找不到来源：%s。如何处理？" % "、".join(l for l, _ in missing[:12]),
                          "options": [{"label": "留空，审核时人工填写", "effect": {"skip": True}},
                                      {"label": "我现在提供（回答中写明“字段=值”）", "effect": {"free": True}}]})

    def _model_codes(self, docs):
        codes = {}
        names = [text for alias in docs for text in dict.fromkeys(self.src_names(alias))]
        for text in names + [self.task]:
            for code in re.findall(r"(?<![A-Za-z0-9])[A-Z]{2,}[A-Z0-9\-]*\d[A-Z0-9\-]*(?![A-Za-z0-9])", text or ""):
                if len(code) >= 6:
                    codes[code] = codes.get(code, 0) + 1
        return sorted(codes, key=lambda code: -codes[code])

    def expected_model_codes(self, docs):
        codes = self._model_codes(docs)
        chosen = set()
        for key, answer in self.answers.items():
            if not re.match(r'^field:.*(?:产品名称|规格型号|产品型号)', key) or not isinstance(answer, dict) or answer.get('default'):
                continue
            effect = self.answer_effect(key)
            if isinstance(effect, dict):
                chosen.update(code for code in codes if contains_model_code(effect.get('value', ''), code))
        return [code for code in codes if code in chosen] if chosen else codes

    def field_product(self, lab, cell, docs):
        candidates = []
        for code in self._model_codes(docs):
            sources = [a for a in docs if any(contains_model_code(text, code) for text in self.src_names(a))]
            if not sources:
                candidates.append((None, code))
                continue
            candidates.append((sources[0], code))
            for alias in sources:
                title = docs[alias].m.get("title", "")
                match = re.search(r'(?<![A-Za-z0-9-])' + re.escape(code) + r"(?![A-Za-z0-9-])型?([^，。；\s]{2,20}?)(?=详细规范|规范|标准|\s|$)", title)
                if match:
                    full = match.group().strip()
                    if full != code and not re.fullmatch(r"(详细)?规范|标准", full[len(code):]):
                        candidates.append((alias, full))
                        break
        self._write_field(lab, cell, candidates, "型号识别")

    def field_basis(self, lab, cell, docs):
        candidates = []
        for alias, md in docs.items():
            title = re.sub(r"\s+", "", md.m.get("title", ""))
            name = re.sub(r"\.\w+$", "", md.m["name"])
            compact_name = re.sub(r"\s+", "", name)
            if title.startswith(compact_name) and any(
                    b["t"] == "p" and re.sub(r"\s+", "", b.get("text", "")) == compact_name for b in md.m.get("blocks", [])[:12]):
                title = compact_name
            basis = next((x for x in (title, name) if re.search(r"规范|标准|细则|大纲", x)), None)
            if basis:
                value = re.sub(r"^半导体集成电路", "", basis)
                version = self.rules.get("doc_version")
                if version and version not in value:
                    value += "（%s）" % version
                candidates.append((alias, value))
        self._write_field(lab, cell, candidates, "依据文件")

    def _write_field(self, lab, cell, candidates, how):
        if not candidates:
            self.report["unfilled"].append("%s（%s）" % (lab, cell))
            return
        selected = candidates[0]
        if len(candidates) > 1:
            key = "field:" + lab
            eff = self.answer_effect(key) or {}
            matches = [(a, v) for a, v in candidates if v == eff.get("value") and
                       ("alias" not in eff or a == eff["alias"])]
            if len(matches) != 1:
                self.ask({"key": key, "topic": "字段取值", "question": "“%s”采用哪个来源和写法？" % lab,
                          "options": [{"label": "%s · %s" % (a or "任务描述", v), "effect": {"alias": a, "value": v}}
                                      for a, v in candidates]})
                return
            selected = matches[0]
        alias, value = selected
        source = ("%s 标题/文件名" % alias) if alias else "任务描述"
        links = [{"label": "打开原文", "alias": alias, "ref": "TITLE"}] if alias else []
        self.ops.append({"id": self.oid(), "op": "set_text", "cell": cell, "text": value})
        self.ops.append({"id": self.oid(), "op": "comment", "anchor": cell, "text": "【字段】" + source, "links": links})
        self.report["fills"].append({"target": cell, "value": value, "source": source, "source_alias": alias,
                                     "how": how if alias else "任务指定型号"})

    # ================================================================= 配方：章节摘抄
    def parse_requirement(self, text):
        docs = self.doc_sources()
        hits = []
        for wd in sorted(DOC_WORDS, key=len, reverse=True):
            for m in re.finditer(re.escape(wd), text):
                if any(h[0] <= m.start() < h[1] for h in hits):
                    continue
                hits.append((m.start(), m.end(), wd))
        hits.sort()
        clauses = []
        if not hits:
            clauses.append({"doc": None, "alias": None, "text": text})
        for i, (s, e, wd) in enumerate(hits):
            nxt = hits[i + 1][0] if i + 1 < len(hits) else len(text)
            seg = text[e:nxt]
            if i == 0:
                prefix = text[:s].strip()
                if not re.fullmatch(r"(?:请)?(?:从|按|根据|参考|参照)?(?:指定|所给|提供|所附)?(?:的)?", prefix):
                    seg = prefix + " " + seg
            seg = re.sub(r"^\s*(?:里|中)?(?:摘入|摘录|摘抄|提取|引用)\s*", "", seg)
            seg = seg.strip(" 。；;：:“”\"")
            alias = None
            best = 0
            for a in docs:
                names = " ".join(self.src_names(a))
                sc = 1.0 if wd in names else max(sim(wd, n) for n in self.src_names(a) if n)
                if sc > best:
                    best, alias = sc, a
            if best < 0.6:
                alias = None
            clauses.append({"doc": wd, "alias": alias, "text": seg})
        for c in clauses:
            seg = re.sub(r"第\s*[一二三四五六七八九十\d]+\s*(部分|章|节|项|条|点)", " ", c["text"])   # 序号单独处理
            for f in FILLER:
                seg = seg.replace(f, " ")
            kws = [k.strip() for k in re.split(r"[、，,；;和及与/\s]+|以及", seg) if k.strip()]
            c["ordinal"] = ordinal_of(c["text"])
            c["keywords"] = [k for k in kws if not re.match(r"^第.+(部分|章|节|项|条)$", k) and len(k) >= 2]
        return clauses

    def find_in(self, alias, kw, ordinal=None):
        md = self.src[alias]
        secs = md.sections()
        if ordinal:
            numbered = [s for s in secs if leading_number(s["title"]) is not None]
            if numbered:
                d0 = min(s.get("depth", 0) for s in numbered)
                for s in numbered:
                    if s.get("depth", 0) == d0 and leading_number(s["title"]) == ordinal:
                        return {"alias": alias, "sec": s["id"], "title": clean_title(s["title"]), "score": 1.0, "how": "序号"}
            return None
        best = None
        for s in secs:
            ct = clean_title(s["title"])
            sc = contains_score(kw, ct)
            if kw in ct:
                sc = max(sc, 0.95)
            if sc >= 0.6 and (best is None or sc > best["score"]):
                best = {"alias": alias, "sec": s["id"], "title": ct, "score": sc, "how": "标题"}
        if best and best["score"] >= 0.9:
            return best
        for b in md.m.get("blocks", []):
            if b["t"] == "p" and 2 <= len(b["text"].strip()) <= 30 and kw in b["text"] and not b.get("cap"):
                sec = None
                for s in secs:
                    if b["id"] in s["blocks"] and (sec is None or s.get("depth", 0) >= sec.get("depth", 0)):
                        sec = s
                rng = None
                if sec:
                    idx = sec["blocks"].index(b["id"])
                    rng = sec["blocks"][idx + 1:]
                cand = {"alias": alias, "range": rng or [], "title": clean_title(b["text"]), "score": 0.85,
                        "how": "段落标题", "start": b["id"], "in_sec": sec["id"] if sec else None}
                if best is None or cand["score"] > best["score"]:
                    best = cand
                break
        return best

    def section_content(self, pick):
        """把选中的源章节/段落范围转成插入项。"""
        md = self.src[pick["alias"]]
        if pick.get("sec"):
            s = md.by[pick["sec"]]
            ids = [x for x in s["blocks"]]
            container = s.get("in", "body")
        else:
            ids = pick.get("range", [])
            container = None
        items = []
        used_tables = set()
        for x in ids:
            b = md.by.get(x)
            if not b:
                continue
            if container and container != "body" and b.get("in", "body") != container and b["t"] == "p":
                continue
            if b["t"] == "p":
                if b.get("cap"):
                    continue
                if b.get("in") and container == "body":
                    continue
                if b.get("fig"):
                    f = md.by[b["fig"]]
                    items.append({"type": "figure", "alias": pick["alias"], "fig": f["id"],
                                  "caption": re.sub(r"^(图|Figure|Fig\.)\s*[A-Z]?[\d.\-]+\s*", "", f.get("cap") or "")})
                    rest = [r for r in (b.get("runs") or []) if not r[1].startswith("@") and r[0].strip()]
                    if rest:
                        items.append({"type": "para", "runs": rest, "src": x})
                    continue
                txt = b["text"].strip()
                if not txt and not b.get("imgs"):
                    continue
                # 模板占位文字（Testtesttest、Funcfuncfunc 这类重复拼接的填充词）不是正文：整段只有占位时不摘抄，按“源内容为空”处理
                fl = re.sub(r"(?i)(?<![a-z])([a-z]{3,8}?)\1{2,}(?![a-z])", "", txt)
                if fl != txt and not re.sub(r"[\W_]+", "", fl) and not b.get("imgs"):
                    self.report.setdefault("filler", []).append("%s:%s“%s”" % (pick["alias"], x, short(txt, 24)))
                    continue
                runs = [list(r) for r in (b.get("runs") or [[b["text"], ""]])]
                if b.get("label"):
                    runs.insert(0, [b["label"] + " ", ""])
                while runs and not runs[0][1].startswith("@") and not runs[0][0].strip():
                    runs.pop(0)
                if runs and not runs[0][1].startswith("@"):
                    runs[0][0] = runs[0][0].lstrip(" \t　")
                items.append({"type": "para", "runs": runs, "src": x, "lvl": b.get("lvl")})
            elif b["t"] == "tbl":
                if b.get("cont") or x in used_tables:
                    continue
                if container == "body" and b.get("in"):
                    continue
                used_tables.add(x)
                items.append({"type": "table", "alias": pick["alias"], "table": x,
                              "caption": re.sub(r"^(表|Table)\s*[A-Z]?[\d.\-]+\s*", "", b.get("cap") or "")})
        return items

    def plan_sections(self):
        docs = self.doc_sources()
        if not docs:
            return
        replaced = {o["anchor"] for o in self.manual if o.get("op") == "add_section" and o.get("mode") == "replace"}
        for s in self.an.get("sections", []):
            if not s["empty"] or s["start"] in replaced:     # 已由人工指定替换的章节不再自动规划
                continue
            reqs = [q["text"] for q in s["requirements"]]
            req = "；".join(reqs) if reqs else clean_title(s["title"])
            if not reqs and not any(x["requirements"] for x in self.an["sections"]) and len(self.an["sections"]) < 2:
                continue
            if reqs:
                clauses = self.parse_requirement(req)
            else:   # 无批注：以标题整体作为检索词（不按“和/及”拆分）
                clauses = [{"doc": None, "alias": None, "text": req, "ordinal": None, "keywords": [req]}]
            picks = []
            for cl in clauses:
                if cl["doc"] and not cl["alias"]:
                    key = "sec:missing:%s:%s" % (s["id"], cl["doc"])
                    eff = self.answer_effect(key)
                    if eff is None:
                        alts = []
                        for kw in cl["keywords"][:2]:
                            for a in docs:
                                f = self.find_in(a, kw)
                                if f:
                                    alts.append((a, kw, f))
                        opts = [{"label": "我稍后提供该文件（草稿中先留“待补充”标记）", "effect": {"placeholder": True}},
                                {"label": "跳过这部分", "effect": {"skip": True}}]
                        for a, kw, f in alts[:2]:
                            opts.append({"label": "用 %s 中的“%s”代替" % (self.src[a].m["name"], f["title"]),
                                         "effect": {"use": f}})
                        self.ask({"key": key, "topic": "缺少源文件",
                                  "question": "目标章节“%s”要求引用“%s”中的“%s”，但未提供该文件。如何处理？" % (
                                      s["title"], cl["doc"], "、".join(cl["keywords"]) or "相关内容"),
                                  "options": opts})
                        eff = opts[0]["effect"]
                    if eff.get("use"):
                        p = dict(eff["use"])
                        p["label_doc"] = cl["doc"]
                        picks.append(p)
                    elif eff.get("placeholder"):
                        for kw in cl["keywords"] or [cl["doc"]]:
                            picks.append({"placeholder": "【待补充：%s中的%s】" % (cl["doc"], kw), "title": kw, "kw": kw,
                                          "label_doc": cl["doc"], "dnote": self.default_note(eff)})
                    continue
                aliases = [cl["alias"]] if cl["alias"] else list(docs)
                targets = ([("__ord__", cl["ordinal"])] if cl["ordinal"] else []) + [(k, None) for k in cl["keywords"]]
                if not targets:
                    targets = [(clean_title(s["title"]), None)]
                for kw, ordv in targets:
                    found = None
                    for a in aliases:
                        f = self.find_in(a, kw if kw != "__ord__" else "", ordv)
                        if f and (found is None or f["score"] > found["score"]):
                            found = f
                    key = "sec:find:%s:%s" % (s["id"], kw if kw != "__ord__" else "第%d部分" % ordv)
                    eff = self.answer_effect(key)
                    if eff is not None:
                        effs = eff if isinstance(eff, list) else [eff]
                        uses = [e["use"] for e in effs if isinstance(e, dict) and e.get("use")]
                        if not uses:
                            continue
                        dn = self.default_note(eff)
                        for u in uses:
                            picks.append(dict(u, label_doc=cl["doc"], kw=kw, how="用户选择", dnote=dn))
                        continue
                    if not found:
                        from .content import search
                        hits = search({a: self.src[a] for a in aliases}, kw if kw != "__ord__" else "", top=3, kinds=("sec", "p"))
                        hits = [h for h in hits if h[0] >= 0.55]
                        strong = bool(hits) and hits[0][0] >= 0.8
                        opts = [{"label": "%s %s" % (self.src[h[1]].m["name"], h[3]),
                                 "effect": {"use": {"alias": h[1], "sec": h[2], "title": clean_title(h[3].replace("章节 ", ""))}
                                            if h[2].startswith("H") else {"alias": h[1], "range": [h[2]], "title": kw}}}
                                for h in hits]
                        opts.append({"label": "跳过，留空待人工填写", "effect": {"skip": True}})
                        opts.append({"label": "我来指定（说明用哪个文件的哪一部分）", "effect": {"free": True}})
                        self.ask({"key": key, "topic": "定位章节", "multi": len(hits) > 1,
                                  "question": "目标章节“%s”需要“%s”，源文件中%s，请选择%s：" % (
                                      s["title"], kw if kw != "__ord__" else "第%d部分" % ordv,
                                      "有以下可能相关的内容" if hits else "没有找到明显对应的内容",
                                      "（可多选）" if len(hits) > 1 else ""),
                                  "options": opts, "recommended": 0 if strong else len(hits)})
                        continue
                    found = dict(found)
                    found["label_doc"] = cl["doc"]
                    found["kw"] = kw
                    picks.append(found)
            uniq, seen = [], set()
            for p in picks:                       # 去重（不同关键词命中同一源章节）
                k = (p.get("alias"), p.get("sec") or p.get("start") or p.get("placeholder"))
                if k not in seen:
                    seen.add(k)
                    uniq.append(p)
            picks = uniq
            if not picks:
                continue
            # 相邻章节/空章节询问
            for p in list(picks):
                if not p.get("sec"):
                    continue
                md = self.src[p["alias"]]
                sec = md.by[p["sec"]]
                empty = sec["size"]["chars"] < 2 and not sec["size"]["tables"] and not sec["size"]["figs"]
                key = "sec:more:%s:%s:%s" % (s["id"], p["alias"], p["sec"])
                eff = self.answer_effect(key)
                if not empty and eff is None:
                    continue          # 源章节有内容：直接摘抄，不打扰用户
                tbl = sec.get("in", "body").split(".")[0]
                sibs = [x for x in md.sections() if x["id"] != sec["id"] and (
                    (sec.get("in", "body") == "body" and x.get("in", "body") == "body" and x.get("parent") == sec.get("parent")) or
                    (sec.get("in", "body") != "body" and x.get("in", "body").split(".")[0] == tbl and x.get("depth") == sec.get("depth")))]
                order = {x['id']: i for i, x in enumerate(md.sections())}
                i0 = order[sec['id']]
                near = [x for x in sibs if abs(order[x['id']] - i0) <= 2 and
                        (x['size']['chars'] or x['size']['tables'] or x['size']['figs']) and
                        (contains_score(clean_title(x["title"]), s["title"] + req) >= 0.2 or abs(order[x['id']] - i0) == 1)]
                if eff is None and near:
                    opts = [{"label": "纳入相邻章节“%s”" % clean_title(x["title"]),
                             "effect": {"add": {"alias": p["alias"], "sec": x["id"], "title": clean_title(x["title"])}}}
                            for x in near[:3]]
                    opts.append({"label": "不纳入其他章节（该部分标注“源文件内容为空”）", "effect": {}})
                    self.ask({"key": key, "topic": "章节取舍", "multi": True,
                              "question": "目标章节“%s”（要求：%s）匹配到的源章节“%s”为空。是否同时纳入下列相邻章节？（可多选）" % (
                                  s["title"], short(req, 30), p["title"]),
                              "options": opts, "recommended": len(opts) - 1})
                if eff:
                    effs = eff if isinstance(eff, list) else [eff]
                    adds = [dict(e["add"], score=0.9, how="用户选择", dnote=self.default_note(eff), kw=p.get("kw"),
                                 label_doc=p.get("label_doc")) for e in effs if e.get("add")]
                    grp = sorted([p] + adds, key=lambda x: order[x["sec"]])     # 按源文件顺序排列
                    idx = picks.index(p)
                    picks[idx:idx + 1] = grp
            # 组装
            items = []
            multi = len(picks) > 1
            titles = [p.get("title", "") for p in picks]
            kws = [p.get("kw") for p in picks]
            docs_of_kw = {}
            for p in picks:
                if p.get("kw"):
                    docs_of_kw.setdefault(p["kw"], set()).add(p.get("label_doc"))
            for k, p in enumerate(picks):
                title = p.get("title") or ""
                same = titles.count(title) > 1 or len(docs_of_kw.get(p.get("kw"), ())) > 1
                if same and p.get("label_doc") and p["label_doc"] not in title:
                    title = "%s（%s）" % (title, p["label_doc"])     # 同一内容来自不同文件时注明出处
                group = []
                if multi:
                    group.append({"type": "subheading", "text": title})
                dn = p.get("dnote")
                if p.get("placeholder"):
                    group.append({"type": "para", "runs": [[p["placeholder"], ""]], "placeholder": True})
                    items.append({"group": group, "src": None, "warn": "；".join(x for x in ("缺少源文件，待补充", dn) if x)})
                    continue
                content = self.section_content(p)
                if not content:
                    group.append({"type": "para", "runs": [["（源文件该部分内容为空，待补充）", ""]], "placeholder": True})
                    items.append({"group": group, "src": p, "warn": "；".join(x for x in ("源文件该部分内容为空", dn) if x)})
                    continue
                paras = ["".join(r[0] for r in c["runs"]) for c in content if c["type"] == "para"]
                kv = kv_items(paras) if len(paras) == len(content) else None
                if kv:
                    key = "sec:layout:%s:%s" % (p["alias"], p.get("sec") or p.get("start"))
                    eff = self.answer_effect(key)
                    if eff is None:
                        self.ask({"key": key, "topic": "呈现方式",
                                  "question": "源内容“%s”是%d条“序号）名称：值”列表（如“%s”）。在目标中如何呈现？" % (
                                      title, len(kv), short(paras[0], 24)),
                                  "options": [{"label": "转为表格（序号/名称/值）并加表号", "effect": {"layout": "table"}},
                                              {"label": "保持原列表", "effect": {"layout": "list"}}]})
                        eff = {"layout": "table"}
                    if eff.get("layout") == "table":
                        hdr = self._kv_header(title, kv)
                        content = [{"type": "kvtable", "header": hdr, "rows": [list(x) for x in kv],
                                    "caption": self._kv_caption(title), "src": [c["src"] for c in content]}]
                    dn = dn or self.default_note(eff)
                group += content
                items.append({"group": group, "src": p, "warn": dn} if dn else {"group": group, "src": p})
            if items:
                self.ops.append({"id": self.oid(), "op": "insert_section", "anchor": s["start"], "clear": s["body"],
                                 "items": items, "requirement": req})
                self.summary.append("[章节摘抄] %s ← %s" % (s["title"], "；".join(
                    ("%s:%s“%s”" % (p["alias"], p.get("sec") or p.get("start"), short(p.get("title", ""), 16))) if p.get("alias")
                    else "待补充(%s)" % p.get("label_doc") for p in picks)))
        # 现有标题统一格式
        for s in self.an.get("sections", []):
            if s["empty"] or s["requirements"]:
                self.ops.append({"id": self.oid(), "op": "format_heading", "block": s["start"]})

    def report_sections(self):
        """根据实际写入的章节登记来源，含人工追加和替换后的内容。"""
        titles = {section["start"]: section["title"] for section in self.an.get("sections", [])}
        for op in self.ops:
            if op["op"] != "insert_section":
                continue
            for item in op.get("items", []):
                src = item.get("src") or {}
                alias = src.get("alias")
                ref = src.get("sec") or src.get("start") or next(iter(src.get("range") or []), None)
                if alias not in self.src or ref not in self.src[alias].by:
                    if not item.get("warn"):
                        continue
                    source = "—"
                else:
                    source = "%s:%s" % (alias, ref)
                parts = []
                for block in item.get("group", []):
                    kind = block["type"]
                    if kind == "para":
                        parts.append("".join(run[0] for run in block.get("runs", [])))
                    elif kind in ("table", "kvtable"):
                        parts.append("[表]")
                    elif kind == "figure":
                        parts.append("[图]")
                    elif block.get("text"):
                        parts.append(block["text"])
                self.report["fills"].append({"target": titles.get(op["anchor"], op["anchor"]),
                                             "value": short(" ".join(parts), 60), "source": source,
                                             "how": item.get("warn") or src.get("how", "")})

    def _kv_header(self, title, kv):
        if re.search(r"进度|节点|计划|里程碑", title):
            return ["序号", "里程碑节点", "计划完成时间"]
        return ["序号", "项目", "内容"]

    def _kv_caption(self, title):
        if re.search(r"进度", title):
            return "进度计划表"
        return title

    # ================================================================= 手工操作
    def apply_manual(self):
        def after_insertions(ref):
            for table, (after, count) in self.row_shift.items():
                prefix = table + ".R"
                if ref.startswith(prefix):
                    row = re.match(r"\d+", ref[len(prefix):])
                    if row and int(row[0]) > after:
                        return prefix + str(int(row[0]) + count) + ref[len(prefix) + len(row[0]):]
            return ref

        for op in self.manual:
            op = dict(op)
            op.pop("target_guard", None)
            for field in ("cell", "anchor", "block"):
                if field in op:
                    op[field] = after_insertions(op[field])
            if "clear" in op:
                op["clear"] = [after_insertions(ref) for ref in op["clear"]]
            op.setdefault("id", self.oid())
            if op["op"] == "add_section":          # 由 add-section 命令生成：把源章节加入目标章节
                anc = op["anchor"]
                p = {"alias": op["alias"], "sec": op["sec"], "title": op.get("title") or
                     clean_title(self.src[op["alias"]].by[op["sec"]]["title"]), "how": "人工指定"}
                group = [{"type": "subheading", "text": p["title"]}] if op.get("subheading", True) else []
                group += self.section_content(p) or [{"type": "para", "runs": [["（源文件该部分内容为空，待补充）", ""]]}]
                item = {"group": group, "src": p}
                tgt_op = next((o for o in self.ops if o["op"] == "insert_section" and o["anchor"] == anc), None)
                if tgt_op and op.get("mode", "append") == "append":
                    tgt_op["items"].append(item)
                else:
                    if tgt_op:
                        self.ops.remove(tgt_op)
                    sec = next((s for s in self.an["sections"] if s["start"] == anc), None)
                    self.ops.append({"id": op["id"], "op": "insert_section", "anchor": anc,
                                     "clear": sec["body"] if sec else [], "items": [item], "requirement": "人工指定"})
                self.summary.append("[手工] 章节 %s ← %s:%s“%s”（%s）" % (anc, op["alias"], op["sec"], p["title"],
                                                                     "追加" if op.get("mode", "append") == "append" else "替换"))
                continue
            tgt = op.get("cell") or op.get("anchor") or op.get("block")
            if op.get("replace", True) and op["op"] in ("set_cell", "set_text"):
                self.ops = [o for o in self.ops if not (o.get("cell") == tgt and o["op"] in ("set_cell", "set_text", "fill_blanks"))]
            self.ops.append(op)
            self.summary.append("[手工] %s %s %s" % (op["op"], tgt, short(op.get("text") or "".join(r[0] for r in op.get("runs", [])), 30)))
