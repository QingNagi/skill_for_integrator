"""检查本机 Office 和可选读取后端，供 doctor 汇总。"""
import os
import re
import sys

from .child_process import child_environment


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
        r = subprocess.run([path, '--version'], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=20, env=child_environment(),
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


def environment_lines():
    '''doctor 汇总：Python、办公软件、PDF 库、OCR 后端、vendor 状态。返回行列表。'''
    py = sys.version_info
    lines = ['Python %s（%s）%s' % (sys.version.split()[0], sys.executable, '' if py >= (3, 10) else '  [严重] 低于 3.10，本工具不支持')]
    from . import __version__
    lines.insert(0, 'traced-template-fill ' + __version__)
    lines += _office_lines()
    try:
        from . import _bootstrap as bs
    except Exception as e:
        bs = None
        lines.append('PDF/OCR/vendor：没能加载 _bootstrap（%s: %s），已跳过' % (type(e).__name__, e))
    if bs is not None:
        try:
            active = bs.activate_vendor()
            lines.append('vendor（离线依赖）：' + ('已加载' if active else '未加载，检查当前 Python 已安装的包'))
            lines.append('PDF 库：')
            try:
                import pdfplumber
                lines.append('  [有] pdfplumber %s（文本和表格）' % pdfplumber.__version__)
            except (ImportError, OSError) as exc:
                lines.append('  [无] pdfplumber（文本和表格）：%s' % exc)
            try:
                import pypdfium2
                lines.append('  [有] pypdfium2 %s（渲染和裁图）' % getattr(pypdfium2, 'PYPDFIUM_INFO', ''))
            except (ImportError, OSError) as exc:
                lines.append('  [无] pypdfium2（渲染和裁图）：%s' % exc)
            try:
                import pypdf
                lines.append('  [有] pypdf %s（文本降级）' % pypdf.__version__)
            except (ImportError, OSError) as exc:
                lines.append('  [无] pypdf（文本降级）：%s' % exc)
            for note in bs.notes()[:4]:
                lines.append('  [降级原因] ' + note)
            oc = bs.ocr_hint(probe=True)
            lines.append('OCR 后端：[%s] %s' % ('有' if oc.get('available') else '无', oc.get('hint', '')))
        except Exception as exc:
            lines.append('PDF/OCR 探测失败：%s' % exc)
    try:
        import xlrd
    except (ImportError, OSError) as exc:
        lines.append('xlrd（.xls 读取，可选）：无，.xls 会改用办公软件转换（%s）' % exc)
    else:
        lines.append('xlrd（.xls 读取，可选）：有')
    lines.append('核心功能（docx/xlsx/csv/md/txt 读写、批注、链接、结构自检）只需 Python 3.10+ 标准库。')
    return lines
