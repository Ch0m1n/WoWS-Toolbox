#requires -Version 5.1
param([string] $Root = (Split-Path $PSScriptRoot -Parent))
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationFramework
. (Join-Path $Root 'GUI/Localization.ps1')
$tokens = $null; $parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    (Join-Path $Root 'GUI/WoWSToolboxGUI.ps1'), [ref] $tokens, [ref] $parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
foreach ($name in @('Get-OptionalShipValue', 'Get-ShipMaterialLabel')) {
    $function = $ast.Find({ param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name
    }, $true)
    Invoke-Expression $function.Extent.Text
}
$picker = $ast.Find({ param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Show-ShipPicker'
}, $true)
$xaml = $picker.Find({ param($node)
    $node -is [Management.Automation.Language.AssignmentStatementAst] -and $node.Left.Extent.Text -eq '$pickerXaml'
}, $true).Right.Expression.Value
$filter = $picker.Find({ param($node)
    $node -is [Management.Automation.Language.AssignmentStatementAst] -and $node.Left.Extent.Text -eq '$applyFilter'
}, $true)

foreach ($language in @('en', 'ko')) {
    $script:WoWSToolboxLanguage = $language
    $translated = Convert-XamlToUiLanguage $xaml
    $dialog = [Windows.Markup.XamlReader]::Parse($translated)
    $search = $dialog.FindName('SearchBox')
    $nation = $dialog.FindName('NationCombo')
    $class = $dialog.FindName('ClassCombo')
    $tier = $dialog.FindName('TierCombo')
    $material = $dialog.FindName('MaterialCombo')
    $grid = $dialog.FindName('ShipGrid')
    $count = $dialog.FindName('CountLabel')
    if ($null -eq $dialog.FindName('MaterialColumn') -or $null -eq $dialog.FindName('DetailMaterial')) {
        throw 'Picker material controls missing'
    }
    $source = 'korabli'
    $allLabel = Get-UiText '전체' 'All'
    foreach ($combo in @($nation, $class, $tier)) {
        [void] $combo.Items.Add($allLabel); $combo.SelectedIndex = 0
    }
    foreach ($label in @('all', 'indexed', 'legacy', 'unknown')) { [void] $material.Items.Add($label) }
    $favoriteOnly = $false; $recentOnly = $false
    $updateQueueCount = {}
    $catalog = @('indexed', 'legacy', 'unknown') | ForEach-Object {
        [pscustomobject] @{
            LocalizedName = "Test $_"; ShipCode = "TEST_$_"; VariantLabel = ''; GameParamsKey = ''
            Nation = 'Test'; ShipClass = 'Cruiser'; Tier = 10
            ShipMaterialStatus = $_; ShipMaterialLabel = ''; QueueSelected = ($_ -eq 'indexed')
            Favorite = $false; Recent = $false
        }
    }
    foreach ($row in $catalog) { $row.ShipMaterialLabel = Get-ShipMaterialLabel -Source $source -Ship $row }
    Invoke-Expression $filter.Extent.Text
    $material.SelectedIndex = 0
    & $applyFilter
    if (@($grid.ItemsSource).Count -ne 3) { throw 'All material filter failed' }
    foreach ($index in @(1, 2, 3)) {
        $material.SelectedIndex = $index
        & $applyFilter
        if (@($grid.ItemsSource).Count -ne 1 -or
            @($grid.ItemsSource)[0].ShipMaterialStatus -ne @('indexed', 'legacy', 'unknown')[$index - 1]) {
            throw "Material filter $index failed"
        }
    }
    if (-not $catalog[0].QueueSelected) { throw 'Filtering lost queued selection' }
    $material.SelectedIndex = 0
    $search.Text = '2.0'
    & $applyFilter
    if (@($grid.ItemsSource).Count -ne 1 -or @($grid.ItemsSource)[0].ShipMaterialStatus -ne 'indexed') {
        throw 'Search by 2.0 label failed'
    }
    $source = 'pc'; $search.Clear(); $material.SelectedIndex = 1
    & $applyFilter
    if (@($grid.ItemsSource).Count -ne 3 -or (Get-ShipMaterialLabel -Source 'pc' -Ship $catalog[0]) -ne '') {
        throw 'Other sources must not use Korabli material filter'
    }
    $dialog.Close()
}
'{"ok":true,"languages":["en","ko"],"material_filters":4,"queue_selection_preserved":true,"other_sources_unaffected":true}'
