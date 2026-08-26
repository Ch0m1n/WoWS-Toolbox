#requires -Version 7.0

[CmdletBinding()]
param([string] $Root = $PSScriptRoot)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$sourceRoot = (Resolve-Path -LiteralPath $Root).Path
$manifestPath = Join-Path $sourceRoot 'MANIFEST.sha256'

function Test-ExcludedSourcePath {
    param([Parameter(Mandatory)] [string] $RelativePath)

    $normalized = $RelativePath.Replace('\', '/')
    return (
        $normalized -eq 'MANIFEST.sha256' -or
        $normalized.StartsWith('.git/', [StringComparison]::OrdinalIgnoreCase) -or
        $normalized.StartsWith('.test-', [StringComparison]::OrdinalIgnoreCase) -or
        $normalized.StartsWith('test-results/', [StringComparison]::OrdinalIgnoreCase) -or
        $normalized.StartsWith('output/', [StringComparison]::OrdinalIgnoreCase) -or
        $normalized.StartsWith('validation/', [StringComparison]::OrdinalIgnoreCase) -or
        $normalized.Contains('/__pycache__/') -or
        $normalized.Contains('/.pytest_cache/')
    )
}

$lines = Get-ChildItem -LiteralPath $sourceRoot -Recurse -File -Force |
    ForEach-Object {
        $relative = [IO.Path]::GetRelativePath($sourceRoot, $_.FullName).Replace('\', '/')
        if (Test-ExcludedSourcePath -RelativePath $relative) { return }
        $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash *$relative"
    } |
    Sort-Object

$tempPath = "$manifestPath.tmp"
[IO.File]::WriteAllLines(
    $tempPath,
    [string[]] $lines,
    [Text.UTF8Encoding]::new($false)
)
Move-Item -LiteralPath $tempPath -Destination $manifestPath -Force

Write-Host "Source manifest updated: $manifestPath"
Write-Host "Entries: $($lines.Count)"
