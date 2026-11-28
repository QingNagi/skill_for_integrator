"""Convert local legacy Word files using the converter from the machine's registered Office installation."""
import configparser
import os
from pathlib import Path
import struct
import subprocess
import tempfile
from xml.etree.ElementTree import ParseError
import zipfile

from .common import DIError
from .ooxml import w
from .ooxml import validate_package_limits
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


def _file_block_configured(major):
    """Let Word evaluate configured Trust Center and administrative File Block settings."""
    if os.name != 'nt':
        return False
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            for prefix in (r'SOFTWARE\Microsoft', r'SOFTWARE\Policies\Microsoft'):
                for section in ('FileBlock', 'FileOpenBlock', 'FileSaveBlock'):
                    key = prefix + r'\Office\%s.0\Word\Security\%s' % (major, section)
                    try:
                        with winreg.OpenKey(hive, key, 0, winreg.KEY_READ | view) as handle:
                            subkeys, values, _ = winreg.QueryInfoKey(handle)
                        if subkeys or values:
                            return True
                    except FileNotFoundError:
                        continue
                    except OSError:
                        return True
    return False


def _standard_word_binary(source):
    """Recognise ordinary Word.Document.8 with an unencrypted Word97+ FIB.

    The first directory sector suffices for typical Word saves. Unusual stream
    layouts, templates and earlier Word formats stay with the desktop reader.
    """
    with source.open('rb') as handle:
        header = handle.read(512)
        if len(header) < 512 or header[:8] != _OLE:
            return False
        shift = struct.unpack_from('<H', header, 30)[0]
        if shift not in (9, 12):
            return False
        sector_size = 1 << shift
        directory = struct.unpack_from('<I', header, 48)[0]
        handle.seek((1 + directory) * sector_size)
        entries = handle.read(sector_size)
        # Word.Document.8 CLSID (little-endian GUID) in the CFB root entry.
        if entries[80:96] != bytes.fromhex('0609020000000000c000000000000046'):
            return False
        name = 'WordDocument'.encode('utf-16-le') + b'\x00\x00'
        for offset in range(128, len(entries) - 127, 128):
            if entries[offset:offset + len(name)] != name or entries[offset + 66] != 2:
                continue
            start, length = struct.unpack_from('<IQ', entries, offset + 116)
            if length < 4096:  # A mini-stream needs the full CFB allocation tables.
                return False
            handle.seek((start + 1) * sector_size)
            fib = handle.read(12)
            if len(fib) < 12:
                return False
            magic, version = struct.unpack_from('<HH', fib)
            flags = struct.unpack_from('<H', fib, 10)[0]
            return magic == 0xa5ec and version >= 0xc1 and not flags & 0x8101
    return False


def eligible_word_2010_doc(src):
    from .environment import _progid_ver
    if os.name != 'nt' or _progid_ver('Word.Application') != 14:
        return False
    from ctypes import windll
    try:
        path = Path(src).resolve(strict=True)
        if windll.kernel32.GetDriveTypeW(path.anchor) != 3:
            return False
        # Windows uses these directories for cached Internet content and Outlook attachments.
        unsafe = {'content.outlook', 'content.ie5', 'inetcache', 'temporary internet files'}
        return not unsafe.intersection(p.casefold() for p in path.parts) and _standard_word_binary(path)
    except (OSError, ValueError):
        return False


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
        validate_package_limits(package)
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
    if os.name == 'nt':
        from .environment import _progid_ver
        major = _progid_ver('Word.Application')
        if not major or _file_block_configured(major):
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
    except (DIError, OSError, subprocess.TimeoutExpired, zipfile.BadZipFile, ParseError, KeyError, RuntimeError):
        return False
