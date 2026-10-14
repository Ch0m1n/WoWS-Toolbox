#requires -Version 5.1
param([string]$Root=(Split-Path $PSScriptRoot -Parent))
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
Add-Type -AssemblyName PresentationFramework
. (Join-Path $Root 'GUI/Localization.ps1')
. (Join-Path $Root 'GUI/Workbench.ps1')
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $Root 'GUI/WoWSToolboxGUI.ps1'),[ref]$tokens,[ref]$errors)
if($errors.Count){throw ($errors|Out-String)}
foreach($name in @('Get-OptionalShipValue','Get-ShipSelectionSummary')){
    $definition=$ast.Find({param($node)$node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name},$true)
    Invoke-Expression $definition.Extent.Text
}
$picker=$ast.Find({param($node)$node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Show-ShipPicker'},$true)
$queueDefinition=$picker.Find({param($node)$node -is [Management.Automation.Language.AssignmentStatementAst] -and $node.Left.Extent.Text -eq '$updateQueueCount'},$true)
function New-DeferredQueueTest {
    $catalog=@([pscustomobject]@{QueueSelected=$true},[pscustomobject]@{QueueSelected=$false})
    $grid=[Windows.Controls.DataGrid]::new();$grid.ItemsSource=$catalog
    $count=[Windows.Controls.TextBlock]::new();$choose=[Windows.Controls.Button]::new()
    Invoke-Expression $queueDefinition.Extent.Text
    $functions=[Collections.Generic.Dictionary[string,scriptblock]]::new()
    foreach($name in @('Get-ShipSelectionSummary','Get-UiText','Convert-ToFormalKoreanText')){$functions[$name]=(Get-Command $name).ScriptBlock}
    $updateQueueCount=New-PickerFilterCallback $updateQueueCount @(Get-Variable -Scope Local) $functions
    [pscustomobject]@{Action=[action]$updateQueueCount;Count=$count;Choose=$choose;Rows=$catalog}
}
foreach($language in @('ko','en')){
    $script:WoWSToolboxLanguage=$language
    $test=New-DeferredQueueTest
    # Queue the delegate only after the defining function scope has ended.
    $test.Rows[1].QueueSelected=$true
    $dispatcher=[Windows.Threading.Dispatcher]::CurrentDispatcher
    $operation=$dispatcher.BeginInvoke([Windows.Threading.DispatcherPriority]::Background,$test.Action)
    $frame=[Windows.Threading.DispatcherFrame]::new()
    [void]$dispatcher.BeginInvoke([Windows.Threading.DispatcherPriority]::ApplicationIdle,[action]{$frame.Continue=$false}.GetNewClosure())
    [Windows.Threading.Dispatcher]::PushFrame($frame)
    if($operation.Status -ne [Windows.Threading.DispatcherOperationStatus]::Completed){throw 'Deferred callback did not complete'}
    if($test.Count.Text -notmatch 'checked 2|체크 2' -or $test.Choose.Content -notmatch '2'){throw 'Deferred count was not refreshed'}
}
'{"ok":true,"deferred_after_scope_exit":true,"languages":["ko","en"]}'
