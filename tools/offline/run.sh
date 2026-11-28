#!/bin/sh
# Launch the GNU/Linux offline kit from any working directory.
set -eu
umask 077

fail() {
    printf '%s\n' "traced-template-fill: $*" >&2
    exit 1
}

check_exec_mount() {
    if command -v findmnt >/dev/null 2>&1; then
        mount_options=$(findmnt -n -o VFS-OPTIONS -T "$1" 2>/dev/null) || return 0
        case ",$mount_options," in
            *,noexec,*) fail '运行时目录所在挂载为 noexec。请将 DI_RUNTIME_DIR 或 XDG_DATA_HOME 指向允许执行的目录。' ;;
        esac
    fi
}

kit=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
expected_arch='@ARCH@'
[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = "$expected_arch" ] ||
    fail "需要 Linux $expected_arch。请选择与本机架构匹配的离线包。"
# Read one checksum without spawning cat on every warm invocation.
read_checksum() {
    [ -f "$1" ] && [ -r "$1" ] || return 1
    checksum=
    {
        IFS= read -r checksum || [ -n "$checksum" ] || return 1
        while IFS= read -r extra || [ -n "$extra" ]; do
            [ -z "$extra" ] || return 1
        done
    } < "$1"
}
read_checksum "$kit/payload.sha256" || fail '无法读取有效的 payload.sha256'
digest=$checksum
case "$digest" in ''|*[!0123456789abcdef]*) fail 'payload.sha256 格式无效' ;; esac
[ "${#digest}" = 64 ] || fail 'payload.sha256 长度无效'

ready() {
    read_checksum "$1/.offline-payload.sha256" && [ "$checksum" = "$digest" ] &&
        [ -x "$1/runtime/python/bin/python3" ] && [ -s "$1/runtime/python/lib/libpython@PYMINOR@.so.1.0" ] &&
        [ -s "$1/runtime/python/lib/python@PYMINOR@/encodings/__init__.py" ] && [ -s "$1/vendor/wheels.lock.json" ] &&
        [ -s "$1/rules/test-data.json" ] && [ -s "$1/assets/offline-ocr.png" ] && [ -s "$1/runtime/tessdata/chi_sim.traineddata" ] && [ -s "$1/runtime/tessdata/eng.traineddata" ] &&
        [ -x "$1/runtime/ocr/tesseract" ] && [ -s "$1/vendor/ocr.lock.json" ] || return 1
    for script in @REQUIRED_FILES@; do
        [ -s "$1/$script" ] || return 1
    done
    return 0
}

if ready "$kit"; then
    installed=$kit
else
    if [ -n "${DI_RUNTIME_DIR:-}" ]; then
        cache="$DI_RUNTIME_DIR"
    elif [ -n "${DI_CACHE_DIR:-}" ]; then
        cache="$DI_CACHE_DIR/offline"
    else
        data_home=${XDG_DATA_HOME:-"${HOME:?Set HOME, XDG_DATA_HOME or DI_CACHE_DIR}/.local/share"}
        case "$data_home" in /*) ;; *) fail 'XDG_DATA_HOME 或 HOME 必须为绝对路径' ;; esac
        cache="$data_home/traced-template-fill/offline"
    fi
    case "$cache" in /*) ;; *) cache="$(pwd -P)/$cache" ;; esac
    installed="$cache/$digest"
    if ! ready "$installed"; then
        mkdir -p -- "$cache"
        check_exec_mount "$cache"
        # A stopped process releases flock automatically. Recheck after waiting.
        (
            flock -x 9 || fail '首次部署需要 util-linux 的 flock'
            if ready "$installed"; then exit 0; fi
            [ -f "$kit/payload.tar.gz" ] || fail '找不到已就绪环境或本地 payload.tar.gz'
            stage=$(mktemp -d -- "$cache/.stage.XXXXXXXX")
            trap 'rm -rf -- "$stage"' EXIT
            trap 'exit 1' HUP INT TERM
            # Verify a private copy so extraction uses precisely the checked bytes.
            cp -- "$kit/payload.tar.gz" "$stage/payload.tar.gz"
            (cd -- "$stage" && printf '%s  payload.tar.gz\n' "$digest" | sha256sum --check --status) ||
                fail '本地载荷 SHA256 校验失败'
            mkdir -- "$stage/skill"
            tar --extract --gzip --no-same-owner --file="$stage/payload.tar.gz" --directory="$stage/skill"
            cp -- "$kit/run.sh" "$kit/payload.sha256" "$stage/skill/"
            chmod 755 "$stage/skill/run.sh"
            # The acceptance test exercises the bundled configuration independent of
            # user overrides. The user's environment applies to their actual command.
            env -u DI_OCR -u DI_NO_VENDOR DI_CACHE_DIR="$stage/skill/vendor/_unpacked" \
                "$stage/skill/runtime/python/bin/python3" -E -s -B -X utf8 \
                "$stage/skill/scripts/offline_check.py" >&2 || fail '离线运行时自检失败。若挂载为 noexec，请将 DI_RUNTIME_DIR 或 XDG_DATA_HOME 指向允许执行的目录。'
            printf '%s\n' "$digest" > "$stage/skill/.offline-payload.sha256"
            if [ -e "$installed" ]; then
                backup=$(mktemp -d -- "$cache/.previous.XXXXXXXX")
                # Keep custom rules and retain the prior version if local files were damaged.
                cp -- "$stage/skill/rules/test-data.json" "$stage/default-rules.json"
                for custom in profiles rules; do
                    if [ -d "$installed/$custom" ]; then
                        mkdir -p -- "$stage/skill/$custom"
                        cp -a -- "$installed/$custom/." "$stage/skill/$custom/"
                    fi
                done
                if ! "$stage/skill/runtime/python/bin/python3" -I -B -c \
                    'import json, sys; sys.exit(not isinstance(json.load(open(sys.argv[1], encoding="utf-8")), dict))' \
                    "$stage/skill/rules/test-data.json"; then
                    cp -- "$stage/default-rules.json" "$stage/skill/rules/test-data.json"
                fi
                mv -- "$installed" "$backup/skill"
                if ! mv -- "$stage/skill" "$installed"; then
                    mv -- "$backup/skill" "$installed"
                    fail '无法启用已校验的运行时'
                fi
            else
                "$stage/skill/runtime/python/bin/python3" -I -B "$stage/skill/scripts/dilib/runtime_maintenance.py" \
                    "$stage/skill" "$installed" "$digest" || fail '无法登记私有运行时的初始文件状态'
                mv -- "$stage/skill" "$installed"
            fi
        ) 9>"$cache/$digest.lock"
    fi
fi
export DI_CACHE_DIR=${DI_CACHE_DIR:-"$installed/vendor/_unpacked"}
exec "$installed/runtime/python/bin/python3" -E -s -B -X utf8 "$installed/scripts/di.py" "$@"
