# -*- coding: utf-8 -*-
"""办公软件适配：自动探测 MS Office(2007~365) / WPS / LibreOffice，用于
   .doc/.wps/.xls/.et 等旧格式转换、导出 PDF/DOC、EMF/WMF 转 PNG。
   核心读写不依赖任何办公软件；只有遇到旧格式或需要导出时才调用。
   环境变量 DI_OFFICE=word|wps|libreoffice 可强制指定。"""
from __future__ import annotations

from collections import ChainMap
from contextlib import ExitStack, contextmanager
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

from .common import DIError, file_sha1

WORD_FMT = {"docx": 16, "doc": 0, "pdf": 17, "rtf": 6, "txt": 7, "html": 10}
EXCEL_FMT = {"xlsx": 51, "xls": 56, "pdf": 57, "csv": 6}
WORD_INPUT = {".doc", ".docx", ".wps", ".wpt", ".rtf", ".dot", ".dotx", ".docm", ".odt", ".mht", ".htm", ".html"}
EXCEL_INPUT = {".xls", ".xlsx", ".xlsm", ".et", ".ett", ".ods", ".xlsb"}

_PS_SCRIPT = Path(__file__).with_name("convert_office.ps1")

_PS_IMG = Path(__file__).with_name("metafile_to_png.ps1")


def _reg_has(progid):
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid + r"\CLSID"):
            return True
    except Exception:
        return False


def _program_on_path(name):
    if os.name != "nt":
        return shutil.which(name)
    # Windows which() can put the current project ahead of PATH.
    cwd = Path.cwd()
    for directory in os.get_exec_path():
        folder = Path(directory.strip('"'))
        if folder.is_absolute() and Path(os.path.abspath(folder)) != cwd:
            executable = folder / (name + ".exe")
            if executable.is_file():
                return str(executable)
    return None


def _find_soffice():
    p = _program_on_path("soffice") or _program_on_path("libreoffice")
    if p:
        return p
    for c in (r"C:\Program Files\LibreOffice\program\soffice.exe",
              r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
              "/usr/bin/soffice", "/usr/local/bin/soffice",
              "/Applications/LibreOffice.app/Contents/MacOS/soffice",
              "/opt/libreoffice/program/soffice"):
        if os.path.exists(c):
            return c
    return None


_DETECTED = None


def detect():
    """返回 {'word': ProgID或None, 'excel': ..., 'wps_word': ..., 'wps_excel': ..., 'soffice': 路径或None}"""
    global _DETECTED
    if _DETECTED is not None:
        return _DETECTED
    d = {"word": None, "excel": None, "wps_word": None, "wps_excel": None, "soffice": None,
         "powershell": _program_on_path("powershell") or _program_on_path("pwsh")}
    if os.name == "nt":
        if _reg_has("Word.Application"):
            d["word"] = "Word.Application"
        if _reg_has("Excel.Application"):
            d["excel"] = "Excel.Application"
        for pid in ("KWps.Application", "Kwps.Application", "WPS.Application", "wps.Application"):
            if _reg_has(pid):
                d["wps_word"] = pid
                break
        for pid in ("KET.Application", "Ket.Application", "ET.Application", "et.Application"):
            if _reg_has(pid):
                d["wps_excel"] = pid
                break
    d["soffice"] = _find_soffice()
    _DETECTED = d
    return d


def describe():
    d = detect()
    items = []
    if d["word"] or d["excel"]:
        items.append("MS Office(" + ",".join(x for x in (d["word"] and "Word", d["excel"] and "Excel") if x) + ")")
    if d["wps_word"] or d["wps_excel"]:
        items.append("WPS(" + ",".join(x for x in (d["wps_word"] and "文字", d["wps_excel"] and "表格") if x) + ")")
    if d["soffice"]:
        items.append("LibreOffice")
    return "、".join(items) if items else "未检测到办公软件（仅能处理 docx/xlsx/md/pdf/csv/txt）"


def _engines(kind):
    d = detect()
    pref = (os.environ.get("DI_OFFICE") or "auto").lower()
    order = []
    ms = d["word"] if kind == "word" else d["excel"]
    wps = d["wps_word"] if kind == "word" else d["wps_excel"]
    cands = {"word": ("com", ms), "wps": ("com", wps), "libreoffice": ("lo", d["soffice"])}
    seq = [pref] + [x for x in ("word", "wps", "libreoffice") if x != pref] if pref in cands else ["word", "wps", "libreoffice"]
    for k in seq:
        typ, val = cands[k]
        if val and (typ != "com" or d["powershell"]):
            order.append((typ, val))
    return order


def _user_internet_cache():
    """Read the current user's configured Internet Cache shell folder (Word work files)."""
    import winreg
    key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as folders:
        configured, _ = winreg.QueryValueEx(folders, "Cache")
    return os.path.expandvars(configured)


