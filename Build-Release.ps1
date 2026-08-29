#requires -Version 7.0

[CmdletBinding()]
param(
    [string] $Version = '5.0.68',
    [string] $OutputRoot = (Join-Path $PSScriptRoot '..\..\outputs'),
    [switch] $CreateZip
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$sourceRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
[IO.Directory]::CreateDirectory($OutputRoot) | Out-Null
$outputRootPath = (Resolve-Path -LiteralPath $OutputRoot).Path
$outputRootAttributes = [IO.File]::GetAttributes($outputRootPath)
[IO.File]::SetAttributes(
    $outputRootPath,
    ($outputRootAttributes -bor [IO.FileAttributes]::NotContentIndexed)
)
$releaseRoot = Join-Path $outputRootPath "WoWS-Toolbox-v$Version"
if (Test-Path -LiteralPath $releaseRoot) {
    throw "Release target already exists; refusing to overwrite: $releaseRoot"
}
[IO.Directory]::CreateDirectory($releaseRoot) | Out-Null
$releaseRootAttributes = [IO.File]::GetAttributes($releaseRoot)
[IO.File]::SetAttributes(
    $releaseRoot,
    ($releaseRootAttributes -bor [IO.FileAttributes]::NotContentIndexed)
)

foreach ($directory in @(
    'Backend',
    'BlenderExtractor',
    'Branding',
    'docs',
    'FullAssembly',
    'GUI',
    'Runtime',
    'Viewer'
)) {
    $source = Join-Path $sourceRoot $directory
    if (-not (Test-Path -LiteralPath $source -PathType Container)) {
        throw "Release directory is missing: $source"
    }
    Copy-Item -LiteralPath $source -Destination (Join-Path $releaseRoot $directory) -Recurse
}

foreach ($file in @(
    'WoWS Toolbox.exe',
    'app-language.txt',
    'LEGAL_NOTICE.txt',
    'LICENSE',
    'README.txt',
    'THIRD_PARTY_NOTICES.md'
)) {
    $source = Join-Path $sourceRoot $file
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Release file is missing: $source"
    }
    Copy-Item -LiteralPath $source -Destination (Join-Path $releaseRoot $file)
}

# Tests, caches, and migration helpers are source-tree assets, not installed
# runtime inputs. Keep the bundled Python distribution intact.
foreach ($relativeRoot in @('Backend', 'BlenderExtractor', 'FullAssembly', 'GUI', 'Viewer')) {
    $root = Join-Path $releaseRoot $relativeRoot
    Get-ChildItem -LiteralPath $root -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -like 'test_*.py' -or
            $_.Name -like '*_test.py' -or
            $_.Extension -eq '.pyc'
        } |
        Remove-Item -Force
    Get-ChildItem -LiteralPath $root -Directory -Recurse -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -in @('__pycache__', '.pytest_cache') } |
        Sort-Object FullName -Descending |
        Remove-Item -Recurse -Force
}


$buildInfo = [ordered]@{
    schema = 'wows-toolbox-build/v1'
    version = $Version
    created_utc = [DateTime]::UtcNow.ToString('o')
    powershell_compatibility = @('5.1', '7')
    blender_required = $false
}
[IO.File]::WriteAllText(
    (Join-Path $releaseRoot 'BUILD_INFO.json'),
    ($buildInfo | ConvertTo-Json -Depth 4),
    [Text.UTF8Encoding]::new($false)
)

$manifestPath = Join-Path $releaseRoot 'MANIFEST.sha256'
$manifestLines = Get-ChildItem -LiteralPath $releaseRoot -File -Recurse |
    Where-Object FullName -ne $manifestPath |
    ForEach-Object {
        $relative = [IO.Path]::GetRelativePath($releaseRoot, $_.FullName).Replace('\', '/')
        $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash *$relative"
    } |
    Sort-Object
[IO.File]::WriteAllLines(
    $manifestPath,
    [string[]] $manifestLines,
    [Text.UTF8Encoding]::new($false)
)
# Windows Search treats every portable launcher under a development output
# folder as a separate installed app. Mark the complete release tree so only
# the installer-created Start menu shortcut is presented as the application.
Get-ChildItem -LiteralPath $releaseRoot -Recurse -Force -ErrorAction Stop |
    ForEach-Object {
        $attributes = [IO.File]::GetAttributes($_.FullName)
        [IO.File]::SetAttributes(
            $_.FullName,
            ($attributes -bor [IO.FileAttributes]::NotContentIndexed)
        )
    }

if ($CreateZip) {
    $zipPath = Join-Path $outputRootPath "WoWS-Toolbox-v$Version.zip"
    if (Test-Path -LiteralPath $zipPath) {
        throw "ZIP target already exists; refusing to overwrite: $zipPath"
    }
    Compress-Archive -LiteralPath $releaseRoot -DestinationPath $zipPath -CompressionLevel Optimal
    Write-Host "Portable ZIP ready: $zipPath"
}
Write-Host "Release ready: $releaseRoot"
Write-Host "Manifest entries: $($manifestLines.Count)"
