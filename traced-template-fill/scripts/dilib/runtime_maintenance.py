"""Offline-cache receipts, lifetime locks and conservative removal of obsolete versions."""
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import time
from contextlib import contextmanager, ExitStack
from pathlib import Path

MARKER = ".offline-payload.sha256"
RECEIPT = ".offline-install-v1.json"
ROOT = Path(__file__).absolute().parents[2]
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
VERSION = re.compile(r"(?:[0-9a-f]{24}|[0-9a-f]{64})\Z")


def _linked(st):
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & 0x400)


def _direct(path):
    for part in (path, *path.parents):
        if _linked(part.lstat()):
            raise ValueError("路径包含链接或重解析点")


def _metadata(path):
    info = path.lstat()
    if _linked(info) or not stat.S_ISREG(info.st_mode) or info.st_size > 8192:
        raise ValueError("安装标记无效")
    return path.read_text(encoding="utf-8").strip()


def _receipt(root):
    if not VERSION.fullmatch(root.name):
        raise ValueError("不是私有离线版本目录")
    _direct(root)
    digest = _metadata(root / MARKER)
    if not DIGEST.fullmatch(digest) or root.name not in (digest, digest[:24]):
        raise ValueError("载荷摘要与目录名不一致")
    rec = json.loads(_metadata(root / RECEIPT))
    if (not isinstance(rec, dict) or rec.get("format") != 1 or rec.get("digest") != digest
            or not isinstance(rec.get("installed_ns"), int) or not DIGEST.fullmatch(str(rec.get("tree")))
            or not isinstance(rec.get("target"), str) or Path(rec["target"]).resolve() != root.resolve()):
        raise ValueError("无法验证安装记录及其原始位置")
    return rec


def _mounted_inside(root):
    if not sys.platform.startswith("linux"):
        return False
    # ismount() alone misses bind mounts on the same filesystem.
    mounts = Path("/proc/self/mountinfo").read_text(encoding="utf-8", errors="surrogateescape").splitlines()
    for line in mounts:
        path = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), line.split()[4])
        if Path(path).is_relative_to(root):
            return True
    return False


def _tree_digest(root):
    root = root.resolve(strict=True)
    if _mounted_inside(root):
        raise ValueError("目录内存在独立挂载")
    digest = hashlib.sha256()

    def visit(folder):
        for path in sorted(folder.iterdir(), key=lambda p: p.name):
            if path.parent == root and path.name in (MARKER, RECEIPT):
                continue
            info = path.lstat()
            rel = path.relative_to(root).as_posix().encode("utf-8", "surrogateescape")
            digest.update(rel + b"\0")
            if _linked(info):
                # Packaged Linux Python uses relative symlinks within its own tree.
                if os.name == "nt" or not path.is_symlink():
                    raise ValueError("目录包含重解析点")
                target = os.readlink(path)
                if os.path.isabs(target) or not path.resolve(strict=True).is_relative_to(root):
                    raise ValueError("目录包含越界链接")
                digest.update(b"L" + os.fsencode(target) + b"\0")
            elif stat.S_ISDIR(info.st_mode):
                digest.update(b"D")
                visit(path)
            elif stat.S_ISREG(info.st_mode):
                content = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        content.update(chunk)
                digest.update(b"F" + content.digest())
            else:
                raise ValueError("目录包含特殊文件")

    visit(root)
    return digest.hexdigest()


def _lock_path(root, digest):
    # Linux installers use this very same advisory lock during initial deployment and repair.
    return root.parent / (digest + ".lock")


def record_install(stage, target, digest):
    """Record only a fresh, verified private stage; never baseline a repaired or user-edited tree."""
    stage, target = Path(stage).absolute(), Path(target).absolute()
    if not DIGEST.fullmatch(digest) or target.name not in (digest, digest[:24]) or target.exists():
        raise ValueError("不能为该目标登记新的缓存版本")
    _direct(stage)
    _direct(target.parent)
    if not stage.is_relative_to(target.parent) or stage == target.parent:
        raise ValueError("暂存目录必须位于离线缓存内")
    for required in ("SKILL.md", "scripts/di.py", "rules/learned.json"):
        if not (stage / required).is_file():
            raise ValueError("缺少离线载荷文件 " + required)
    record = {"format": 1, "target": str(target.resolve()), "digest": digest,
              "tree": _tree_digest(stage), "installed_ns": time.time_ns()}
    lock = _lock_path(target, digest)
    if os.path.lexists(lock):
        _direct(lock)
    lock.touch(exist_ok=True)
    with (stage / RECEIPT).open("x", encoding="utf-8") as stream:
        json.dump(record, stream, ensure_ascii=True)


@contextmanager
def _lock(path, exclusive):
    _direct(path)
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("锁文件异常")
    if os.name == "nt":
        import _winapi
        handle = _winapi.CreateFile(str(path), 0x80000000, 0 if exclusive else 3, 0, 3, 0, 0)
        try:
            yield
        finally:
            _winapi.CloseHandle(handle)
    else:
        import fcntl
        with path.open("rb") as stream:
            flags = fcntl.LOCK_EX | fcntl.LOCK_NB if exclusive else fcntl.LOCK_SH
            fcntl.flock(stream.fileno(), flags)
            yield


