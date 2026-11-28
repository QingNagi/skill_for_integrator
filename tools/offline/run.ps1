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
    $parent = if ($env:DI_RUNTIME_DIR) {
        [IO.Path]::GetFullPath($env:DI_RUNTIME_DIR)
    } elseif ($env:DI_CACHE_DIR) {
        [IO.Path]::GetFullPath((Join-Path $env:DI_CACHE_DIR 'offline'))
    } else { [IO.Path]::GetFullPath((Join-Path $local 'traced-template-fill\offline')) }
    # Keep native DLL and OCR paths short. The marker stores the full digest.
    return [IO.Path]::Combine($parent, $Digest.Substring(0, 24))
}

function Test-InstalledDigest {
    param([string] $Root, [string] $Digest)
    $marker = Get-OfflineIoPath ([IO.Path]::Combine($Root, '.offline-payload.sha256'))
    return ([IO.File]::Exists($marker) -and [IO.File]::ReadAllText($marker).Trim() -eq $Digest)
}

function Get-MissingSkillFiles {
    param([string] $Root)
    $ioRoot = Get-OfflineIoPath $Root
    $required = @(
        # @RUNTIME_FILES@
        'runtime\tessdata\eng.traineddata'
        'runtime\tessdata\chi_sim.traineddata'
        'vendor\wheels.lock.json'
        'SKILL.md'
        'assets\offline-ocr.png'
    )
    foreach ($relative in $required) {
        $info = [IO.FileInfo]::new([IO.Path]::Combine($ioRoot, $relative))
        if (-not $info.Exists -or $info.Length -eq 0) { $relative }
    }
}

function Test-OfflineReady {
    param([string] $Root, [string] $Digest)
    return ((Test-InstalledDigest $Root $Digest) -and @(Get-MissingSkillFiles $Root).Count -eq 0)
}

function Invoke-OfflinePython {
    param([string] $Python, [string[]] $Arguments, [ref] $ExitCode, [switch] $BundledDefaults, [string] $CheckTemp)
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
    $native.Arguments = (@($Arguments | ForEach-Object {
        $quoted = [regex]::Replace([string] $_, '(\\*)"', '$1$1\"')
        '"' + [regex]::Replace($quoted, '(\\+)$', '$1$1') + '"'
    }) -join ' ')
    if ($BundledDefaults) {
        foreach ($name in 'DI_OCR', 'DI_NO_VENDOR') {
            $native.EnvironmentVariables.Remove($name)
        }
    }
    if ($CheckTemp) {
        foreach ($name in 'TEMP', 'TMP', 'TMPDIR') {
            $native.EnvironmentVariables[$name] = $CheckTemp
        }
    }
    $process = [Diagnostics.Process]::Start($native)
    try {
        $stderr = $process.StandardError.ReadToEndAsync()
        while ($null -ne ($line = $process.StandardOutput.ReadLine())) { Write-Output $line }
        $process.WaitForExit()
        $errors = $stderr.GetAwaiter().GetResult()
        if ($errors) { [Console]::Error.Write($errors) }
        $ExitCode.Value = $process.ExitCode
    } finally {
        $process.Dispose()
    }
}

if ($MyInvocation.InvocationName -eq '.') { return }

$ErrorActionPreference = 'Stop'
if ([Console]::IsOutputRedirected) { [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false) }
$cliArguments = @($args)
$digest = Read-OfflineDigest $PSScriptRoot
$installed = Get-PrivateSkillPath $digest
if (-not (Test-OfflineReady $installed $digest)) {
    & (Join-Path $PSScriptRoot 'install.ps1') -PrivateCache | ForEach-Object { [Console]::Error.WriteLine($_) }
}

$executionRoot = Get-OfflineExecutionRoot $installed
$python = Get-OfflineIoPath (Join-Path $executionRoot 'runtime\python\python.exe')
$entry = Get-OfflineIoPath (Join-Path $executionRoot 'scripts\di.py')
$code = 0
Invoke-OfflinePython $python (@('-B', '-X', 'utf8', $entry) + $cliArguments) ([ref]$code)
exit $code
