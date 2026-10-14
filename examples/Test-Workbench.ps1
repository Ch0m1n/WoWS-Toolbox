#requires -Version 5.1
param([string]$Root=(Split-Path $PSScriptRoot -Parent))
$ErrorActionPreference='Stop'
Add-Type -AssemblyName PresentationFramework
. (Join-Path $Root 'GUI/Localization.ps1')
. (Join-Path $Root 'GUI/Workbench.ps1')
. (Join-Path $Root 'GUI/PickerTools.ps1')
function Get-OptionalShipValue($Ship,$Name){return [string]$Ship.$Name}
foreach($file in @('Workbench.ps1','PickerTools.ps1')){
    $tokens=$null;$errors=$null
    $ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $Root "GUI/$file"),[ref]$tokens,[ref]$errors)
    if($errors.Count){throw ($errors|Out-String)}
    foreach($node in $ast.FindAll({param($node) $node -is [Management.Automation.Language.StringConstantExpressionAst] -and $node.Value -match '^<Window'},$true)){
        $dialog=[Windows.Markup.XamlReader]::Parse($node.Value)
        $dialog.Close()
    }
}
$ship=[pscustomobject]@{ShipCode='ABC';GameParamsKey='KEY';GameParamsIndex='IDX';Tier=7;VariantLabel='premium';ModelPath='hull/test.model'}
if(-not(Test-PickerAdvancedFilter $ship @{exact_code='ABC';tier_min=5;tier_max=8;variant_include='premium'})){throw 'Advanced matching failed'}
if(Test-PickerAdvancedFilter $ship @{exact_code='AB'}){throw 'Code must match exactly'}
if(Test-PickerAdvancedFilter $ship @{variant_exclude='premium'}){throw 'Exclusion failed'}
if(Test-PickerAdvancedFilter $ship @{variant_include='.*'}){throw 'Variant matching must be plain text'}
$owner=[Windows.Window]::new();$button=[Windows.Controls.Button]::new()
$search=[Windows.Controls.TextBox]::new();$combo=[Windows.Controls.ComboBox]::new();$grid=[Windows.Controls.DataGrid]::new()
Install-PickerTools $owner @($ship) 'pc' $button $search $combo $combo $combo $combo @{} $grid {}
if($button.ContextMenu.Items.Count -ne 5){throw 'Search tools missing'}
if($button.ContextMenu.Items[3].IsEnabled){throw 'Other-source reinspection must be disabled'}
$button.RaiseEvent([Windows.RoutedEventArgs]::new([Windows.Controls.Button]::ClickEvent))
if(-not $button.ContextMenu.IsOpen){throw 'Closure context did not open menu'}
$button.ContextMenu.IsOpen=$false;$owner.Close()
'{"ok":true,"workbench_xaml":true,"advanced_plain_filters":true,"picker_menu_closure":true}'
