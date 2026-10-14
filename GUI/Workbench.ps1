#requires -Version 5.1
$script:WorkbenchOutputPolicy = 'new'
$script:PickerPendingSelections = @{}

function Invoke-MetadataTool {
    param([string[]] $Arguments)
    $lines = & $script:PythonCommand -X utf8 -B (Join-Path $script:BackendRoot 'workbench.py') @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) { throw ($lines -join [Environment]::NewLine) }
    return ($lines -join [Environment]::NewLine) | ConvertFrom-Json
}

function Get-ExtractionPlan {
    $lines = & $script:PythonCommand -X utf8 -B $script:BatchExtractScript --manifest $script:BatchManifestPath --plan-only 2>&1
    if ($LASTEXITCODE -ne 0) { throw ($lines -join [Environment]::NewLine) }
    return ($lines -join [Environment]::NewLine) | ConvertFrom-Json
}

function Approve-ExtractionPlan {
    param($Manifest)
    $plan = Get-ExtractionPlan
    if (-not $plan.ok) {
        $reasons = @($plan.items | ForEach-Object { $_.blockers }) -join [Environment]::NewLine
        throw $reasons
    }
    # Ordinary extraction needs no modal. Existing output replacement is confirmed.
    if (@($plan.items | Where-Object { $_.action -eq 'replace' }).Count -gt 0) {
        return Show-ExtractionPlan $Manifest
    }
    $Manifest.common['approved_plan'] = [string] $plan.fingerprint
    Write-JsonAtomic -Path $script:BatchManifestPath -Value $Manifest -Depth 16
    return [string] $plan.fingerprint
}

