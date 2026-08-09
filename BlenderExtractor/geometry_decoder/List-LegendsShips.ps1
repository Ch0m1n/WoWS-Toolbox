#requires -Version 7.0

[CmdletBinding()]
param(
    [string]$GameDir = "D:\SteamLibrary\steamapps\common\World of Warships Legends",
    [string]$Python = "python",
    [ValidatePattern('^[A-Za-z]{2,3}(?:_[A-Za-z]{2,4})?$')]
    [string]$Language = "ko",
    [switch]$SupportedOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$arguments = @(
    (Join-Path $PSScriptRoot "ship_catalog.py"),
    "--game-dir", $GameDir,
    "--language", $Language
)
if ($SupportedOnly) {
    $arguments += "--supported-only"
}

& $Python -B @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Legends ship catalog exited with $LASTEXITCODE."
}
