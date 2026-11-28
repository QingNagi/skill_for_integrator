#!/usr/bin/env python3
"""Build self-deploying Linux offline kits on Linux or Windows, from pinned local inputs."""
import argparse
from contextlib import contextmanager
import gzip
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import posixpath
import shlex
import sys
import tarfile
import tempfile
from urllib import request
from urllib.parse import quote, urlsplit

sys.dont_write_bytecode = True
from build_offline_bundle import TOP, source_files, skill_bytes, source_manifest, runtime_support, wheel_specs
from fetch_vendor import VENDOR, MIRRORS, MirrorRedirect, checked_url
from prepare_win64_runtime import MODEL_REV, MODEL_URL, MODELS
from runtime_bom import ocr_binary, runtime_bom


REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / TOP
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


def put_file(archive, name, path, mode=0o644):
    entry = tarfile.TarInfo(name)
    entry.mode, entry.size = mode, path.stat().st_size
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
        with opener.open(request.Request(url, headers={"User-Agent": "traced-template-fill-build"}), timeout=90) as src:
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
    wheels = {}
    for arch in architectures:
        name = runtime_name(arch)
        cached_asset(sources / name, UPSTREAM + quote(name), *PYTHONS[arch])
        for rel, _, spec in wheel_specs("manylinux_" + arch):
            wheels[rel] = spec
    for name, (size, sha, _) in MODELS.items():
        cached_asset(sources / name, MODEL_URL + name, size, sha)
    for rel, (name, size, sha, wheel_path) in wheels.items():
        cached_asset(vendor / rel, MIRRORS["pypi"] + wheel_path + "/" + name, size, sha)


def build_kit(arch, out, sources, vendor, runtime_only=False):
    runtime = verified(sources / runtime_name(arch), *PYTHONS[arch])
    platform = "manylinux_" + arch
    py_minor = ".".join(VERSION.split(".")[:2])
    bom = runtime_bom(platform, VERSION + "+" + RELEASE, UPSTREAM + quote(runtime.name), *PYTHONS[arch],
                      "runtime/python/lib/python" + py_minor + "/LICENSE.txt", vendor)
    engine = ocr_binary(platform, vendor)
    wheel_paths = [("vendor/" + rel, verified(vendor / rel, spec[1], spec[2]))
                   for rel, _, spec in wheel_specs(platform)]
    model_paths = [("runtime/tessdata/" + name, verified(sources / name, length, sha))
                   for name, (length, sha, _) in MODELS.items()]
    out = out.resolve()
    if ROOT == out or ROOT in out.parents:
        raise ValueError("Build outside the source tree.")
    out.mkdir(parents=True, exist_ok=True)
    label = "runtime" if runtime_only else "offline"
    output = out / (TOP + "-" + label + "-linux-" + arch + ".tar.gz")
    public_docs = ["SKILL.md", "THIRD_PARTY_NOTICES.md", "vendor/README.md"]
    public_docs.extend(p.relative_to(ROOT).as_posix() for p in sorted((ROOT / "references").glob("*.md")))
    if (ROOT / "LICENSE").is_file():
        public_docs.append("LICENSE")
    files = {rel: path for path, rel in source_files()}
    required = [rel for rel in files if rel.startswith(("scripts/", "config/"))]
    required.extend(rel for rel, _ in wheel_paths)
    launcher = (REPO / "tools/offline/run.sh").read_text(encoding="utf-8")
    for key, value in (("ARCH", arch), ("PYMINOR", py_minor),
                       ("REQUIRED_FILES", " ".join(shlex.quote(rel) for rel in sorted(required)))):
        launcher = launcher.replace("@" + key + "@", value)
    with tempfile.TemporaryDirectory(dir=out) as tmp:
        staging = Path(tmp)
        payload = staging / "payload.tar.gz"
        with compressed_tar(payload) as archive:
            put_bytes(archive, "runtime-bom.cdx.json", bom)
            if runtime_only:
                put_bytes(archive, "runtime/skill-files.json", source_manifest())
                put_file(archive, "runtime/stage_skill.py", REPO / "tools/offline/stage_skill.py")
            else:
                for rel, path in sorted(files.items()):
                    if rel.endswith(".py"):
                        compile(path.read_bytes(), rel, "exec")
                    put_bytes(archive, rel, skill_bytes(path, rel), 0o755 if rel == "run.sh" else 0o644)
            for path, rel in runtime_support():
                put_file(archive, rel, path)
            for rel, path in wheel_paths + model_paths:
                put_file(archive, rel, path)
            put_file(archive, "runtime/ocr/" + engine.name, engine, 0o755)
            copy_runtime(archive, runtime)
            source_note = ("CPython %s, python-build-standalone %s, SHA256 %s\n%s%s\n"
                           "OCR models: https://github.com/tesseract-ocr/tessdata_fast/tree/%s\n" %
                           (VERSION, RELEASE, PYTHONS[arch][1], UPSTREAM, runtime.name, MODEL_REV))
            put_bytes(archive, "runtime/SOURCE.txt", source_note.encode("utf-8"))
        payload_sha = digest(payload)
        launcher = launcher.replace("@PAYLOAD_SHA256@", payload_sha)
        kit = staging / output.name
        top = TOP + ("/offline-runtime/" if runtime_only else "/")
        with compressed_tar(kit, level=1) as archive:
            put_bytes(archive, top + "runtime-bom.cdx.json", bom)
            for rel in ([] if runtime_only else public_docs):
                if rel in files:
                    put_bytes(archive, top + rel, skill_bytes(files[rel], rel))
                else:
                    put_file(archive, top + rel, ROOT / rel)
            readme = "runtime.md" if runtime_only else "README.md"
            put_file(archive, top + "README.md", REPO / "tools/offline" / readme)
            put_bytes(archive, top + "run.sh", launcher.encode("utf-8"), 0o755)
            put_bytes(archive, top + "payload.sha256", (payload_sha + "\n").encode("ascii"))
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
    parser.add_argument("--vendor-dir", type=Path, default=Path(VENDOR), help="Directory with verified py/ and wheels/ trees")
    parser.add_argument("--fetch-inputs", action="store_true",
                        help="Build host only: fetch missing or mismatched pinned assets, then build")
    parser.add_argument("--runtime-only", action="store_true", help="Build platform companions for a separate source ZIP")
    args = parser.parse_args()
    architectures = list(PYTHONS) if args.arch == "all" else [args.arch]
    if args.fetch_inputs:
        fetch_inputs(architectures, args.from_dir, args.vendor_dir)
    for arch in architectures:
        print(build_kit(arch, args.out, args.from_dir, args.vendor_dir, args.runtime_only), flush=True)


if __name__ == "__main__":
    main()
