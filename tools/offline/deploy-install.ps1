[CmdletBinding(DefaultParameterSetName='Project')]
param(
    [Parameter(Mandatory, ParameterSetName='Project')][string] $ProjectRoot,
    [Parameter(Mandatory, ParameterSetName='User')][switch] $User,
    [switch] $SourceOnly
)
$ErrorActionPreference = 'Stop'
$manifest = Join-Path $PSScriptRoot 'SHA256SUMS-@TAG@.txt'
function Confirm-ReleaseFile([string] $Name, [string] $Path) {
    $line = @(Get-Content -LiteralPath $manifest | Where-Object {
        $_ -cmatch ('^[0-9a-f]{64}  ' + [regex]::Escape($Name) + '$')
    })
    $hasher = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead($Path)
    try {
        $actual = [BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-', '')
    } finally {
        $stream.Dispose()
        $hasher.Dispose()
    }
    if ($line.Count -ne 1 -or $actual -ne $line[0].Substring(0,64)) {
        throw "SHA-256 mismatch: $Name"
    }
}
$helper = Join-Path $PSScriptRoot 'deploy.py'
Confirm-ReleaseFile 'deploy.py' $helper
$arguments = @('-I', '-B', '-X', 'utf8', $helper, '--manifest', $manifest)
if ($User) { $arguments += '--user' } else { $arguments += @('--project-root', $ProjectRoot) }
if ($SourceOnly) { $arguments += '--source-only' }
$python = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if ($python -and $python.Source -notmatch '\\Microsoft\\WindowsApps\\python.exe$') {
    & $python.Source -I -c 'import sys; sys.exit(sys.version_info < (3,10))'
    if ($LASTEXITCODE -eq 0) {
        & $python.Source @arguments
        exit $LASTEXITCODE
    }
}
if ($SourceOnly) { throw 'Source-only installation requires Python 3.10+' }
$tar = Join-Path $env:SystemRoot 'System32\tar.exe'
$tempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
$work = [IO.Directory]::CreateDirectory((Join-Path $tempBase ('ttf-' + [guid]::NewGuid().ToString('N'))))
try {
    $archive = 'traced-template-fill-runtime-win64.zip'
    $copy = Join-Path $work.FullName $archive
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $archive) -Destination $copy
    Confirm-ReleaseFile $archive $copy
    & $tar -xf $copy -C $work.FullName 'traced-template-fill/offline-runtime/payload.zip'
    if ($LASTEXITCODE) { throw 'Cannot open runtime companion' }
    & $tar -xf (Join-Path $work.FullName 'traced-template-fill\offline-runtime\payload.zip') -C $work.FullName 'traced-template-fill/runtime/python'
    if ($LASTEXITCODE) { throw 'Cannot extract the offline interpreter' }
    & (Join-Path $work.FullName 'traced-template-fill\runtime\python\python.exe') @arguments
    $result = $LASTEXITCODE
} finally {
    if ($work.Parent.FullName -eq $tempBase -and !($work.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        Remove-Item -LiteralPath $work.FullName -Recurse -Force
    }
}
exit $result