function Show-ExtractionPlan {
    param($Manifest)
    $xaml = @'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Width="1080" Height="620" WindowStartupLocation="CenterOwner" Background="#0B1728" Foreground="#E4EBF5">
 <Grid Margin="18"><Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="*"/><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/></Grid.RowDefinitions>
  <StackPanel><TextBlock x:Name="Heading" FontSize="22"/><ComboBox x:Name="Policy" Margin="0,12,0,12" Width="300" HorizontalAlignment="Left"/></StackPanel>
  <DataGrid x:Name="Rows" Grid.Row="1" IsReadOnly="True" AutoGenerateColumns="False" CanUserAddRows="False">
   <DataGrid.Columns><DataGridTextColumn Header="Ship" Binding="{Binding name}" Width="150"/><DataGridTextColumn Header="Source / Build" Binding="{Binding sourceBuild}" Width="145"/>
    <DataGridTextColumn Header="Requested" Binding="{Binding request}" Width="140"/><DataGridTextColumn Header="Applied" Binding="{Binding applied}" Width="120"/>
    <DataGridTextColumn Header="Camouflage" Binding="{Binding camouflage}" Width="180"/><DataGridTextColumn Header="Action" Binding="{Binding action}" Width="110"/><DataGridTextColumn Header="Cache" Binding="{Binding cache}" Width="100"/><DataGridTextColumn Header="Retry" Binding="{Binding retry}" Width="150"/>
    <DataGridTextColumn Header="Output" Binding="{Binding output}" Width="300"/><DataGridTextColumn Header="Blockers" Binding="{Binding blockers}" Width="220"/></DataGrid.Columns>
  </DataGrid>
  <TextBlock x:Name="Summary" Grid.Row="2" TextWrapping="Wrap" Margin="0,12,0,12"/>
  <StackPanel Grid.Row="3" Orientation="Horizontal" HorizontalAlignment="Right"><Button x:Name="Cancel" Padding="16,8" Margin="0,0,8,0"/><Button x:Name="Run" Padding="16,8"/></StackPanel>
 </Grid></Window>
'@
    $dialog = [Windows.Markup.XamlReader]::Parse($xaml); $dialog.Owner = $window
    $dialog.Title = Get-UiText '추출 계획' 'Extraction plan'
    $dialog.FindName('Heading').Text = $dialog.Title
    $dialog.FindName('Cancel').Content = Get-UiText '취소' 'Cancel'
    $dialog.FindName('Run').Content = Get-UiText '계획대로 추출' 'Extract as planned'
    $policy = $dialog.FindName('Policy')
    foreach ($label in @((Get-UiText '새 버전 폴더' 'New version folder'), (Get-UiText '동일 조건·검증된 결과만 건너뛰기' 'Skip matching verified output'), (Get-UiText '검증 후 교체 · 이전 결과 백업' 'Replace after validation; retain backup'))) {
        [void] $policy.Items.Add($label)
    }
    $labels=@(@('함선','Ship'),@('게임 / 빌드','Source / Build'),@('요청','Requested'),@('적용','Applied'),@('위장','Camouflage'),@('출력 방식','Action'),@('캐시','Cache'),@('재시도 변경','Retry'),@('출력 경로','Output'),@('실행 차단 이유','Blockers'))
    for($i=0;$i -lt $labels.Count;$i++){ $dialog.FindName('Rows').Columns[$i].Header=Get-UiText $labels[$i][0] $labels[$i][1] }
    $state = @{ Plan = $null }
    $refresh = {
        $Manifest.common.output_policy = @('new', 'skip_verified', 'replace')[$policy.SelectedIndex]
        Write-JsonAtomic -Path $script:BatchManifestPath -Value $Manifest -Depth 16
        $state.Plan = Get-ExtractionPlan
        $dialog.FindName('Rows').ItemsSource = @($state.Plan.items | ForEach-Object {
            [pscustomobject]@{ name=$_.name; sourceBuild="$($_.source) / $($_.build)";
                request="$($_.requested.formats) LOD$($_.requested.lod) / $($_.requested.texture_max_size)";
                applied="$($_.applied.formats) LOD$($_.applied.lod) / $($_.applied.texture_max_size)";
                camouflage="$($_.requested.camouflage) $($_.requested.camouflage_color_scheme)";
                action=(@{new=(Get-UiText '새 결과' 'New output');replace=(Get-UiText '백업 후 교체' 'Backup and replace');skip_verified=(Get-UiText '검증 후 건너뜀' 'Verified skip')}[$_.action]); cache=(Get-UiText '미확인' 'Unverified'); retry=if($_.PSObject.Properties['retry']){Get-UiText "설정 변경: $($_.retry.settings_changed) / 조건 변경: $($_.retry.conditions_changed)" "Settings changed: $($_.retry.settings_changed) / conditions: $($_.retry.conditions_changed)"}else{''}; output=$_.output; blockers=($_.blockers -join ' / ') }
        })
        $seconds = 0.0; $bytes = [double] 0
        foreach ($item in $state.Plan.items) { $seconds += [double] $item.estimate.seconds; $bytes += [double] $item.estimate.bytes }
        $basis = @($state.Plan.items | ForEach-Object { "$($_.name): $($_.estimate.basis) / N=$($_.estimate.sample_count) / $($_.estimate.observed_seconds_range -join '–')s" }) -join '; '
        $dialog.FindName('Summary').Text = Get-UiText "추정 $([math]::Round($seconds / 60, 1))분 / $([math]::Round($bytes / 1GB, 2))GB`n$basis`n캐시 재사용과 임시파일 공간은 추정에 반영되지 않았어요. 실행 직전에 조건을 다시 확인해요." "Estimated $([math]::Round($seconds / 60, 1)) min / $([math]::Round($bytes / 1GB, 2)) GB`n$basis`nCache reuse and temporary space are not accounted for. Conditions are checked again before execution."
        $dialog.FindName('Run').IsEnabled = [bool] $state.Plan.ok
    }
    $policy.Add_SelectionChanged({ try { & $refresh } catch { $dialog.FindName('Run').IsEnabled=$false; $dialog.FindName('Summary').Text=$_.Exception.Message } })
    $policy.SelectedIndex = @{new=0;skip_verified=1;replace=2}[[string] $Manifest.common.output_policy]
    $dialog.FindName('Cancel').Add_Click({ $dialog.DialogResult=$false })
    $dialog.FindName('Run').Add_Click({ $dialog.DialogResult=$true })
    if ($dialog.ShowDialog()) {
        $Manifest.common['approved_plan'] = [string] $state.Plan.fingerprint
        Write-JsonAtomic -Path $script:BatchManifestPath -Value $Manifest -Depth 16
        $script:WorkbenchOutputPolicy = [string] $Manifest.common.output_policy
        return [string] $state.Plan.fingerprint
    }
    return ''
}

