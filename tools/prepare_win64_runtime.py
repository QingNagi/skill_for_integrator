#!/usr/bin/env python3
"""Stage the official Windows x64 embedded Python and the two offline OCR models."""
import argparse
import hashlib
import io
import os
from pathlib import Path
import sys
import tempfile
import time
import zipfile

sys.dont_write_bytecode = True
from fetch_vendor import is_good, mirror_opener


DOWNLOAD_TIMEOUT_SECONDS = 90
PYTHON_VERSION = "3.14.10"
PYTHON_ZIP = "python-3.14.10-embed-amd64.zip"
PYTHON_RELEASE = "https://www.python.org/downloads/release/python-31410/"
PYTHON_URL = "https://www.python.org/ftp/python/3.14.10/" + PYTHON_ZIP
PYTHON_SIZE = 12619664
PYTHON_SHA256 = "90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159"

PYTHON_EVIDENCE = {
    ".spdx.json": (15254, "d448c78f5fae170303d3b022d61507155e4d30e541c32bff3a4edd2b1e8dbf47"),
    ".sigstore": (7275, "6025bde4c3672b62e4d19e16cbe9e3e6c921594ffc6014b0b6f30a970f6bf43f"),
}

MODEL_REPOSITORY = "https://github.com/tesseract-ocr/tessdata_fast"
MODEL_REV = "87416418657359cb625c412a48b6e1d6d41c29bd"
MODEL_URL = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/" + MODEL_REV + "/"
# Git blob IDs were compared with the tree at MODEL_REV; SHA256 is used at build time.
MODELS = {
    "chi_sim.traineddata": (2469156, "a5fcb6f0db1e1d6d8522f39db4e848f05984669172e584e8d76b6b3141e1f730",
                            "388bac276d033d06e5ed5ba7a7ad14ae58f97dab"),
    "eng.traineddata": (4113088, "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2",
                        "bbef4675053b5b468cdb477053e28b1c698ba08e"),
    "LICENSE": (11358, "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
                "d645695673349e3947e8e5ae42332d0ac3164cd7"),
}


def read_verified(source_dir, name, url, size, digest):
    if source_dir:
        stream = (source_dir / name).open("rb")
    else:
        stream = mirror_opener(url).open(url, timeout=DOWNLOAD_TIMEOUT_SECONDS)
    with stream:
        data = stream.read(size + 1)
    if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("Size or SHA256 mismatch for " + name)
    return data


def cache_inputs(source_dir):
    source_dir.mkdir(parents=True, exist_ok=True)
    assets = [(PYTHON_ZIP, PYTHON_URL, PYTHON_SIZE, PYTHON_SHA256)]
    assets.extend((PYTHON_ZIP + suffix, PYTHON_URL + suffix, size, sha)
                  for suffix, (size, sha) in PYTHON_EVIDENCE.items())
    assets.extend((name, MODEL_URL + name, size, sha) for name, (size, sha, _) in MODELS.items())
    for name, url, size, sha in assets:
        dest = source_dir / name
        if is_good(dest, size, sha):
            continue
        with tempfile.TemporaryDirectory(dir=source_dir) as tmp:
            part = Path(tmp) / name
            part.write_bytes(read_verified(None, name, url, size, sha))
            os.replace(part, dest)


def stage_runtime(out, source_dir):
    if out.exists():
        raise FileExistsError("Choose a fresh runtime destination: " + str(out))
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out.parent) as staging:
        stage = Path(staging) / "runtime"
        python_dir = stage / "python"
        python_dir.mkdir(parents=True)
        data = read_verified(source_dir, PYTHON_ZIP, PYTHON_URL, PYTHON_SIZE, PYTHON_SHA256)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            archive.extractall(python_dir)
        for suffix, (length, digest) in PYTHON_EVIDENCE.items():
            evidence = read_verified(source_dir, PYTHON_ZIP + suffix, PYTHON_URL + suffix, length, digest)
            (python_dir / (PYTHON_ZIP + suffix)).write_bytes(evidence)
        # Isolated embedded runtime: these entries resolve against this ._pth file.
        (python_dir / "python314._pth").write_text("python314.zip\n.\n../../scripts\n", encoding="ascii", newline="\r\n")
        (python_dir / "SOURCE.md").write_text(
            "# Embedded CPython " + PYTHON_VERSION + " for Windows x64\n\n"
            "The Python Software Foundation distributes the [original ZIP](" + PYTHON_URL + "). "
            "Its [release page](" + PYTHON_RELEASE + ") publishes SHA256 `" + PYTHON_SHA256 + "`.\n\n"
            "The bundle preserves the original executable, DLLs, standard library and LICENSE.txt. "
            "The local python314._pth adds the skill's scripts directory. The embedded interpreter "
            "uses its private standard library and leaves global site packages disabled.\n",
            encoding="utf-8", newline="\r\n",
        )
        data_dir = stage / "tessdata"
        data_dir.mkdir()
        rows = [
            "# Tesseract fast integer language models", "",
            "The Chinese simplified and English models and the Apache-2.0 LICENSE come from "
            "[tessdata_fast at the pinned commit](" + MODEL_REPOSITORY + "/tree/" + MODEL_REV + ").",
            "", "| File | Bytes | SHA256 | Upstream Git blob |", "|---|---:|---|---|",
        ]
        for name, (size, sha, blob) in MODELS.items():
            (data_dir / name).write_bytes(read_verified(source_dir, name, MODEL_URL + name, size, sha))
            rows.append("| `%s` | %d | `%s` | `%s` |" % (name, size, sha, blob))
        (data_dir / "SOURCE.md").write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\r\n")
        # Endpoint scanners may briefly hold files just extracted into this private directory.
        for attempt in range(8):
            try:
                os.rename(stage, out)
                break
            except OSError as exc:
                if out.exists() or getattr(exc, "winerror", None) not in (5, 32, 33) or attempt == 7:
                    raise
                time.sleep(0.1 * (attempt + 1))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path, help="New runtime directory to create")
    parser.add_argument("--from-dir", type=Path, help="Use already downloaded original files, offline")
    parser.add_argument("--fetch-inputs", action="store_true", help="Fetch pinned Windows originals into --from-dir")
    args = parser.parse_args(argv)
    if args.fetch_inputs and not args.from_dir:
        parser.error("--fetch-inputs requires --from-dir")
    try:
        if args.fetch_inputs:
            cache_inputs(args.from_dir)
        stage_runtime(args.out.resolve(), args.from_dir)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        parser.exit(1, "Runtime preparation failed: %s\n" % exc)
    print("Prepared", args.out)


if __name__ == "__main__":
    main()
