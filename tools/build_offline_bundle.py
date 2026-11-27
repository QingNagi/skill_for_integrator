#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''生成离线完整包：每个平台一个 zip（技能全部文件 + 该平台的 PyMuPDF 轮子），另出仅含纯 Python 轮子的 lite 包。
只用标准库，兼容 Python 3.8+。

  python tools/build_offline_bundle.py --out D:/dist --tag v1.0
  python tools/build_offline_bundle.py --pub D:/PUB --tag v1.0     输出到 PUB 同级的 dist/
  --platform all|平台名,...   默认 all（要求这些平台的轮子已在 vendor/wheels 且 SHA256 校验通过）
  --no-lite                   不出 lite 包
先运行 tools/fetch_vendor.py --platform all 下载并校验轮子。打包前请确认本目录是准备公开的干净副本。
'''
import argparse
import hashlib
import os
import sys
import zipfile

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fetch_vendor as fv  # noqa: E402

ROOT = fv.ROOT
TOP = 'doc-integrator'
SKIP_DIRS = ('__pycache__', '_unpacked', '.git', '.github', 'qa', 'dist', 'runtime')
SKIP_EXT = ('.pyc', '.pyo', '.part', '.tmp')


def files(plat):
    '''每个包均含纯 Python 轮子；完整包另含选定平台的原生轮子。'''
    pure = {'vendor/py/' + spec[0] for spec in fv.PURE_WHEELS.values()}
    native = 'vendor/wheels/%s/%s' % (plat, fv.WHEELS[plat][0]) if plat else None
    for r, ds, fs in os.walk(ROOT):
        ds[:] = sorted(d for d in ds if d not in SKIP_DIRS)
        for f in sorted(fs):
            if f.endswith(SKIP_EXT):
                continue
            rel = os.path.relpath(os.path.join(r, f), ROOT).replace(os.sep, '/')
            if rel.startswith('vendor/ocr/'):
                continue
            if rel.startswith('vendor/py/') and rel not in pure:
                continue
            if f.endswith('.whl') and rel not in pure and rel != native:
                continue
            yield os.path.join(r, f), rel


def build(out, tag, plat):
    dest = os.path.join(out, '%s-%s-%s.zip' % (TOP, tag, plat or 'lite'))
    part = dest + '.part'
    n = 0
    with zipfile.ZipFile(part, 'w') as z:
        for p, rel in files(plat):
            ct = zipfile.ZIP_STORED if rel.endswith('.whl') else zipfile.ZIP_DEFLATED
            z.write(p, TOP + '/' + rel, compress_type=ct)
            n += 1
    with zipfile.ZipFile(part) as z:
        bad = z.testzip()
        has = [x for x in z.namelist() if x.endswith('.whl')]
        expected = {TOP + '/vendor/py/' + spec[0]: spec[2] for spec in fv.PURE_WHEELS.values()}
        if plat:
            name, _, digest, _ = fv.WHEELS[plat]
            expected[TOP + '/vendor/wheels/%s/%s' % (plat, name)] = digest
        if set(has) == set(expected):
            for member, digest in expected.items():
                with z.open(member) as stream:
                    actual = hashlib.sha256()
                    for block in iter(lambda: stream.read(fv.MEBIBYTE), b''):
                        actual.update(block)
                if actual.hexdigest() != digest:
                    bad = member
                    break
    if bad or set(has) != set(expected):
        os.remove(part)
        raise SystemExit('包内容检查失败：%s（损坏项 %s，轮子 %s）' % (dest, bad, has))
    os.replace(part, dest)
    return dest, n


def main(argv=None):
    ap = argparse.ArgumentParser(description='生成每个平台的离线完整包')
    ap.add_argument('--out', help='输出目录')
    ap.add_argument('--pub', help='PUB 目录；输出到它同级的 dist/')
    ap.add_argument('--tag', default='dev', help='版本标记，写进文件名')
    ap.add_argument('--platform', default='all')
    ap.add_argument('--no-lite', action='store_true')
    a = ap.parse_args(argv)
    if (not a.tag or a.tag in ('.', '..') or not all(c.isalnum() or c in '.-_' for c in a.tag)):
        ap.error('--tag 只能包含字母、数字、点、连字符和下划线')
    if a.platform == 'lite' and a.no_lite:
        ap.error('--platform lite 和 --no-lite 不能同时使用')
    if a.out:
        out = os.path.abspath(a.out)
    elif a.pub:
        out = os.path.join(os.path.dirname(os.path.abspath(a.pub)), 'dist')
    else:
        print('请用 --out 指定输出目录，或用 --pub 指定 PUB 目录（输出到其同级 dist/）')
        return 2
    if a.platform == 'all':
        plats = sorted(fv.WHEELS)
    elif a.platform == 'lite':
        plats = []
    else:
        plats = [p.strip() for p in a.platform.split(',') if p.strip()]
    unknown = [p for p in plats if p not in fv.WHEELS]
    if unknown:
        print('未知平台：%s。可选：%s' % (', '.join(unknown), ', '.join(sorted(fv.WHEELS))))
        return 2
    missing = [name for name, size, sha, _ in fv.PURE_WHEELS.values()
               if not fv.is_good(os.path.join(fv.VENDOR, 'py', name), size, sha)]
    for p in plats:
        name, size, sha, _ = fv.WHEELS[p]
        if not fv.is_good(os.path.join(fv.VENDOR, 'wheels', p, name), size, sha):
            missing.append(p)
    if missing:
        print('这些平台的轮子缺失或 SHA256 不符：%s。先运行 tools/fetch_vendor.py --platform all' % ', '.join(missing))
        return 1
    os.makedirs(out, exist_ok=True)
    jobs = plats + ([] if a.no_lite else [None])
    sums = []
    for p in jobs:
        dest, n = build(out, a.tag, p)
        sums.append('%s  %s' % (fv.sha256_of(dest), os.path.basename(dest)))
        print('[完成] %s  %d 个文件  %.1f MiB' % (dest, n, os.path.getsize(dest) / fv.MEBIBYTE))
    with open(os.path.join(out, 'SHA256SUMS-%s.txt' % a.tag), 'w', encoding='utf-8') as f:
        f.write('\n'.join(sums) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
