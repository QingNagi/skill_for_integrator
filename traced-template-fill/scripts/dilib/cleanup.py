"""List or remove derived parse-cache files without touching jobs or snapshots."""
import os
import re
import stat
from pathlib import Path

from .common import DIError, out


def physical(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise DIError("缓存路径中存在链接，请检查该路径：%s" % path)
    return info


def cmd_clean(args):
    root = Path(args.out).absolute()
    cache = root / "_缓存"
    if not os.path.lexists(cache):
        out("没有解析缓存：%s" % cache)
        return
    for folder in reversed((root, *root.parents)):
        physical(folder)
    physical(cache)
    key = r"[0-9a-f]{16}(?:-[0-9a-f]{16})?"
    files = []
    for name in ("models", "media", "conv"):
        directory = cache / name
        if not os.path.lexists(directory):
            continue
        physical(directory)
        for item in directory.iterdir():
            mode = physical(item).st_mode
            if name == "media" and stat.S_ISDIR(mode) and re.fullmatch(key, item.name):
                for parent, dirs, members in os.walk(item):
                    for child in dirs:
                        physical(Path(parent) / child)
                    for child in members:
                        path = Path(parent) / child
                        if stat.S_ISREG(physical(path).st_mode):
                            files.append(path)
            elif stat.S_ISREG(mode):
                if ((name == "models" and re.fullmatch(key + r"\.json", item.name)) or
                        (name == "conv" and re.fullmatch(r".+_[0-9a-f]{10}\.(?:docx|xlsx)", item.name))):
                    files.append(item)
    size = sum(file.stat().st_size for file in files)
    if args.apply:
        for file in files:
            file.unlink()
        out("已清理 %d 个缓存文件，共 %d 字节。目录：%s" % (len(files), size, cache))
    else:
        out("可以清理 %d 个缓存文件，共 %d 字节。目录：%s。确认后加 --apply。" % (len(files), size, cache))
