#!/usr/bin/env python3
"""Build self-deploying Linux offline kits on Linux or Windows, from pinned local inputs."""
import argparse
from contextlib import contextmanager
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import sys
import tarfile
import tempfile
from urllib import request
from urllib.parse import quote, urlsplit

sys.dont_write_bytecode = True
from fetch_vendor import MIRRORS, MirrorRedirect, checked_url
from prepare_win64_runtime import MODEL_REV, MODEL_URL, MODELS


ROOT = Path(__file__).resolve().parents[1]
RELEASE = "20261026"
VERSION = "3.14.10"
UPSTREAM = "https://github.com/astral-sh/python-build-standalone/releases/download/" + RELEASE + "/"
# GitHub release asset digests and byte lengths, published by Astral for this tag.
PYTHONS = {
    "x86_64": (36776589, "4d3c16c59ed0802a3d1d34d8bce504b15ceeaff39d62dadecc6a9fa803412aed"),
    "aarch64": (30635900, "90f48337bc3dd4ae6eda81e001e4c981236d403b6b5a07778bbdf94ee423dc00"),
}


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def verified(path, size, expected):
    if path.stat().st_size != size or digest(path) != expected:
        raise ValueError("Size or SHA256 mismatch: " + str(path))
    return path


@contextmanager
def compressed_tar(path, level=5):
    with path.open("wb") as stream:
        with gzip.GzipFile(filename="", mode="wb", fileobj=stream, mtime=0, compresslevel=level) as gz:
            with tarfile.open(fileobj=gz, mode="w|", format=tarfile.PAX_FORMAT) as archive:
                yield archive


def put_bytes(archive, name, contents, mode=0o644):
    entry = tarfile.TarInfo(name)
    entry.mode, entry.size = mode, len(contents)
    archive.addfile(entry, io.BytesIO(contents))


def put_file(archive, name, path):
    entry = tarfile.TarInfo(name)
    entry.mode, entry.size = 0o644, path.stat().st_size
    with path.open("rb") as stream:
        archive.addfile(entry, stream)


def in_python_root(name):
    return name == "python" or name.startswith("python/")


def copy_runtime(archive, source):
    """Rename the interpreter tree in the tar stream. Preserve POSIX links and modes."""
    with tarfile.open(source, "r|gz") as runtime:
        for member in runtime:
            name = member.name
            if not in_python_root(name) or ".." in PurePosixPath(name).parts:
                raise ValueError("Unexpected runtime path: " + name)
            if not (member.isdir() or member.isfile() or member.issym() or member.islnk()):
                raise ValueError("Unsupported runtime entry: " + name)
            entry = tarfile.TarInfo("runtime/" + name)
            entry.mode, entry.type, entry.size = member.mode & 0o777, member.type, member.size
            if member.issym() or member.islnk():
                dest = member.linkname
                resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), dest)
                                              if member.issym() else dest)
                if not in_python_root(resolved):
                    raise ValueError("Runtime link leaves python/: " + name)
                entry.linkname = dest if member.issym() else "runtime/" + resolved
            if member.isfile():
                with runtime.extractfile(member) as stream:
                    archive.addfile(entry, stream)
            else:
                archive.addfile(entry)


def runtime_name(arch):
    return "cpython-%s+%s-%s-unknown-linux-gnu-install_only_stripped.tar.gz" % (VERSION, RELEASE, arch)


def cached_asset(path, url, size, sha):
    if path.is_file() and path.stat().st_size == size and digest(path) == sha:
        return
    hosts = {urlsplit(url).netloc}
    if "github.com" in hosts:
        hosts.update({"release-assets.githubusercontent.com", "objects.githubusercontent.com"})
    checked_url(url, hosts)
    opener = request.build_opener(MirrorRedirect(hosts))
    path.parent.mkdir(parents=True, exist_ok=True)
    print("Fetching", path.name, flush=True)
    with tempfile.TemporaryDirectory(dir=path.parent) as tmp:
        part = Path(tmp) / path.name
        with opener.open(request.Request(url, headers={"User-Agent": "doc-integrator-build"}), timeout=90) as src:
            with part.open("wb") as dst:
                remaining = size
                while True:
                    block = src.read(min(1024 * 1024, remaining + 1))
                    if not block:
                        break
                    remaining -= len(block)
                    if remaining < 0:
                        raise ValueError("Download exceeded pinned length: " + path.name)
                    dst.write(block)
        verified(part, size, sha)
        os.replace(part, path)


def fetch_inputs(architectures, sources, vendor):
    manifest = json.loads((ROOT / "vendor/wheels.lock.json").read_text(encoding="utf-8"))
    wheels = [("py", spec) for spec in manifest["pure"].values()]
    for arch in architectures:
        name = runtime_name(arch)
        cached_asset(sources / name, UPSTREAM + quote(name), *PYTHONS[arch])
        platform = "manylinux_" + arch
        wheels.append(("wheels/" + platform, manifest["pymupdf"][platform]))
    for name, (size, sha, _) in MODELS.items():
        cached_asset(sources / name, MODEL_URL + name, size, sha)
    for folder, (name, size, sha, wheel_path) in wheels:
        cached_asset(vendor / folder / name, MIRRORS["pypi"] + wheel_path + "/" + name, size, sha)


