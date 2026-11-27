#Requires -Version 7.2
[CmdletBinding()]
param(
    [string]$Python = 'python',
    [string]$Wheelhouse,
    [string]$EnvironmentDirectory = (Join-Path $HOME '.venvs/doc-integrator-zipreview-1')
)

$ErrorActionPreference = 'Stop'
$wheel = Get-Item -LiteralPath (Join-Path $PSScriptRoot 'skillspector-2.13.7001-py3-none-any.whl')
$expected = 'e88bd2b4caec0b1b42e31192458a16292b8d028a718917ed5765f7e5576ef28b'
if ((Get-FileHash -LiteralPath $wheel.FullName -Algorithm SHA256).Hash -ne $expected) {
    throw 'The review wheel does not match its recorded SHA-256.'
}
$bin = Join-Path $EnvironmentDirectory $(if ($IsWindows) { 'Scripts' } else { 'bin' })
$interpreter = Join-Path $bin $(if ($IsWindows) { 'python.exe' } else { 'python' })
$scanner = Join-Path $bin $(if ($IsWindows) { 'skillspector.exe' } else { 'skillspector' })
$version = 'SkillSpector v2.13.7001'
if ((Test-Path -LiteralPath $scanner -PathType Leaf) -and (& $scanner --version) -eq $version -and $LASTEXITCODE -eq 0) {
    Write-Output $scanner
    return
}
if ($Wheelhouse) { $Wheelhouse = (Resolve-Path -LiteralPath $Wheelhouse).ProviderPath }
if (!(Test-Path -LiteralPath $interpreter -PathType Leaf)) {
    & $Python -m venv $EnvironmentDirectory
    if ($LASTEXITCODE) { throw 'Could not create the review virtual environment.' }
}
$pipArgs = @('-m','pip','install','--disable-pip-version-check')
if ($Wheelhouse) { $pipArgs += @('--no-index','--only-binary=:all:','--find-links',$Wheelhouse) }
& $interpreter @pipArgs $wheel.FullName | Out-Host
if ($LASTEXITCODE) { throw 'Could not install the review scanner and its dependencies.' }
if ((& $scanner --version) -ne $version -or $LASTEXITCODE -ne 0) { throw 'Unexpected installed scanner version.' }
Write-Output $scanner
