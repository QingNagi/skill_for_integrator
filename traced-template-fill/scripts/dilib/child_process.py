"""Environment needed by local Office, OCR and draft workers."""
import os
from pathlib import Path


_PLATFORM = (
    "SystemRoot", "WINDIR", "SystemDrive", "OS", "ComSpec", "PATHEXT", "ProgramData", "ALLUSERSPROFILE",
    "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "CommonProgramFiles", "CommonProgramFiles(x86)",
    "CommonProgramW6432", "PROCESSOR_ARCHITECTURE", "PROCESSOR_ARCHITEW6432", "NUMBER_OF_PROCESSORS",
    "COMPUTERNAME", "USERNAME", "USERDOMAIN", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA",
    "LOCALAPPDATA", "PUBLIC", "SESSIONNAME", "HOME", "USER", "LOGNAME", "TMPDIR", "TEMP", "TMP",
    "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "LC_MESSAGES", "LC_NUMERIC", "LC_TIME", "LC_COLLATE",
    "LC_MONETARY", "LC_PAPER", "LC_NAME", "LC_ADDRESS", "LC_TELEPHONE", "LC_MEASUREMENT",
    "LC_IDENTIFICATION", "TZ", "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS",
    "XDG_RUNTIME_DIR", "XDG_DATA_DIRS", "XDG_DATA_HOME", "XDG_CONFIG_DIRS", "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME", "FONTCONFIG_PATH", "FONTCONFIG_FILE",
)
_OCR = ("TESSDATA_PREFIX", "OMP_THREAD_LIMIT", "OMP_NUM_THREADS")
_WORKER = (
    "DI_OFFICE", "DI_UTF8", "DI_USER_DATA_DIR", "DI_RUNTIME_DIR", "DI_CACHE_DIR", "DI_VENDOR_DIR",
    "DI_NO_VENDOR", "DI_TIMING", "DI_TESSERACT", "DI_OCR", "DI_OCR_MIXED_MIN_AREA",
    "DI_OCR_SPARSE_MIN_AREA", "DI_OCR_BODY_MARGIN", "DI_OCR_NATIVE_CHARS",
)


def program_directories():
    """Use absolute PATH entries, excluding aliases of the current project directory."""
    cwd = os.path.normcase(os.path.realpath(os.curdir))
    for entry in os.get_exec_path():
        path = entry.strip('"') if os.name == "nt" else entry
        if os.path.isabs(path) and os.path.normcase(os.path.realpath(path)) != cwd:
            yield path


def child_environment(*, scratch=None, worker=False, ocr=False):
    keys = _PLATFORM
    if worker or ocr:
        keys += _OCR
    if worker:
        keys += _WORKER
    env = {key: os.environ[key] for key in keys if key in os.environ}
    env["PATH"] = os.pathsep.join(program_directories())
    if scratch is not None:
        env.update(TEMP=scratch, TMP=scratch, TMPDIR=scratch)
    return env


def tesseract_path(value):
    path = Path(value)
    name = "tesseract.exe" if os.name == "nt" else "tesseract"
    if (not path.is_absolute() or os.path.normcase(path.name) != name
            or not path.is_file() or not os.access(path, os.X_OK)):
        raise ValueError("请指定本地 Tesseract 可执行文件的绝对路径（%s）" % name)
    return str(path)
