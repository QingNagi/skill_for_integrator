#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''下载并校验 vendor/wheels 里固定版本（1.28.2）的 PyMuPDF 轮子。只用标准库，兼容 Python 3.8+。

  python tools/fetch_vendor.py                  下载本机平台的轮子
  python tools/fetch_vendor.py --platform all   下载全部 5 个平台
  python tools/fetch_vendor.py --from-dir D     不联网：在目录 D（含子目录）里找同名文件，校验后复制
  python tools/fetch_vendor.py --verify         只校验指定平台和纯 Python 轮子
  --mirror tuna|pypi|URL前缀                     默认先试清华镜像，再试官方；支持断点续传
'''
import argparse
import http.client
from http import HTTPStatus
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

sys.dont_write_bytecode = True
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR = os.path.join(ROOT, 'vendor')
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
from dilib import _bootstrap as bs  # noqa: E402

_manifest = bs.wheel_manifest()
WHEELS = _manifest['pymupdf']
PURE_WHEELS = _manifest['pure']
is_good = bs.is_good
sha256_of = bs.sha256_of
MIRRORS = {'tuna': 'https://pypi.tuna.tsinghua.edu.cn/packages/',
           'pypi': 'https://files.pythonhosted.org/packages/'}
UA = 'doc-integrator-fetch'
MEBIBYTE = 1 << 20
DOWNLOAD_CHUNK_BYTES = 64 * 1024
REQUEST_TIMEOUT_SECONDS = 30


def checked_url(url, allowed_hosts):
    parts = urlsplit(url)
    if (parts.scheme != 'https' or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.netloc.lower() not in allowed_hosts):
        raise ValueError('只允许指定镜像的 HTTPS 地址')
    return parts


class MirrorRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_hosts):
        self.allowed_hosts = allowed_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        checked_url(newurl, self.allowed_hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def mirror_opener(url):
    # 自定义镜像必须由操作者显式指定。重定向也必须留在选定镜像或内置源上。
    hosts = {urlsplit(v).netloc.lower() for v in MIRRORS.values()}
    hosts.add(urlsplit(url).netloc.lower())
    checked_url(url, hosts)
    opener = urllib.request.OpenerDirector()
    for handler in (urllib.request.ProxyHandler(), urllib.request.HTTPSHandler(),
                    MirrorRedirect(hosts), urllib.request.HTTPDefaultErrorHandler(),
                    urllib.request.HTTPErrorProcessor()):
        opener.add_handler(handler)
    return opener


def download(url, part, size, tries=8):
    '''断点续传到 part；字节数到位返回 True。403/404 视为此源没有，直接放弃。'''
    opener = mirror_opener(url)
    for _ in range(tries):
        have = os.path.getsize(part) if os.path.exists(part) else 0
        if have > size:
            os.remove(part)
            have = 0
        if have == size:
            return True
        headers = {'User-Agent': UA}
        if have:
            headers['Range'] = 'bytes=%d-' % have
        try:
            with opener.open(urllib.request.Request(url, headers=headers), timeout=REQUEST_TIMEOUT_SECONDS) as r:
                mode = 'ab' if (have and r.status == HTTPStatus.PARTIAL_CONTENT) else 'wb'
                remaining = size - (have if mode == 'ab' else 0)
                with open(part, mode) as f:
                    while True:
                        b = r.read(min(DOWNLOAD_CHUNK_BYTES, remaining + 1))
                        if not b:
                            break
                        if len(b) > remaining:
                            return False
                        f.write(b)
                        remaining -= len(b)
        except urllib.error.HTTPError as e:
            if e.code in (HTTPStatus.FORBIDDEN, HTTPStatus.NOT_FOUND):
                return False
            if e.code == HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE and os.path.exists(part):
                os.remove(part)
        except (urllib.error.URLError, OSError, http.client.HTTPException):
            pass
        else:
            continue
        time.sleep(2)
    return os.path.exists(part) and os.path.getsize(part) == size


def find_local(folder, name):
    for root, _dirs, files in os.walk(folder):
        if name in files:
            return os.path.join(root, name)
    return None


def sources(a, name, path):
    if a.from_dir:
        p = find_local(a.from_dir, name)
        if p:
            yield 'dir', p
        return
    if a.mirror in MIRRORS:
        bases = [MIRRORS[a.mirror]]
    elif a.mirror:
        bases = [a.mirror.rstrip('/') + '/']
    else:
        bases = [MIRRORS['tuna'], MIRRORS['pypi']]
    for b in bases:
        yield 'url', b + path + '/' + name


def fetch_one(plat, a):
    if plat in PURE_WHEELS:
        name, size, sha, path = PURE_WHEELS[plat]
        d = os.path.join(VENDOR, 'py')
    else:
        name, size, sha, path = WHEELS[plat]
        d = os.path.join(VENDOR, 'wheels', plat)
    dest = os.path.join(d, name)
    part = dest + '.part'
    if is_good(dest, size, sha):
        print('[已就绪] %s  %s' % (plat, name))
        return True
    if a.verify:
        print('[缺失或校验不符] %s  %s' % (plat, name))
        return False
    os.makedirs(d, exist_ok=True)
    for how, src in sources(a, name, path):
        if how == 'dir':
            shutil.copyfile(src, part)
        else:
            print('[下载] %s  %.1f MiB  %s' % (plat, size / MEBIBYTE, src))
            if not download(src, part, size):
                continue
        if os.path.getsize(part) == size and sha256_of(part) == sha:
            os.replace(part, dest)
            print('[完成] %s  SHA256 校验通过  %s' % (plat, name))
            return True
        os.remove(part)
        print('[校验失败] %s  来源 %s，已丢弃' % (plat, src))
    print('[失败] %s  没有取到可用的 %s（可换 --mirror，或用 --from-dir 指向已下载的目录）' % (plat, name))
    return False


def main(argv=None):
    ap = argparse.ArgumentParser(description='按 vendor/wheels.lock.json 下载或校验离线依赖')
    ap.add_argument('--platform', default='current',
                    help='current / all / lite / 平台名（逗号分隔）：' + ', '.join(sorted(WHEELS)))
    ap.add_argument('--mirror', help='tuna / pypi / 以 /packages/ 结尾的网址前缀；默认先 tuna 后 pypi')
    ap.add_argument('--from-dir', dest='from_dir', help='离线：在此目录（含子目录）里找同名轮子')
    ap.add_argument('--verify', action='store_true', help='只校验，不下载')
    a = ap.parse_args(argv)
    if a.platform == 'all':
        plats = sorted(WHEELS)
    elif a.platform == 'lite':
        plats = []
    elif a.platform == 'current':
        cur = bs.platform_tag()
        if not cur or cur not in WHEELS:
            print('本机平台没有对应的 PyMuPDF 轮子，PDF 将用 pypdf 降级。可用 --platform 指定。')
            return 2
        plats = [cur]
    else:
        plats = [p.strip() for p in a.platform.split(',') if p.strip()]
        unknown = [p for p in plats if p not in WHEELS]
        if unknown:
            print('未知平台：%s。可选：%s' % (', '.join(unknown), ', '.join(sorted(WHEELS))))
            return 2
    try:
        results = [fetch_one(p, a) for p in list(PURE_WHEELS) + plats]
    except (ValueError, OSError) as exc:
        print('无法获取轮子：%s' % exc)
        return 1
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())
