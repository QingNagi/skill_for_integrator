# -*- coding: utf-8 -*-
'''数字回溯校验：草稿生成后，把每个填入的数值和文字回到源文件对应区域核对，确认确实出现过。

校验对象（只看程序自动填入的内容；用户用 set 手工指定的、问题回答里的固定值不在此列）：
  测试数据   报告 fills 里来源为 S2:Sheet1!B27:B34 的实测值：取回源单元格原始值，格式化（小数位、有效数字）、
             取范围（最小~最大）、最大/最小/平均/中位、取负号之后要能还原
  规范表行   规范表逐行复制的单元格文字，必须由对应源表行的单元格文字拼成
  章节摘抄   章节摘抄里带来源段落的文字，必须出现在源段落里
  表单字段   型号、依据文件必须出现在源文件的标题或文件名里
  草稿回读   计划里的实测值与草稿对应单元格逐个对照（计划里有加行时跳过）

不一致的登记为 unverified：kind=source、verify=True，沿用终稿既有的“未核”拦截
（回答里没有对应的 key，只能改正后重出草稿，或经用户明确同意再 --accept-unverified）。
'''
from __future__ import annotations

import re

from . import testdata
from .common import fmt_number, norm_text, short, to_number

_NUM = r'[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?'
_REF_RE = re.compile(r'^([^!]+)!([A-Za-z]+)(\d+)(?::([A-Za-z]+)(\d+))?$')
_CELL_ID = re.compile(r'^T\d+\.R\d+\.C\d+$')


# ======================================================================= 通用工具
def col_idx(letters):
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def sheet_values(md, ref):
    '''Sheet1!B27:B34 -> 区域内非空单元格的原始值；找不到工作表或写法不对返回 None。'''
    m = _REF_RE.match(ref.strip())
    if not m:
        return None
    name, c0, r0, c1, r1 = m.groups()
    sh = next((s for s in md.m.get('sheets', []) if s['name'] == name), None)
    if sh is None:
        return None
    ca, cb = col_idx(c0), col_idx(c1 or c0)
    ra, rb = int(r0) - 1, int(r1 or r0) - 1
    vals = []
    for r in range(ra, min(rb, len(sh['rows']) - 1) + 1):
        row = sh['rows'][r]
        for c in range(ca, cb + 1):
            v = row[c] if c < len(row) else None
            if v is not None and str(v).strip() != '':
                vals.append(v)
    return vals


def _merge_rules(plan):
    rules = dict(testdata.DEFAULT_RULES)
    rules.update({k: v for k, v in (plan.get('rules') or {}).items() if v is not None})
    return rules


def _bad(res, target, detail):
    res['items'].append({'kind': 'source', 'verify': True, 'key': 'verify:' + target, 'name': target,
                         'detail': '数字回溯校验未通过：' + detail})


def _dec(tok):
    m = re.match(r'^[+-]?\d*\.(\d+)', tok)
    return len(m.group(1)) if m else 0


def _fmt_raw(nums, n=6):
    return ','.join(fmt_number(x, 6, 3) for x in nums[:n]) + ('…' if len(nums) > n else '')


def _candidates(nums, negate=False):
    '''源数值能还原出的所有候选：原值、最小、最大、平均、中位（取负号时再加相反数）。'''
    c = list(nums)
    if nums:
        s = sorted(nums)
        k = len(s) // 2
        c += [s[0], s[-1], sum(nums) / len(nums), s[k] if len(s) % 2 else (s[k - 1] + s[k]) / 2]
    return (c + [-x for x in c]) if negate else c


def _numeric_tokens(text, sep):
    '''把“最小~最大”拆成数字串；不是数字（如状态文字）返回 None。'''
    t = text.strip()
    parts = None
    for s in (sep, '~', '～', '至'):
        if s and s != '-' and s in t:
            ps = [x.strip() for x in t.split(s)]
            if len(ps) == 2 and all(ps):
                parts = ps
                break
    if parts is None:
        m = re.match(r'^(%s)\s*-\s*(%s)$' % (_NUM, _NUM), t)
        parts = [m.group(1), m.group(2)] if m else [t]
    return parts if all(re.fullmatch(_NUM, p) for p in parts) else None


def _matches(tok, cands, rules):
    '''tok 能由某个候选值按规则格式化还原：完全相同，或精度不低于规则小数位且误差不超过自身精度的一半。'''
    try:
        v = float(tok)
    except ValueError:
        return False
    dec = rules.get('decimals', 2)
    for c in cands:
        if fmt_number(c, dec, rules.get('min_sig', 3)) == tok.lstrip('+'):
            return True
        if _dec(tok) >= dec and abs(v - c) <= 0.5 * 10 ** (-_dec(tok)) + 1e-9:
            return True
    return False


