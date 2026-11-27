# Dot-sourced by install.ps1 for the same cache checks used by this launcher.
function Get-OfflineIoPath {
    param([string] $Path)
    $full = [IO.Path]::GetFullPath($Path)
    if ($full.StartsWith('\\?\')) { return $full }
    if ($full.StartsWith('\\')) { return '\\?\UNC\' + $full.Substring(2) }
    return '\\?\' + $full
}

function Get-OfflineExecutionRoot {
    param([string] $Root)
    # CPython's DLL loader and Tesseract's model loader may apply MAX_PATH
    # even on systems where ordinary .NET file I/O accepts device paths.
    if ($Root.Length -ge 192) {
        try {
            $fso = New-Object -ComObject Scripting.FileSystemObject
            $short = $fso.GetFolder((Get-OfflineIoPath $Root)).ShortPath
            if ($short.StartsWith('\\?\UNC\')) { $short = '\\' + $short.Substring(8) }
            elseif ($short.StartsWith('\\?\')) { $short = $short.Substring(4) }
            if ($short.Length -lt $Root.Length) { return $short }
        } catch [System.Runtime.InteropServices.COMException] {
            # Volumes without short names must use their regular path.
        }
    }
    return $Root
}

function Read-OfflineDigest {
    param([string] $Kit)
    $file = Get-OfflineIoPath (Join-Path $Kit 'payload.sha256')
    if (-not [IO.File]::Exists($file)) { throw "Missing kit file: $file" }
    $digest = [IO.File]::ReadAllText($file).Trim()
    if ($digest -notmatch '^[0-9a-fA-F]{64}$') { throw 'Invalid payload.sha256. Expected one SHA-256 digest.' }
    return $digest.ToLowerInvariant()
}

function Get-PrivateSkillPath {
    param([string] $Digest)
    $local = $env:LOCALAPPDATA
    if (-not $local) { $local = [Environment]::GetFolderPath('LocalApplicationData') }
    if (-not $local) { throw 'Cannot locate the local application data directory for this user.' }
    $parent = [IO.Path]::GetFullPath((Join-Path $local 'doc-integrator\offline'))
    $standard = [IO.Path]::Combine($parent, $Digest, 'doc-integrator')
    # Leave space for the embedded interpreter's own native DLL search paths.
    # The readiness marker and archive validation always use the full digest.
    if ($standard.Length -gt 195) { return [IO.Path]::Combine($parent, $Digest.Substring(0, 24)) }
    return $standard
}

function Test-InstalledDigest {
    param([string] $Root, [string] $Digest)
    $marker = Get-OfflineIoPath (Join-Path $Root '.offline-payload.sha256')
    return ([IO.File]::Exists($marker) -and [IO.File]::ReadAllText($marker).Trim() -eq $Digest)
}

function Get-MissingSkillFiles {
    param([string] $Root)
    $ioRoot = Get-OfflineIoPath $Root
    foreach ($relative in 'runtime\python\python.exe', 'runtime\python\python314._pth',
             'runtime\python\python314.dll', 'runtime\python\python314.zip', 'scripts\di.py',
             'scripts\dilib\cli.py', 'scripts\dilib\draft_review.py', 'runtime\tessdata\eng.traineddata', 'runtime\tessdata\chi_sim.traineddata',
             'vendor\wheels.lock.json', 'vendor\wheels\win_amd64\pymupdf-1.28.2-cp310-abi3-win_amd64.whl',
             'vendor\py\pypdf-6.21.4-py3-none-any.whl', 'vendor\py\typing_extensions-4.16.0-py3-none-any.whl') {
        $info = [IO.FileInfo]::new([IO.Path]::Combine($ioRoot, $relative))
        if (-not $info.Exists -or $info.Length -eq 0) { $relative }
    }
}

function Test-OfflineReady {
    param([string] $Root, [string] $Digest)
    return ((Test-InstalledDigest $Root $Digest) -and @(Get-MissingSkillFiles $Root).Count -eq 0)
}

function Find-InstalledSkill {
    param([string] $Digest, [string] $Private, [string] $Kit)
    # An existing project/user install may contain user-customized rules.
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $current = [IO.DirectoryInfo]::new((Get-Location).ProviderPath)
    $bases = @()
    while ($current) {
        $bases += $current.FullName
        $current = $current.Parent
    }
    $kitDir = [IO.DirectoryInfo]::new($Kit)
    if ($kitDir.Parent -and $kitDir.Parent.Name -eq 'skills' -and $kitDir.Parent.Parent -and $kitDir.Parent.Parent.Parent) {
        $bases += $kitDir.Parent.Parent.Parent.FullName
    }
    $bases += [Environment]::GetFolderPath('UserProfile')
    foreach ($base in $bases) {
        if ($base -and $seen.Add($base)) {
            foreach ($agentDir in '.agents', '.claude', '.codex') {
                $candidate = Join-Path $base "$agentDir\skills\doc-integrator"
                if (Test-OfflineReady $candidate $Digest) { return $candidate }
            }
        }
    }
    if (Test-OfflineReady $Private $Digest) { return $Private }
}

if ($MyInvocation.InvocationName -eq '.') { return }

$ErrorActionPreference = 'Stop'
if ([Console]::IsOutputRedirected) { [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false) }
$cliArguments = @($args)
$digest = Read-OfflineDigest $PSScriptRoot
$private = Get-PrivateSkillPath $digest
$installed = Find-InstalledSkill $digest $private $PSScriptRoot
if (-not $installed) {
    & (Join-Path $PSScriptRoot 'install.ps1') -PrivateCache | ForEach-Object { [Console]::Error.WriteLine($_) }
    $installed = $private
}

$executionRoot = Get-OfflineExecutionRoot $installed
$python = Get-OfflineIoPath (Join-Path $executionRoot 'runtime\python\python.exe')
$entry = Get-OfflineIoPath (Join-Path $executionRoot 'scripts\di.py')
# Windows PowerShell 5.1 drops empty argv entries and embedded quotes in native
# calls. Quote each token according to the Windows argv rules on every host.
$native = [Diagnostics.ProcessStartInfo]::new()
$native.FileName = $python
$native.WorkingDirectory = (Get-Location).ProviderPath
$native.UseShellExecute = $false
$native.RedirectStandardOutput = $true
$native.RedirectStandardError = $true
$native.StandardOutputEncoding = [Text.Encoding]::UTF8
$native.StandardErrorEncoding = [Text.Encoding]::UTF8
$native.Arguments = (@(@('-B', '-X', 'utf8', $entry) + $cliArguments | ForEach-Object {
    $quoted = [regex]::Replace([string] $_, '(\\*)"', '$1$1\"')
    '"' + [regex]::Replace($quoted, '(\\+)$', '$1$1') + '"'
}) -join ' ')
$process = [Diagnostics.Process]::Start($native)
try {
    $stderr = $process.StandardError.ReadToEndAsync()
    while ($null -ne ($line = $process.StandardOutput.ReadLine())) { Write-Output $line }
    $process.WaitForExit()
    $errors = $stderr.GetAwaiter().GetResult()
    if ($errors) { [Console]::Error.Write($errors) }
    $code = $process.ExitCode
} finally {
    $process.Dispose()
}
exit $code
