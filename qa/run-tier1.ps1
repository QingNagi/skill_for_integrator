#Requires -Version 7.2
[CmdletBinding(DefaultParameterSetName='Package')]
param(
    [Parameter(Mandatory, ParameterSetName='Package')][string]$Package,
    [Parameter(ParameterSetName='Package')][string]$PackageSha256,
    [Parameter(Mandatory, ParameterSetName='Installed')][string]$InstalledDirectory,
    [string]$OutputDirectory = (Join-Path ([IO.Path]::GetTempPath()) 'doc-integrator-reviews'),
    [string]$Evaluator = 'skillevaluator',
    [string]$SkillSpector = $(if ($env:SKILLEVALUATOR_SKILLSPECTOR_PATH) { $env:SKILLEVALUATOR_SKILLSPECTOR_PATH } else { 'skillspector' }),
    [string]$Gitleaks = 'gitleaks',
    [switch]$ScannerDiagnostics
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$originalScanner = $env:SKILLEVALUATOR_SKILLSPECTOR_PATH
$originalPath = $env:PATH
$expectedLicense = '935cf13e19f8c31b497d20b05d73623431a226b230c3599bc30fa3348979bc68'
$expectedWaiver = '8b6c47fc52d7400d9b9c67b7b011998eb99f6c4f27d73f449206c48c430e9e2c'
$expectedIds = @('CPYTHON-31410-LICENSE-BZIP2','CPYTHON-31410-LICENSE-DFARS-7013','CPYTHON-31410-LICENSE-DFARS-7014')
$evidence = [ordered]@{ status = 'preflight'; started_utc = [datetime]::UtcNow.ToString('o') }
$exitCode = 2
$review = $null

function Digest([string]$Path) {
    (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function ExpandVerifiedZip([string]$Path, [string]$Expected, [string]$Destination) {
    if ($Expected -notmatch '^[a-fA-F0-9]{64}$') { throw "Invalid expected SHA-256 for $Path" }
    $stream = [IO.File]::OpenRead($Path)
    try {
        $actual = (Get-FileHash -InputStream $stream -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $Expected) { throw "SHA-256 mismatch for $Path (expected $Expected, got $actual)" }
        $stream.Position = 0
        $archive = [IO.Compression.ZipArchive]::new($stream, [IO.Compression.ZipArchiveMode]::Read, $true)
        try { [IO.Compression.ZipFileExtensions]::ExtractToDirectory($archive, $Destination) }
        finally { $archive.Dispose() }
        $actual
    } finally { $stream.Dispose() }
}

function PhysicalDirectory([IO.DirectoryInfo]$Entry) {
    if (!$Entry.Parent) { return $Entry.FullName }
    $path = Join-Path (PhysicalDirectory $Entry.Parent) $Entry.Name
    $current = [IO.DirectoryInfo]::new($path)
    if ($current.LinkTarget) { return $current.ResolveLinkTarget($true).FullName }
    $path
}

function Snapshot([string]$Source, [string]$Destination) {
    [IO.Directory]::CreateDirectory($Destination) | Out-Null
    foreach ($entry in Get-ChildItem -LiteralPath $Source -Force) {
        if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Snapshot contains a link. Provide its physical deployed tree or the release ZIP: $($entry.FullName)"
        }
        $dest = Join-Path $Destination $entry.Name
        if ($entry.PSIsContainer) { Snapshot $entry.FullName $dest }
        else { [IO.File]::Copy($entry.FullName, $dest) }
    }
}

function ExpandKit([string]$Wrapper, [string]$Stage) {
    $payload = Join-Path $Wrapper 'payload.zip'
    $expected = [IO.File]::ReadAllText((Join-Path $Wrapper 'payload.sha256')).Trim()
    $evidence.payload_sha256 = ExpandVerifiedZip $payload $expected $Stage
    $children = @(Get-ChildItem -LiteralPath $Stage -Force)
    if ($children.Count -ne 1 -or $children[0].Name -cne 'doc-integrator' -or !$children[0].PSIsContainer) {
        throw 'The payload must contain one doc-integrator/ directory.'
    }
    # Also scan any wrapper files whose bytes differ from the installed equivalents.
    $mapping = @{ 'run.ps1'='tools/offline/run.ps1'; 'run.sh'='tools/offline/windows-bash.sh'; 'install.ps1'='tools/offline/install.ps1'; 'README.md'='tools/offline/README.md' }
    $skill = $children[0].FullName
    $additional = Join-Path $skill 'release-wrapper'
    if (Test-Path -LiteralPath $additional) { throw "Reserved review path exists: $additional" }
    $copied = @(foreach ($item in Get-ChildItem -LiteralPath $Wrapper -File -Recurse -Force) {
        $relative = [IO.Path]::GetRelativePath($Wrapper, $item.FullName).Replace('\','/')
        if ($relative -in 'payload.zip','payload.sha256') { continue }
        $mapped = if ($mapping.ContainsKey($relative)) { $mapping[$relative] } else { $relative }
        $deployed = Join-Path $skill $mapped
        if ([IO.File]::Exists($deployed) -and (Digest $item.FullName) -eq (Digest $deployed)) { continue }
        $dest = Join-Path $additional $relative
        [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($dest)) | Out-Null
        [IO.File]::Copy($item.FullName,$dest)
        $relative
    })
    $evidence.additional_wrapper_files = $copied
}

try {
    $source = if ($PSCmdlet.ParameterSetName -eq 'Package') { $Package } else { $InstalledDirectory }
    $source = (Resolve-Path -LiteralPath $source).ProviderPath
    $output = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputDirectory)
    if ($PSCmdlet.ParameterSetName -eq 'Installed') {
        $source = PhysicalDirectory ([IO.DirectoryInfo]::new($source))
        $output = PhysicalDirectory ([IO.DirectoryInfo]::new($output))
        $relativeOutput = [IO.Path]::GetRelativePath($source, $output)
        $parentPrefix = '..' + [IO.Path]::DirectorySeparatorChar
        if ($relativeOutput -eq '.' -or (!$relativeOutput.StartsWith($parentPrefix) -and $relativeOutput -ne '..' -and ![IO.Path]::IsPathRooted($relativeOutput))) {
            throw 'OutputDirectory must be outside the deployed skill.'
        }
    }
    $review = Join-Path $output ((Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,10))
    [IO.Directory]::CreateDirectory($review) | Out-Null
    Write-Host "Review directory: $review"
    $stage = Join-Path $review 'stage'
    $skill = Join-Path $stage 'doc-integrator'
    $evidence.input = $source
    $evidence.target = $skill
    if ($PSCmdlet.ParameterSetName -eq 'Package') {
        if (!$PackageSha256) { $PackageSha256 = ([IO.File]::ReadAllText("$source.sha256").Trim() -split '\s+')[0] }
        $outer = Join-Path $review 'kit'
        $evidence.package_sha256 = ExpandVerifiedZip $source $PackageSha256 $outer
        $entries = @(Get-ChildItem -LiteralPath $outer -Force)
        if ($entries.Count -ne 1 -or $entries[0].Name -cne 'doc-integrator' -or !$entries[0].PSIsContainer) {
            throw 'The release must contain one doc-integrator/ directory.'
        }
        ExpandKit $entries[0].FullName $stage
    } elseif ([IO.File]::Exists((Join-Path $source 'payload.zip'))) {
        $wrapper = Join-Path $review 'kit/doc-integrator'
        Snapshot $source $wrapper
        ExpandKit $wrapper $stage
    } else {
        if (![IO.File]::Exists((Join-Path $source 'SKILL.md'))) { throw 'Select the deployed skill directory containing SKILL.md.' }
        Snapshot $source $skill
    }
    if (![IO.File]::Exists((Join-Path $skill 'SKILL.md'))) { throw 'The staged skill has no SKILL.md.' }

    $Evaluator = (Get-Command $Evaluator -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    $SkillSpector = (Get-Command $SkillSpector -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    $Gitleaks = (Get-Command $Gitleaks -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    $env:SKILLEVALUATOR_SKILLSPECTOR_PATH = $SkillSpector
    $env:PATH = ([IO.Path]::GetDirectoryName($Gitleaks)) + [IO.Path]::PathSeparator + $originalPath
    $evidence.tools = [ordered]@{}
    foreach ($tool in @(@($Evaluator,'--version'),@($SkillSpector,'--version'),@($Gitleaks,'version'))) {
        $version = (& $tool[0] $tool[1] 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) { throw "Unable to identify tool $($tool[0]): $version" }
        $evidence.tools[$tool[0]] = $version
    }

    $license = Join-Path $skill 'runtime/python/LICENSE.txt'
    $waiver = Join-Path $stage 'pii-waivers.yaml'
    $waiverSource = Join-Path $PSScriptRoot 'pii-waivers.yaml'
    $evidence.waiver_sha256_lf = $null
    if ([IO.File]::Exists($waiverSource)) {
        [IO.File]::Copy($waiverSource,$waiver)
        $normalized = [IO.File]::ReadAllText($waiver).Replace("`r`n","`n")
        $evidence.waiver_sha256_lf = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($normalized))).ToLowerInvariant()
    }
    $evidence.license_sha256 = if ([IO.File]::Exists($license)) { Digest $license } else { $null }
    $today = [datetime]::UtcNow.Date
    $active = $today -ge [datetime]'2026-11-27' -and $today -le [datetime]'2027-05-27'
    $useWaivers = $evidence.waiver_sha256_lf -eq $expectedWaiver -and $evidence.license_sha256 -eq $expectedLicense -and $active
    $evidence.waivers_enabled = $useWaivers
    $arguments = @('tier1',$skill,'--no-llm','-r','json','-r','markdown','-o',$review)
    if ($useWaivers) { $arguments += @('--pii-waivers',$waiver) }
    else { Write-Warning 'Waiver evidence or validity date mismatch. Running full Tier 1 with no PII waivers.' }

    & $Evaluator @arguments *> (Join-Path $review 'tier1.log')
    $tierExit = $LASTEXITCODE
    $evidence.evaluator_exit = $tierExit
    $exitCode = if ($tierExit) { $tierExit } else { 2 }
    $report = Get-Content -Raw -LiteralPath (Join-Path $review 'skillevaluator-tier1.json') | ConvertFrom-Json
    $evidence.overall_status = $report.overall_status
    $evidence.incomplete_scans = @($report.incomplete_scans)
    $pii = @($report.results | Where-Object validator -eq 'PII Scan')
    $applied = @($pii.findings | Where-Object check_name -eq 'applied-pii-waiver' | ForEach-Object { $_.metadata.waiver_id } | Sort-Object)
    $evidence.applied_waivers = $applied
    $matched = $applied.Count -eq 3 -and !(Compare-Object $expectedIds $applied)
    $evidence.pii_status = $pii.status
    $evidence.status = $report.overall_status
    if ($tierExit -eq 0 -and $report.overall_passed -eq $true -and $useWaivers -and $matched -and $pii.status -eq 'passed') { $exitCode = 0 }
    if (!$useWaivers -or !$matched) {
        $evidence.status = 'waiver_verification_failed'
        Write-Warning 'The three reviewed license waivers were not all verified. See the PII findings.'
    }

    if ($ScannerDiagnostics) {
        try {
            & $SkillSpector scan $skill --format json --no-llm > (Join-Path $review 'skillspector.json') 2> (Join-Path $review 'skillspector.stderr.txt')
            $evidence.skillspector_exit = $LASTEXITCODE
            $raw = Get-Content -Raw -LiteralPath (Join-Path $review 'skillspector.json') | ConvertFrom-Json -ErrorAction Stop
            $evidence.skillspector_risk = $raw.risk_assessment
            $evidence.skillspector_coverage = $raw.analysis_completeness.coverage_percent
        } catch {
            $evidence.diagnostics_error = $_.Exception.Message
            Write-Warning "Direct scanner diagnostics failed: $($_.Exception.Message)"
        }
    }
    Write-Host "Tier 1: $($report.overall_status). PII: $($pii.status). Applied waivers: $($applied.Count). Exit: $exitCode"
} catch {
    if ($exitCode -eq 0) { $exitCode = 2 }
    $evidence.status = 'review_error'
    $evidence.error = $_.Exception.Message
    [Console]::Error.WriteLine($_.Exception.Message)
} finally {
    $env:SKILLEVALUATOR_SKILLSPECTOR_PATH = $originalScanner
    $env:PATH = $originalPath
    if ($review) {
        $evidence.exit_code = $exitCode
        $evidence | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $review 'evidence.json') -Encoding utf8
    }
}
exit $exitCode
