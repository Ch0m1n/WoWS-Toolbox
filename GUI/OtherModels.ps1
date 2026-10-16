#requires -Version 5.1
# Dialog state is retained explicitly so deferred WPF callbacks never depend on locals.
$script:OtherModelsState = $null

function Update-OtherModelSelectionCount {
    $state=$script:OtherModelsState
    if($null -eq $state -or $null -eq $state.Grid){return}
    $selected=@($state.Rows | Where-Object Selected).Count
    $state.Status.Text=Get-UiText "표시 $($state.Visible.Count)개 / 전체 $($state.Rows.Count)개 · 선택 ${selected}개" "Showing $($state.Visible.Count) / $($state.Rows.Count) models; $selected selected"
}

function Update-OtherModelFilter {
    $state = $script:OtherModelsState
    if ($null -eq $state -or $null -eq $state.Grid) { return }
    $query = $state.Search.Text.Trim()
    $kind = [string] $state.Kind.SelectedItem.Key
    $visible = @($state.Rows | Where-Object {
        ($kind -eq 'all' -or $_.Kind -eq $kind) -and
        ([string]::IsNullOrWhiteSpace($query) -or
         ($_.Name + ' ' + $_.Code + ' ' + $_.Nation + ' ' + $_.Path).IndexOf($query, [StringComparison]::OrdinalIgnoreCase) -ge 0)
    })
    $state.Visible = $visible
    $state.Grid.ItemsSource = $visible
    $selected = @($state.Rows | Where-Object Selected).Count
    $state.Status.Text = Get-UiText "표시 $($visible.Count)개 / 전체 $($state.Rows.Count)개 · 선택 ${selected}개" "Showing $($visible.Count) / $($state.Rows.Count) models; $selected selected"
}

function Start-OtherModelCatalog {
    param([switch] $Refresh)
    if ($null -ne $script:ActiveRunner -or $script:BatchActive) { return }
    $source = Get-SourceKey
    if ($source -eq 'blitz') {
        [Windows.MessageBox]::Show($window, (Get-UiText '기타 모델은 PC·코라블리·레전드에서 지원합니다. 게임 소스를 변경해 주세요.' 'Other models supports PC, Korabli and Legends. Change the game source first.'), (Get-UiText '기타 모델' 'Other models')) | Out-Null
        return
    }
    $game = Get-GamePath $source
    if ([string]::IsNullOrWhiteSpace($game) -or -not (Test-Path -LiteralPath $game -PathType Container)) {
        [Windows.MessageBox]::Show($window, (Get-UiText '설정에서 선택한 게임의 설치 경로를 확인해 주세요.' 'Check the selected game installation path in Settings.'), (Get-UiText '게임 경로 없음' 'Game path missing')) | Out-Null
        return
    }
    $path = Join-Path $script:StateRoot ('OtherModels\catalog-' + $source + '.json')
    $script:OtherModelsState = @{ Source=$source; Game=$game; Catalog=$path; Rows=@(); Grid=$null; Refresh=$false; OpenViewer=$false }
    $arguments = @('-X','utf8','-B',(Join-Path $script:BackendRoot 'other_models.py'),'catalog','--source',$source,'--game-dir',$game,'--cache-root',(Join-Path $script:StateRoot 'Cache'),'--output',$path)
    if ($Refresh) { $arguments += '--refresh' }
    $script:LastResult = $null
    $controls.MainProgress.IsIndeterminate = $true
    $controls.ProgressStage.Text = Get-UiText '기타 모델 목록 읽기' 'Reading other models'
    Add-Log (Get-UiText "$(Get-SourceDisplay $source) 기타 모델 목록을 읽습니다." "Reading other models for $(Get-SourceDisplay $source).")
    Start-ToolProcess -Operation 'other-model-catalog' -Arguments $arguments -Completion {
        param($exitCode)
        $controls.MainProgress.IsIndeterminate = $false
        if ($exitCode -ne 0 -or $script:CancelRequested) {
            $controls.ProgressStage.Text = Get-UiText '기타 모델 목록 읽기 중단' 'Other model listing stopped'
            return
        }
        $catalog = Get-Content -LiteralPath $script:OtherModelsState.Catalog -Raw -Encoding UTF8 | ConvertFrom-Json
        $script:OtherModelsState.Rows = @($catalog.items)
        Show-OtherModelPicker
    }
}