@contextmanager
def using_runtime(root=ROOT):
    # Source installations and older releases have no managed receipt.
    if not VERSION.fullmatch(root.name) or not (root / RECEIPT).is_file():
        yield
        return
    try:
        rec = _receipt(root)
    except (OSError, ValueError, RuntimeError):
        # A moved or unmanaged installation is never eligible for pruning.
        yield
        return
    with ExitStack() as stack:
        try:
            stack.enter_context(_lock(_lock_path(root, rec["digest"]), exclusive=False))
        except (OSError, ValueError) as exc:
            raise RuntimeUnavailable("离线版本锁暂不可用，请重新运行离线启动器。%s" % exc) from exc
        yield


class RuntimeUnavailable(Exception):
    pass


def _hold_binaries(root, stack):
    # Direct invocations of the bundled Python or OCR program can bypass di.py.
    # Opening an executable image for write (without writing) fails while it is running.
    if os.name == "nt":
        import _winapi
        for rel in ("runtime/python/python.exe", "runtime/ocr/tesseract.exe"):
            handle = _winapi.CreateFile(str(root / rel), 0x40000000, 7, 0, 3, 0, 0)
            stack.callback(_winapi.CloseHandle, handle)
    else:
        for rel in ("runtime/python/bin/python3", "runtime/ocr/tesseract"):
            stack.callback(os.close, os.open(root / rel, os.O_WRONLY))


def prune(current=ROOT, apply=False):
    current = Path(current).absolute()
    result = {"eligible": [], "removed": [], "kept": {}, "note": ""}
    try:
        _receipt(current)
    except (OSError, ValueError, RuntimeError) as exc:
        result["note"] = "不能验证当前私有离线版本，已保留所有目录。请从离线套装启动器运行。%s" % exc
        return result
    cache = current.parent
    records = {}
    for root in sorted(cache.iterdir()):
        if not VERSION.fullmatch(root.name):
            continue
        try:
            records[root] = _receipt(root)
        except (OSError, ValueError, RuntimeError) as exc:
            result["kept"][root.name] = "未验证或旧版安装：" + str(exc)
    previous = max((p for p in records if p != current),
                   key=lambda p: (records[p]["installed_ns"], p.name), default=None)
    for root, record in records.items():
        if root in (current, previous):
            result["kept"][root.name] = "当前版本" if root == current else "最近的其他版本"
            continue
        try:
            with ExitStack() as stack:
                if os.name == "nt":
                    stack.enter_context(_lock(cache / ".traced-template-fill.install.lock", exclusive=True))
                stack.enter_context(_lock(_lock_path(root, record["digest"]), exclusive=True))
                if _receipt(root) != record or _tree_digest(root) != record["tree"]:
                    raise ValueError("内容有修改或额外文件")
                if os.name == "nt":
                    # Windows blocks renaming a directory with open child images.
                    # Release our probes before the rename; an external process
                    # winning that race instead makes the rename fail safely.
                    with ExitStack() as probes:
                        _hold_binaries(root, probes)
                else:
                    _hold_binaries(root, stack)
                if not apply:
                    result["eligible"].append(root.name)
                    continue
                # Claim the verified direct child under the installation lock. A new
                # process can no longer start through its old path during removal.
                tmp = Path(tempfile.mkdtemp(prefix=".prune-", dir=cache))
                try:
                    quarantine = tmp / root.name
                    root.rename(quarantine)
                    try:
                        _direct(quarantine)
                        if (tmp.parent != cache or _metadata(quarantine / MARKER) != record["digest"]
                                or json.loads(_metadata(quarantine / RECEIPT)) != record
                                or _tree_digest(quarantine) != record["tree"]):
                            raise ValueError("清理期间内容已变化")
                    except BaseException:
                        quarantine.rename(root)
                        raise
                    try:
                        shutil.rmtree(quarantine)
                    except OSError as exc:
                        result["note"] += "旧版本的清理未完成，剩余文件位于 %s（%s）。" % (quarantine, exc)
                        continue
                finally:
                    if not any(tmp.iterdir()):
                        tmp.rmdir()
                result["removed"].append(root.name)
        except (OSError, ValueError, RuntimeError) as exc:
            result["kept"][root.name] = "运行中、内容有变或无法安全清理：" + str(exc)
    return result


def configured_cache():
    if os.environ.get("DI_RUNTIME_DIR"):
        return Path(os.environ["DI_RUNTIME_DIR"])
    if os.environ.get("DI_CACHE_DIR"):
        return Path(os.environ["DI_CACHE_DIR"]) / "offline"
    base = os.environ.get("LOCALAPPDATA") if os.name == "nt" else os.environ.get("XDG_DATA_HOME")
    return (Path(base) if base else Path.home() / ".local/share") / "traced-template-fill/offline"


def _mount_is_noexec(path):
    return sys.platform.startswith("linux") and bool(os.statvfs(path).f_flag & os.ST_NOEXEC)


def execution_hint(path):
    path = Path(path).absolute()
    while not path.exists() and path != path.parent:
        path = path.parent
    try:
        blocked = _mount_is_noexec(path)
    except OSError as exc:
        return "无法读取运行时目录的挂载选项：%s" % exc
    if blocked:
        return "运行时目录所在挂载禁止执行（noexec）。请将 DI_RUNTIME_DIR 或 XDG_DATA_HOME 指向允许执行的目录。"
    return ""


if __name__ == "__main__":
    try:
        record_install(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
    except (OSError, ValueError, RuntimeError) as exc:
        # Optional maintenance must not prevent a verified payload from running.
        print("无法生成完整的维护记录，此版本将保留：%s" % exc, file=sys.stderr)

