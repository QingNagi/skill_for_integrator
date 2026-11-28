"""CycloneDX inventory of the interpreter, wheels, OCR executable, sources and models."""
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path
import zipfile

import fetch_vendor as vendor_tools
from build_offline_bundle import wheel_specs
from prepare_win64_runtime import MODELS, MODEL_REV, MODEL_URL


def component(kind, name, version, digest, size, installed, license_path, url=None, license_name=None):
    item = {
        "type": kind, "name": name, "version": version, "bom-ref": installed,
        "hashes": [{"alg": "SHA-256", "content": digest}],
        "properties": [
            {"name": "bytes", "value": str(size)},
            {"name": "installed:path", "value": installed},
            {"name": "installed:license", "value": license_path},
        ],
    }
    if url:
        item["externalReferences"] = [{"type": "distribution", "url": url}]
    if license_name:
        item["licenses"] = [{"license": {"name": license_name}}]
    return item


def ocr_lock():
    lock = Path(vendor_tools.ROOT) / "vendor" / "ocr.lock.json"
    return json.loads(lock.read_text(encoding="utf-8"))


def ocr_binary(platform, vendor):
    spec = ocr_lock()["binaries"][platform]
    binary = Path(vendor) / "ocr" / platform / spec["name"]
    if not vendor_tools.is_good(str(binary), spec["size"], spec["sha256"]):
        raise ValueError("Missing or invalid pinned OCR engine: " + str(binary))
    return binary


def runtime_bom(platform, python_version, url, size, sha, python_license, vendor):
    parts = [component("platform", "CPython", python_version, sha, size, "runtime/python",
                       python_license, url, "Python distribution licenses")]
    for rel, package, (filename, size, digest, path) in wheel_specs(platform):
        with zipfile.ZipFile(Path(vendor) / rel) as archive:
            meta, = (n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))
            metadata = BytesParser().parsebytes(archive.read(meta))
            notices = [n for n in archive.namelist() if
                       any(word in n.lower() for word in ("license", "copying", "copyright")) and not n.endswith("/")]
            if not notices:
                raise ValueError("Wheel has no license notices: " + filename)
            if package == "pypdfium2":
                info = json.loads(archive.read("pypdfium2_raw/version.json"))
                native_version = ".".join(str(info[k]) for k in ("major", "minor", "build", "patch"))
                lib, = (n for n in archive.namelist() if n.startswith("pypdfium2_raw/") and n.endswith((".dll", ".so")))
                native = archive.read(lib)
                member_path = "vendor/" + rel + "!/"
                parts.append(component("library", "PDFium", native_version, hashlib.sha256(native).hexdigest(),
                                       len(native), member_path + lib, "; ".join(member_path + n for n in notices)))
        location = "vendor/" + rel
        item = component("library", package, metadata["Version"], digest, size, location,
                         location + "!/" + notices[0], vendor_tools.MIRRORS["pypi"] + path + "/" + filename,
                         metadata.get("License-Expression"))
        item["properties"].extend({"name": "wheel:notice", "value": name} for name in sorted(notices))
        parts.append(item)
    lock = ocr_lock()
    engine = lock["binaries"][platform]
    tesseract = lock["sources"]["tesseract"]
    item = component("application", "Tesseract", tesseract["version"], engine["sha256"], engine["size"],
                     "runtime/ocr/" + engine["name"], tesseract["notice"], license_name=tesseract["license"])
    for source, spec in lock["sources"].items():
        item.setdefault("externalReferences", []).append({
            "type": "distribution", "url": spec["url"], "comment": source + " " + spec["version"],
            "hashes": [{"alg": "SHA-256", "content": spec["sha256"]}],
        })
        item["properties"].append({"name": "source:license:" + source, "value": spec["notice"]})
    parts.append(item)
    for filename, (length, digest, _) in MODELS.items():
        kind = "file" if filename == "LICENSE" else "machine-learning-model"
        parts.append(component(kind, "tessdata_fast/" + filename, MODEL_REV, digest, length,
                               "runtime/tessdata/" + filename, "runtime/tessdata/LICENSE",
                               MODEL_URL + filename, "Apache-2.0"))
    bom = {
        "bomFormat": "CycloneDX", "specVersion": "1.6", "version": 1,
        "metadata": {"component": {
            "type": "platform", "name": "traced-template-fill offline runtime", "version": platform,
            "description": "Hashes identify the upstream interpreter archive, original wheels and models, "
                           "and the bundled OCR executable. Wheel notices cover their embedded libraries.",
        }},
        "components": parts,
    }
    return (json.dumps(bom, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
