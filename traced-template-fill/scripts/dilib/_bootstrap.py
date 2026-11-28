"""Offline PDF libraries and the pinned local Tesseract executable."""
import base64
import csv
import hashlib
import importlib
import importlib.metadata
import io
import json
import os
import platform
import stat
import sys
import sysconfig
import tempfile
import time
import zipfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_VENDOR = _ROOT / "vendor"
_notes = []
_cache = {}
OCR_LANGUAGE = "chi_sim+eng"
OCR_DPI = 300


def _note(text):
    if text not in _notes:
        _notes.append(text)


def notes():
    return list(_notes)


def wheel_manifest():
    return json.loads((_ROOT / "vendor/wheels.lock.json").read_text(encoding="utf-8"))


def _digest(stream):
    result = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        result.update(chunk)
    return result


def sha256_of(path):
    with open(path, "rb") as stream:
        return _digest(stream).hexdigest()


def is_good(path, size, sha):
    try:
        return os.path.getsize(path) == size and sha256_of(path) == sha
    except OSError:
        return False


def platform_tag():
    if sys.maxsize <= 2 ** 32:
        return None
    if sys.platform == "win32" and sysconfig.get_platform() == "win-amd64":
        return "win_amd64"
    arch = platform.machine().lower()
    if sys.platform.startswith("linux") and arch in ("x86_64", "aarch64"):
        return "manylinux_" + arch
    return None


def _long(path):
    """The embedded Windows runtime also works with long paths on unmanaged PCs."""
    path = os.path.abspath(path)
    if os.name == "nt" and not path.startswith("\\\\?\\"):
        path = "\\\\?\\UNC\\" + path[2:] if path.startswith("\\\\") else "\\\\?\\" + path
    return Path(path)


def _link(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _verified_cache(dest, archive):
    if not dest.is_dir() or _link(dest.lstat()):
        return False
    names = {entry.filename for entry in archive.infolist() if not entry.is_dir()}
    record = next(n for n in names if n.endswith(".dist-info/RECORD"))
    expected = archive.read(record)
    if (dest / record).read_bytes() != expected:
        return False
    # A cached directory is an import root. Reject injected files and reparse points.
    actual = set()
    for folder, dirs, files in os.walk(dest):
        for item in dirs + files:
            path = Path(folder) / item
            if _link(path.lstat()):
                return False
        actual.update((Path(folder) / item).relative_to(dest).as_posix() for item in files)
    if names != actual:
        return False
    checked = {record}
    for name, h, length in csv.reader(io.StringIO(expected.decode("utf-8"))):
        if name == record:
            continue
        path = dest / name
        if path.stat().st_size != int(length):
            return False
        with path.open("rb") as src:
            value = base64.urlsafe_b64encode(_digest(src).digest()).decode("ascii").rstrip("=")
        if h != "sha256=" + value:
            return False
        checked.add(name)
    return checked == actual


def _publish_wheel(stage, dest):
    # On Windows a file observer can hold a child without delete sharing.
    # The extracted tree stays private until a single rename succeeds.
    deadline = time.monotonic() + 5
    delay = 0.05
    while True:
        try:
            os.rename(stage, dest)
            return
        except OSError as exc:
            if dest.exists():
                return  # A concurrent publisher won. The caller verifies its tree.
            if getattr(exc, "winerror", None) not in (5, 32, 33) or time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.4)


def _unpack(path, spec, cache):
    name, size, digest, _ = spec
    with path.open("rb") as stream:
        if path.name != name or os.fstat(stream.fileno()).st_size != size or _digest(stream).hexdigest() != digest:
            raise ValueError("轮子校验失败：" + name)
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            cache.mkdir(parents=True, exist_ok=True)
            dest = cache / digest
            if not dest.exists():
                with tempfile.TemporaryDirectory(dir=cache) as tmp:
                    stage = Path(tmp) / "wheel"
                    archive.extractall(stage)
                    _publish_wheel(stage, dest)
            if not _verified_cache(dest, archive):
                raise ValueError("轮子缓存校验失败：" + str(dest))
    return str(dest)


def _vendor_root():
    return _long(os.environ.get("DI_VENDOR_DIR") or _VENDOR)


def _vendor_packages():
    lock = wheel_manifest()
    root = _vendor_root()
    tag = platform_tag()
    full = (root / "wheels" / (tag or "unsupported")).is_dir() and sys.version_info[:2] == (3, 14)
    packages = [(root / "py", spec) for key, spec in lock["pure"].items()
                if full or key in ("pypdf", "typing_extensions")]
    if full:
        packages.extend((root / "wheels" / tag, spec) for spec in lock["native"][tag].values())
    return full, packages


def delivery_mode():
    """Source installs use host libraries; kits keep their advertised dependency set."""
    if os.environ.get("DI_NO_VENDOR"):
        return "system"
    if (_ROOT / "runtime/python").is_dir():
        return "full"
    marker = _ROOT / "vendor/pdf-mode.txt"
    if marker.is_file():
        mode = marker.read_text(encoding="ascii").strip()
        if mode not in ("full", "lite"):
            from .common import DIError
            raise DIError("PDF 组件清单损坏，请恢复离线套装。")
        return mode
    return "source"


