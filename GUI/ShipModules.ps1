# Hull, main battery, and torpedo research choices belong to each queue entry.
$script:QueueModulesUpdating = $false
$script:ResearchModuleDialog = $null

function Initialize-QueueModules {
    param($Entry)
    foreach ($name in @('HullUpgrade', 'ArtilleryUpgrade', 'TorpedoUpgrade')) {
        if ($null -eq $Entry.PSObject.Properties[$name]) {
            $Entry | Add-Member -NotePropertyName $name -NotePropertyValue 'top'
        }
    }
}

function Get-QueueModuleCatalog {
    param($Entry)
    if ($null -eq $Entry -or [string] $Entry.Source -notin @('pc', 'korabli')) { return @() }
    $property = $Entry.Ship.PSObject.Properties['Modules']
    if ($null -eq $property -or $null -eq $property.Value) { return @() }
    return @($property.Value.Hulls)
}

function Set-ModuleComboItems {
    param($Combo, [object[]] $Options, [string] $Current, [string] $Label, [bool] $Supported)
    $topText = if ($Supported -and $Options.Count -eq 0) { Get-UiText "$Label · 없음" "$Label · None" }
        elseif ($Supported) { Get-UiText "$Label · 최종 연구 ($($Options[-1].Name))" "$Label · Fully upgraded ($($Options[-1].Name))" }
        else { Get-UiText "$Label · 기본 구성" "$Label · Default loadout" }
    $top = [pscustomobject] @{ Id='top'; Name=$topText }
    $items = @($top) + @($Options | ForEach-Object {
        [pscustomobject] @{ Id=[string] $_.Id; Name="$Label · $($_.Name)" }
    })
    $selected = $items | Where-Object { $_.Id -eq $Current } | Select-Object -First 1
    if ($null -eq $selected) {
        # Keep stale saved choices visible; execution validates against the current client.
        $selected = [pscustomobject] @{ Id=$Current; Name="$Label · $Current" }
        $items += $selected
    }
    $Combo.ItemsSource = $items
    $Combo.SelectedItem = $selected
    $Combo.IsEnabled = $Supported -and $Options.Count -gt 1 -and
        $null -eq $script:ActiveRunner -and -not $script:BatchActive
    $Combo.ToolTip = if ($Supported) {
        Get-UiText '대기열에서 선택한 함선의 부품을 바꿔요. 대공포와 부포는 선체 구성을 따라가요.' 'Change modules for the selected queued ship. AA and secondary mounts follow the hull.'
    } else {
        Get-UiText 'PC·코라블리 함선의 목록을 새로고친 뒤 대기열에서 선택해 주세요.' 'Refresh the PC or Korabli ship catalog, then select a queued ship.'
    }
}

function Update-QueueModuleControls {
    $script:QueueModulesUpdating = $true
    try {
        $entry = $controls.QueueList.SelectedItem
        $hulls = @(Get-QueueModuleCatalog $entry)
        $supported = $hulls.Count -gt 0
        $hullId = 'top'; $artilleryId = 'top'; $torpedoId = 'top'
        if ($null -ne $entry) {
            Initialize-QueueModules $entry
            $hullId = [string] $entry.HullUpgrade
            $artilleryId = [string] $entry.ArtilleryUpgrade
            $torpedoId = [string] $entry.TorpedoUpgrade
        }
        $hull = if ($hullId -eq 'top' -and $supported) { $hulls[-1] }
            else { $hulls | Where-Object { $_.Id -eq $hullId } | Select-Object -First 1 }
        $guns = @(); $torps = @()
        if ($null -ne $hull) { $guns = @($hull.Artillery); $torps = @($hull.Torpedoes) }
        Set-ModuleComboItems $controls.HullModuleCombo $hulls $hullId (Get-UiText '선체' 'Hull') $supported
        Set-ModuleComboItems $controls.ArtilleryModuleCombo $guns $artilleryId (Get-UiText '주함포' 'Main battery') $supported
        Set-ModuleComboItems $controls.TorpedoModuleCombo $torps $torpedoId (Get-UiText '어뢰' 'Torpedoes') $supported
        if ($controls.ContainsKey('ResearchModulesButton')) {
            $controls.ResearchModulesButton.IsEnabled = $supported -and $null -eq $script:ActiveRunner -and -not $script:BatchActive
            $controls.ResearchModulesButton.Content = Get-UiText '부품 선택' 'Select modules'
            $controls.ResearchModulesButton.ToolTip = $controls.HullModuleCombo.ToolTip
            $controls.ResearchModulesSummary.Text = if ($supported) { Get-QueueModuleLabel $entry }
                elseif ($null -eq $entry) { Get-UiText '대기열 함선을 선택해 주세요' 'Select a queued ship' }
                elseif ([string] $entry.Source -in @('pc', 'korabli')) { Get-UiText '목록 새로고침 후 추가·편집에서 함선을 다시 선택해 주세요' 'Refresh the catalog and reselect the ship in Add/Edit' }
                else { Get-UiText '이 게임은 기본 구성을 사용해요' 'This game uses its fixed configuration' }
        }
        if ($null -ne $script:ResearchModuleDialog) {
            Update-ResearchModuleCards $hulls $guns $torps $entry
        }
    } finally { $script:QueueModulesUpdating = $false }
}

