#!/bin/sh
# Claude Code uses a Bash shell on some Windows installations. Keep the project's cwd.
set -eu
kit=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
exec powershell.exe -NoProfile -File "$(cygpath -w "$kit/run.ps1")" "$@"
