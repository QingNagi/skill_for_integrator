"""检查本机 Office 和可选读取后端，供 doctor 汇总。"""
import os
import re
import sys


# ---- 环境汇总（doctor 用）：每一项探测失败都降级成文字，不抛异常
WORD_VER = {12: '2007', 14: '2010', 15: '2013', 16: '2016/2019/2021/365'}


def _progid_ver(progid):
    '''读注册表 ProgID\\CurVer 里的主版本号（不启动办公软件）；非 Windows 或读不到返回 None。'''
    if os.name != 'nt':
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid + '\\CurVer') as k:
            v = winreg.QueryValue(k, '')
        m = re.search(r'[.](\d+)$', v or '')
        return int(m.group(1)) if m else None
    except Exception:
        return None


def _lo_version(path):
    import subprocess
    try:
        r = subprocess.run([path, '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        s = r.stdout.decode('utf-8', 'replace').strip()
        return s.splitlines()[0].strip() if s else None
    except Exception:
        return None


def _office_lines():
    try:
        from . import office
        d = office.detect()
    except Exception as e:
        return ['办公软件：探测失败（%s），不影响 docx/xlsx 的读写' % type(e).__name__]
    out = []
    for key, label in (('word', 'Word'), ('excel', 'Excel')):
        if d.get(key):
            v = _progid_ver(d[key])
            out.append('  [有] MS %s %s' % (label, ('%s（%s）' % (v, WORD_VER.get(v, '版本未识别'))) if v else '（版本未读到）'))
    for key, label in (('wps_word', 'WPS 文字'), ('wps_excel', 'WPS 表格')):
        if d.get(key):
            v = _progid_ver(d[key])
            out.append('  [有] %s（接口 %s%s；没有实机验证）' % (label, d[key], '，注册表版本号 %s' % v if v else ''))
    if d.get('soffice'):
        out.append('  [有] LibreOffice：%s（%s）' % (_lo_version(d['soffice']) or '版本未读到', d['soffice']))
    if not out:
        out.append('  [无] 未检测到 Word / WPS / LibreOffice：只能处理 docx/xlsx/md/pdf/csv/txt，旧格式和导出 PDF 需要装其中一个')
    if not d.get('powershell') and os.name == 'nt':
        out.append('  [注意] 没找到 PowerShell：Word/WPS 的 COM 调用不可用')
    pref = os.environ.get('DI_OFFICE')
    return ['办公软件：' + ('已指定 DI_OFFICE=%s' % pref if pref else '自动选择（Word → WPS → LibreOffice）')] + out


def _modver(mod):
    for a in ('__version__', 'VersionBind'):
        v = getattr(mod, a, None)
        if v:
            return str(v)
    try:
        return str(mod.version[0])
    except Exception:
        return None


def _vendor_lines(bs):
    out = ['vendor（离线包）：']
    try:
        root = getattr(bs, '_VENDOR', None)
        if not root or not os.path.isdir(root):
            return ['vendor（离线包）：没有 vendor 目录；PDF 只能用本机已装的库，离线机器上会降级']
        if os.environ.get('DI_NO_VENDOR'):
            out.append('  [注意] 已设置 DI_NO_VENDOR=1，vendor 被跳过')
        plat = bs.platform_tag()
        if not plat:
            out.append('  [无] 本平台（%s）没有对应的 PyMuPDF 轮子目录' % sys.platform)
        else:
            whl = bs._find_wheel(plat)
            out.append('  [%s] PyMuPDF 轮子：%s' % ('有' if whl else '无', os.path.basename(whl) if whl else '%s 下没有适合当前 Python %d.%d 的 %s 版轮子' % (plat, sys.version_info[0], sys.version_info[1], bs.PINNED_PYMUPDF)))
        out.append('  [%s] vendor/py（纯 Python 降级库 pypdf）' %
                   ('有' if os.path.isdir(os.path.join(root, 'py')) else '无'))
    except Exception as e:
        out.append('  [注意] vendor 状态没查全：%s' % type(e).__name__)
    return out


def environment_lines():
    '''doctor 汇总：Python、办公软件、PDF 库、OCR 后端、vendor 状态。返回行列表。'''
    py = sys.version_info
    lines = ['Python %s（%s）%s' % (sys.version.split()[0], sys.executable, '' if py >= (3, 8) else '  [严重] 低于 3.8，本工具不支持')]
    lines += _office_lines()
    try:
        from . import _bootstrap as bs
    except Exception as e:
        bs = None
        lines.append('PDF/OCR/vendor：没能加载 _bootstrap（%s: %s），已跳过' % (type(e).__name__, e))
    if bs is not None:
        lines.append('PDF 库：')
        try:
            m = bs.use_pymupdf()
            lines.append('  [%s] pymupdf - PDF 读取（推荐）%s' % ('有' if m else '无', '，版本 %s' % _modver(m) if m else ''))
            m = bs.use_pypdf()
            lines.append('  [%s] pypdf - PDF 文本（降级）%s' % ('有' if m else '无', '，版本 %s' % _modver(m) if m else ''))
            import importlib.util
            lines.append('  [%s] pdfplumber - PDF 读取（备选）' % ('有' if importlib.util.find_spec('pdfplumber') else '无'))
            for n in bs.notes()[:4]:
                lines.append('  [降级原因] ' + n)
        except Exception as e:
            lines.append('  [注意] PDF 库探测没跑完：%s: %s' % (type(e).__name__, e))
        try:
            oc = bs.ocr_hint(probe=True)
            lines.append('OCR 后端：[%s] %s' % ('有' if oc.get('available') else '无', oc.get('hint', '')))
        except Exception as e:
            lines.append('OCR 后端：探测失败（%s）' % type(e).__name__)
        lines += _vendor_lines(bs)
    try:
        import importlib.util
        lines.append('xlrd（.xls 读取，可选）：%s' % ('有' if importlib.util.find_spec('xlrd') else '无，.xls 会改用办公软件转换'))
    except Exception:
        pass
    lines.append('核心功能（docx/xlsx/csv/md/txt 读写、批注、链接、结构自检）只需 Python 3.8+ 标准库。')
    return lines