function Show-OtherModelPicker {
    $state = $script:OtherModelsState
    $xaml = @'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
 Width="1060" Height="700" MinWidth="800" MinHeight="520" WindowStartupLocation="CenterOwner" Background="#0B1728" Foreground="#E4EBF5">
 <Window.Resources>
  <Style TargetType="DataGridColumnHeader"><Setter Property="Background" Value="#152944"/><Setter Property="Foreground" Value="#F2F7FF"/><Setter Property="Padding" Value="8,7"/><Setter Property="BorderBrush" Value="#304963"/><Setter Property="BorderThickness" Value="0,0,1,1"/></Style>
  <Style TargetType="DataGridRow"><Setter Property="Background" Value="#0D1A2C"/><Setter Property="Foreground" Value="#E4EBF5"/><Setter Property="MinHeight" Value="28"/></Style>
  <Style TargetType="DataGridCell"><Setter Property="BorderThickness" Value="0"/><Setter Property="Padding" Value="6,4"/><Setter Property="Foreground" Value="#E4EBF5"/></Style>
 </Window.Resources>
 <Grid Margin="20"><Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/><RowDefinition Height="*"/><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/></Grid.RowDefinitions>
  <StackPanel><TextBlock x:Name="Heading" FontSize="22" FontWeight="SemiBold"/><TextBlock x:Name="SourceInfo" Margin="0,6,0,0" Foreground="#9CB0C9" TextWrapping="Wrap"/></StackPanel>
  <Grid Grid.Row="1" Margin="0,16,0,12"><Grid.ColumnDefinitions><ColumnDefinition Width="*"/><ColumnDefinition Width="170"/><ColumnDefinition Width="Auto"/></Grid.ColumnDefinitions>
   <TextBox x:Name="Search" Padding="8" ToolTip="Search"/><ComboBox x:Name="Kind" Grid.Column="1" Margin="8,0" DisplayMemberPath="Label"/><Button x:Name="Refresh" Grid.Column="2" Padding="12,6"/>
  </Grid>
  <DataGrid x:Name="Models" Grid.Row="2" AutoGenerateColumns="False" CanUserAddRows="False" CanUserDeleteRows="False" IsReadOnly="False" SelectionMode="Extended" HeadersVisibility="Column" RowHeaderWidth="0" Background="#091523" Foreground="#E4EBF5" BorderBrush="#304963" EnableRowVirtualization="True">
   <DataGrid.Columns><DataGridTemplateColumn x:Name="SelectColumn" Width="65"><DataGridTemplateColumn.CellTemplate><DataTemplate><CheckBox HorizontalAlignment="Center" VerticalAlignment="Center" IsChecked="{Binding Selected, Mode=TwoWay, UpdateSourceTrigger=PropertyChanged}" AutomationProperties.Name="{Binding Name}"/></DataTemplate></DataGridTemplateColumn.CellTemplate></DataGridTemplateColumn>
    <DataGridTextColumn x:Name="NameColumn" Binding="{Binding Name}" IsReadOnly="True" Width="240"/><DataGridTextColumn x:Name="KindColumn" Binding="{Binding KindLabel}" IsReadOnly="True" Width="95"/>
    <DataGridTextColumn x:Name="CodeColumn" Binding="{Binding Code}" IsReadOnly="True" Width="100"/><DataGridTextColumn x:Name="NationColumn" Binding="{Binding Nation}" IsReadOnly="True" Width="100"/>
    <DataGridTextColumn x:Name="PathColumn" Binding="{Binding Path}" IsReadOnly="True" Width="*" MinWidth="200"/></DataGrid.Columns>
  </DataGrid>
  <StackPanel Grid.Row="3" Margin="0,12,0,12"><TextBlock x:Name="Status"/>
   <TextBlock x:Name="Info" Foreground="#9CB0C9" Margin="0,6,0,8" TextWrapping="Wrap"/>
   <CheckBox x:Name="Effects" Margin="0,0,0,8" Foreground="#E4EBF5"/><CheckBox x:Name="OpenViewer" Foreground="#E4EBF5"/>
  </StackPanel>
  <DockPanel Grid.Row="4"><StackPanel Orientation="Horizontal" DockPanel.Dock="Left"><Button x:Name="SelectVisible" Padding="12,7" Margin="0,0,8,0"/><Button x:Name="Clear" Padding="12,7"/></StackPanel>
   <StackPanel Orientation="Horizontal" HorizontalAlignment="Right"><Button x:Name="Close" Padding="14,7" Margin="0,0,8,0"/><Button x:Name="Run" Padding="18,7"/></StackPanel>
  </DockPanel>
 </Grid>
</Window>
'@
    $dialog = [Windows.Markup.XamlReader]::Parse($xaml)
    $dialog.Owner = $window
    $dialog.Resources.MergedDictionaries.Add($window.Resources)
    $dialog.Title = Get-UiText '기타 모델' 'Other models'
    $dialog.FindName('Heading').Text = Get-UiText '항공기·포탄·어뢰·미사일' 'Aircraft, shells, torpedoes and missiles'
    $dialog.FindName('SourceInfo').Text = "$(Get-SourceDisplay $state.Source) · $($state.Game)"
    $labels = @{
        Refresh=@('목록 새로고침','Refresh'); SelectVisible=@('표시 항목 선택','Select visible'); Clear=@('선택 해제','Clear selection'); Close=@('닫기','Close'); Run=@('선택 모델 추출','Extract selected models')
    }
    foreach ($name in $labels.Keys) { $dialog.FindName($name).Content = Get-UiText $labels[$name][0] $labels[$name][1] }
    $columns = @(@('선택','Select'),@('모델 이름','Model name'),@('종류','Type'),@('코드','Code'),@('국가','Nation'),@('리소스 경로','Resource path'))
    $state.Grid = $dialog.FindName('Models')
    for ($i=0; $i -lt $columns.Count; $i++) { $state.Grid.Columns[$i].Header = Get-UiText $columns[$i][0] $columns[$i][1] }
    $kindLabels = @{aircraft=(Get-UiText '항공기' 'Aircraft'); shell=(Get-UiText '포탄' 'Shell'); torpedo=(Get-UiText '어뢰' 'Torpedo'); missile=(Get-UiText '미사일·로켓' 'Missile / rocket')}
    foreach ($row in $state.Rows) { $row | Add-Member NoteProperty KindLabel $kindLabels[[string]$row.Kind] -Force }
    $state.Dialog=$dialog; $state.Search=$dialog.FindName('Search'); $state.Kind=$dialog.FindName('Kind'); $state.Status=$dialog.FindName('Status')
    $state.Effects=$dialog.FindName('Effects'); $state.Viewer=$dialog.FindName('OpenViewer')
    $state.Effects.Content=Get-UiText '프로펠러 회전판·포탄 궤적 효과도 포함' 'Include propeller discs and shell tracer effects'
    $state.Viewer.Content=Get-UiText '추출 후 마지막 모델 열기' 'Open the last model after extraction'
    $dialog.FindName('Info').Text=Get-UiText 'OBJ·MTL·원본 크기 PNG를 새 폴더에 저장합니다. 없는 모델은 목록에 나오지 않으며, 지원하지 않는 형식과 누락 텍스처는 로그에 표시합니다.' 'Save OBJ, MTL and original-size PNGs in new folders. Only installed geometry is listed; unsupported formats and missing textures are reported in the log.'
    [void]$state.Kind.Items.Add([pscustomobject]@{Key='all';Label=(Get-UiText '전체 종류' 'All types')})
    foreach ($kind in @('aircraft','shell','torpedo','missile')) { [void]$state.Kind.Items.Add([pscustomobject]@{Key=$kind;Label=$kindLabels[$kind]}) }
    $state.Kind.SelectedIndex=0
    $state.Search.Add_TextChanged({Update-OtherModelFilter})
    $state.Kind.Add_SelectionChanged({Update-OtherModelFilter})
    $state.Grid.AddHandler([Windows.Controls.Primitives.ToggleButton]::CheckedEvent, [Windows.RoutedEventHandler]{Update-OtherModelSelectionCount})
    $state.Grid.AddHandler([Windows.Controls.Primitives.ToggleButton]::UncheckedEvent, [Windows.RoutedEventHandler]{Update-OtherModelSelectionCount})
    $dialog.FindName('SelectVisible').Add_Click({
        foreach($row in $script:OtherModelsState.Visible) { $row.Selected=$true }
        $script:OtherModelsState.Grid.Items.Refresh(); Update-OtherModelFilter
    })
    $dialog.FindName('Clear').Add_Click({
        foreach($row in $script:OtherModelsState.Rows) { $row.Selected=$false }
        $script:OtherModelsState.Grid.Items.Refresh(); Update-OtherModelFilter
    })
    $dialog.FindName('Close').Add_Click({$script:OtherModelsState.Dialog.DialogResult=$false})
    $dialog.FindName('Refresh').Add_Click({$script:OtherModelsState.Refresh=$true; $script:OtherModelsState.Dialog.DialogResult=$false})
    $dialog.FindName('Run').Add_Click({
        $s=$script:OtherModelsState
        [void]$s.Grid.CommitEdit([Windows.Controls.DataGridEditingUnit]::Cell,$true)
        [void]$s.Grid.CommitEdit([Windows.Controls.DataGridEditingUnit]::Row,$true)
        $count=@($s.Rows | Where-Object Selected).Count
        if($count -lt 1 -or $count -gt 200){$s.Status.Text=Get-UiText '모델을 1~200개 선택해 주세요.' 'Select between 1 and 200 models.';return}
        $s.IncludeEffects=[bool]$s.Effects.IsChecked; $s.OpenViewer=[bool]$s.Viewer.IsChecked
        $s.Dialog.DialogResult=$true
    })
    Update-OtherModelFilter
    $accepted=$dialog.ShowDialog()
    $state.Grid=$null
    if($state.Refresh){Start-OtherModelCatalog -Refresh;return}
    if(-not $accepted){return}
    $requestPath=Join-Path $script:StateRoot ('OtherModels\request-'+[guid]::NewGuid().ToString('N')+'.json')
    $request=@{source=$state.Source;game_dir=$state.Game;output_root=[string]$script:Settings.OutputPath;cache_root=(Join-Path $script:StateRoot 'Cache');paths=@($state.Rows | Where-Object Selected | ForEach-Object Path);include_effects=$state.IncludeEffects;oodle_dll=[string]$script:Settings.OodlePath}
    $problem=Get-OutputPathProblem -OutputPath $request.output_root -GamePaths @($state.Game)
    if(-not [string]::IsNullOrWhiteSpace($problem)){throw $problem}
    Write-JsonAtomic -Path $requestPath -Value $request -Depth 8
    $script:LastResult=$null
    $controls.MainProgress.IsIndeterminate=$false
    Start-ToolProcess -Operation 'other-model-export' -Arguments @('-X','utf8','-B',(Join-Path $script:BackendRoot 'other_models.py'),'export','--request',$requestPath) -Completion {
        param($exitCode)
        $result=$script:LastResult
        if($null -eq $result -or $null -eq $result.PSObject.Properties['successes']){
            $controls.ProgressStage.Text=Get-UiText '기타 모델 추출 중단' 'Other model extraction stopped';return
        }
        $ok=@($result.successes).Count; $failed=@($result.failures).Count
        $controls.ProgressStage.Text=Get-UiText "기타 모델 완료 ${ok}개 · 실패 ${failed}개" "Other models: $ok completed; $failed failed"
        $controls.MainProgress.Value=100
        foreach($failure in @($result.failures)){Add-Log "$($failure.path): $($failure.message)" -ErrorLine}
        foreach($success in @($result.successes)){foreach($warning in @($success.warnings)){Add-Log ([string]$warning)}}
        if($ok -gt 0){
            $script:LastOutputDir=[string]$result.output_dir
            Add-Log (Get-UiText "기타 모델 저장: $($result.output_dir)" "Other models saved: $($result.output_dir)")
            if($script:OtherModelsState.OpenViewer){Open-ModelInViewer -ObjPath ([string]$result.obj)}
        }
        if($exitCode -ne 0){Set-TopStatus (Get-UiText '일부 추출 실패' 'Some extractions failed') '#F87171'}
    }
}
