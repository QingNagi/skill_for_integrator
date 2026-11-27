"""Convert local legacy Word files using the converter from the machine's registered Office installation."""
import configparser
import os
from pathlib import Path
import subprocess
import tempfile
from xml.etree.ElementTree import ParseError
import zipfile

from .ooxml import w
from .safe_xml import parse_xml


_OLE = bytes.fromhex("D0CF11E0A1B11AE1")
_DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
_OVERRIDE = "{http://schemas.openxmlformats.org/package/2006/content-types}Override"
_MAX_ZONE_BYTES = 16 * 1024


def _installed_converter():
    if os.name != "nt":
        return None
    import winreg
    key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Winword.exe"
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key, 0, winreg.KEY_READ | view) as handle:
                value, _ = winreg.QueryValueEx(handle, "")
            word = Path(os.path.expandvars(value.strip('"')))
            converter = word.with_name("Wordconv.exe")
            if word.is_absolute() and word.name.lower() == "winword.exe" and converter.is_file():
                return str(converter)
        except (OSError, ValueError):
            continue
    return None


def _local_legacy_document(source):
    with source.open("rb") as stream:
        if stream.read(len(_OLE)) != _OLE:
            return False
    # Leave Internet/Restricted or unrecognized origin metadata to Office's protected-file workflow.
    try:
        with open(str(source) + ":Zone.Identifier", "rb") as stream:
            origin = stream.read(_MAX_ZONE_BYTES + 1)
    except FileNotFoundError:
        return True
    if len(origin) > _MAX_ZONE_BYTES:
        return False
    settings = configparser.ConfigParser(interpolation=None)
    try:
        settings.read_string(origin.decode("utf-8-sig"))
        return settings.get("ZoneTransfer", "ZoneId", fallback="") in ("0", "1", "2")
    except (configparser.Error, UnicodeError):
        return False


def _valid_docx(path):
    with zipfile.ZipFile(path) as package:
        if any(name.rsplit("/", 1)[-1].lower() == "vbaproject.bin" for name in package.namelist()):
            return False
        with package.open("[Content_Types].xml") as stream:
            types = parse_xml(stream)
        if not any(part.get("PartName") == "/word/document.xml" and part.get("ContentType") == _DOCX_CONTENT_TYPE
                   for part in types.findall(_OVERRIDE)):
            return False
        with package.open("word/document.xml") as stream:
            document = parse_xml(stream)
        return document.tag == w("document") and document.find(w("body")) is not None and package.testzip() is None


def try_convert_doc(src, dst):
    """Return True only after atomically publishing a verified .docx. Retain any previous output on failure."""
    source, destination = Path(src).absolute(), Path(dst).absolute()
    if source.suffix.lower() != ".doc" or destination.suffix.lower() != ".docx":
        return False
    converter = _installed_converter()
    if not converter:
        return False
    try:
        if not _local_legacy_document(source):
            return False
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="di_conv_", dir=str(destination.parent)) as work:
            staged = Path(work) / "converted.docx"
            env = {name: os.environ[name] for name in ("SystemRoot", "WINDIR", "SystemDrive", "OS", "ComSpec",
                   "ProgramFiles", "ProgramFiles(x86)", "CommonProgramFiles", "CommonProgramFiles(x86)",
                   "USERPROFILE", "APPDATA", "LOCALAPPDATA") if name in os.environ}
            env.update(TEMP=work, TMP=work)
            if env.get("SystemRoot"):
                env["PATH"] = str(Path(env["SystemRoot"]) / "System32")
            finished = subprocess.run([converter, "-oice", "-nme", str(source), str(staged)], cwd=work, env=env,
                                      stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                      timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if finished.returncode or not _valid_docx(staged):
                return False
            os.replace(staged, destination)
        return True
    except (OSError, subprocess.TimeoutExpired, zipfile.BadZipFile, ParseError, KeyError, RuntimeError):
        return False