def check_value(value, raws, rules, how=''):
    '''value：填入的文字；raws：源区域原始值。通过返回 None，否则返回不一致的原因。'''
    v = (value or '').strip()
    if not raws:
        return '源区域里没有数据'
    pass_t, fail_t = rules.get('pass_text', '合格'), rules.get('fail_text', '不合格')
    nums = [to_number(x) for x in raws if to_number(x) is not None]
    stats = [str(x).strip().upper() for x in raws if to_number(x) is None]
    if v in (pass_t, fail_t):
        if nums:
            return '源数据是数值，填入的却是“%s”' % v
        if v == pass_t and not all(s in testdata.PASS_WORDS for s in stats):
            return '源数据里有不是“通过”的结果（%s）' % ','.join(sorted(set(stats))[:4])
        if v == fail_t and not any(s in testdata.FAIL_WORDS for s in stats):
            return '源数据里没有“不通过”的结果'
        return None
    toks = _numeric_tokens(v, rules.get('range_sep', '~'))
    if toks is None:        # 文本结果（非通过/不通过的状态文字）
        parts = [p.strip().upper() for p in v.split('/') if p.strip()]
        return None if parts and all(p in stats for p in parts) else '源数据里找不到“%s”' % short(v, 20)
    cands = _candidates(nums, '取负' in how)
    for t in toks:
        if not _matches(t, cands, rules):
            return '“%s”在源数据（%s）里还原不出来（取范围、最大、最小、平均后都对不上）' % (t, _fmt_raw(nums))
    return None


# ======================================================================= 测试数据
def _testdata(plan, models, rules, res):
    for f in plan['report'].get('fills', []):
        m = re.match(r'^(S\d+):(.+!.+)$', f.get('source') or '')
        if not m or m.group(1) not in models or models[m.group(1)].m['type'] != 'xlsx':
            continue
        value, how, tgt = f.get('value', ''), f.get('how', ''), f.get('target', '')
        if value == '':
            continue        # 留空不是“填入”；留空由问题回答或“无数据”决定，已在报告里单列
        if '按回答填合格' in how or '按回答填写固定值' in how:
            res['skipped'].append('%s：%s' % (tgt, how))        # 用户回答决定的值，不是从源数据算出来的
            continue
        raws, miss = [], []
        for part in m.group(2).split(';'):
            vals = sheet_values(models[m.group(1)], part)
            if vals is None:
                miss.append(part)
            else:
                raws += vals
        res['checked'] += 1
        why = ('找不到源区域 %s' % '、'.join(miss)) if miss else check_value(value, raws, rules, how)
        if why:
            _bad(res, tgt, '%s 填入“%s”（来源 %s）：%s' % (tgt, short(value, 30), short(f.get('source', ''), 40), why))


# ======================================================================= 规范表行
# ======================================================================= 章节摘抄
# ======================================================================= 表单字段
# ======================================================================= 草稿回读
# ======================================================================= 入口
def verify_draft(job, plan, draft=None):
    '''返回 {'items': 未通过的登记项, 'checked': 核对项数, 'skipped': 由回答决定未核的项, 'notes': 说明}。'''
    res = {'items': [], 'checked': 0, 'skipped': [], 'notes': []}
    rules = _merge_rules(plan)
    models = job.source_models()
    _testdata(plan, models, rules, res)
    _spec_rows(plan, models, res)
    _sections(plan, models, res)
    _fields(plan, models, job.data.get('task', ''), res)
    _orphans(plan, res)
    if draft:
        _readback(plan, draft, res)
    return res


# ----------------------------------------------------------------------- 规范表行
def _src_row_texts(md, ref, cols=None):
    '''T3.R2 -> 源表该行（或其中指定列）的单元格文字，合并单元格已填充；找不到返回 None。'''
    m = re.match(r'^(T\d+)\.R(\d+)$', ref or '')
    if not m or md is None or m.group(1) not in md.by or md.by[m.group(1)].get('t') != 'tbl':
        return None
    grid = md.table_grid(m.group(1))[0]
    r = int(m.group(2))
    if r >= len(grid):
        return None
    row = grid[r] if cols is None else [grid[r][c] for c in cols if c < len(grid[r])]
    return [x for x in row if x and x.strip()]


