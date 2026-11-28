"""Copy only the exact reviewed skill files paired with this offline runtime."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys


def stage_skill(source, stage):
    manifest = json.loads((stage / "runtime/skill-files.json").read_text("ascii"))
    for name, expected in manifest.items():
        rel = PurePosixPath(name)
        if rel.is_absolute() or ".." in rel.parts or "\\" in name or ":" in name:
            raise ValueError("Invalid skill file name: " + name)
        original = source.joinpath(*rel.parts)
        if not original.resolve(strict=True).is_relative_to(source.resolve(strict=True)):
            raise ValueError("Skill file outside the installation: " + name)
        with original.open("rb") as stream:
            data = stream.read(512 * 1024 + 1)
        if len(data) > 512 * 1024 or hashlib.sha256(data).hexdigest() != expected:
            raise ValueError("Skill differs from the approved runtime pair: " + name)
        dest = stage.joinpath(*rel.parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("xb") as stream:
            stream.write(data)
        if name == "run.sh":
            dest.chmod(0o755)


if __name__ == "__main__":
    stage_skill(Path(sys.argv[1]), Path(sys.argv[2]))
