# -*- coding: utf-8 -*-
"""办公软件适配：自动探测 MS Office(2007~365) / WPS / LibreOffice，用于
   .doc/.wps/.xls/.et 等旧格式转换、导出 PDF/DOC、EMF/WMF 转 PNG。
   核心读写不依赖任何办公软件；只有遇到旧格式或需要导出时才调用。
   环境变量 DI_OFFICE=word|wps|libreoffice 可强制指定。"""
from __future__ import annotations

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
  foreach ($line in $jobs) {
    if (-not $line -or -not $line.Trim()) { continue }
    $p = $line.Split("`t"); [string]$src = $p[0]; [string]$dst = $p[1]; [int]$fmt = [int]$p[2]
    try {
      if ($kind -eq 'word') {
        $doc = $app.Documents.Open($src, $false, $true, $false)
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


def _run_ps(script, args, timeout=600):
    tmpdir = tempfile.mkdtemp(prefix="di_ps_")
    sp = os.path.join(tmpdir, "run.ps1")
    with open(sp, "w", encoding="utf-8-sig") as f:      # 必须带 BOM，否则 PowerShell 5 按 GBK 读取
        f.write(script)
    ps = detect()["powershell"] or "powershell"
    cmd = [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", sp] + list(args)
    try:
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise DIError("办公软件转换超时（可能有弹窗或文件被占用）")
    return tmpdir


def _com_batch(progid, kind, pairs):
    tmpdir = tempfile.mkdtemp(prefix="di_job_")
    jf = os.path.join(tmpdir, "jobs.txt")
    rf = os.path.join(tmpdir, "result.txt")
    with open(jf, "w", encoding="utf-8-sig") as f:
        for src, dst, fmt in pairs:
            f.write("%s\t%s\t%d\n" % (os.path.abspath(src), os.path.abspath(dst), fmt))
    _run_ps(_PS_SCRIPT, [jf, rf, progid, kind])
    res = {}
    if os.path.exists(rf):
        for ln in open(rf, encoding="utf-8-sig").read().splitlines():
            p = ln.split("\t")
            if p[0] == "OK":
                res[os.path.normcase(os.path.abspath(p[1]))] = None
            elif p[0] in ("ERR", "FATAL"):
                res[os.path.normcase(os.path.abspath(p[1])) if p[1] else "*"] = p[2] if len(p) > 2 else "未知错误"
    return res


def _lo_convert(soffice, src, dst_fmt, out_dir):
    filt = {"docx": "docx:MS Word 2007 XML", "doc": "doc:MS Word 97", "pdf": "pdf", "xlsx": "xlsx:Calc MS Excel 2007 XML",
            "xls": "xls:MS Excel 97", "png": "png", "csv": "csv"}.get(dst_fmt, dst_fmt)
    prof = os.path.join(tempfile.gettempdir(), "di_lo_profile")
    url = "file:///" + prof.replace("\\", "/").lstrip("/")
    cmd = [soffice, "-env:UserInstallation=" + url, "--headless", "--norestore", "--convert-to", filt,
           "--outdir", out_dir, src]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600)
    cand = os.path.join(out_dir, os.path.splitext(os.path.basename(src))[0] + "." + dst_fmt)
    return cand if os.path.exists(cand) else None


def convert_many(items):
    """items: [(src, dst, fmt_ext)] -> {dst: 错误信息或None}。同类文件在一个办公软件进程内批量完成。"""
    results = {}
    groups = {"word": [], "excel": []}
    for src, dst, fmt in items:
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
                fmap = WORD_FMT if kind == "word" else EXCEL_FMT
                pairs = [(s, d, fmap[f]) for s, d, f in pending if f in fmap]
                res = _com_batch(val, kind, pairs)
                still = []
                for s, d, f in pending:
                    key = os.path.normcase(os.path.abspath(d))
                    if key in res and res[key] is None and os.path.exists(d):
                        results[d] = None
                    else:
                        errors[d] = res.get(key) or res.get("*") or "转换失败"
                        still.append((s, d, f))
                pending = still
            else:
                still = []
                for s, d, f in pending:
                    out_dir = tempfile.mkdtemp(prefix="di_lo_")
                    got = _lo_convert(val, s, f, out_dir)
                    if got:
                        os.makedirs(os.path.dirname(os.path.abspath(d)), exist_ok=True)
                        shutil.move(got, d)
                        results[d] = None
                    else:
                        errors[d] = "LibreOffice 转换失败"
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
            got = _lo_convert(so, s, "png", os.path.dirname(os.path.abspath(d)))
            if got:
                if os.path.abspath(got) != os.path.abspath(d):
                    shutil.move(got, d)
                ok.append(d)
    return ok
