# -*- coding: utf-8 -*-
'''可选依赖统一入口：PyMuPDF、pypdf、OCR 探测。

约定：use_* 缺包返回 None，绝不抛异常；调用方据此降级，并用 notes() 取原因登记。
延迟导入：只在被调用时才探测和导入，import 本模块本身不触碰任何第三方包。
环境变量：DI_NO_VENDOR=1 跳过 vendor（测试隔离用）；DI_CACHE_DIR 指定轮子解压位置。
'''
import base64
import csv
import hashlib
import io
import json
import importlib
import os
import stat
import sys
import tempfile
import zipfile

PINNED_PYMUPDF = '1.28.2'


def _local_io_path(path):
    """Use the wide Windows filesystem API even when long paths are disabled by policy."""
    if os.name != 'nt':
        return path
    path = os.path.normpath(os.path.abspath(path))
    prefix = '\\\\?\\'
    if path.startswith(prefix):
        return path
    if path.startswith('\\\\'):
        return prefix + 'UNC\\' + path[2:]
    return prefix + path


_ROOT = _local_io_path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_VENDOR = os.path.join(_ROOT, 'vendor')
_cache = {}
_notes = []


def _note(msg):
    if msg not in _notes:
        _notes.append(msg)


def notes():
    '''降级原因（简短中文），供调用方登记。'''
    return list(_notes)


def _use_vendor():
    return not os.environ.get('DI_NO_VENDOR')


def init():
    """保留启动入口；PDF 库在首次使用时校验并延迟加载。"""


def wheel_manifest():
    with open(os.path.join(_ROOT, 'vendor', 'wheels.lock.json'), encoding='utf-8') as src:
        return json.load(src)


def _digest(stream):
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b''):
        digest.update(block)
    return digest


def sha256_of(path):
    with open(path, 'rb') as src:
        return _digest(src).hexdigest()


def is_good(path, size, sha):
    try:
        return os.path.isfile(path) and os.path.getsize(path) == size and sha256_of(path) == sha
    except OSError:
        return False


def platform_tag():
    '''返回本机对应 vendor/wheels 下的目录名；不支持返回 None。'''
    try:
        if sys.maxsize <= 2 ** 32:
            return None
        if sys.platform.startswith('win'):
            # The wheel must match the interpreter ABI, even under Windows emulation.
            import sysconfig
            return 'win_amd64' if sysconfig.get_platform() == 'win-amd64' else None
        import platform
        m = platform.machine().lower()
        x64 = m in ('amd64', 'x86_64')
        arm = m in ('arm64', 'aarch64')
        if sys.platform == 'darwin':
            return 'macosx_x86_64' if x64 else ('macosx_arm64' if arm else None)
        if sys.platform.startswith('linux'):
            return 'manylinux_x86_64' if x64 else ('manylinux_aarch64' if arm else None)
    except (OSError, ValueError) as exc:
        _note('无法识别本机平台：' + str(exc))
    return None


def _import_first(names, need=None):
    for n in names:
        try:
            mod = importlib.import_module(n)
        except Exception as exc:
            if not isinstance(exc, ModuleNotFoundError) or exc.name != n:
                _note('导入 %s 失败：%s' % (n, exc))
            continue
        if need is None or hasattr(mod, need):
            return mod
    return None


def _wheel_usable(name):
    '''按文件名里的 Python 标签判断当前解释器能否用（abi3 要求不低于标注版本）。'''
    parts = name[:-4].split('-')
    if len(parts) < 5:
        return False
    py, abi = parts[-3], parts[-2]
    if not (py.startswith('cp') and py[2:].isdigit() and len(py) > 3):
        return True
    need = (int(py[2]), int(py[3:]))
    cur = sys.version_info[:2]
    return cur >= need if abi == 'abi3' else cur == need


def _wheel_candidate(plat):
    spec = wheel_manifest()['pymupdf'].get(plat)
    if not spec or not _wheel_usable(spec[0]):
        return None
    name, size, digest, _ = spec
    return os.path.join(_VENDOR, 'wheels', plat, name), size, digest


def _find_wheel(plat):
    candidate = _wheel_candidate(plat)
    return candidate[0] if candidate and is_good(*candidate) else None


def _cache_dirs(plat):
    '''解压位置，优先用户缓存目录，其次 vendor/_unpacked。'''
    out = []
    env = os.environ.get('DI_CACHE_DIR')
    if env:
        out.append(os.path.join(env, plat))
    if os.name == 'nt':
        base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
    else:
        base = os.environ.get('XDG_CACHE_HOME') or os.path.join(os.path.expanduser('~'), '.cache')
    out.append(os.path.join(base, 'doc-integrator', 'unpacked', plat))
    out.append(os.path.join(_VENDOR, '_unpacked', plat))
    return [_local_io_path(path) for path in out]


