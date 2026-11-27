# -*- coding: utf-8 -*-
"""办公软件适配：自动探测 MS Office(2007~365) / WPS / LibreOffice，用于
   .doc/.wps/.xls/.et 等旧格式转换、导出 PDF/DOC、EMF/WMF 转 PNG。
   核心读写不依赖任何办公软件；只有遇到旧格式或需要导出时才调用。
   环境变量 DI_OFFICE=word|wps|libreoffice 可强制指定。"""
from __future__ import annotations

from collections import ChainMap
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

_PS_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
$jobs = Get-Content -LiteralPath $args[0] -Encoding UTF8
$resultFile = $args[1]; $progid = $args[2]; $kind = $args[3]
$results = @()
$app = $null
try {
  $app = New-Object -ComObject $progid
  try { $app.Visible = $false } catch {}
  try { $app.DisplayAlerts = 0 } catch {}
  if ($progid -in @('Word.Application', 'Excel.Application')) { $app.AutomationSecurity = 3 }
  foreach ($line in $jobs) {
    if (-not $line -or -not $line.Trim()) { continue }
    $p = $line.Split("`t"); [string]$src = $p[0]; [string]$dst = $p[1]; [int]$fmt = [int]$p[2]
    try {
      if ($kind -eq 'word') {
        if ($progid -eq 'Word.Application') {
          $doc = $app.Documents.OpenNoRepairDialog($src, $false, $true, $false)
        } else { $doc = $app.Documents.Open($src, $false, $true, $false) }
        $done = $false
        if ($fmt -eq 17) { try { $doc.ExportAsFixedFormat($dst, 17); $done = $true } catch {} }
        if (-not $done) { try { $doc.SaveAs([ref]$dst, [ref]$fmt); $done = $true } catch {} }
        if (-not $done) { try { $doc.SaveAs($dst, $fmt); $done = $true } catch {} }
        if (-not $done) { $doc.SaveAs2($dst, $fmt) }
        try { $doc.Close([ref]0) } catch { $doc.Close() }
      } else {
        $wb = $app.Workbooks.Open($src, 0, $true)
        if ($fmt -eq 57) { $wb.ExportAsFixedFormat(0, $dst) } else { $wb.SaveAs($dst, $fmt) }
        $wb.Close($false)
      }
      $results += "OK`t$dst"
    } catch { $results += "ERR`t$dst`t" + $_.Exception.Message }
  }
} catch { $results += "FATAL`t`t" + $_.Exception.Message }
finally {
  if ($app) { try { $app.Quit() } catch {}; try { [void][Runtime.Interopservices.Marshal]::ReleaseComObject($app) } catch {} }
}
[IO.File]::WriteAllLines($resultFile, [string[]]$results, (New-Object Text.UTF8Encoding($true)))
'''

_PS_IMG = r'''
Add-Type -AssemblyName System.Drawing
$jobs = Get-Content -LiteralPath $args[0] -Encoding UTF8
$results = @()
foreach ($line in $jobs) {
  if (-not $line -or -not $line.Trim()) { continue }
  $p = $line.Split("`t"); [string]$src = $p[0]; [string]$dst = $p[1]; [double]$scale = [double]$p[2]
  try {
    $img = [System.Drawing.Image]::FromFile($src)
    $w = [int]([Math]::Max(1, $img.Width * $scale)); $h = [int]([Math]::Max(1, $img.Height * $scale))
    if ($w -gt 3000) { $h = [int]($h * 3000 / $w); $w = 3000 }
    $bmp = New-Object System.Drawing.Bitmap $w, $h
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.Clear([System.Drawing.Color]::White)
    $g.SmoothingMode = 'HighQuality'
    $g.DrawImage($img, 0, 0, $w, $h)
    $bmp.Save($dst, [System.Drawing.Imaging.ImageFormat]::Png)
    $g.Dispose(); $bmp.Dispose(); $img.Dispose()
    $results += "OK`t$dst"
  } catch { $results += "ERR`t$dst`t" + $_.Exception.Message }
}
[IO.File]::WriteAllLines($args[1], [string[]]$results, (New-Object Text.UTF8Encoding($true)))
'''


def _reg_has(progid):
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid + r"\CLSID"):
            return True
    except Exception:
        return False


def _find_soffice():
    p = shutil.which("soffice") or shutil.which("libreoffice")
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
         "powershell": shutil.which("powershell") or shutil.which("pwsh")}
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
    sp = os.path.join(tmpdir, "run.ps1")
    with open(sp, "w", encoding="utf-8-sig") as f:      # 必须带 BOM，否则 PowerShell 5 按 GBK 读取
        f.write(script)
    ps = detect()["powershell"] or "powershell"
    cmd = [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", sp] + list(args)
    try:
        child_env = ChainMap({"TEMP": tmpdir, "TMP": tmpdir}, os.environ)
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, env=child_env,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
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


def _lo_convert(soffice, src, dst_fmt, dst):
    """Convert under a fresh profile and publish only the new output from this process."""
    filt = {"docx": "docx:MS Word 2007 XML", "doc": "doc:MS Word 97", "pdf": "pdf", "xlsx": "xlsx:Calc MS Excel 2007 XML",
            "xls": "xls:MS Excel 97", "png": "png", "csv": "csv"}.get(dst_fmt, dst_fmt)
    source, destination = Path(src).absolute(), Path(dst).absolute()
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="di_lo_", dir=str(destination.parent)) as work:
            stage = Path(work, "output")
            stage.mkdir()
            profile = Path(work, "profile").as_uri()
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
                    if workfile_error:
                        remaining = []
                        for src, dst, fmt in pending:
                            if dst not in errors and fmt == "docx" and os.path.splitext(src)[1].lower() == ".doc":
                                from .word_converter import try_convert_doc
                                if try_convert_doc(src, dst):
                                    results[dst] = None
                                    continue
                            errors.setdefault(dst, workfile_error)
                            remaining.append((src, dst, fmt))
                        pending = remaining
                        continue
                fmap = WORD_FMT if kind == "word" else EXCEL_FMT
                pairs = [(s, d, fmap[f]) for s, d, f in pending if f in fmap]
                try:
                    res = _com_batch(val, kind, pairs)
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
