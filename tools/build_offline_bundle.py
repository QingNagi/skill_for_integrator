#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a text-only skill, its standalone lite wheel pack, or a legacy platform wheel kit."""
import argparse
import hashlib
import json
import os
import re
import shutil
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


def source_files():
    """Map exact runtime locations; ignore other workspace directories and build outputs."""
    root = Path(ROOT)
    allowlist = (Path(HERE) / "skill-files.txt").read_text(encoding="utf-8").splitlines()
    selected = {rel: root / rel for rel in allowlist}
    if (root / "LICENSE").is_file():
        selected["LICENSE"] = root / "LICENSE"
    for name in ("wheels.lock.json", "ocr.lock.json"):
        selected["config/" + name] = root / "vendor" / name
    if len(selected) > 500:
        raise ValueError("Too many skill files")
    for rel, path in sorted(selected.items()):
        if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink() or not path.is_file():
            raise ValueError("Invalid skill source: " + rel)
        if not 0 < path.stat().st_size <= 512 * 1024:
            raise ValueError("Invalid skill file size: " + rel)
        value = path.read_text(encoding="utf-8-sig")
        if any(ord(c) < 32 and c not in "\t\n\r" for c in value):
            raise ValueError("Non-text skill file: " + rel)
        if rel == "SKILL.md":
            frontmatter = value.split("---", 2)
            if len(frontmatter) < 3 or not re.search(r"(?m)^name: " + re.escape(TOP) + r"\s*$", frontmatter[1]):
                raise ValueError("Skill directory and front matter name differ")
        yield path, rel


def skill_bytes(path, rel):
    data = path.read_bytes()
    if rel == "LICENSE":
        return data
    # Only the validated, allowlisted skill text goes through this conversion.
    lf = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return lf.replace(b"\n", b"\r\n") if rel.endswith(".cmd") else lf