def word_workfile_error():
    """Check the configured Word cache. The conversion process gets its own TEMP and TMP."""
    path = 'Content.Word'
    try:
        configured = _user_internet_cache()
        if not os.path.isabs(configured):
            raise OSError('Word 的 Internet Cache 未配置为绝对路径')
        path = os.path.join(configured, 'Content.Word')
        os.makedirs(path, exist_ok=True)
        with tempfile.TemporaryFile(dir=path):
            pass
    except OSError as exc:
        return ('Word 无法创建工作文件，当前进程不能使用 %s（%s）。'
                '请在日常桌面 Word 中打开原件并另存为 DOCX，再用该副本作为输入；'
                '如只需导出 PDF，可在桌面 Word 中导出。' % (path, exc))
    return None


def _run_ps(script, args, timeout=600):
    tmpdir = os.path.dirname(os.path.abspath(args[1]))
    ps = detect()["powershell"]
    if not ps:
        raise DIError("找不到已安装的 PowerShell")
    cmd = [ps, "-NoProfile", "-NonInteractive", "-File", str(script)] + list(args)
    try:
        child_env = ChainMap({"TEMP": tmpdir, "TMP": tmpdir}, os.environ)
        run = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, env=child_env,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if run.returncode:
            raise DIError("PowerShell 转换脚本未完成（退出码 %d），请检查脚本执行策略或使用 LibreOffice" % run.returncode)
    except subprocess.TimeoutExpired:
        raise DIError("办公软件转换超时（可能有弹窗或文件被占用）")


def _com_batch(progid, kind, pairs):
    if not pairs:
        return {}
    # Keep the control files and native scratch together, even if the host TEMP is unusable.
    parents = dict.fromkeys(os.path.dirname(os.path.abspath(dst)) for _, dst, _ in pairs)
    for parent in parents:
        try:
            work = tempfile.TemporaryDirectory(prefix="di_job_", dir=parent)
            break
        except OSError as exc:
            error = str(exc)
    else:
        return {"*": "无法在输出目录创建临时工作目录：" + error}
    with work as tmpdir:
        jf = os.path.join(tmpdir, "jobs.txt")
        rf = os.path.join(tmpdir, "result.txt")
        with open(jf, "w", encoding="utf-8-sig") as f:
            for src, dst, fmt in pairs:
                f.write("%s\t%s\t%d\n" % (os.path.abspath(src), os.path.abspath(dst), fmt))
        _run_ps(_PS_SCRIPT, [jf, rf, progid, kind])
        res = {}
        if os.path.exists(rf):
            with open(rf, encoding="utf-8-sig") as stream:
                for line in stream:
                    fields = line.rstrip("\r\n").split("\t")
                    if len(fields) >= 2 and fields[0] in ("OK", "ERR", "FATAL"):
                        key = os.path.normcase(os.path.abspath(fields[1])) if fields[1] else "*"
                        res[key] = None if fields[0] == "OK" else (fields[2] if len(fields) > 2 else "未知错误")
        return res


@contextmanager
def _lo_workspace(parent):
    from ._bootstrap import _long

    work = Path(tempfile.mkdtemp(prefix="di_lo_", dir=str(parent)))
    try:
        yield str(work)
    finally:
        # LibreOffice creates deep language-extension directories even for a one-page PDF.
        # Cleanup errors must not cause a successfully published file to be converted again.
        try:
            shutil.rmtree(_long(work))
        except OSError as exc:
            print("LibreOffice 临时目录未能清理：%s（%s）" % (work, exc), file=sys.stderr)


def _lo_convert(soffice, src, dst_fmt, dst):
    """Convert under a fresh profile and publish only the new output from this process."""
    filt = {"docx": "docx:MS Word 2007 XML", "doc": "doc:MS Word 97", "pdf": "pdf", "xlsx": "xlsx:Calc MS Excel 2007 XML",
            "xls": "xls:MS Excel 97", "png": "png", "csv": "csv"}.get(dst_fmt, dst_fmt)
    source, destination = Path(src).absolute(), Path(dst).absolute()
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with _lo_workspace(destination.parent) as work:
            stage = Path(work, "output")
            stage.mkdir()
            profile_dir = Path(work, "profile")
            user = profile_dir / "user"
            user.mkdir(parents=True)
            (user / "registrymodifications.xcu").write_text(
                '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
                '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
                '<prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop>'
                '<prop oor:name="BlockUntrustedRefererLinks" oor:op="fuse"><value>true</value></prop>'
                '</item></oor:items>', encoding="utf-8")
            profile = profile_dir.as_uri()
            cmd = [soffice, "-env:UserInstallation=" + profile, "--headless", "--norestore",
                   "--convert-to", filt, "--outdir", str(stage), str(source)]
            env = ChainMap({"TEMP": work, "TMP": work, "TMPDIR": work}, os.environ)
            run = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600,
                                 cwd=work, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if run.returncode:
                return "LibreOffice 转换失败（退出码 %d）" % run.returncode
            generated = stage / (source.stem + "." + dst_fmt)
            if generated.is_symlink() or not generated.is_file() or not generated.stat().st_size:
                return "LibreOffice 未生成有效的输出文件"
            os.replace(generated, destination)
    except subprocess.TimeoutExpired:
        return "LibreOffice 转换超时"
    except OSError as exc:
        return "LibreOffice 转换失败：%s" % exc
    return None


