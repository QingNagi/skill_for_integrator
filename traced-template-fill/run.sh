#!/bin/sh
set -eu
skill=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
if [ -d "$skill/offline-runtime" ]; then
    case "$(uname -s)" in
        MINGW*|MSYS*|CYGWIN*) exec "$skill/offline-runtime/run.cmd" "$@" ;;
        *) exec sh "$skill/offline-runtime/run.sh" "$@" ;;
    esac
fi
if [ -x "$skill/runtime/python/bin/python3" ]; then
    exec "$skill/runtime/python/bin/python3" -E -s -B -X utf8 "$skill/scripts/di.py" "$@"
fi
if [ -x "$skill/runtime/python/python.exe" ]; then
    exec "$skill/runtime/python/python.exe" -B -X utf8 "$skill/scripts/di.py" "$@"
fi
if command -v python3 >/dev/null 2>&1; then exec python3 -B -X utf8 "$skill/scripts/di.py" "$@"; fi
exec python -B -X utf8 "$skill/scripts/di.py" "$@"
