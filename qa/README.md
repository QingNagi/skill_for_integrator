# Windows 离线发布包的 Tier 1 复核

本流程检查交付 ZIP 中实际安装的 `doc-integrator/`。在独立临时目录拆包，并在技能目录旁保存豁免文件、工具版本和报告。评审机需要 PowerShell 7、SkillEvaluator、SkillSpector 和 Gitleaks。以下命令从仓库根目录运行。

## 准备发布件

先核对 ZIP 的发布摘要，再核对内部载荷的摘要。用唯一的新目录做每次评审，可保留新旧结果并复现问题。

```powershell
$package = (Resolve-Path 'dist/doc-integrator-offline-win64.zip').Path
$published = (Get-Content -Raw -LiteralPath "$package.sha256").Trim().Split(' ')[0]
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $package).Hash -ne $published) {
    throw '发布 ZIP 摘要不符'
}
$review = Join-Path ([IO.Path]::GetTempPath()) ('doc-integrator-review-' + [guid]::NewGuid().ToString('N'))
$outer = Join-Path $review 'kit'
$stage = Join-Path $review 'stage'
Expand-Archive -LiteralPath $package -DestinationPath $outer
$wrapper = Join-Path $outer 'doc-integrator'
$payload = Join-Path $wrapper 'payload.zip'
$expected = (Get-Content -Raw -LiteralPath (Join-Path $wrapper 'payload.sha256')).Trim()
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $payload).Hash -ne $expected) {
    throw '内部载荷摘要不符'
}
Expand-Archive -LiteralPath $payload -DestinationPath $stage
$skill = (Resolve-Path (Join-Path $stage 'doc-integrator')).Path
$waivers = Join-Path $stage 'pii-waivers.yaml'
Copy-Item -LiteralPath 'qa/pii-waivers.yaml' -Destination $waivers
```

## 核对许可证与豁免

`qa/pii-waivers.yaml` 的三个文件位置均相对于豁免文件。将它复制到 `stage/`，即可匹配 `stage/doc-integrator/runtime/python/LICENSE.txt`。三个记录分别对应第 359 行 bzip2 署名和第 631、672 行 DFARS 条款编号。每条只匹配相应类别、行号和证据摘要，并在 2027-05-27 到期。升级 CPython 或到期时，应重新核对许可证和新的扫描记录。

在联网评审机从 Python 发布页取得原始 `python-3.14.10-embed-amd64.zip` 并核对 SHA-256。内网评审可使用相同摘要的已归档原件。已核对上游压缩包摘要为 `90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159`；两份许可证字节相同，均为 35,407 字节，SHA-256 为 `935cf13e19f8c31b497d20b05d73623431a226b230c3599bc30fa3348979bc68`。[CPython 3.14.10 官方发布页](https://www.python.org/downloads/release/python-31410/)

```powershell
$upstream = Join-Path $review 'python-3.14.10-embed-amd64.zip'
Invoke-WebRequest 'https://www.python.org/ftp/python/3.14.10/python-3.14.10-embed-amd64.zip' -OutFile $upstream
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $upstream).Hash -ne '90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159') {
    throw '上游归档摘要不符'
}
$cpython = Join-Path $review 'cpython'
Expand-Archive -LiteralPath $upstream -DestinationPath $cpython
$original = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $cpython 'LICENSE.txt')).Hash
$deployed = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $skill 'runtime/python/LICENSE.txt')).Hash
if ($original -ne $deployed) { throw '发布包许可证与上游原件不符' }
```

## 全量 Tier 1 与故障定位

使用当前评审环境提供的可执行文件。若工具没在 PATH 中，替换下方工具路径，并把 Gitleaks 所在目录放进本次 PowerShell 会话的 PATH。`--no-llm` 运行本地静态评估，不向模型提供文档内容。正式命令保留所有默认静态检查。报告存放在扫描目标目录外。

```powershell
$evaluator = (Get-Command skillevaluator -ErrorAction Stop).Source
$env:SKILLEVALUATOR_SKILLSPECTOR_PATH = (Get-Command skillspector -ErrorAction Stop).Source
$gitleaks = (Get-Command gitleaks -ErrorAction Stop).Source
& $evaluator --version
& $env:SKILLEVALUATOR_SKILLSPECTOR_PATH --version
& $gitleaks version
& $evaluator tier1 $skill --no-llm --pii-waivers $waivers -r json -r markdown -o (Join-Path $review 'tier1')
$tier1Exit = $LASTEXITCODE
$tier1 = Get-Content -Raw -LiteralPath (Join-Path $review 'tier1/skillevaluator-tier1.json') | ConvertFrom-Json
$tier1.results | Select-Object validator,status
$tier1.incomplete_scans
$tier1Exit
```

在 PII 结果中检查恰有三条 `applied-pii-waiver`，核对它们的 ID；新增命中、过期或失配必须单独处理。若 SkillSpector 报退出 2，直接保留原始 JSON 与标准错误。PowerShell 7 的输出重定向可保存 JSON；该扫描器的 Windows 安全输出模式拒绝 `--output 文件`。

```powershell
$rawReport = Join-Path $review 'skillspector.json'
& $env:SKILLEVALUATOR_SKILLSPECTOR_PATH scan $skill --format json --no-llm > $rawReport 2> (Join-Path $review 'skillspector.stderr.txt')
$scannerExit = $LASTEXITCODE
$raw = Get-Content -Raw -LiteralPath $rawReport | ConvertFrom-Json
$raw.analysis_completeness | Select-Object status,execution_successful,coverage_percent
$raw.analysis_completeness.ledger_exceptions | Where-Object fatal | Select-Object phase,reason_code,path
$raw.issues | Group-Object severity | Select-Object Name,Count
$scannerExit
```

在 ALDE-2 所测的 2.13.6 中，Windows PyMuPDF wheel 为 19,826,532 字节，超过单文件 16,777,216 字节的扫描缓存上限。报告在同一路径同时登记 `size_limit` 和致命的 `archive_malformed`。扫描器只把缓存前缀送入嵌套归档解析器，前缀缺少 ZIP 末尾的中央目录。完整的固定版本 wheel 通过 CRC 和 SHA-256 校验。应保留原始扫描结果，分别记录未完成范围与实际检出的安全项。完整 wheel 的校验解决来源与完整性问题，不能替代内容安全评估。