function Set-QueueModuleSelection {
    param([string] $Field, [string] $Control)
    if ($script:QueueModulesUpdating -or $null -ne $script:ActiveRunner -or $script:BatchActive) { return }
    $entry = $controls.QueueList.SelectedItem
    $option = $controls[$Control].SelectedItem
    if ($null -eq $entry -or $null -eq $option) { return }
    Initialize-QueueModules $entry
    $entry.$Field = [string] $option.Id
    if ($Field -eq 'HullUpgrade') {
        # Retain the chosen research upgrade only if the new hull supports it.
        $hulls = @(Get-QueueModuleCatalog $entry)
        $hull = if ($entry.HullUpgrade -eq 'top') { $hulls[-1] }
            else { $hulls | Where-Object { $_.Id -eq $entry.HullUpgrade } | Select-Object -First 1 }
        if ($null -ne $hull) {
            if ($entry.ArtilleryUpgrade -ne 'top' -and $entry.ArtilleryUpgrade -notin @($hull.Artillery | ForEach-Object { $_.Id })) { $entry.ArtilleryUpgrade = 'top' }
            if ($entry.TorpedoUpgrade -ne 'top' -and $entry.TorpedoUpgrade -notin @($hull.Torpedoes | ForEach-Object { $_.Id })) { $entry.TorpedoUpgrade = 'top' }
        }
    }
    Update-QueueEntryDisplay $entry
    $controls.QueueList.Items.Refresh()
    Update-QueueModuleControls
}

function Get-QueueModuleLabel {
    param($Entry)
    if ([string] $Entry.Source -notin @('pc', 'korabli')) { return '' }
    Initialize-QueueModules $Entry
    $hulls = @(Get-QueueModuleCatalog $Entry)
    $hull = if ($Entry.HullUpgrade -eq 'top' -and $hulls.Count -gt 0) { $hulls[-1] }
        else { $hulls | Where-Object { $_.Id -eq $Entry.HullUpgrade } | Select-Object -First 1 }
    $labels = @()
    foreach ($pair in @(@('HullUpgrade','선체','Hull'), @('ArtilleryUpgrade','주함포','Gun'), @('TorpedoUpgrade','어뢰','Torpedo'))) {
        $id = [string] $Entry.($pair[0])
        $text = if ($id -eq 'top') { Get-UiText '최종' 'Top' } else {
            $options = if ($pair[0] -eq 'HullUpgrade') { $hulls } elseif ($null -eq $hull) { @() }
                elseif ($pair[0] -eq 'ArtilleryUpgrade') { @($hull.Artillery) } else { @($hull.Torpedoes) }
            $match = $options | Where-Object { $_.Id -eq $id } | Select-Object -First 1
            if ($null -ne $match) { [string] $match.Name } else { $id }
        }
        $labels += "$(Get-UiText $pair[1] $pair[2]) $text"
    }
    return $labels -join ' / '
}

