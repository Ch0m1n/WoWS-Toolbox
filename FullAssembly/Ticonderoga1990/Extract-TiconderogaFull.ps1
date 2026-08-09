#requires -Version 7.0

[CmdletBinding()]
param(
    [string] $GameDir = 'D:\SteamLibrary\steamapps\common\World of Warships Legends',
    [Parameter(Mandatory)]
    [string] $OutputRoot,
    [string] $Python = 'python',
    [string] $Blender = 'C:\Program Files\Blender Foundation\Blender 3.5\blender.exe',
    [ValidateSet('harbor_dock', 'neutral_battle_intact')]
    [string] $VisibilityProfile = 'harbor_dock',
    [switch] $Execute,
    [switch] $Overwrite
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$pipeline = Join-Path $PSScriptRoot 'Pipeline\extract_ticonderoga_full.py'
$arguments = @(
    $pipeline,
    '--game-dir', $GameDir,
    '--output-root', $OutputRoot,
    '--python', (Get-Command $Python -ErrorAction Stop).Source,
    '--blender', $Blender,
    '--visibility-profile', $VisibilityProfile
)
if ($Execute) {
    $arguments += '--execute'
}
if ($Overwrite) {
    $arguments += '--overwrite'
}

$previousBytecodeSetting = [Environment]::GetEnvironmentVariable(
    'PYTHONDONTWRITEBYTECODE'
)
try {
    [Environment]::SetEnvironmentVariable(
        'PYTHONDONTWRITEBYTECODE',
        '1'
    )
    & $Python @arguments
    $pipelineExitCode = $LASTEXITCODE
}
finally {
    [Environment]::SetEnvironmentVariable(
        'PYTHONDONTWRITEBYTECODE',
        $previousBytecodeSetting
    )
}
if ($pipelineExitCode -ne 0) {
    throw "Ticonderoga verified-profile pipeline exited with $pipelineExitCode."
}
