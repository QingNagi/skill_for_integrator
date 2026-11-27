[CmdletBinding(DefaultParameterSetName='Discoverable')]
param(
    [Parameter(ParameterSetName='Discoverable')]
    [ValidateSet('Codex', 'Claude')]
    [string] $Agent = 'Codex',
    [Parameter(ParameterSetName='Discoverable')]
    [string] $ProjectRoot,
    [Parameter(Mandatory=$true, ParameterSetName='Cache')]
    [switch] $PrivateCache,
    [Parameter(Mandatory=$true, ParameterSetName='Repair')]
    [string] $RepairRoot
)

$ErrorActionPreference = 'Stop'
$skillName = 'doc-integrator'
$markerName = '.offline-payload.sha256'
. (Join-Path $PSScriptRoot 'run.ps1')

function Confirm-PayloadDigest {
    param([IO.Stream] $Stream, [string] $Expected)
    $hasher = [Security.Cryptography.SHA256]::Create()
    try {
        $actual = [BitConverter]::ToString($hasher.ComputeHash($Stream)).Replace('-', '')
    } finally {
        $hasher.Dispose()
    }
    if ($actual -ne $Expected) {
        throw 'The payload.zip SHA-256 digest differs from payload.sha256. Obtain a complete matching kit.'
    }
    $Stream.Position = 0
}

