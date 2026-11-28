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

if [ -f "$dist/install.sh" ]; then
    export HOME="$tmp/home"
    project="$tmp/scoped project"
    mkdir -p "$HOME" "$project/.claude/skills/doc-integrator"
    printf '%s\n' '---' 'name: doc-integrator' '---' > "$project/.claude/skills/doc-integrator/SKILL.md"
    printf 'local rule\n' > "$project/.claude/skills/doc-integrator/my-rule"
    # Exercise offline bootstrapping on a machine with no system Python on PATH.
    tools="$tmp/system-tools"
    mkdir "$tools"
    for tool in sh awk dirname uname mktemp cp tar gzip sha256sum mkdir rm env mv chmod flock; do
        ln -s "$(command -v "$tool")" "$tools/$tool"
    done
    PATH="$tools" /bin/sh "$dist/install.sh" --project-root "$project"
    sh "$dist/install.sh" --project-root "$project"
    canon="$project/.agents/skills/traced-template-fill"
    [[ "$canon" -ef "$project/.claude/skills/traced-template-fill" ]]
    [[ "$(find "$project/.traced-template-fill/backups" -name my-rule | wc -l)" -eq 1 ]]
    [[ ! -e "$project/.claude/skills/doc-integrator" ]]
    [[ "$installed" -ef "$project/.traced-template-fill/linux-$arch" ]]
    python3 - "$dist/$source_zip" "$canon" <<'PY'
from pathlib import Path
from sys import argv
from zipfile import ZipFile
root = Path(argv[2])
with ZipFile(argv[1]) as package:
    assert set(package.namelist()) == {p.relative_to(root.parent).as_posix() for p in root.rglob('*') if p.is_file()}
    for name in package.namelist():
        assert (root.parent / name).read_bytes() == package.read(name)
PY
    bash "$canon/run.sh" doctor
    printf 'Installer migration, both host paths, exact reviewed source, external runtime and repeat install passed.\n'
fi
