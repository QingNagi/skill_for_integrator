#!/usr/bin/env bash
# Run the two independently delivered artifacts with an empty user cache.
set -euo pipefail
dist=$(cd -- "${1:?Provide the directory containing both deliveries}" && pwd -P)
source_zip=${2:?Provide the skill ZIP filename}
arch=$(uname -m)
runtime="traced-template-fill-runtime-linux-$arch.tar.gz"
(cd "$dist" && sha256sum --check "$source_zip.sha256" "$runtime.sha256")
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
python3 -m zipfile -e "$dist/$source_zip" "$tmp"
tar -xzf "$dist/$runtime" -C "$tmp"
cd "$tmp"
export DI_RUNTIME_DIR="$tmp/versions" DI_CACHE_DIR="$tmp/cache"
skill="$tmp/traced-template-fill"
entry="$skill/run.sh"
read -r digest < "$skill/offline-runtime/payload.sha256"
installed="$DI_RUNTIME_DIR/$digest"
mv "$skill/scripts/di.py" "$tmp/original.py"
printf '%s\n' '# wrong source version' > "$skill/scripts/di.py"
if bash "$entry" --help >negative.out 2>negative.err; then
    echo 'Accepted a mismatched source archive' >&2
    exit 1
fi
grep -Fq 'Skill differs from the approved runtime pair: scripts/di.py' negative.err
test ! -e "$installed"
mv "$tmp/original.py" "$skill/scripts/di.py"
bash "$entry" --help >cold.out 2>cold.err
grep -Fq '第 2 页中英文 OCR' cold.err
grep -qx "$digest" "$installed/.offline-payload.sha256"
mv "$skill/offline-runtime/payload.tar.gz" "$tmp/withheld.tar.gz"
bash "$entry" --help >warm.out 2>warm.err
cmp cold.out warm.out
test ! -s warm.err
printf 'Separate skill/runtime verified on Linux %s (mismatch rejected, bilingual cold start, warm cache).\n' "$arch"