function Get-RetryActionText {
    param([string] $Action)
    switch ($Action) {
        'close_editor_check_permissions' { Get-UiText '편집기를 닫고 파일 권한을 확인한 뒤 다시 계획을 확인해요.' 'Close the editor, check permissions, then replan.' }
        'check_paths_and_components' { Get-UiText '게임 경로와 구성품 진단을 확인해요.' 'Check game paths and component diagnostics.' }
        'free_space_then_replan' { Get-UiText '디스크 공간을 확보한 뒤 계획을 다시 확인해요.' 'Free disk space, then replan.' }
        'change_supported_options' { Get-UiText '지원되는 옵션으로 바꿔 주세요. 자동 재시도하지 않아요.' 'Choose supported options. No automatic retry.' }
        'verify_game_resources_and_decoder' { Get-UiText '게임 리소스와 Oodle 복원 도구를 확인해요.' 'Verify game resources and the Oodle decoder.' }
        'recalculate_plan' { Get-UiText '빌드나 출력 조건이 바뀌었어요. 계획을 다시 확인해요.' 'Build or output conditions changed. Replan.' }
        default { Get-UiText '로그와 검증 내역을 확인한 뒤 재추출해요. 부분 파일 이어쓰기는 하지 않아요.' 'Inspect the log and validation report, then reexport. Partial files are not resumed.' }
    }
}

function Select-MetadataFile {
    $picker = [Microsoft.Win32.OpenFileDialog]::new()
    $picker.Filter = 'JSON metadata (*.json)|*.json'
    if ($picker.ShowDialog($window)) { return $picker.FileName }
    return ''
}

