"""Stage regular files from hash-checked release archives for a local antivirus, including nested wheels."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import zipfile


def fingerprint(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def destination(root, name):
    path = PurePosixPath(name)
    if not path.parts or path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
        raise ValueError(f"Unsafe archive member {name!r}")
    return root.joinpath(*path.parts)


def stage(release, output):
    output.mkdir(parents=True, exist_ok=False)
    archives, links, pending, expanded = {}, [], [], set()
    for sidecar in sorted(release.glob("*.sha256")):
        artifact = sidecar.with_suffix("")
        declared, filename = sidecar.read_text("ascii").split()
        if artifact.name != filename or fingerprint(artifact) != declared:
            raise ValueError(f"Wrong digest for {artifact.name}")
        copy = output / artifact.name
        shutil.copyfile(artifact, copy)
        archives[copy.name] = declared
        pending.append(copy)
    if not archives:
        raise ValueError("No release archives with SHA-256 sidecars")
    file_count = 0
    ocr, collisions = {}, []

    def member(folder, name, stream):
        nonlocal file_count
        target = destination(folder, name)
        if target.exists():
            # A Windows staging volume may fold distinct Linux names such as Eterm and eterm.
            target = output / "case-collisions" / str(file_count) / target.name
            collisions.append([folder.name, name, target.relative_to(output).as_posix()])
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as sink:
            shutil.copyfileobj(stream, sink)
        file_count += 1
        if target.name in ("tesseract", "tesseract.exe"):
            ocr[target.relative_to(output).as_posix()] = fingerprint(target)
        if target.name.lower().endswith((".zip", ".whl", ".gz", ".tgz")):
            pending.append(target)

    while pending:
        artifact = pending.pop()
        key = fingerprint(artifact)
        if key in expanded:
            continue
        expanded.add(key)
        folder = output / "contents" / str(len(expanded))
        if zipfile.is_zipfile(artifact):
            with zipfile.ZipFile(artifact) as archive:
                for item in archive.infolist():
                    if not item.is_dir():
                        with archive.open(item) as stream:
                            member(folder, item.filename, stream)
        elif artifact.name.endswith((".tar.gz", ".tgz")):
            with tarfile.open(artifact, "r|gz") as archive:
                for item in archive:
                    destination(folder, item.name)
                    if item.isfile():
                        with archive.extractfile(item) as stream:
                            member(folder, item.name, stream)
                    elif item.islnk() or item.issym():
                        links.append([artifact.name, item.name, item.linkname])
                    elif not item.isdir():
                        raise ValueError("Unexpected tar member " + item.name)
        elif artifact.suffix == ".gz":
            with gzip.open(artifact) as stream:
                member(folder, artifact.stem, stream)
        else:
            raise ValueError("Malformed archive " + artifact.name)
    return {"inputs": archives, "distinct_containers_expanded": len(expanded),
            "regular_members_staged": file_count, "ocr_engines": ocr,
            "case_collisions_staged_separately": collisions, "tar_links_not_materialized": links}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release", type=Path)
    parser.add_argument("new_stage", type=Path)
    parser.add_argument("inventory", type=Path)
    args = parser.parse_args()
    result = stage(args.release.resolve(), args.new_stage.resolve())
    args.inventory.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

