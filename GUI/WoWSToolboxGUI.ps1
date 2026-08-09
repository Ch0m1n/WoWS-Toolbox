#requires -Version 5.1

[CmdletBinding()]
param(
    [switch] $SelfTest,
    [switch] $SmokeTest,
    [switch] $QueueSelfTest,
    [ValidateSet('', 'legends', 'pc', 'korabli')]
    [string] $CatalogTestSource = '',
    [string] $ViewerTestModel = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'WoWS Toolbox GUI는 Windows 10/11에서 실행해 주세요.'
}

function Get-PreferredPowerShellHost {
    foreach ($candidate in @(
        (Join-Path $env:ProgramFiles 'PowerShell\7\pwsh.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\PowerShell\7\pwsh.exe')
    )) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }

    $command = Get-Command pwsh.exe -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -ne $command -and
        $command.Source -notlike '*\Microsoft\WindowsApps\pwsh.exe') {
        return $command.Source
    }

    $command = Get-Command powershell.exe -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -ne $command) { return $command.Source }

    $windowsPowerShell = Join-Path $env:SystemRoot `
        'System32\WindowsPowerShell\v1.0\powershell.exe'
    if (Test-Path -LiteralPath $windowsPowerShell -PathType Leaf) {
        return $windowsPowerShell
    }
    throw 'PowerShell 7 또는 Windows PowerShell 5.1을 찾지 못했어요.'
}
$powerShellCommand = Get-PreferredPowerShellHost
if ([Threading.Thread]::CurrentThread.ApartmentState -ne 'STA') {
    $relaunchArguments = @(
        '-STA', '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', $PSCommandPath
    )
    if ($SelfTest) { $relaunchArguments += '-SelfTest' }
    if ($SmokeTest) { $relaunchArguments += '-SmokeTest' }
    if ($QueueSelfTest) { $relaunchArguments += '-QueueSelfTest' }
    if (-not [string]::IsNullOrWhiteSpace($CatalogTestSource)) {
        $relaunchArguments += @('-CatalogTestSource', $CatalogTestSource)
    }
    if (-not [string]::IsNullOrWhiteSpace($ViewerTestModel)) {
        $relaunchArguments += @('-ViewerTestModel', $ViewerTestModel)
    }
    & $powerShellCommand @relaunchArguments
    exit $LASTEXITCODE
}

Add-Type -AssemblyName PresentationCore
Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName WindowsBase
Add-Type -AssemblyName System.Xaml
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Net.Http

if (-not ('WoWSToolboxV5.GuiProcessRunner' -as [type])) {
    $runnerSource = @"
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;`nusing System.IO;
using System.Text;

namespace WoWSToolboxV5
{
    public sealed class ProcessLine
    {
        public bool IsError { get; private set; }
        public string Text { get; private set; }
        public ProcessLine(bool isError, string text)
        {
            IsError = isError;
            Text = text ?? string.Empty;
        }
    }

    public sealed class GuiProcessRunner : IDisposable
    {
        private readonly ConcurrentQueue<ProcessLine> _lines =
            new ConcurrentQueue<ProcessLine>();
        private Process _process;
        private volatile bool _stdoutClosed;
        private volatile bool _stderrClosed;

        public bool IsComplete
        {
            get
            {
                if (_process == null) return false;
                try
                {
                    return _process.HasExited && _stdoutClosed && _stderrClosed;
                }
                catch (InvalidOperationException)
                {
                    return false;
                }
            }
        }

        public int ExitCode
        {
            get
            {
                if (!IsComplete)
                    throw new InvalidOperationException("Process is not complete.");
                return _process.ExitCode;
            }
        }

        public void Start(string fileName, string[] arguments, string workingDirectory)
        {
            if (_process != null)
                throw new InvalidOperationException("Runner is already active.");

            var info = new ProcessStartInfo
            {
                FileName = fileName,
                WorkingDirectory = string.IsNullOrWhiteSpace(workingDirectory)
                    ? Environment.CurrentDirectory : workingDirectory,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                StandardOutputEncoding = new UTF8Encoding(false),
                StandardErrorEncoding = new UTF8Encoding(false)
            };
            info.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1";
            info.EnvironmentVariables["PYTHONUTF8"] = "1";
            info.Arguments = BuildArguments(arguments ?? new string[0]);

            _process = new Process { StartInfo = info };
            _process.OutputDataReceived += (sender, args) =>
            {
                if (args.Data == null) _stdoutClosed = true;
                else _lines.Enqueue(new ProcessLine(false, args.Data));
            };
            _process.ErrorDataReceived += (sender, args) =>
            {
                if (args.Data == null) _stderrClosed = true;
                else _lines.Enqueue(new ProcessLine(true, args.Data));
            };
            if (!_process.Start())
                throw new InvalidOperationException("Process failed to start.");
            _process.BeginOutputReadLine();
            _process.BeginErrorReadLine();
        }

        private static string BuildArguments(string[] arguments)
        {
            var result = new StringBuilder();
            foreach (string argument in arguments)
            {
                if (result.Length > 0) result.Append(' ');
                result.Append(QuoteArgument(argument ?? string.Empty));
            }
            return result.ToString();
        }

        private static string QuoteArgument(string argument)
        {
            if (argument.Length == 0) return "\"\"";
            if (argument.IndexOfAny(new char[] { ' ', '\t', '\n', '\v', '"' }) < 0)
                return argument;

            var result = new StringBuilder();
            result.Append('"');
            int backslashes = 0;
            foreach (char character in argument)
            {
                if (character == '/')
                {
                    backslashes++;
                    continue;
                }
                if (character == '"')
                {
                    result.Append('/', backslashes * 2 + 1);
                    result.Append('"');
                    backslashes = 0;
                    continue;
                }
                result.Append('/', backslashes);
                result.Append(character);
                backslashes = 0;
            }
            result.Append('/', backslashes * 2);
            result.Append('"');
            return result.ToString();
        }

        public ProcessLine[] Drain()
        {
            var result = new List<ProcessLine>();
            ProcessLine line;
            while (_lines.TryDequeue(out line)) result.Add(line);
            return result.ToArray();
        }

        public void CancelTree()
        {
            if (_process == null) return;
            try
            {
                if (_process.HasExited) return;
                var taskkill = new ProcessStartInfo
                {
                    FileName = Path.Combine(
                        Environment.GetFolderPath(Environment.SpecialFolder.System),
                        "taskkill.exe"
                    ),
                    Arguments = "/PID " + _process.Id + " /T /F",
                    UseShellExecute = false,
                    CreateNoWindow = true
                };
                using (var killer = Process.Start(taskkill))
                {
                    if (killer != null) killer.WaitForExit(5000);
                }
                if (!_process.HasExited) _process.Kill();
            }
            catch (Exception exception)
            {
                _lines.Enqueue(new ProcessLine(
                    true, "Cancel failed: " + exception.Message
                ));
            }
        }

        public void Dispose()
        {
            if (_process == null) return;
            _process.Dispose();
            _process = null;
        }
    }
}
"@
    Add-Type -TypeDefinition $runnerSource -Language CSharp
}

if (-not ('WoWSToolboxV5.WindowActivator' -as [type])) {
    $windowActivatorSource = @"
using System;
using System.Runtime.InteropServices;
using System.Text;

namespace WoWSToolboxV5
{
    public static class WindowActivator
    {
        private delegate bool EnumWindowsProc(IntPtr handle, IntPtr parameter);

        [DllImport("user32.dll")]
        private static extern bool EnumWindows(EnumWindowsProc callback, IntPtr parameter);

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern int GetWindowText(IntPtr handle, StringBuilder text, int maxCount);

        [DllImport("user32.dll")]
        private static extern bool IsWindowVisible(IntPtr handle);

        [DllImport("user32.dll")]
        private static extern bool ShowWindowAsync(IntPtr handle, int command);

        [DllImport("user32.dll")]
        private static extern bool SetForegroundWindow(IntPtr handle);

        public static bool Activate(string exactTitle)
        {
            bool activated = false;
            EnumWindows(delegate(IntPtr handle, IntPtr parameter)
            {
                if (!IsWindowVisible(handle)) return true;
                StringBuilder title = new StringBuilder(256);
                GetWindowText(handle, title, title.Capacity);
                if (!string.Equals(title.ToString(), exactTitle, StringComparison.Ordinal))
                    return true;
                ShowWindowAsync(handle, 9);
                SetForegroundWindow(handle);
                activated = true;
                return false;
            }, IntPtr.Zero);
            return activated;
        }
    }
}
"@
    Add-Type -TypeDefinition $windowActivatorSource -Language CSharp
}

$automatedMode =
    $SelfTest -or $SmokeTest -or $QueueSelfTest -or
    -not [string]::IsNullOrWhiteSpace($CatalogTestSource) -or
    -not [string]::IsNullOrWhiteSpace($ViewerTestModel)
$script:InstanceMutex = $null
if (-not $automatedMode) {
    $mutexCreated = $false
    $script:InstanceMutex = [Threading.Mutex]::new(
        $true, 'Local\WoWSToolbox.Gui.v1', [ref] $mutexCreated
    )
    if (-not $mutexCreated) {
        [void] [WoWSToolboxV5.WindowActivator]::Activate('WoWS Toolbox')
        $script:InstanceMutex.Dispose()
        exit 0
    }
}

$script:PackageRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$script:AppVersion = '5.0.31'
$script:UpdateApiUrl = 'https://api.github.com/repos/Ch0m1n/WoWS-Toolbox/releases/latest'
$localizationScript = Join-Path $PSScriptRoot 'Localization.ps1'
if (-not (Test-Path -LiteralPath $localizationScript -PathType Leaf)) {
    throw "Localization module is missing: $localizationScript"
}
. $localizationScript
$script:InstallerLanguage = Get-WoWSToolboxLanguageMarker -PackageRoot $script:PackageRoot
$script:ViewerRoot = Join-Path $script:PackageRoot 'Viewer'
$script:ViewerRuntimeRoot = Join-Path $script:ViewerRoot 'Runtime'
$script:ViewerWebRoot = Join-Path $script:ViewerRoot 'web'
$viewerCoreDll = Join-Path $script:ViewerRuntimeRoot 'Microsoft.Web.WebView2.Core.dll'
$viewerWpfDll = Join-Path $script:ViewerRuntimeRoot 'Microsoft.Web.WebView2.Wpf.dll'
$viewerLoaderDll = Join-Path $script:ViewerRuntimeRoot 'WebView2Loader.dll'
foreach ($viewerDependency in @($viewerCoreDll, $viewerWpfDll, $viewerLoaderDll)) {
    if (-not (Test-Path -LiteralPath $viewerDependency -PathType Leaf)) {
        throw "3D 뷰어 런타임 파일이 없어요: $viewerDependency"
    }
}
$env:PATH = "$($script:ViewerRuntimeRoot);$env:PATH"
Add-Type -Path $viewerCoreDll
Add-Type -Path $viewerWpfDll
$script:BackendRoot = Join-Path $script:PackageRoot 'Backend'
$script:CatalogScript = Join-Path $script:BackendRoot 'catalog.py'
$script:ExtractScript = Join-Path $script:BackendRoot 'extract_ship.py'
$script:BatchExtractScript = Join-Path $script:BackendRoot 'batch_extract.py'
$script:BundledPythonCommand = Join-Path $script:PackageRoot 'Runtime\Python\python.exe'
$script:PythonCommand = if (Test-Path -LiteralPath $script:BundledPythonCommand -PathType Leaf) {
    $script:BundledPythonCommand
}
else {
    (Get-Command python -ErrorAction Stop).Source
}
$script:StateRoot = if ($SelfTest -or $SmokeTest -or $QueueSelfTest -or $CatalogTestSource -or $ViewerTestModel) {
    Join-Path ([IO.Path]::GetTempPath()) "WoWSToolbox-SelfTest-$PID"
}
else {
    Join-Path $env:LOCALAPPDATA 'WoWSToolbox'
}
$script:CatalogRoot = Join-Path $script:StateRoot 'Catalog'
$script:UpdateRoot = Join-Path $script:StateRoot 'Updates'
$script:SettingsPath = Join-Path $script:StateRoot 'settings.json'
[IO.Directory]::CreateDirectory($script:CatalogRoot) | Out-Null

function Write-TextAtomic {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [AllowEmptyString()] [string] $Text
    )
    $parent = Split-Path -Parent $Path
    if (-not [string]::IsNullOrWhiteSpace($parent)) {
        [IO.Directory]::CreateDirectory($parent) | Out-Null
    }
    $temporaryPath = "$Path.$PID.$([Guid]::NewGuid().ToString('N')).tmp"
    try {
        [IO.File]::WriteAllText(
            $temporaryPath, $Text, [Text.UTF8Encoding]::new($false)
        )
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
            try {
                [IO.File]::Replace($temporaryPath, $Path, $null)
            }
            catch {
                Move-Item -LiteralPath $temporaryPath -Destination $Path -Force
            }
        }
        else {
            [IO.File]::Move($temporaryPath, $Path)
        }
    }
    finally {
        if (Test-Path -LiteralPath $temporaryPath -PathType Leaf) {
            Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
        }
    }
}

function Write-JsonAtomic {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] $Value,
        [int] $Depth = 12,
        [switch] $Compress
    )
    $json = if ($Compress) {
        $Value | ConvertTo-Json -Depth $Depth -Compress
    }
    else {
        $Value | ConvertTo-Json -Depth $Depth
    }
    Write-TextAtomic -Path $Path -Text $json
}

function ConvertTo-WoWSToolboxVersion {
    param([Parameter(Mandatory)] [string] $Value)
    $normalized = $Value.Trim().TrimStart([char[]] @('v', 'V'))
    $normalized = ($normalized -split '[-+]', 2)[0]
    $parsed = $null
    if (-not [Version]::TryParse($normalized, [ref] $parsed)) {
        throw "지원하지 않는 버전 형식이에요: $Value"
    }
    return $parsed
}

function ConvertFrom-WoWSToolboxReleaseJson {
    param(
        [Parameter(Mandatory)] [string] $Json,
        [Parameter(Mandatory)] [string] $CurrentVersion
    )
    $release = $Json | ConvertFrom-Json -ErrorAction Stop
    if ([bool] $release.draft -or [bool] $release.prerelease) {
        throw '정식 GitHub 릴리스가 아니에요.'
    }
    $current = ConvertTo-WoWSToolboxVersion $CurrentVersion
    $latest = ConvertTo-WoWSToolboxVersion ([string] $release.tag_name)
    if ($latest -le $current) {
        return [pscustomobject] @{
            UpdateAvailable = $false
            CurrentVersion = $current.ToString()
            Version = $latest.ToString()
            TagName = [string] $release.tag_name
        }
    }

    $versionText = $latest.ToString()
    $installerName = "WoWS-Toolbox-Setup-$versionText.exe"
    $asset = @($release.assets | Where-Object {
        [string] $_.name -ceq $installerName
    } | Select-Object -First 1)
    if ($asset.Count -eq 0) { $asset = $null } else { $asset = $asset[0] }
    $installerUrl = if ($null -eq $asset) { '' } else { [string] $asset.browser_download_url }
    $digest = if ($null -eq $asset -or $null -eq $asset.PSObject.Properties['digest']) {
        ''
    }
    else { [string] $asset.digest }
    $sha256 = if ($digest -match '(?i)^sha256:([0-9a-f]{64})$') {
        $Matches[1].ToUpperInvariant()
    }
    else { '' }
    $installerUri = $null
    $safeInstallerUrl = [Uri]::TryCreate(
        $installerUrl, [UriKind]::Absolute, [ref] $installerUri
    ) -and $installerUri.Scheme -eq 'https' -and
        $installerUri.Host -eq 'github.com'
    $releaseUrl = [string] $release.html_url
    $releaseUri = $null
    $safeReleaseUrl = [Uri]::TryCreate(
        $releaseUrl, [UriKind]::Absolute, [ref] $releaseUri
    ) -and $releaseUri.Scheme -eq 'https' -and
        $releaseUri.Host -eq 'github.com'

    return [pscustomobject] @{
        UpdateAvailable = $true
        CurrentVersion = $current.ToString()
        Version = $versionText
        TagName = [string] $release.tag_name
        Name = [string] $release.name
        ReleaseUrl = if ($safeReleaseUrl) { $releaseUrl } else { '' }
        InstallerUrl = if ($safeInstallerUrl) { $installerUrl } else { '' }
        InstallerName = $installerName
        Sha256 = $sha256
        Installable = $null -ne $asset -and $safeInstallerUrl -and
            -not [string]::IsNullOrWhiteSpace($sha256)
    }
}

