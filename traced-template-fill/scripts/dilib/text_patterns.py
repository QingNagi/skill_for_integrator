"""Shared heading and caption patterns for Word, PDF and text readers."""
import re

HEAD_PATTERNS = [
    (1, re.compile(r"^第[一二三四五六七八九十百零\d]+[章篇部分]")),
    (2, re.compile(r"^[一二三四五六七八九十]+[、．.]")),
    (3, re.compile(r"^\d+[、]")),
    # 用先行断言而不“吃掉”编号后的首字，sub() 去编号时才不会截掉标题第一个字
    (3, re.compile(r"^\d+\.(?!\d)\s*(?=[^\d\s])")),
    (4, re.compile(r"^\d+\.\d+(?!\.\d)\s*(?=[^\d\s.])")),
    (5, re.compile(r"^\d+\.\d+\.\d+\s*(?=[^\d\s.])")),
    (6, re.compile(r"^[（(][一二三四五六七八九十]+[)）]")),
    (7, re.compile(r"^[（(]\d+[)）]")),
]
CAPTION_RE = re.compile(r"^\s*(表|图|附表|附图|Table|Figure|Fig\.)\s*([A-Z]?[\d.\-－]+)", re.I)
