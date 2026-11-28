#!/bin/sh
set -eu
skill=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
if [ -d "$skill/offline-runtime" ]; then
    case "$(uname -s)" in
        MINGW*|MSYS*|CYGWIN*) exec "$skill/offline-runtime/run.cmd" "$@" ;;
        *) exec sh "$skill/offline-runtime/run.sh" "$@" ;;
    esac
fi
case "$skill" in
    */.agents/skills/traced-template-fill|*/.claude/skills/traced-template-fill)
        registry="$skill/../../../.traced-template-fill"
        case "$(uname -s)" in
            Linux) binding="$registry/linux-$(uname -m)"
                if [ -L "$binding" ]; then
                    exec "$binding/runtime/python/bin/python3" -E -s -B -X utf8 "$binding/scripts/di.py" "$@"
                fi ;;
            MINGW*|MSYS*|CYGWIN*) exec "$skill/run.cmd" "$@" ;;
        esac ;;
esac
if [ -x "$skill/runtime/python/bin/python3" ]; then
    exec "$skill/runtime/python/bin/python3" -E -s -B -X utf8 "$skill/scripts/di.py" "$@"
fi
if [ -x "$skill/runtime/python/python.exe" ]; then
    exec "$skill/runtime/python/python.exe" -B -X utf8 "$skill/scripts/di.py" "$@"
fi
if command -v python3 >/dev/null 2>&1; then exec python3 -B -X utf8 "$skill/scripts/di.py" "$@"; fi
exec python -B -X utf8 "$skill/scripts/di.py" "$@"
