"""Build the relocatable OCR executable with Zig 0.14.1 and Pillow for host font conversion."""
import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import textwrap
import urllib.request

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / ".build" / "ocr-build"
TARGETS = {"win_amd64": "x86_64-windows-gnu", "manylinux_x86_64": "x86_64-linux-musl",
           "manylinux_aarch64": "aarch64-linux-musl"}
SOURCES = json.loads((REPO / "traced-template-fill/vendor/ocr.lock.json").read_text(encoding="utf-8"))["sources"]


def sources(name):
    spec = SOURCES[name]
    url, digest = spec["url"], spec["sha256"]
    filename = name + "-" + url.rsplit("/", 1)[1]
    path = CACHE / (filename + ".tar.gz")
    if not path.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=90) as stream, path.open("wb") as out:
            shutil.copyfileobj(stream, out)
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError("Source hash mismatch: " + str(path))
    target = CACHE / "src" / filename
    if not target.exists():
        with tarfile.open(path) as archive:
            archive.extractall(target.parent, filter="data")
    return target


def bitmap_font_source(lept, generated):
    """Keep all Leptonica caption fonts in a format supported by our PNM-only image pipeline."""
    from PIL import Image

    header = (lept / "bmfdata.h").read_text(encoding="utf-8")
    def as_pbm(match):
        tiff = base64.b64decode("".join(re.findall(r'"([^"]*)"', match[2])), validate=True)
        with Image.open(io.BytesIO(tiff)) as bitmap:
            if bitmap.format != "TIFF" or bitmap.mode != "1":
                raise ValueError("Unexpected embedded Leptonica font format")
            data = io.BytesIO()
            bitmap.save(data, format="PPM")  # Pillow writes binary PBM (P4) for mode 1.
            data.seek(0)
            with Image.open(data) as converted:
                if bitmap.size != converted.size or bitmap.tobytes() != converted.tobytes():
                    raise ValueError("Font conversion changed glyph pixels")
            encoded = base64.b64encode(data.getvalue()).decode("ascii")
        return match[1] + "\n" + "\n".join('    "' + line + '"' for line in textwrap.wrap(encoded, 76)) + ";"

    header, count = re.subn(r'(static const char\s+fontdata_\d+\[\] =)\s*((?:"[^"]*"\s*)+);', as_pbm, header)
    if count != 9:
        raise ValueError("Expected nine Leptonica bitmap fonts")
    (generated / "bmfdata.h").write_text(header, encoding="utf-8")
    # Quoted C includes search the translation unit's own directory first.
    source = generated / "bmf.c"
    shutil.copyfile(lept / "bmf.c", source)
    return source


def pnm_line_source(tess, generated):
    """Select Tesseract's existing lossless PNM line store for the PNG-free build."""
    original = tess / "src/ccstruct/imagedata.cpp"
    body = original.read_text(encoding="utf-8")
    fallback = """  ret = pixWriteMem(&data, &size, pix, IFF_PNG);
  if (ret) {
    ret = pixWriteMem(&data, &size, pix, IFF_PNM);
  }"""
    if body.count(fallback) != 1:
        raise ValueError("Expected the pinned Tesseract PNG/PNM line encoder")
    source = generated / "imagedata.cpp"
    source.write_text(body.replace(fallback, "  ret = pixWriteMem(&data, &size, pix, IFF_PNM);"), encoding="utf-8")
    return source


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
    ver = SOURCES["tesseract"]["version"]
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
    bitmap_font = bitmap_font_source(lept, generated)
    cmd += [(bitmap_font if p.name == "bmf.c" else p).as_posix() for p in sorted(lept.glob("*.c"))]
    cmd += ["-cflags", "-std=c++17", "-w", "-DNDEBUG", *maps, "-DDISABLED_LEGACY_ENGINE", "-DGRAPHICS_DISABLED", "-DFAST_FLOAT"]
    if platform == "manylinux_aarch64":
        cmd += ["-DHAVE_NEON"]
    line_encoder = pnm_line_source(tess, generated)
    cmd += ["--"] + [(line_encoder if p == "src/ccstruct/imagedata.cpp" else tess / p).as_posix()
                      for p in sorted(cpp)]
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
