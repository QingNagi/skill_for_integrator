#!/usr/bin/env python3
"""Build the Windows x64 first-use Agent skill ZIP from pinned offline inputs."""
import argparse
import os
from pathlib import Path
import sys
import tempfile
import zipfile

sys.dont_write_bytecode = True
import build_offline_bundle as bundle
from prepare_win64_runtime import PYTHON_SHA256, PYTHON_SIZE, PYTHON_URL, PYTHON_VERSION, stage_runtime
from runtime_bom import ocr_binary, runtime_bom


def runtime_files(members):
    """Check the modules and native files that individual CLI commands load on demand."""
    top = bundle.TOP + "/"
    return sorted(name[len(top):] for name in members if name.startswith(top) and not name.endswith("/")
                  and (name == top + "rules/test-data.json"
                       or name.startswith(top + "scripts/") and name.endswith(".py")
                       or name.startswith(top + "runtime/python/") and Path(name).suffix.lower()
                       in {".exe", ".dll", ".pyd", ".zip", "._pth"}
                       or name == top + "runtime/ocr/tesseract.exe"
                       or name.startswith(top + "vendor/") and (name.endswith(".whl") or name.endswith(".lock.json"))))


def render_launcher(template, members):
    entries = "\n".join("        '" + rel.replace("/", "\\").replace("'", "''") + "'" for rel in runtime_files(members))
    return template.replace("        # @RUNTIME_FILES@", entries)


def build_kit(output, sources, vendor=None):
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
        skill_zip, _ = bundle.build(str(stage), "agent", "win_amd64", vendor)
        payload = Path(skill_zip)
        with zipfile.ZipFile(payload, "a", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(bundle.TOP + "/runtime-bom.cdx.json", bom)
            archive.write(engine, bundle.TOP + "/runtime/ocr/" + engine.name)
            for file in sorted(runtime.rglob("*")):
                if file.is_file():
                    archive.write(file, bundle.TOP + "/" + file.relative_to(stage).as_posix())
        with zipfile.ZipFile(payload) as archive:
            bad = archive.testzip()
            if bad:
                raise ValueError("损坏的发布文件：" + bad)
            required = runtime_files(archive.namelist()) + [
                "SKILL.md", "assets/offline-ocr.png", "vendor/wheels.lock.json",
                "runtime/tessdata/eng.traineddata", "runtime/tessdata/chi_sim.traineddata",
            ]
            launcher = render_launcher((templates / "run.ps1").read_text(encoding="utf-8"), archive.namelist())
        digest = bundle.fv.sha256_of(payload)
        kit = stage / "kit.zip"
        with zipfile.ZipFile(kit, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            top = bundle.TOP + "/"
            archive.writestr(top + "runtime-bom.cdx.json", bom)
            archive.writestr(top + "run.ps1", launcher.encode("utf-8-sig"))
            cmd = (templates / "run.cmd").read_text(encoding="ascii").replace("@PAYLOAD_SHA256@", digest)
            archive.writestr(top + "run.cmd", cmd.replace("\n", "\r\n").encode("ascii"))
            archive.writestr(top + "runtime-files.txt", "\r\n".join(rel.replace("/", "\\") for rel in required) + "\r\n")
            for name in ("cmd_install.py", "install.ps1", "README.md"):
                archive.write(templates / name, top + name)
            archive.writestr(top + "run.sh", (templates / "windows-bash.sh").read_text(encoding="utf-8"))
            docs = [root / "SKILL.md", root / "vendor/README.md", root / "THIRD_PARTY_NOTICES.md"]
            if (root / "LICENSE").is_file():
                docs.append(root / "LICENSE")
            for file in docs + sorted((root / "references").glob("*.md")):
                archive.write(file, top + file.relative_to(root).as_posix())
            archive.write(payload, top + "payload.zip", compress_type=zipfile.ZIP_STORED)
            archive.writestr(top + "payload.sha256", digest + "\r\n")
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
    args = parser.parse_args()
    build_kit(args.out.resolve(), args.from_dir, args.vendor_dir)
