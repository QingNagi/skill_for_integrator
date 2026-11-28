"""Install the verified Windows payload for run.cmd, sharing the PowerShell install lock."""
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from contextlib import contextmanager


@contextmanager
def install_lock(parent):
    import _winapi
    lock = str(parent / ".traced-template-fill.install.lock")
    while True:
        try:
            # No share mode, OPEN_ALWAYS. Windows releases the handle if an installer stops.
            handle = _winapi.CreateFile(lock, _winapi.GENERIC_WRITE, 0, 0, 4, 0, 0)
            break
        except OSError as exc:
            if exc.winerror not in (32, 33):
                raise
            time.sleep(0.2)
    try:
        yield
    finally:
        _winapi.CloseHandle(handle)


def missing(root, files):
    return [name for name in files if not (root / name).is_file() or not (root / name).stat().st_size]


def install(payload, target, digest, required):
    parent = target.parent
    parent.mkdir(exist_ok=True, parents=True)
    with install_lock(parent):
        marker = ".offline-payload.sha256"
        if target.exists():
            if not (target / marker).is_file() or (target / marker).read_text("ascii").strip() != digest:
                raise ValueError("Existing installation does not match this payload: " + str(target))
            repair = missing(target, required)
            if not repair:
                return
        else:
            repair = None
        with tempfile.TemporaryDirectory(prefix=".di-", dir=parent) as temp:
            stage = Path(temp) / "skill"
            stage.mkdir()
            with payload.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                    raise ValueError("SHA256 mismatch: " + str(payload))
                stream.seek(0)
                with zipfile.ZipFile(stream) as archive:
                    for entry in archive.infolist():
                        parts = PurePosixPath(entry.filename).parts
                        if not parts or parts[0] != "traced-template-fill" or len(parts) < 2 or ".." in parts:
                            raise ValueError("Invalid archive member: " + entry.filename)
                        path = stage.joinpath(*parts[1:]).resolve()
                        if not path.is_relative_to(stage):
                            raise ValueError("Archive member escaped the stage")
                        if entry.is_dir():
                            path.mkdir(parents=True, exist_ok=True)
                        else:
                            path.parent.mkdir(parents=True, exist_ok=True)
                            with archive.open(entry) as src, path.open("xb") as dst:
                                shutil.copyfileobj(src, dst)
            python = stage / "runtime/python/python.exe"
            paired = stage / "runtime/stage_skill.py"
            if paired.is_file():
                source = Path(__file__).resolve().parent.parent
                subprocess.run([python, "-I", "-B", paired, source, stage], check=True)
            if missing(stage, required):
                raise ValueError("Incomplete payload")
            env = os.environ.copy()
            for name in ("DI_OCR", "DI_NO_VENDOR"):
                env.pop(name, None)
            subprocess.run([python, "-B", "-X", "utf8", stage / "scripts/di.py", "--help"],
                           check=True, env=env, stdout=subprocess.DEVNULL)
            check_temp = Path(temp) / "selfcheck-temp"
            check_temp.mkdir()
            env.update(TEMP=str(check_temp), TMP=str(check_temp), TMPDIR=str(check_temp))
            subprocess.run([python, "-B", "-X", "utf8", stage / "scripts/offline_check.py"],
                           check=True, env=env, stdout=sys.stderr)
            if repair is None:
                subprocess.run([python, "-I", "-B", stage / "scripts/dilib/runtime_maintenance.py",
                                stage, target, digest], check=True)
                (stage / marker).write_text(digest + "\n", encoding="ascii")
                stage.rename(target)
            else:
                for name in repair:
                    path = target / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(stage / name, path)


if __name__ == "__main__":
    archive_path, destination, checksum, manifest = sys.argv[1:]
    install(Path(archive_path), Path(destination), checksum, Path(manifest).read_text("utf-8").splitlines())
