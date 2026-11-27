#!/usr/bin/env bash
# Run from the repository root in a fresh Linux network namespace.
set -euo pipefail
dist="$(pwd)/dist"
archive="doc-integrator-offline-linux-$(uname -m).tar.gz"
(cd "$dist" && sha256sum -c "$archive.sha256")
test_dir=$(mktemp -d)
trap 'rm -rf -- "$test_dir"' EXIT
export HOME="$test_dir/home" XDG_DATA_HOME="$test_dir/测试 用户"
mkdir -p "$HOME" "$test_dir/项目" "$XDG_DATA_HOME"
tar -xzf "$dist/$archive" -C "$test_dir"
entry="$test_dir/doc-integrator/run.sh"
cd "$test_dir/项目"
# Host Python only produces the synthetic input. The kit must use its own interpreter.
python3 - <<'PY'
from zipfile import ZipFile
with ZipFile("目标.docx", "w") as z:
    z.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
    z.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
    z.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>交接记录</w:t></w:r></w:p><w:sectPr/></w:body></w:document>')
PY
printf '验收读数为 17.8 V。\n' > 测量.txt
bash "$entry" run -t 目标.docx -s 测量.txt --out 核对 --name 本次 --task '核对原始读数' 2>首次.log
cat 首次.log
grep -q '离线自检通过' 首次.log
test -s 核对/本次/plan.json
mv "$test_dir/doc-integrator/payload.tar.gz" "$test_dir/隔离载荷"
bash "$entry" show 核对/本次 S1:P1 --full | tee 回读.log
grep -Fq '17.8 V' 回读.log
echo "First use, bilingual OCR and warm reuse passed on $(uname -m)"
