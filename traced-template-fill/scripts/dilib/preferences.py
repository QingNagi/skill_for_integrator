"""Project and user preferences, separate from the installed skill."""
import os
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


def mappings(skill_dir, job):
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
