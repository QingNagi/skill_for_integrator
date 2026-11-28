#!/usr/bin/env bash
# Run from the repository root, as an ordinary user in an isolated network namespace.
set -euo pipefail
repo=$(pwd -P)
kit_dist=${2:-"$repo/dist"}
arch=$(uname -m)
archive="traced-template-fill-offline-linux-$arch.tar.gz"
(cd "$kit_dist" && sha256sum --check "$archive.sha256")
[[ $(id -u) != 0 ]] || { echo 'Run acceptance as an ordinary user' >&2; exit 1; }
python3 - <<'PY'
import socket
interfaces = {name for _, name in socket.if_nameindex()}
assert interfaces <= {'lo'}, f'Run inside a fresh network namespace: {interfaces}'
PY

test_dir=$(mktemp -d)
input="$test_dir/只读 输入"
cleanup() {
    if [[ -d $input ]]; then chmod u+w "$input"; fi
    rm -rf -- "$test_dir"
}
trap cleanup EXIT
export HOME="$test_dir/home"
unset DI_CACHE_DIR DI_NO_VENDOR DI_OCR PYTHONPATH PYTHONHOME
mkdir -p "$HOME" "$input"
tar -xzf "$kit_dist/$archive" -C "$test_dir"
kit="$test_dir/traced-template-fill"
entry="$kit/run.sh"
read -r digest < "$kit/payload.sha256"
metrics="$test_dir/samples.tsv"

# Host Python produces synthetic inputs and measures child processes. The skill uses embedded Python.
python3 - "$repo/tests" "$input" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from test_selfcheck import COMMENTS, doc_xml, make_docx
root = Path(sys.argv[2])
heading = '<w:pPr><w:outlineLvl w:val="0"/></w:pPr>'
target = ('<w:p>' + heading + '<w:commentRangeStart w:id="0"/><w:r><w:t>一、交接记录</w:t></w:r>'
          '<w:commentRangeEnd w:id="0"/><w:r><w:commentReference w:id="0"/></w:r></w:p><w:p/>')
source = ('<w:p>' + heading + '<w:r><w:t>1 项目背景</w:t></w:r></w:p>'
          '<w:p><w:r><w:t>验收读数为 17.8 V。</w:t></w:r></w:p>')
make_docx(root / '目标.docx', {'word/document.xml': doc_xml(target),
                             'word/comments.xml': COMMENTS.replace('note', '研制任务书中的项目背景')})
