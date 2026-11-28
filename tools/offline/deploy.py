"""Install a verified release into one explicit host scope. Ship alongside the archives."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import uuid
import zipfile

NAME = "traced-template-fill"
HOSTS = (".agents", ".claude", ".codex", ".openclaw", ".hermes")
WINDOWS = os.name == "nt"


def is_link(path):
    return path.is_symlink() or (os.path.lexists(path) and
                                 bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400))


def checked_folder(root, path):
    """Never traverse a host or backup-directory link into another project."""
    current = root
    for piece in path.relative_to(root).parts:
        current = current / piece
        if is_link(current):
            raise ValueError("Linked parent directory is outside the migration scope: " + str(current))
        current.mkdir(exist_ok=True)


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(data)
    return h.hexdigest()


def snapshot(root, expected=None):
    result = {}
    for folder, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(folder, name)
            key = path.relative_to(root).as_posix()
            if is_link(path) or (expected is not None and key not in expected):
                return None
            if path.is_dir():
                result[key] = None
            elif path.is_file() and (expected is None or expected[key] is not None and
                                     path.stat().st_size == expected[key][0]):
                result[key] = (path.stat().st_size, digest(path))
            else:
                return None
    return result


def link(target, alias, relative=False):
    if WINDOWS:
        import _winapi
        _winapi.CreateJunction(str(target), str(alias))
    else:
        alias.symlink_to(os.path.relpath(target, alias.parent) if relative else target, target_is_directory=True)


@contextmanager
def deployment_lock(state):
    with (state / "install.lock").open("a+b") as lock:
        if WINDOWS:
            import msvcrt
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def extract(archive, dest, expected):
    """Release envelopes contain regular files under exactly one prefix."""
    def copy(name, stream):
        parts = name.split("/")
        if not name.startswith(expected + "/") or any(
            p in ("", ".", "..") or ":" in p or "\\" in p for p in parts
        ):
            raise ValueError("Unexpected release member: " + name)
        path = dest.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as dst:
            shutil.copyfileobj(stream, dst)
        if name.endswith(".sh"):
            path.chmod(0o755)

    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as package:
            for item in package.infolist():
                if item.is_dir() or stat.S_ISLNK(item.external_attr >> 16):
                    raise ValueError("Unexpected non-file ZIP member: " + item.filename)
                with package.open(item) as stream:
                    copy(item.filename, stream)
    else:
        with tarfile.open(archive, "r:gz") as package:
            for item in package:
                if not item.isfile():
                    raise ValueError("Unexpected non-file TAR member: " + item.name)
                with package.extractfile(item) as stream:
                    copy(item.name, stream)


def doctor(folder, env=None):
    command = ([str(Path(os.environ["SystemRoot"]) / "System32/cmd.exe"), "/d", "/v:off", "/c",
                "run.cmd", "doctor"] if WINDOWS else ["sh", "run.sh", "doctor"])
    subprocess.run(command, cwd=folder, env=env, check=True)


def prepare(cache, stage, archive):
    extract(archive, stage, NAME + "/offline-runtime")
    kit = stage / NAME / "offline-runtime"
    payload_id = (kit / "payload.sha256").read_text("ascii").strip()
    if not re.fullmatch("[0-9a-f]{64}", payload_id):
        raise ValueError("Invalid payload digest")
    env = dict(os.environ, DI_RUNTIME_DIR=str(cache))
    doctor(kit, env)
    shutil.rmtree(kit)
    return cache / (payload_id[:24] if WINDOWS else payload_id)


def host_install(root, source, runtime, arch, *, scope_user=False):
    state = root / ".traced-template-fill"
    checked_folder(root, state)
    canonical = root / ".agents/skills" / NAME
    alias = root / ".claude/skills" / NAME
    binding = state / arch
    with deployment_lock(state):
        candidates = []
        for host in HOSTS:
            folder = root / host / "skills"
            if host in (".agents", ".claude") or os.path.lexists(folder):
                checked_folder(root, folder)
                candidates.extend(folder / n for n in (NAME, "doc-integrator"))
        # The unqualified skills/ location is project-local (OpenClaw).
        if not scope_user and (root / "skills").is_dir():
            checked_folder(root, root / "skills")
            candidates.extend(root / "skills" / n for n in (NAME, "doc-integrator"))
        if not scope_user and root != Path.home().resolve():
            for host in HOSTS:
                for n in (NAME, "doc-integrator"):
                    other = Path.home() / host / "skills" / n
                    if os.path.lexists(other):
                        print("User-scope entry (separate scope):", other)
        replace = []
        desired = snapshot(source)
        for candidate in candidates:
            if not os.path.lexists(candidate):
                continue
            if candidate == canonical and candidate.is_dir() and not is_link(candidate) and snapshot(candidate, desired) == desired:
                continue
            if candidate == alias and is_link(alias) and alias.resolve() == canonical:
                continue
            if not is_link(candidate):
                metadata = candidate / "SKILL.md"
                if not metadata.is_file() or not re.search(
                    r"(?m)^name:\s*(?:doc-integrator|traced-template-fill)\s*$",
                    metadata.read_text("utf-8-sig", errors="replace")[:16384]
                ):
                    raise ValueError("Unrecognized entry at reserved skill name; inspect " + str(candidate))
            replace.append(candidate)
        if os.path.lexists(binding):
            if not is_link(binding):
                raise ValueError("Unmanaged runtime binding: " + str(binding))
            if runtime is None or binding.resolve() != runtime:
                replace.append(binding)
        backups = []
        created = []
        backup = state / "backups" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
        try:
            for path in replace:
                dest = backup / path.relative_to(root)
                checked_folder(root, dest.parent)
                path.rename(dest)
                backups.append((path, dest))
                print("Backup:", path, "->", dest, flush=True)
            if not canonical.exists():
                with tempfile.TemporaryDirectory(dir=state) as tmp:
                    staged = Path(tmp) / NAME
                    shutil.copytree(source, staged)
                    staged.rename(canonical)
                created.append(canonical)
            if not os.path.lexists(alias):
                link(canonical, alias, relative=True)
                created.append(alias)
            if runtime and not os.path.lexists(binding):
                link(runtime, binding)
                created.append(binding)
            doctor(alias)
            if snapshot(canonical, desired) != desired or alias.resolve() != canonical:
                raise ValueError("Installed source does not match the release")
        except BaseException:
            for path in reversed(created):
                if is_link(path):
                    path.rmdir() if WINDOWS else path.unlink()
                else:
                    shutil.rmtree(path)
            for path, dest in reversed(backups):
                dest.rename(path)
            raise
    print("Installed:", canonical, "\nClaude entry:", alias)
    if runtime:
        print("Offline runtime:", runtime)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--project-root", type=Path)
    group.add_argument("--user", action="store_true")
    parser.add_argument("--source-only", action="store_true")
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    root = (Path.home() if args.user else args.project_root).resolve(strict=True)
    if not root.is_dir():
        parser.error("Choose an existing directory")
    manifest = args.manifest.resolve(strict=True)
    release = manifest.parent
    expected = {filename: sha for sha, filename in
                (line.split("  ", 1) for line in manifest.read_text("ascii").splitlines())}
    source_name, = [name for name in expected if name.startswith(NAME + "-") and name.endswith("-source.zip")]
    arch = "win64" if WINDOWS else "linux-" + platform.machine()
    archives = [source_name]
    if not args.source_only:
        archives.append(NAME + "-runtime-" + arch + (".zip" if WINDOWS else ".tar.gz"))
    with tempfile.TemporaryDirectory(prefix=".ttf-install-", dir=root) as scratch:
        stage = Path(scratch)
        for name in archives:
            if name != Path(name).name or name not in expected:
                raise ValueError("Missing package in manifest: " + name)
            # Read from the private verified copy throughout extraction.
            path = stage / name
            shutil.copyfile(release / name, path)
            if digest(path) != expected[name]:
                raise ValueError("SHA256 mismatch: " + name)
        extract(stage / source_name, stage, NAME)
        runtime = None
        if not args.source_only:
            default = (Path(os.environ["LOCALAPPDATA"]) if WINDOWS else
                       Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")))
            cache = Path(os.environ.get("DI_RUNTIME_DIR") or default / NAME / "offline").resolve()
            protected = [root / host / "skills" for host in HOSTS] + [root / "skills"]
            if any(cache.is_relative_to(path.resolve()) for path in protected):
                raise ValueError("Choose a runtime location outside host skills directories")
            runtime = prepare(cache, stage, stage / archives[1])
        host_install(root, stage / NAME, runtime, arch, scope_user=args.user)


if __name__ == "__main__":
    main()
