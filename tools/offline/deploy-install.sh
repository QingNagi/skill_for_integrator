#!/bin/sh
set -eu
umask 077
release=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
manifest="$release/SHA256SUMS-@TAG@.txt"
helper="$release/deploy.py"
verify() {
    expected=$(awk -v name="$1" '$2 == name {print $1}' "$manifest")
    [ -n "$expected" ] && (cd -- "$release" && printf '%s  %s\n' "$expected" "$1" | sha256sum -c --status)
}
verify deploy.py || { echo 'deploy.py SHA-256 mismatch' >&2; exit 1; }
if command -v python3 >/dev/null 2>&1 && python3 -I -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    exec python3 -I -B "$helper" --manifest "$manifest" "$@"
fi
case " $* " in *" --source-only "*) echo 'Source-only installation requires Python 3.10+' >&2; exit 1 ;; esac
archive="traced-template-fill-runtime-linux-$(uname -m).tar.gz"
cache=${DI_RUNTIME_DIR:-"${XDG_DATA_HOME:-"${HOME:?Set HOME or DI_RUNTIME_DIR}/.local/share"}/traced-template-fill/offline"}
mkdir -p -- "$cache"
cache=$(CDPATH= cd -- "$cache" && pwd -P)
scratch=$(mktemp -d -- "$cache/.ttf-bootstrap.XXXXXXXX")
trap 'rm -rf -- "$scratch"' EXIT
cp -- "$release/$archive" "$scratch/$archive"
expected=$(awk -v name="$archive" '$2 == name {print $1}' "$manifest")
(cd "$scratch" && printf '%s  %s\n' "$expected" "$archive" | sha256sum -c --status)
tar -xzf "$scratch/$archive" -C "$scratch" traced-template-fill/offline-runtime/payload.tar.gz
tar -xzf "$scratch/traced-template-fill/offline-runtime/payload.tar.gz" -C "$scratch" runtime/python
"$scratch/runtime/python/bin/python3" -I -B "$helper" --manifest "$manifest" "$@"