function Show-Workbench {
    $xaml = @'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml" Width="820" Height="660" WindowStartupLocation="CenterOwner" Background="#0B1728" Foreground="#E4EBF5">
 <Grid Margin="18"><Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/><RowDefinition Height="*"/><RowDefinition Height="Auto"/></Grid.RowDefinitions>
  <ComboBox x:Name="Operation" Margin="0,0,0,12"/>
  <TextBlock x:Name="Help" Grid.Row="1" TextWrapping="Wrap" Margin="0,0,0,12"/>
  <TextBox x:Name="Result" Grid.Row="2" IsReadOnly="True" AcceptsReturn="True" TextWrapping="Wrap" VerticalScrollBarVisibility="Auto"/>
  <StackPanel Grid.Row="3" Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,12,0,0"><Button x:Name="Execute" Padding="16,8" Margin="0,0,8,0"/><Button x:Name="Save" Padding="16,8"/></StackPanel>
 </Grid></Window>
'@
    $dialog = [Windows.Markup.XamlReader]::Parse($xaml); $dialog.Owner = $window
    $dialog.Title = Get-UiText '작업 도구' 'Workflow tools'
    $operations = @(
        @( 'extraction-plan', '추출 계획 보기', 'Preview extraction plan'),
        @( 'quality-save', '품질 프리셋 저장', 'Save quality preset'), @( 'quality-load', '품질 프리셋 불러오기', 'Load quality preset'),
        @( 'snapshot', '현재 함선 목록 스냅샷 저장', 'Save current catalog snapshot'), @( 'snapshots', '저장된 스냅샷 목록', 'List snapshots'),
        @( 'snapshot-compare', '두 스냅샷 비교', 'Compare two snapshots'), @( 'pin', '스냅샷 보존 고정', 'Pin snapshot'),
        @( 'diagnostics', '공유용 구성품 진단 미리보기', 'Preview component diagnostics'),
        @( 'recovery', '중단 작업 복구 검토', 'Review interrupted batch'), @( 'recovery-ack', '복구 검토 완료 · 기록 보존', 'Acknowledge recovery; retain journal'),
        @( 'validate', '추출 결과 파일 재검증', 'Revalidate extraction'), @( 'compare', '두 추출 결과의 조건 비교', 'Compare extraction conditions')
    )
    $combo = $dialog.FindName('Operation')
    foreach ($entry in $operations) { [void] $combo.Items.Add((Get-UiText $entry[1] $entry[2])) }
    $combo.SelectedIndex = 0
    $dialog.FindName('Help').Text = Get-UiText '결과를 먼저 확인하고 필요한 메타데이터만 저장해요. 복구 검토는 자동 재추출하거나 기존 결과를 삭제하지 않아요.' 'Review the result before saving metadata. Recovery never automatically retries or deletes output.'
    $dialog.FindName('Execute').Content = Get-UiText '실행·미리보기' 'Run / preview'
    $dialog.FindName('Save').Content = Get-UiText '표시된 메타데이터 저장' 'Save displayed metadata'
    $dialog.FindName('Save').IsEnabled = $false
    $view = @{ Data=$null }
    $dialog.FindName('Execute').Add_Click({
        try {
            $command = $operations[$combo.SelectedIndex][0]
            if ($command -eq 'extraction-plan') {
                if (-not (Test-ExtractionReady)) { return }
                $dialog.Close()
                $approved = Show-ExtractionPlan (New-BatchManifest)
                if ($approved) { Start-PersistentBatchExtraction -ApprovedPlan $approved }
                return
            }
            if ($command -eq 'quality-save') {
                $save = [Microsoft.Win32.SaveFileDialog]::new(); $save.Filter='Quality preset (*.json)|*.json'; $save.FileName='quality-preset.json'
                if (-not $save.ShowDialog($dialog)) { return }
                $view.Data = [ordered]@{schema='wows-toolbox-quality-preset/v1';formats=(Get-ComboTag $controls.FormatCombo);lod=[int](Get-ComboTag $controls.LodCombo);texture_max_size=[int](Get-ComboTag $controls.TextureCombo)}
                Write-JsonAtomic -Path $save.FileName -Value $view.Data -Depth 5
            } elseif ($command -eq 'quality-load') {
                $file=Select-MetadataFile; if (-not $file) { return }
                if ((Get-Item -LiteralPath $file).Length -gt 65536) { throw 'Preset is too large' }
                $preset=Get-Content -Raw -Encoding UTF8 -LiteralPath $file | ConvertFrom-Json
                if ($preset.schema -ne 'wows-toolbox-quality-preset/v1' -or $preset.formats -ne 'obj' -or $preset.lod -notin @(0,1,2) -or $preset.texture_max_size -notin @(0,1024,2048)) { throw 'Unsupported quality preset' }
                Select-ComboTag $controls.FormatCombo $preset.formats; Select-ComboTag $controls.LodCombo ([string]$preset.lod); Select-ComboTag $controls.TextureCombo ([string]$preset.texture_max_size)
                $view.Data=$preset
            } else {
                $arguments=@($command,'--state-root',$script:StateRoot,'--toolbox-root',$script:PackageRoot,'--output-root',[string]$script:Settings.OutputPath)
                if ($command -eq 'recovery-ack') { $arguments[0]='recovery'; $arguments+='--acknowledge' }
                if ($command -eq 'snapshot') { $arguments+=@('--file',(Get-CatalogPath (Get-SourceKey)),'--source',(Get-SourceKey)) }
                elseif ($command -in @('snapshot-compare','pin','validate','compare')) {
                    $file=Select-MetadataFile; if (-not $file) { return }; $arguments+=@('--file',$file)
                    if ($command -in @('snapshot-compare','compare')) { $other=Select-MetadataFile; if (-not $other) { return }; $arguments+=@('--other',$other) }
                }
                $view.Data=Invoke-MetadataTool $arguments
            }
            $dialog.FindName('Result').Text=$view.Data | ConvertTo-Json -Depth 20
            $dialog.FindName('Save').IsEnabled=$true
        } catch { $dialog.FindName('Result').Text=$_.Exception.Message; $dialog.FindName('Save').IsEnabled=$false }
    })
    $dialog.FindName('Save').Add_Click({
        $save=[Microsoft.Win32.SaveFileDialog]::new();$save.Filter='JSON metadata (*.json)|*.json';$save.FileName='metadata-summary.json'
        if ($save.ShowDialog($dialog)) { Write-JsonAtomic -Path $save.FileName -Value $view.Data -Depth 20 }
    })
    [void] $dialog.ShowDialog()
}

