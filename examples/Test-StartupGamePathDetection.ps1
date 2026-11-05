#requires -Version 5.1
param([string] $GuiPath = (Join-Path (Split-Path -Parent $PSScriptRoot) 'GUI/WoWSToolboxGUI.ps1'))
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($GuiPath,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'GUI parse failed'}
foreach($name in @('Set-GamePath','Invoke-StartupGamePathDetection')) {
    $function=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name},$true)
    if($null -eq $function){throw "Missing function: $name"}
    . ([scriptblock]::Create($function.Extent.Text))
}
$automatedMode=$false
function Test-GamePathForSource {param($Source,$Path) return -not [string]::IsNullOrWhiteSpace($Path) -and $Path -eq 'C:\ValidPC' -and $Source -eq 'pc'}
function Find-InstalledGamePaths {param($CurrentPaths) $script:DetectionCalls++;return [pscustomobject] $script:Detected}
function Save-Settings {$script:SaveCalls++}
function Get-UiText {param($Ko,$En) return $En}
function Get-SourceDisplay {param($Source) return $Source}
function Add-Log {param($Message) [void]$script:Messages.Add($Message)}
function Reset-Fixture {
    $script:Settings=[pscustomobject]@{LegendsPath='';PcPath='';KorabliPath='';BlitzPath='';SettingsSchema='2'}
    $script:NeedsStartupPathDetection=$true
    $script:Detected=@{legends='';pc='';korabli='';blitz=''}
    $script:DetectionCalls=0;$script:SaveCalls=0;$script:Messages=[Collections.Generic.List[string]]::new()
    $script:controls=@{}
    foreach($name in @('LegendsPathBox','PcPathBox','KorabliPathBox','BlitzPathBox')) {$script:controls[$name]=[pscustomobject]@{Text=''} }
}
# Real user failure: no detected path for one or more optional games.
Reset-Fixture
Invoke-StartupGamePathDetection
if($script:SaveCalls -ne 1 -or $script:NeedsStartupPathDetection){throw 'Empty detection did not finish startup'}
foreach($name in @('LegendsPath','PcPath','KorabliPath','BlitzPath')) {if($script:Settings.$name -ne ''){throw 'Missing game path was not empty'}}
Reset-Fixture
$script:Settings.PcPath='C:\ValidPC';$script:Detected.pc='C:\ValidPC'
Invoke-StartupGamePathDetection
if($script:Settings.PcPath -ne 'C:\ValidPC' -or $script:Settings.KorabliPath -ne '' -or $script:SaveCalls -ne 1){throw 'Single installed game handling failed'}
Reset-Fixture
$script:Settings.KorabliPath='C:\RemovedGame'
Invoke-StartupGamePathDetection
if($script:Settings.KorabliPath -ne '' -or $script:Messages.Count -ne 1){throw 'Stale saved path was not cleared and reported'}
Reset-Fixture
$script:NeedsStartupPathDetection=$false;$script:Settings.PcPath='C:\ValidPC'
Invoke-StartupGamePathDetection
if($script:DetectionCalls -ne 0 -or $script:SaveCalls -ne 0){throw 'Valid settings unnecessarily triggered detection'}
Reset-Fixture
$automatedMode=$true
Invoke-StartupGamePathDetection
if($script:DetectionCalls -ne 0){throw 'Automated-mode guard changed'}
Write-Output ('Startup path detection passed: no games, PC only, stale path, valid settings, automated mode; PowerShell '+$PSVersionTable.PSVersion)