def _covered(value, texts):
    '''value 由这些单元格文字拼成（允许去掉表注上标字母）；较长的说明文字因去掉上标而缺字时，按原顺序出现也算。'''
    full = norm_text(value)
    if not full:
        return True
    v, pieces = full, set()
    for t in texts:
        n = norm_text(t)
        pieces.add(n)
        if len(n) > 1 and n[-1] in 'abcde':
            pieces.add(n[:-1])
    for p in sorted(pieces, key=len, reverse=True):
        if p and p in v:
            v = v.replace(p, '', 1)
    if v == '':
        return True
    if len(full) < 6:
        return False
    it = iter(norm_text(' '.join(texts)))
    return all(ch in it for ch in full)


def _spec_rows(plan, models, res):
    '''规范表逐行复制（以及“与规范不一致”按规范覆盖）的单元格：必须能在登记的源表行、源列里找到。'''
    src = {}
    for o in plan['ops']:
        if o['op'] == 'comment' and (o.get('group') or '').startswith(('spec:', 'drift:')) and o.get('links'):
            src[(o['anchor'].split('.')[0], o['group'])] = (o['links'][0].get('alias'), o['links'][0].get('ref'))
    for o in plan['ops']:
        g = o.get('group') or ''
        if o['op'] != 'set_cell' or not g.startswith(('spec:', 'drift:')):
            continue
        value = ''.join(r[0] for r in o.get('runs', []))
        if not value.strip():
            continue
        alias, ref = src.get((o['cell'].split('.')[0], g), (None, None))
        texts = _src_row_texts(models.get(alias), ref, o.get('from_cols'))
        res['checked'] += 1
        if texts is None:
            _bad(res, o['cell'], '%s 填入“%s”，但没有登记可回查的源表行（%s）' % (o['cell'], short(value, 20), ref or '无'))
        elif not _covered(value, texts):
            _bad(res, o['cell'], '%s 填入“%s”，源表 %s:%s 的对应单元格（%s）里没有' % (o['cell'], short(value, 20), alias, ref, short(' | '.join(texts), 30)))


# ----------------------------------------------------------------------- 章节摘抄
def _para_ok(md, bid, text):
    b = md.get(bid)
    if not b:
        return '找不到源段落 %s' % bid
    v = norm_text(text)
    if not v or v in norm_text(b.get('text') or '') or v in norm_text('%s %s' % (b.get('label') or '', b.get('text') or '')):
        return None
    return '摘抄的文字在源段落 %s 里找不到' % bid


def _sections(plan, models, res):
    for o in plan['ops']:
        if o['op'] != 'insert_section':
            continue
        where = '章节“%s”' % short(o.get('requirement') or o.get('anchor', ''), 20)
        for it in o.get('items', []):
            alias = (it.get('src') or {}).get('alias')
            md = models.get(alias)
            if md is None:
                continue        # 待补充占位：没有来源内容可核
            for g in it.get('group', []):
                tp, why = g.get('type'), None
                if tp == 'para' and g.get('src') and not g.get('placeholder'):
                    why = _para_ok(md, g['src'], ''.join(r[0] for r in g.get('runs', []) if not str(r[1]).startswith('@')))
                elif tp == 'table':
                    why = None if md.by.get(g.get('table')) else '找不到源表 %s' % g.get('table')
                elif tp == 'figure':
                    why = None if md.by.get(g.get('fig')) else '找不到源图 %s' % g.get('fig')
                elif tp == 'kvtable':
                    blob = norm_text(' '.join((md.get(x) or {}).get('text', '') for x in (g.get('src') or [])))
                    miss = [r[1] for r in g.get('rows', []) if norm_text(r[1]) not in blob or norm_text(r[2]) not in blob]
                    why = ('列表项在源段落里找不到：' + '、'.join(short(x, 12) for x in miss[:3])) if miss else None
                else:
                    continue
                res['checked'] += 1
                if why:
                    _bad(res, where, '%s（%s）：%s' % (where, alias, why))