def source_manifest():
    return (json.dumps({rel: hashlib.sha256(skill_bytes(path, rel)).hexdigest() for path, rel in source_files()},
                       ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode("ascii")


def runtime_support():
    root = Path(ROOT)
    for pattern in ("assets/offline-ocr.png", "THIRD_PARTY_NOTICES.md", "vendor/README.md",
                    "vendor/SHA256SUMS", "vendor/*.lock.json", "vendor/licenses/*.txt"):
        for path in sorted(root.glob(pattern)):
            yield path, path.relative_to(root).as_posix()


def wheel_files(plat, vendor):
    for rel, _, spec in wheel_specs(plat):
        path = Path(vendor or fv.VENDOR) / rel
        if not fv.is_good(str(path), spec[1], spec[2]):
            raise ValueError("Missing or invalid wheel: " + str(path))
        yield path, rel


def dependency_files(plat, vendor=None):
    if plat == "source":
        return
    if plat:
        yield from runtime_support()
    for path, rel in wheel_files(plat, vendor):
        yield path, ("vendor/" if plat else "") + rel


def build(out, tag, plat, vendor=None):
    label = plat or "dependencies"
    top = TOP if plat else TOP + "-dependencies"
    dest = os.path.join(out, "%s-%s-%s.zip" % (TOP, tag, label))
    with tempfile.TemporaryDirectory(dir=out) as staging:
        clean = Path(staging) / top
        clean.mkdir()
        if plat is not None:
            for path, rel in source_files():
                target = clean / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(skill_bytes(path, rel))
        for path, rel in dependency_files(plat, vendor):
            target = clean / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        if plat is None:
            requirements, sums, lock = [], [], {}
            snapshot = Path(HERE) / "offline/dependencies-advisories.json"
            checked = {p["wheel"]: p["sha256"] for p in json.loads(snapshot.read_text("utf-8"))["packages"]}
            if checked != {spec[0]: spec[2] for _, _, spec in wheel_specs(None)}:
                raise ValueError("Refresh the dependency advisory snapshot for the changed wheels")
            shutil.copyfile(snapshot, clean / "advisories.json")
            for rel, package, spec in wheel_specs(None):
                requirements.append("%s==%s --hash=sha256:%s" % (package, spec[0].split("-")[1], spec[2]))
                sums.append("%s  %s" % (spec[2], rel))
                lock[package] = spec
                name = package + "-LICENSE.txt"
                shutil.copyfile(Path(ROOT) / "vendor/licenses" / name, clean / name)
            (clean / "requirements.txt").write_text("\n".join(requirements) + "\n", encoding="ascii")
            (clean / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="ascii")
            (clean / "wheels.lock.json").write_text(json.dumps({"pure": lock}, indent=2) + "\n", encoding="ascii")
            (clean / "pdf-mode.txt").write_text("lite\n", encoding="ascii")
            shutil.copyfile(Path(HERE) / "offline/dependencies.md", clean / "README.md")
        elif plat != "source":
            (clean / "vendor/pdf-mode.txt").write_text("full", encoding="ascii")
        part = Path(staging) / "package.zip"
        paths = sorted(p for p in clean.rglob("*") if p.is_file())
        with zipfile.ZipFile(part, "w") as archive:
            for path in paths:
                info = zipfile.ZipInfo(top + "/" + path.relative_to(clean).as_posix())
                info.create_system = 3
                info.external_attr = (0o100755 if path.name == "run.sh" else 0o100644) << 16
                info.compress_type = zipfile.ZIP_STORED if path.suffix == ".whl" else zipfile.ZIP_DEFLATED
                archive.writestr(info, path.read_bytes())
        with zipfile.ZipFile(part) as archive:
            names = archive.namelist()
            if archive.testzip() or set(names) != {top + "/" + p.relative_to(clean).as_posix() for p in paths}:
                raise ValueError("Archive validation failed: " + dest)
            for rel, _, spec in wheel_specs(plat):
                member = top + ("/vendor/" if plat else "/") + rel
                if hashlib.sha256(archive.read(member)).hexdigest() != spec[2]:
                    raise ValueError("Invalid archived wheel: " + member)
            if plat == "source":
                for name in names:
                    archive.read(name).decode("utf-8")
        os.replace(part, dest)
    return dest, len(names)


def installers(stage, tag):
    for template, filename in (("deploy-install.sh", "install.sh"), ("deploy-install.ps1", "install.ps1"), ("deploy.py", "deploy.py")):
        script = stage / filename
        content = (Path(HERE) / "offline" / template).read_text("utf-8").replace("@TAG@", tag)
        script.write_bytes(content.encode("utf-8"))
        if filename.endswith(".sh"):
            script.chmod(0o755)
        yield script


def build_release(output, tag, sources, vendor):
    """Build and verify every artifact privately before publishing the checksum inventory."""
    from build_linux_agent_kit import PYTHONS, build_kit as linux_kit
    from build_win64_agent_kit import build_kit as windows_kit

    output, sources, vendor = Path(output).resolve(), Path(sources).resolve(), Path(vendor).resolve()
    if output.is_relative_to(Path(ROOT).resolve()):
        raise ValueError("Build outside the skill tree")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output) as tmp:
        stage = Path(tmp)
        packages = [Path(build(stage, tag, plat, vendor)[0]) for plat in ("source", None)]
        for paired in (False, True):
            label = "runtime" if paired else "offline"
            win = stage / (TOP + "-" + label + "-win64.zip")
            windows_kit(win, sources, vendor, paired)
            packages.append(win)
            for arch in PYTHONS:
                packages.append(linux_kit(arch, stage, sources, vendor, paired))
        packages.extend(installers(stage, tag))
        lines = []
        for package in sorted(packages):
            line = fv.sha256_of(package) + "  " + package.name + "\n"
            lines.append(line)
            package.with_name(package.name + ".sha256").write_bytes(line.encode("ascii"))
        manifest = stage / ("SHA256SUMS-" + tag + ".txt")
        manifest.write_bytes("".join(lines).encode("ascii"))
        for package in packages:
            for item in (package, package.with_name(package.name + ".sha256")):
                os.replace(item, output / item.name)
        os.replace(manifest, output / manifest.name)
    print(output / manifest.name)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description='生成每个平台的离线完整包')
    ap.add_argument('--out', help='输出目录')
    ap.add_argument('--pub', help='PUB 目录；输出到它同级的 dist/')
    ap.add_argument('--tag', default='dev', help='版本标记，写进文件名')
    ap.add_argument('--platform', default='source')
    ap.add_argument('--vendor-dir', default=fv.VENDOR, help='轮子目录，默认 .build/vendor')
    ap.add_argument('--from-dir', type=Path, help='Verified original interpreter archives, Python release evidence and OCR models for --platform release')
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
    if requested == ['release']:
        if not a.from_dir or a.no_lite:
            ap.error('release builds all eight artifacts and requires --from-dir')
        return build_release(out, a.tag, a.from_dir, a.vendor_dir)
    if requested == ['all']:
        requested = ['source'] + sorted(fv.WHEELS)
    unknown = [item for item in requested if item not in ('source', 'lite', 'dependencies') and item not in fv.WHEELS]
    if not requested or unknown:
        ap.error('可选交付：source、dependencies（原 lite）、all 或 ' + ', '.join(sorted(fv.WHEELS)))
    requested = ['lite' if item == 'dependencies' else item for item in requested]
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
