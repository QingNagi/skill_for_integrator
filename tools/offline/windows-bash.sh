#!/bin/sh
# Claude Code uses a Bash shell on some Windows installations. Keep the project's cwd.
set -eu
kit=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
shell=powershell.exe
if command -v pwsh.exe >/dev/null 2>&1; then
    shell=pwsh.exe
fi
exec "$shell" -NoProfile -File "$(cygpath -w "$kit/run.ps1")" "$@"
