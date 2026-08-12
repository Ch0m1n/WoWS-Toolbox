#requires -Version 5.1

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$packageRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'Localization.ps1')
Set-WoWSToolboxLanguage (Get-WoWSToolboxLanguageMarker -PackageRoot $packageRoot)

$targetScript = Join-Path $PSScriptRoot 'WoWSToolboxGUI.ps1'

try {
    & $targetScript
}
catch {
    $stateRoot = Join-Path $env:LOCALAPPDATA 'WoWSToolbox'
    $logPath = Join-Path $stateRoot 'launch-error.log'
    try {
        [void] (New-Item -ItemType Directory -Path $stateRoot -Force)
        $details = @(
            ('[{0:O}] Toolbox launch failed' -f [DateTimeOffset]::Now)
            $_.Exception.ToString()
            $_.ScriptStackTrace
            ''
        ) -join [Environment]::NewLine
        Add-Content -LiteralPath $logPath -Value $details -Encoding utf8
    }
    catch {
        $logPath = '(오류 로그도 저장하지 못했습니다)'
    }

    $message = if ($script:WoWSToolboxLanguage -eq 'en') {
@"
Could not start WoWS Toolbox.

$($_.Exception.Message)

Error log: $logPath
"@
    }
    else {
@"
WoWS Toolbox를 시작하지 못했습니다.

$($_.Exception.Message)

오류 로그: $logPath
"@
    }
    try {
        Add-Type -AssemblyName PresentationFramework
        [void] [System.Windows.MessageBox]::Show(
            $message,
            (Get-UiText 'WoWS Toolbox 시작 오류' 'WoWS Toolbox startup error'),
            [System.Windows.MessageBoxButton]::OK,
            [System.Windows.MessageBoxImage]::Error
        )
    }
    catch {
        # The launcher is normally hidden. The durable log remains the fallback.
    }
    exit 1
}