$documents = [Environment]::GetFolderPath(
    [Environment+SpecialFolder]::MyDocuments
)
$legacyDefaultOutputPath = Join-Path $documents 'WoWS-Exports'
$programDefaultOutputPath = Join-Path $script:PackageRoot 'output'
function Test-DeprecatedPackagedOutputPath {
    param([string] $Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
    try {
        $fullPath = [IO.Path]::GetFullPath($Path).TrimEnd([char[]] @('\', '/'))
        return $fullPath -match (
            '(?i)[\\/](?:outputs[\\/])?' +
            'WoWS-Toolbox-v\d+(?:\.\d+){2}(?:[^\\/]*)?[\\/]output$'
        )
    }
    catch { return $false }
}
$defaultSettings = [ordered] @{
    SettingsSchema = '2'
    LegendsPath = 'D:\SteamLibrary\steamapps\common\World of Warships Legends'
    PcPath = 'D:\Games\World_of_Warships'
    KorabliPath = 'D:\Games\Korabli'
    OutputPath = $programDefaultOutputPath
    OodlePath = ''
    Language = 'en'
    Formats = 'obj'
    TextureMaxSize = '0'
    Lod = '0'
    NotifyComplete = 'true'
    AutoCheckUpdates = 'true'
}
$script:Settings = [ordered] @{}
$script:SettingsRecoveryNotice = ''
$savedSettingsSchema = 0
foreach ($pair in $defaultSettings.GetEnumerator()) {
    $script:Settings[$pair.Key] = $pair.Value
}
if (Test-Path -LiteralPath $script:SettingsPath -PathType Leaf) {
    try {
        $saved = Get-Content -Raw -LiteralPath $script:SettingsPath |
            ConvertFrom-Json -ErrorAction Stop
        if ($null -ne $saved.PSObject.Properties['SettingsSchema']) {
            [void] [int]::TryParse(
                [string] $saved.SettingsSchema,
                [ref] $savedSettingsSchema
            )
        }
        foreach ($key in @($defaultSettings.Keys)) {
            if ($null -ne $saved.PSObject.Properties[$key]) {
                $script:Settings[$key] = [string] $saved.$key
            }
        }
    }
    catch {
        $invalidName = 'settings.invalid-{0:yyyyMMdd-HHmmss}-{1}.json' -f (
            Get-Date
        ), ([Guid]::NewGuid().ToString('N').Substring(0, 8))
        $invalidPath = Join-Path $script:StateRoot $invalidName
        try {
            [IO.File]::Copy($script:SettingsPath, $invalidPath, $false)
            $script:SettingsRecoveryNotice =
                "Recovered malformed settings: $invalidPath"
        }
        catch {
            $script:SettingsRecoveryNotice =
                'Recovered malformed settings with safe defaults.'
        }
    }
}
$settingsQualityMigrated = $savedSettingsSchema -lt 2
if ($settingsQualityMigrated) {
    # Releases before schema 2 silently defaulted to 2K textures. Move that
    # one-time default to the lossless profile; later choices are preserved.
    $script:Settings.TextureMaxSize = '0'
    $script:Settings.Lod = '0'
    $script:Settings.SettingsSchema = '2'
}
if ($script:InstallerLanguage -in @('ko', 'en')) {
    $script:Settings.Language = $script:InstallerLanguage
}
Set-WoWSToolboxLanguage ([string] $script:Settings.Language)

$settingsOutputMigrated = $false
try {
    $currentOutputFull = [IO.Path]::GetFullPath(
        [string] $script:Settings.OutputPath
    ).TrimEnd([char[]] @('\', '/'))
    $legacyOutputFull = [IO.Path]::GetFullPath(
        $legacyDefaultOutputPath
    ).TrimEnd([char[]] @('\', '/'))
    $programOutputFull = [IO.Path]::GetFullPath(
        $programDefaultOutputPath
    ).TrimEnd([char[]] @('\', '/'))
    if ([StringComparer]::OrdinalIgnoreCase.Equals(
        $currentOutputFull, $legacyOutputFull
    ) -or (
        -not [StringComparer]::OrdinalIgnoreCase.Equals(
            $currentOutputFull, $programOutputFull
        ) -and
        (Test-DeprecatedPackagedOutputPath $currentOutputFull)
    )) {
        $script:Settings.OutputPath = $programDefaultOutputPath
        $settingsOutputMigrated = $true
    }
}
catch {
    # Invalid custom paths are reported by the existing settings validator.
}
if (($settingsOutputMigrated -or $settingsQualityMigrated) -and -not $automatedMode) {
    Write-JsonAtomic -Path $script:SettingsPath -Value $script:Settings -Depth 4
}
if (-not $automatedMode) {
    try {
        [IO.Directory]::CreateDirectory(
            [string] $script:Settings.OutputPath
        ) | Out-Null
    }
    catch {
        # Extraction readiness shows a concrete permission error if needed.
    }
}

$xaml = @'
<Window
    xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
    xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
    xmlns:wv2="clr-namespace:Microsoft.Web.WebView2.Wpf;assembly=Microsoft.Web.WebView2.Wpf"
    x:Name="RootWindow"
    Title="WoWS Toolbox"
    Width="1260" Height="820" MinWidth="1060" MinHeight="720"
    WindowStartupLocation="CenterScreen"
    Background="#08111F" Foreground="#E8EEF8"
    FontFamily="Segoe UI" FontSize="13">
    <Window.Resources>
        <SolidColorBrush x:Key="PanelBrush" Color="#0E1A2C"/>
        <SolidColorBrush x:Key="CardBrush" Color="#122139"/>
        <SolidColorBrush x:Key="CardAltBrush" Color="#0B1728"/>
        <SolidColorBrush x:Key="BorderBrush" Color="#223653"/>
        <SolidColorBrush x:Key="MutedBrush" Color="#8FA2BC"/>
        <SolidColorBrush x:Key="AccentBrush" Color="#3B82F6"/>
        <SolidColorBrush x:Key="AccentHoverBrush" Color="#5A99F8"/>
        <Style TargetType="TextBox">
            <Setter Property="Background" Value="#091525"/>
            <Setter Property="Foreground" Value="#E8EEF8"/>
            <Setter Property="BorderBrush" Value="#2A3E5B"/>
            <Setter Property="BorderThickness" Value="1"/>
            <Setter Property="Padding" Value="10,7"/>
            <Setter Property="CaretBrush" Value="#E8EEF8"/>
            <Setter Property="VerticalContentAlignment" Value="Center"/>
        </Style>
        <Style TargetType="ComboBoxItem">
            <Setter Property="Background" Value="#0B1728"/>
            <Setter Property="Foreground" Value="#DDE8F7"/>
            <Setter Property="Padding" Value="10,8"/>
            <Setter Property="HorizontalContentAlignment" Value="Stretch"/>
            <Style.Triggers>
                <Trigger Property="IsHighlighted" Value="True">
                    <Setter Property="Background" Value="#1D3D68"/>
                    <Setter Property="Foreground" Value="#FFFFFF"/>
                </Trigger>
                <Trigger Property="IsSelected" Value="True">
                    <Setter Property="Background" Value="#245FAD"/>
                    <Setter Property="Foreground" Value="#FFFFFF"/>
                    <Setter Property="FontWeight" Value="SemiBold"/>
                </Trigger>
            </Style.Triggers>
        </Style>
        <Style TargetType="ComboBox">
            <Setter Property="Background" Value="#13233B"/>
            <Setter Property="Foreground" Value="#E8EEF8"/>
            <Setter Property="BorderBrush" Value="#2A3E5B"/>
            <Setter Property="BorderThickness" Value="1"/>
            <Setter Property="Padding" Value="10,6"/>
            <Setter Property="MinHeight" Value="36"/>
            <Setter Property="SnapsToDevicePixels" Value="True"/>
            <Setter Property="Template">
                <Setter.Value>
                    <ControlTemplate TargetType="ComboBox">
                        <Grid x:Name="ComboRoot">
                            <Border x:Name="ComboBorder"
                                    Background="{TemplateBinding Background}"
                                    BorderBrush="{TemplateBinding BorderBrush}"
                                    BorderThickness="{TemplateBinding BorderThickness}"
                                    CornerRadius="7"/>
                            <ToggleButton x:Name="DropDownToggle"
                                          Background="Transparent"
                                          BorderThickness="0"
                                          Focusable="False"
                                          ClickMode="Press"
                                          IsChecked="{Binding IsDropDownOpen, Mode=TwoWay,
                                              RelativeSource={RelativeSource TemplatedParent}}">
                                <ToggleButton.Template>
                                    <ControlTemplate TargetType="ToggleButton">
                                        <Border Background="Transparent">
                                            <Grid>
                                                <Grid.ColumnDefinitions>
                                                    <ColumnDefinition Width="*"/>
                                                    <ColumnDefinition Width="28"/>
                                                </Grid.ColumnDefinitions>
                                                <ContentPresenter
                                                    Margin="{TemplateBinding Padding}"
                                                    HorizontalAlignment="Left"
                                                    VerticalAlignment="Center"
                                                    Content="{Binding SelectionBoxItem,
                                                        RelativeSource={RelativeSource AncestorType={x:Type ComboBox}}}"
                                                    ContentTemplate="{Binding SelectionBoxItemTemplate,
                                                        RelativeSource={RelativeSource AncestorType={x:Type ComboBox}}}"
                                                    TextElement.Foreground="{Binding Foreground,
                                                        RelativeSource={RelativeSource AncestorType={x:Type ComboBox}}}"/>
                                                <Path Grid.Column="1"
                                                      Width="8" Height="5"
                                                      HorizontalAlignment="Center"
                                                      VerticalAlignment="Center"
                                                      Fill="#AFC2DA"
                                                      Data="M 0 0 L 8 0 L 4 5 Z"/>
                                            </Grid>
                                        </Border>
                                    </ControlTemplate>
                                </ToggleButton.Template>
                            </ToggleButton>
                            <Popup x:Name="PART_Popup"
                                   Placement="Bottom"
                                   AllowsTransparency="True"
                                   Focusable="False"
                                   IsOpen="{TemplateBinding IsDropDownOpen}"
                                   PopupAnimation="Fade">
                                <Border MinWidth="{TemplateBinding ActualWidth}"
                                        MaxHeight="360"
                                        Margin="0,4,0,0"
                                        Background="#0B1728"
                                        BorderBrush="#365273"
                                        BorderThickness="1"
                                        CornerRadius="8">
                                    <ScrollViewer Margin="3"
                                                  VerticalScrollBarVisibility="Auto">
                                        <ItemsPresenter
                                            KeyboardNavigation.DirectionalNavigation="Contained"/>
                                    </ScrollViewer>
                                </Border>
                            </Popup>
                        </Grid>
                        <ControlTemplate.Triggers>
                            <Trigger Property="IsMouseOver" Value="True">
                                <Setter TargetName="ComboBorder"
                                        Property="BorderBrush" Value="#4B73A2"/>
                                <Setter TargetName="ComboBorder"
                                        Property="Background" Value="#172A45"/>
                            </Trigger>
                            <Trigger Property="IsKeyboardFocusWithin" Value="True">
                                <Setter TargetName="ComboBorder"
                                        Property="BorderBrush" Value="#5C9CFF"/>
                            </Trigger>
                            <Trigger Property="IsEnabled" Value="False">
                                <Setter TargetName="ComboRoot"
                                        Property="Opacity" Value=".45"/>
                            </Trigger>
                        </ControlTemplate.Triggers>
                    </ControlTemplate>
                </Setter.Value>
            </Setter>
        </Style>
        <Style TargetType="Button">
            <Setter Property="Background" Value="#172943"/>
            <Setter Property="Foreground" Value="#E8EEF8"/>
            <Setter Property="BorderBrush" Value="#2A4264"/>
            <Setter Property="BorderThickness" Value="1"/>
            <Setter Property="Padding" Value="16,9"/>
            <Setter Property="Cursor" Value="Hand"/>
            <Setter Property="FontWeight" Value="SemiBold"/>
            <Setter Property="Template">
                <Setter.Value>
                    <ControlTemplate TargetType="Button">
                        <Border x:Name="ButtonBorder"
                                Background="{TemplateBinding Background}"
                                BorderBrush="{TemplateBinding BorderBrush}"
                                BorderThickness="{TemplateBinding BorderThickness}"
                                CornerRadius="8">
                            <ContentPresenter
                                HorizontalAlignment="Center"
                                VerticalAlignment="Center"
                                Margin="{TemplateBinding Padding}"/>
                        </Border>
                        <ControlTemplate.Triggers>
                            <Trigger Property="IsMouseOver" Value="True">
                                <Setter TargetName="ButtonBorder"
                                        Property="Background" Value="#203755"/>
                            </Trigger>
                            <Trigger Property="IsEnabled" Value="False">
                                <Setter TargetName="ButtonBorder" Property="Opacity" Value=".42"/>
                            </Trigger>
                        </ControlTemplate.Triggers>
                    </ControlTemplate>
                </Setter.Value>
            </Setter>
        </Style>
        <Style x:Key="PrimaryButton" TargetType="Button" BasedOn="{StaticResource {x:Type Button}}">
            <Setter Property="Background" Value="#2563EB"/>
            <Setter Property="BorderBrush" Value="#4A8BFF"/>
        </Style>
        <Style x:Key="QuietButton" TargetType="Button" BasedOn="{StaticResource {x:Type Button}}">
            <Setter Property="Background" Value="Transparent"/>
        </Style>
        <Style x:Key="NavRadio" TargetType="RadioButton">
            <Setter Property="Foreground" Value="#9FB0C7"/>
            <Setter Property="Background" Value="Transparent"/>
            <Setter Property="Padding" Value="16,12"/>
            <Setter Property="Margin" Value="0,3"/>
            <Setter Property="Cursor" Value="Hand"/>
            <Setter Property="Template">
                <Setter.Value>
                    <ControlTemplate TargetType="RadioButton">
                        <Border x:Name="NavBorder"
                                Background="{TemplateBinding Background}"
                                CornerRadius="9">
                            <ContentPresenter
                                Margin="{TemplateBinding Padding}"
                                VerticalAlignment="Center"/>
                        </Border>
                        <ControlTemplate.Triggers>
                            <Trigger Property="IsMouseOver" Value="True">
                                <Setter TargetName="NavBorder" Property="Background" Value="#13233A"/>
                            </Trigger>
                            <Trigger Property="IsChecked" Value="True">
                                <Setter TargetName="NavBorder" Property="Background" Value="#17345D"/>
                                <Setter Property="Foreground" Value="#F3F7FD"/>
                                <Setter Property="FontWeight" Value="SemiBold"/>
                            </Trigger>
                        </ControlTemplate.Triggers>
                    </ControlTemplate>
                </Setter.Value>
            </Setter>
        </Style>
        <Style x:Key="CardBorder" TargetType="Border">
            <Setter Property="Background" Value="{StaticResource CardBrush}"/>
            <Setter Property="BorderBrush" Value="{StaticResource BorderBrush}"/>
            <Setter Property="BorderThickness" Value="1"/>
            <Setter Property="CornerRadius" Value="12"/>
            <Setter Property="Padding" Value="20"/>
        </Style>
        <Style TargetType="ProgressBar">
            <Setter Property="Height" Value="7"/>
            <Setter Property="Background" Value="#17253A"/>
            <Setter Property="Foreground" Value="#3B82F6"/>
            <Setter Property="BorderThickness" Value="0"/>
        </Style>
        <Style TargetType="DataGrid">
            <Setter Property="Background" Value="#0A1525"/>
            <Setter Property="Foreground" Value="#E4EBF5"/>
            <Setter Property="BorderBrush" Value="#263B59"/>
            <Setter Property="GridLinesVisibility" Value="Horizontal"/>
            <Setter Property="HorizontalGridLinesBrush" Value="#1C2D46"/>
            <Setter Property="RowBackground" Value="#0D1A2C"/>
            <Setter Property="AlternatingRowBackground" Value="#101F34"/>
            <Setter Property="HeadersVisibility" Value="Column"/>
            <Setter Property="CanUserAddRows" Value="False"/>
            <Setter Property="CanUserDeleteRows" Value="False"/>
            <Setter Property="IsReadOnly" Value="True"/>
            <Setter Property="SelectionMode" Value="Single"/>
        </Style>
    </Window.Resources>

    <Grid>
        <Grid.ColumnDefinitions>
            <ColumnDefinition Width="218"/>
            <ColumnDefinition Width="*"/>
        </Grid.ColumnDefinitions>

        <Border Grid.Column="0" Background="#0B1728"
                BorderBrush="#1D2D45" BorderThickness="0,0,1,0">
            <Grid Margin="18,22">
                <Grid.RowDefinitions>
                    <RowDefinition Height="76"/>
                    <RowDefinition Height="*"/>
                    <RowDefinition Height="Auto"/>
                </Grid.RowDefinitions>
                <StackPanel>
                    <TextBlock Text="WoWS Toolbox" FontSize="21" FontWeight="Bold"/>
                    <TextBlock Text="WOWS MODEL TOOLBOX" Foreground="#60A5FA"
                               FontSize="10" FontWeight="SemiBold"/>
                </StackPanel>
                <StackPanel Grid.Row="1">
                    <RadioButton x:Name="NavExtract" Style="{StaticResource NavRadio}"
                                 Content="함선 추출" IsChecked="True"/>
                    <RadioButton x:Name="NavViewer" Style="{StaticResource NavRadio}"
                                 Content="3D 모델 뷰어"/>
                    <RadioButton x:Name="NavSettings" Style="{StaticResource NavRadio}"
                                 Content="설정"/>
                </StackPanel>
                <StackPanel Grid.Row="2">
                    <TextBlock Text="대기열 추출 · 파트별 모델"
                               Foreground="#71849F" FontSize="11"/>
                    <TextBlock x:Name="FooterVersion" Text="v5.0.31"
                               Foreground="#536780" FontSize="11" Margin="0,4,0,0"/>
                </StackPanel>
            </Grid>
        </Border>

        <Grid Grid.Column="1">
            <Grid.RowDefinitions>
                <RowDefinition Height="68"/>
                <RowDefinition Height="*"/>
            </Grid.RowDefinitions>
            <Border Background="#0A1525" BorderBrush="#1D2D45"
                    BorderThickness="0,0,0,1">
                <Grid Margin="30,0">
                    <Grid.ColumnDefinitions>
                        <ColumnDefinition Width="*"/>
                        <ColumnDefinition Width="Auto"/>
                    </Grid.ColumnDefinitions>
                    <StackPanel VerticalAlignment="Center">
                        <TextBlock x:Name="TopTitle" Text="함선 추출"
                                   FontSize="18" FontWeight="SemiBold"/>
                        <TextBlock x:Name="TopSubtitle"
                                   Text="여러 함선을 대기열에 담아 선체와 무장을 선택한 형식으로 내보내요."
                                   Foreground="#8194AD" FontSize="11"/>
                    </StackPanel>
                    <Border Grid.Column="1" Background="#10213A"
                            BorderBrush="#234166" BorderThickness="1"
                            CornerRadius="12" Padding="12,7"
                            VerticalAlignment="Center">
                        <StackPanel Orientation="Horizontal">
                            <Ellipse x:Name="TopStatusDot" Width="8" Height="8"
                                     Fill="#34D399" Margin="0,0,8,0"/>
                            <TextBlock x:Name="TopStatusText" Text="준비됨"
                                       VerticalAlignment="Center" FontSize="12"/>
                        </StackPanel>
                    </Border>
                </Grid>
            </Border>

            <Grid Grid.Row="1" Margin="30,24,30,24">
                <Grid x:Name="ExtractPage">
                    <Grid.RowDefinitions>
                        <RowDefinition Height="Auto"/>
                        <RowDefinition Height="Auto"/>
                        <RowDefinition Height="Auto"/>
                        <RowDefinition Height="Auto"/>
                        <RowDefinition Height="*"/>
                    </Grid.RowDefinitions>

                    <Grid>
                        <Grid.ColumnDefinitions>
                            <ColumnDefinition Width="*"/>
                            <ColumnDefinition Width="310"/>
                        </Grid.ColumnDefinitions>
                        <StackPanel>
                            <TextBlock Text="함선 모델 추출"
                                       FontSize="28" FontWeight="SemiBold"/>
                            <TextBlock Text="선체·주함포·부포·대공포·어뢰를 형식별 개별 오브젝트로 저장해요."
                                       Foreground="#8FA2BC" Margin="0,7,0,0"/>
                        </StackPanel>
                        <StackPanel Grid.Column="1">
                            <TextBlock Text="게임 소스" Foreground="#8FA2BC"
                                       FontSize="11" Margin="0,0,0,5"/>
                            <ComboBox x:Name="SourceCombo" SelectedIndex="0">
                                <ComboBoxItem Content="World of Warships Legends"/>
                                <ComboBoxItem Content="World of Warships (PC)"/>
                                <ComboBoxItem Content="Korabli"/>
                            </ComboBox>
                            <Grid Margin="0,8,0,0">
                                <Grid.ColumnDefinitions>
                                    <ColumnDefinition Width="*"/>
                                    <ColumnDefinition Width="Auto"/>
                                </Grid.ColumnDefinitions>
                                <TextBlock x:Name="CurrentGamePathText"
                                           Text="게임 폴더를 확인하는 중"
                                           Foreground="#8296B1" FontSize="11"
                                           VerticalAlignment="Center"
                                           TextTrimming="CharacterEllipsis"/>
                                <Button Grid.Column="1" x:Name="BrowseCurrentGameButton"
                                        Content="게임 폴더 선택"
                                        Style="{StaticResource QuietButton}"
                                        Margin="10,0,0,0" Padding="10,5"/>
                            </Grid>
                        </StackPanel>
                    </Grid>

                    <Border Grid.Row="1" Style="{StaticResource CardBorder}" Margin="0,22,0,0">
                        <Grid>
                            <Grid.ColumnDefinitions>
                                <ColumnDefinition Width="*"/>
                                <ColumnDefinition Width="Auto"/>
                            </Grid.ColumnDefinitions>
                            <StackPanel>
                                <TextBlock Text="추출 대기열" Foreground="#8296B1"
                                           FontSize="11" FontWeight="SemiBold"/>
                                <TextBlock x:Name="SelectedShipName"
                                           Text="대기열이 비어 있어요"
                                           FontSize="22" FontWeight="SemiBold"
                                           Margin="0,7,0,0"/>
                                <TextBlock x:Name="SelectedShipMeta"
                                           Text="함선 선택에서 한 척 이상 체크해 주세요."
                                           Foreground="#91A4BD" Margin="0,6,0,0"/>
                                <ListBox x:Name="QueueList" Height="78" Margin="0,12,0,0" AllowDrop="True"
                                         DisplayMemberPath="Display" Background="#091523"
                                         Foreground="#DCE8F7" BorderBrush="#263B59"
                                         BorderThickness="1" Padding="4"
                                         ScrollViewer.VerticalScrollBarVisibility="Auto">
                                    <ListBox.ItemContainerStyle>
                                        <Style TargetType="ListBoxItem">
                                            <Setter Property="Padding" Value="9,5"/>
                                            <Setter Property="HorizontalContentAlignment" Value="Stretch"/>
                                            <Setter Property="Background" Value="Transparent"/>
                                            <Style.Triggers>
                                                <Trigger Property="IsMouseOver" Value="True">
                                                    <Setter Property="Background" Value="#142B46"/>
                                                </Trigger>
                                                <Trigger Property="IsSelected" Value="True">
                                                    <Setter Property="Background" Value="#1D5686"/>
                                                    <Setter Property="Foreground" Value="White"/>
                                                </Trigger>
                                            </Style.Triggers>
                                        </Style>
                                    </ListBox.ItemContainerStyle>
                                </ListBox>
                            </StackPanel>
                            <StackPanel Grid.Column="1" Margin="18,0,0,0">
                                <StackPanel Orientation="Horizontal">
                                    <Button x:Name="RefreshCatalogButton" Content="목록 새로고침"
                                            Style="{StaticResource QuietButton}" Margin="0,0,8,0"/>
                                    <Button x:Name="OpenPickerButton" Content="함선 추가·편집"/>
                                </StackPanel>
                                <StackPanel Orientation="Horizontal" HorizontalAlignment="Right"
                                            Margin="0,10,0,0">
                                    <Button x:Name="RemoveQueueButton" Content="선택 제거"
                                            Style="{StaticResource QuietButton}" Margin="0,0,8,0"
                                            IsEnabled="False"/>
                                    <Button x:Name="ClearQueueButton" Content="대기열 비우기"
                                            Style="{StaticResource QuietButton}" IsEnabled="False"/>
                                </StackPanel>
                                <StackPanel Orientation="Horizontal" HorizontalAlignment="Right"
                                            Margin="0,10,0,0">
                                    <Button x:Name="QueueUpButton" Content="위로"
                                            Style="{StaticResource QuietButton}" Margin="0,0,6,0"
                                            IsEnabled="False"/>
                                    <Button x:Name="QueueDownButton" Content="아래로"
                                            Style="{StaticResource QuietButton}" Margin="0,0,6,0"
                                            IsEnabled="False"/>
                                    <Button x:Name="SaveQueueButton" Content="저장"
                                            Style="{StaticResource QuietButton}" Margin="0,0,6,0"/>
                                    <Button x:Name="LoadQueueButton" Content="불러오기"
                                            Style="{StaticResource QuietButton}"/>
                                </StackPanel>
                            </StackPanel>
                        </Grid>
                    </Border>
                    <Border Grid.Row="2" Style="{StaticResource CardBorder}" Margin="0,14,0,0">
                        <Grid>
                            <Grid.ColumnDefinitions>
                                <ColumnDefinition Width="*"/>
                                <ColumnDefinition Width="Auto"/>
                            </Grid.ColumnDefinitions>
                            <StackPanel>
                                <TextBlock Text="출력" Foreground="#8296B1"
                                           FontSize="11" FontWeight="SemiBold"/>
                                <TextBlock x:Name="OutputPathLabel"
                                           Text="출력 폴더"
                                           FontSize="14" FontWeight="SemiBold"
                                           Margin="0,6,0,0"/>
                                <TextBlock Text="선체와 무장을 독립 부품으로 유지한 OBJ·MTL·PNG를 저장해요."
                                           Foreground="#7F92AC" FontSize="11" Margin="0,5,0,0"/>
                                <StackPanel Orientation="Horizontal" Margin="0,12,0,0">
                                    <ComboBox x:Name="FormatCombo" Width="155" SelectedIndex="0"
                                              ToolTip="출력 형식">
                                        <ComboBoxItem Content="OBJ만 · 세 게임 공통 · Blender 불필요" Tag="obj"/>
                                    </ComboBox>
                                    <ComboBox x:Name="TextureCombo" Width="120" Margin="8,0,0,0"
                                              SelectedIndex="0" ToolTip="텍스처 크기">
                                        <ComboBoxItem Content="원본 크기 컬러" Tag="0"/>
                                        <ComboBoxItem Content="2K 텍스처" Tag="2048"/>
                                        <ComboBoxItem Content="1K 텍스처" Tag="1024"/>
                                    </ComboBox>
                                    <ComboBox x:Name="LodCombo" Width="105" Margin="8,0,0,0"
                                              SelectedIndex="0" ToolTip="모델 정밀도">
                                        <ComboBoxItem Content="최고 LOD" Tag="0"/>
                                        <ComboBoxItem Content="중간 LOD" Tag="1"/>
                                        <ComboBoxItem Content="저용량 LOD" Tag="2"/>
                                    </ComboBox>
                                </StackPanel>
                            </StackPanel>
                            <StackPanel Grid.Column="1" Orientation="Horizontal"
                                        VerticalAlignment="Center">
                                <CheckBox x:Name="OverwriteCheck" Content="같은 폴더 덮어쓰기"
                                          Foreground="#AAB9CD" VerticalAlignment="Center"
                                          Margin="0,0,16,0"/>
                                <Button x:Name="BrowseOutputButton" Content="변경"/>
                            </StackPanel>
                        </Grid>
                    </Border>

                    <Grid Grid.Row="3" Margin="0,16,0,0">
                        <Grid.ColumnDefinitions>
                            <ColumnDefinition Width="*"/>
                            <ColumnDefinition Width="Auto"/>
                        </Grid.ColumnDefinitions>
                        <StackPanel VerticalAlignment="Center">
                            <TextBlock x:Name="ProgressStage" Text="준비됨"
                                       FontWeight="SemiBold"/>
                            <TextBlock x:Name="ProgressMessage"
                                       Text="게임 설치 폴더는 읽기 전용으로 사용해요."
                                       Foreground="#8093AC" FontSize="11" Margin="0,4,0,8"/>
                            <ProgressBar x:Name="MainProgress" Minimum="0" Maximum="100" Value="0"/>
                        </StackPanel>
                        <StackPanel Grid.Column="1" Orientation="Horizontal"
                                    VerticalAlignment="Bottom" Margin="18,0,0,0">
                            <Button x:Name="InspectButton" Content="추출 준비 검사"
                                    Margin="0,0,8,0"/>
                            <Button x:Name="PauseButton" Content="일시 정지"
                                    Margin="0,0,8,0" IsEnabled="False"/>
                            <Button x:Name="CancelButton" Content="취소"
                                    Margin="0,0,8,0" IsEnabled="False"/>
                            <Button x:Name="ExtractButton" Content="대기열 모델 추출"
                                    Style="{StaticResource PrimaryButton}" IsEnabled="False"/>
                        </StackPanel>
                    </Grid>

                    <Expander Grid.Row="4" x:Name="LogExpander"
                              Header="작업 로그" IsExpanded="True"
                              Margin="0,18,0,0" Foreground="#9EB0C7">
                        <Border Background="#050C16" BorderBrush="#1E304A"
                                BorderThickness="1" CornerRadius="8" Padding="10"
                                Margin="0,8,0,0">
                            <TextBox x:Name="LogBox" IsReadOnly="True"
                                     Background="Transparent" BorderThickness="0"
                                     FontFamily="Cascadia Mono,Consolas"
                                     FontSize="11" TextWrapping="NoWrap"
                                     VerticalScrollBarVisibility="Auto"
                                     HorizontalScrollBarVisibility="Auto"
                                     AcceptsReturn="True"/>
                        </Border>
                    </Expander>
                </Grid>

                <Grid x:Name="ViewerPage" Visibility="Collapsed">
                    <Grid.RowDefinitions>
                        <RowDefinition Height="Auto"/>
                        <RowDefinition Height="Auto"/>
                        <RowDefinition Height="*"/>
                    </Grid.RowDefinitions>
                    <Grid>
                        <Grid.ColumnDefinitions>
                            <ColumnDefinition Width="*"/>
                            <ColumnDefinition Width="Auto"/>
                        </Grid.ColumnDefinitions>
                        <StackPanel>
                            <TextBlock Text="3D 모델 뷰어" FontSize="28" FontWeight="SemiBold"/>
                            <TextBlock Text="추출한 OBJ를 바로 확인하고 파트 선택·숨김·분리 보기·이동·회전을 할 수 있어요."
                                       Foreground="#8FA2BC" Margin="0,7,0,0"/>
                        </StackPanel>
                        <StackPanel Grid.Column="1" Orientation="Horizontal" VerticalAlignment="Bottom">
                            <Button x:Name="OpenRecentModelButton" Content="최근 추출 열기"
                                    Style="{StaticResource QuietButton}" Margin="0,0,8,0"/>
                            <Button x:Name="OpenCompareModelButton" Content="비교 OBJ 열기"
                                    Style="{StaticResource QuietButton}" Margin="0,0,8,0"/>
                            <Button x:Name="OpenModelButton" Content="OBJ 파일 열기"
                                    Style="{StaticResource PrimaryButton}"/>
                        </StackPanel>
                    </Grid>
                    <Border Grid.Row="1" Style="{StaticResource CardBorder}" Margin="0,18,0,14" Padding="16,12">
                        <Grid>
                            <Grid.ColumnDefinitions>
                                <ColumnDefinition Width="Auto"/>
                                <ColumnDefinition Width="*"/>
                                <ColumnDefinition Width="Auto"/>
                            </Grid.ColumnDefinitions>
                            <Border Background="#123158" CornerRadius="10" Padding="10,5" Margin="0,0,12,0">
                                <TextBlock Text="MODEL DECK" Foreground="#8FC6FF" FontSize="10" FontWeight="Bold"/>
                            </Border>
                            <StackPanel Grid.Column="1" VerticalAlignment="Center">
                                <TextBlock x:Name="ViewerPathLabel" Text="열린 모델 없음"
                                           FontWeight="SemiBold" TextTrimming="CharacterEllipsis"/>
                                <TextBlock x:Name="ViewerStatus" Text="OBJ 파일을 선택하면 오프라인 뷰어에서 열어요."
                                           Foreground="#8093AC" FontSize="11" Margin="0,3,0,0"/>
                            </StackPanel>
                            <Button Grid.Column="2" x:Name="OpenViewerFolderButton" Content="모델 폴더 열기"
                                    Style="{StaticResource QuietButton}" IsEnabled="False" Margin="14,0,0,0"/>
                        </Grid>
                    </Border>
                    <Border Grid.Row="2" Background="#050B14" BorderBrush="#243A58"
                            BorderThickness="1" CornerRadius="12" ClipToBounds="True">
                        <wv2:WebView2 x:Name="ModelWebView" DefaultBackgroundColor="#050B14"/>
                    </Border>
                </Grid>
                <ScrollViewer x:Name="SettingsPage" Visibility="Collapsed"
                              VerticalScrollBarVisibility="Auto">
                    <StackPanel>
                        <TextBlock Text="설정" FontSize="28" FontWeight="SemiBold"/>
                        <TextBlock Text="세 게임 설치 경로와 출력 위치를 지정해요. Blender는 사용하지 않아요."
                                   Foreground="#8FA2BC" Margin="0,7,0,20"/>
                        <Border Style="{StaticResource CardBorder}" Margin="0,0,0,14">
                            <Grid>
                                <Grid.ColumnDefinitions>
                                    <ColumnDefinition Width="*"/>
                                    <ColumnDefinition Width="220"/>
                                </Grid.ColumnDefinitions>
                                <StackPanel VerticalAlignment="Center">
                                    <TextBlock Text="인터페이스 언어" FontSize="16" FontWeight="SemiBold"/>
                                    <TextBlock Text="언어 변경은 프로그램을 다시 열 때 적용돼요."
                                               Foreground="#8195AF" FontSize="11" Margin="0,6,0,0"/>
                                </StackPanel>
                                <ComboBox Grid.Column="1" x:Name="LanguageCombo" SelectedIndex="0"
                                          VerticalAlignment="Center" Margin="18,0,0,0">
                                    <ComboBoxItem Tag="ko" Content="한국어"/>
                                    <ComboBoxItem Tag="en" Content="English"/>
                                </ComboBox>
                            </Grid>
                        </Border>
                        <Border Style="{StaticResource CardBorder}" Margin="0,0,0,14">
                            <Grid>
                                <Grid.ColumnDefinitions>
                                    <ColumnDefinition Width="*"/>
                                    <ColumnDefinition Width="Auto"/>
                                    <ColumnDefinition Width="Auto"/>
                                </Grid.ColumnDefinitions>
                                <StackPanel VerticalAlignment="Center">
                                    <TextBlock Text="업데이트" FontSize="16" FontWeight="SemiBold"/>
                                    <TextBlock Text="시작할 때 새 GitHub 릴리스를 자동으로 확인해요."
                                               Foreground="#8195AF" FontSize="11" Margin="0,6,0,0"/>
                                </StackPanel>
                                <CheckBox Grid.Column="1" x:Name="AutoUpdateCheck"
                                          Content="시작할 때 자동 확인" VerticalAlignment="Center"
                                          Margin="18,0,14,0"/>
                                <Button Grid.Column="2" x:Name="CheckUpdateButton" Content="지금 확인"
                                        Style="{StaticResource QuietButton}" VerticalAlignment="Center"/>
                            </Grid>
                        </Border>
                        <Border Style="{StaticResource CardBorder}">
                            <StackPanel>
                                <TextBlock Text="게임 설치 폴더" FontSize="16"
                                           FontWeight="SemiBold" Margin="0,0,0,14"/>
                                <TextBlock Text="World of Warships Legends" Foreground="#8FA2BC"/>
                                <Grid Margin="0,5,0,12">
                                    <Grid.ColumnDefinitions>
                                        <ColumnDefinition Width="*"/>
                                        <ColumnDefinition Width="Auto"/>
                                    </Grid.ColumnDefinitions>
                                    <TextBox x:Name="LegendsPathBox"/>
                                    <Button Grid.Column="1" x:Name="BrowseLegendsButton"
                                            Content="찾기" Margin="8,0,0,0"/>
                                </Grid>
                                <TextBlock Text="World of Warships (PC)" Foreground="#8FA2BC"/>
                                <Grid Margin="0,5,0,12">
                                    <Grid.ColumnDefinitions>
                                        <ColumnDefinition Width="*"/>
                                        <ColumnDefinition Width="Auto"/>
                                    </Grid.ColumnDefinitions>
                                    <TextBox x:Name="PcPathBox"/>
                                    <Button Grid.Column="1" x:Name="BrowsePcButton"
                                            Content="찾기" Margin="8,0,0,0"/>
                                </Grid>
                                <TextBlock Text="Korabli" Foreground="#8FA2BC"/>
                                <Grid Margin="0,5,0,0">
                                    <Grid.ColumnDefinitions>
                                        <ColumnDefinition Width="*"/>
                                        <ColumnDefinition Width="Auto"/>
                                    </Grid.ColumnDefinitions>
                                    <TextBox x:Name="KorabliPathBox"/>
                                    <Button Grid.Column="1" x:Name="BrowseKorabliButton"
                                            Content="찾기" Margin="8,0,0,0"/>
                                </Grid>
                            </StackPanel>
                        </Border>
                        <Border Style="{StaticResource CardBorder}" Margin="0,14,0,0">
                            <StackPanel>
                                <TextBlock Text="도구와 출력" FontSize="16"
                                           FontWeight="SemiBold" Margin="0,0,0,14"/>
                                <TextBlock Text="기본 출력 폴더" Foreground="#8FA2BC"/>
                                <Grid Margin="0,5,0,12">
                                    <Grid.ColumnDefinitions>
                                        <ColumnDefinition Width="*"/>
                                        <ColumnDefinition Width="Auto"/>
                                    </Grid.ColumnDefinitions>
                                    <TextBox x:Name="SettingsOutputBox"/>
                                    <Button Grid.Column="1" x:Name="BrowseSettingsOutputButton"
                                            Content="찾기" Margin="8,0,0,0"/>
                                </Grid>
                                <TextBlock Text="호환성 예약 필드 · 사용하지 않음" Foreground="#8FA2BC" Visibility="Collapsed"/>
                                <Grid Margin="0,5,0,12" Visibility="Collapsed">
                                    <Grid.ColumnDefinitions>
                                        <ColumnDefinition Width="*"/>
                                        <ColumnDefinition Width="Auto"/>
                                    </Grid.ColumnDefinitions>
                                    <TextBox x:Name="BlenderPathBox"/>
                                    <Button Grid.Column="1" x:Name="BrowseBlenderButton"
                                            Content="찾기" Margin="8,0,0,0"/>
                                </Grid>
                                <TextBlock Text="Oodle 런타임 DLL · 코라블리 전용"
                                           Foreground="#8FA2BC"/>
                                <Grid Margin="0,5,0,0">
                                    <Grid.ColumnDefinitions>
                                        <ColumnDefinition Width="*"/>
                                        <ColumnDefinition Width="Auto"/>
                                        <ColumnDefinition Width="Auto"/>
                                    </Grid.ColumnDefinitions>
                                    <TextBox x:Name="OodlePathBox"/>
                                    <Button Grid.Column="1" x:Name="FindOodleButton"
                                            Content="자동 찾기" Margin="8,0,0,0"/>
                                    <Button Grid.Column="2" x:Name="BrowseOodleButton"
                                            Content="직접 선택" Margin="8,0,0,0"/>
                                </Grid>
                                <TextBlock Text="Oodle DLL은 배포본에 포함하지 않고, 사용자 PC의 합법적으로 설치된 런타임 경로만 참조해요."
                                           Foreground="#657B97" FontSize="11" Margin="0,7,0,0"/>
                            </StackPanel>
                        </Border>
                        <Border Style="{StaticResource CardBorder}" Margin="0,14,0,0">
                            <Grid>
                                <Grid.ColumnDefinitions>
                                    <ColumnDefinition Width="*"/>
                                    <ColumnDefinition Width="Auto"/>
                                </Grid.ColumnDefinitions>
                                <StackPanel>
                                    <TextBlock Text="자동 탐색·캐시·진단" FontSize="16"
                                               FontWeight="SemiBold"/>
                                    <TextBlock x:Name="CacheInfoText"
                                               Text="캐시 용량을 계산하려면 상태 새로고침을 눌러 주세요."
                                               Foreground="#8195AF" FontSize="11" Margin="0,6,0,0"/>
                                </StackPanel>
                                <WrapPanel Grid.Column="1" VerticalAlignment="Center"
                                           Margin="16,0,0,0">
                                    <Button x:Name="AutoDetectButton" Content="경로 자동 탐색"
                                            Margin="0,0,8,0"/>
                                    <Button x:Name="CacheRefreshButton" Content="캐시 상태"
                                            Margin="0,0,8,0"/>
                                    <Button x:Name="OpenCacheButton" Content="캐시 열기"
                                            Margin="0,0,8,0"/>
                                    <Button x:Name="ClearCacheButton" Content="캐시 비우기"
                                            Margin="0,0,8,0"/>
                                    <Button x:Name="DiagnosticsButton" Content="진단 ZIP"/>
                                </WrapPanel>
                            </Grid>
                        </Border>
                        <Border Style="{StaticResource CardBorder}" Margin="0,14,0,0">
                            <StackPanel>
                                <TextBlock Text="WoWS Toolbox 5.0.31 · 비공식 커뮤니티 도구"
                                           FontSize="15" FontWeight="SemiBold"/>
                                <TextBlock Margin="0,6,0,0" Foreground="#8195AF" FontSize="11"
                                           TextWrapping="Wrap"
                                           Text="게임 명칭·상표·3D 모델·텍스처 등 모든 게임 자산의 권리는 각 권리자에게 있어요. WoWS Toolbox는 Wargaming·Lesta Games 또는 관계사의 승인·후원 제품이 아니며, 추출물은 해당 EULA·플랫폼 약관과 법률에 따라 사용해야 해요."/>
                            </StackPanel>
                        </Border>
                        <Grid Margin="0,16,0,4">
                            <TextBlock x:Name="SettingsStatus"
                                       Text="경로를 확인한 뒤 저장해 주세요."
                                       Foreground="#8FA2BC" VerticalAlignment="Center"/>
                            <StackPanel HorizontalAlignment="Right" Orientation="Horizontal">
                                <Button x:Name="ValidateSettingsButton" Content="경로 검사"
                                        Margin="0,0,8,0"/>
                                <Button x:Name="SaveSettingsButton" Content="설정 저장"
                                        Style="{StaticResource PrimaryButton}"/>
                            </StackPanel>
                        </Grid>
                    </StackPanel>
                </ScrollViewer>
            </Grid>
        </Grid>
    </Grid>
</Window>
'@

$xaml = Convert-XamlToUiLanguage $xaml
$reader = [Xml.XmlNodeReader]::new([xml] $xaml)
$window = [Windows.Markup.XamlReader]::Load($reader)
$script:AppIconPath = Join-Path $script:PackageRoot 'Branding\WoWS-Toolbox.ico'
$script:AppIcon = $null
if (Test-Path -LiteralPath $script:AppIconPath -PathType Leaf) {
    try {
        $window.Icon = [Windows.Media.Imaging.BitmapFrame]::Create(
            [Uri]::new($script:AppIconPath)
        )
        $script:AppIcon = [Drawing.Icon]::new($script:AppIconPath)
    }
    catch {
        # Branding must never prevent the extraction UI from opening.
    }
}

function Get-Control {
    param([Parameter(Mandatory)] [string] $Name)
    $control = $window.FindName($Name)
    if ($null -eq $control) {
        throw "필수 UI 컨트롤을 찾지 못했어요: $Name"
    }
    return $control
}

$controlNames = @(
    'NavExtract', 'NavViewer', 'NavSettings',
    'TopTitle', 'TopSubtitle', 'TopStatusDot', 'TopStatusText',
    'ExtractPage', 'ViewerPage', 'SettingsPage', 'SourceCombo',
    'CurrentGamePathText', 'BrowseCurrentGameButton',
    'SelectedShipName', 'SelectedShipMeta', 'QueueList',
    'RefreshCatalogButton', 'OpenPickerButton', 'RemoveQueueButton',
    'QueueUpButton', 'QueueDownButton', 'SaveQueueButton', 'LoadQueueButton',
    'ClearQueueButton', 'OutputPathLabel', 'OverwriteCheck',
    'BrowseOutputButton', 'ProgressStage', 'ProgressMessage',
    'MainProgress', 'InspectButton', 'PauseButton', 'CancelButton', 'ExtractButton',
    'FormatCombo', 'TextureCombo', 'LodCombo', 'LanguageCombo',
    'AutoUpdateCheck', 'CheckUpdateButton',
    'LogBox', 'ModelWebView', 'ViewerPathLabel', 'ViewerStatus',
    'OpenModelButton', 'OpenRecentModelButton', 'OpenCompareModelButton', 'OpenViewerFolderButton',
    'LegendsPathBox', 'PcPathBox', 'KorabliPathBox',
    'SettingsOutputBox', 'BlenderPathBox', 'OodlePathBox',
    'BrowseLegendsButton', 'BrowsePcButton', 'BrowseKorabliButton',
    'BrowseSettingsOutputButton', 'BrowseBlenderButton',
    'FindOodleButton', 'BrowseOodleButton', 'ValidateSettingsButton',
    'SaveSettingsButton', 'SettingsStatus',
    'AutoDetectButton', 'CacheRefreshButton', 'OpenCacheButton',
    'ClearCacheButton', 'DiagnosticsButton', 'CacheInfoText'
)
$controls = @{}
foreach ($name in $controlNames) {
    $controls[$name] = Get-Control $name
}

if ($SelfTest) {
    $sourceCombo = $window.FindName('SourceCombo')
    [void] $sourceCombo.ApplyTemplate()
    $updateFixtureJson = '{"tag_name":"v5.0.31","name":"WoWS Toolbox 5.0.31","draft":false,"prerelease":false,"html_url":"https://github.com/Ch0m1n/WoWS-Toolbox/releases/tag/v5.0.31","assets":[{"name":"WoWS-Toolbox-Setup-5.0.31.exe","browser_download_url":"https://github.com/Ch0m1n/WoWS-Toolbox/releases/download/v5.0.31/WoWS-Toolbox-Setup-5.0.31.exe","digest":"sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"}]}'
    $updateFixture = ConvertFrom-WoWSToolboxReleaseJson -CurrentVersion '5.0.30' -Json $updateFixtureJson
    [pscustomobject] @{
        ok = $true
        language = $script:WoWSToolboxLanguage
        window = $window.Name
        controls = $controls.Count
        backend_catalog = (Test-Path -LiteralPath $script:CatalogScript)
        backend_extract = (Test-Path -LiteralPath $script:ExtractScript)
        hull_only_control_present = $null -ne $window.FindName('HullOnly')
        thumbnail_control_present = $null -ne $window.FindName('ShipThumbnail')
        viewer_control_present = $null -ne $window.FindName('ModelWebView')
        update_controls_present = $null -ne $window.FindName('AutoUpdateCheck') -and
            $null -ne $window.FindName('CheckUpdateButton')
        auto_update_default = [string] $script:Settings.AutoCheckUpdates
        update_release_parser_ok = $updateFixture.UpdateAvailable -and
            $updateFixture.Installable -and $updateFixture.Version -eq '5.0.31'
        queue_controls_present =
            $null -ne $window.FindName('QueueList') -and
            $null -ne $window.FindName('RemoveQueueButton') -and
            $null -ne $window.FindName('ClearQueueButton')
        batch_extract_label = [string] $window.FindName('ExtractButton').Content
        webview_runtime_present =
            (Test-Path -LiteralPath $viewerCoreDll -PathType Leaf) -and
            (Test-Path -LiteralPath $viewerWpfDll -PathType Leaf) -and
            (Test-Path -LiteralPath $viewerLoaderDll -PathType Leaf)
        combo_custom_template =
            $null -ne $sourceCombo.Template.FindName('DropDownToggle', $sourceCombo) -and
            $null -ne $sourceCombo.Template.FindName('PART_Popup', $sourceCombo)
        combo_item_style_present =
            $null -ne $window.Resources[[Windows.Controls.ComboBoxItem]]
        combo_foreground = [string] $sourceCombo.Foreground
        combo_background = [string] $sourceCombo.Background
        default_output = [string] $script:Settings.OutputPath
        program_output_default = [StringComparer]::OrdinalIgnoreCase.Equals(
            [IO.Path]::GetFullPath([string] $script:Settings.OutputPath),
            [IO.Path]::GetFullPath($programDefaultOutputPath)
        )
        output_folder_name = Split-Path -Leaf ([string] $script:Settings.OutputPath)
        deprecated_packaged_output_detected = Test-DeprecatedPackagedOutputPath (
            'C:\sandbox\outputs\WoWS-Toolbox-v5.0.6\output'
        )
        custom_output_preserved = -not (Test-DeprecatedPackagedOutputPath (
            'C:\Users\example\Documents\Custom-WoWS-Exports'
        ))
    } | ConvertTo-Json -Compress
    $window.Close()
    return
}

$script:Catalogs = @{}
$script:SelectedShip = $null
$script:SelectedSource = 'legends'
$script:ExtractionQueue = [Collections.Generic.List[object]]::new()
$script:BatchItems = @()
$script:BatchIndex = 0
$script:BatchSucceeded = [Collections.Generic.List[object]]::new()
$script:BatchFailed = [Collections.Generic.List[object]]::new()
$script:BatchActive = $false
$script:BatchCurrentItem = $null
$script:ActiveRunner = $null
$script:ActiveOperation = ''
$script:ActiveCompletion = $null
$script:UpdateHttpClient = $null
$script:UpdateCheckTask = $null
$script:UpdateCheckManual = $false
$script:UpdateCheckStarted = $false
$script:UpdateDownloadClient = $null
$script:UpdateDownloadTask = $null
$script:PendingUpdate = $null
$script:UpdateDownloadTempPath = ''
$script:UpdateDownloadFinalPath = ''
$script:UpdateInstallerStarted = $false
$script:PendingPicker = $false
$script:LastResult = $null
$script:LastOutputDir = ''
$script:CancelRequested = $false
$script:CatalogRefreshSource = ''
$script:CatalogRefreshOutput = ''
$script:ViewerReady = $false
$script:ViewerInitializing = $false
$script:PendingViewerModel = ''
$script:ViewerModelPath = ''
$script:ViewerMappedDirectory = ''
$script:ViewerCompareMappedDirectory = ''
$script:ViewerMappingSerial = 0
$script:ViewerCompareMappingSerial = 0
$script:ViewerComparePath = ''
$script:ViewerTestResult = $null
$script:ViewerInitTask = $null
$script:ViewerInitPoll = $null
$script:ViewerCoreConfigured = $false
$script:NotifyIcon = $null
$script:NotifyTimer = $null
$script:ViewerUserDataRoot = Join-Path $script:StateRoot 'WebView2'
$script:BatchManifestPath = Join-Path $script:StateRoot 'active-batch.json'
$script:BatchControlPath = Join-Path $script:StateRoot 'active-batch-control.json'
$script:BatchSummary = $null
$script:BatchPaused = $false
$script:QueueDragStart = $null
$script:QueueDragItem = $null
$script:FavoritesPath = Join-Path $script:StateRoot 'favorites.json'
$script:RecentShipsPath = Join-Path $script:StateRoot 'recent-ships.json'

function Get-SourceKey {
    param([int] $Index = $controls.SourceCombo.SelectedIndex)
    return @('legends', 'pc', 'korabli')[$Index]
}

function Get-SourceDisplay {
    param([string] $Source)
    switch ($Source) {
        'legends' { 'World of Warships Legends' }
        'pc' { 'World of Warships (PC)' }
        'korabli' { 'Korabli' }
        default { $Source }
    }
}

function Get-GamePath {
    param([string] $Source)
    switch ($Source) {
        'legends' { [string] $script:Settings.LegendsPath }
        'pc' { [string] $script:Settings.PcPath }
        'korabli' { [string] $script:Settings.KorabliPath }
        default { '' }
    }
}

function Set-GamePath {
    param(
        [Parameter(Mandatory)] [string] $Source,
        [Parameter(Mandatory)] [string] $Path
    )
    switch ($Source) {
        'legends' {
            $script:Settings.LegendsPath = $Path
            $controls.LegendsPathBox.Text = $Path
        }
        'pc' {
            $script:Settings.PcPath = $Path
            $controls.PcPathBox.Text = $Path
        }
        'korabli' {
            $script:Settings.KorabliPath = $Path
            $controls.KorabliPathBox.Text = $Path
        }
        default { throw "알 수 없는 게임 소스예요: $Source" }
    }
}

function Get-NormalizedGamePath {
    param([string] $Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return '' }
    try {
        return [IO.Path]::GetFullPath($Path).TrimEnd(
            [IO.Path]::DirectorySeparatorChar,
            [IO.Path]::AltDirectorySeparatorChar
        ).ToLowerInvariant()
    }
    catch { return $Path.Trim().TrimEnd('\', '/').ToLowerInvariant() }
}

function Get-GamePathToken {
    param([string] $Path)
    $normalized = Get-NormalizedGamePath $Path
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes($normalized)
        $hash = $sha.ComputeHash($bytes)
        return (-join @($hash[0..5] | ForEach-Object { $_.ToString('x2') }))
    }
    finally { $sha.Dispose() }
}

function Get-GameInstallLabel {
    param([string] $Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return '경로 없음' }
    $trimmed = $Path.TrimEnd('\', '/')
    $label = Split-Path -Leaf $trimmed
    if ([string]::IsNullOrWhiteSpace($label)) { return $trimmed }
    return $label
}

function Get-GameFolderSource {
    param([Parameter(Mandatory)] [string] $Path)
    if (Test-Path -LiteralPath (Join-Path $Path 'WorldOfWarshipsLegends.exe') -PathType Leaf) {
        return 'legends'
    }
    if (Test-Path -LiteralPath (Join-Path $Path 'Korabli.exe') -PathType Leaf) {
        return 'korabli'
    }
    if (Test-Path -LiteralPath (Join-Path $Path 'WorldOfWarships.exe') -PathType Leaf) {
        return 'pc'
    }
    return ''
}

function Resolve-GameFolderRoot {
    param([Parameter(Mandatory)] [string] $SelectedPath)
    try { $current = [IO.DirectoryInfo]::new([IO.Path]::GetFullPath($SelectedPath)) }
    catch { return $null }
    for ($depth = 0; $depth -le 4 -and $null -ne $current; $depth++) {
        if (-not [string]::IsNullOrWhiteSpace((Get-GameFolderSource $current.FullName))) {
            return $current.FullName
        }
        $current = $current.Parent
    }
    return $null
}

function Get-GameFolderProblem {
    param(
        [Parameter(Mandatory)] [string] $Source,
        [Parameter(Mandatory)] [string] $Path
    )
    $packageRoot = Join-Path $Path 'res_packages'
    if (-not (Test-Path -LiteralPath $packageRoot -PathType Container)) {
        return 'res_packages 폴더가 없어요'
    }
    if ($null -eq (Get-ChildItem -LiteralPath $packageRoot -Filter '*.pkg' -File -ErrorAction SilentlyContinue |
            Select-Object -First 1)) {
        return 'res_packages 안에 .pkg 게임 패키지가 없어요'
    }
    if ($Source -in @('pc', 'korabli')) {
        $binRoot = Join-Path $Path 'bin'
        $latestBuild = Get-ChildItem -LiteralPath $binRoot -Directory -ErrorAction SilentlyContinue |
            Where-Object Name -Match '^\d+$' |
            Sort-Object { [long] $_.Name } -Descending |
            Select-Object -First 1
        if ($null -eq $latestBuild) { return 'bin 아래에서 숫자 빌드 폴더를 찾지 못했어요' }
        $idxRoot = Join-Path $latestBuild.FullName 'idx'
        if ($null -eq (Get-ChildItem -LiteralPath $idxRoot -Filter '*.idx' -File -ErrorAction SilentlyContinue |
                Select-Object -First 1)) {
            return '최신 빌드의 idx 인덱스를 찾지 못했어요'
        }
    }
    return ''
}

function Update-CurrentGamePathUi {
    $source = Get-SourceKey
    $path = Get-GamePath $source
    $label = Get-GameInstallLabel $path
    $controls.CurrentGamePathText.Text = "$label · $path"
    $controls.CurrentGamePathText.ToolTip = $path
    $color = if (Test-Path -LiteralPath $path -PathType Container) {
        '#8FAED0'
    }
    else { '#FCA5A5' }
    $controls.CurrentGamePathText.Foreground =
        [Windows.Media.BrushConverter]::new().ConvertFrom($color)
}

function Select-CurrentGameFolder {
    $selected = Select-Folder (Get-GamePath (Get-SourceKey))
    if ([string]::IsNullOrWhiteSpace($selected)) { return }
    $root = Resolve-GameFolderRoot $selected
    if ([string]::IsNullOrWhiteSpace([string] $root)) {
        [Windows.MessageBox]::Show(
            $window,
            (Get-UiText '선택한 위치에서 WoWS, Korabli 또는 Legends 실행 파일을 찾지 못했어요.' 'No WoWS, Korabli, or Legends executable was found at the selected location.'),
            (Get-UiText '게임 폴더를 인식하지 못했어요' 'Game folder not recognized'),
            [Windows.MessageBoxButton]::OK,
            [Windows.MessageBoxImage]::Warning
        ) | Out-Null
        return
    }
    $source = Get-GameFolderSource $root
    $problem = Get-GameFolderProblem -Source $source -Path $root
    if (-not [string]::IsNullOrWhiteSpace($problem)) {
        [Windows.MessageBox]::Show(
            $window,
            (Get-UiText "게임 폴더 구조가 아직 완전하지 않아요.`n`n$problem`n`n게임 업데이트가 끝난 뒤 다시 선택해 주세요." "The game folder structure is incomplete.`n`n$problem`n`nTry again after the game update has finished."),
            (Get-UiText '게임 데이터 확인 필요' 'Game data check required'),
            [Windows.MessageBoxButton]::OK,
            [Windows.MessageBoxImage]::Warning
        ) | Out-Null
        return
    }
    Set-GamePath -Source $source -Path $root
    [void] $script:Catalogs.Remove($source)
    $controls.SourceCombo.SelectedIndex = @{ legends = 0; pc = 1; korabli = 2 }[$source]
    Save-Settings
    Update-CurrentGamePathUi
    Add-Log "게임 설치본 선택: $(Get-SourceDisplay $source) · $root"
    Start-CatalogRefresh
}

function Set-TopStatus {
    param(
        [string] $Text,
        [string] $Color = '#34D399'
    )
    $controls.TopStatusText.Text = Convert-ToUiText $Text
    $controls.TopStatusDot.Fill = [Windows.Media.BrushConverter]::new().ConvertFrom($Color)
}

function Add-Log {
    param(
        [string] $Text,
        [switch] $ErrorLine
    )
    if ([string]::IsNullOrWhiteSpace($Text)) { return }
    $Text = Convert-ToUiText $Text
    $prefix = if ($ErrorLine) { 'ERR' } else { 'OUT' }
    $line = '{0:HH:mm:ss} [{1}] {2}' -f (Get-Date), $prefix, $Text
    if ($controls.LogBox.Text.Length -gt 120000) {
        $controls.LogBox.Text = $controls.LogBox.Text.Substring(
            $controls.LogBox.Text.Length - 90000
        )
    }
    $controls.LogBox.AppendText($line + [Environment]::NewLine)
    $controls.LogBox.ScrollToEnd()
}

function New-UpdateHttpClient {
    if ($null -eq $script:UpdateHttpClient) {
        $client = [Net.Http.HttpClient]::new()
        $client.Timeout = [TimeSpan]::FromSeconds(25)
        $client.DefaultRequestHeaders.UserAgent.ParseAdd(
            "WoWS-Toolbox/$($script:AppVersion)"
        )
        $client.DefaultRequestHeaders.Accept.ParseAdd(
            'application/vnd.github+json'
        )
        [void] $client.DefaultRequestHeaders.TryAddWithoutValidation(
            'X-GitHub-Api-Version', '2022-11-28'
        )
        $script:UpdateHttpClient = $client
    }
    return $script:UpdateHttpClient
}

function Show-UpdateMessage {
    param(
        [Parameter(Mandatory)] [string] $Korean,
        [Parameter(Mandatory)] [string] $English,
        [Windows.MessageBoxImage] $Image = [Windows.MessageBoxImage]::Information
    )
    [Windows.MessageBox]::Show(
        $window,
        (Get-UiText $Korean $English),
        (Get-UiText 'WoWS Toolbox 업데이트' 'WoWS Toolbox Update'),
        [Windows.MessageBoxButton]::OK,
        $Image
    ) | Out-Null
}

function Start-UpdateCheck {
    param([switch] $Manual)
    if ($automatedMode) { return }
    if ($null -ne $script:UpdateCheckTask -or
        $null -ne $script:UpdateDownloadTask) {
        if ($Manual) {
            Show-UpdateMessage '이미 업데이트를 확인하거나 받고 있어요.' `
                'An update check or download is already in progress.'
        }
        return
    }
    if (-not $Manual -and
        [string] $script:Settings.AutoCheckUpdates -ne 'true') { return }
    try {
        $script:UpdateCheckStarted = $true
        $script:UpdateCheckManual = [bool] $Manual
        $controls.CheckUpdateButton.IsEnabled = $false
        $client = New-UpdateHttpClient
        $script:UpdateCheckTask = $client.GetStringAsync($script:UpdateApiUrl)
        Add-Log (Get-UiText 'GitHub에서 새 버전을 확인하는 중이에요.' 'Checking GitHub for updates.')
    }
    catch {
        $script:UpdateCheckTask = $null
        $controls.CheckUpdateButton.IsEnabled = $true
        if ($Manual) {
            Show-UpdateMessage "업데이트 확인을 시작하지 못했어요.`n`n$($_.Exception.Message)" `
                "Could not start the update check.`n`n$($_.Exception.Message)" `
                -Image ([Windows.MessageBoxImage]::Warning)
        }
        else {
            Add-Log (Get-UiText "업데이트 확인 시작 실패: $($_.Exception.Message)" "Could not start the update check: $($_.Exception.Message)") -ErrorLine
        }
    }
}

function Remove-UpdateDownloadTempFile {
    if (-not [string]::IsNullOrWhiteSpace($script:UpdateDownloadTempPath) -and
        (Test-Path -LiteralPath $script:UpdateDownloadTempPath -PathType Leaf)) {
        Remove-Item -LiteralPath $script:UpdateDownloadTempPath -Force `
            -ErrorAction SilentlyContinue
    }
}

function Start-VerifiedUpdateInstaller {
    param(
        [Parameter(Mandatory)] $Update,
        [Parameter(Mandatory)] [string] $InstallerPath
    )
    $actualHash = (Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256).Hash
    if (-not [StringComparer]::OrdinalIgnoreCase.Equals(
        $actualHash, [string] $Update.Sha256
    )) {
        throw '다운로드한 설치 파일의 SHA-256이 GitHub 릴리스와 일치하지 않아요.'
    }
    Add-Log (Get-UiText `
        "WoWS Toolbox $($Update.Version) 설치 파일 검증 완료. 설치 프로그램을 열어요." `
        "WoWS Toolbox $($Update.Version) installer verified. Opening setup.")
    Start-Process -FilePath $InstallerPath `
        -WorkingDirectory ([IO.Path]::GetDirectoryName($InstallerPath))
    $script:UpdateInstallerStarted = $true
    $window.Close()
}

function Start-UpdateDownload {
    param([Parameter(Mandatory)] $Update)
    if (-not [bool] $Update.Installable) {
        throw '검증 가능한 GitHub 설치 파일이 없어요.'
    }
    if ($script:BatchActive -or $null -ne $script:ActiveRunner) {
        Show-UpdateMessage `
            '현재 작업을 마친 뒤 다시 업데이트해 주세요.' `
            'Finish the current task before updating.' `
            -Image ([Windows.MessageBoxImage]::Warning)
        return
    }
    $versionRoot = Join-Path $script:UpdateRoot ([string] $Update.Version)
    [IO.Directory]::CreateDirectory($versionRoot) | Out-Null
    $finalPath = Join-Path $versionRoot ([string] $Update.InstallerName)
    $tempPath = "$finalPath.download"
    if (Test-Path -LiteralPath $finalPath -PathType Leaf) {
        try {
            Start-VerifiedUpdateInstaller -Update $Update -InstallerPath $finalPath
            return
        }
        catch {
            Remove-Item -LiteralPath $finalPath -Force -ErrorAction SilentlyContinue
            if ($script:UpdateInstallerStarted) { throw }
        }
    }
    if (Test-Path -LiteralPath $tempPath -PathType Leaf) {
        Remove-Item -LiteralPath $tempPath -Force
    }
    $client = [Net.WebClient]::new()
    $client.Headers[[Net.HttpRequestHeader]::UserAgent] =
        "WoWS-Toolbox/$($script:AppVersion)"
    $script:PendingUpdate = $Update
    $script:UpdateDownloadClient = $client
    $script:UpdateDownloadTempPath = $tempPath
    $script:UpdateDownloadFinalPath = $finalPath
    $script:UpdateDownloadTask = $client.DownloadFileTaskAsync(
        [Uri]::new([string] $Update.InstallerUrl), $tempPath
    )
    $controls.CheckUpdateButton.IsEnabled = $false
    Set-TopStatus (Get-UiText '업데이트 받는 중' 'Downloading update') '#60A5FA'
    Add-Log (Get-UiText `
        "WoWS Toolbox $($Update.Version) 설치 파일을 받고 있어요." `
        "Downloading the WoWS Toolbox $($Update.Version) installer.")
}

function Complete-UpdateWork {
    if ($null -ne $script:UpdateCheckTask -and
        $script:UpdateCheckTask.IsCompleted) {
        $task = $script:UpdateCheckTask
        $manual = $script:UpdateCheckManual
        $script:UpdateCheckTask = $null
        $script:UpdateCheckManual = $false
        $controls.CheckUpdateButton.IsEnabled = $true
        try {
            if ($task.IsCanceled) { throw '업데이트 확인이 취소됐어요.' }
            if ($task.IsFaulted) { throw $task.Exception.GetBaseException() }
            $json = $task.GetAwaiter().GetResult()
            $update = ConvertFrom-WoWSToolboxReleaseJson `
                -Json $json -CurrentVersion $script:AppVersion
            if (-not $update.UpdateAvailable) {
                Add-Log (Get-UiText `
                    "최신 버전 $($script:AppVersion)을 사용 중이에요." `
                    "WoWS Toolbox $($script:AppVersion) is up to date.")
                if ($manual) {
                    Show-UpdateMessage `
                        "최신 버전 $($script:AppVersion)을 사용 중이에요." `
                        "WoWS Toolbox $($script:AppVersion) is up to date."
                }
                return
            }
            Add-Log (Get-UiText `
                "새 버전 $($update.Version)을 찾았어요." `
                "WoWS Toolbox $($update.Version) is available.")
            if (-not $update.Installable) {
                $messageKo = "새 버전 $($update.Version)을 찾았지만 검증 가능한 설치 파일이 없어요. 릴리스 페이지를 열까요?"
                $messageEn = "WoWS Toolbox $($update.Version) is available, but no verifiable installer was found. Open the release page?"
                $answer = [Windows.MessageBox]::Show(
                    $window, (Get-UiText $messageKo $messageEn),
                    (Get-UiText '업데이트 확인' 'Update available'),
                    [Windows.MessageBoxButton]::YesNo,
                    [Windows.MessageBoxImage]::Warning
                )
                if ($answer -eq [Windows.MessageBoxResult]::Yes -and
                    -not [string]::IsNullOrWhiteSpace([string] $update.ReleaseUrl)) {
                    Start-Process ([string] $update.ReleaseUrl)
                }
                return
            }
            $questionKo = "WoWS Toolbox $($update.Version) 업데이트가 확인됐어요.`n지금 업데이트할까요?"
            $questionEn = "WoWS Toolbox $($update.Version) is available.`nUpdate now?"
            $answer = [Windows.MessageBox]::Show(
                $window, (Get-UiText $questionKo $questionEn),
                (Get-UiText '업데이트 확인' 'Update available'),
                [Windows.MessageBoxButton]::YesNo,
                [Windows.MessageBoxImage]::Question
            )
            if ($answer -eq [Windows.MessageBoxResult]::Yes) {
                Start-UpdateDownload $update
            }
        }
        catch {
            $message = $_.Exception.Message
            if ($manual) {
                Show-UpdateMessage "업데이트를 확인하지 못했어요.`n`n$message" `
                    "Could not check for updates.`n`n$message" `
                    -Image ([Windows.MessageBoxImage]::Warning)
            }
            else {
                Add-Log (Get-UiText "업데이트 자동 확인 실패: $message" "Automatic update check failed: $message") -ErrorLine
            }
        }
    }

    if ($null -ne $script:UpdateDownloadTask -and
        $script:UpdateDownloadTask.IsCompleted) {
        $task = $script:UpdateDownloadTask
        $update = $script:PendingUpdate
        $client = $script:UpdateDownloadClient
        $tempPath = $script:UpdateDownloadTempPath
        $finalPath = $script:UpdateDownloadFinalPath
        $script:UpdateDownloadTask = $null
        $script:UpdateDownloadClient = $null
        $script:PendingUpdate = $null
        $controls.CheckUpdateButton.IsEnabled = $true
        try {
            if ($task.IsCanceled) { throw '업데이트 다운로드가 취소됐어요.' }
            if ($task.IsFaulted) { throw $task.Exception.GetBaseException() }
            $actualHash = (Get-FileHash -LiteralPath $tempPath -Algorithm SHA256).Hash
            if (-not [StringComparer]::OrdinalIgnoreCase.Equals(
                $actualHash, [string] $update.Sha256
            )) {
                throw '다운로드한 설치 파일의 SHA-256이 GitHub 릴리스와 일치하지 않아요.'
            }
            Move-Item -LiteralPath $tempPath -Destination $finalPath -Force
            Start-VerifiedUpdateInstaller -Update $update -InstallerPath $finalPath
        }
        catch {
            Remove-UpdateDownloadTempFile
            Set-TopStatus '오류' '#FB7185'
            Show-UpdateMessage "업데이트를 설치하지 못했어요.`n`n$($_.Exception.Message)" `
                "Could not install the update.`n`n$($_.Exception.Message)" `
                -Image ([Windows.MessageBoxImage]::Error)
            Add-Log (Get-UiText "업데이트 실패: $($_.Exception.Message)" "Update failed: $($_.Exception.Message)") -ErrorLine
        }
        finally {
            if ($null -ne $client) { $client.Dispose() }
            $script:UpdateDownloadTempPath = ''
            $script:UpdateDownloadFinalPath = ''
        }
    }
}

function Stop-UpdateNetwork {
    if ($null -ne $script:UpdateDownloadClient) {
        try { $script:UpdateDownloadClient.CancelAsync() } catch {}
        try { $script:UpdateDownloadClient.Dispose() } catch {}
        $script:UpdateDownloadClient = $null
    }
    if ($null -ne $script:UpdateHttpClient) {
        try { $script:UpdateHttpClient.Dispose() } catch {}
        $script:UpdateHttpClient = $null
    }
    Remove-UpdateDownloadTempFile
}

function Update-OutputLabel {
    $controls.OutputPathLabel.Text = [string] $script:Settings.OutputPath
}

function Get-OptionalShipValue {
    param(
        [Parameter(Mandatory)] $Ship,
        [Parameter(Mandatory)] [string] $Name
    )
    $property = $Ship.PSObject.Properties[$Name]
    if ($null -eq $property -or $null -eq $property.Value) { return '' }
    return [string] $property.Value
}

function Get-ShipQueueKey {
    param(
        [Parameter(Mandatory)] [string] $Source,
        [Parameter(Mandatory)] $Ship,
        [string] $GamePath = ''
    )
    if ([string]::IsNullOrWhiteSpace($GamePath)) {
        $GamePath = Get-GamePath $Source
    }
    $identity = [string] $Ship.Id
    if ([string]::IsNullOrWhiteSpace($identity)) {
        $identity = if ($Source -eq 'legends') {
            [string] $Ship.GameParamsKey
        }
        else { [string] $Ship.GameParamsIndex }
    }
    if ($Source -eq 'legends') {
        $variantIdentity = Get-OptionalShipValue -Ship $Ship -Name 'ModelPath'
        if ([string]::IsNullOrWhiteSpace($variantIdentity)) {
            $variantIdentity = Get-OptionalShipValue -Ship $Ship -Name 'ShipResource'
        }
        $identity = "${identity}::${variantIdentity}"
    }
    $installToken = Get-GamePathToken $GamePath
    return "${Source}::${installToken}::$identity"
}

function Get-QueueEntryGamePath {
    param([Parameter(Mandatory)] $Entry)
    $property = $Entry.PSObject.Properties['GamePath']
    if ($null -ne $property -and
        -not [string]::IsNullOrWhiteSpace([string] $property.Value)) {
        return [string] $property.Value
    }
    return Get-GamePath ([string] $Entry.Source)
}

function New-ShipQueueEntry {
    param(
        [Parameter(Mandatory)] [string] $Source,
        [Parameter(Mandatory)] $Ship,
        [string] $GamePath = ''
    )
    if ([string]::IsNullOrWhiteSpace($GamePath)) {
        $GamePath = Get-GamePath $Source
    }
    $tier = if ([int] $Ship.Tier -gt 0) { "T$($Ship.Tier)" } else { 'T?' }
    $install = Get-GameInstallLabel $GamePath
    $sourceLabel = Get-SourceDisplay $Source
    $installLabel = if ($install.Equals($sourceLabel, [StringComparison]::OrdinalIgnoreCase)) {
        $sourceLabel
    }
    else { "$sourceLabel · $install" }
    [pscustomobject] @{
        Source = $Source
        GamePath = $GamePath
        Ship = $Ship
        Key = Get-ShipQueueKey -Source $Source -Ship $Ship -GamePath $GamePath
        Display = "$installLabel  ·  $($Ship.LocalizedName)  ·  $tier  ·  $($Ship.ShipCode)"
    }
}

function Update-SelectedShipUi {
    Update-QueueUi
}

function Save-Settings {
    Write-JsonAtomic -Path $script:SettingsPath -Value $script:Settings -Depth 4
    Set-WoWSToolboxLanguage ([string] $script:Settings.Language)
    [void] (Set-WoWSToolboxLanguageMarker `
        -PackageRoot $script:PackageRoot `
        -Language ([string] $script:Settings.Language))
}

function Select-Folder {
    param([string] $InitialPath)
    $dialog = [Windows.Forms.FolderBrowserDialog]::new()
    $dialog.Description = '폴더를 선택해 주세요.'
    $dialog.UseDescriptionForTitle = $true
    if (Test-Path -LiteralPath $InitialPath -PathType Container) {
        $dialog.SelectedPath = $InitialPath
    }
    try {
        if ($dialog.ShowDialog() -eq [Windows.Forms.DialogResult]::OK) {
            return $dialog.SelectedPath
        }
    }
    finally {
        $dialog.Dispose()
    }
    return $null
}

function Select-File {
    param(
        [string] $InitialPath,
        [string] $Filter
    )
    $dialog = [Microsoft.Win32.OpenFileDialog]::new()
    $dialog.Filter = $Filter
    if (Test-Path -LiteralPath $InitialPath -PathType Leaf) {
        $dialog.FileName = $InitialPath
        $dialog.InitialDirectory = Split-Path -Parent $InitialPath
    }
    if ($dialog.ShowDialog($window)) {
        return $dialog.FileName
    }
    return $null
}

function Find-OodleRuntime {
    $candidates = @(
        [string] $script:Settings.OodlePath,
        'D:\SteamLibrary\steamapps\common\Battlefield 6\oo2core_9_win64.dll',
        'C:\Program Files (x86)\Steam\steamapps\common\Battlefield 6\oo2core_9_win64.dll',
        'C:\Program Files\Steam\steamapps\common\Battlefield 6\oo2core_9_win64.dll'
    )
    foreach ($candidate in $candidates) {
        if (-not [string]::IsNullOrWhiteSpace($candidate) -and
            (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return $candidate
        }
    }
    foreach ($root in @(
        'D:\SteamLibrary\steamapps\common',
        'C:\Program Files (x86)\Steam\steamapps\common',
        'C:\Program Files\Steam\steamapps\common'
    )) {
        if (-not (Test-Path -LiteralPath $root -PathType Container)) { continue }
        $found = Get-ChildItem -LiteralPath $root -Filter 'oo2core_*_win64.dll' `
            -File -Recurse -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($null -ne $found) { return $found.FullName }
    }
    return ''
}

function Test-BlenderRequired {
    param(
        [string[]] $Sources,
        [string] $Formats
    )
    return $false
}
function Test-SettingsPaths {
    Sync-UiToSettings
    $problems = [Collections.Generic.List[string]]::new()
    $requiredSources = @($script:ExtractionQueue | ForEach-Object Source |
        Sort-Object -Unique)
    if ($requiredSources.Count -eq 0) { $requiredSources = @(Get-SourceKey) }
    foreach ($pair in @(
        @('legends', 'Legends', $script:Settings.LegendsPath),
        @('pc', 'PC', $script:Settings.PcPath),
        @('korabli', 'Korabli', $script:Settings.KorabliPath)
    )) {
        if ($requiredSources -contains $pair[0] -and
            -not (Test-Path -LiteralPath $pair[2] -PathType Container)) {
            $problems.Add("$($pair[1]) 경로 없음")
        }
    }
    $requestedFormats = Get-ComboTag $controls.FormatCombo
    $outputProblem = Get-OutputPathProblem -OutputPath ([string] $script:Settings.OutputPath) -GamePaths @(
        @($requiredSources | ForEach-Object { Get-GamePath ([string] $_) })
    )
    if (-not [string]::IsNullOrWhiteSpace($outputProblem)) {
        $problems.Add($outputProblem)
    }
    if ($problems.Count -eq 0) {
        $controls.SettingsStatus.Text = '경로 검사를 통과했어요.'
        $controls.SettingsStatus.Foreground =
            [Windows.Media.BrushConverter]::new().ConvertFrom('#6EE7B7')
        return $true
    }
    $controls.SettingsStatus.Text = ($problems -join ' · ')
    $controls.SettingsStatus.Foreground =
        [Windows.Media.BrushConverter]::new().ConvertFrom('#FCA5A5')
    return $false
}

function Start-ToolProcess {
    param(
        [string] $Operation,
        [string[]] $Arguments,
        [scriptblock] $Completion
    )
    if ($null -ne $script:ActiveRunner) {
        throw '이미 다른 작업이 실행 중이에요.'
    }
    $runner = [WoWSToolboxV5.GuiProcessRunner]::new()
    $env:WOWS_TOOLBOX_LANGUAGE = [string] $script:Settings.Language
    $runner.Start($script:PythonCommand, $Arguments, $script:PackageRoot)
    $script:ActiveRunner = $runner
    $script:ActiveOperation = $Operation
    $script:ActiveCompletion = $Completion
    $script:CancelRequested = $false
    Set-BusyState
}

function Get-CatalogPath {
    param([string] $Source)
    $installToken = Get-GamePathToken (Get-GamePath $Source)
    return Join-Path $script:CatalogRoot "$Source-$installToken.json"
}

function Load-CatalogFile {
    param(
        [string] $Source,
        [string] $Path
    )
    $rows = @(Get-Content -Raw -LiteralPath $Path |
        ConvertFrom-Json -ErrorAction Stop)
    $script:Catalogs[$Source] = @($rows | Where-Object {
        $supported = $_.PSObject.Properties['Supported']
        $null -eq $supported -or [bool] $supported.Value
    })
    Add-Log "$((Get-SourceDisplay $Source)) 함선 $($script:Catalogs[$Source].Count)개를 불러왔어요."
}

function Start-CatalogRefresh {
    param([switch] $OpenPickerAfter)
    $source = Get-SourceKey
    $gamePath = Get-GamePath $source
    if (-not (Test-Path -LiteralPath $gamePath -PathType Container)) {
        [Windows.MessageBox]::Show(
            $window,
            (Get-UiText "$(Get-SourceDisplay $source) 설치 경로를 설정에서 먼저 확인해 주세요." "Check the $(Get-SourceDisplay $source) installation path in Settings first."),
            (Get-UiText '게임 경로 없음' 'Game path missing'),
            [Windows.MessageBoxButton]::OK,
            [Windows.MessageBoxImage]::Warning
        ) | Out-Null
        return
    }
    $output = Get-CatalogPath $source
    $script:CatalogRefreshSource = $source
    $script:CatalogRefreshOutput = $output
    $script:PendingPicker = $OpenPickerAfter
    $controls.ProgressStage.Text = '함선 목록을 읽는 중'
    $controls.ProgressMessage.Text = '게임 패키지는 수정하지 않고 인덱스와 번역 데이터만 읽어요.'
    $controls.MainProgress.IsIndeterminate = $true
    Add-Log "$(Get-SourceDisplay $source) 함선 목록 새로고침을 시작해요."
    $catalogCompletion = {
        param($exitCode)
        $catalogSource = [string] $script:CatalogRefreshSource
        $catalogOutput = [string] $script:CatalogRefreshOutput
        $controls.MainProgress.IsIndeterminate = $false
        if ($exitCode -ne 0) {
            $controls.ProgressStage.Text = '목록 읽기 실패'
            $controls.ProgressMessage.Text = '로그에서 마지막 오류를 확인해 주세요.'
            $script:PendingPicker = $false
            return
        }
        Load-CatalogFile -Source $catalogSource -Path $catalogOutput
        $controls.ProgressStage.Text = '함선 목록 준비 완료'
        $controls.ProgressMessage.Text =
            "$($script:Catalogs[$catalogSource].Count)개 항목에서 원하는 함선을 고를 수 있어요."
        $controls.MainProgress.Value = 100
        if ($script:PendingPicker) {
            $script:PendingPicker = $false
            $window.Dispatcher.BeginInvoke([action] { Show-ShipPicker }) | Out-Null
        }
    }
    Start-ToolProcess -Operation "catalog:$source" -Arguments @(
        '-B', $script:CatalogScript,
        '--source', $source,
        '--game-dir', $gamePath,
        '--toolbox-root', $script:PackageRoot,
        '--language', [string] $script:Settings.Language,
        '--output', $output
    ) -Completion $catalogCompletion
}

function Show-ShipPicker {
    $source = Get-SourceKey
    if (-not $script:Catalogs.ContainsKey($source) -or
        $script:Catalogs[$source].Count -eq 0) {
        Start-CatalogRefresh -OpenPickerAfter
        return
    }
    $catalog = @($script:Catalogs[$source])
    $favoritesSet = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $recentSet = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($pair in @(
        @($script:FavoritesPath, $favoritesSet),
        @($script:RecentShipsPath, $recentSet)
    )) {
        if (Test-Path -LiteralPath $pair[0] -PathType Leaf) {
            try {
                foreach ($key in @(Get-Content -Raw -LiteralPath $pair[0] | ConvertFrom-Json)) {
                    [void] $pair[1].Add([string] $key)
                }
            }
            catch {}
        }
    }
    $queuedKeys = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::OrdinalIgnoreCase
    )
    foreach ($entry in @($script:ExtractionQueue | Where-Object Source -eq $source)) {
        [void] $queuedKeys.Add([string] $entry.Key)
    }
    foreach ($row in $catalog) {
        $rowKey = Get-ShipQueueKey -Source $source -Ship $row
        if ($null -eq $row.PSObject.Properties['QueueSelected']) {
            $row | Add-Member -NotePropertyName QueueSelected -NotePropertyValue $false
        }
        if ($null -eq $row.PSObject.Properties['Favorite']) {
            $row | Add-Member -NotePropertyName Favorite -NotePropertyValue $false
        }
        if ($null -eq $row.PSObject.Properties['Recent']) {
            $row | Add-Member -NotePropertyName Recent -NotePropertyValue $false
        }
        $row.QueueSelected = $queuedKeys.Contains($rowKey)
        $row.Favorite = $favoritesSet.Contains($rowKey)
        $row.Recent = $recentSet.Contains($rowKey)
    }
    $pickerXaml = @'
<Window
    xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
    xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
    Title="함선 찾아 선택" Width="1120" Height="720"
    MinWidth="900" MinHeight="580"
    WindowStartupLocation="CenterOwner"
    Background="#08111F" Foreground="#E8EEF8"
    FontFamily="Segoe UI" FontSize="12">
    <Window.Resources>
        <Style TargetType="TextBox">
            <Setter Property="Background" Value="#0B1728"/>
            <Setter Property="Foreground" Value="#E8EEF8"/>
            <Setter Property="BorderBrush" Value="#2A3E5B"/>
            <Setter Property="Padding" Value="9,7"/>
        </Style>
        <Style TargetType="ComboBox">
            <Setter Property="Background" Value="#13233B"/>
            <Setter Property="Foreground" Value="#E8EEF8"/>
            <Setter Property="BorderBrush" Value="#2A3E5B"/>
            <Setter Property="Padding" Value="7,5"/>
        </Style>
        <Style TargetType="Button">
            <Setter Property="Background" Value="#172943"/>
            <Setter Property="Foreground" Value="#E8EEF8"/>
            <Setter Property="BorderBrush" Value="#2A4264"/>
            <Setter Property="Padding" Value="15,8"/>
            <Setter Property="FontWeight" Value="SemiBold"/>
        </Style>
        <Style TargetType="DataGrid">
            <Setter Property="Background" Value="#0A1525"/>
            <Setter Property="Foreground" Value="#E4EBF5"/>
            <Setter Property="BorderBrush" Value="#263B59"/>
            <Setter Property="GridLinesVisibility" Value="Horizontal"/>
            <Setter Property="HorizontalGridLinesBrush" Value="#1C2D46"/>
            <Setter Property="RowBackground" Value="#0D1A2C"/>
            <Setter Property="AlternatingRowBackground" Value="#101F34"/>
            <Setter Property="HeadersVisibility" Value="Column"/>
            <Setter Property="CanUserAddRows" Value="False"/>
            <Setter Property="IsReadOnly" Value="False"/>
        </Style>
        <Style TargetType="DataGridColumnHeader">
            <Setter Property="Background" Value="#152944"/>
            <Setter Property="Foreground" Value="#F2F7FF"/>
            <Setter Property="BorderBrush" Value="#2D4667"/>
            <Setter Property="BorderThickness" Value="0,0,1,1"/>
            <Setter Property="Padding" Value="10,8"/>
            <Setter Property="FontWeight" Value="SemiBold"/>
            <Setter Property="HorizontalContentAlignment" Value="Left"/>
        </Style>
        <Style TargetType="DataGridRow">
            <Setter Property="Foreground" Value="#E4EBF5"/>
            <Style.Triggers>
                <Trigger Property="IsMouseOver" Value="True">
                    <Setter Property="Background" Value="#17304D"/>
                </Trigger>
                <Trigger Property="IsSelected" Value="True">
                    <Setter Property="Background" Value="#1D5686"/>
                    <Setter Property="Foreground" Value="#FFFFFF"/>
                </Trigger>
            </Style.Triggers>
        </Style>
        <Style TargetType="DataGridCell">
            <Setter Property="BorderThickness" Value="0"/>
            <Setter Property="Padding" Value="7,5"/>
            <Setter Property="VerticalContentAlignment" Value="Center"/>
            <Style.Triggers>
                <Trigger Property="IsSelected" Value="True">
                    <Setter Property="Background" Value="Transparent"/>
                    <Setter Property="Foreground" Value="#FFFFFF"/>
                    <Setter Property="BorderThickness" Value="0"/>
                </Trigger>
            </Style.Triggers>
        </Style>
    </Window.Resources>
    <Grid Margin="22">
        <Grid.RowDefinitions>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="*"/>
            <RowDefinition Height="Auto"/>
        </Grid.RowDefinitions>
        <Grid>
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="260"/>
            </Grid.ColumnDefinitions>
            <StackPanel>
                <TextBlock Text="함선 찾아 선택" FontSize="24" FontWeight="SemiBold"/>
                <TextBlock Text="행을 더블클릭하면 바로 적용하고, 왼쪽 추출 칸을 체크하면 여러 함선을 함께 담을 수 있어요."
                           Foreground="#8FA2BC" Margin="0,5,0,0"/>
            </StackPanel>
            <Border Grid.Column="1" Background="#10213A" BorderBrush="#234166"
                    BorderThickness="1" CornerRadius="9" Padding="12"
                    VerticalAlignment="Center">
                <TextBlock x:Name="PickerSourceLabel" Text="게임 소스"/>
            </Border>
        </Grid>
        <Grid Grid.Row="1" Margin="0,18,0,12">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="150"/>
                <ColumnDefinition Width="150"/>
                <ColumnDefinition Width="120"/>
                <ColumnDefinition Width="Auto"/>
            </Grid.ColumnDefinitions>
            <TextBox x:Name="SearchBox"
                     ToolTip="인게임 이름, 코드, 내부 변형 검색"/>
            <ComboBox Grid.Column="1" x:Name="NationCombo" Margin="8,0,0,0"/>
            <ComboBox Grid.Column="2" x:Name="ClassCombo" Margin="8,0,0,0"/>
            <ComboBox Grid.Column="3" x:Name="TierCombo" Margin="8,0,0,0"/>
            <Button Grid.Column="4" x:Name="ResetButton" Content="필터 초기화"
                    Margin="8,0,0,0"/>
        </Grid>
        <Grid Grid.Row="2">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="2.1*"/>
                <ColumnDefinition Width="1*"/>
            </Grid.ColumnDefinitions>
            <DataGrid x:Name="ShipGrid" AutoGenerateColumns="False"
                      AlternationCount="2" SelectionMode="Single"
                      SelectionUnit="FullRow">
                <DataGrid.Columns>
                    <DataGridTemplateColumn Header="추출" Width="58" IsReadOnly="True">
                        <DataGridTemplateColumn.CellTemplate>
                            <DataTemplate>
                                <CheckBox IsChecked="{Binding QueueSelected, Mode=TwoWay, UpdateSourceTrigger=PropertyChanged}"
                                          HorizontalAlignment="Center" VerticalAlignment="Center"
                                          ToolTip="이 함선을 추출 대기열에 포함"/>
                            </DataTemplate>
                        </DataGridTemplateColumn.CellTemplate>
                    </DataGridTemplateColumn>
                    <DataGridTemplateColumn Header="★" Width="42" IsReadOnly="True">
                        <DataGridTemplateColumn.CellTemplate>
                            <DataTemplate>
                                <CheckBox IsChecked="{Binding Favorite, Mode=TwoWay, UpdateSourceTrigger=PropertyChanged}"
                                          HorizontalAlignment="Center" VerticalAlignment="Center"
                                          ToolTip="즐겨찾기"/>
                            </DataTemplate>
                        </DataGridTemplateColumn.CellTemplate>
                    </DataGridTemplateColumn>
                    <DataGridTextColumn Header="인게임 이름" IsReadOnly="True"
                                        Binding="{Binding LocalizedName}" Width="*"/>
                    <DataGridTextColumn Header="국가" IsReadOnly="True"
                                        Binding="{Binding Nation}" Width="88"/>
                    <DataGridTextColumn Header="함종" IsReadOnly="True"
                                        Binding="{Binding ShipClass}" Width="82"/>
                    <DataGridTextColumn Header="티어" IsReadOnly="True"
                                        Binding="{Binding Tier}" Width="52"/>
                    <DataGridTextColumn Header="코드" IsReadOnly="True"
                                        Binding="{Binding ShipCode}" Width="92"/>
                </DataGrid.Columns>
            </DataGrid>
            <Border Grid.Column="1" Background="#101E33" BorderBrush="#263B59"
                    BorderThickness="1" CornerRadius="10" Padding="18"
                    Margin="12,0,0,0">
                <StackPanel>
                    <TextBlock Text="현재 행" Foreground="#8498B2"
                               FontSize="11" FontWeight="SemiBold"/>
                    <TextBlock x:Name="DetailName" Text="왼쪽에서 함선을 골라 주세요."
                               FontSize="19" FontWeight="SemiBold"
                               TextWrapping="Wrap" Margin="0,10,0,0"/>
                    <TextBlock x:Name="DetailMeta" Foreground="#91A4BD"
                               TextWrapping="Wrap" Margin="0,8,0,0"/>
                    <Border Background="#0A1525" BorderBrush="#223653"
                            BorderThickness="1" CornerRadius="8" Padding="12"
                            Margin="0,16,0,0">
                        <StackPanel>
                            <TextBlock Text="내부 변형" Foreground="#7489A4" FontSize="10"/>
                            <TextBlock x:Name="DetailVariant" Text="-"
                                       TextWrapping="Wrap" Margin="0,4,0,0"/>
                            <TextBlock Text="GameParams" Foreground="#7489A4"
                                       FontSize="10" Margin="0,13,0,0"/>
                            <TextBlock x:Name="DetailKey" Text="-"
                                       FontFamily="Consolas" FontSize="10"
                                       TextWrapping="Wrap" Margin="0,4,0,0"/>
                        </StackPanel>
                    </Border>
                    <TextBlock Text="행을 더블클릭하면 바로 적용해요. 체크한 항목은 필터를 바꿔도 유지되고, 적용을 누르면 현재 게임의 대기열만 갱신해요."
                               Foreground="#60758F" FontSize="10"
                               TextWrapping="Wrap" Margin="0,16,0,0"/>
                </StackPanel>
            </Border>
        </Grid>
        <Grid Grid.Row="3" Margin="0,14,0,0">
            <TextBlock x:Name="CountLabel" Foreground="#8194AD"
                       VerticalAlignment="Center"/>
            <StackPanel Orientation="Horizontal" HorizontalAlignment="Right">
                                <Button x:Name="FavoriteOnlyButton" Content="즐겨찾기만" Margin="0,0,8,0"/>
                <Button x:Name="RecentOnlyButton" Content="최근 추출만" Margin="0,0,8,0"/>
                <Button x:Name="SelectVisibleButton" Content="표시 전체 체크" Margin="0,0,8,0"/>
                <Button x:Name="ClearVisibleButton" Content="표시 체크 해제" Margin="0,0,8,0"/>
                <Button x:Name="CancelPickerButton" Content="취소" Margin="0,0,8,0"/>
                <Button x:Name="ChooseButton" Content="대기열에 적용"
                        Background="#2563EB"/>
            </StackPanel>
        </Grid>
    </Grid>
</Window>
'@
    $pickerXaml = Convert-XamlToUiLanguage $pickerXaml
    $pickerReader = [Xml.XmlNodeReader]::new([xml] $pickerXaml)
    $dialog = [Windows.Markup.XamlReader]::Load($pickerReader)
    $dialog.Owner = $window
    $dialog.Resources[[Windows.Controls.ComboBox]] =
        $window.Resources[[Windows.Controls.ComboBox]]
    $dialog.Resources[[Windows.Controls.ComboBoxItem]] =
        $window.Resources[[Windows.Controls.ComboBoxItem]]
    $search = $dialog.FindName('SearchBox')
    $nation = $dialog.FindName('NationCombo')
    $class = $dialog.FindName('ClassCombo')
    $tier = $dialog.FindName('TierCombo')
    $grid = $dialog.FindName('ShipGrid')
    $count = $dialog.FindName('CountLabel')
    $detailName = $dialog.FindName('DetailName')
    $detailMeta = $dialog.FindName('DetailMeta')
    $detailVariant = $dialog.FindName('DetailVariant')
    $detailKey = $dialog.FindName('DetailKey')
    $choose = $dialog.FindName('ChooseButton')
    $favoriteOnlyButton = $dialog.FindName('FavoriteOnlyButton')
    $recentOnlyButton = $dialog.FindName('RecentOnlyButton')
    $selectVisibleButton = $dialog.FindName('SelectVisibleButton')
    $clearVisibleButton = $dialog.FindName('ClearVisibleButton')
    $favoriteOnly = $false
    $recentOnly = $false
    $dialog.FindName('PickerSourceLabel').Text = Get-SourceDisplay $source

    $allLabel = Get-UiText '전체' 'All'
    foreach ($combo in @($nation, $class, $tier)) {
        [void] $combo.Items.Add($allLabel)
        $combo.SelectedIndex = 0
    }
    foreach ($value in @($catalog.Nation | Sort-Object -Unique)) {
        if (-not [string]::IsNullOrWhiteSpace([string] $value)) {
            [void] $nation.Items.Add([string] $value)
        }
    }
    foreach ($value in @($catalog.ShipClass | Sort-Object -Unique)) {
        if (-not [string]::IsNullOrWhiteSpace([string] $value)) {
            [void] $class.Items.Add([string] $value)
        }
    }
    foreach ($value in @($catalog.Tier | Where-Object { [int] $_ -gt 0 } |
        Sort-Object -Unique)) {
        [void] $tier.Items.Add([string] $value)
    }

    $updateQueueCount = {
        $selectedCount = @($catalog | Where-Object { [bool] $_.QueueSelected }).Count
        $choose.Content = if ($selectedCount -gt 0) {
            Get-UiText "${selectedCount}척 대기열에 적용" "Apply $selectedCount ships to queue"
        }
        else { Get-UiText '빈 대기열로 적용' 'Apply empty queue' }
    }
    $applyFilter = {
        $query = $search.Text.Trim()
        $nationValue = [string] $nation.SelectedItem
        $classValue = [string] $class.SelectedItem
        $tierValue = [string] $tier.SelectedItem
        $filtered = @($catalog | Where-Object {
            $row = $_
            $searchable = "{0} {1} {2} {3}" -f
                [string] $row.LocalizedName,
                [string] $row.ShipCode,
                [string] $row.VariantLabel,
                [string] $row.GameParamsKey
            $matchesQuery = [string]::IsNullOrWhiteSpace($query) -or
                $searchable.IndexOf(
                    $query,
                    [StringComparison]::OrdinalIgnoreCase
                ) -ge 0
            $matchesNation = $nationValue -eq $allLabel -or
                [string] $row.Nation -eq $nationValue
            $matchesClass = $classValue -eq $allLabel -or
                [string] $row.ShipClass -eq $classValue
            $matchesTier = $tierValue -eq $allLabel -or
                [string] $row.Tier -eq $tierValue
            $matchesQuick = (-not $favoriteOnly -or [bool] $row.Favorite) -and
                (-not $recentOnly -or [bool] $row.Recent)
            $matchesQuery -and $matchesNation -and
                $matchesClass -and $matchesTier -and $matchesQuick
        })
        $grid.ItemsSource = $filtered
        $selectedCount = @($catalog | Where-Object { [bool] $_.QueueSelected }).Count
        $count.Text = Get-UiText "표시 $($filtered.Count) / 전체 $($catalog.Count) · 체크 $selectedCount" "Showing $($filtered.Count) / $($catalog.Count) · checked $selectedCount"
        & $updateQueueCount
    }
    $updateDetail = {
        $row = $grid.SelectedItem
        if ($null -eq $row) {
            $detailName.Text = Get-UiText '왼쪽에서 함선을 골라 주세요.' 'Select a ship on the left.'
            $detailMeta.Text = ''
            $detailVariant.Text = '-'
            $detailKey.Text = '-'
            return
        }
        $detailName.Text = [string] $row.LocalizedName
        $detailMeta.Text = "$($row.Nation) · $($row.ShipClass) · Tier $($row.Tier) · $($row.ShipCode)"
        $detailVariant.Text = [string] $row.VariantLabel
        $detailKey.Text = [string] $row.GameParamsKey
    }
    $search.Add_TextChanged($applyFilter)
    $nation.Add_SelectionChanged($applyFilter)
    $class.Add_SelectionChanged($applyFilter)
    $tier.Add_SelectionChanged($applyFilter)
    $grid.Add_SelectionChanged($updateDetail)
    $grid.Add_PreviewMouseLeftButtonUp({
        $dialog.Dispatcher.BeginInvoke(
            [Windows.Threading.DispatcherPriority]::Background,
            [action] {
                & $updateQueueCount
                $selectedCount = @($catalog | Where-Object { [bool] $_.QueueSelected }).Count
                $count.Text = Get-UiText "표시 $(@($grid.ItemsSource).Count) / 전체 $($catalog.Count) · 체크 $selectedCount" "Showing $(@($grid.ItemsSource).Count) / $($catalog.Count) · checked $selectedCount"
            }
        ) | Out-Null
    })
    $grid.Add_CurrentCellChanged({
        $dialog.Dispatcher.BeginInvoke(
            [Windows.Threading.DispatcherPriority]::Background,
            [action] {
                & $updateQueueCount
                $selectedCount = @($catalog | Where-Object { [bool] $_.QueueSelected }).Count
                $count.Text = Get-UiText "표시 $(@($grid.ItemsSource).Count) / 전체 $($catalog.Count) · 체크 $selectedCount" "Showing $(@($grid.ItemsSource).Count) / $($catalog.Count) · checked $selectedCount"
            }
        ) | Out-Null
    })
    $dialog.FindName('ResetButton').Add_Click({
        $search.Clear()
        $nation.SelectedIndex = 0
        $class.SelectedIndex = 0
        $tier.SelectedIndex = 0
    })
    $favoriteOnlyButton.Add_Click({
        $favoriteOnly = -not $favoriteOnly
        $favoriteOnlyButton.Background = if ($favoriteOnly) { '#2563EB' } else { '#172943' }
        & $applyFilter
    })
    $recentOnlyButton.Add_Click({
        $recentOnly = -not $recentOnly
        $recentOnlyButton.Background = if ($recentOnly) { '#2563EB' } else { '#172943' }
        & $applyFilter
    })
    $selectVisibleButton.Add_Click({
        foreach ($row in @($grid.ItemsSource)) { $row.QueueSelected = $true }
        $grid.Items.Refresh()
        & $updateQueueCount
        $count.Text = Get-UiText "표시 $(@($grid.ItemsSource).Count) / 전체 $($catalog.Count) · 체크 $(@($catalog | Where-Object QueueSelected).Count)" "Showing $(@($grid.ItemsSource).Count) / $($catalog.Count) · checked $(@($catalog | Where-Object QueueSelected).Count)"
    })
    $clearVisibleButton.Add_Click({
        foreach ($row in @($grid.ItemsSource)) { $row.QueueSelected = $false }
        $grid.Items.Refresh()
        & $updateQueueCount
        $count.Text = Get-UiText "표시 $(@($grid.ItemsSource).Count) / 전체 $($catalog.Count) · 체크 $(@($catalog | Where-Object QueueSelected).Count)" "Showing $(@($grid.ItemsSource).Count) / $($catalog.Count) · checked $(@($catalog | Where-Object QueueSelected).Count)"
    })
    $applyPickerSelection = {
        [void] $grid.CommitEdit([Windows.Controls.DataGridEditingUnit]::Cell, $true)
        [void] $grid.CommitEdit([Windows.Controls.DataGridEditingUnit]::Row, $true)
        $favoriteKeys = @($catalog | Where-Object { [bool] $_.Favorite } | ForEach-Object {
            Get-ShipQueueKey -Source $source -Ship $_
        })
        Write-JsonAtomic -Path $script:FavoritesPath -Value $favoriteKeys -Depth 4
        $dialog.Tag = @($catalog | Where-Object { [bool] $_.QueueSelected })
        $dialog.DialogResult = $true
    }
    $getPickerRowFromSource = {
        param([object] $OriginalSource)
        $node = $OriginalSource
        $cell = $null
        while ($null -ne $node) {
            if ($node -is [Windows.Controls.Primitives.ButtonBase]) { return $null }
            if ($node -is [Windows.Controls.DataGridCell]) { $cell = $node }
            if ($node -is [Windows.Controls.DataGridRow]) {
                if ($null -ne $cell -and $cell.Column.DisplayIndex -lt 2) { return $null }
                return $node
            }
            try {
                $node = [Windows.Media.VisualTreeHelper]::GetParent($node)
            }
            catch { return $null }
        }
        return $null
    }
    $grid.Add_MouseDoubleClick({
        param($sender, $eventArgs)
        $rowContainer = & $getPickerRowFromSource $eventArgs.OriginalSource
        if ($null -eq $rowContainer -or $null -eq $rowContainer.Item) { return }
        $rowContainer.Item.QueueSelected = $true
        $grid.SelectedItem = $rowContainer.Item
        $grid.Items.Refresh()
        & $updateQueueCount
        $eventArgs.Handled = $true
        & $applyPickerSelection
    })
    $choose.Add_Click({
        & $applyPickerSelection
    })
    $dialog.FindName('CancelPickerButton').Add_Click({ $dialog.Close() })
    & $applyFilter
    $firstChecked = @($catalog | Where-Object { [bool] $_.QueueSelected } |
        Select-Object -First 1)
    if ($firstChecked.Count -gt 0) {
        $grid.SelectedItem = $firstChecked[0]
        $grid.ScrollIntoView($firstChecked[0])
    }
    if ($dialog.ShowDialog()) {
        for ($index = $script:ExtractionQueue.Count - 1; $index -ge 0; $index--) {
            if ([string] $script:ExtractionQueue[$index].Source -eq $source) {
                $script:ExtractionQueue.RemoveAt($index)
            }
        }
        foreach ($ship in @($dialog.Tag)) {
            $script:ExtractionQueue.Add(
                (New-ShipQueueEntry -Source $source -Ship $ship)
            )
        }
        $selectedRows = @($dialog.Tag)
        $script:SelectedShip = if ($selectedRows.Count -gt 0) {
            $selectedRows[-1]
        }
        else { $null }
        $script:SelectedSource = $source
        Update-QueueUi
        Add-Log "$(Get-SourceDisplay $source) 대기열을 $($selectedRows.Count)척으로 갱신했어요."
    }
}
function Test-PathInside {
    param(
        [Parameter(Mandatory)] [string] $Candidate,
        [Parameter(Mandatory)] [string] $Parent
    )
    $candidateFull = [IO.Path]::GetFullPath($Candidate).TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    )
    $parentFull = [IO.Path]::GetFullPath($Parent).TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    )
    if ($candidateFull.Equals($parentFull, [StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }
    $prefix = $parentFull + [IO.Path]::DirectorySeparatorChar
    return $candidateFull.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
}

function Get-OutputPathProblem {
    param([string] $OutputPath, [string[]] $GamePaths)
    if ([string]::IsNullOrWhiteSpace($OutputPath)) { return '출력 폴더가 비어 있어요' }
    try {
        $output = [IO.Path]::GetFullPath($OutputPath)
        $root = [IO.Path]::GetPathRoot($output)
        if ($output.TrimEnd('\', '/').Equals(
                $root.TrimEnd('\', '/'),
                [StringComparison]::OrdinalIgnoreCase
            )) {
            return '드라이브 루트는 출력 폴더로 사용할 수 없어요'
        }
        foreach ($gamePath in $GamePaths) {
            if ([string]::IsNullOrWhiteSpace($gamePath)) { continue }
            $game = [IO.Path]::GetFullPath($gamePath)
            if ((Test-PathInside -Candidate $output -Parent $game) -or
                (Test-PathInside -Candidate $game -Parent $output)) {
                return '출력 폴더와 게임 설치 폴더는 서로 완전히 분리해야 해요'
            }
        }
    }
    catch { return "출력 폴더 경로가 잘못됐어요: $($_.Exception.Message)" }
    return ''
}

function Test-ExtractionReady {
    $problems = [Collections.Generic.List[string]]::new()
    if ($script:ExtractionQueue.Count -eq 0) {
        $problems.Add('추출 대기열이 비어 있어요')
    }
    $checkedInstalls = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::OrdinalIgnoreCase
    )
    foreach ($entry in $script:ExtractionQueue) {
        $source = [string] $entry.Source
        $gamePath = Get-QueueEntryGamePath $entry
        $installKey = "$source|$(Get-NormalizedGamePath $gamePath)"
        if (-not $checkedInstalls.Add($installKey)) { continue }
        if (-not (Test-Path -LiteralPath $gamePath -PathType Container)) {
            $problems.Add("$(Get-SourceDisplay $source) 설치 폴더를 찾지 못했어요: $gamePath")
            continue
        }
        $detectedSource = Get-GameFolderSource $gamePath
        if ($detectedSource -ne $source) {
            $problems.Add("게임 종류와 설치 폴더가 맞지 않아요: $gamePath")
            continue
        }
        $folderProblem = Get-GameFolderProblem -Source $source -Path $gamePath
        if (-not [string]::IsNullOrWhiteSpace($folderProblem)) {
            $problems.Add("$(Get-GameInstallLabel $gamePath): $folderProblem")
        }
    }
    $requestedSources = @($script:ExtractionQueue | ForEach-Object Source | Sort-Object -Unique)
    $requestedFormats = Get-ComboTag $controls.FormatCombo
    if ($requestedFormats -ne 'obj') {
        $problems.Add((Get-UiText 'WoWS Toolbox는 세 게임 모두 Blender 없이 OBJ만 지원해요' 'WoWS Toolbox supports OBJ only without Blender for all three games'))
    }
    $outputProblem = Get-OutputPathProblem -OutputPath ([string] $script:Settings.OutputPath) -GamePaths @(
        @($script:ExtractionQueue | ForEach-Object {
            Get-QueueEntryGamePath $_
        } | Sort-Object -Unique)
    )
    if (-not [string]::IsNullOrWhiteSpace($outputProblem)) {
        $problems.Add($outputProblem)
    }
    if (@($script:ExtractionQueue | Where-Object Source -eq 'korabli').Count -gt 0) {
        $oodle = Find-OodleRuntime
        if ([string]::IsNullOrWhiteSpace($oodle)) {
            $problems.Add('코라블리 Oodle 런타임을 찾지 못했어요')
        }
        else {
            $script:Settings.OodlePath = $oodle
            $controls.OodlePathBox.Text = $oodle
        }
    }
    if ($problems.Count -gt 0) {
        $controls.ProgressStage.Text = '추출 준비 필요'
        $controls.ProgressMessage.Text = $problems -join ' · '
        Add-Log ($problems -join ' · ') -ErrorLine
        return $false
    }
    $controls.ProgressStage.Text = '대기열 준비 완료'
    $controls.ProgressMessage.Text =
        "$($script:ExtractionQueue.Count)척을 차례대로 선택한 편집 형식으로 내보낼 준비가 됐어요."
    Add-Log "대기열 $($script:ExtractionQueue.Count)척 준비 검사를 통과했어요."
    return $true
}

function Remove-SucceededQueueItems {
    $completedKeys = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::OrdinalIgnoreCase
    )
    foreach ($entry in $script:BatchSucceeded) {
        [void] $completedKeys.Add([string] $entry.Key)
    }
    for ($index = $script:ExtractionQueue.Count - 1; $index -ge 0; $index--) {
        if ($completedKeys.Contains([string] $script:ExtractionQueue[$index].Key)) {
            $script:ExtractionQueue.RemoveAt($index)
        }
    }
}

function Show-CompletionNotification {
    param(
        [Parameter(Mandatory)] [string] $Title,
        [Parameter(Mandatory)] [string] $Message,
        [ValidateSet('Info', 'Warning', 'Error')]
        [string] $Kind = 'Info'
    )
    if ([string] $script:Settings.NotifyComplete -ne 'true') { return }
    try {
        if ($null -ne $script:NotifyTimer) {
            $script:NotifyTimer.Stop()
            $script:NotifyTimer = $null
        }
        if ($null -ne $script:NotifyIcon) {
            $script:NotifyIcon.Visible = $false
            $script:NotifyIcon.Dispose()
        }
        $script:NotifyIcon = [Windows.Forms.NotifyIcon]::new()
        $script:NotifyIcon.Icon = if ($null -ne $script:AppIcon) {
            $script:AppIcon
        }
        else {
            switch ($Kind) {
                'Warning' { [Drawing.SystemIcons]::Warning }
                'Error' { [Drawing.SystemIcons]::Error }
                default { [Drawing.SystemIcons]::Information }
            }
        }
        $script:NotifyIcon.Visible = $true
        $script:NotifyIcon.BalloonTipTitle = $Title
        $script:NotifyIcon.BalloonTipText = $Message
        $script:NotifyIcon.BalloonTipIcon = [Windows.Forms.ToolTipIcon]::$Kind
        $script:NotifyIcon.ShowBalloonTip(6000)
        $script:NotifyTimer = [Windows.Threading.DispatcherTimer]::new()
        $script:NotifyTimer.Interval = [TimeSpan]::FromSeconds(8)
        $script:NotifyTimer.Add_Tick({
            $script:NotifyTimer.Stop()
            $script:NotifyIcon.Visible = $false
            $script:NotifyIcon.Dispose()
            $script:NotifyIcon = $null
            $script:NotifyTimer = $null
        })
        $script:NotifyTimer.Start()
    }
    catch {
        Add-Log "완료 알림을 표시하지 못했어요: $($_.Exception.Message)" -ErrorLine
    }
}
function Finish-BatchExtraction {
    param([switch] $Cancelled)
    $total = @($script:BatchItems).Count
    $successCount = $script:BatchSucceeded.Count
    $failureCount = $script:BatchFailed.Count
    Remove-SucceededQueueItems
    $script:BatchActive = $false
    $script:BatchCurrentItem = $null
    $script:ActiveOperation = ''
    Set-BusyState
    Update-QueueUi

    if ($Cancelled) {
        $controls.ProgressStage.Text = '대기열 추출 취소됨'
        $controls.ProgressMessage.Text =
            "성공 ${successCount}척 · 실패/미완료 항목은 대기열에 남겼어요. 부분 출력은 자동 삭제하지 않았어요."
        Set-TopStatus '취소됨' '#FBBF24'
        Add-Log "대기열 취소: 성공 $successCount / 전체 $total"
        return
    }

    $controls.MainProgress.IsIndeterminate = $false
    $controls.MainProgress.Value = 100
    if ($failureCount -eq 0) {
        $controls.ProgressStage.Text = '대기열 추출 완료'
        $controls.ProgressMessage.Text = "${successCount}척을 모두 저장했어요."
        Set-TopStatus '완료' '#34D399'
        Add-Log "대기열 완료: $successCount / $total"
        Show-CompletionNotification -Title 'WoWS Toolbox 추출 완료' `
            -Message "${successCount}척 저장 완료 · 출력 폴더를 확인해 주세요."
        [Windows.MessageBox]::Show(
            $window,
            (Get-UiText "대기열 추출이 끝났어요.`n`n성공: ${successCount}척`n출력: $($script:Settings.OutputPath)" "Queue extraction completed.`n`nSucceeded: $successCount ships`nOutput: $($script:Settings.OutputPath)"),
            (Get-UiText '대기열 추출 완료' 'Queue extraction complete'),
            [Windows.MessageBoxButton]::OK,
            [Windows.MessageBoxImage]::Information
        ) | Out-Null
    }
    else {
        $failedNames = @($script:BatchFailed | ForEach-Object {
            $_.Entry.Ship.LocalizedName
        }) -join ', '
        $controls.ProgressStage.Text = '일부 함선 추출 실패'
        $controls.ProgressMessage.Text =
            "성공 ${successCount}척 · 실패 ${failureCount}척 · 실패 항목만 대기열에 남겼어요."
        Set-TopStatus '일부 실패' '#F87171'
        Add-Log "대기열 종료: 성공 $successCount / 실패 $failureCount — $failedNames" -ErrorLine
        Show-CompletionNotification -Title 'WoWS Toolbox 일부 실패' `
            -Message "성공 ${successCount}척 · 실패 ${failureCount}척" -Kind Warning
        [Windows.MessageBox]::Show(
            $window,
            (Get-UiText "대기열 처리가 끝났지만 일부 함선은 실패했어요.`n`n성공: ${successCount}척`n실패: ${failureCount}척`n$failedNames`n`n실패한 항목만 대기열에 남아 있어요." "The queue finished, but some ships failed.`n`nSucceeded: $successCount ships`nFailed: $failureCount ships`n$failedNames`n`nOnly failed items remain in the queue."),
            (Get-UiText '일부 함선 추출 실패' 'Some ship extractions failed'),
            [Windows.MessageBoxButton]::OK,
            [Windows.MessageBoxImage]::Warning
        ) | Out-Null
    }
}

function Complete-BatchItem {
    param([int] $ExitCode)
    $entry = $script:BatchCurrentItem
    if ($ExitCode -eq 0 -and $null -ne $script:LastResult -and
        [bool] $script:LastResult.ok) {
        $script:BatchSucceeded.Add($entry)
        $script:LastOutputDir = [string] $script:LastResult.output_dir
        Add-Log "완료: $($entry.Ship.LocalizedName) — $($script:LastResult.obj)"
    }
    elseif (-not $script:CancelRequested) {
        $script:BatchFailed.Add([pscustomobject] @{
            Entry = $entry
            ExitCode = $ExitCode
        })
        Add-Log "실패: $($entry.Ship.LocalizedName) (exit $ExitCode)" -ErrorLine
    }
    $script:BatchIndex++
    if ($script:CancelRequested) {
        Finish-BatchExtraction -Cancelled
        return
    }
    $window.Dispatcher.BeginInvoke([action] { Start-NextBatchExtraction }) | Out-Null
}

function Start-NextBatchExtraction {
    if (-not $script:BatchActive) { return }
    if ($script:BatchIndex -ge @($script:BatchItems).Count) {
        Finish-BatchExtraction
        return
    }
    $entry = $script:BatchItems[$script:BatchIndex]
    $script:BatchCurrentItem = $entry
    $source = [string] $entry.Source
    $ship = $entry.Ship
    $position = $script:BatchIndex + 1
    $total = @($script:BatchItems).Count

    $arguments = [Collections.Generic.List[string]]::new()
    foreach ($value in @(
        '-B', $script:ExtractScript,
        '--source', $source,
        '--game-dir', (Get-QueueEntryGamePath $entry),
        '--toolbox-root', $script:PackageRoot,
        '--output-root', [string] $script:Settings.OutputPath,
        '--display-name', [string] $ship.LocalizedName
    )) {
        $arguments.Add([string] $value)
    }
    if ($source -eq 'legends') {
        $arguments.Add('--ship-key')
        $arguments.Add([string] $ship.GameParamsKey)
        $selectedModelPath = Get-OptionalShipValue -Ship $ship -Name 'ModelPath'
        $shipResource = Get-OptionalShipValue -Ship $ship -Name 'ShipResource'
        if (-not [string]::IsNullOrWhiteSpace($selectedModelPath)) {
            $arguments.Add('--selected-model-path')
            $arguments.Add($selectedModelPath)
        }
        if (-not [string]::IsNullOrWhiteSpace($shipResource)) {
            $arguments.Add('--ship-resource')
            $arguments.Add($shipResource)
        }
    }
    else {
        $arguments.Add('--ship-index')
        $arguments.Add([string] $ship.GameParamsIndex)
    }
    if ($source -eq 'korabli' -and
        -not [string]::IsNullOrWhiteSpace([string] $script:Settings.OodlePath)) {
        $arguments.Add('--oodle-dll')
        $arguments.Add([string] $script:Settings.OodlePath)
    }
    if ($controls.OverwriteCheck.IsChecked) {
        $arguments.Add('--overwrite')
    }

    $script:LastResult = $null
    $controls.MainProgress.IsIndeterminate = $false
    $controls.MainProgress.Value = [math]::Round(100 * $script:BatchIndex / $total, 1)
    $controls.ProgressStage.Text = "[$position/$total] $($ship.LocalizedName) 준비 중"
    $controls.ProgressMessage.Text =
        "$(Get-SourceDisplay $source)에서 이 함선에 필요한 리소스만 읽어요."
    Add-Log "[$position/$total] 추출 시작: $($ship.LocalizedName) / $(Get-SourceDisplay $source)"
    Start-ToolProcess -Operation "extract:${source}:$position/$total" `
        -Arguments $arguments.ToArray() -Completion {
            param($exitCode)
            Complete-BatchItem -ExitCode $exitCode
        }
}

function Switch-Page {
    param([string] $Page)
    $controls.ExtractPage.Visibility = 'Collapsed'
    $controls.ViewerPage.Visibility = 'Collapsed'
    $controls.SettingsPage.Visibility = 'Collapsed'
    switch ($Page) {
        'extract' {
            $controls.ExtractPage.Visibility = 'Visible'
            $controls.TopTitle.Text = '함선 추출'
            $controls.TopSubtitle.Text =
                '여러 함선을 대기열에 담아 선체와 무장을 선택한 형식으로 내보내요.'
        }
        'viewer' {
            $controls.ViewerPage.Visibility = 'Visible'
            $controls.TopTitle.Text = '3D 모델 뷰어'
            $controls.TopSubtitle.Text =
                'OBJ 파트를 직접 선택하고 숨기거나 배치 상태를 확인해요.'
            if (-not $script:ViewerInitializing -and -not $script:ViewerReady) {
                $window.Dispatcher.BeginInvoke(
                    [Windows.Threading.DispatcherPriority]::Loaded,
                    [action] { Initialize-ModelViewer }
                ) | Out-Null
            }
        }
        'settings' {
            Sync-SettingsToUi
            $controls.SettingsPage.Visibility = 'Visible'
            $controls.TopTitle.Text = '설정'
            $controls.TopSubtitle.Text =
                '세 게임 설치 경로, 출력과 코라블리 압축 런타임을 관리해요.'
        }
    }
}

function Get-ModelMaterialPath {
    param([Parameter(Mandatory)] [string] $ObjPath)
    $objDirectory = [IO.Path]::GetDirectoryName($ObjPath)
    try {
        foreach ($line in Get-Content -LiteralPath $ObjPath -TotalCount 120) {
            if ($line -match '^\s*mtllib\s+(.+?)\s*$') {
                $candidate = Join-Path $objDirectory $Matches[1].Trim('"')
                if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                    return (Resolve-Path -LiteralPath $candidate).Path
                }
            }
        }
    }
    catch {}
    $fallback = [IO.Path]::ChangeExtension($ObjPath, '.mtl')
    if (Test-Path -LiteralPath $fallback -PathType Leaf) {
        return (Resolve-Path -LiteralPath $fallback).Path
    }
    return ''
}

function Get-AssemblyValidationPath {
    param([Parameter(Mandatory)] [string] $ObjPath)
    $objDirectory = [IO.Path]::GetDirectoryName($ObjPath)
    $objBase = [IO.Path]::GetFileNameWithoutExtension($ObjPath)
    $baseCandidates = @($objBase)
    if ($objBase -match '^(.*)_Editable$') {
        $baseCandidates = @($Matches[1], $objBase)
    }
    foreach ($baseName in $baseCandidates) {
        $candidate = Join-Path $objDirectory ($baseName + '.validation.json')
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    $validationFiles = @(
        Get-ChildItem -LiteralPath $objDirectory -Filter '*.validation.json' -File `
            -ErrorAction SilentlyContinue
    )
    if ($validationFiles.Count -eq 1) { return $validationFiles[0].FullName }
    return ''
}

function Send-ViewerMessage {
    param([Parameter(Mandatory)] [hashtable] $Message)
    if ($null -eq $controls.ModelWebView.CoreWebView2) { return }
    $json = $Message | ConvertTo-Json -Compress -Depth 6
    $controls.ModelWebView.CoreWebView2.PostWebMessageAsJson($json)
}

function Send-ModelToViewer {
    param([Parameter(Mandatory)] [string] $ObjPath)
    $resolved = (Resolve-Path -LiteralPath $ObjPath -ErrorAction Stop).Path
    $modelDirectory = [IO.Path]::GetDirectoryName($resolved)
    $objName = [IO.Path]::GetFileName($resolved)
    $mtlPath = Get-ModelMaterialPath $resolved
    $armorPath = [IO.Path]::ChangeExtension($resolved, '.armor.json')
    $modelReportPath = [IO.Path]::ChangeExtension($resolved, '.model.json')
    $assemblyReportPath = Get-AssemblyValidationPath $resolved
    $core = $controls.ModelWebView.CoreWebView2
    $mappingChanged = $script:ViewerMappedDirectory -ine $modelDirectory
    if ($mappingChanged) {
        try { $core.ClearVirtualHostNameToFolderMapping('model.local') } catch {}
        $core.SetVirtualHostNameToFolderMapping(
            'model.local',
            $modelDirectory,
            [Microsoft.Web.WebView2.Core.CoreWebView2HostResourceAccessKind]::Allow
        )
        $script:ViewerMappedDirectory = $modelDirectory
    }
    $script:ViewerMappingSerial++
    if ($mappingChanged) {
        $script:ViewerModelPath = $resolved
        $script:PendingViewerModel = $resolved
        $script:ViewerReady = $false
        $controls.ViewerPathLabel.Text = $resolved
        $controls.ViewerStatus.Text = '새 모델 폴더를 뷰어에 연결하는 중이에요...'
        $controls.OpenViewerFolderButton.IsEnabled = $true
        $core.Navigate(
            'https://viewer.local/index.html?app=5.0.31&lang=' +
                [Uri]::EscapeDataString($script:WoWSToolboxLanguage) +
                '&modelMapping=' + $script:ViewerMappingSerial
        )
        return
    }
    $cacheSuffix = '?mapping=' + $script:ViewerMappingSerial
    $message = @{
        type = 'loadModel'
        displayName = [IO.Path]::GetFileNameWithoutExtension($resolved)
        objName = $objName
        objUrl = 'https://model.local/' + [Uri]::EscapeDataString($objName) + $cacheSuffix
        mtlUrl = ''
        resourceBaseUrl = 'https://model.local/'
    }
    if (Test-Path -LiteralPath $armorPath -PathType Leaf) {
        $message.armorUrl = 'https://model.local/' +
            [Uri]::EscapeDataString([IO.Path]::GetFileName($armorPath)) + $cacheSuffix
    }
    if (Test-Path -LiteralPath $modelReportPath -PathType Leaf) {
        $message.modelReportUrl = 'https://model.local/' +
            [Uri]::EscapeDataString([IO.Path]::GetFileName($modelReportPath)) + $cacheSuffix
    }
    if (-not [string]::IsNullOrWhiteSpace($assemblyReportPath)) {
        $message.assemblyReportUrl = 'https://model.local/' +
            [Uri]::EscapeDataString([IO.Path]::GetFileName($assemblyReportPath)) + $cacheSuffix
    }
    if (-not [string]::IsNullOrWhiteSpace($mtlPath)) {
        $message.mtlUrl = 'https://model.local/' +
            [Uri]::EscapeDataString([IO.Path]::GetFileName($mtlPath)) + $cacheSuffix
    }
    Send-ViewerMessage $message
    $script:ViewerModelPath = $resolved
    $script:PendingViewerModel = ''
    $controls.ViewerPathLabel.Text = $resolved
    $controls.ViewerStatus.Text = '모델 데이터를 읽는 중이에요...'
    $controls.OpenViewerFolderButton.IsEnabled = $true
}

function Send-CompareModelToViewer {
    param([Parameter(Mandatory)] [string] $ObjPath)
    if (-not $script:ViewerReady) { throw '먼저 기준 OBJ를 열어 주세요.' }
    $resolved = (Resolve-Path -LiteralPath $ObjPath -ErrorAction Stop).Path
    $modelDirectory = [IO.Path]::GetDirectoryName($resolved)
    $objName = [IO.Path]::GetFileName($resolved)
    $mtlPath = Get-ModelMaterialPath $resolved
    $modelReportPath = [IO.Path]::ChangeExtension($resolved, '.model.json')
    $assemblyReportPath = Get-AssemblyValidationPath $resolved
    $core = $controls.ModelWebView.CoreWebView2
    if ($script:ViewerCompareMappedDirectory -ine $modelDirectory) {
        try { $core.ClearVirtualHostNameToFolderMapping('compare.local') } catch {}
        $core.SetVirtualHostNameToFolderMapping(
            'compare.local',
            $modelDirectory,
            [Microsoft.Web.WebView2.Core.CoreWebView2HostResourceAccessKind]::Allow
        )
        $script:ViewerCompareMappedDirectory = $modelDirectory
    }
    $script:ViewerCompareMappingSerial++
    $cacheSuffix = '?mapping=' + $script:ViewerCompareMappingSerial
    $message = @{
        type = 'loadCompareModel'
        displayName = [IO.Path]::GetFileNameWithoutExtension($resolved)
        objUrl = 'https://compare.local/' + [Uri]::EscapeDataString($objName) + $cacheSuffix
        mtlUrl = ''
        resourceBaseUrl = 'https://compare.local/'
    }
    if (-not [string]::IsNullOrWhiteSpace($mtlPath)) {
        $message.mtlUrl = 'https://compare.local/' +
            [Uri]::EscapeDataString([IO.Path]::GetFileName($mtlPath)) + $cacheSuffix
    }
    if (Test-Path -LiteralPath $modelReportPath -PathType Leaf) {
        $message.modelReportUrl = 'https://compare.local/' +
            [Uri]::EscapeDataString([IO.Path]::GetFileName($modelReportPath)) + $cacheSuffix
    }
    if (-not [string]::IsNullOrWhiteSpace($assemblyReportPath)) {
        $message.assemblyReportUrl = 'https://compare.local/' +
            [Uri]::EscapeDataString([IO.Path]::GetFileName($assemblyReportPath)) + $cacheSuffix
    }
    Send-ViewerMessage $message
    $script:ViewerComparePath = $resolved
    $controls.ViewerStatus.Text = '비교 모델을 읽는 중이에요...'
}
function Open-ModelInViewer {
    param([Parameter(Mandatory)] [string] $ObjPath)
    if (-not (Test-Path -LiteralPath $ObjPath -PathType Leaf)) {
        throw "OBJ 파일을 찾지 못했어요: $ObjPath"
    }
    if ([IO.Path]::GetExtension($ObjPath) -ine '.obj') {
        throw 'OBJ 파일만 열 수 있어요.'
    }
    $resolved = (Resolve-Path -LiteralPath $ObjPath).Path
    $modelBytes = (Get-Item -LiteralPath $resolved).Length
    if (-not $automatedMode -and $modelBytes -gt 750MB) {
        $sizeText = '{0:N1} GB' -f ($modelBytes / 1GB)
        $answer = [Windows.MessageBox]::Show(
            $window,
            (Get-UiText (
                "OBJ가 ${sizeText}라서 뷰어가 잠시 멈춘 것처럼 보일 수 있어요.`n그래도 열까요?"
            ) (
                "This OBJ is $sizeText and the viewer may appear unresponsive while loading.`nOpen it anyway?"
            )),
            (Get-UiText '큰 모델 열기' 'Open large model'),
            [Windows.MessageBoxButton]::YesNo,
            [Windows.MessageBoxImage]::Warning
        )
        if ($answer -ne [Windows.MessageBoxResult]::Yes) { return }
    }
    $script:PendingViewerModel = $resolved
    $controls.ViewerPathLabel.Text = $resolved
    $controls.ViewerStatus.Text = '3D 뷰어를 준비하는 중이에요...'
    Set-TopStatus '모델 로딩 중' '#60A5FA'
    $controls.OpenViewerFolderButton.IsEnabled = $true
    $controls.NavViewer.IsChecked = $true
    if ($script:ViewerReady) {
        Send-ModelToViewer $resolved
    }
    elseif (-not $script:ViewerInitializing) {
        $window.Dispatcher.BeginInvoke(
            [Windows.Threading.DispatcherPriority]::Loaded,
            [action] { Initialize-ModelViewer }
        ) | Out-Null
    }
}

function Find-RecentModel {
    if ($null -ne $script:LastResult -and
        $null -ne $script:LastResult.PSObject.Properties['obj']) {
        $candidate = [string] $script:LastResult.obj
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    $outputRoot = [string] $script:Settings.OutputPath
    if (-not (Test-Path -LiteralPath $outputRoot -PathType Container)) { return '' }
    $latest = Get-ChildItem -LiteralPath $outputRoot -Recurse -Filter '*.obj' -File `
        -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if ($null -eq $latest) { return '' }
    return $latest.FullName
}

function Handle-ViewerWebMessage {
    param($MessageArgs)
    try {
        $message = $MessageArgs.WebMessageAsJson | ConvertFrom-Json
        switch ([string] $message.type) {
            'html-ready' {
                $controls.ViewerStatus.Text = '3D 모듈을 불러오는 중이에요...'
            }
            'ready' {
                $script:ViewerReady = $true
                $script:ViewerInitializing = $false
                $controls.ViewerStatus.Text = '뷰어 준비 완료 · OBJ 파일을 열어 주세요.'
                if (-not [string]::IsNullOrWhiteSpace($script:PendingViewerModel)) {
                    Send-ModelToViewer $script:PendingViewerModel
                }
            }
            'loaded' {
                $armorState = if ([bool] $message.armor) { ' · 장갑 보기 지원' } else { ' · 장갑 데이터 없음' }
                $controls.ViewerStatus.Text =
                    "파트 $($message.parts)개 · 삼각형 $([int64] $message.triangles)개${armorState}"
                $script:ViewerTestResult = [pscustomobject] @{
                    ok = $true
                    name = [string] $message.name
                    parts = [int] $message.parts
                    triangles = [int64] $message.triangles
                    armor = [bool] $message.armor
                    armor_triangles = [int64] $message.armorTriangles
                    armor_groups = [int] $message.armorGroups
                    armor_zones = [int] $message.armorZones
                    axis_mode = [string] $message.axisMode
                }
                Set-TopStatus '뷰어 준비됨' '#34D399'
            }
            'warning' {
                $warning = [string] $message.message
                if ($warning.Length -gt 500) { $warning = $warning.Substring(0, 500) + '…' }
                $controls.ViewerStatus.Text = "뷰어 경고: $warning"
                Add-Log "뷰어 경고: $warning"
            }
            'compareLoaded' {
                $controls.ViewerStatus.Text = "비교 모델 준비됨: $($message.name)"
            }
            'measurement' {
                $controls.ViewerStatus.Text = "측정 거리: $([math]::Round([double] $message.distance, 3)) 모델 단위"
            }
            'selection' {
                $controls.ViewerStatus.Text = "선택: $($message.name)"
            }
            'error' {
                $viewerError = [string] $message.message
                if ($viewerError.Length -gt 500) {
                    $viewerError = $viewerError.Substring(0, 500) + '…'
                }
                $controls.ViewerStatus.Text = "불러오기 실패: $viewerError"
                Add-Log "뷰어 오류: $viewerError" -ErrorLine
                $script:ViewerTestResult = [pscustomobject] @{
                    ok = $false
                    error = $viewerError
                }
                Set-TopStatus '뷰어 오류' '#F87171'
            }
        }
    }
    catch {
        $controls.ViewerStatus.Text = "뷰어 메시지 처리 오류: $($_.Exception.Message)"
    }
}

function Complete-ModelViewerInitialization {
    if ($script:ViewerCoreConfigured) { return }
    $core = $controls.ModelWebView.CoreWebView2
    if ($null -eq $core) { throw 'WebView2 컨트롤이 만들어졌지만 CoreWebView2를 찾지 못했어요.' }
    $script:ViewerCoreConfigured = $true
    $core.Settings.AreDevToolsEnabled = $false
    $core.Settings.AreDefaultContextMenusEnabled = $false
    $core.Settings.IsStatusBarEnabled = $false
    $core.Settings.IsZoomControlEnabled = $false
    try { $core.Settings.AreHostObjectsAllowed = $false } catch { }
    $core.add_NavigationStarting({
        param($sender, $eventArgs)
        try {
            $target = [Uri] $eventArgs.Uri
            if ($target.Scheme -ne 'https' -or $target.Host -ne 'viewer.local') {
                $eventArgs.Cancel = $true
                Add-Log "뷰어 외부 이동 차단: $($eventArgs.Uri)"
            }
        }
        catch { $eventArgs.Cancel = $true }
    })
    $core.add_NewWindowRequested({
        param($sender, $eventArgs)
        $eventArgs.Handled = $true
        Add-Log "뷰어 새 창 열기 차단: $($eventArgs.Uri)"
    })
    $core.add_DownloadStarting({
        param($sender, $eventArgs)
        $eventArgs.Cancel = $true
        Add-Log '뷰어 파일 다운로드를 차단했어요.'
    })
    $core.SetVirtualHostNameToFolderMapping(
        'viewer.local',
        $script:ViewerWebRoot,
        [Microsoft.Web.WebView2.Core.CoreWebView2HostResourceAccessKind]::Allow
    )
    if (-not [string]::IsNullOrWhiteSpace($script:PendingViewerModel) -and
        (Test-Path -LiteralPath $script:PendingViewerModel -PathType Leaf)) {
        $initialModelDirectory = [IO.Path]::GetDirectoryName($script:PendingViewerModel)
        $core.SetVirtualHostNameToFolderMapping(
            'model.local',
            $initialModelDirectory,
            [Microsoft.Web.WebView2.Core.CoreWebView2HostResourceAccessKind]::Allow
        )
        $script:ViewerMappedDirectory = $initialModelDirectory
    }
    $core.Navigate("https://viewer.local/index.html?app=5.0.31&lang=$script:WoWSToolboxLanguage")
}
function Initialize-ModelViewer {
    if ($script:ViewerReady -or $script:ViewerInitializing) { return }
    $script:ViewerInitializing = $true
    $controls.ViewerStatus.Text = '오프라인 3D 엔진을 시작하는 중이에요...'
    try {
        [IO.Directory]::CreateDirectory($script:ViewerUserDataRoot) | Out-Null
        $environmentTask = [Microsoft.Web.WebView2.Core.CoreWebView2Environment]::CreateAsync(
            $null,
            $script:ViewerUserDataRoot,
            $null
        )
        $viewerEnvironment = $environmentTask.GetAwaiter().GetResult()
        $script:ViewerInitTask =
            $controls.ModelWebView.EnsureCoreWebView2Async($viewerEnvironment)
        $controls.ViewerStatus.Text = '뷰어 컨트롤을 연결하는 중이에요...'
        $controllerDeadline = (Get-Date).AddSeconds(15)
        while (-not $script:ViewerInitTask.IsCompleted -and
            (Get-Date) -lt $controllerDeadline) {
            [Windows.Forms.Application]::DoEvents()
            [Threading.Thread]::Sleep(10)
        }
        if (-not $script:ViewerInitTask.IsCompleted) {
            throw 'WebView2 컨트롤 연결 시간이 초과됐어요.'
        }
        if ($script:ViewerInitTask.IsFaulted) {
            throw $script:ViewerInitTask.Exception.GetBaseException()
        }
        Complete-ModelViewerInitialization
    }
    catch {
        $script:ViewerInitializing = $false
        $controls.ViewerStatus.Text = "3D 뷰어 시작 실패: $($_.Exception.Message)"
        $script:ViewerTestResult = [pscustomobject] @{
            ok = $false
            error = $_.Exception.Message
        }
        Set-TopStatus '뷰어 오류' '#F87171'
    }
}
function Recover-GuiError {
    param(
        [string] $Context,
        [Exception] $Exception
    )
    $message = if ($null -ne $Exception -and
        -not [string]::IsNullOrWhiteSpace($Exception.Message)) {
        $Exception.Message
    }
    else { '알 수 없는 GUI 오류' }
    try { Add-Log "$Context — $message" -ErrorLine } catch {}
    if ($null -ne $script:ActiveRunner) {
        try { $script:ActiveRunner.CancelTree() } catch {}
        try { $script:ActiveRunner.Dispose() } catch {}
    }
    $script:ActiveRunner = $null
    $script:ActiveOperation = ''
    $script:ActiveCompletion = $null
    $script:PendingPicker = $false
    $script:CatalogRefreshSource = ''
    $script:CatalogRefreshOutput = ''
    $script:BatchActive = $false
    $script:BatchCurrentItem = $null
    try { Set-BusyState } catch {}
    try {
        $controls.MainProgress.IsIndeterminate = $false
        $controls.ProgressStage.Text = '작업 오류'
        $controls.ProgressMessage.Text = "${Context}: $message"
        Set-TopStatus '오류' '#F87171'
    }
    catch {}
}

function Get-ComboTag {
    param([Parameter(Mandatory)] $Combo)
    if ($null -eq $Combo.SelectedItem) { return '' }
    $tag = $Combo.SelectedItem.Tag
    if ($null -eq $tag) { return [string] $Combo.SelectedItem.Content }
    return [string] $tag
}

function Select-ComboTag {
    param(
        [Parameter(Mandatory)] $Combo,
        [Parameter(Mandatory)] [string] $Tag
    )
    foreach ($item in $Combo.Items) {
        if ([string] $item.Tag -eq $Tag) {
            $Combo.SelectedItem = $item
            return
        }
    }
}

function Update-QualityControls {
    $legendsFixedProfile = (Get-SourceKey) -eq 'legends'
    $controls.TextureCombo.IsEnabled = -not $legendsFixedProfile
    $controls.LodCombo.IsEnabled = -not $legendsFixedProfile
    if ($legendsFixedProfile) {
        Select-ComboTag $controls.TextureCombo '0'
        Select-ComboTag $controls.LodCombo '0'
        $controls.TextureCombo.ToolTip = Get-UiText (
            'Legends는 선언된 원본 크기 컬러 텍스처를 사용해요.'
        ) (
            'Legends uses the declared original-size color textures.'
        )
        $controls.LodCombo.ToolTip = Get-UiText (
            'Legends는 ModelUber 최고 품질 LOD0으로 고정해요.'
        ) (
            'Legends is fixed to the highest-quality ModelUber LOD0.'
        )
    }
    else {
        $controls.TextureCombo.ToolTip =
            Get-UiText '컬러 텍스처 최대 크기' 'Maximum color-texture size'
        $controls.LodCombo.ToolTip =
            Get-UiText '모델 정밀도' 'Model detail'
    }
}

function Sync-SettingsToUi {
    $controls.LegendsPathBox.Text = [string] $script:Settings.LegendsPath
    $controls.PcPathBox.Text = [string] $script:Settings.PcPath
    $controls.KorabliPathBox.Text = [string] $script:Settings.KorabliPath
    $controls.SettingsOutputBox.Text = [string] $script:Settings.OutputPath
    $controls.OodlePathBox.Text = [string] $script:Settings.OodlePath
    Select-ComboTag $controls.LanguageCombo ([string] $script:Settings.Language)
    $controls.AutoUpdateCheck.IsChecked =
        [string] $script:Settings.AutoCheckUpdates -eq 'true'
    Select-ComboTag $controls.FormatCombo ([string] $script:Settings.Formats)
    Select-ComboTag $controls.TextureCombo ([string] $script:Settings.TextureMaxSize)
    Select-ComboTag $controls.LodCombo ([string] $script:Settings.Lod)
    Update-OutputLabel
    Update-CurrentGamePathUi
    Update-QualityControls
}

function Sync-UiToSettings {
    $script:Settings.LegendsPath = $controls.LegendsPathBox.Text.Trim()
    $script:Settings.PcPath = $controls.PcPathBox.Text.Trim()
    $script:Settings.KorabliPath = $controls.KorabliPathBox.Text.Trim()
    $script:Settings.OutputPath = $controls.SettingsOutputBox.Text.Trim()
    $script:Settings.OodlePath = $controls.OodlePathBox.Text.Trim()
    $script:Settings.Language = Get-ComboTag $controls.LanguageCombo
    $script:Settings.AutoCheckUpdates = if ($controls.AutoUpdateCheck.IsChecked -eq $true) {
        'true'
    }
    else { 'false' }
    $script:Settings.Formats = Get-ComboTag $controls.FormatCombo
    $script:Settings.TextureMaxSize = Get-ComboTag $controls.TextureCombo
    $script:Settings.Lod = Get-ComboTag $controls.LodCombo
}

function Update-QueueUi {
    $controls.QueueList.ItemsSource = $null
    $controls.QueueList.ItemsSource = $script:ExtractionQueue
    $count = $script:ExtractionQueue.Count
    $busy = $null -ne $script:ActiveRunner -or $script:BatchActive
    $selectedIndex = $controls.QueueList.SelectedIndex
    if ($count -eq 0) {
        $controls.SelectedShipName.Text = Get-UiText '대기열이 비어 있어요' 'The queue is empty'
        $controls.SelectedShipMeta.Text = Get-UiText `
            '함선 추가·편집에서 한 척 이상 체크해 주세요.' `
            'Select at least one ship in Add/Edit ships.'
    }
    else {
        $englishCount = if ($count -eq 1) { '1 ship queued' } else { "$count ships queued" }
        $controls.SelectedShipName.Text = Get-UiText "${count}척 대기 중" $englishCount
        $sourceSummary = @($script:ExtractionQueue | Group-Object Source | ForEach-Object {
            $countText = if ($script:WoWSToolboxLanguage -eq 'en') {
                if ($_.Count -eq 1) { '1 ship' } else { "$($_.Count) ships" }
            }
            else { "$($_.Count)척" }
            "$(Get-SourceDisplay $_.Name) $countText"
        }) -join ' · '
        $queueMeta = Get-UiText `
            '한 프로세스에서 선계산하며 추출해요.' `
            'Precomputed and extracted in one process.'
        $controls.SelectedShipMeta.Text = "$sourceSummary · $queueMeta"
    }
    $controls.ExtractButton.IsEnabled = $count -gt 0 -and -not $busy
    $controls.ClearQueueButton.IsEnabled = $count -gt 0 -and -not $busy
    $controls.RemoveQueueButton.IsEnabled = $selectedIndex -ge 0 -and -not $busy
    $controls.QueueUpButton.IsEnabled = $selectedIndex -gt 0 -and -not $busy
    $controls.QueueDownButton.IsEnabled = $selectedIndex -ge 0 -and
        $selectedIndex -lt ($count - 1) -and -not $busy
}

function Set-BusyState {
    $busy = $null -ne $script:ActiveRunner -or $script:BatchActive
    foreach ($name in @(
        'SourceCombo', 'BrowseCurrentGameButton', 'RefreshCatalogButton', 'OpenPickerButton',
        'RemoveQueueButton', 'ClearQueueButton', 'QueueUpButton', 'QueueDownButton',
        'SaveQueueButton', 'LoadQueueButton', 'BrowseOutputButton', 'InspectButton',
        'FormatCombo', 'TextureCombo', 'LodCombo', 'OverwriteCheck',
        'NavViewer', 'NavSettings'
    )) {
        $controls[$name].IsEnabled = -not $busy
    }
    $controls.CancelButton.IsEnabled = $busy
    $controls.PauseButton.IsEnabled = $script:BatchActive
    if ($busy) {
        $controls.ExtractButton.IsEnabled = $false
        Set-TopStatus '작업 중' '#60A5FA'
    }
    else {
        $controls.PauseButton.Content = '일시 정지'
        Set-TopStatus '준비됨' '#34D399'
        Update-QueueUi
    }
}

function Write-BatchControl {
    param(
        [bool] $Paused = $false,
        [bool] $Cancel = $false
    )
    Write-JsonAtomic -Path $script:BatchControlPath -Value ([ordered] @{
        paused = $Paused
        cancel = $Cancel
    }) -Depth 3 -Compress
}

function Get-SafeRunSlug {
    param([Parameter(Mandatory)] $Ship)
    $variant = Get-OptionalShipValue -Ship $Ship -Name 'ShipResource'
    if ([string]::IsNullOrWhiteSpace($variant)) {
        $variant = [IO.Path]::GetFileNameWithoutExtension(
            (Get-OptionalShipValue -Ship $Ship -Name 'ModelPath')
        )
    }
    $raw = "$($Ship.GameParamsKey)_$variant"
    return [regex]::Replace($raw, '[^0-9A-Za-z_.-]+', '_').Trim('_.')
}

function New-BatchManifest {
    Sync-UiToSettings
    Save-Settings
    $items = @($script:ExtractionQueue | ForEach-Object {
        $entry = $_
        $ship = $entry.Ship
        [ordered] @{
            source = [string] $entry.Source
            game_dir = Get-QueueEntryGamePath $entry
            display_name = [string] $ship.LocalizedName
            ship_key = if ($entry.Source -eq 'legends') { [string] $ship.GameParamsKey } else { '' }
            selected_model_path = Get-OptionalShipValue -Ship $ship -Name 'ModelPath'
            ship_resource = Get-OptionalShipValue -Ship $ship -Name 'ShipResource'
            run_slug = if ($entry.Source -eq 'legends') { Get-SafeRunSlug $ship } else { '' }
            ship_index = if ($entry.Source -ne 'legends') { [string] $ship.GameParamsIndex } else { '' }
            ship_code = [string] $ship.ShipCode
            nation = [string] $ship.Nation
            ship_class = [string] $ship.ShipClass
            tier = [int] $ship.Tier
        }
    })
    $manifest = [ordered] @{
        schema = 'wows-toolbox-batch-request/v1'
        created = (Get-Date).ToString('o')
        common = [ordered] @{
            toolbox_root = $script:PackageRoot
            output_root = [string] $script:Settings.OutputPath
            language = [string] $script:Settings.Language
            oodle_dll = [string] $script:Settings.OodlePath
            cache_root = (Join-Path $script:StateRoot 'Cache')
            state_root = $script:StateRoot
            control_file = $script:BatchControlPath
            overwrite = [bool] $controls.OverwriteCheck.IsChecked
            keep_glb = $false
            formats = Get-ComboTag $controls.FormatCombo
            texture_max_size = [int] (Get-ComboTag $controls.TextureCombo)
            lod = [int] (Get-ComboTag $controls.LodCombo)
        }
        items = $items
    }
    Write-JsonAtomic -Path $script:BatchManifestPath -Value $manifest -Depth 12
    return $manifest
}

function Complete-PersistentBatch {
    param([int] $ExitCode)
    if ($script:CancelRequested) {
        Finish-BatchExtraction -Cancelled
        return
    }
    if ($null -eq $script:BatchSummary) {
        foreach ($entry in @($script:BatchItems)) {
            if (-not $script:BatchSucceeded.Contains($entry)) {
                $script:BatchFailed.Add([pscustomobject] @{
                    Entry = $entry
                    ExitCode = $ExitCode
                })
            }
        }
    }
    Finish-BatchExtraction
    if ($script:BatchSucceeded.Count -gt 0 -and
        (Test-Path -LiteralPath $script:Settings.OutputPath -PathType Container)) {
        $open = [Windows.MessageBox]::Show(
            $window,
            (Get-UiText '완료된 출력 폴더를 지금 열까요?' 'Open the completed output folder now?'),
            (Get-UiText '결과 열기' 'Open results'),
            [Windows.MessageBoxButton]::YesNo,
            [Windows.MessageBoxImage]::Question
        )
        if ($open -eq [Windows.MessageBoxResult]::Yes) {
            Start-Process explorer.exe -ArgumentList @([string] $script:Settings.OutputPath)
        }
    }
}

function Start-PersistentBatchExtraction {
    [void] (New-BatchManifest)
    Write-BatchControl
    $script:BatchItems = @($script:ExtractionQueue)
    $script:BatchIndex = 0
    $script:BatchSucceeded = [Collections.Generic.List[object]]::new()
    $script:BatchFailed = [Collections.Generic.List[object]]::new()
    $script:BatchActive = $true
    $script:BatchCurrentItem = $script:BatchItems[0]
    $script:BatchSummary = $null
    $script:BatchPaused = $false
    $script:CancelRequested = $false
    $script:LastResult = $null
    $script:LastOutputDir = ''
    $controls.MainProgress.Value = 0
    $controls.ProgressStage.Text = '대기열 엔진 시작 중'
    $controls.ProgressMessage.Text = '게임 빌드·디스크·메모리를 먼저 확인해요.'
    Set-BusyState
    Start-ToolProcess -Operation 'batch-extract-v5' -Arguments @(
        '-B', $script:BatchExtractScript,
        '--manifest', $script:BatchManifestPath
    ) -Completion {
        param($exitCode)
        Complete-PersistentBatch -ExitCode $exitCode
    }
}

function Start-ShipExtraction {
    if (-not (Test-ExtractionReady)) { return }
    $count = $script:ExtractionQueue.Count
    $preview = @($script:ExtractionQueue | Select-Object -First 8 | ForEach-Object {
        "• $($_.Ship.LocalizedName) — $(Get-SourceDisplay $_.Source)"
    }) -join "`n"
    if ($count -gt 8) { $preview += "`n• 외 $($count - 8)척" }
    $profiles = "$(Get-ComboTag $controls.FormatCombo) · 텍스처 $(Get-ComboTag $controls.TextureCombo) · LOD $(Get-ComboTag $controls.LodCombo)"
    $answer = [Windows.MessageBox]::Show(
        $window,
        (Get-UiText "${count}척을 단일 대기열 엔진으로 추출할까요?`n`n$preview`n`n$profiles`n세 게임 모두 Blender 없이 OBJ로 조립해요." "Extract $count ships with the queue engine?`n`n$preview`n`n$profiles`nAll three game sources are assembled without Blender."),
        (Get-UiText '대기열 모델 추출' 'Extract queued models'),
        [Windows.MessageBoxButton]::YesNo,
        [Windows.MessageBoxImage]::Question
    )
    if ($answer -ne [Windows.MessageBoxResult]::Yes) { return }
    [IO.Directory]::CreateDirectory($script:Settings.OutputPath) | Out-Null
    Start-PersistentBatchExtraction
}

function Handle-ProcessLine {
    param([WoWSToolboxV5.ProcessLine] $Line)
    $text = [regex]::Replace(
        [string] $Line.Text,
        ([char] 27 + '\[[0-9;?]*[ -/]*[@-~]'),
        ''
    )
    Add-Log $text -ErrorLine:$Line.IsError
    if ($text.StartsWith('[PROGRESS] ')) {
        try {
            $progress = $text.Substring(11) | ConvertFrom-Json
            $stage = [string] $progress.stage
            $percent = [double] $progress.percent
            $total = [math]::Max(1, @($script:BatchItems).Count)
            $position = [math]::Min($total, $script:BatchIndex + 1)
            $shipName = if ($null -ne $script:BatchCurrentItem) {
                [string] $script:BatchCurrentItem.Ship.LocalizedName
            } else { '함선' }
            $controls.ProgressStage.Text = "[$position/$total] $shipName — $stage"
            $controls.MainProgress.Value = [math]::Round(
                100 * ($script:BatchIndex + ($percent / 100)) / $total,
                1
            )
            $controls.ProgressMessage.Text = Convert-ToUiText ([string] $progress.message)
            $controls.MainProgress.IsIndeterminate = $false
        }
        catch {}
    }
    elseif ($text.StartsWith('[RESULT] ')) {
        try { $script:LastResult = $text.Substring(9) | ConvertFrom-Json } catch {}
    }
    elseif ($text.StartsWith('[COMPAT] ')) {
        try {
            $compat = $text.Substring(9) | ConvertFrom-Json
            $buildText = if ($null -ne $compat.build) { " 빌드 $($compat.build)" } else { '' }
            $controls.ProgressMessage.Text = "$($compat.source)$buildText — $($compat.message)"
        }
        catch {}
    }
    elseif ($text.StartsWith('[BATCH] ')) {
        try {
            $event = $text.Substring(8) | ConvertFrom-Json
            switch ([string] $event.event) {
                'preflight' {
                    $sizeGb = [math]::Round([double] $event.estimated_bytes / 1GB, 1)
                    $minutes = [math]::Ceiling([double] $event.estimated_seconds / 60)
                    $controls.ProgressStage.Text = '추출 준비 검사 완료'
                    $controls.ProgressMessage.Text = "예상 ${minutes}분 · 약 ${sizeGb}GB · 디스크/메모리 확인 완료"
                }
                'item_start' {
                    $script:BatchIndex = [int] $event.index - 1
                    if ($script:BatchIndex -ge 0 -and $script:BatchIndex -lt @($script:BatchItems).Count) {
                        $script:BatchCurrentItem = $script:BatchItems[$script:BatchIndex]
                    }
                    $controls.ProgressStage.Text = "[$($event.index)/$($event.count)] $($event.name)"
                }
                'prefetch' {
                    $controls.ProgressMessage.Text = "다음 함선 선계산 중: $($event.name)"
                }
                'item_complete' {
                    $index = [int] $event.index - 1
                    $entry = $script:BatchItems[$index]
                    if (-not $script:BatchSucceeded.Contains($entry)) {
                        $script:BatchSucceeded.Add($entry)
                        Add-RecentShip $entry
                    }
                    $script:LastResult = $event.result
                    $script:LastOutputDir = [string] $event.result.output_dir
                    $minutes = [math]::Floor([double] $event.eta_seconds / 60)
                    $seconds = [int] $event.eta_seconds % 60
                    $controls.ProgressMessage.Text = "완료 · 남은 시간 약 ${minutes}분 ${seconds}초"
                }
                'item_failed' {
                    $index = [int] $event.index - 1
                    $entry = $script:BatchItems[$index]
                    $script:BatchFailed.Add([pscustomobject] @{
                        Entry = $entry
                        ExitCode = 1
                        Message = [string] $event.message
                    })
                    $controls.ProgressMessage.Text = "$($event.name) 실패 — $($event.message)"
                }
                'paused' {
                    $controls.ProgressStage.Text = '대기열 일시 정지'
                }
                'resumed' {
                    $controls.ProgressStage.Text = '대기열 재개'
                }
                'summary' {
                    $script:BatchSummary = $event
                    $controls.MainProgress.Value = 100
                }
                'fatal' {
                    $controls.ProgressStage.Text = '대기열 시작 실패'
                    $controls.ProgressMessage.Text = Convert-ToUiText ([string] $event.message)
                }
            }
        }
        catch {}
    }
}

function Add-RecentShip {
    param([Parameter(Mandatory)] $Entry)
    $keys = [Collections.Generic.List[string]]::new()
    [void] $keys.Add([string] $Entry.Key)
    if (Test-Path -LiteralPath $script:RecentShipsPath -PathType Leaf) {
        try {
            foreach ($key in @(Get-Content -Raw -LiteralPath $script:RecentShipsPath | ConvertFrom-Json)) {
                if (-not $keys.Contains([string] $key)) { [void] $keys.Add([string] $key) }
            }
        }
        catch {}
    }
    while ($keys.Count -gt 30) { $keys.RemoveAt($keys.Count - 1) }
    Write-JsonAtomic -Path $script:RecentShipsPath -Value $keys -Depth 4
}

function Move-QueueItem {
    param([int] $Direction)
    $index = $controls.QueueList.SelectedIndex
    $target = $index + $Direction
    if ($index -lt 0 -or $target -lt 0 -or $target -ge $script:ExtractionQueue.Count) { return }
    $item = $script:ExtractionQueue[$index]
    $script:ExtractionQueue.RemoveAt($index)
    $script:ExtractionQueue.Insert($target, $item)
    Update-QueueUi
    $controls.QueueList.SelectedIndex = $target
    $controls.QueueList.ScrollIntoView($item)
}

function Save-QueueFile {
    $dialog = [Microsoft.Win32.SaveFileDialog]::new()
    $dialog.Filter = 'WoWS Toolbox 대기열|*.wowsqueue.json|JSON|*.json'
    $dialog.FileName = 'ship-queue.wowsqueue.json'
    if (-not $dialog.ShowDialog($window)) { return }
    $payload = @($script:ExtractionQueue | ForEach-Object {
        [ordered] @{
            source = $_.Source
            game_path = Get-QueueEntryGamePath $_
            ship = $_.Ship
        }
    })
    Write-JsonAtomic -Path $dialog.FileName -Value $payload -Depth 20
    Add-Log "대기열 저장: $($dialog.FileName)"
}

function ConvertTo-ValidatedQueueEntries {
    param([object[]] $Rows)
    if ($Rows.Count -gt 2000) { throw '대기열은 최대 2,000척까지 불러올 수 있어요.' }
    $loaded = [Collections.Generic.List[object]]::new()
    $keys = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $supportedSources = @('legends', 'pc', 'korabli')
    for ($index = 0; $index -lt $Rows.Count; $index++) {
        $row = $Rows[$index]
        if ($null -eq $row -or $null -eq $row.PSObject.Properties['source'] -or
            $null -eq $row.PSObject.Properties['ship']) {
            throw "대기열 $($index + 1)번째 항목에 source 또는 ship이 없어요."
        }
        $source = ([string] $row.source).Trim().ToLowerInvariant()
        if ($supportedSources -notcontains $source) {
            throw "대기열 $($index + 1)번째 항목의 게임 소스가 잘못됐어요: $source"
        }
        $ship = $row.ship
        $name = [string] $ship.LocalizedName
        if ([string]::IsNullOrWhiteSpace($name) -or $name.Length -gt 200) {
            throw "대기열 $($index + 1)번째 함선 이름이 비어 있거나 너무 길어요."
        }
        $identity = if ($source -eq 'legends') {
            [string] $ship.GameParamsKey
        }
        else { [string] $ship.GameParamsIndex }
        if ([string]::IsNullOrWhiteSpace($identity) -or $identity.Length -gt 300) {
            throw "대기열 $($index + 1)번째 함선 식별자가 없거나 잘못됐어요."
        }
        $gamePath = Get-GamePath $source
        if ($null -ne $row.PSObject.Properties['game_path'] -and
            -not [string]::IsNullOrWhiteSpace([string] $row.game_path)) {
            $gamePath = [string] $row.game_path
        }
        if ($gamePath.Length -gt 1000) {
            throw "대기열 $($index + 1)번째 게임 경로가 너무 길어요."
        }
        $entry = New-ShipQueueEntry -Source $source -Ship $ship -GamePath $gamePath
        if ([string]::IsNullOrWhiteSpace([string] $entry.Key)) {
            throw "대기열 $($index + 1)번째 함선 키를 만들지 못했어요."
        }
        if ($keys.Add([string] $entry.Key)) { $loaded.Add($entry) }
    }
    return $loaded
}

function Load-QueueFile {
    $dialog = [Microsoft.Win32.OpenFileDialog]::new()
    $dialog.Filter = 'WoWS Toolbox 대기열|*.wowsqueue.json|JSON|*.json'
    if (-not $dialog.ShowDialog($window)) { return }
    try {
        $file = Get-Item -LiteralPath $dialog.FileName -ErrorAction Stop
        if ($file.Length -gt 16MB) { throw '대기열 파일이 16MB 안전 한도를 넘었어요.' }
        $raw = Get-Content -Raw -LiteralPath $file.FullName -ErrorAction Stop
        if ([string]::IsNullOrWhiteSpace($raw) -or -not $raw.TrimStart().StartsWith('[')) {
            throw '대기열 JSON의 최상위 값은 배열이어야 해요.'
        }
        $rows = @($raw | ConvertFrom-Json -ErrorAction Stop)
        $loaded = ConvertTo-ValidatedQueueEntries -Rows $rows
        $script:ExtractionQueue.Clear()
        foreach ($entry in $loaded) { $script:ExtractionQueue.Add($entry) }
        Update-QueueUi
        Add-Log "대기열 불러오기: $($loaded.Count)척"
    }
    catch {
        $message = $_.Exception.Message
        Add-Log "대기열 불러오기 실패: $message" -ErrorLine
        [Windows.MessageBox]::Show(
            $window,
            (Get-UiText "대기열 파일을 불러오지 못했어요. 기존 대기열은 그대로 유지했어요.$([Environment]::NewLine)$([Environment]::NewLine)$message" "Could not load the queue file. The existing queue was kept.$([Environment]::NewLine)$([Environment]::NewLine)$message"),
            (Get-UiText '대기열 불러오기 실패' 'Queue load failed'),
            [Windows.MessageBoxButton]::OK,
            [Windows.MessageBoxImage]::Warning
        ) | Out-Null
    }
}

function Get-CacheRoot { return (Join-Path $script:StateRoot 'Cache') }

function Get-FolderBytes {
    param([string] $Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) { return 0L }
    return [long] (@(Get-ChildItem -LiteralPath $Path -File -Recurse -ErrorAction SilentlyContinue |
        Measure-Object -Property Length -Sum).Sum)
}

function Refresh-CacheStatus {
    $root = Get-CacheRoot
    $bytes = Get-FolderBytes $root
    $files = if (Test-Path -LiteralPath $root -PathType Container) {
        @(Get-ChildItem -LiteralPath $root -File -Recurse -ErrorAction SilentlyContinue).Count
    } else { 0 }
    $sizeGb = [math]::Round($bytes / 1GB, 2)
    $capacityNote = if ($bytes -gt 20GB) {
        Get-UiText (
            ' · 용량이 커졌어요. 필요하면 캐시 비우기를 사용해 주세요.'
        ) (
            ' · The cache is large. Use Clear cache if you need the space.'
        )
    }
    else { '' }
    $controls.CacheInfoText.Text = Get-UiText (
        "캐시 ${files}개 · $sizeGb GB · 게임 빌드가 바뀌면 새 캐시를 자동 생성해요.$capacityNote"
    ) (
        "Cache: ${files} files · $sizeGb GB · a new cache is created after game updates.$capacityNote"
    )
}

function Find-FirstExisting {
    param([string[]] $Candidates, [switch] $File)
    foreach ($candidate in $Candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) { continue }
        if ($File) {
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
        }
        elseif (Test-Path -LiteralPath $candidate -PathType Container) { return $candidate }
    }
    return ''
}

function Auto-DetectPaths {
    $controls.LegendsPathBox.Text = Find-FirstExisting @(
        $controls.LegendsPathBox.Text,
        'D:\SteamLibrary\steamapps\common\World of Warships Legends',
        'C:\Program Files (x86)\Steam\steamapps\common\World of Warships Legends',
        'C:\Program Files\Steam\steamapps\common\World of Warships Legends'
    )
    $controls.PcPathBox.Text = Find-FirstExisting @(
        $controls.PcPathBox.Text, 'D:\Games\World_of_Warships', 'C:\Games\World_of_Warships'
    )
    $controls.KorabliPathBox.Text = Find-FirstExisting @(
        $controls.KorabliPathBox.Text, 'D:\Games\Korabli', 'C:\Games\Korabli'
    )
    $oodle = Find-OodleRuntime
    if (-not [string]::IsNullOrWhiteSpace($oodle)) { $controls.OodlePathBox.Text = $oodle }
    $controls.SettingsStatus.Text = '자동 탐색 결과를 채웠어요. 경로 검사 후 저장해 주세요.'
}

function New-DiagnosticsZip {
    $output = [string] $script:Settings.OutputPath
    [IO.Directory]::CreateDirectory($output) | Out-Null
    $buildRoot = Join-Path $script:StateRoot ("DiagnosticsBuild-{0}" -f $PID)
    if (Test-Path -LiteralPath $buildRoot) { Remove-Item -LiteralPath $buildRoot -Recurse -Force }
    [IO.Directory]::CreateDirectory($buildRoot) | Out-Null
    $summary = [ordered] @{
        schema = 'wows-toolbox-diagnostics/v1'
        created = (Get-Date).ToString('o')
        app_version = $script:AppVersion
        os = [Environment]::OSVersion.VersionString
        powershell = $PSVersionTable.PSVersion.ToString()
        games = [ordered] @{
            legends_path_valid = Test-Path -LiteralPath $script:Settings.LegendsPath -PathType Container
            pc_path_valid = Test-Path -LiteralPath $script:Settings.PcPath -PathType Container
            korabli_path_valid = Test-Path -LiteralPath $script:Settings.KorabliPath -PathType Container
        }
        native_obj_pipeline = $true
        cache_bytes = Get-FolderBytes (Get-CacheRoot)
        last_batch_report_present = Test-Path -LiteralPath (Join-Path $script:StateRoot 'last-batch-report.json')
    }
    [IO.File]::WriteAllText(
        (Join-Path $buildRoot 'summary.json'),
        ($summary | ConvertTo-Json -Depth 8),
        [Text.UTF8Encoding]::new($false)
    )
    $lastReport = Join-Path $script:StateRoot 'last-batch-report.json'
    if (Test-Path -LiteralPath $lastReport -PathType Leaf) {
        $text = [IO.File]::ReadAllText($lastReport)
        $text = $text.Replace([Environment]::GetFolderPath('UserProfile'), '%USERPROFILE%')
        foreach ($pair in @(
            @([string] $script:Settings.LegendsPath, '%LEGENDS%'),
            @([string] $script:Settings.PcPath, '%WOWS_PC%'),
            @([string] $script:Settings.KorabliPath, '%KORABLI%'),
            @([string] $script:Settings.OutputPath, '%OUTPUT%')
        )) {
            if (-not [string]::IsNullOrWhiteSpace($pair[0])) { $text = $text.Replace($pair[0], $pair[1]) }
        }
        $text = [regex]::Replace(
            $text,
            '(?i)"(?:[a-z]:\\\\|\\\\\\\\)(?:\\\\.|[^"])*"',
            '"%PATH%"'
        )
        [IO.File]::WriteAllText(
            (Join-Path $buildRoot 'last-batch-report.sanitized.json'),
            $text,
            [Text.UTF8Encoding]::new($false)
        )
    }
    $zip = Join-Path $output ("WoWSToolbox-Diagnostics-{0:yyyyMMdd-HHmmss}.zip" -f (Get-Date))
    Compress-Archive -Path (Join-Path $buildRoot '*') -DestinationPath $zip -Force
    Remove-Item -LiteralPath $buildRoot -Recurse -Force
    Add-Log "개인 경로와 모델 자산을 제외한 진단 ZIP 생성: $zip"
    [Windows.MessageBox]::Show(
        $window, (Get-UiText "진단 ZIP을 만들었어요.`n$zip" "Diagnostic ZIP created.`n$zip"), (Get-UiText '진단 ZIP 완료' 'Diagnostic ZIP ready'),
        [Windows.MessageBoxButton]::OK, [Windows.MessageBoxImage]::Information
    ) | Out-Null
}

$controls.QueueList.Add_SelectionChanged({
    $queueBusy = $null -ne $script:ActiveRunner -or $script:BatchActive
    $queueIndex = $controls.QueueList.SelectedIndex
    $controls.QueueUpButton.IsEnabled = $queueIndex -gt 0 -and -not $queueBusy
    $controls.QueueDownButton.IsEnabled = $queueIndex -ge 0 -and $queueIndex -lt ($script:ExtractionQueue.Count - 1) -and -not $queueBusy
})
function Get-QueueDropIndex {
    param($OriginalSource)
    $current = $OriginalSource
    while ($null -ne $current -and
        $current -isnot [Windows.Controls.ListBoxItem]) {
        try { $current = [Windows.Media.VisualTreeHelper]::GetParent($current) }
        catch { return -1 }
    }
    if ($null -eq $current) { return -1 }
    return $controls.QueueList.ItemContainerGenerator.IndexFromContainer($current)
}

$controls.QueueList.Add_PreviewMouseLeftButtonDown({
    param($sender, $eventArgs)
    $script:QueueDragStart = $eventArgs.GetPosition($controls.QueueList)
    $index = Get-QueueDropIndex $eventArgs.OriginalSource
    $script:QueueDragItem = if ($index -ge 0) {
        $script:ExtractionQueue[$index]
    } else { $null }
})
$controls.QueueList.Add_PreviewMouseMove({
    param($sender, $eventArgs)
    if ($null -eq $script:QueueDragStart -or
        $null -eq $script:QueueDragItem -or
        $eventArgs.LeftButton -ne [Windows.Input.MouseButtonState]::Pressed -or
        $script:BatchActive) { return }
    $point = $eventArgs.GetPosition($controls.QueueList)
    $distance = [math]::Abs($point.X - $script:QueueDragStart.X) +
        [math]::Abs($point.Y - $script:QueueDragStart.Y)
    if ($distance -lt 8) { return }
    try {
        [void] [Windows.DragDrop]::DoDragDrop(
            $controls.QueueList,
            $script:QueueDragItem,
            [Windows.DragDropEffects]::Move
        )
    }
    finally {
        $script:QueueDragStart = $null
        $script:QueueDragItem = $null
    }
})
$controls.QueueList.Add_PreviewDragOver({
    param($sender, $eventArgs)
    if ($null -ne $script:QueueDragItem -and -not $script:BatchActive) {
        $eventArgs.Effects = [Windows.DragDropEffects]::Move
        $eventArgs.Handled = $true
    }
})
$controls.QueueList.Add_Drop({
    param($sender, $eventArgs)
    $item = $script:QueueDragItem
    try {
        if ($null -eq $item -or $script:BatchActive) { return }
        $from = $script:ExtractionQueue.IndexOf($item)
        $to = Get-QueueDropIndex $eventArgs.OriginalSource
        if ($to -lt 0) { $to = $script:ExtractionQueue.Count - 1 }
        if ($from -lt 0 -or $from -eq $to) { return }
        $script:ExtractionQueue.RemoveAt($from)
        $to = [math]::Max(0, [math]::Min($to, $script:ExtractionQueue.Count))
        $script:ExtractionQueue.Insert($to, $item)
        Update-QueueUi
        $controls.QueueList.SelectedItem = $item
        Add-Log "대기열 순서 이동: $($item.Ship.LocalizedName)"
    }
    finally {
        $script:QueueDragStart = $null
        $script:QueueDragItem = $null
    }
})
$controls.QueueUpButton.Add_Click({ Move-QueueItem -Direction -1 })
$controls.QueueDownButton.Add_Click({ Move-QueueItem -Direction 1 })
$controls.SaveQueueButton.Add_Click({ Save-QueueFile })
$controls.LoadQueueButton.Add_Click({ Load-QueueFile })
$controls.PauseButton.Add_Click({
    if (-not $script:BatchActive) { return }
    $script:BatchPaused = -not $script:BatchPaused
    Write-BatchControl -Paused $script:BatchPaused
    $controls.PauseButton.Content = if ($script:BatchPaused) { '계속' } else { '일시 정지' }
    $controls.ProgressMessage.Text = if ($script:BatchPaused) {
        '현재 함선은 마친 뒤 다음 항목 앞에서 멈춰요.'
    } else { '대기열을 다시 진행해요.' }
})
$controls.FormatCombo.Add_SelectionChanged({ Sync-UiToSettings; Save-Settings })
$controls.TextureCombo.Add_SelectionChanged({ Sync-UiToSettings; Save-Settings })
$controls.LodCombo.Add_SelectionChanged({ Sync-UiToSettings; Save-Settings })
$controls.AutoUpdateCheck.Add_Checked({ Sync-UiToSettings; Save-Settings })
$controls.AutoUpdateCheck.Add_Unchecked({ Sync-UiToSettings; Save-Settings })
$controls.CheckUpdateButton.Add_Click({ Start-UpdateCheck -Manual })
$controls.AutoDetectButton.Add_Click({ Auto-DetectPaths })
$controls.CacheRefreshButton.Add_Click({ Refresh-CacheStatus })
$controls.OpenCacheButton.Add_Click({
    $root = Get-CacheRoot
    [IO.Directory]::CreateDirectory($root) | Out-Null
    Start-Process explorer.exe -ArgumentList @($root)
})
$controls.ClearCacheButton.Add_Click({
    $root = Get-CacheRoot
    if (-not (Test-Path -LiteralPath $root -PathType Container)) { Refresh-CacheStatus; return }
    $answer = [Windows.MessageBox]::Show(
        $window,
        (Get-UiText "모델·게임 데이터 캐시를 비울까요?`n원본과 추출 결과는 지우지 않지만 다음 추출은 다시 계산해요." "Clear the model and game-data cache?`nOriginal files and exports are kept, but the next extraction will be recalculated."),
        (Get-UiText '캐시 비우기' 'Clear cache'),
        [Windows.MessageBoxButton]::YesNo,
        [Windows.MessageBoxImage]::Warning
    )
    if ($answer -eq [Windows.MessageBoxResult]::Yes) {
        Remove-Item -LiteralPath $root -Recurse -Force
        Refresh-CacheStatus
        Add-Log '추출 캐시를 비웠어요. 원본과 결과물은 유지했어요.'
    }
})
$controls.DiagnosticsButton.Add_Click({ New-DiagnosticsZip })
function Update-DynamicUiLanguage {
    if ($script:WoWSToolboxLanguage -ne 'en') { return }
    foreach ($name in @(
        'TopTitle', 'TopSubtitle', 'TopStatusText', 'CurrentGamePathText',
        'SelectedShipName', 'SelectedShipMeta', 'ProgressStage', 'ProgressMessage',
        'ViewerPathLabel', 'ViewerStatus', 'SettingsStatus', 'CacheInfoText'
    )) {
        $control = $controls[$name]
        if ($null -ne $control -and $null -ne $control.PSObject.Properties['Text']) {
            $control.Text = Convert-ToUiText ([string] $control.Text)
        }
    }
    foreach ($name in @('PauseButton', 'ExtractButton')) {
        $control = $controls[$name]
        if ($null -ne $control -and $null -ne $control.PSObject.Properties['Content']) {
            $control.Content = Convert-ToUiText ([string] $control.Content)
        }
    }
}
$timer = [Windows.Threading.DispatcherTimer]::new()
$timer.Interval = [TimeSpan]::FromMilliseconds(120)
$timer.Add_Tick({
    try {
        Update-DynamicUiLanguage
        Complete-UpdateWork
        if ($script:ViewerInitializing -and
            -not $script:ViewerCoreConfigured -and
            $null -ne $script:ViewerInitTask -and
            $script:ViewerInitTask.IsCompleted) {
            if ($script:ViewerInitTask.IsFaulted) {
                throw $script:ViewerInitTask.Exception.GetBaseException()
            }
            Complete-ModelViewerInitialization
        }
        if ($null -eq $script:ActiveRunner) { return }
        foreach ($line in $script:ActiveRunner.Drain()) {
            Handle-ProcessLine $line
        }
        if (-not $script:ActiveRunner.IsComplete) { return }
        $exitCode = $script:ActiveRunner.ExitCode
        foreach ($line in $script:ActiveRunner.Drain()) {
            Handle-ProcessLine $line
        }
        $completion = $script:ActiveCompletion
        $script:ActiveRunner.Dispose()
        $script:ActiveRunner = $null
        $script:ActiveOperation = ''
        $script:ActiveCompletion = $null
        Set-BusyState
        if ($null -ne $completion) {
            & $completion $exitCode
        }
        $script:CatalogRefreshSource = ''
        $script:CatalogRefreshOutput = ''
    }
    catch {
        Recover-GuiError -Context '작업 결과를 처리하지 못했어요' `
            -Exception $_.Exception
    }
})
$timer.Start()

$controls.ModelWebView.Add_WebMessageReceived({
    param($messageSender, $messageArgs)
    Handle-ViewerWebMessage $messageArgs
})
$controls.NavExtract.Add_Checked({ Switch-Page 'extract' })
$controls.NavViewer.Add_Checked({ Switch-Page 'viewer' })
$controls.NavSettings.Add_Checked({ Switch-Page 'settings' })
$controls.SourceCombo.Add_SelectionChanged({
    $script:SelectedSource = Get-SourceKey
    Update-QualityControls
    Update-CurrentGamePathUi
    Update-QueueUi
    $source = Get-SourceKey
    $path = Get-CatalogPath $source
    if (-not $script:Catalogs.ContainsKey($source) -and
        (Test-Path -LiteralPath $path -PathType Leaf)) {
        try { Load-CatalogFile -Source $source -Path $path } catch {}
    }
})
$controls.BrowseCurrentGameButton.Add_Click({ Select-CurrentGameFolder })
$controls.RefreshCatalogButton.Add_Click({
    try { Start-CatalogRefresh }
    catch {
        Recover-GuiError -Context '함선 목록 새로고침을 시작하지 못했어요' `
            -Exception $_.Exception
    }
})
$controls.OpenPickerButton.Add_Click({
    try { Show-ShipPicker }
    catch {
        Recover-GuiError -Context '함선 선택 창을 열지 못했어요' `
            -Exception $_.Exception
    }
})
$controls.QueueList.Add_SelectionChanged({
    $busy = $null -ne $script:ActiveRunner -or $script:BatchActive
    $controls.RemoveQueueButton.IsEnabled =
        $null -ne $controls.QueueList.SelectedItem -and -not $busy
})
$controls.RemoveQueueButton.Add_Click({
    $selected = $controls.QueueList.SelectedItem
    if ($null -eq $selected) { return }
    [void] $script:ExtractionQueue.Remove($selected)
    Update-QueueUi
    Add-Log "대기열에서 제거: $($selected.Ship.LocalizedName)"
})
$controls.ClearQueueButton.Add_Click({
    if ($script:ExtractionQueue.Count -eq 0) { return }
    $answer = [Windows.MessageBox]::Show(
        $window,
        (Get-UiText "대기열 $($script:ExtractionQueue.Count)척을 모두 비울까요?" "Remove all $($script:ExtractionQueue.Count) ships from the queue?"),
        (Get-UiText '대기열 비우기' 'Clear queue'),
        [Windows.MessageBoxButton]::YesNo,
        [Windows.MessageBoxImage]::Question
    )
    if ($answer -ne [Windows.MessageBoxResult]::Yes) { return }
    $script:ExtractionQueue.Clear()
    Update-QueueUi
    Add-Log '추출 대기열을 비웠어요.'
})
$controls.InspectButton.Add_Click({ [void] (Test-ExtractionReady) })
$controls.ExtractButton.Add_Click({ Start-ShipExtraction })
$controls.CancelButton.Add_Click({
    if ($script:BatchActive) {
        $script:CancelRequested = $true
        Write-BatchControl -Cancel $true
        $controls.ProgressMessage.Text = '현재 함선 뒤에서 안전하게 중단하도록 요청했어요.'
    }
    elseif ($null -ne $script:ActiveRunner) {
        $script:CancelRequested = $true
        $script:ActiveRunner.CancelTree()
    }
})
$controls.BrowseOutputButton.Add_Click({
    $selected = Select-Folder $script:Settings.OutputPath
    if ($null -ne $selected) {
        $script:Settings.OutputPath = $selected
        $controls.SettingsOutputBox.Text = $selected
        Update-OutputLabel
        Save-Settings
    }
})

foreach ($binding in @(
    @('BrowseLegendsButton', 'LegendsPathBox'),
    @('BrowsePcButton', 'PcPathBox'),
    @('BrowseKorabliButton', 'KorabliPathBox'),
    @('BrowseSettingsOutputButton', 'SettingsOutputBox')
)) {
    $buttonName = $binding[0]
    $boxName = $binding[1]
    $controls[$buttonName].Add_Click({
        $selected = Select-Folder $controls[$boxName].Text
        if ($null -ne $selected) { $controls[$boxName].Text = $selected }
    }.GetNewClosure())
}
$controls.BrowseOodleButton.Add_Click({
    $selected = Select-File $controls.OodlePathBox.Text `
        'Oodle 런타임|oo2core_*_win64.dll|DLL 파일|*.dll'
    if ($null -ne $selected) { $controls.OodlePathBox.Text = $selected }
})
$controls.FindOodleButton.Add_Click({
    Sync-UiToSettings
    $found = Find-OodleRuntime
    if ([string]::IsNullOrWhiteSpace($found)) {
        $controls.SettingsStatus.Text =
            'Oodle 런타임을 자동으로 찾지 못했어요. 직접 선택해 주세요.'
    }
    else {
        $controls.OodlePathBox.Text = $found
        $controls.SettingsStatus.Text = "Oodle 런타임을 찾았어요: $found"
    }
})
$controls.ValidateSettingsButton.Add_Click({ [void] (Test-SettingsPaths) })
$controls.SaveSettingsButton.Add_Click({
    $oldPaths = @{
        legends = [string] $script:Settings.LegendsPath
        pc = [string] $script:Settings.PcPath
        korabli = [string] $script:Settings.KorabliPath
    }
    Sync-UiToSettings
    foreach ($source in @('legends', 'pc', 'korabli')) {
        if ((Get-NormalizedGamePath $oldPaths[$source]) -ne
            (Get-NormalizedGamePath (Get-GamePath $source))) {
            [void] $script:Catalogs.Remove($source)
        }
    }
    Save-Settings
    Update-OutputLabel
    Update-CurrentGamePathUi
    $controls.SettingsStatus.Text = '설정을 저장했어요.'
    Add-Log '설정을 저장했어요.'
})

$controls.OpenModelButton.Add_Click({
    try {
        $initial = if (-not [string]::IsNullOrWhiteSpace($script:ViewerModelPath)) {
            $script:ViewerModelPath
        }
        else { '' }
        $selected = Select-File $initial 'Wavefront OBJ|*.obj|모든 파일|*.*'
        if ($null -ne $selected) { Open-ModelInViewer $selected }
    }
    catch {
        Recover-GuiError -Context '모델을 열지 못했어요' -Exception $_.Exception
    }
})
$controls.OpenCompareModelButton.Add_Click({
    try {
        if ([string]::IsNullOrWhiteSpace($script:ViewerModelPath)) {
            throw '비교 전에 기준 OBJ를 먼저 열어 주세요.'
        }
        $selected = Select-File $script:ViewerModelPath 'Wavefront OBJ|*.obj|모든 파일|*.*'
        if ($null -ne $selected) { Send-CompareModelToViewer $selected }
    }
    catch {
        Recover-GuiError -Context '비교 모델을 열지 못했어요' -Exception $_.Exception
    }
})
$controls.OpenRecentModelButton.Add_Click({
    try {
        $recent = Find-RecentModel
        if ([string]::IsNullOrWhiteSpace($recent)) {
            [Windows.MessageBox]::Show(
                $window,
                (Get-UiText '출력 폴더에서 OBJ 파일을 찾지 못했어요.' 'No OBJ file was found in the output folder.'),
                (Get-UiText '최근 모델 없음' 'No recent model'),
                [Windows.MessageBoxButton]::OK,
                [Windows.MessageBoxImage]::Information
            ) | Out-Null
            return
        }
        Open-ModelInViewer $recent
    }
    catch {
        Recover-GuiError -Context '최근 모델을 열지 못했어요' -Exception $_.Exception
    }
})
$controls.OpenViewerFolderButton.Add_Click({
    if ([string]::IsNullOrWhiteSpace($script:ViewerModelPath)) { return }
    $modelDirectory = [IO.Path]::GetDirectoryName($script:ViewerModelPath)
    if (Test-Path -LiteralPath $modelDirectory -PathType Container) {
        Start-Process explorer.exe -ArgumentList @($modelDirectory)
    }
})
$window.Dispatcher.Add_UnhandledException({
    param($sender, $eventArgs)
    Recover-GuiError -Context '화면 처리 중 오류가 발생했어요' `
        -Exception $eventArgs.Exception
    $eventArgs.Handled = $true
})
$window.Add_Closing({
    $updateDownloadActive = $null -ne $script:UpdateDownloadTask
    if (-not $script:UpdateInstallerStarted -and (
        $script:BatchActive -or $null -ne $script:ActiveRunner -or
        $updateDownloadActive
    )) {
        $answer = [Windows.MessageBox]::Show(
            $window,
            (Get-UiText '작업이 실행 중이에요. 하위 프로세스를 종료하고 창을 닫을까요?' 'A task is running. Stop its child processes and close the window?'),
            (Get-UiText '작업 중' 'Task running'),
            [Windows.MessageBoxButton]::YesNo,
            [Windows.MessageBoxImage]::Warning
        )
        if ($answer -ne [Windows.MessageBoxResult]::Yes) {
            $_.Cancel = $true
            return
        }
        if ($null -ne $script:ActiveRunner) {
            $script:ActiveRunner.CancelTree()
        }
    }
    Stop-UpdateNetwork
    $timer.Stop()
    if ($null -ne $script:NotifyTimer) { $script:NotifyTimer.Stop() }
    if ($null -ne $script:NotifyIcon) {
        $script:NotifyIcon.Visible = $false
        $script:NotifyIcon.Dispose()
    }
    # Dispose the WPF WebView2 host before PowerShell exits. Letting setup or
    # Windows tear down its child process can surface 0x80000003 dialogs.
    try {
        if ($null -ne $controls.ModelWebView.CoreWebView2) {
            $controls.ModelWebView.CoreWebView2.Stop()
        }
    }
    catch {}
    try { $controls.ModelWebView.Dispose() } catch {}
    $script:ViewerReady = $false
    $script:ViewerCoreConfigured = $false
})

Sync-SettingsToUi
Update-QueueUi
$initialCatalog = Get-CatalogPath 'legends'
if (Test-Path -LiteralPath $initialCatalog -PathType Leaf) {
    try { Load-CatalogFile -Source 'legends' -Path $initialCatalog } catch {}
}
if (-not [string]::IsNullOrWhiteSpace($script:SettingsRecoveryNotice)) {
    Add-Log $script:SettingsRecoveryNotice -ErrorLine
}
Add-Log (Get-UiText "WoWS Toolbox $($script:AppVersion) 준비 완료. 설치본 선택·대기열 추출·모델/장갑 뷰어를 사용할 수 있어요." "WoWS Toolbox $($script:AppVersion) ready. Select an installation, extract queued ships, and use the model/armor viewer.")
Switch-Page 'extract'
if ($QueueSelfTest) {
    $dummyShip = [pscustomobject] @{
        Id = 'queue-self-test'
        LocalizedName = 'Queue Test Ship'
        Tier = 6
        ShipCode = 'TEST001'
        GameParamsKey = 'PTEST001_Queue_Test'
        GameParamsIndex = 'PTEST001'
        ModelPath = ''
        ShipResource = ''
        Nation = 'Test'
        ShipClass = 'Destroyer'
    }
    $script:ExtractionQueue.Add(
        (New-ShipQueueEntry -Source 'legends' -Ship $dummyShip)
    )
    Update-QueueUi
    $queueValidationOk = $false
    try {
        [void] (ConvertTo-ValidatedQueueEntries -Rows @(
            [pscustomobject] @{ source = 'invalid'; ship = $dummyShip }
        ))
    }
    catch { $queueValidationOk = $true }
    $pathSafetyOk = -not [string]::IsNullOrWhiteSpace(
        (Get-OutputPathProblem -OutputPath $script:StateRoot -GamePaths @($script:StateRoot))
    )
    $batchManifest = New-BatchManifest
    $manifestOnDisk = Get-Content -Raw -LiteralPath $script:BatchManifestPath |
        ConvertFrom-Json
    $manifestOk =
        $batchManifest.items.Count -eq 1 -and
        $manifestOnDisk.items.Count -eq 1 -and
        ([string] $manifestOnDisk.items[0].game_dir) -eq
            (Get-QueueEntryGamePath $script:ExtractionQueue[0]) -and
        -not [string]::IsNullOrWhiteSpace([string] $manifestOnDisk.common.formats)
    $installIsolationOk =
        (Get-GamePathToken 'C:\Games\WoWS-Live') -ne
        (Get-GamePathToken 'C:\Games\WoWS-PTS')
    $launchProbe = Join-Path $script:StateRoot 'queue_launch_probe.py'
    [IO.File]::WriteAllText(
        $launchProbe,
        'print("queue-launch-ok", flush=True)',
        [Text.UTF8Encoding]::new($false)
    )
    $script:BatchExtractScript = $launchProbe
    $launchOk = $false
    try {
        Start-PersistentBatchExtraction
        $deadline = (Get-Date).AddSeconds(5)
        while (-not $script:ActiveRunner.IsComplete -and (Get-Date) -lt $deadline) {
            [Threading.Thread]::Sleep(20)
        }
        if ($script:ActiveRunner.IsComplete) {
            $launchLines = @($script:ActiveRunner.Drain())
            $launchOk =
                $script:ActiveRunner.ExitCode -eq 0 -and
                @($launchLines | Where-Object Text -eq 'queue-launch-ok').Count -eq 1
        }
    }
    finally {
        if ($null -ne $script:ActiveRunner) {
            if (-not $script:ActiveRunner.IsComplete) {
                $script:ActiveRunner.CancelTree()
                [Threading.Thread]::Sleep(100)
            }
            $script:ActiveRunner.Dispose()
            $script:ActiveRunner = $null
        }
        $script:BatchActive = $false
        Set-BusyState
    }
    [pscustomobject] @{
        ok =
            $script:ExtractionQueue.Count -eq 1 -and
            $controls.SelectedShipName.Text -eq (Get-UiText '1척 대기 중' '1 ship queued') -and
            $controls.ExtractButton.IsEnabled -and
            $controls.ClearQueueButton.IsEnabled -and
            $manifestOk -and
            $installIsolationOk -and
            $queueValidationOk -and
            $pathSafetyOk -and
            $launchOk -and
            ($script:WoWSToolboxLanguage -ne 'en' -or $controls.LogBox.Text -notmatch '[가-힣]')
        queue_count = $script:ExtractionQueue.Count
        heading = $controls.SelectedShipName.Text
        extract_enabled = $controls.ExtractButton.IsEnabled
        clear_enabled = $controls.ClearQueueButton.IsEnabled
        manifest_ok = $manifestOk
        install_isolation_ok = $installIsolationOk
        manifest_formats = [string] $manifestOnDisk.common.formats
        queue_validation_ok = $queueValidationOk
        path_safety_ok = $pathSafetyOk
        launch_ok = $launchOk
        english_log_ok = ($script:WoWSToolboxLanguage -ne 'en' -or $controls.LogBox.Text -notmatch '[가-힣]')
        display = $script:ExtractionQueue[0].Display
    } | ConvertTo-Json -Compress
    $window.Close()
    return
}
if (-not [string]::IsNullOrWhiteSpace($CatalogTestSource)) {
    $sourceIndex = @{
        legends = 0
        pc = 1
        korabli = 2
    }[$CatalogTestSource]
    $controls.SourceCombo.SelectedIndex = $sourceIndex
    $window.Opacity = 0
    $window.ShowInTaskbar = $false
    $deadline = (Get-Date).AddSeconds(90)
    $catalogWatcher = [Windows.Threading.DispatcherTimer]::new()
    $catalogWatcher.Interval = [TimeSpan]::FromMilliseconds(150)
    $catalogWatcher.Add_Tick({
        if ((Get-Date) -gt $deadline) {
            $catalogWatcher.Stop()
            if ($null -ne $script:ActiveRunner) {
                $script:ActiveRunner.CancelTree()
            }
            [Console]::WriteLine(([pscustomobject] @{
                ok = $false
                source = $CatalogTestSource
                error = 'catalog test timeout'
            } | ConvertTo-Json -Compress))
            $window.Close()
            return
        }
        if ($null -ne $script:ActiveRunner) { return }
        $count = if ($script:Catalogs.ContainsKey($CatalogTestSource)) {
            $script:Catalogs[$CatalogTestSource].Count
        }
        else { 0 }
        $catalogWatcher.Stop()
        [Console]::WriteLine(([pscustomobject] @{
            ok = ($count -gt 0)
            source = $CatalogTestSource
            count = $count
            stage = $controls.ProgressStage.Text
            message = $controls.ProgressMessage.Text
        } | ConvertTo-Json -Compress))
        $window.Close()
    })
    $window.Add_ContentRendered({
        $catalogWatcher.Start()
        Start-CatalogRefresh
    })
    [void] $window.ShowDialog()
    return
}
if (-not [string]::IsNullOrWhiteSpace($ViewerTestModel)) {
    $window.Opacity = 0.02
    $window.Left = -32000
    $window.Top = -32000
    $window.ShowInTaskbar = $false
    $deadline = (Get-Date).AddSeconds(45)
    $viewerModelQueued = $false
    $viewerWatcher = [Windows.Threading.DispatcherTimer]::new()
    $viewerWatcher.Interval = [TimeSpan]::FromMilliseconds(150)
    $viewerWatcher.Add_Tick({
        if (-not $viewerModelQueued -and $script:ViewerReady) {
            $viewerModelQueued = $true
            try { Open-ModelInViewer $ViewerTestModel }
            catch {
                $script:ViewerTestResult = [pscustomobject] @{
                    ok = $false
                    error = $_.Exception.Message
                }
            }
        }
        if ($null -ne $script:ViewerTestResult) {
            $viewerWatcher.Stop()
            [Console]::WriteLine(($script:ViewerTestResult | ConvertTo-Json -Compress))
            $window.Close()
            return
        }
        if ((Get-Date) -gt $deadline) {
            $viewerWatcher.Stop()
            [Console]::WriteLine(([pscustomobject] @{
                ok = $false
                error = 'viewer test timeout'
                status = $controls.ViewerStatus.Text
                model_queued = $viewerModelQueued
                task_status = if ($null -eq $script:ViewerInitTask) { 'none' } else { [string] $script:ViewerInitTask.Status }
                is_loaded = [bool] $controls.ModelWebView.IsLoaded
                actual_width = [int] $controls.ModelWebView.ActualWidth
                actual_height = [int] $controls.ModelWebView.ActualHeight
                core_configured = [bool] $script:ViewerCoreConfigured
                core_present = $null -ne $controls.ModelWebView.CoreWebView2
                source = if ($null -eq $controls.ModelWebView.Source) { '' } else { [string] $controls.ModelWebView.Source }
            } | ConvertTo-Json -Compress))
            $window.Close()
        }
    })
    $window.Add_ContentRendered({
        $viewerWatcher.Start()
        $controls.NavViewer.IsChecked = $true
        Initialize-ModelViewer
    })
    [void] $window.ShowDialog()
    return
}
if ($SmokeTest) {
    [pscustomobject] @{
        ok = $true
        selected_source = (Get-SourceKey)
        page = $controls.TopTitle.Text
        event_runtime = $true
        launcher_backend = (Test-Path -LiteralPath $script:ExtractScript)
    } | ConvertTo-Json -Compress
    $timer.Stop()
    $window.Close()
    return
}
$window.Add_ContentRendered({
    if (-not $script:UpdateCheckStarted -and
        [string] $script:Settings.AutoCheckUpdates -eq 'true') {
        Start-UpdateCheck
    }
})
try {
    [void] $window.ShowDialog()
}
finally {
    if ($null -ne $script:InstanceMutex) {
        try { $script:InstanceMutex.ReleaseMutex() } catch {}
        $script:InstanceMutex.Dispose()
        $script:InstanceMutex = $null
    }
}