function Expand-SkillPayload {
    param([IO.Stream] $Stream, [string] $Stage)
    $root = Get-OfflineIoPath $Stage
    $prefix = $root + [IO.Path]::DirectorySeparatorChar
    $zip = [IO.Compression.ZipArchive]::new($Stream, [IO.Compression.ZipArchiveMode]::Read, $true)
    try {
        foreach ($entry in $zip.Entries) {
            $name = $entry.FullName
            if ($name -ceq "$skillName/") { continue }
            if (-not $name.StartsWith("$skillName/", [StringComparison]::Ordinal) -or $name.Contains('\')) {
                throw "Unexpected payload member: $name"
            }
            $parts = $name.Substring($skillName.Length + 1).TrimEnd('/').Split('/')
            foreach ($part in $parts) {
                if (-not $part -or $part.EndsWith('.') -or $part.EndsWith(' ') -or
                    $part.IndexOfAny([IO.Path]::GetInvalidFileNameChars()) -ge 0 -or
                    $part -match '^(con|prn|aux|nul|conin\$|conout\$|com[1-9]|lpt[1-9])(\.|$)') {
                    throw "Invalid Windows path in payload: $name"
                }
            }
            $dest = [IO.Path]::GetFullPath([IO.Path]::Combine($root, ($parts -join '\')))
            if (-not $dest.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Payload member escaped the skill directory: $name"
            }
            if ($name.EndsWith('/')) {
                [void] [IO.Directory]::CreateDirectory($dest)
            } else {
                [void] [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($dest))
                [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $dest, $false)
            }
        }
    } finally {
        $zip.Dispose()
    }
}

function Test-OfflineSkill {
    param([string] $Root)
    $Root = Get-OfflineExecutionRoot $Root
    $python = Join-Path $Root 'runtime\python\python.exe'
    $entry = Join-Path $Root 'scripts\di.py'
    $smoke = Join-Path $Root 'scripts\offline_check.py'
    foreach ($file in @($python, $entry, $smoke, (Join-Path $Root 'SKILL.md'),
            (Join-Path $Root 'runtime\tessdata\eng.traineddata'), (Join-Path $Root 'runtime\tessdata\chi_sim.traineddata'))) {
        $info = [IO.FileInfo]::new((Get-OfflineIoPath $file))
        if (-not $info.Exists -or $info.Length -eq 0) {
            throw "Incomplete offline skill. Missing or empty file: $file"
        }
    }
    $python = Get-OfflineIoPath $python
    $entry = Get-OfflineIoPath $entry
    $smoke = Get-OfflineIoPath $smoke
    # CLI import/argument parsing and real bilingual PDF processing exercise the installed runtime.
    $code = 0
    Invoke-OfflinePython $python @('-B', '-X', 'utf8', $entry, '--help') ([ref]$code) -BundledDefaults | Out-Null
    if ($code -ne 0) { throw "Skill CLI help failed (exit $code). If Windows reports a DLL pathname is too long, use install.ps1 -ProjectRoot with a shorter existing project path." }
    Invoke-OfflinePython $python @('-B', '-X', 'utf8', $smoke) ([ref]$code) -BundledDefaults
    if ($code -ne 0) { throw "Offline document/OCR check failed (exit $code). For a Windows native model/DLL path-length error, use install.ps1 -ProjectRoot with a shorter existing project path." }
}

function Enter-InstallLock {
    param([string] $Path)
    while ($true) {
        try {
            return [IO.File]::Open($Path, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        } catch [IO.IOException] {
            $code = $_.Exception.HResult -band 65535
            if ($code -notin 32, 33) { throw }
            Start-Sleep -Milliseconds 200
        }
    }
}

function Install-ValidatedSkill {
    param([string] $Target, [string] $Digest)
    if (Test-OfflineReady $Target $Digest) { return }
    $parent = [IO.Path]::GetDirectoryName($Target)
    $ioParent = Get-OfflineIoPath $parent
    $ioTarget = Get-OfflineIoPath $Target
    [void] [IO.Directory]::CreateDirectory($ioParent)
    # Keep the lock filename: process termination releases the exclusive handle,
    # while deleting and recreating a lock file could strand competing installers.
    $lock = Enter-InstallLock ([IO.Path]::Combine($ioParent, '.doc-integrator.install.lock'))
    try {
        if (Test-OfflineReady $Target $Digest) { return }
        $repair = [IO.Directory]::Exists($ioTarget) -or [IO.File]::Exists($ioTarget)
        if ($repair -and -not (Test-InstalledDigest $Target $Digest)) {
            throw "Existing skill at '$Target' is unmanaged or belongs to another kit. Choose another project or move the entire skill (including rules and profiles) to a backup before requesting installation here."
        }
        $archive = Get-OfflineIoPath (Join-Path $PSScriptRoot 'payload.zip')
        if (-not [IO.File]::Exists($archive)) { throw "Missing kit file: $archive. Restore this file for first use or repair." }
        Add-Type -AssemblyName System.IO.Compression
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $stageName = '.di-' + [IO.Path]::GetRandomFileName()
        $stage = [IO.Path]::GetFullPath((Join-Path $parent $stageName))
        $ioStage = Get-OfflineIoPath $stage
        $stageInfo = [IO.Directory]::CreateDirectory($ioStage)
        try {
            $stream = [IO.File]::OpenRead($archive)
            try {
                Confirm-PayloadDigest $stream $Digest
                Expand-SkillPayload $stream $stage
            } finally {
                $stream.Dispose()
            }
            $prepared = $stage
            $missing = @(Get-MissingSkillFiles $prepared)
            if ($missing.Count) { throw ('Incomplete payload. Missing or empty file: ' + ($missing -join ', ')) }
            Test-OfflineSkill $prepared
            if ($repair) {
                # Preserve every existing nonempty file, including local rules/profiles.
                # Restore only the missing essentials. Each replacement is staged on the same volume.
                foreach ($relative in @(Get-MissingSkillFiles $Target)) {
                    $dest = [IO.Path]::Combine($ioTarget, $relative)
                    $source = [IO.Path]::Combine($ioStage, $relative)
                    [void] [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($dest))
                    if ([IO.File]::Exists($dest)) {
                        [IO.File]::Replace($source, $dest, [IO.Path]::Combine($ioStage, [guid]::NewGuid().ToString('N')))
                    } else {
                        [IO.File]::Move($source, $dest)
                    }
                }
            } else {
                [IO.File]::WriteAllText([IO.Path]::Combine($ioStage, $markerName), $Digest)
                # A virus scanner may briefly retain a handle to the freshly executed interpreter.
                for ($attempt = 1; ; $attempt++) {
                    try {
                        [IO.Directory]::Move($ioStage, $ioTarget)
                        break
                    } catch [IO.IOException], [UnauthorizedAccessException] {
                        $code = $_.Exception.GetBaseException().HResult -band 65535
                        if ($attempt -ge 8 -or $code -notin 5, 32, 33) { throw }
                        Start-Sleep -Milliseconds (100 * $attempt)
                    }
                }
            }
        } finally {
            if ([IO.Directory]::Exists($ioStage) -and $stageInfo.Parent.FullName -eq $ioParent -and $stageInfo.Name -ceq $stageName -and
                -not ($stageInfo.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                [IO.Directory]::Delete($ioStage, $true)
            }
        }
    } finally {
        $lock.Dispose()
    }
}

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or -not [Environment]::Is64BitOperatingSystem) {
    throw 'This offline kit requires 64-bit Windows with x64 application support.'
}
$digest = Read-OfflineDigest $PSScriptRoot
if ($RepairRoot) {
    $target = [IO.Path]::GetFullPath($RepairRoot)
    if (-not (Test-InstalledDigest $target $digest)) { throw "Cannot repair an unmanaged or different kit: $target" }
} elseif ($PrivateCache) {
    $target = Get-PrivateSkillPath $digest
} else {
    if ($ProjectRoot) {
        $project = Get-Item -LiteralPath $ProjectRoot -Force
        if (-not $project.PSIsContainer -or $project.PSProvider.Name -ne 'FileSystem') {
            throw "ProjectRoot must be an existing project directory: $ProjectRoot"
        }
        $base = $project.FullName
    } else {
        $base = [Environment]::GetFolderPath('UserProfile')
        if (-not $base) { throw 'Cannot find the current user profile. Supply -ProjectRoot for a project installation.' }
    }
    $agentDir = if ($Agent -eq 'Claude') { '.claude' } else { '.agents' }
    $target = [IO.Path]::GetFullPath((Join-Path $base "$agentDir\skills\$skillName"))
}
Install-ValidatedSkill $target $digest
Write-Output "Offline skill ready: $target"
Write-Output ('Embedded Python: ' + (Join-Path $target 'runtime\python\python.exe'))
Write-Output ('CLI entry point: ' + (Join-Path $target 'scripts\di.py'))
