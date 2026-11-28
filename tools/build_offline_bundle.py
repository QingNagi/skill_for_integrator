#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''生成离线完整包：每个平台一个 zip（技能全部文件 + PDF 读取依赖），另出含 pypdf 的 lite 包。
只用标准库，兼容 Python 3.8+。

  python tools/build_offline_bundle.py --out D:/dist --tag v1.0
  python tools/build_offline_bundle.py --pub D:/PUB --tag v1.0     输出到 PUB 同级的 dist/
  --platform source|lite|all|平台名,...   默认 source，不需要准备 wheel；其他交付从 .build/vendor 取依赖。
  --no-lite                   不出 lite 包
先运行 tools/fetch_vendor.py --platform all 下载并校验轮子。打包前请确认本目录是准备公开的干净副本。
'''
import argparse
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import zipfile

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fetch_vendor as fv  # noqa: E402

ROOT = fv.ROOT
TOP = os.path.basename(ROOT)


def wheel_specs(plat):
    """Yield (vendor-relative path, package, pinned spec) for this delivery."""
    if plat == "source":
        return
    for package, spec in fv.PURE_WHEELS.items():
        if plat or package in {"pypdf", "typing_extensions"}:
            yield "py/" + spec[0], package, spec
    if plat:
        for package, spec in fv.WHEELS[plat].items():
            yield "wheels/%s/%s" % (plat, spec[0]), package, spec


def files(plat, vendor=None):
    """Select only installable source files and wheels for this platform."""
    root = Path(ROOT)
    for required in ("SKILL.md", "scripts/di.py", "rules/test-data.json", "vendor/wheels.lock.json"):
        if not (root / required).is_file() or not (root / required).stat().st_size:
            raise ValueError("Missing or empty skill file: " + required)
    for pattern in ("SKILL.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "run.cmd", "run.sh",
                    "assets/offline-ocr.png", "profiles/*.json", "references/*.md", "rules/*.json",
                    "scripts/**/*.py", "scripts/**/*.ps1", "vendor/licenses/*.txt", "vendor/README.md", "vendor/SHA256SUMS",
                    "vendor/*.lock.json"):
        for path in sorted(root.glob(pattern)):
            yield path, path.relative_to(root).as_posix()
    if plat == "source":
        return
    wheel_root = Path(vendor or fv.VENDOR)
    for rel, _, spec in wheel_specs(plat):
        path = wheel_root / rel
        if not fv.is_good(str(path), spec[1], spec[2]):
            raise ValueError("Missing or invalid wheel: " + str(path))
        yield path, "vendor/" + rel


def build(out, tag, plat, vendor=None):
    dest = os.path.join(out, '%s-%s-%s.zip' % (TOP, tag, plat or 'lite'))
    with tempfile.TemporaryDirectory(dir=out) as staging:
        part = Path(staging) / 'package.zip'
        expected = {TOP + '/vendor/' + rel: spec[2] for rel, _, spec in wheel_specs(plat)}
        with zipfile.ZipFile(part, 'w') as archive:
            if plat != "source":
                archive.writestr(TOP + "/vendor/pdf-mode.txt", "full" if plat else "lite")
            for path, rel in files(plat, vendor):
                method = zipfile.ZIP_STORED if rel.endswith('.whl') else zipfile.ZIP_DEFLATED
                archive.write(path, TOP + '/' + rel, compress_type=method)
        with zipfile.ZipFile(part) as archive:
            names = archive.namelist()
            actual = {name for name in names if name.endswith('.whl')}
            bad = archive.testzip()
            if bad or actual != set(expected):
                raise ValueError('Archive validation failed: %s (%s)' % (dest, bad or 'wheel set'))
            for member, digest in expected.items():
                with archive.open(member) as stream:
                    check = hashlib.sha256()
                    for block in iter(lambda: stream.read(fv.MEBIBYTE), b''):
                        check.update(block)
                    if check.hexdigest() != digest:
                        raise ValueError('Invalid archived wheel: ' + member)
        os.replace(part, dest)
    return dest, len(names)


def main(argv=None):
    ap = argparse.ArgumentParser(description='生成每个平台的离线完整包')
    ap.add_argument('--out', help='输出目录')
    ap.add_argument('--pub', help='PUB 目录；输出到它同级的 dist/')
    ap.add_argument('--tag', default='dev', help='版本标记，写进文件名')
    ap.add_argument('--platform', default='source')
    ap.add_argument('--vendor-dir', default=fv.VENDOR, help='轮子目录，默认 .build/vendor')
    ap.add_argument('--no-lite', action='store_true')
    a = ap.parse_args(argv)
    if (not a.tag or a.tag in ('.', '..') or not all(c.isalnum() or c in '.-_' for c in a.tag)):
        ap.error('--tag 只能包含字母、数字、点、连字符和下划线')
    if a.out:
        out = os.path.abspath(a.out)
    elif a.pub:
        out = os.path.join(os.path.dirname(os.path.abspath(a.pub)), 'dist')
    else:
        print('请用 --out 指定输出目录，或用 --pub 指定 PUB 目录（输出到其同级 dist/）')
        return 2
    requested = [item.strip() for item in a.platform.split(',') if item.strip()]
    if requested == ['all']:
        requested = ['source'] + sorted(fv.WHEELS)
    unknown = [item for item in requested if item not in ('source', 'lite') and item not in fv.WHEELS]
    if not requested or unknown:
        ap.error('可选交付：source、lite、all 或 ' + ', '.join(sorted(fv.WHEELS)))
    if 'lite' in requested and a.no_lite:
        ap.error('--no-lite 与显式选择 lite 冲突')
    plats = list(dict.fromkeys(item for item in requested if item in fv.WHEELS))
    jobs = (['source'] if 'source' in requested else []) + plats
    if 'lite' in requested or (plats and not a.no_lite):
        jobs.append(None)
    missing = [rel for p in jobs for rel, _, spec in wheel_specs(p)
               if not fv.is_good(os.path.join(a.vendor_dir, rel), spec[1], spec[2])]
    if missing:
        print('这些平台的轮子缺失或 SHA256 不符：%s。先运行 tools/fetch_vendor.py --platform all' % ', '.join(missing))
        return 1
    os.makedirs(out, exist_ok=True)
    sums = []
    for p in jobs:
        dest, n = build(out, a.tag, p, a.vendor_dir)
        sums.append('%s  %s' % (fv.sha256_of(dest), os.path.basename(dest)))
        with open(dest + '.sha256', 'w', encoding='ascii') as checksum:
            checksum.write(sums[-1] + '\n')
        print('[完成] %s  %d 个文件  %.1f MiB' % (dest, n, os.path.getsize(dest) / fv.MEBIBYTE))
    with open(os.path.join(out, 'SHA256SUMS-%s.txt' % a.tag), 'w', encoding='utf-8') as f:
        f.write('\n'.join(sums) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