function Get-ResearchModuleDetail {
    param($Option, [string] $Kind)
    if ($Kind -eq 'Hull') { return [string] $controls.QueueList.SelectedItem.Ship.LocalizedName }
    $models = $Option.PSObject.Properties['Models']
    if ($null -ne $models -and @($models.Value).Count -gt 0) {
        $model = [string] @($models.Value)[0]
        if ($Kind -eq 'Artillery' -and $model -match '_(\d+)_(\d+)mm_') { return "$($Matches[1]) mm/$($Matches[2])" }
        if ($Kind -eq 'Torpedo' -and $model -match '_(\d+)in_(\d+)(?:tube|_)') { return "$($Matches[1]) in · $($Matches[2]) $(Get-UiText '연장' 'tubes')" }
        if ($Kind -eq 'Torpedo' -and $model -match '_(\d+)_?mm_(\d+)_?(?:tube|tubes)') { return "$($Matches[1]) mm · $($Matches[2]) $(Get-UiText '연장' 'tubes')" }
    }
    return Get-UiText '연구 부품' 'Research module'
}

function Select-ResearchModuleCard {
    param($Selection)
    if ($script:QueueModulesUpdating) { return }
    $combo = $controls[[string] $Selection.Control]
    $choice = $combo.ItemsSource | Where-Object { $_.Id -eq $Selection.Id } | Select-Object -First 1
    if ($null -ne $choice) {
        if ($combo.SelectedItem -eq $choice) { Update-QueueModuleControls }
        else { $combo.SelectedItem = $choice }
    }
}

function Update-ResearchModuleCards {
    param([object[]] $Hulls, [object[]] $Guns, [object[]] $Torps, $Entry)
    $state = $script:ResearchModuleDialog
    foreach ($group in @(
        @{Kind='Hull';Options=$Hulls;Field='HullUpgrade';Control='HullModuleCombo';Panel=$state.Hull;Label=(Get-UiText '선체' 'Hull')},
        @{Kind='Artillery';Options=$Guns;Field='ArtilleryUpgrade';Control='ArtilleryModuleCombo';Panel=$state.Artillery;Label=(Get-UiText '주함포' 'Main battery')},
        @{Kind='Torpedo';Options=$Torps;Field='TorpedoUpgrade';Control='TorpedoModuleCombo';Panel=$state.Torpedo;Label=(Get-UiText '어뢰' 'Torpedoes')}
    )) {
        $panel = $group.Panel; $panel.Children.Clear()
        $options = @($group.Options)
        $id = [string] $Entry.($group.Field)
        $selected = if ($id -eq 'top' -and $options.Count -gt 0) { [string] $options[-1].Id } else { $id }
        if ($options.Count -eq 0) {
            $none = [Windows.Controls.TextBlock]::new(); $none.Text = Get-UiText '탑재 무장 없음' 'No armament'; $none.Foreground='#AAB9CD'; $none.Margin='12,16,12,0'
            [void] $panel.Children.Add($none)
        }
        for ($i=0; $i -lt $options.Count; $i++) {
            $option=$options[$i]
            $card=[Windows.Controls.RadioButton]::new();$card.GroupName=$group.Kind;$card.Style=$state.Window.Resources['ResearchCard']
            $card.Tag=@{Control=$group.Control;Id=[string]$option.Id};$card.IsChecked=[string]$option.Id -eq $selected
            $card.IsEnabled=$null -eq $script:ActiveRunner -and -not $script:BatchActive
            $detail=Get-ResearchModuleDetail $option $group.Kind
            $card.SetValue([Windows.Automation.AutomationProperties]::NameProperty,"$($group.Label) $($option.Name), $detail")
            $content=[Windows.Controls.StackPanel]::new()
            $icon=[Windows.Shapes.Path]::new();$icon.Stroke='#CBD5E1';$icon.StrokeThickness=1.5;$icon.Width=64;$icon.Height=22;$icon.Stretch='Uniform';$icon.HorizontalAlignment='Left';$icon.Margin='0,0,0,7'
            $geometry=if($group.Kind -eq 'Hull'){'M 1,12 L 10,23 L 59,23 L 69,12 Z M 22,12 L 22,6 L 45,6 L 45,12 M 33,6 L 33,1'}elseif($group.Kind -eq 'Artillery'){'M 3,22 L 3,10 L 27,10 L 32,22 Z M 26,12 L 64,3 M 28,16 L 67,7 M 6,22 L 33,22'}else{'M 3,7 L 54,7 L 68,15 L 54,23 L 3,23 Z M 11,7 L 2,1 L 2,29 L 11,23 M 19,7 L 19,23'}
            $icon.Data=[Windows.Media.Geometry]::Parse($geometry);[void]$content.Children.Add($icon)
            $title=[Windows.Controls.TextBlock]::new();$title.Text="$($group.Label) ($($option.Name))";$title.FontWeight='SemiBold';$title.FontSize=14;$title.Foreground='#F1F5F9';[void]$content.Children.Add($title)
            $description=[Windows.Controls.TextBlock]::new();$description.Text=$detail;$description.TextWrapping='Wrap';$description.FontSize=12;$description.Foreground='#B9C6D6';$description.Margin='0,4,0,0';[void]$content.Children.Add($description)
            $card.Content=$content
            $card.Add_Click({param($sender,$eventArgs) Select-ResearchModuleCard $sender.Tag})
            [void]$panel.Children.Add($card)
            if($i -lt $options.Count-1){
                $line=[Windows.Controls.TextBlock]::new();$line.Text='↓';$line.Foreground='#71859D';$line.HorizontalAlignment='Center';$line.FontSize=16;[void]$panel.Children.Add($line)
            }
        }
        if ($id -ne 'top' -and $id -notin @($options | ForEach-Object {$_.Id})) {
            $warning=[Windows.Controls.TextBlock]::new();$warning.Text=Get-UiText '저장된 부품을 찾지 못했어요. 다시 선택해 주세요.' 'Saved module is unavailable. Select another module.';$warning.Foreground='#FBBF24';$warning.TextWrapping='Wrap';[void]$panel.Children.Add($warning)
        }
    }
    $state.Summary.Text=Get-QueueModuleLabel $Entry
}