make_docx(root / '研制任务书.docx', {'word/document.xml': doc_xml(source)})
PY
chmod 444 "$input"/*.docx
chmod 555 "$input"

measure() {
    python3 - "$metrics" "$@" <<'PY'
import subprocess, sys, time
from pathlib import Path
metrics, label, log, *command = sys.argv[1:]
with open(log, 'wb') as stream:
    start = time.perf_counter_ns()
    with subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT) as result:
        private_check_seen = False
        while label == "cold_run" and not private_check_seen and result.poll() is None:
            # Inspect only this launcher's descendants. Python may silently fall back to /tmp.
            todo = [result.pid]
            while todo and not private_check_seen:
                pid = todo.pop()
                proc = Path("/proc") / str(pid)
                try:
                    todo.extend(map(int, (proc / "task" / str(pid) / "children").read_text().split()))
                    argv = (proc / "cmdline").read_bytes().split(b"\0")
                    check = next((Path(arg.decode()) for arg in argv if arg.endswith(b"/scripts/offline_check.py")), None)
                    if not check:
                        continue
                    # GNU env briefly includes the full Python argv while it still owns the caller environment.
                    interpreter = check.parents[1] / "runtime/python/bin/python3"
                    if Path(argv[0].decode()).resolve() != interpreter.resolve():
                        continue
                    env = dict(item.split(b"=", 1) for item in (proc / "environ").read_bytes().split(b"\0") if item)
                except (FileNotFoundError, ProcessLookupError):
                    continue
                expected = check.parents[2] / "selfcheck-temp"
                values = [env.get(key) for key in (b"TMPDIR", b"TEMP", b"TMP")]
                assert expected.is_dir() and values == [bytes(expected)] * 3, (check, expected, values)
                private_check_seen = True
            if not private_check_seen:
                time.sleep(0.025)
        result.wait()
        if label == "cold_run" and result.returncode == 0:
            assert private_check_seen, "The offline self-check did not run with a private staging temp"
    elapsed = (time.perf_counter_ns() - start) / 1_000_000
with open(metrics, 'a', encoding='utf-8') as samples:
    print(label, f'{elapsed:.3f}', sep='\t', file=samples)
if result.returncode:
    sys.stderr.buffer.write(Path(log).read_bytes())
    sys.exit(result.returncode)
PY
}

task=(run --draft -t "$input/目标.docx" -s "$input/研制任务书.docx" --task '按批注摘录来源并交付草稿')
check_delivery() {
    python3 - "$1" <<'PY'
from pathlib import Path
from sys import argv
from xml.etree import ElementTree as ET
from zipfile import ZipFile
job = Path(argv[1])
draft, = (job / '草稿').glob('*.docx')
w = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
with ZipFile(draft) as archive:
    assert archive.testzip() is None
    assert '验收读数为 17.8 V。' in [el.text for el in ET.fromstring(archive.read('word/document.xml')).iter(w+'t')]
    assert len(ET.fromstring(archive.read('word/comments.xml')).findall(w+'comment')) == 2
assert 'S1:H0' in (job / '审核报告.md').read_text(encoding='utf-8')
assert any((job / '草稿/源文件快照').glob('*.docx'))
PY
}
for sample in 1 2 3; do
    export XDG_DATA_HOME="$test_dir/测试 用户 $sample"
    project="$test_dir/工作 项目 $sample"
    mkdir -p "$project"
    cd "$project"
    TEMP="$input" TMP="$input" TMPDIR="$input" measure cold_run "$project/cold.log" bash "$entry" "${task[@]}" --name 本次
    grep -q '离线自检通过' cold.log
    check_delivery 整合输出/本次
done
installed="$XDG_DATA_HOME/traced-template-fill/offline/$digest"
job="$project/整合输出/本次"
test -z "$(find "$XDG_DATA_HOME/traced-template-fill/offline" \( -name "selfcheck-temp" -o -name ".stage.*" \) -print -quit)"
test -x "$installed/runtime/python/bin/python3"
test -x "$installed/runtime/ocr/tesseract"
"$installed/runtime/ocr/tesseract" --version > "$project/tesseract-version.log"
grep -q '^tesseract ' "$project/tesseract-version.log"
"$installed/runtime/ocr/tesseract" --list-langs --tessdata-dir "$installed/runtime/tessdata" > "$project/languages.log" 2> "$project/languages.stderr"
if [[ -s "$project/languages.stderr" ]]; then cat "$project/languages.stderr" >&2; exit 1; fi
grep -xq chi_sim "$project/languages.log"
grep -xq eng "$project/languages.log"
test ! -e "$input/整合输出"

# Remove the distribution media. Ready jobs must keep working without rechecking or reinstalling it.
mv "$kit/payload.tar.gz" "$test_dir/隔离载荷"
direct=("$installed/runtime/python/bin/python3" -E -s -B -X utf8 "$installed/scripts/di.py")
for sample in 1 2 3 4 5; do
    measure warm_run "warm-run-$sample.log" bash "$entry" "${task[@]}" --name "复用$sample"
    check_delivery "整合输出/复用$sample"
    # Alternate measurements of the same read-only query to see the warm launcher's overhead.
    if (( sample % 2 )); then
        measure warm_search "search-$sample.log" bash "$entry" search "$job" '验收读数'
        measure direct_search "direct-$sample.log" "${direct[@]}" search "$job" '验收读数'
    else
        measure direct_search "direct-$sample.log" "${direct[@]}" search "$job" '验收读数'
        measure warm_search "search-$sample.log" bash "$entry" search "$job" '验收读数'
    fi
    cmp "search-$sample.log" "direct-$sample.log"
done
if grep -q '离线自检通过' warm-run-*.log search-*.log; then exit 1; fi
ref=$(sed -nE 's/^.*(S1:P[0-9]+).*17\.8 V.*$/\1/p' search-1.log | head -1)
test -n "$ref"
bash "$installed/run.sh" show "$job" "$ref" --full > show.log
grep -Fq '17.8 V' show.log
bash "$entry" "${task[@]}" --out "$test_dir/指定 输出" --name 指定 > explicit.log
test -s "$test_dir/指定 输出/指定/plan.json"
test ! -e "$input/整合输出"
if bash "$entry" unknown-test-command >error.log 2>&1; then exit 1; else test "$?" -eq 2; fi

python3 - "$project" <<'PY'
import json, sys
from pathlib import Path
root = Path(sys.argv[1]) / '整合输出'
Path('batch.json').write_text(json.dumps({'jobs': [str(root / n) for n in ('复用1', '复用2')]}))
PY
bash "$entry" batch draft batch.json --jobs 2 > parallel-drafts.log
python3 - <<'PY'
import json
from pathlib import Path
rows = [line.split(' {', 1) for line in Path('parallel-drafts.log').read_text().splitlines()]
assert [name for name, _ in rows] == ['复用1', '复用2']
for _, body in rows:
    result = json.loads('{' + body)
    assert result['status'] == 'draft_ready' and Path(result['draft']).is_file()
PY

python3 "$repo/qa/check_linux_handoff.py" "$entry" "$input" "$installed"

if command -v soffice >/dev/null || command -v libreoffice >/dev/null || [[ -x /opt/libreoffice/program/soffice ]]; then
    bash "$entry" convert "$input/目标.docx" pdf > convert.log
    pdfs=(整合输出/转换_*/目标.pdf)
    test "${#pdfs[@]}" -eq 1
    test -s "${pdfs[0]}"
    grep -aFq '%PDF-' "${pdfs[0]}"
    mkdir '旧格式'
    bad_temp="$project/不可作临时目录"
    printf 'ordinary file' > "$bad_temp"
    TEMP="$bad_temp" TMP="$bad_temp" TMPDIR="$bad_temp" bash "$entry" convert "$input/研制任务书.docx" doc --out '旧格式/来源.doc'
    TEMP="$bad_temp" TMP="$bad_temp" TMPDIR="$bad_temp" bash "$entry" convert '旧格式/来源.doc' docx --out '旧格式/往返.docx'
    python3 - <<'PY'
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile
assert Path('旧格式/来源.doc').read_bytes()[:8].hex() == 'd0cf11e0a1b11ae1'
with ZipFile('旧格式/往返.docx') as archive:
    assert archive.testzip() is None
    assert '验收读数为 17.8 V。' in ''.join(ET.fromstring(archive.read('word/document.xml')).itertext())
