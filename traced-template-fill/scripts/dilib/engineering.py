"""Text matching for specification symbols, SI units and document search. Original values stay unchanged."""
import re
import unicodedata


# The increment sign, Greek delta and triangular marker can look identical on a form.
DELTA = unicodedata.lookup("GREEK CAPITAL LETTER DELTA")
TRIANGLE = unicodedata.lookup("WHITE UP-POINTING TRIANGLE")
INCREMENT = unicodedata.lookup("INCREMENT")
MU = unicodedata.lookup("GREEK SMALL LETTER MU")
MICRO = unicodedata.lookup("MICRO SIGN")
OMEGA = unicodedata.lookup("GREEK CAPITAL LETTER OMEGA")


def symbol_aliases(config):
    """An explicitly paired base alias can also name the change in that parameter."""
    aliases = {}
    for label, target in config.items():
        if label.startswith("_"):
            continue
        if isinstance(target, dict):
            symbol = target["symbol"]
            aliases[label] = symbol
            if target.get("also_delta") is True:
                aliases[DELTA + label] = DELTA + symbol
        else:
            aliases[label] = target
    return aliases


def norm_text(s):
    """匹配用归一化：全角转半角、去空白、统一括号与符号、小写。"""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.replace(TRIANGLE, DELTA).replace(INCREMENT, DELTA)
    s = re.sub(r"\s+", "", s)
    return s.lower()


def bigrams(s):
    s = norm_text(s)
    toks = set()
    # ASCII 单词整体作为 token
    for w in re.findall(rf"[a-z0-9{DELTA.lower()}+\-#.]+", s):
        toks.add(w)
    han = re.sub(rf"[a-z0-9{DELTA.lower()}+\-#.()（）:：,，;；、。/\\\[\]【】]", " ", s)
    for seg in han.split():
        if len(seg) == 1:
            toks.add(seg)
        for i in range(len(seg) - 1):
            toks.add(seg[i:i + 2])
    return toks


def norm_sym(s):
    s = norm_text(s).upper()
    s = re.sub(rf"[^0-9A-Z{DELTA}+\-]", "", s)
    return s


def extract_symbol(text):
    """Return the trailing engineering symbol, including its original change marker."""
    m = re.search(rf"([{TRIANGLE}{DELTA}A-Za-z][A-Za-z0-9{TRIANGLE}{DELTA}+\-]*)\s*$", text or "")
    return m.group(1) if m else ""


def token_variants(tok, prefixes):
    t = tok.upper()
    out = [t]
    for p in prefixes:
        if t.startswith(p.upper()) and len(t) > len(p):
            out.append(DELTA + t[len(p):])
    return out


def unit_key(unit):
    value = unicodedata.normalize("NFKC", unit.strip())
    return re.sub(rf"^[u{MU}](?=[A-Za-z{OMEGA}])", MU, value)


_SI_PREFIX = f"[fpnum{MICRO}{MU}kMGT]?"
MEASUREMENT_UNIT = re.compile(
    rf"(?:^|[0-9])(?:{_SI_PREFIX}(?:V|A|W|F|H|Hz|{OMEGA}|Ohm|s|S|m|g|J|Pa|rad|dB|bps|bit/s)"
    rf"(?:/{_SI_PREFIX}(?:V|A|Hz|s|m))?"
    r"|°[CF]?|℃|[％%])$"
)
