"""Project and user preferences, separate from the installed skill."""
import os
import re
from contextlib import contextmanager
from pathlib import Path

from .common import DIError, read_json, write_json


def project_dir(job=None):
    if job is None:
        return Path.cwd() / ".doc-integrator"
    # Older jobs did not record their working directory. Anchor them to their
    # original output location, even when reopened from another shell.
    return Path(job.data.get("workspace") or Path(job.dir).parent.parent) / ".doc-integrator"


def user_dir():
    override = os.environ.get("DI_USER_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    base = os.environ.get("APPDATA" if os.name == "nt" else "XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / "traced-template-fill"


def migrate_installed_rules(skill_dir):
    current = Path(skill_dir).resolve()
    if not (current / ".offline-payload.sha256").is_file() or not re.fullmatch(r"[0-9a-f]{24}|[0-9a-f]{64}", current.name):
        return
    folder = user_dir()
    destination = folder / "learned.json"
    if current.name in read_json(destination, {}).get("migrated_installs", []):
        return
    legacy = []
    for install in current.parent.iterdir():
        marker = install / ".offline-payload.sha256"
        learned = install / "rules" / "learned.json"
        if (install.resolve() != install or not re.fullmatch(r"[0-9a-f]{24}|[0-9a-f]{64}", install.name)
                or not marker.is_file() or not learned.is_file() or learned.resolve() != learned):
            continue
        if marker.read_text(encoding="ascii").strip().lower().startswith(install.name):
            legacy.append(learned)
    with locked_preferences(folder):
        records = read_json(destination, {})
        if current.name in records.get("migrated_installs", []):
            return
        answers = records.setdefault("answers", {})
        for path in sorted(legacy, key=lambda p: p.stat().st_mtime, reverse=True):
            for key, value in read_json(path, {}).get("answers", {}).items():
                answers.setdefault(key, value)
        records.setdefault("migrated_installs", []).append(current.name)
        write_json(destination, records)


def mappings(skill_dir, job):
    migrate_installed_rules(skill_dir)
    answers = {}
    for folder in (Path(skill_dir) / "rules", user_dir(), project_dir(job)):
        answers.update((read_json(folder / "learned.json", {}) or {}).get("answers", {}))
    return answers


@contextmanager
def locked_preferences(folder):
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "learned.lock").open("a+b") as lock:
        if os.name == "nt":
            import msvcrt
            # Windows byte-range locks can extend beyond end-of-file.
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        yield


def remember(job, updates):
    if updates:
        folder = project_dir(job)
        path = folder / "learned.json"
        with locked_preferences(folder):
            records = read_json(path, {}) or {}
            records.setdefault("answers", {}).update(updates)
            write_json(path, records)
        return path


def profile_name(name):
    if not name.strip() or name in (".", "..") or any(c in name for c in '/\\:'):
        raise DIError("排版规范名称只能使用文件名，例如 公文。")
    return name if name.endswith(".json") else name + ".json"
