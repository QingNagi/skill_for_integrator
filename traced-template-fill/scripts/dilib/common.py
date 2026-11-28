# -*- coding: utf-8 -*-
"""通用工具：控制台输出、JSON读写、哈希、中文字号、文本归一化与相似度。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import unicodedata
import uuid

TOOL_AUTHOR = "文档整合助手"      # 草稿批注作者名（定稿时据此删除）
TOOL_INITIALS = "AI"


# ---------------------------------------------------------------- 控制台
def setup_console():
    """输出编码：被管道读取（模型/脚本调用）时用 UTF-8；人在 cmd 窗口直接运行时沿用控制台编码。
    DI_UTF8=1 强制 UTF-8，DI_UTF8=0 强制控制台编码。"""
    force = os.environ.get("DI_UTF8")
    for stream in (sys.stdout, sys.stderr):
        try:
            tty = stream.isatty()
            if force == "1" or (force != "0" and not tty):
                stream.reconfigure(encoding="utf-8", errors="replace")
            else:
                stream.reconfigure(errors="replace")
        except Exception:
            pass


def out(*parts):
    print(*parts, flush=True)


class DIError(Exception):
    """可预期的错误：只打印消息，不打印堆栈。"""


class ReviewBlocked(DIError):
    """The requested document needs a reviewer decision or corrected input."""


# ---------------------------------------------------------------- 文件
def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def write_json(path, data, compact=False):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp = os.path.join(directory, ".di-" + uuid.uuid4().hex + ".tmp")
    stream = open(tmp, "x", encoding="utf-8")
    try:
        with stream:
            if compact:
                json.dump(data, stream, ensure_ascii=False, separators=(",", ":"))
            else:
                json.dump(data, stream, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def cache_hasher():
    """Keep cache keys and saved answers compatible with previous releases."""
    return hashlib.new("sha1", usedforsecurity=False)


def file_sha1(path, n=16):
    h = cache_hasher()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:n]


def ensure_dir(p):
    os.makedirs(p, exist_ok=True)
    return p


def safe_name(s, maxlen=60):
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", s).strip(" .")
    return s[:maxlen] or "unnamed"


def contains_model_code(text, code):
    """Match a whole product code, including its revision suffix, alongside Chinese text."""
    if not code:
        return False
    return bool(re.search(r'(?<![a-z0-9-])' + re.escape(code) + r'(?![a-z0-9]|-[a-z0-9])',
                          text or '', flags=re.IGNORECASE | re.ASCII))


def lock_file_of(path):
    """Word/WPS 打开文件时产生的 ~$ 锁文件路径（存在说明文件正被编辑）。"""
    d, b = os.path.split(path)
    cands = ["~$" + b[2:], "~$" + b[1:], "~$" + b] if len(b) > 2 else ["~$" + b]
    for c in cands:
        p = os.path.join(d, c)
        if os.path.exists(p):
            return p
    return None


# ---------------------------------------------------------------- 中文字号
CN_SIZES = {
    "初号": 42, "小初": 36, "一号": 26, "小一": 24, "二号": 22, "小二": 18,
    "三号": 16, "小三": 15, "四号": 14, "小四": 12, "五号": 10.5, "小五": 9,
    "六号": 7.5, "小六": 6.5, "七号": 5.5, "八号": 5,
}


def parse_size(v):
    """'小四' / '12' / '12pt' / '10.5磅' -> 磅值(float)。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if s in CN_SIZES:
        return float(CN_SIZES[s])
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(pt|磅|号)?$", s, re.I)
    if m:
        return float(m.group(1))
    raise DIError("无法识别的字号：%s" % v)


# ---------------------------------------------------------------- 文本
_FW = {ord(c): ord(c) - 0xFEE0 for c in "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ"}


def norm_text(s):
    """匹配用归一化：全角转半角、去空白、统一括号与符号、小写。"""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.translate(_FW)
    s = s.replace("（", "(").replace("）", ")").replace("△", "Δ").replace("∆", "Δ")
    s = re.sub(r"\s+", "", s)
    return s.lower()


def bigrams(s):
    s = norm_text(s)
    toks = set()
    # ASCII 单词整体作为 token
    for w in re.findall(r"[a-z0-9δ+\-#.]+", s):
        toks.add(w)
    han = re.sub(r"[a-z0-9δ+\-#.()（）:：,，;；、。/\\\[\]【】]", " ", s)
    for seg in han.split():
        if len(seg) == 1:
            toks.add(seg)
        for i in range(len(seg) - 1):
            toks.add(seg[i:i + 2])
    return toks


def token_scores(query, candidate):
    """Return query coverage and similarity using one intersection."""
    shared = len(query & candidate)
    if not shared:
        return 0.0, 0.0
    union_size = len(query) + len(candidate) - shared
    similarity = shared / min(len(query), len(candidate)) * 0.7 + shared / union_size * 0.3
    return shared / len(query), similarity


def sim(a, b):
    """基于二元组的相似度（0~1），对中文短语足够稳健且无需分词库。"""
    return token_scores(bigrams(a), bigrams(b))[1]


def contains_score(query, text):
    """query 的 token 在 text 中出现的比例。"""
    Q = bigrams(query)
    if not Q:
        return 0.0
    T = bigrams(text)
    return len(Q & T) / len(Q)


def short(s, n=60):
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def runs_text(runs):
    return "".join(r[0] for r in runs)


# ---------------------------------------------------------------- 数字格式
_NUM_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


_NUMERIC_GLYPHS = str.maketrans("−－–＋～µ", "---+~μ")


def numeric_text(value):
    """Normalize measurement notation while preserving unit case and source text."""
    return str(value).strip().translate(_NUMERIC_GLYPHS)


def to_number(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if v is None:
        return None
    s = numeric_text(v).replace("，", ",")
    if _NUM_RE.match(s):
        return float(s)
    return None


def fmt_number(x, decimals=2, min_sig=3, max_decimals=6, strip_zeros=True):
    """默认规则：保留 decimals 位小数，但至少保证 min_sig 位有效数字；去掉末尾 0。
    例：107.347968->107.35, -0.888689->-0.889, 0.004105->0.00411, 40.0->40"""
    if x is None:
        return ""
    if x == 0:
        return "0"
    import math
    ax = abs(x)
    d = decimals
    if min_sig:
        mag = int(math.floor(math.log10(ax)))          # 最高位数量级
        need = min_sig - 1 - mag                         # 达到 min_sig 位有效数字需要的小数位
        d = max(d, need)
    d = max(0, min(d, max_decimals))
    s = ("%." + str(d) + "f") % x
    if strip_zeros and "." in s:
        s = s.rstrip("0").rstrip(".")
    if s in ("-0", "+0"):
        s = "0"
    return s