def _hold_excel_source(src, handles):
    """Keep the inspected file readable but deny writes and replacement until Excel closes it."""
    if os.name != "nt":
        return
    import ctypes as ct
    from ctypes import wintypes as wt
    kernel = ct.WinDLL("kernel32", use_last_error=True)
    open_file = kernel.CreateFileW
    open_file.argtypes = (wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID, wt.DWORD, wt.DWORD, wt.HANDLE)
    open_file.restype = wt.HANDLE
    handle = open_file(str(Path(src).absolute()), 0x80000000, 1, None, 3, 0, None)
    if handle == wt.HANDLE(-1).value:
        raise ct.WinError(ct.get_last_error())
    kernel.CloseHandle.argtypes = (wt.HANDLE,)
    handles.callback(kernel.CloseHandle, handle)


def _checked_com_batch(progid, kind, pairs, errors):
    if kind != "excel":
        return _com_batch(progid, kind, pairs)
    eligible = []
    with ExitStack() as handles:
        for src, dst, fmt in pairs:
            try:
                _hold_excel_source(src, handles)
            except OSError as exc:
                errors[dst] = "无法锁定待核对的工作簿：%s" % exc
                continue
            if _com_workbook(src):
                eligible.append((src, dst, fmt))
            else:
                errors[dst] = "无法排除 Excel 4.0 宏；需要 LibreOffice 转换或提供无宏 XLSX"
        return _com_batch(progid, kind, eligible) if eligible else {}


def _com_workbook(src):
    """Excel's ForceDisable excludes XLM. Route opaque and macro-sheet inputs to LibreOffice."""
    import posixpath
    import re
    import zipfile
    from xml.etree.ElementTree import ParseError
    from .ooxml import validate_package_limits
    from .safe_xml import parse_xml

    if Path(src).suffix.lower() == ".xls":
        from .legacy_excel import ordinary_xls
        return ordinary_xls(src)
    if Path(src).suffix.lower() not in (".xlsx", ".xlsm"):
        return False
    try:
        with zipfile.ZipFile(src) as archive:
            validate_package_limits(archive)
            types = parse_xml(archive.read("[Content_Types].xml"))
            content_types = {n.get("PartName"): n.get("ContentType", "").lower() for n in types}
            if content_types.get("/xl/workbook.xml") not in (
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
                    "application/vnd.ms-excel.sheet.macroenabled.main+xml"):
                return False
            if any("macrosheet" in n.get("ContentType", "").lower() for n in types):
                return False
            wb = parse_xml(archive.read("xl/workbook.xml"))
            rels = parse_xml(archive.read("xl/_rels/workbook.xml.rels"))
            ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
            rid = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
            # A static range is sufficient for ordinary names and print areas. Leave
            # procedural names and expressions that need evaluation to the fallback.
            cell = r"(?:\$?[A-Z]{1,3}\$?[1-9][0-9]*|\$?[A-Z]{1,3}|\$?[1-9][0-9]*)"
            static_range = re.compile(r"(?:'(?:[^']|'')+'|[\w.]+)!" + cell + "(?::" + cell + ")?", re.I)
            for name in wb.iter(ns + "definedName"):
                if (name.get("xlm") in ("true", "1") or name.get("function") in ("true", "1")
                        or name.get("name", "").lower().split(".")[-1] in ("auto_open", "auto_close", "auto_activate", "auto_deactivate")
                        or not all(static_range.fullmatch(ref.strip()) for ref in (name.text or "").split(","))):
                    return False
            relations = {r.get("Id"): r for r in rels if r.get("TargetMode") != "External"}
            sheets = wb.findall(ns + "sheets/" + ns + "sheet")
            members = set(archive.namelist())
            for sheet in sheets:
                relation = relations.get(sheet.get(rid))
                if relation is None:
                    return False
                kind = relation.get("Type", "").rsplit("/", 1)[-1]
                part = posixpath.normpath(posixpath.join("/xl", relation.get("Target", "")))
                if (kind not in ("worksheet", "chartsheet") or part[1:] not in members
                        or content_types.get(part) != "application/vnd.openxmlformats-officedocument.spreadsheetml." + kind + "+xml"):
                    return False
            return bool(sheets)
    except (DIError, OSError, RuntimeError, zipfile.BadZipFile, ParseError, KeyError, ValueError):
        return False


