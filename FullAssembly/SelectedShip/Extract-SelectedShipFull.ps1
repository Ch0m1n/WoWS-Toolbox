#requires -Version 7.0

[CmdletBinding()]
param(
    [string] $GameDir = 'D:\SteamLibrary\steamapps\common\World of Warships Legends',
    [Parameter(Mandatory)]
    [string] $ShipKey,
    [Parameter(Mandatory)]
    [string] $OutputRoot,
    [string] $SelectedModelPath = '',
    [string] $RunSlug = '',
    [string] $CacheRoot = '',
    [string] $Python = 'python',
    [string] $Blender = 'C:\Program Files\Blender Foundation\Blender 3.5\blender.exe',
    [ValidateSet('harbor_dock', 'neutral_battle_intact')]
    [string] $VisibilityProfile = 'harbor_dock',
    [switch] $Execute,
    [switch] $Overwrite,
    [switch] $KeepWorkFiles
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$pipeline = Join-Path $PSScriptRoot 'Pipeline\extract_selected_ship_full.py'
$arguments = @(
    $pipeline,
    '--game-dir', $GameDir,
    '--ship-key', $ShipKey,
    '--output-root', $OutputRoot,
    '--python', (Get-Command $Python -ErrorAction Stop).Source,
    '--blender', $Blender,
    '--visibility-profile', $VisibilityProfile
)
if (-not [string]::IsNullOrWhiteSpace($SelectedModelPath)) {
    $arguments += @('--selected-model-path', $SelectedModelPath)
}
if (-not [string]::IsNullOrWhiteSpace($RunSlug)) {
    $arguments += @('--run-slug', $RunSlug)
}
if (-not [string]::IsNullOrWhiteSpace($CacheRoot)) {
    $arguments += @('--cache-root', $CacheRoot)
}
if ($Execute) {
    $arguments += '--execute'
}
if ($Overwrite) {
    $arguments += '--overwrite'
}
if ($KeepWorkFiles) {
    $arguments += '--keep-work-files'
}

$previousBytecodeSetting = [Environment]::GetEnvironmentVariable(
    'PYTHONDONTWRITEBYTECODE'
)
$previousPythonUtf8 = [Environment]::GetEnvironmentVariable('PYTHONUTF8')
$previousPythonIoEncoding = [Environment]::GetEnvironmentVariable('PYTHONIOENCODING')
$previousConsoleEncoding = [Console]::OutputEncoding
$previousOutputEncoding = $OutputEncoding
try {
    $utf8 = [Text.UTF8Encoding]::new($false)
    [Console]::OutputEncoding = $utf8
    $OutputEncoding = $utf8
    [Environment]::SetEnvironmentVariable('PYTHONDONTWRITEBYTECODE', '1')
    [Environment]::SetEnvironmentVariable('PYTHONUTF8', '1')
    [Environment]::SetEnvironmentVariable('PYTHONIOENCODING', 'utf-8')
    & $Python @arguments
    $pipelineExitCode = $LASTEXITCODE
}
finally {
    [Console]::OutputEncoding = $previousConsoleEncoding
    $OutputEncoding = $previousOutputEncoding
    [Environment]::SetEnvironmentVariable(
        'PYTHONDONTWRITEBYTECODE',
        $previousBytecodeSetting
    )
    [Environment]::SetEnvironmentVariable('PYTHONUTF8', $previousPythonUtf8)
    [Environment]::SetEnvironmentVariable(
        'PYTHONIOENCODING',
        $previousPythonIoEncoding
    )
}
if ($pipelineExitCode -ne 0) {
    throw "Selected-ship full pipeline exited with $pipelineExitCode."
}