def _is_link(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def _cached_wheel_matches(dest, archive):
    """与已校验轮子内的 RECORD 比对，额外的可导入文件也会使缓存失效。"""
    if not os.path.isdir(dest) or _is_link(os.lstat(dest)):
        return False
    sizes = {}
    pending = [(dest, '')]
    while pending:
        folder, prefix = pending.pop()
        with os.scandir(folder) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                if _is_link(info):
                    return False
                name = prefix + entry.name
                if stat.S_ISDIR(info.st_mode):
                    pending.append((entry.path, name + '/'))
                else:
                    sizes[name] = info.st_size
    members = {i.filename for i in archive.infolist() if not i.is_dir()}
    if sizes.keys() != members:
        return False
    record = next(name for name in members if name.endswith('.dist-info/RECORD'))
    expected_record = archive.read(record)
    with open(os.path.join(dest, *record.split('/')), 'rb') as src:
        if src.read() != expected_record:
            return False
    checked = {record}
    for name, checksum, size in csv.reader(io.StringIO(expected_record.decode('utf-8'))):
        if name == record:
            continue
        path = os.path.join(dest, *name.split('/'))
        if sizes.get(name) != int(size):
            return False
        with open(path, 'rb') as src:
            actual = base64.urlsafe_b64encode(_digest(src).digest()).decode('ascii').rstrip('=')
        if checksum != 'sha256=' + actual:
            return False
        checked.add(name)
    return checked == members


def _unpack(whl, plat):
    """从经过固定摘要验证的打开文件读取轮子；只复用与该轮子完全一致的缓存。"""
    name, size, digest, _ = wheel_manifest()['pymupdf'][plat]
    with open(whl, 'rb') as src:
        if os.path.basename(whl) != name or os.fstat(src.fileno()).st_size != size:
            return None
        if _digest(src).hexdigest() != digest:
            return None
        src.seek(0)
        with zipfile.ZipFile(src) as archive:
            for base in _cache_dirs(plat):
                dest = os.path.join(base, digest)
                try:
                    if os.path.lexists(dest):
                        if _cached_wheel_matches(dest, archive):
                            return dest
                        _note('忽略与原始轮子不符的 PyMuPDF 解压缓存')
                        continue
                    os.makedirs(base, exist_ok=True)
                    with tempfile.TemporaryDirectory(dir=base) as tmp:
                        archive.extractall(tmp)
                        try:
                            os.rename(tmp, dest)
                        except OSError:
                            # 其他进程可能已完成相同轮子的解压。
                            if not _cached_wheel_matches(dest, archive):
                                continue
                    return dest
                except (OSError, zipfile.BadZipFile):
                    continue
    return None


def _mod_version(mod):
    v = getattr(mod, '__version__', None)
    if not v:
        try:
            v = mod.version[0]
        except Exception:
            v = None
    return str(v) if v else None


def _from_vendor():
    plat = platform_tag()
    if not plat:
        _note('本平台没有内置 PyMuPDF 轮子，PDF 降级')
        return None
    candidate = _wheel_candidate(plat)
    if not candidate or not os.path.isfile(candidate[0]):
        _note('vendor 里没有本平台可用的 PyMuPDF 轮子，PDF 降级')
        return None
    # _unpack verifies the pinned digest on the same handle it then extracts.
    dest = _unpack(candidate[0], plat)
    if not dest:
        _note('PyMuPDF 轮子或解压缓存校验失败，或目录不可写，PDF 降级')
        return None
    added = dest not in sys.path
    if added:
        sys.path.append(dest)
    importlib.invalidate_caches()
    mod = _import_first(('pymupdf', 'fitz'), 'open')
    ok = mod is not None and _mod_version(mod) == PINNED_PYMUPDF
    if not ok:
        _note('vendor 里的 PyMuPDF 无法导入或版本不是 %s，PDF 降级' % PINNED_PYMUPDF)
        if added and dest in sys.path:
            sys.path.remove(dest)
        return None
    return mod


def use_pymupdf():
    '''返回 pymupdf 模块；系统已装的优先，其次 vendor 轮子；都没有返回 None。'''
    if 'pymupdf' in _cache:
        return _cache['pymupdf']
    mod = None
    try:
        mod = _import_first(('pymupdf', 'fitz'), 'open')
        if mod is None and _use_vendor():
            mod = _from_vendor()
        if mod is None:
            _note('没有可用的 PyMuPDF')
    except Exception:
        mod = None
    _cache['pymupdf'] = mod
    return mod


def _pure_wheels():
    if sys.version_info < (3, 9):
        _note('内置 pypdf 要求 Python 3.9 及以上')
        return []
    wheels = wheel_manifest()['pure']
    packages = ['pypdf']
    if sys.version_info < (3, 11):
        packages.append('typing_extensions')
    paths = []
    for key in packages:
        name, size, digest, _ = wheels[key]
        path = os.path.join(_VENDOR, 'py', name)
        if not is_good(path, size, digest):
            _note('纯 Python 轮子缺失或 SHA256 校验失败：' + name)
            return []
        paths.append(path)
    return paths


def use_pypdf():
    """先用系统 pypdf，否则直接从已校验的纯 Python 轮子加载。"""
    if 'pypdf' in _cache:
        return _cache['pypdf']
    mod = _import_first(('pypdf',), 'PdfReader')
    if mod is None and _use_vendor():
        try:
            paths = _pure_wheels()
            if paths:
                added = [path for path in paths if path not in sys.path]
                sys.path.extend(added)
                importlib.invalidate_caches()
                mod = _import_first(('pypdf',), 'PdfReader')
                if mod is None:
                    for path in added:
                        sys.path.remove(path)
        except (OSError, ValueError, KeyError) as exc:
            _note('无法校验内置 pypdf：' + str(exc))
    if mod is None:
        _note('没有可用的 pypdf')
    _cache['pypdf'] = mod
    return mod


OCR_LANGUAGE = 'chi_sim+eng'
OCR_DPI = 300


def page_ocr(page, *, tessdata, **kwargs):
    """The native Windows reader needs an ASCII model path for its narrow fopen."""
    if os.name != 'nt':
        return page.get_textpage_ocr(tessdata=tessdata, **kwargs)
    # The CLI has already opened this PDF. Change only this process's wide
    # current directory during the synchronous native call, including on error.
    previous = os.getcwd()
    try:
        os.chdir(_local_io_path(tessdata))
        return page.get_textpage_ocr(tessdata='.', **kwargs)
    finally:
        os.chdir(previous)


def ocr_hint(probe=False):
    """Describe the native PDF OCR configuration used by the reader and its cache."""
    fitz = use_pymupdf()
    version = _mod_version(fitz) or 'none'
    data_dir = os.path.join(_ROOT, 'runtime', 'tessdata')
    res = {'available': False, 'engine': None, 'language': OCR_LANGUAGE, 'tessdata': data_dir}
    if os.environ.get('DI_OCR', '').strip().lower() == 'off':
        status, hint = 'off', 'DI_OCR=off 已关闭 PDF 扫描页识别'
    elif not callable(getattr(getattr(fitz, 'Page', None), 'get_textpage_ocr', None)):
        status, hint = 'unavailable', '当前 PyMuPDF 无法执行 PDF OCR'
    else:
        try:
            digests = [sha256_of(os.path.join(data_dir, lang + '.traineddata'))
                       for lang in OCR_LANGUAGE.split('+')]
        except OSError:
            status, hint = 'missing', '缺少可读取的 runtime/tessdata 中英文语言数据，扫描页登记为未读取'
        else:
            status = ':'.join(digests)
            hint = 'PDF 扫描页使用本地 PyMuPDF/Tesseract 识别（%s），识别结果需对照原页复核' % OCR_LANGUAGE
            res.update(available=True, engine='PyMuPDF/Tesseract')
            if probe:
                try:
                    with fitz.open() as sample:
                        page = sample.new_page(width=280, height=140)
                        page.insert_text((15, 45), 'OCR 128', fontsize=24)
                        page.insert_text((15, 100), '测试电压', fontname='china-s', fontsize=25)
                        text = page_ocr(page, full=True, dpi=200, language=OCR_LANGUAGE,
                                        tessdata=data_dir).extractText().replace(' ', '')
                    if '128' not in text or '电压' not in text:
                        raise ValueError('中英文样张未能识别')
                    hint += '（中英文识别自测通过）'
                except Exception as exc:
                    res.update(available=False, engine=None)
                    hint = 'PDF OCR 运行自测失败：%s' % exc
    res.update(hint=hint, signature='pdf-ocr-v1:%s:%s:%s:%s' % (version, OCR_LANGUAGE, OCR_DPI, status))
    return res
