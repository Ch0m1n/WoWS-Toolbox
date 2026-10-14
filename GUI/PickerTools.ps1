#requires -Version 5.1
function Install-PickerTools {
    param($Owner,$Catalog,[string]$Source,$Button,$Search,$Nation,$Class,$Tier,$Material,$Advanced,$Grid,$Apply)
    $menu=[Windows.Controls.ContextMenu]::new();$Button.ContextMenu=$menu
    $context=@{Owner=$Owner;Catalog=$Catalog;Source=$Source;Button=$Button;Search=$Search;Nation=$Nation;Class=$Class;Tier=$Tier;Material=$Material;Advanced=$Advanced;Grid=$Grid;Apply=$Apply;StateRoot=$script:StateRoot;PendingSelections=$script:PickerPendingSelections}
    $Button.Add_Click({$context.Button.ContextMenu.IsOpen=$true}.GetNewClosure())
    $save=[Windows.Controls.MenuItem]::new();$save.Header=Get-UiText '검색·필터 프리셋 저장' 'Save search / filter preset';[void]$menu.Items.Add($save)
    $save.Add_Click({
        try {
            $all=Get-UiText '전체' 'All'
            $raw=@{}
            foreach($pair in @(@('nation',$context.Nation,'Nation','NationRaw'),@('class',$context.Class,'ShipClass','ShipClassRaw'))){
                $raw[$pair[0]]=$null
                if([string]$pair[1].SelectedItem -ne $all){
                    $row=$context.Catalog|Where-Object{[string]$_.($pair[2]) -eq [string]$pair[1].SelectedItem}|Select-Object -First 1
                    $value=Get-OptionalShipValue $row $pair[3];if(-not $value){throw 'Stable raw filter value unavailable'};$raw[$pair[0]]=$value
                }
            }
            $preset=[ordered]@{schema='wows-toolbox-search-preset/v1';source=$context.Source;query=$context.Search.Text;nation=$raw.nation;class=$raw.class;tier=[string]$context.Tier.SelectedItem;material_index=$context.Material.SelectedIndex;advanced=$context.Advanced}
            if($preset.tier -eq $all){$preset.tier=$null}
            $picker=[Microsoft.Win32.SaveFileDialog]::new();$picker.Filter='Search preset (*.json)|*.json';$picker.FileName='search-preset.json'
            if($picker.ShowDialog($context.Owner)){Write-JsonAtomic -Path $picker.FileName -Value $preset -Depth 8}
        }catch{[void][Windows.MessageBox]::Show($context.Owner,$_.Exception.Message)}
    }.GetNewClosure())
    $load=[Windows.Controls.MenuItem]::new();$load.Header=Get-UiText '검색·필터 프리셋 불러오기' 'Load search / filter preset';[void]$menu.Items.Add($load)
    $load.Add_Click({
        try {
            $file=Select-MetadataFile;if(-not $file){return}
            if((Get-Item -LiteralPath $file).Length -gt 65536){throw 'Preset is too large'}
            $preset=Get-Content -Raw -Encoding UTF8 -LiteralPath $file|ConvertFrom-Json
            if($preset.schema -ne 'wows-toolbox-search-preset/v1' -or $preset.source -ne $context.Source -or $preset.material_index -notin @(0,1,2,3)){throw 'Unsupported or different-source search preset'}
            if($context.Source -ne 'korabli' -and $preset.material_index -gt 0){throw 'Material filter is unavailable for this source'}
            $values=@{nation=0;class=0;tier=0}
            foreach($pair in @(@('nation',$context.Nation,'NationRaw','Nation'),@('class',$context.Class,'ShipClassRaw','ShipClass'))){
                if($preset.($pair[0])){
                    $row=$context.Catalog|Where-Object{(Get-OptionalShipValue $_ $pair[2]) -eq [string]$preset.($pair[0])}|Select-Object -First 1
                    if(-not $row){throw 'Saved filter value is unavailable'}
                    $values[$pair[0]]=$pair[1].Items.IndexOf([string]$row.($pair[3]));if($values[$pair[0]] -lt 0){throw 'Saved filter value is unavailable'}
                }
            }
            if($preset.tier){$values.tier=$context.Tier.Items.IndexOf([string]$preset.tier);if($values.tier -lt 0){throw 'Saved tier is unavailable'}}
            $advanced=@{}
            foreach($property in $preset.advanced.PSObject.Properties){if($property.Name -notin @('exact_code','tier_min','tier_max','variant_include','variant_exclude')){throw 'Unknown advanced filter'};$advanced[$property.Name]=$property.Value}
            if($advanced.ContainsKey('tier_min') -and ($advanced.tier_min -lt 0 -or $advanced.tier_min -gt $advanced.tier_max -or $advanced.tier_max -gt 20)){throw 'Invalid tier range'}
            $context.Advanced.Clear();foreach($key in $advanced.Keys){$context.Advanced[$key]=$advanced[$key]}
            $context.Search.Text=[string]$preset.query;$context.Nation.SelectedIndex=$values.nation;$context.Class.SelectedIndex=$values.class;$context.Tier.SelectedIndex=$values.tier;$context.Material.SelectedIndex=$preset.material_index
            & $context.Apply
        }catch{[void][Windows.MessageBox]::Show($context.Owner,$_.Exception.Message)}
    }.GetNewClosure())
    $advanced=[Windows.Controls.MenuItem]::new();$advanced.Header=Get-UiText '고급 조건 편집' 'Edit advanced conditions';[void]$menu.Items.Add($advanced)
    $advanced.Add_Click({
        $values=Show-PickerAdvancedFilter -Owner $context.Owner -Current $context.Advanced
        if($null -ne $values){$context.Advanced.Clear();foreach($key in $values.Keys){$context.Advanced[$key]=$values[$key]};$context.Button.ToolTip=$context.Advanced|ConvertTo-Json -Compress;& $context.Apply}
    }.GetNewClosure())
    foreach($mode in @('selected','unknown')){
        $item=[Windows.Controls.MenuItem]::new();$item.Header=if($mode -eq 'selected'){Get-UiText '체크·선택 함선 재질 재검사' 'Reinspect checked / selected materials'}else{Get-UiText '미확인 재질만 재검사' 'Reinspect unknown materials'}
        $item.IsEnabled=$Source -eq 'korabli';[void]$menu.Items.Add($item)
        $local=@{Context=$context;Mode=$mode}
        $item.Add_Click({
            $ctx=$local.Context
            $chosen=if($local.Mode -eq 'unknown'){@($ctx.Catalog|Where-Object{$_.ShipMaterialStatus -eq 'unknown'})}else{@($ctx.Catalog|Where-Object{$_.QueueSelected})}
            if($chosen.Count -eq 0 -and $local.Mode -eq 'selected' -and $ctx.Grid.SelectedItem){$chosen=@($ctx.Grid.SelectedItem)}
            $models=@($chosen|ForEach-Object{Get-OptionalShipValue $_ 'ModelPath'}|Where-Object{$_}|Sort-Object -Unique)
            if($models.Count -eq 0){return}
            $selection=@{};foreach($row in $ctx.Catalog){$selection[(Get-ShipQueueKey -Source $ctx.Source -Ship $row)]=[bool]$row.QueueSelected}
            $ctx.PendingSelections[$ctx.Source]=$selection
            $file=Join-Path $ctx.StateRoot 'material-selection.json';Write-JsonAtomic -Path $file -Value $models -Depth 4
            $ctx.Owner.Close();Start-CatalogRefresh -OpenPickerAfter -SelectedModelsPath $file
        }.GetNewClosure())
    }
}