def convert_many(items):
    """items: [(src, dst, fmt_ext)] -> {dst: 错误信息或None}。同类文件在一个办公软件进程内批量完成。"""
    results = {}
    groups = {"word": [], "excel": []}
    for src, dst, fmt in items:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
        except OSError as exc:
            results[dst] = "无法创建输出目录：%s" % exc
            continue
        ext = os.path.splitext(src)[1].lower()
        kind = "excel" if ext in EXCEL_INPUT else "word"
        groups[kind].append((src, dst, fmt))
    for kind, lst in groups.items():
        if not lst:
            continue
        pending = list(lst)
        errors = {}
        for typ, val in _engines(kind):
            if not pending:
                break
            if typ == "com":
                if val == 'Word.Application':
                    workfile_error = word_workfile_error()
                    remaining = []
                    for src, dst, fmt in pending:
                        if dst not in errors and fmt == "docx" and os.path.splitext(src)[1].lower() == ".doc":
                            from .word_converter import eligible_word_2010_doc, try_convert_doc
                            if (workfile_error or eligible_word_2010_doc(src)) and try_convert_doc(src, dst):
                                results[dst] = None
                                continue
                        if workfile_error:
                            errors.setdefault(dst, workfile_error)
                        remaining.append((src, dst, fmt))
                    pending = remaining
                    if not pending or workfile_error:
                        continue
                fmap = WORD_FMT if kind == "word" else EXCEL_FMT
                pairs = [(s, d, fmap[f]) for s, d, f in pending if f in fmap]
                try:
                    res = _checked_com_batch(val, kind, pairs, errors)
                except (DIError, OSError) as exc:
                    res = {"*": str(exc)}
                still = []
                for s, d, f in pending:
                    key = os.path.normcase(os.path.abspath(d))
                    if key in res and res[key] is None and os.path.exists(d):
                        results[d] = None
                    else:
                        errors.setdefault(d, res.get(key) or res.get("*") or "转换失败")
                        still.append((s, d, f))
                pending = still
            else:
                still = []
                for s, d, f in pending:
                    failure = _lo_convert(val, s, f, d)
                    if failure is None:
                        results[d] = None
                    else:
                        errors[d] = errors[d] + " / " + failure if d in errors else failure
                        still.append((s, d, f))
                pending = still
        for s, d, f in pending:
            results[d] = errors.get(d) or ("未检测到可用的办公软件，无法转换 %s（%s）" % (os.path.basename(s), describe()))
    return results


def convert(src, fmt, dst=None, cache_dir=None):
    """单文件转换；cache_dir 提供时按内容哈希缓存。"""
    if dst is None:
        base = os.path.splitext(os.path.basename(src))[0]
        if cache_dir:
            dst = os.path.join(cache_dir, "%s_%s.%s" % (base, file_sha1(src, 10), fmt))
        else:
            dst = os.path.join(tempfile.mkdtemp(prefix="di_cv_"), base + "." + fmt)
    if os.path.exists(dst) and cache_dir:
        return dst
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    res = convert_many([(src, dst, fmt)])
    err = res.get(dst)
    if err or not os.path.exists(dst):
        raise DIError("转换失败：%s -> %s：%s" % (os.path.basename(src), fmt, err))
    return dst


def metafile_to_png(pairs, scale=2.0):
    """[(emf/wmf, png)] -> 成功列表。Windows 用 GDI+，其他系统用 LibreOffice。"""
    ok = []
    if os.name == "nt" and detect()["powershell"]:
        tmpdir = tempfile.mkdtemp(prefix="di_img_")
        jf, rf = os.path.join(tmpdir, "jobs.txt"), os.path.join(tmpdir, "res.txt")
        with open(jf, "w", encoding="utf-8-sig") as f:
            for s, d in pairs:
                f.write("%s\t%s\t%s\n" % (os.path.abspath(s), os.path.abspath(d), scale))
        _run_ps(_PS_IMG, [jf, rf], timeout=300)
        if os.path.exists(rf):
            for ln in open(rf, encoding="utf-8-sig").read().splitlines():
                p = ln.split("\t")
                if p[0] == "OK":
                    ok.append(p[1])
        return ok
    so = detect()["soffice"]
    if so:
        for s, d in pairs:
            if _lo_convert(so, s, "png", d) is None:
                ok.append(d)
    return ok
