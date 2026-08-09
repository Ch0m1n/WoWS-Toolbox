#requires -Version 7.0

[CmdletBinding()]
param(
    [string] $Python = 'python',
    [string] $Blender = 'C:\Program Files\Blender Foundation\Blender 3.5\blender.exe'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$rootSelfTest = [IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot '..\..\..\Run-SelfTests.ps1')
)
& (Get-Command pwsh -ErrorAction Stop).Source -NoLogo -NoProfile `
    -File $rootSelfTest -Python $Python -Blender $Blender -IncludeBlender
if ($LASTEXITCODE -ne 0) {
    throw "Toolbox self-test exited with $LASTEXITCODE."
}
