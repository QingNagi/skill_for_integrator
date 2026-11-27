"""Build the relocatable single-file OCR executable with Zig 0.14.1 (Clang and libc++)."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import urllib.request

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / ".build" / "ocr-build"
TARGETS = {"win_amd64": "x86_64-windows-gnu", "manylinux_x86_64": "x86_64-linux-musl",
           "manylinux_aarch64": "aarch64-linux-musl"}
SOURCES = {
    "tesseract": ("5.5.5", "tesseract-ocr/tesseract", "b02cf6f37cfc95be5bf7e4b2d39e51997b1ac41f7beb4e2bd16777bb67561c79"),
    "leptonica": ("1.87.0", "DanBloomberg/leptonica", "fa2b40c5caea96d1bb93a97486262aed8731b69ce25a84a6bf5d25323e33f631"),
}


def sources(name):
    version, repo, digest = SOURCES[name]
    filename = name + "-" + version
    path = CACHE / (filename + ".tar.gz")
    if not path.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        url = "https://codeload.github.com/" + repo + "/tar.gz/refs/tags/" + version
        with urllib.request.urlopen(url, timeout=90) as stream, path.open("wb") as out:
            shutil.copyfileobj(stream, out)
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError("Source hash mismatch: " + str(path))
    target = CACHE / "src" / filename
    if not target.exists():
        with tarfile.open(path) as archive:
            archive.extractall(target.parent, filter="data")
    return target


def build(platform, zig, vendor):
    if subprocess.check_output([zig, "version"], text=True).strip() != "0.14.1":
        raise ValueError("Use Zig 0.14.1")
    tess = sources("tesseract")
    lept = sources("leptonica") / "src"
    target = TARGETS[platform]
    name = "tesseract.exe" if platform == "win_amd64" else "tesseract"
    output = vendor / "ocr" / platform / name
    output.parent.mkdir(exist_ok=True, parents=True)
    generated = CACHE / platform / "include"
    (generated / "tesseract").mkdir(parents=True, exist_ok=True)
    (generated / "endianness.h").write_text("#define L_LITTLE_ENDIAN 1\n", encoding="ascii")
    ver = SOURCES["tesseract"][0]
    version = (tess / "include/tesseract/version.h.in").read_text()
    for key, val in zip(("MAJOR", "MINOR", "MICRO"), ver.split(".")):
        version = version.replace("@GENERIC_" + key + "_VERSION@", val)
    (generated / "tesseract/version.h").write_text(version.replace("@PACKAGE_VERSION@", ver), encoding="utf-8")
    sources_file = (tess / "cmake/SourceLists.cmake").read_text()
    groups = {key: set(body.split()) for key, body in
              re.findall(r"set\(TESSERACT_SRC_(\w+)\s+([^)]*)\)", sources_file)}
    cpp = set().union(*(groups[key] for key in
                       ("ARCH API CCMAIN CCSTRUCT CCUTIL CLASSIFY CUTIL DICT LSTM TEXTORD VIEWER WORDREC").split()))
    cpp = cpp - groups["LEGACY"] | {"src/tesseract.cpp"}
    if platform == "manylinux_aarch64":
        cpp |= groups["ARCH_NEON"]
    incs = [generated, lept, tess / "include"] + sorted(p for p in (tess / "src").iterdir() if p.is_dir())
    cmd = ["build-exe", "-target", target, "-lc", "-lc++", "-O", "ReleaseSmall", "-fstrip",
           "-femit-bin=" + output.as_posix()]
    if platform == "win_amd64":
        cmd += ["-luser32", "-ladvapi32", "-lgdi32"]
    for inc in incs:
        cmd += ["-I", inc.as_posix()]
    maps = ["-ffile-prefix-map=" + str(CACHE) + "=ocr", "-ffile-prefix-map=" + CACHE.as_posix() + "=ocr"]
    cmd += ["-cflags", "-std=gnu11", "-w", "-DNDEBUG", *maps, "-DHAVE_LIBJPEG=0", "-DHAVE_LIBPNG=0",
            "-DHAVE_LIBTIFF=0", "-DHAVE_LIBZ=0", "--"]
    cmd += [p.as_posix() for p in sorted(lept.glob("*.c"))]
    cmd += ["-cflags", "-std=c++17", "-w", "-DNDEBUG", *maps, "-DDISABLED_LEGACY_ENGINE", "-DGRAPHICS_DISABLED", "-DFAST_FLOAT"]
    if platform == "manylinux_aarch64":
        cmd += ["-DHAVE_NEON"]
    cmd += ["--"] + [(tess / p).as_posix() for p in sorted(cpp)]
    response = generated.parent / "zig.rsp"
    response.write_text("\n".join('"' + arg + '"' for arg in cmd[1:]), encoding="utf-8")
    subprocess.run([zig, cmd[0], "@" + response.as_posix()], check=True)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", required=True, choices=sorted(TARGETS))
    parser.add_argument("--vendor-dir", type=Path, default=REPO / ".build/vendor")
    parser.add_argument("--zig", default="zig")
    args = parser.parse_args()
    print(build(args.platform, args.zig, args.vendor_dir.resolve()))