function Test-PickerAdvancedFilter {
    param($Ship, $Filter)
    if ($null -eq $Filter -or $Filter.Count -eq 0) { return $true }
    $code = [string] $Filter['exact_code']
    if ($code -and $code -notin @([string]$Ship.ShipCode, (Get-OptionalShipValue $Ship 'GameParamsKey'), (Get-OptionalShipValue $Ship 'GameParamsIndex'))) { return $false }
    if ($Filter.Contains('tier_min') -and [int] $Ship.Tier -lt [int] $Filter['tier_min']) { return $false }
    if ($Filter.Contains('tier_max') -and [int] $Ship.Tier -gt [int] $Filter['tier_max']) { return $false }
    $variant = (Get-OptionalShipValue $Ship 'VariantLabel') + ' ' + (Get-OptionalShipValue $Ship 'ModelPath')
    $include = [string] $Filter['variant_include']; $exclude = [string] $Filter['variant_exclude']
    if ($include -and $variant.IndexOf($include, [StringComparison]::OrdinalIgnoreCase) -lt 0) { return $false }
    if ($exclude -and $variant.IndexOf($exclude, [StringComparison]::OrdinalIgnoreCase) -ge 0) { return $false }
    return $true
}

function Show-PickerAdvancedFilter {
    param($Owner, $Current)
    $dialog=[Windows.Window]::new();$dialog.Owner=$Owner;$dialog.Title=Get-UiText '고급 필터' 'Advanced filters';$dialog.Width=400;$dialog.Height=390;$dialog.WindowStartupLocation='CenterOwner'
    $panel=[Windows.Controls.StackPanel]::new();$panel.Margin='16';$dialog.Content=$panel
    $boxes=@{}
    foreach ($entry in @(@('exact_code','정확한 함선 코드 / GameParams','Exact ship code / GameParams'),@('tier_min','최소 티어','Minimum tier'),@('tier_max','최대 티어','Maximum tier'),@('variant_include','변형에 포함할 문자열','Variant must contain'),@('variant_exclude','변형에서 제외할 문자열','Exclude variant containing'))) {
        $label=[Windows.Controls.TextBlock]::new();$label.Text=Get-UiText $entry[1] $entry[2];$panel.Children.Add($label)|Out-Null
        $box=[Windows.Controls.TextBox]::new();$box.Margin='0,3,0,10';if($Current.Contains($entry[0])){$box.Text=[string]$Current[$entry[0]]};$panel.Children.Add($box)|Out-Null;$boxes[$entry[0]]=$box
    }
    $apply=[Windows.Controls.Button]::new();$apply.Content=Get-UiText '조건 적용' 'Apply conditions';$panel.Children.Add($apply)|Out-Null
    $apply.Add_Click({
        try {
            $values=@{};foreach($key in $boxes.Keys){$values[$key]=$boxes[$key].Text.Trim()}
            $min=0;$max=20
            if($values.tier_min -and -not [int]::TryParse($values.tier_min,[ref]$min)){throw 'Invalid minimum tier'}
            if($values.tier_max -and -not [int]::TryParse($values.tier_max,[ref]$max)){throw 'Invalid maximum tier'}
            if($min -lt 0 -or $max -gt 20 -or $min -gt $max){throw 'Invalid tier range'}
            $values.tier_min=$min;$values.tier_max=$max;$dialog.Tag=$values;$dialog.DialogResult=$true
        } catch { [void][Windows.MessageBox]::Show($dialog,$_.Exception.Message) }
    })
    if($dialog.ShowDialog()){return $dialog.Tag}
    return $null
}

function Get-MaterialIndexStamp {
    param([string]$Game,[string]$Build)
    $parts=@(Get-ChildItem -LiteralPath (Join-Path $Game "bin/$Build/idx") -Filter '*.idx' -File -ErrorAction SilentlyContinue | Sort-Object Name | ForEach-Object {
        $millis=([DateTimeOffset]$_.LastWriteTimeUtc).ToUnixTimeMilliseconds()
        "$($_.Name):$($_.Length):$millis"
    })
    return $parts -join '|'
}

