"""Identify tool-authored Word documents even when renamed or moved to another project."""
import hashlib
import json
from pathlib import Path
import re
from xml.etree import ElementTree as ET

from .common import TOOL_AUTHOR
from .ooxml import w

CUSTOM = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
VALUE = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"
CUSTOM_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties"
CUSTOM_TYPE = "application/vnd.openxmlformats-officedocument.custom-properties+xml"
FIELD = "TTF-Generated"


def part_name(pkg):
    for _, kind, target, mode in pkg.rels("").items():
        if kind == CUSTOM_REL and mode != "External":
            return pkg.resolve("", target)
    return "docProps/custom.xml"


def generated(pkg):
    part = part_name(pkg)
    if pkg.has(part) and any(el.get("name") == FIELD for el in pkg.xml(part)):
        return True
    return pkg.has("word/comments.xml") and any(
        el.get(w("author")) == TOOL_AUTHOR for el in pkg.xml("word/comments.xml").findall(w("comment")))


def mark(pkg, job, version_name, kind):
    path = part_name(pkg)
    if pkg.has(path):
        root = pkg.xml(path)
    else:
        root = ET.Element("{%s}Properties" % CUSTOM)
        pkg.new_xml(path, root, [("", CUSTOM), ("vt", VALUE)])
    if not any(kind == CUSTOM_REL and mode != "External" for _, kind, _, mode in pkg.rels("").items()):
        pkg.rels("").add(CUSTOM_REL, path)
    pkg.ensure_override(path, CUSTOM_TYPE)
    prop = next((el for el in root if el.get("name") == FIELD), None)
    if prop is None:
        pid = max([1] + [int(el.get("pid")) for el in root if (el.get("pid") or "").isdigit()]) + 1
        prop = ET.SubElement(root, "{%s}property" % CUSTOM, name=FIELD,
                             fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}", pid=str(pid))
    for child in list(prop):
        prop.remove(child)
    main = "word/document.xml"
    data = pkg.part_bytes(main)
    version = re.search(r"_v([0-9]+)\.docx$", Path(version_name).name, re.I)
    legacy_id = hashlib.sha256((job.dir + job.data.get("created", "")).encode("utf-8")).hexdigest()[:32]
    record = {"job": job.data.setdefault("document_id", legacy_id), "kind": kind,
              "version": int(version[1]) if version else 1, "body_sha256": hashlib.sha256(data).hexdigest()}
    ET.SubElement(prop, "{%s}lpwstr" % VALUE).text = json.dumps(record, sort_keys=True, separators=(",", ":"))

