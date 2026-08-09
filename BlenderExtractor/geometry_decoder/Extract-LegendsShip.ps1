#requires -Version 7.0

[CmdletBinding()]
param(
    [string]$GameDir = "D:\SteamLibrary\steamapps\common\World of Warships Legends",
    [string]$ShipIndexFile,
    [string]$ShipResource,
    [string[]]$ShipIndex,
    [string]$OutputRoot = (Join-Path $PSScriptRoot "ship_exports"),
    [string]$Python = "python",
    [string]$Blender = "C:\Program Files\Blender Foundation\Blender 3.5\blender.exe",
    [ValidateRange(0, 99)]
    [int]$IntactLod = 0,
    [switch]$Execute,
    [switch]$Overwrite,
    [switch]$NoBlender
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$hasExactIndex = -not [string]::IsNullOrWhiteSpace($ShipIndexFile)
$hasLegacyTerms = $null -ne $ShipIndex -and $ShipIndex.Count -gt 0
if ($hasExactIndex -eq $hasLegacyTerms) {
    throw "Specify exactly one selector: -ShipIndexFile or legacy -ShipIndex."
}
if (-not [string]::IsNullOrWhiteSpace($ShipResource) -and -not $hasExactIndex) {
    throw "-ShipResource requires exact -ShipIndexFile selection."
}
$arguments = @(
    (Join-Path $PSScriptRoot "extract_legends_ship.py"),
    "--game-dir", $GameDir,
    "--output-root", $OutputRoot,
    "--blender", $Blender,
    "--intact-lod", $IntactLod
)
if ($hasExactIndex) {
    $arguments += @("--ship-index-file", $ShipIndexFile)
    if (-not [string]::IsNullOrWhiteSpace($ShipResource)) {
        $arguments += @("--ship-resource", $ShipResource)
    }
}
else {
    foreach ($term in $ShipIndex) {
        $arguments += @("--ship-index", $term)
    }
}
if ($Execute) {
    $arguments += "--execute"
}
if ($Overwrite) {
    $arguments += "--overwrite"
}
if ($NoBlender) {
    $arguments += "--no-blender"
}

& $Python -B @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Legends ship pipeline exited with $LASTEXITCODE."
}
