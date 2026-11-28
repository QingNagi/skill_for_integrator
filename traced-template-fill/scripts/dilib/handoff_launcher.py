"""Create a reviewer shortcut to the exact installed interpreter that sealed this batch."""
import os
from pathlib import Path
import shlex
import sys

from .common import DIError


def launcher_text(interpreter, entry, windows):
    for value in (interpreter, entry):
        if any(ord(ch) < 32 for ch in value) or (windows and '"' in value):
            raise DIError("运行时路径含无法写入交接启动脚本的字符。")
    if windows:
        # Percent expansion also occurs inside cmd.exe quotes. Disable delayed expansion
        # for literal ! in user profile and installation paths. No CALL or second parsing.
        python, script = (s.replace("%", "%%") for s in (interpreter, entry))
        return "\r\n".join(("@echo off", "setlocal EnableExtensions DisableDelayedExpansion", "chcp 65001 >nul",
                            f'"{python}" -E -s -B -X utf8 "{script}" deliver "%~dp0handoff.json"',
                            'set "result=%errorlevel%"', "pause", "exit /b %result%", ""))
    return ("#!/bin/sh\nset -eu\n"
            'folder=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)\n'
            f'exec {shlex.quote(interpreter)} -E -s -B -X utf8 {shlex.quote(entry)} deliver "$folder/handoff.json"\n')


def write_launchers(sealed_manifest):
    manifest = Path(sealed_manifest).resolve()
    if manifest.name != "handoff.json":
        raise DIError("请从本批专属的 handoff.json 封存文件生成启动脚本。")
    windows = os.name == "nt"
    platform = "windows" if windows else "linux"
    launcher = manifest.with_name("出稿.cmd" if windows else "出稿.sh")
    entry = Path(__file__).resolve().parents[1] / "di.py"
    body = launcher_text(str(Path(sys.executable).absolute()), str(entry), windows).encode("utf-8")
    try:
        fd = os.open(launcher, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o755)
    except FileExistsError:
        if launcher.is_symlink() or not launcher.is_file() or launcher.read_bytes() != body:
            raise DIError("已有交接脚本内容不同，已保留。请在独立终端从正式技能入口执行 deliver 并指定封存清单。")
    else:
        with os.fdopen(fd, "wb") as output:
            output.write(body)
    return {"preferred": str(launcher), platform: str(launcher)}
