#!/bin/sh
# Launch the GNU/Linux offline kit from any working directory.
set -eu
umask 077

fail() {
    printf '%s\n' "doc-integrator: $*" >&2
    exit 1
}

kit=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
expected_arch='@ARCH@'
[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = "$expected_arch" ] ||
    fail "需要 Linux $expected_arch。请选择与本机架构匹配的离线包。"
digest=$(cat -- "$kit/payload.sha256")
case "$digest" in ''|*[!0123456789abcdef]*) fail 'payload.sha256 格式无效' ;; esac
[ "${#digest}" = 64 ] || fail 'payload.sha256 长度无效'

ready() {
    [ -f "$1/.offline-payload.sha256" ] && [ "$(cat -- "$1/.offline-payload.sha256")" = "$digest" ] &&
        [ -x "$1/runtime/python/bin/python3" ] && [ -s "$1/scripts/di.py" ] &&
        [ -s "$1/scripts/dilib/cli.py" ] && [ -s "$1/vendor/wheels.lock.json" ] &&
        [ -s "$1/runtime/tessdata/chi_sim.traineddata" ] && [ -s "$1/runtime/tessdata/eng.traineddata" ] &&
        [ -s "$1/vendor/wheels/manylinux_$expected_arch/@WHEEL@" ] &&
        [ -s "$1/vendor/py/@PYPDF@" ] && [ -s "$1/vendor/py/@TYPING@" ]
}

if ready "$kit"; then
    installed=$kit
else
    data_home=${XDG_DATA_HOME:-"${HOME:?Set HOME or XDG_DATA_HOME}/.local/share"}
    case "$data_home" in /*) ;; *) fail 'XDG_DATA_HOME 或 HOME 必须为绝对路径' ;; esac
    cache="$data_home/doc-integrator/offline"
    installed="$cache/$digest"
    if ! ready "$installed"; then
        mkdir -p -- "$cache"
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
                "$stage/skill/scripts/offline_check.py" >&2 || fail '离线运行时自检失败'
            printf '%s\n' "$digest" > "$stage/skill/.offline-payload.sha256"
            if [ -e "$installed" ]; then
                backup=$(mktemp -d -- "$cache/.previous.XXXXXXXX")
                # Keep custom rules and retain the prior version if local files were damaged.
                for custom in profiles rules; do
                    if [ -d "$installed/$custom" ]; then
                        mkdir -p -- "$stage/skill/$custom"
                        cp -a -- "$installed/$custom/." "$stage/skill/$custom/"
                    fi
                done
                mv -- "$installed" "$backup/skill"
                if ! mv -- "$stage/skill" "$installed"; then
                    mv -- "$backup/skill" "$installed"
                    fail '无法启用已校验的运行时'
                fi
            else
                mv -- "$stage/skill" "$installed"
            fi
        ) 9>"$cache/$digest.lock"
    fi
fi
export DI_CACHE_DIR=${DI_CACHE_DIR:-"$installed/vendor/_unpacked"}
exec "$installed/runtime/python/bin/python3" -E -s -B -X utf8 "$installed/scripts/di.py" "$@"
