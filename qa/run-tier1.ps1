#Requires -Version 7.2
[CmdletBinding(DefaultParameterSetName='Package')]
param(
    [Parameter(Mandatory, ParameterSetName='Package')][string]$Package,
    [Parameter(ParameterSetName='Package')][string]$PackageSha256,
    [Parameter(Mandatory, ParameterSetName='Installed')][string]$InstalledDirectory,
    [string]$OutputDirectory = (Join-Path ([IO.Path]::GetTempPath()) 'traced-template-fill-reviews'),
    [string]$Evaluator = 'skillevaluator',
    [string]$SkillSpector = $env:SKILLEVALUATOR_SKILLSPECTOR_PATH,
    [string]$Gitleaks = 'gitleaks',
    [switch]$ScannerDiagnostics,
    [Parameter(ParameterSetName='Package')][switch]$SourceGate,
    [string]$PreviousVersion
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$originalScanner = $env:SKILLEVALUATOR_SKILLSPECTOR_PATH
$originalPath = $env:PATH
$expectedLicense = '935cf13e19f8c31b497d20b05d73623431a226b230c3599bc30fa3348979bc68'
$waiverPins = @{
    'doc-integrator' = '8b6c47fc52d7400d9b9c67b7b011998eb99f6c4f27d73f449206c48c430e9e2c'
    'traced-template-fill' = '205636c123e99cb8ed23711f610f0702f989044c4dfa4e039555cf04b68e60b5'
}

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

function SkillDirectory([string]$Parent) {
    $entries = @(Get-ChildItem -LiteralPath $Parent -Force)
    if ($entries.Count -ne 1 -or !$entries[0].PSIsContainer -or !$waiverPins.ContainsKey($entries[0].Name)) {
        throw 'Expected one traced-template-fill/ or doc-integrator/ directory.'
    }
    return $entries[0]
}

function ExpandKit([string]$Wrapper, [string]$Stage) {
    $payload = Join-Path $Wrapper 'payload.zip'
    $expected = [IO.File]::ReadAllText((Join-Path $Wrapper 'payload.sha256')).Trim()
    $evidence.payload_sha256 = ExpandVerifiedZip $payload $expected $Stage
    $child = SkillDirectory $Stage
    if ($child.Name -cne (Split-Path $Wrapper -Leaf)) { throw 'The wrapper and payload skill names differ.' }
    # Legacy kits stored these wrapper templates under tools/ in the inner payload.
    $mapping = @{ 'run.ps1'='tools/offline/run.ps1'; 'run.sh'='tools/offline/windows-bash.sh'; 'install.ps1'='tools/offline/install.ps1'; 'README.md'='tools/offline/README.md' }
    $skill = $child.FullName
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
    $evidence.input = $source
    if ($PSCmdlet.ParameterSetName -eq 'Package') {
        if (!$PackageSha256) { $PackageSha256 = ([IO.File]::ReadAllText("$source.sha256").Trim() -split '\s+')[0] }
        $outer = Join-Path $review 'kit'
        $evidence.package_sha256 = ExpandVerifiedZip $source $PackageSha256 $outer
        $entry = SkillDirectory $outer
        $skillName = $entry.Name
        $skill = Join-Path $stage $skillName
        if ([IO.File]::Exists((Join-Path $entry.FullName 'payload.zip'))) { ExpandKit $entry.FullName $stage }
        else { Snapshot $entry.FullName $skill }
    } else {
        $skillFile = Join-Path $source 'SKILL.md'
        if (![IO.File]::Exists($skillFile)) { throw 'Select the deployed skill directory containing SKILL.md.' }
        $skillName = if ([IO.File]::ReadAllText($skillFile) -match '(?m)^name:\s*doc-integrator\s*$') { 'doc-integrator' } else { 'traced-template-fill' }
        $skill = Join-Path $stage $skillName
        if ([IO.File]::Exists((Join-Path $source 'payload.zip'))) {
            $wrapper = Join-Path $review "kit/$skillName"
            Snapshot $source $wrapper
            ExpandKit $wrapper $stage
        } else { Snapshot $source $skill }
    }
    $evidence.target = $skill
    $evidence.skill_name = $skillName
    if (![IO.File]::Exists((Join-Path $skill 'SKILL.md'))) { throw 'The staged skill has no SKILL.md.' }

    $Evaluator = (Get-Command $Evaluator -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    if (!$SkillSpector) { $SkillSpector = 'skillspector' }
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
    $expectedWaiver = $waiverPins[$skillName]
    $waiverFilename = if ($skillName -ceq 'doc-integrator') { 'pii-waivers-legacy.yaml' } else { 'pii-waivers.yaml' }
    $waiverSource = Join-Path $PSScriptRoot $waiverFilename
    $needsWaivers = [IO.Directory]::Exists((Join-Path $skill 'runtime/python'))
    $evidence.wheels = @(Get-ChildItem -LiteralPath $skill -File -Recurse -Filter '*.whl' | ForEach-Object {
        [ordered]@{ path = [IO.Path]::GetRelativePath($skill, $_.FullName); sha256 = Digest $_.FullName }
    })
    $evidence.scope = if ($needsWaivers) { 'skill_with_embedded_runtime' }
        elseif ($evidence.wheels.Count) { 'skill_with_wheels' } else { 'skill_source' }
    $evidence.skill_md_sha256 = Digest (Join-Path $skill 'SKILL.md')
    $evidence.waiver_sha256_lf = $null
    if ($needsWaivers -and [IO.File]::Exists($waiverSource)) {
        [IO.File]::Copy($waiverSource,$waiver)
        $normalized = [IO.File]::ReadAllText($waiver).Replace("`r`n","`n")
        $evidence.waiver_sha256_lf = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($normalized))).ToLowerInvariant()
    }
    $evidence.license_sha256 = if ([IO.File]::Exists($license)) { Digest $license } else { $null }
    $today = [datetime]::UtcNow.Date
    $active = $today -ge [datetime]'2026-11-27' -and $today -le [datetime]'2027-05-27'
    $useWaivers = $evidence.waiver_sha256_lf -eq $expectedWaiver -and $evidence.license_sha256 -eq $expectedLicense -and $active
    $evidence.waivers_enabled = $useWaivers
    $evidence.source_gate = [bool]$SourceGate
    $arguments = @('tier1',$skill,'--no-llm','-r','json','-r','markdown','-r','html','-o',$review)
    if ($PreviousVersion) { $arguments += @('--previous-version',$PreviousVersion) }
    if ($SourceGate) {
        if ($evidence.scope -ne 'skill_source' -or !$PreviousVersion) {
            throw 'SourceGate requires the standalone source package and its previous version.'
        }
        $baseline = Join-Path $review 'skillspector-baseline.yaml'
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'skillspector-baseline.yaml') -Destination $baseline
        $normalized = [IO.File]::ReadAllText($baseline).Replace("`r`n","`n")
        $pin = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($normalized)))
        if ($pin -ne 'C10471D63669511D561C5A912CEC0C9B7AC2D4AA7CE0A60FF2FFB5971D952E56') {
            throw 'Source baseline differs from the reviewed record.'
        }
        $evidence.baseline_sha256 = Digest $baseline
        $evidence.previous_version = $PreviousVersion
        $arguments += @('--skillspector-baseline',$baseline)
    }
    if ($useWaivers) { $arguments += @('--pii-waivers',$waiver) }
    elseif ($needsWaivers) { Write-Warning 'Waiver evidence or validity date mismatch. Running full Tier 1 with no PII waivers.' }

    & $Evaluator @arguments *> (Join-Path $review 'tier1.log')
    $tierExit = $LASTEXITCODE
    $evidence.evaluator_exit = $tierExit
    $exitCode = if ($tierExit) { $tierExit } else { 2 }
    $report = Get-Content -Raw -LiteralPath (Join-Path $review 'skillevaluator-tier1.json') | ConvertFrom-Json
    $evidence.overall_status = $report.overall_status
    $evidence.publication_status = $report.publication_status
    $security = $report.results | Where-Object validator -eq 'Security Scan'
    $evidence.skillspector_applicability = $security.skillspector_applicability
    $evidence.skillspector_baseline = $security.skillspector_baseline
    $evidence.incomplete_scans = @($report.incomplete_scans)
    $pii = @($report.results | Where-Object validator -eq 'PII Scan')
    $applied = @($pii.findings | Where-Object check_name -eq 'applied-pii-waiver' | ForEach-Object { $_.metadata.waiver_id } | Sort-Object)
    $evidence.applied_waivers = $applied
    $matched = if ($needsWaivers) { $useWaivers -and $applied.Count -eq 3 -and !(Compare-Object $expectedIds $applied) } else { $applied.Count -eq 0 }
    $evidence.pii_status = $pii.status
    $evidence.status = $report.overall_status
    if ($tierExit -eq 0 -and $report.overall_passed -eq $true -and $matched -and $pii.status -eq 'passed') { $exitCode = 0 }
    if (!$matched) {
        $evidence.status = 'waiver_verification_failed'
        Write-Warning 'The three reviewed license waivers were not all verified. See the PII findings.'
    }

    if ($ScannerDiagnostics -or $SourceGate) {
        try {
            & $SkillSpector scan $skill --format json --no-llm > (Join-Path $review 'skillspector.json') 2> (Join-Path $review 'skillspector.stderr.txt')
            $evidence.skillspector_exit = $LASTEXITCODE
            $raw = Get-Content -Raw -LiteralPath (Join-Path $review 'skillspector.json') | ConvertFrom-Json -ErrorAction Stop
            $evidence.skillspector_risk = $raw.risk_assessment
            $evidence.skillspector_coverage = $raw.analysis_completeness.coverage_percent
            $evidence.raw_completeness = $raw.analysis_completeness.status
            $evidence.raw_counts = [ordered]@{}
            foreach ($severity in @('CRITICAL','HIGH','MEDIUM','LOW','INFO')) {
                $evidence.raw_counts[$severity] = @($raw.issues | Where-Object severity -eq $severity).Count
            }
        } catch {
            $evidence.diagnostics_error = $_.Exception.Message
            Write-Warning "Direct scanner diagnostics failed: $($_.Exception.Message)"
        }
    }
    if ($SourceGate) {
        $receipt = $security.skillspector_baseline
        $harness = $report.results | Where-Object validator -eq 'Harness Compatibility'
        $profiles = $harness.success_details | Where-Object check -eq 'harness_profile_evaluation'
        $evidence.quality_grade = $report.quality_summary.grade
        $evidence.harness_profiles = @($profiles.metadata.harnesses)
        $evidence.harness_not_loaded = @($harness.findings | Where-Object { $_.metadata.effect -eq 'not_loaded' })
        $evidence.source_gate_passed = (
            $exitCode -eq 0 -and $report.results.Count -eq 13 -and
            @($report.results | Where-Object status -ne 'passed').Count -eq 0 -and
            @($report.incomplete_scans).Count -eq 0 -and
            $evidence.quality_grade -eq 'A' -and $profiles.metadata.profiles_evaluated -eq 6 -and
            $evidence.harness_not_loaded.Count -eq 0 -and
            $receipt.loaded -eq $true -and $receipt.status -eq 'no_matches' -and
            $receipt.digest -eq "sha256:$($evidence.baseline_sha256)" -and
            $receipt.suppressed_findings -eq 0 -and
            $security.skillspector_version -eq '2.13.7' -and $null -ne $security.skillspector_applicability -and
            $evidence.skillspector_exit -in 0,1 -and $raw.execution_successful -eq $true -and
            $raw.metadata.skillspector_version -eq '2.13.7' -and $raw.analysis_completeness.is_complete -eq $true -and
            $raw.analysis_completeness.status -eq 'complete' -and
            $raw.suppressed_count -eq 0 -and $evidence.raw_counts.HIGH -eq 0 -and $evidence.raw_counts.CRITICAL -eq 0
        )
        if (!$evidence.source_gate_passed -and !$exitCode) { $exitCode = 2 }
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
        if ($report) {
            $statuses = @($report.results | Group-Object status | ForEach-Object { "$($_.Name) $($_.Count)" })
            $security = $report.results | Where-Object validator -eq 'Security Scan'
            @(
                '# 发布包评审记录'
                ''
                "| 项目 | 本轮记录 |"
                "| --- | --- |"
                "| 文件或目录 | $([IO.Path]::GetFileName($evidence.input)) |"
                "| 发布包 SHA-256 | $($evidence.package_sha256) |"
                "| 评审时间 UTC | $($evidence.started_utc) |"
                "| Tier 1 | $($report.overall_status) · $($statuses -join ' / ') |"
                "| Security Scan | $($security.status) |"
                "| 独立无基线扫描 HIGH | $($evidence.raw_counts.HIGH) |"
                "| Tier 1 安全 HIGH | $($security.summary.high_count) |"
                "| Tier 1 路径适用性处理前 / 后的风险分 | $($security.skillspector_applicability.raw_risk_score) / $($security.skillspector_applicability.applicable_risk_score) |"
                "| 路径适用性保留 / 过滤 | $($security.skillspector_applicability.kept_issues) / $($security.skillspector_applicability.suppressed_inapplicable_issues) |"
                "| 基线命中 | $($security.skillspector_baseline.suppressed_findings) |"
                "| PII | $($evidence.pii_status) |"
                "| 发布资格 | $($report.publication_status) |"
                ''
                '[HTML](skillevaluator-tier1.html) · [完整检查明细](skillevaluator-tier1.md) · [工具、输入与退出码](evidence.json)'
            ) | Set-Content -LiteralPath (Join-Path $review 'release-review.md') -Encoding utf8
        }
    }
}
exit $exitCode