def activate_vendor():
    """Load the exact private wheels into this interpreter, preserving a pypdf-only kit."""
    if os.environ.get("DI_NO_VENDOR"):
        return False
    if "activated" in _cache:
        return _cache["activated"]
    full, packages = _vendor_packages()
    mode = delivery_mode()
    if mode == "full" and not full:
        from .common import DIError
        raise DIError("套装缺少匹配的 PDF 原生组件，请恢复离线套装。")
    default = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    cache = _long(os.environ.get("DI_CACHE_DIR") or default / "traced-template-fill") / "pdf-wheels-v2"
    paths = []
    sys.dont_write_bytecode = True
    try:
        for folder, spec in packages:
            paths.append(_unpack(folder / spec[0], spec, cache))
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        if mode in ("full", "lite"):
            from .common import DIError
            raise DIError("套装的 PDF 组件未通过校验，请恢复离线套装：" + str(exc)) from exc
        _note("内置 PDF 依赖不可用：" + str(exc))
        return False
    sys.path[:0] = [p for p in paths if p not in sys.path]
    importlib.invalidate_caches()
    _cache["activated"] = True
    return True


def use_pypdf():
    activate_vendor()
    try:
        return importlib.import_module("pypdf")
    except ImportError as exc:
        _note("没有可用的 pypdf：" + str(exc))
        return None


def _parser_signature():
    if "parser_signature" not in _cache:
        if not os.environ.get("DI_NO_VENDOR"):
            full, packages = _vendor_packages()
            if full:
                # A lookup reads only an existing JSON model. Actual parsing still
                # authenticates all wheels and every extracted file in activate_vendor.
                try:
                    present = all((folder / spec[0]).stat().st_size == spec[1] for folder, spec in packages)
                except OSError:
                    present = False
                if present:
                    pins = ' '.join(sorted(spec[2] for _, spec in packages)).encode('ascii')
                    signature = 'pinned-' + hashlib.sha256(pins).hexdigest()
                    _cache["parser_signature"] = signature
                    return signature
        activate_vendor()
        versions = []
        for name in ("pdfplumber", "pdfminer.six", "pypdfium2", "pypdf", "Pillow"):
            try:
                version = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                version = "none"
            versions.append(name + "=" + version)
        _cache["parser_signature"] = ",".join(versions)
    return _cache["parser_signature"]


def parsed_signature(options, backend):
    # A transient verification failure can fall back to system libraries. Revisit
    # that parse after repair instead of publishing it as a pinned-reader result.
    if options['pinned_reader'] and (not _cache.get('activated') or backend != 'pdfplumber'):
        return None
    return options['signature']


def ocr_hint(probe=False):
    tag = platform_tag()
    override = os.environ.get("DI_TESSERACT")
    executable = _long(override or _ROOT / "runtime/ocr" / ("tesseract.exe" if os.name == "nt" else "tesseract"))
    models = _long(os.environ.get("TESSDATA_PREFIX") or _ROOT / "runtime/tessdata")
    res = {"available": False, "engine": None, "executable": str(executable), "tessdata": str(models),
           "language": OCR_LANGUAGE, "hint": "缺少本地 Tesseract 或语言数据。"}
    signature = "missing"
    if os.environ.get("DI_OCR", "").strip().lower() == "off":
        signature, res["hint"] = "off", "DI_OCR=off 已关闭扫描页识别。"
    else:
        try:
            manifest = json.loads((_ROOT / "vendor/ocr.lock.json").read_text(encoding="utf-8"))
            if override:
                engine = sha256_of(executable)
            else:
                pin = manifest["binaries"][tag]
                if not is_good(executable, pin["size"], pin["sha256"]):
                    raise ValueError("本地 OCR 程序缺失或 SHA256 校验失败")
                engine = pin["sha256"]
            fingerprints = [engine]
            for language in OCR_LANGUAGE.split("+"):
                name = language + ".traineddata"
                pin = manifest["models"][name]
                if not is_good(models / name, pin["size"], pin["sha256"]):
                    raise ValueError("语言数据缺失或 SHA256 校验失败：" + name)
                fingerprints.append(pin["sha256"])
            signature = ":".join(fingerprints)
            res.update(available=True, engine="Tesseract", hint="本地 Tesseract 简体中文和英文识别。")
            if probe:
                activate_vendor()
                from PIL import Image
                from .pdf_ocr import recognize_image
                with Image.open(_ROOT / "assets/offline-ocr.png") as image:
                    lines = recognize_image(image, res)
                sample = "".join("".join(line["text"].split()) for line in lines)
                if "中文离线识别" not in sample or "OFFLINEDOCUMENT2026" not in sample.upper():
                    raise ValueError("中英文样张识别失败")
                res["hint"] += "中英文样张自测通过。"
        except (OSError, ValueError, KeyError, ImportError, RuntimeError) as exc:
            res.update(available=False, engine=None, hint=str(exc))
    parser = _parser_signature()
    res["pinned_reader"] = parser.startswith('pinned-')
    from .pdf_ocr import scan_policy
    res["scan_policy"] = scan_policy()
    res["ocr_identity"] = signature
    res["dpi"] = OCR_DPI
    res["signature"] = "pdf-ocr-v5:%s:%s:%s:%s:%s" % (
        parser, OCR_LANGUAGE, OCR_DPI, signature, json.dumps(res["scan_policy"], sort_keys=True))
    return res