function Get-ViewerProvenance {
    param([string]$ObjPath)
    $file=Join-Path ([IO.Path]::GetDirectoryName($ObjPath)) 'extraction.manifest.json'
    if(-not (Test-Path -LiteralPath $file -PathType Leaf)){return $null}
    if((Get-Item -LiteralPath $file).Length -gt 8MB){return $null}
    try {
        $data=Get-Content -Raw -Encoding UTF8 -LiteralPath $file|ConvertFrom-Json
        if($data.schema -ne 'wows-toolbox-output/v1'){return $null}
        return @{source=$data.source;selection=$data.selection;requested=$data.requested;applied=$data.applied;counts=$data.counts;armor_available=$data.armor_available}
    }catch{return $null}
}

function Save-ViewerSession {
    param($Session)
    $json=$Session|ConvertTo-Json -Depth 12
    if($json.Length -gt 1MB -or $Session.schema -ne 'wows-toolbox-viewer-session/v1' -or [string]$Session.model -notmatch '^[a-f0-9]{64}$'){throw 'Invalid viewer session'}
    $save=[Microsoft.Win32.SaveFileDialog]::new();$save.Filter='Viewer session (*.json)|*.json';$save.FileName='viewer-session.json'
    if($save.ShowDialog($window)){Write-JsonAtomic -Path $save.FileName -Value $Session -Depth 12}
}

function Show-DiagnosticsProjection {
    param($Data)
    $dialog=[Windows.Window]::new();$dialog.Owner=$window;$dialog.Title=Get-UiText '공유 전 진단 미리보기' 'Preview diagnostics before sharing';$dialog.Width=600;$dialog.Height=550
    $panel=[Windows.Controls.StackPanel]::new();$panel.Margin='16';$dialog.Content=$panel
    $boxes=@();foreach($component in $Data.components){$check=[Windows.Controls.CheckBox]::new();$check.Content="$($component.component): $($component.status)";$check.IsChecked=$true;$panel.Children.Add($check)|Out-Null;$boxes+=@{Box=$check;Value=$component}}
    $batch=[Windows.Controls.CheckBox]::new();$batch.Content=Get-UiText '마지막 배치 성공·실패 수 포함' 'Include last batch success / failure counts';$batch.Margin='0,14,0,10';$panel.Children.Add($batch)|Out-Null
    $save=[Windows.Controls.Button]::new();$save.Content=Get-UiText '선택한 진단만 ZIP 저장' 'Save selected diagnostics as ZIP';$panel.Children.Add($save)|Out-Null
    $save.Add_Click({$projection=[ordered]@{schema=$Data.schema;python=$Data.python;components=@($boxes|Where-Object{$_.Box.IsChecked}|ForEach-Object{$_.Value})};if($batch.IsChecked){$projection['last_batch']=$Data.last_batch};$dialog.Tag=$projection;$dialog.DialogResult=$true})
    if($dialog.ShowDialog()){return $dialog.Tag};return $null
}

function New-PickerFilterCallback {
    param([scriptblock]$Code, [Management.Automation.PSVariable[]]$Variables,
          [Collections.Generic.Dictionary[string,scriptblock]]$Functions)
    $names=@('catalog','grid','count','choose','source','allLabel','search','nation','class','tier','material','selectedOnlyCheck','advancedFilters','pickerQuickFilters','updateQueueCount','updateDetail','showCamoDetail','camoCombo','detailName','detailMeta','detailMaterial','detailVariant','detailKey','defaultCamo')
    $locals=[Collections.Generic.List[Management.Automation.PSVariable]]::new()
    foreach($variable in $Variables){if($variable.Name -in $names){$locals.Add([Management.Automation.PSVariable]::new($variable.Name,$variable.Value))}}
    $Variables=$locals.ToArray()
    # Preserve private helpers as well as local controls when called by a menu
    # closure or when Launch-Gui.ps1 invokes the GUI in a child script scope.
    return { [void]$Code.InvokeWithContext($Functions, $Variables, @()) }.GetNewClosure()
}
