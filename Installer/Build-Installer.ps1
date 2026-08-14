#requires -Version 7.0

[CmdletBinding()]
param(
    [string] $ReleaseRoot = (Join-Path (Split-Path -Parent $PSScriptRoot) '..\..\outputs\WoWS-Toolbox-v5.0.41'),
    [string] $OutputRoot = (Join-Path (Split-Path -Parent $PSScriptRoot) '..\..\outputs')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$release = (Resolve-Path -LiteralPath $ReleaseRoot).Path
[void] (New-Item -ItemType Directory -Path $OutputRoot -Force)
$output = (Resolve-Path -LiteralPath $OutputRoot).Path
$setupPath = Join-Path $output 'WoWS-Toolbox-Setup-5.0.41.exe'
$webViewBootstrapper = Join-Path $PSScriptRoot 'dependencies\MicrosoftEdgeWebview2Setup.exe'
$expectedWebViewSha256 = '8C4A80540B6BBCBEF30A4E8C7D1AC504B6FC09DB922B4ACDFD85C9D5F6F1050E'
if (-not (Test-Path -LiteralPath $webViewBootstrapper -PathType Leaf)) {
    throw "Official WebView2 bootstrapper is missing: $webViewBootstrapper"
}
$webViewHash = (Get-FileHash -LiteralPath $webViewBootstrapper -Algorithm SHA256).Hash
if ($webViewHash -ne $expectedWebViewSha256) {
    throw "WebView2 bootstrapper SHA-256 mismatch: $webViewHash"
}
$webViewSignature = Get-AuthenticodeSignature -LiteralPath $webViewBootstrapper
if ($webViewSignature.Status -ne 'Valid' -or $webViewSignature.SignerCertificate.Subject -notlike '*Microsoft Corporation*') {
    throw "WebView2 bootstrapper signature is not a valid Microsoft signature: $($webViewSignature.Status)"
}
if (Test-Path -LiteralPath $setupPath) {
    throw "Installer target already exists; refusing to overwrite: $setupPath"
}

foreach ($relative in @(
    'WoWS Toolbox.exe',
    'README.txt', 'LEGAL_NOTICE.txt',
    'Branding\WoWS-Toolbox.ico', 'MANIFEST.sha256',
    'Runtime\Python\python.exe',
    'Runtime\Python\Lib\site-packages\PIL\__init__.py',
    'Backend\native_glb_export.py',
    'Backend\runtime_i18n.py'
)) {
    if (-not (Test-Path -LiteralPath (Join-Path $release $relative) -PathType Leaf)) {
        throw "Release file required by installer is missing: $relative"
    }
}

$launcherPath = Join-Path $release 'WoWS Toolbox.exe'
$launcherVersion = (Get-Item -LiteralPath $launcherPath).VersionInfo
if ($launcherVersion.FileVersion.Trim() -ne '5.0.41.0' -or
    $launcherVersion.ProductVersion.Trim() -ne '5.0.41') {
    throw "Launcher version metadata is wrong: $($launcherVersion.FileVersion) / $($launcherVersion.ProductVersion)"
}
$launcherProbe = Start-Process -FilePath $launcherPath -ArgumentList '--check' -Wait -PassThru
if ($launcherProbe.ExitCode -ne 0) {
    throw "Launcher readiness probe failed with exit $($launcherProbe.ExitCode)"
}

$manifestLine = Get-Content -LiteralPath (Join-Path $release 'MANIFEST.sha256') |
    Where-Object { $_ -match ' \*Branding/WoWS-Toolbox\.ico$' } |
    Select-Object -First 1
if (-not $manifestLine) {
    throw 'Release manifest does not contain the WoWS Toolbox icon.'
}

$compilerCandidates = @(
    (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
    'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
    'C:\Program Files\Inno Setup 6\ISCC.exe'
)
$compiler = $compilerCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
    Select-Object -First 1
if (-not $compiler) {
    throw 'Inno Setup 6 compiler (ISCC.exe) was not found.'
}

$scriptPath = Join-Path $PSScriptRoot 'WoWS-Toolbox.iss'
& $compiler "/O$output" $scriptPath
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup compiler failed with exit $LASTEXITCODE"
}
if (-not (Test-Path -LiteralPath $setupPath -PathType Leaf)) {
    throw "Installer was not produced: $setupPath"
}

$hash = (Get-FileHash -LiteralPath $setupPath -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Host "Installer ready: $setupPath"
Write-Host "SHA-256: $hash"