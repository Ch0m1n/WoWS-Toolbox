#requires -Version 5.1
param([string] $Screenshot, [string] $FixtureJson)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Add-Type -AssemblyName PresentationFramework,PresentationCore,WindowsBase
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'GUI/ShipModules.ps1')
function Get-UiText { param($Ko,$En) return $En }
function Update-QueueEntryDisplay { param($Entry) $Entry.Display = Get-QueueModuleLabel $Entry }
$script:ActiveRunner = $null
$script:BatchActive = $false
$controls = @{}
foreach ($name in @('HullModuleCombo','ArtilleryModuleCombo','TorpedoModuleCombo')) {
    $controls[$name] = [Windows.Controls.ComboBox]::new()
    $controls[$name].DisplayMemberPath = 'Name'
    $controls[$name].Width = 235
    $controls[$name].Margin = '4'
}
$controls.QueueList = [Windows.Controls.ListBox]::new()
$controls.HullModuleCombo.Add_SelectionChanged({ Set-QueueModuleSelection 'HullUpgrade' 'HullModuleCombo' })
$controls.ArtilleryModuleCombo.Add_SelectionChanged({ Set-QueueModuleSelection 'ArtilleryUpgrade' 'ArtilleryModuleCombo' })
$controls.TorpedoModuleCombo.Add_SelectionChanged({ Set-QueueModuleSelection 'TorpedoUpgrade' 'TorpedoModuleCombo' })
$entry = [pscustomobject] @{Source='pc';Ship=[pscustomobject] @{LocalizedName='Charles Martel';Modules=[pscustomobject] @{Hulls=@(
    [pscustomobject] @{Id='Hull_A';Name='A';Artillery=@([pscustomobject]@{Id='Gun_A';Name='A'},[pscustomobject]@{Id='Gun_B';Name='B'});Torpedoes=@([pscustomobject]@{Id='Torp_A';Name='A'})},
    [pscustomobject] @{Id='Hull_B';Name='B';Artillery=@([pscustomobject]@{Id='Gun_A';Name='A'},[pscustomobject]@{Id='Gun_B';Name='B'});Torpedoes=@([pscustomobject]@{Id='Torp_B';Name='B'})}
)} };Display=''}
Update-QueueModuleControls
if ($controls.HullModuleCombo.IsEnabled) { throw 'Empty queue allowed module changes' }
[void]$controls.QueueList.Items.Add($entry)
$controls.QueueList.SelectedItem = $entry
Update-QueueModuleControls
if ($entry.HullUpgrade -ne 'top' -or $entry.ArtilleryUpgrade -ne 'top' -or $entry.TorpedoUpgrade -ne 'top') { throw 'Default must be fully researched' }
if (-not $controls.HullModuleCombo.IsEnabled -or -not $controls.ArtilleryModuleCombo.IsEnabled) { throw 'Multiple choices were disabled' }
$controls.ArtilleryModuleCombo.SelectedItem = @($controls.ArtilleryModuleCombo.ItemsSource | Where-Object {$_.Id -eq 'Gun_B'})[0]
$controls.HullModuleCombo.SelectedItem = @($controls.HullModuleCombo.ItemsSource | Where-Object {$_.Id -eq 'Hull_A'})[0]
if ($entry.HullUpgrade -ne 'Hull_A' -or $entry.ArtilleryUpgrade -ne 'Gun_B') { throw 'Hull switch discarded compatible researched guns' }
$entry.TorpedoUpgrade = 'Torp_A'
$controls.HullModuleCombo.SelectedItem = @($controls.HullModuleCombo.ItemsSource | Where-Object {$_.Id -eq 'Hull_B'})[0]
if ($entry.TorpedoUpgrade -ne 'top') { throw 'Incompatible torpedoes survived hull switch' }
$script:BatchActive = $true
Update-QueueModuleControls
if ($controls.HullModuleCombo.IsEnabled) { throw 'Busy queue was editable' }
$script:BatchActive = $false
Update-QueueModuleControls
$roundtrip = $entry | ConvertTo-Json -Depth 20 | ConvertFrom-Json
Initialize-QueueModules $roundtrip
if ($roundtrip.ArtilleryUpgrade -ne 'Gun_B') { throw 'Roundtrip lost research selection' }
$fixed = [pscustomobject] @{Source='legends';Ship=[pscustomobject]@{};Display=''}
[void]$controls.QueueList.Items.Add($fixed); $controls.QueueList.SelectedItem=$fixed
Update-QueueModuleControls
if ($controls.HullModuleCombo.IsEnabled -or $controls.ArtilleryModuleCombo.IsEnabled -or $controls.TorpedoModuleCombo.IsEnabled) { throw 'Fixed-source controls enabled' }
$controls.QueueList.SelectedItem=$entry
Update-QueueModuleControls
if ($FixtureJson) { $entry.Ship.Modules = Get-Content -Raw -LiteralPath $FixtureJson | ConvertFrom-Json }
Reset-ResearchModules
$window=[Windows.Window]::new();$window.Width=1;$window.Height=1;$window.Opacity=0;$window.ShowInTaskbar=$false;$window.Show();$window.Hide()
$script:ResearchTestError=$null
$script:ResearchTestTimer=[Windows.Threading.DispatcherTimer]::new();$script:ResearchTestTimer.Interval=[TimeSpan]::FromMilliseconds(250)
$script:ResearchTestTimer.Add_Tick({
    if($null -eq $script:ResearchModuleDialog -or -not $script:ResearchModuleDialog.Window.IsLoaded){return}
    try {
        $state=$script:ResearchModuleDialog
        $hulls=@($state.Hull.Children | Where-Object {$_ -is [Windows.Controls.RadioButton]})
        $guns=@($state.Artillery.Children | Where-Object {$_ -is [Windows.Controls.RadioButton]})
        if($hulls.Count -ne 2 -or $guns.Count -ne 2){throw 'Research card columns are incorrect'}
        if(-not $hulls[-1].IsChecked -or -not $guns[-1].IsChecked){throw 'Fully upgraded cards were not selected by default'}
        $hulls[0].RaiseEvent([Windows.RoutedEventArgs]::new([Windows.Controls.Primitives.ButtonBase]::ClickEvent))
        $hulls=@($state.Hull.Children | Where-Object {$_ -is [Windows.Controls.RadioButton]})
        $guns=@($state.Artillery.Children | Where-Object {$_ -is [Windows.Controls.RadioButton]})
        if(-not $hulls[0].IsChecked -or -not $guns[-1].IsChecked){throw 'Stock hull with researched guns did not survive card click'}
        $state.Window.FindName('ResetTop').RaiseEvent([Windows.RoutedEventArgs]::new([Windows.Controls.Primitives.ButtonBase]::ClickEvent))
        if($entry.HullUpgrade -ne 'top' -or $entry.ArtilleryUpgrade -ne 'top'){throw 'Fully-upgrade button did not restore defaults'}
        $hulls=@($state.Hull.Children | Where-Object {$_ -is [Windows.Controls.RadioButton]})
        $hulls[0].RaiseEvent([Windows.RoutedEventArgs]::new([Windows.Controls.Primitives.ButtonBase]::ClickEvent))
        if ($Screenshot) {
            $state.Window.UpdateLayout()
            $bitmap=[Windows.Media.Imaging.RenderTargetBitmap]::new([int]$state.Window.ActualWidth,[int]$state.Window.ActualHeight,96,96,[Windows.Media.PixelFormats]::Pbgra32)
            $bitmap.Render($state.Window);$encoder=[Windows.Media.Imaging.PngBitmapEncoder]::new();$encoder.Frames.Add([Windows.Media.Imaging.BitmapFrame]::Create($bitmap))
            $stream=[IO.File]::Create($Screenshot);try{$encoder.Save($stream)}finally{$stream.Dispose()}
        }
    } catch {$script:ResearchTestError=$_.Exception.Message}
    finally {$script:ResearchTestTimer.Stop();$script:ResearchModuleDialog.Window.DialogResult=$false}
})
try {$script:ResearchTestTimer.Start();Show-ResearchModulePicker} finally {$script:ResearchTestTimer.Stop();$window.Close()}
if($script:ResearchTestError){throw $script:ResearchTestError}
Write-Output '{"ok":true,"top_defaults":true,"hull_compatibility":true,"busy_state":true,"queue_roundtrip":true,"research_cards":true,"reset_all":true}'
