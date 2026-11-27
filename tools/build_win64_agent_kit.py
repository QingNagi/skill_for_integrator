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
from prepare_win64_runtime import stage_runtime


def build_kit(output, sources):
    root = Path(bundle.ROOT)
    if root in output.parents:
        raise ValueError("请将生成的发布包放在技能目录之外。")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as tmp:
        stage = Path(tmp)
        runtime = stage / "runtime"
        stage_runtime(runtime, sources)
        skill_zip, _ = bundle.build(str(stage), "agent", "win_amd64")
        payload = Path(skill_zip)
        with zipfile.ZipFile(payload, "a", compression=zipfile.ZIP_DEFLATED) as archive:
            for file in sorted(runtime.rglob("*")):
                if file.is_file():
                    archive.write(file, "doc-integrator/" + file.relative_to(stage).as_posix())
        with zipfile.ZipFile(payload) as archive:
            bad = archive.testzip()
            if bad:
                raise ValueError("损坏的发布文件：" + bad)
        digest = bundle.fv.sha256_of(payload)
        kit = stage / "kit.zip"
        with zipfile.ZipFile(kit, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            top = "doc-integrator/"
            for name in ("run.ps1", "install.ps1", "README.md"):
                archive.write(root / "tools" / "offline" / name, top + name)
            archive.writestr(top + "run.sh", (root / "tools/offline/windows-bash.sh").read_text(encoding="utf-8"))
            docs = [root / "SKILL.md", root / "vendor/README.md", root / "THIRD_PARTY_NOTICES.md"]
            for file in docs + sorted((root / "reference").glob("*.md")):
                archive.write(file, top + file.relative_to(root).as_posix())
            archive.write(payload, top + "payload.zip", compress_type=zipfile.ZIP_STORED)
            archive.writestr(top + "payload.sha256", digest + "\n")
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
    args = parser.parse_args()
    build_kit(args.out.resolve(), args.from_dir)