function Reset-ResearchModules {
    if ($null -ne $script:ActiveRunner -or $script:BatchActive) { return }
    $entry=$controls.QueueList.SelectedItem
    if($null -eq $entry){return}
    Initialize-QueueModules $entry
    $entry.HullUpgrade='top';$entry.ArtilleryUpgrade='top';$entry.TorpedoUpgrade='top'
    Update-QueueEntryDisplay $entry;$controls.QueueList.Items.Refresh();Update-QueueModuleControls
}

function Show-ResearchModulePicker {
    $entry=$controls.QueueList.SelectedItem
    if($null -eq $entry -or $null -ne $script:ActiveRunner -or $script:BatchActive){return}
    if(@(Get-QueueModuleCatalog $entry).Count -eq 0){return}
    $markup=@'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml" Width="740" Height="550" MinWidth="660" MinHeight="430" Background="#0B1726" Foreground="#F1F5F9" WindowStartupLocation="CenterOwner" ShowInTaskbar="False">
 <Window.Resources>
  <Style x:Key="ResearchCard" TargetType="RadioButton">
   <Setter Property="Margin" Value="0,4"/><Setter Property="HorizontalContentAlignment" Value="Stretch"/><Setter Property="Background" Value="#142338"/>
   <Setter Property="Template"><Setter.Value><ControlTemplate TargetType="RadioButton">
    <Border x:Name="Frame" BorderThickness="1" BorderBrush="#34465E" Background="{TemplateBinding Background}" CornerRadius="3" Padding="14,12">
     <Grid><ContentPresenter Margin="0,0,20,0"/><TextBlock x:Name="Check" Text="✓" FontWeight="Bold" Foreground="#E1C47C" FontSize="17" HorizontalAlignment="Right" VerticalAlignment="Top" Visibility="Collapsed"/></Grid>
    </Border>
    <ControlTemplate.Triggers>
     <Trigger Property="IsChecked" Value="True"><Setter TargetName="Frame" Property="BorderBrush" Value="#D7B971"/><Setter TargetName="Frame" Property="Background" Value="#263448"/><Setter TargetName="Check" Property="Visibility" Value="Visible"/></Trigger>
     <Trigger Property="IsMouseOver" Value="True"><Setter TargetName="Frame" Property="BorderBrush" Value="#E2D3AA"/></Trigger>
     <Trigger Property="IsKeyboardFocused" Value="True"><Setter TargetName="Frame" Property="BorderThickness" Value="2"/><Setter TargetName="Frame" Property="BorderBrush" Value="#F8FAFC"/></Trigger>
     <Trigger Property="IsEnabled" Value="False"><Setter Property="Opacity" Value="0.5"/></Trigger>
    </ControlTemplate.Triggers>
   </ControlTemplate></Setter.Value></Setter>
  </Style>
  <Style TargetType="Button"><Setter Property="Padding" Value="16,8"/><Setter Property="Background" Value="#23364E"/><Setter Property="Foreground" Value="#F1F5F9"/><Setter Property="BorderBrush" Value="#465A74"/></Style>
 </Window.Resources>
 <Grid Margin="24"><Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/><RowDefinition Height="*"/><RowDefinition Height="Auto"/></Grid.RowDefinitions>
  <TextBlock x:Name="ShipName" FontSize="22" FontWeight="SemiBold"/>
  <TextBlock x:Name="Help" Grid.Row="1" Margin="0,8,0,16" Foreground="#B9C6D6" TextWrapping="Wrap"/>
  <Grid Grid.Row="2"><Grid.ColumnDefinitions><ColumnDefinition Width="*"/><ColumnDefinition Width="16"/><ColumnDefinition Width="*"/><ColumnDefinition Width="16"/><ColumnDefinition Width="*"/></Grid.ColumnDefinitions>
   <DockPanel><TextBlock x:Name="HullTitle" DockPanel.Dock="Top" Margin="0,0,0,8" FontWeight="SemiBold"/><ScrollViewer VerticalScrollBarVisibility="Auto"><StackPanel x:Name="HullCards"/></ScrollViewer></DockPanel>
   <DockPanel Grid.Column="2"><TextBlock x:Name="GunTitle" DockPanel.Dock="Top" Margin="0,0,0,8" FontWeight="SemiBold"/><ScrollViewer VerticalScrollBarVisibility="Auto"><StackPanel x:Name="GunCards"/></ScrollViewer></DockPanel>
   <DockPanel Grid.Column="4"><TextBlock x:Name="TorpedoTitle" DockPanel.Dock="Top" Margin="0,0,0,8" FontWeight="SemiBold"/><ScrollViewer VerticalScrollBarVisibility="Auto"><StackPanel x:Name="TorpedoCards"/></ScrollViewer></DockPanel>
  </Grid>
  <StackPanel Grid.Row="3" Margin="0,14,0,0"><TextBlock x:Name="Summary" Margin="0,0,0,12" Foreground="#D7B971" TextWrapping="Wrap"/><DockPanel><Button x:Name="ResetTop" DockPanel.Dock="Left"/><Button x:Name="CloseDialog" IsCancel="True" HorizontalAlignment="Right"/></DockPanel></StackPanel>
 </Grid>
</Window>
'@
    $dialog=[Windows.Markup.XamlReader]::Parse($markup);$dialog.Owner=$window;$dialog.Title=Get-UiText '부품 연구 구성' 'Research modules'
    $dialog.FindName('ShipName').Text=[string]$entry.Ship.LocalizedName
    $dialog.FindName('Help').Text=Get-UiText '카드를 클릭하면 대기열에 바로 적용돼요. 대공포·부포는 선택한 선체 구성을 따라가요.' 'Click a module to apply it to the queue. AA and secondary mounts follow the selected hull.'
    $dialog.FindName('HullTitle').Text=Get-UiText '선체' 'HULL';$dialog.FindName('GunTitle').Text=Get-UiText '주함포' 'MAIN BATTERY';$dialog.FindName('TorpedoTitle').Text=Get-UiText '어뢰' 'TORPEDOES'
    $dialog.FindName('ResetTop').Content=Get-UiText '모두 최종 연구' 'Fully upgrade all';$dialog.FindName('CloseDialog').Content=Get-UiText '닫기' 'Close'
    $dialog.FindName('ResetTop').Add_Click({Reset-ResearchModules})
    $script:ResearchModuleDialog=@{Window=$dialog;Hull=$dialog.FindName('HullCards');Artillery=$dialog.FindName('GunCards');Torpedo=$dialog.FindName('TorpedoCards');Summary=$dialog.FindName('Summary')}
    try {Update-QueueModuleControls;[void]$dialog.ShowDialog()}
    finally {$script:ResearchModuleDialog=$null}
}
