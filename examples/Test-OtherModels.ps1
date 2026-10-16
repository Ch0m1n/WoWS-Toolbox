#requires -Version 5.1
param([string] $Screenshot)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
Add-Type -AssemblyName PresentationFramework,PresentationCore,WindowsBase
$root=Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'GUI/OtherModels.ps1')
function Get-UiText {param([string]$Ko,[string]$En) return $En}
function Get-SourceDisplay {param([string]$Source) return $Source}
$window=[Windows.Window]::new();$window.Width=1;$window.Height=1;$window.Opacity=0;$window.ShowInTaskbar=$false
$window.Show();$window.Hide()
$script:OtherModelsState=@{Source='pc';Game='C:\Game';Rows=@(
    [pscustomobject]@{Name='Fighter';Code='AAF001';Kind='aircraft';Nation='usa';Path='content/gameplay/usa/aircraft/fighter/A.geometry';Selected=$false},
    [pscustomobject]@{Name='Torpedo';Code='APT001';Kind='torpedo';Nation='usa';Path='content/gameplay/usa/projectile/torpedo/A.geometry';Selected=$false},
    [pscustomobject]@{Name='Shell';Code='CPA001';Kind='shell';Nation='common';Path='content/gameplay/common/projectile/artillery/A.geometry';Selected=$false}
);Grid=$null;Refresh=$false;OpenViewer=$false}
$script:OtherModelsTestError=$null
$timer=[Windows.Threading.DispatcherTimer]::new();$timer.Interval=[TimeSpan]::FromMilliseconds(350)
$timer.Add_Tick({
    try {
        $state=$script:OtherModelsState
        if($null -eq $state.Grid -or -not $state.Dialog.IsLoaded){return}
        if($state.Grid.Columns.Count -ne 6 -or $state.Grid.Items.Count -ne 3){throw 'Initial model table is incorrect'}
        $state.Search.Text='torpedo'
        if($state.Grid.Items.Count -ne 1){throw 'Search did not filter the table'}
        $click=[Windows.RoutedEventArgs]::new([Windows.Controls.Button]::ClickEvent)
        $state.Dialog.FindName('SelectVisible').RaiseEvent($click)
        $state.Search.Text=''
        $state.Kind.SelectedIndex=1
        $state.Dialog.FindName('SelectVisible').RaiseEvent([Windows.RoutedEventArgs]::new([Windows.Controls.Button]::ClickEvent))
        if(@($state.Rows | Where-Object Selected).Count -ne 2){throw 'Selection did not persist across filters'}
        $state.Dialog.FindName('Clear').RaiseEvent([Windows.RoutedEventArgs]::new([Windows.Controls.Button]::ClickEvent))
        if(@($state.Rows | Where-Object Selected).Count -ne 0){throw 'Clear selection failed'}
        $state.Dialog.FindName('Run').RaiseEvent([Windows.RoutedEventArgs]::new([Windows.Controls.Button]::ClickEvent))
        if(-not $state.Dialog.IsVisible -or $state.Status.Text -notmatch '1 and 200'){throw 'Empty selection was not blocked'}
        $state.Kind.SelectedIndex=0
        if($Screenshot){
            $state.Dialog.UpdateLayout()
            $bitmap=[Windows.Media.Imaging.RenderTargetBitmap]::new([int]$state.Dialog.ActualWidth,[int]$state.Dialog.ActualHeight,96,96,[Windows.Media.PixelFormats]::Pbgra32)
            $bitmap.Render($state.Dialog)
            $encoder=[Windows.Media.Imaging.PngBitmapEncoder]::new();$encoder.Frames.Add([Windows.Media.Imaging.BitmapFrame]::Create($bitmap))
            $stream=[IO.File]::Create($Screenshot);try{$encoder.Save($stream)}finally{$stream.Dispose()}
        }
    } catch {$script:OtherModelsTestError=$_.Exception.Message}
    finally {$timer.Stop();if($null -ne $script:OtherModelsState.Grid){$script:OtherModelsState.Dialog.DialogResult=$false}}
})
try{$timer.Start();Show-OtherModelPicker}finally{$timer.Stop();$window.Close()}
if($script:OtherModelsTestError){throw $script:OtherModelsTestError}
Write-Output '{"ok":true,"other_models_picker":true,"search":true,"selection_across_filters":true,"empty_selection_blocked":true}'