# ----------------------------------------------------------------------- 表单字段
def _fields(plan, models, task, res):
    '''型号、依据文件：必须出现在源文件标题或文件名里；括号里的版本说明要在标题或任务描述里。'''
    docs = [m for m in models.values() if m.m['type'] != 'xlsx']
    hay = norm_text(' '.join('%s %s' % (m.m.get('title', ''), m.m.get('name', '')) for m in docs))
    for f in plan['report'].get('fills', []):
        if f.get('how') not in ('型号识别', '依据文件'):
            continue
        v = f.get('value', '')
        core = re.sub(r'[（(][^（）()]*[）)]\s*$', '', v)
        ver = re.search(r'[（(]([^（）()]*)[）)]\s*$', v)
        res['checked'] += 1
        why = None
        if norm_text(core) not in hay:
            why = '“%s”在源文件标题和文件名里找不到' % short(core, 24)
        elif ver and norm_text(ver.group(1)) not in hay + norm_text(task):
            why = '括号里的“%s”既不在源文件标题里，也不在任务描述里' % short(ver.group(1), 16)
        if why:
            _bad(res, f.get('target', ''), '%s 填入“%s”：%s' % (f.get('target', ''), short(v, 30), why))


# ----------------------------------------------------------------------- 无出处的写入
def _orphans(plan, res):
    '''每个自动写入的实测值格，审核报告里都必须有对应的来源登记；有写入没出处的不能放行。'''
    reg = {f.get('target'): f.get('value', '') for f in plan['report'].get('fills', [])}
    for o in plan['ops']:
        if o['op'] == 'set_cell' and (o.get('group') or '').startswith('data:'):
            text = ''.join(r[0] for r in o.get('runs', []))
            if reg.get(o['cell']) != text:
                _bad(res, o['cell'], '%s 写入“%s”，但没有对应的来源登记（审核报告里查不到出处）' % (o['cell'], short(text, 20)))


# ----------------------------------------------------------------------- 草稿回读
def _readback(plan, draft, res):
    '''草稿回读：计划写入的格子，草稿里真的是这个值（写入失败、被后面的操作覆盖都会暴露）。
    计划里有加行或插入章节时块编号会整体移位，无法按编号回读：登记在 notes 里，来源回查照常进行。'''
    kinds = {o['op'] for o in plan['ops']}
    if kinds & {'add_rows', 'insert_section'}:
        res['notes'].append('计划含加行/插入章节，块编号会移位，草稿回读已跳过')
        return
    want = {}
    for o in plan['ops']:
        if o['op'] == 'set_cell' and o.get('cell'):
            want[o['cell']] = ''.join(r[0] for r in o.get('runs', []))
    if not want:
        return
    try:
        from .finalize import texts_by_id
        got = dict(texts_by_id(draft))
    except Exception as e:      # 回读不了不等于没问题：登记并拦截，不静默跳过
        _bad(res, '草稿回读', '草稿没能回读核对（%s），写入是否成功没有验证' % short(str(e), 60))
        return
    res['readback'] = len(want)
    for cid, text in want.items():
        if norm_text(got.get(cid, '')) != norm_text(text):
            _bad(res, cid, '%s 计划写入“%s”，草稿里实际是“%s”' % (cid, short(text, 20), short(got.get(cid, ''), 20)))


# ----------------------------------------------------------------------- 低置信度引用：读全回执
def mark_full_read(job, ref):
    '''show --full 读全之后登记回执：写进 answers.json（键 fullread:<引用>，绑定当前文件指纹）。
    终稿拦截沿用“未读取”项对 answers 的检查；文件换版后回执随旧回答一起失效。
    回执只证明“读全过”，不是用户对来源的确认。'''
    import datetime
    answers = job.read('answers.json', {}) or {}
    answers['fullread:' + ref] = {'label': '已用 show --full 读全', 'effect': {'ok': True, 'receipt': True},
                                  'fp': job.fingerprint(), 'answered_at': datetime.datetime.now().isoformat(timespec='seconds')}
    job.write('answers.json', answers)


def full_read_pending(job, report):
    '''report['need_full_read'] 里还没有读全回执（或回执已因文件换版失效）的引用。'''
    answers = job.read('answers.json', {}) or {}
    fp = job.fingerprint()
    return [x for x in (report.get('need_full_read') or []) if (answers.get('fullread:' + x.get('ref', '')) or {}).get('fp') != fp]


def full_read_items(pending):
    '''没读全的引用登记为“未读取”（沿用终稿对 unread 的拦截）。'''
    return [{'kind': 'unread', 'key': 'fullread:' + x['ref'], 'name': x['ref'],
             'detail': '%s 只按标题/近似命中（%s），还没用 show --full 读全核对' % (x['ref'], x.get('why', ''))} for x in pending]


def summary(res):
    s = '数字回溯校验：已核对 %d 项' % res['checked']
    s += ('，%d 项在源文件里还原不出来' % len(res['items'])) if res['items'] else '，都能在源文件里还原'
    if res['skipped']:
        s += '；另有 %d 项由用户回答决定，未做回溯' % len(res['skipped'])
    return s
