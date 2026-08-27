#requires -Version 5.1

[CmdletBinding()]
param([string] $OutputPath)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path (Split-Path -Parent $PSScriptRoot) 'WoWS Toolbox.exe'
}

$source = Join-Path $PSScriptRoot 'WoWSToolboxLauncher.cs'
$packageRoot = Split-Path -Parent $PSScriptRoot
$icon = Join-Path $packageRoot 'Branding\WoWS-Toolbox.ico'
foreach ($required in @($source, $icon)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Launcher build input is missing: $required"
    }
}

$compilerCandidates = @(
    (Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'),
    (Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe')
)
$compiler = $compilerCandidates |
    Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
    Select-Object -First 1
if (-not $compiler) {
    throw 'The .NET Framework C# compiler (csc.exe) was not found.'
}

$arguments = @(
    '/nologo',
    '/target:winexe',
    '/platform:x64',
    '/optimize+',
    '/checked+',
    "/win32icon:$icon",
    "/out:$OutputPath",
    '/reference:System.dll',
    '/reference:System.Windows.Forms.dll',
    $source
)
& $compiler $arguments
if ($LASTEXITCODE -ne 0) {
    throw "Launcher compilation failed with exit $LASTEXITCODE"
}
if (-not (Test-Path -LiteralPath $OutputPath -PathType Leaf)) {
    throw "Launcher was not produced: $OutputPath"
}

$version = (Get-Item -LiteralPath $OutputPath).VersionInfo
if ($version.FileVersion.Trim() -ne '5.0.65.0' -or
    $version.ProductVersion.Trim() -ne '5.0.65') {
    throw "Launcher version metadata is wrong: $($version.FileVersion) / $($version.ProductVersion)"
}
Write-Host "Launcher ready: $OutputPath"
