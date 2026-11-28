#!/usr/bin/env python3
"""Build the Windows x64 first-use Agent skill ZIP from pinned offline inputs."""
import argparse
import os
import shutil
from pathlib import Path
import sys
import tempfile
import zipfile

sys.dont_write_bytecode = True
import build_offline_bundle as bundle
from prepare_win64_runtime import PYTHON_SHA256, PYTHON_SIZE, PYTHON_URL, PYTHON_VERSION, cache_inputs, stage_runtime
from runtime_bom import ocr_binary, runtime_bom


def put(archive, name, source, method=zipfile.ZIP_DEFLATED):
    """Use the ZIP epoch and stable permissions for files and generated entries alike."""
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.create_system = 3
    info.external_attr = (0o100755 if name.endswith(".sh") else 0o100644) << 16
    info.compress_type = method
    if isinstance(source, Path):
        info.file_size = source.stat().st_size
        with source.open("rb") as src, archive.open(info, "w") as dst:
            shutil.copyfileobj(src, dst)
    else:
        archive.writestr(info, source)


def runtime_files(members):
    """Check the modules and native files that individual CLI commands load on demand."""
    top = bundle.TOP + "/"
    return sorted(name[len(top):] for name in members if name.startswith(top) and not name.endswith("/")
                  and (name == top + "rules/test-data.json" or name.startswith(top + "config/")
                       or name.startswith(top + "scripts/") and name.endswith((".py", ".ps1"))
                       or name.startswith(top + "runtime/python/") and Path(name).suffix.lower()
                       in {".exe", ".dll", ".pyd", ".zip", "._pth"}
                       or name == top + "runtime/ocr/tesseract.exe"
                       or name.startswith(top + "vendor/") and (name.endswith(".whl") or name.endswith(".lock.json"))))


def render_launcher(template, members):
    entries = "\n".join("        '" + rel.replace("/", "\\").replace("'", "''") + "'" for rel in runtime_files(members))
    return template.replace("        # @RUNTIME_FILES@", entries)


def build_kit(output, sources, vendor=None, runtime_only=False):
    root = Path(bundle.ROOT)
    templates = Path(bundle.fv.REPO) / "tools" / "offline"
    if root in output.parents:
        raise ValueError("请将生成的发布包放在技能目录之外。")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as tmp:
        stage = Path(tmp)
        runtime = stage / "runtime"
        vendor = Path(vendor or bundle.fv.VENDOR)
        engine = ocr_binary("win_amd64", vendor)
        stage_runtime(runtime, sources)
        bom = runtime_bom("win_amd64", PYTHON_VERSION, PYTHON_URL, PYTHON_SIZE, PYTHON_SHA256,
                          "runtime/python/LICENSE.txt", vendor)
        payload = stage / "payload.zip"
        source = list(bundle.source_files())
        source_names = {rel for _, rel in source}
        with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            top = bundle.TOP + "/"
            if runtime_only:
                put(archive, top + "runtime/skill-files.json", bundle.source_manifest())
                put(archive, top + "runtime/stage_skill.py", templates / "stage_skill.py")
            else:
                for path, rel in source:
                    put(archive, top + rel, bundle.skill_bytes(path, rel))
            for path, rel in bundle.runtime_support():
                put(archive, top + rel, path)
            for path, rel in bundle.wheel_files("win_amd64", vendor):
                put(archive, top + "vendor/" + rel, path, zipfile.ZIP_STORED)
            put(archive, top + "vendor/pdf-mode.txt", "full")
            put(archive, bundle.TOP + "/runtime-bom.cdx.json", bom)
            put(archive, top + "runtime/ocr/" + engine.name, engine)
            for file in sorted(runtime.rglob("*"), key=lambda p: p.relative_to(runtime).as_posix()):
                if file.is_file():
                    put(archive, top + file.relative_to(stage).as_posix(), file)
        with zipfile.ZipFile(payload) as archive:
            bad = archive.testzip()
            if bad:
                raise ValueError("损坏的发布文件：" + bad)
            members = list(set(archive.namelist()) | {bundle.TOP + "/" + rel for _, rel in source})
            required = runtime_files(members) + [
                "SKILL.md", "assets/offline-ocr.png", "vendor/wheels.lock.json",
                "runtime/tessdata/eng.traineddata", "runtime/tessdata/chi_sim.traineddata",
            ]
            launcher = render_launcher((templates / "run.ps1").read_text(encoding="utf-8"), members)
        digest = bundle.fv.sha256_of(payload)
        kit = stage / "kit.zip"
        with zipfile.ZipFile(kit, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            top = bundle.TOP + ("/offline-runtime/" if runtime_only else "/")
            put(archive, top + "runtime-bom.cdx.json", bom)
            cmd = (templates / "run.cmd").read_text(encoding="ascii").replace("@PAYLOAD_SHA256@", digest)
            put(archive, top + "run.cmd", cmd.replace("\n", "\r\n").encode("ascii"))
            put(archive, top + "runtime-files.txt", "\r\n".join(rel.replace("/", "\\") for rel in required) + "\r\n")
            put(archive, top + "cmd_install.py", templates / "cmd_install.py")
            if runtime_only:
                put(archive, top + "README.md", templates / "runtime.md")
            else:
                put(archive, top + "run.ps1", launcher.encode("utf-8-sig"))
                for name in ("install.ps1", "README.md"):
                    put(archive, top + name, templates / name)
                put(archive, top + "run.sh", (templates / "windows-bash.sh").read_text(encoding="utf-8"))
                docs = [root / "SKILL.md", root / "vendor/README.md", root / "THIRD_PARTY_NOTICES.md"]
                if (root / "LICENSE").is_file():
                    docs.append(root / "LICENSE")
                for file in docs + sorted((root / "references").glob("*.md")):
                    rel = file.relative_to(root).as_posix()
                    put(archive, top + rel, bundle.skill_bytes(file, rel) if rel in source_names else file)
            put(archive, top + "payload.zip", payload, zipfile.ZIP_STORED)
            put(archive, top + "payload.sha256", digest + "\r\n")
        package_sha = bundle.fv.sha256_of(kit)
        checksum = stage / "kit.sha256"
        checksum.write_bytes(("%s  %s\n" % (package_sha, output.name)).encode("utf-8"))
        os.replace(kit, output)
        os.replace(checksum, output.with_name(output.name + ".sha256"))
    print(str(output), package_sha)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="Output ZIP, outside the skill tree")
    parser.add_argument("--from-dir", type=Path, help="Use local original interpreter and model files")
    parser.add_argument("--vendor-dir", type=Path, help="External directory with pinned py/ and wheels/ trees")
    parser.add_argument("--runtime-only", action="store_true", help="Build the companion for a separate source ZIP")
    parser.add_argument("--fetch-inputs", action="store_true", help="Fetch missing or mismatched pinned Windows inputs into --from-dir")
    args = parser.parse_args()
    if args.fetch_inputs:
        if not args.from_dir:
            parser.error("--fetch-inputs requires --from-dir")
        cache_inputs(args.from_dir)
    build_kit(args.out.resolve(), args.from_dir, args.vendor_dir, args.runtime_only)