PY
    office_status=converted
else
    if bash "$entry" convert "$input/目标.docx" pdf >convert.log 2>&1; then exit 1; fi
    grep -Fq '转换失败' convert.log
    office_status=unavailable
fi
test ! -e "$input/整合输出"
test ! -e "$input/目标.pdf"

# Missing code must trigger recovery before Python starts, while keeping user rules and profiles.
printf '{"custom":true}\n' > "$installed/config/rules/qa-local.json"
printf '{"profile":"local"}\n' > "$installed/config/profiles/qa-local.json"
mv "$installed/scripts/dilib/office.py" "$test_dir/removed-office.py"
: > "$installed/config/rules/test-data.json"
if bash "$entry" --help > absent.log 2>&1; then exit 1; fi
grep -Fq '本地 payload.tar.gz' absent.log
mv "$test_dir/隔离载荷" "$kit/payload.tar.gz"
bash "$entry" --help >repair1.log 2>&1 & first=$!
bash "$entry" --help >repair2.log 2>&1 & second=$!
wait "$first" || { cat repair1.log; exit 1; }
wait "$second" || { cat repair2.log; exit 1; }
test "$(grep -h '离线自检通过' repair1.log repair2.log | wc -l)" -eq 1
cmp "$test_dir/removed-office.py" "$installed/scripts/dilib/office.py"
test -s "$installed/config/rules/test-data.json"
grep -Fq '"custom":true' "$installed/config/rules/qa-local.json"
grep -Fq '"profile":"local"' "$installed/config/profiles/qa-local.json"

# A fresh user must reject altered bytes and must not publish an installation marker.
bad="$test_dir/损坏套装"
mkdir "$bad"
cp "$entry" "$kit/payload.sha256" "$bad/"
printf 'damaged archive\n' > "$bad/payload.tar.gz"
if XDG_DATA_HOME="$test_dir/bad-home" bash "$bad/run.sh" --help >damaged.log 2>&1; then exit 1; fi
grep -Fq 'SHA256 校验失败' damaged.log
test ! -e "$test_dir/bad-home/traced-template-fill/offline/$digest/.offline-payload.sha256"

python3 - "$metrics" "$arch" "$digest" "$office_status" "${1:-}" <<'PY'
import json, platform, statistics, sys
from pathlib import Path
metrics, arch, digest, office, output = sys.argv[1:]
samples = {}
for line in Path(metrics).read_text(encoding='utf-8').splitlines():
    name, value = line.split('\t')
    samples.setdefault(name, []).append(float(value))
report = {
    'arch': arch, 'glibc': platform.libc_ver(), 'kernel': platform.release(),
    'cold_install_with_read_only_caller_temp': True, 'staging_temp_removed': True,
    'payload_sha256': digest, 'libreoffice': office, 'parallel_batch_drafts': 2, 'clock': 'perf_counter_ns',
    'independent_terminal_handoff': {'jobs': 2, 'same_installed_interpreter_and_entry': True, 'read_only_originals': True, 'cancelled_without_drafts': True,
                                     'piped_input_and_hidden_output_refused': True},
    'cold_condition': 'new application cache per sample; OS disk cache not flushed',
    'timings_ms': {name: {'samples': values, 'median': statistics.median(values)}
                   for name, values in samples.items()},
}
print('LINUX_OFFLINE_RESULT ' + json.dumps(report, ensure_ascii=False))
if output:
    Path(output).write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
PY