def build_kit(arch, out, sources, vendor):
    runtime = verified(sources / runtime_name(arch), *PYTHONS[arch])
    manifest = json.loads((ROOT / "vendor/wheels.lock.json").read_text(encoding="utf-8"))
    platform = "manylinux_" + arch
    wheel = manifest["pymupdf"][platform]
    wheels = [("wheels/" + platform, wheel)] + [("py", spec) for spec in manifest["pure"].values()]
    wheel_paths = []
    for folder, (name, length, sha, _) in wheels:
        wheel_paths.append(("vendor/" + folder + "/" + name,
                            verified(vendor / folder / name, length, sha)))
    model_paths = [("runtime/tessdata/" + name, verified(sources / name, length, sha))
                   for name, (length, sha, _) in MODELS.items()]
    out = out.resolve()
    if ROOT == out or ROOT in out.parents:
        raise ValueError("Build outside the source tree.")
    out.mkdir(parents=True, exist_ok=True)
    output = out / ("doc-integrator-offline-linux-" + arch + ".tar.gz")
    public_docs = ["SKILL.md", "THIRD_PARTY_NOTICES.md", "vendor/README.md"]
    public_docs.extend(p.relative_to(ROOT).as_posix() for p in sorted((ROOT / "reference").glob("*.md")))
    files = set(public_docs + ["README.md", "tools/fetch_vendor.py", "tools/offline/README.md",
                               "vendor/wheels.lock.json", "tests/fixtures/offline-ocr.png"])
    for pattern in ("scripts/*.py", "scripts/dilib/*.py", "profiles/*.json", "rules/*.json", "vendor/licenses/*.txt"):
        files.update(p.relative_to(ROOT).as_posix() for p in ROOT.glob(pattern))
    launcher = (ROOT / "tools/offline/run.sh").read_text(encoding="utf-8")
    for key, value in (("ARCH", arch), ("WHEEL", wheel[0]), ("PYPDF", manifest["pure"]["pypdf"][0]),
                       ("TYPING", manifest["pure"]["typing_extensions"][0])):
        launcher = launcher.replace("@" + key + "@", value)
    with tempfile.TemporaryDirectory(dir=out) as tmp:
        staging = Path(tmp)
        payload = staging / "payload.tar.gz"
        with compressed_tar(payload) as archive:
            for rel in sorted(files):
                if rel.endswith(".py"):
                    code = (ROOT / rel).read_bytes()
                    compile(code, rel, "exec")
                    put_bytes(archive, rel, code)
                else:
                    put_file(archive, rel, ROOT / rel)
            for rel, path in wheel_paths + model_paths:
                put_file(archive, rel, path)
            copy_runtime(archive, runtime)
            source_note = ("CPython %s, python-build-standalone %s, SHA256 %s\n%s%s\n"
                           "OCR models: https://github.com/tesseract-ocr/tessdata_fast/tree/%s\n" %
                           (VERSION, RELEASE, PYTHONS[arch][1], UPSTREAM, runtime.name, MODEL_REV))
            put_bytes(archive, "runtime/SOURCE.txt", source_note.encode("utf-8"))
        kit = staging / output.name
        top = "doc-integrator/"
        with compressed_tar(kit, level=1) as archive:
            for rel in public_docs:
                put_file(archive, top + rel, ROOT / rel)
            put_file(archive, top + "README.md", ROOT / "tools/offline/README.md")
            put_bytes(archive, top + "run.sh", launcher.encode("utf-8"), 0o755)
            put_bytes(archive, top + "payload.sha256", (digest(payload) + "\n").encode("ascii"))
            put_file(archive, top + "payload.tar.gz", payload)
        checksum = staging / (output.name + ".sha256")
        checksum.write_bytes(("%s  %s\n" % (digest(kit), output.name)).encode("ascii"))
        os.replace(kit, output)
        os.replace(checksum, out / checksum.name)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", choices=["all", *PYTHONS], default="all")
    parser.add_argument("--out", required=True, type=Path, help="Release output directory")
    parser.add_argument("--from-dir", required=True, type=Path, help="Directory with original CPython archives and OCR files")
    parser.add_argument("--vendor-dir", type=Path, default=ROOT / "vendor", help="Directory with verified py/ and wheels/ trees")
    parser.add_argument("--fetch-inputs", action="store_true",
                        help="Build host only: fetch missing or mismatched pinned assets, then build")
    args = parser.parse_args()
    architectures = list(PYTHONS) if args.arch == "all" else [args.arch]
    if args.fetch_inputs:
        fetch_inputs(architectures, args.from_dir, args.vendor_dir)
    for arch in architectures:
        print(build_kit(arch, args.out, args.from_dir, args.vendor_dir), flush=True)


if __name__ == "__main__":
    main()
