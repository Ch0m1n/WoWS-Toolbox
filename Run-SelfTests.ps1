#requires -Version 7.0

[CmdletBinding()]
param([string] $Python = 'python')

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$pythonCommand = (Get-Command $Python -ErrorAction Stop).Source
$pwshCommand = (Get-Command pwsh -ErrorAction Stop).Source
$mainGui = Join-Path $PSScriptRoot 'GUI\WoWSToolboxGUI.ps1'
$environmentSkips = 0

function Assert-ExitCode([string] $Label) {
    if ($LASTEXITCODE -ne 0) { throw "$Label failed with exit code $LASTEXITCODE." }
}

Write-Host '1/9 Main GUI and queue self-tests'
$mainSelf = & $pwshCommand -STA -NoLogo -NoProfile -File $mainGui -SelfTest |
    Select-Object -Last 1 | ConvertFrom-Json
Assert-ExitCode 'Main GUI self-test'
if (-not $mainSelf.ok -or $mainSelf.language -ne 'en' -or $mainSelf.controls -lt 60 -or
    $mainSelf.source_count -ne 4 -or -not $mainSelf.blitz_control_present -or
    $mainSelf.hull_only_control_present -or $mainSelf.thumbnail_control_present -or
    -not $mainSelf.viewer_control_present -or
    -not $mainSelf.update_controls_present -or
    $mainSelf.auto_update_default -ne 'true' -or
    -not $mainSelf.update_release_parser_ok -or
    -not $mainSelf.queue_controls_present -or
    $mainSelf.batch_extract_label -notin @('대기열 모델 추출', 'Extract queued models') -or
    -not $mainSelf.webview_runtime_present -or
    -not $mainSelf.combo_custom_template -or
    -not $mainSelf.program_output_default -or
    $mainSelf.output_folder_name -ne 'output' -or
    -not $mainSelf.deprecated_packaged_output_detected -or
    -not $mainSelf.custom_output_preserved -or
    -not $mainSelf.combo_item_style_present) {
    throw 'Main GUI self-test acceptance failed.'
}
$smoke = & $pwshCommand -STA -NoLogo -NoProfile -File $mainGui -SmokeTest |
    Select-Object -Last 1 | ConvertFrom-Json
Assert-ExitCode 'Main GUI smoke test'
$queueSelf = & $pwshCommand -STA -NoLogo -NoProfile -File $mainGui -QueueSelfTest |
    Select-Object -Last 1 | ConvertFrom-Json
Assert-ExitCode 'Queue self-test'
if (-not $smoke.ok -or -not $smoke.event_runtime -or
    -not $queueSelf.ok -or $queueSelf.queue_count -ne 1 -or
    -not $queueSelf.extract_enabled -or -not $queueSelf.clear_enabled -or
    -not $queueSelf.manifest_ok -or $queueSelf.manifest_formats -ne 'obj' -or
    -not $queueSelf.english_log_ok -or
    -not $queueSelf.queue_validation_ok -or -not $queueSelf.path_safety_ok -or
    -not $queueSelf.launch_ok) {
    throw 'Main GUI runtime/queue acceptance failed.'
}

$windowsPowerShell = Join-Path $env:SystemRoot (
    'System32\WindowsPowerShell\v1.0\powershell.exe'
)
if (-not (Test-Path -LiteralPath $windowsPowerShell -PathType Leaf)) {
    throw 'Windows PowerShell 5.1 is missing from this Windows installation.'
}
$legacySelf = & $windowsPowerShell -STA -NoLogo -NoProfile -ExecutionPolicy Bypass -File $mainGui -SelfTest |
    Select-Object -Last 1 | ConvertFrom-Json
Assert-ExitCode 'Windows PowerShell 5.1 GUI self-test'
$legacySmoke = & $windowsPowerShell -STA -NoLogo -NoProfile -ExecutionPolicy Bypass -File $mainGui -SmokeTest |
    Select-Object -Last 1 | ConvertFrom-Json
Assert-ExitCode 'Windows PowerShell 5.1 GUI smoke test'
if (-not $legacySelf.ok -or -not $legacySelf.update_release_parser_ok -or
    -not $legacySelf.update_controls_present -or -not $legacySmoke.ok -or
    -not $legacySmoke.event_runtime) {
    throw 'Windows PowerShell 5.1 compatibility acceptance failed.'
}

$languageMarker = Join-Path $PSScriptRoot 'app-language.txt'
$originalLanguageMarker = [IO.File]::ReadAllBytes($languageMarker)
if ([Text.Encoding]::UTF8.GetString($originalLanguageMarker).Trim() -ne 'en') {
    throw 'English is not the default application language.'
}
try {
    [IO.File]::WriteAllText(
        $languageMarker,
        "en`n",
        [Text.UTF8Encoding]::new($false)
    )
    $englishMain = & $pwshCommand -STA -NoLogo -NoProfile -File $mainGui -SelfTest |
        Select-Object -Last 1 | ConvertFrom-Json
    Assert-ExitCode 'English main GUI self-test'
}
finally {
    [IO.File]::WriteAllBytes($languageMarker, $originalLanguageMarker)
}
if ($englishMain.language -ne 'en' -or
    $englishMain.batch_extract_label -ne 'Extract queued models') {
    throw 'English UI language propagation acceptance failed.'
}

Write-Host '2/9 Ship picker and modern queue UI checks'
$guiText = Get-Content -Raw -LiteralPath $mainGui
$catalogLocalizationOk =
    $guiText.Contains('$catalogLanguage = if ($script:WoWSToolboxLanguage') -and
    $guiText.Contains('"$Source-v$catalogVersion-$catalogLanguage-$installToken.json"')
if (-not $catalogLocalizationOk) {
    throw 'Language-specific ship/camouflage catalog caching is missing.'
}
$unsafeInterpolation = [regex]::Match($guiText, '\$[A-Za-z_][A-Za-z0-9_]*[가-힣]+')
if ($unsafeInterpolation.Success) {
    throw "Unsafe Korean variable interpolation: $($unsafeInterpolation.Value)"
}
$pickerMatch = [regex]::Match(
    $guiText,
    '(?s)\$pickerXaml\s*=\s*@''\r?\n(?<xaml>.*?)\r?\n''@'
)
if (-not $pickerMatch.Success) { throw 'Ship picker XAML block is missing.' }
Add-Type -AssemblyName PresentationFramework
$reader = [Xml.XmlNodeReader]::new([xml] $pickerMatch.Groups['xaml'].Value)
$picker = [Windows.Markup.XamlReader]::Load($reader)
try {
    $grid = $picker.FindName('ShipGrid')
    if ($null -eq $grid -or $grid.Columns.Count -lt 6 -or
        $null -eq $picker.FindName('FavoriteOnlyButton') -or
        $null -eq $picker.FindName('RecentOnlyButton') -or
        $null -eq $picker.FindName('SelectVisibleButton') -or
        $null -eq $picker.FindName('ClearVisibleButton') -or
        [string] $picker.FindName('ChooseButton').Content -ne '대기열에 적용') {
        throw 'Ship picker multi-select/favorite acceptance failed.'
    }
}
finally { $picker.Close() }
foreach ($marker in @(
    'AllowDrop="True"', 'function Save-QueueFile', 'function Load-QueueFile',
    'function Start-PersistentBatchExtraction', 'function Show-CompletionNotification',
    'OpenCompareModelButton', 'FormatCombo', 'TextureCombo', 'LodCombo',
    'CamouflageCombo', 'CamouflageCatalogVersion', '$staleCamouflageCatalog', 'LanguageCombo', 'Convert-XamlToUiLanguage', 'Get-WoWSToolboxLanguageMarker',
    'function Update-DynamicUiLanguage',
    '''TopSubtitle'', ''TopStatusText'', ''SelectedShipName'', ''SelectedShipMeta''',
    '$searchable.IndexOf(', '$script:ExtractionQueue.Insert($to, $item)',
    'modelReportUrl', 'assemblyReportUrl', 'Get-AssemblyValidationPath',
    'Test-DeprecatedPackagedOutputPath', '?app=5.0.61',
    'ConvertTo-ValidatedQueueEntries', '[PIPELINE] ', 'child_heartbeat',
    'Get-OutputPathProblem', 'add_NavigationStarting', 'add_NewWindowRequested',
    '$grid.Add_MouseDoubleClick(', '$getPickerRowFromSource',
    '$rowContainer.Item.QueueSelected = $true', '& $applyPickerSelection',
    'CurrentGamePathText', 'BrowseCurrentGameButton',
    'function Get-GameFolderSource', 'function Resolve-GameFolderRoot',
    'function Get-QueueEntryGamePath', 'game_path = Get-QueueEntryGamePath',
    "modelMapping=' +", '$script:ViewerReady = $false',
    'Add-Log "뷰어 오류: $viewerError" -ErrorLine',
    "SettingsSchema = '2'", "TextureMaxSize = '0'",
    'ModelWebView.Dispose()', 'function Write-JsonAtomic',
    'Local\WoWSToolbox.Gui.v1', 'function Update-QualityControls',
    'Ship-specific appearance · automatic',
    'AutoUpdateCheck', 'CheckUpdateButton', 'function Start-UpdateCheck',
    'Test-Path -LiteralPath $InitialPath -PathType Container',
    '$initial = [string] $script:Settings.OutputPath',
    'PYTHONIOENCODING',
    'api.github.com/repos/Ch0m1n/WoWS-Toolbox/releases/latest',
    'digest', 'Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256'
)) {
    if (-not $guiText.Contains($marker)) { throw "Modern queue marker missing: $marker" }
}
$localizationScript = Join-Path $PSScriptRoot 'GUI\Localization.ps1'
$localizationText = Get-Content -Raw -LiteralPath $localizationScript
if (-not $localizationText.Contains('function Convert-ToFormalKoreanText')) {
    throw 'Formal Korean localization function is missing.'
}
. $localizationScript
Set-WoWSToolboxLanguage 'ko'
$formalKoreanSamples = @(
    (Get-UiText '설정을 저장했어요.' 'Settings saved.'),
    (Convert-ToUiText '대기열이 비어 있어요.'),
    (Convert-XamlToUiLanguage '<TextBlock Text="모델을 열면 개별 파트가 여기에 표시돼요."/>'),
    (Convert-ToUiText '업데이트할까요?')
)
if ($formalKoreanSamples[0] -ne '설정을 저장했습니다.' -or
    $formalKoreanSamples[1] -ne '대기열이 비어 있습니다.' -or
    $formalKoreanSamples[2] -notmatch '표시됩니다' -or
    $formalKoreanSamples[3] -ne '업데이트하시겠습니까?') {
    throw "Formal Korean localization acceptance failed: $($formalKoreanSamples -join ' | ')"
}
if ($guiText.Contains('[action] { Send-ViewerMessage $message }')) {
    throw 'Unsafe deferred viewer message callback returned.'
}

$viewerFixtureRoot = Join-Path ([IO.Path]::GetTempPath()) "WoWSToolbox-ViewerFixture-$PID"
[IO.Directory]::CreateDirectory($viewerFixtureRoot) | Out-Null
$viewerFixture = Join-Path $viewerFixtureRoot 'viewer-remap.obj'
try {
    [IO.File]::WriteAllText(
        $viewerFixture,
        @(
            'o ViewerRemapTriangle',
            'v -1 0 0',
            'v 1 0 0',
            'v 0 0 1',
            'vt 0 0',
            'vt 1 0',
            'vt 0.5 1',
            'vn 0 1 0',
            'f 1/1/1 2/2/1 3/3/1'
        ) -join [Environment]::NewLine,
        [Text.UTF8Encoding]::new($false)
    )
    $viewerSelf = & $pwshCommand -STA -NoLogo -NoProfile -File $mainGui -ViewerTestModel $viewerFixture | Select-Object -Last 1 | ConvertFrom-Json
    Assert-ExitCode 'Viewer remap self-test'
    $viewerEnvironmentUnavailable = -not $viewerSelf.ok -and
        $null -ne $viewerSelf.PSObject.Properties['error'] -and
        [string] $viewerSelf.error -match '0x8000FFFF|E_UNEXPECTED|WebView2 .*?(연결 시간이 초과|connection timed out)'
    if (-not $viewerEnvironmentUnavailable -and
        (-not $viewerSelf.ok -or $viewerSelf.parts -lt 1 -or
            $viewerSelf.triangles -ne 1)) {
        throw "Viewer remap acceptance failed: $($viewerSelf | ConvertTo-Json -Compress)"
    }
    if ($viewerEnvironmentUnavailable) {
        $environmentSkips++
        Write-Warning 'Live WPF WebView2 remap test skipped because this Windows session rejected a second controller (E_UNEXPECTED).'
    }
}
finally {
    Remove-Item -LiteralPath $viewerFixtureRoot -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host '3/9 Python syntax, CLIs, and regressions'
$pipelineText = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'FullAssembly\SelectedShip\Pipeline\extract_selected_ship_full.py')
$pipelineWrapperText = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'FullAssembly\SelectedShip\Extract-SelectedShipFull.ps1')
foreach ($marker in @('PYTHONUTF8', 'PYTHONIOENCODING', 'sys.stdout.reconfigure')) {
    if (-not $pipelineText.Contains($marker)) { throw "Pipeline UTF-8 marker missing: $marker" }
}
foreach ($marker in @('"event": "child_start"', '"event": "child_heartbeat"', '"event": "child_complete"')) {
    if (-not $pipelineText.Contains($marker)) { throw "Pipeline liveness marker missing: $marker" }
}
foreach ($marker in @('PYTHONUTF8', 'PYTHONIOENCODING', '[Console]::OutputEncoding')) {
    if (-not $pipelineWrapperText.Contains($marker)) { throw "Pipeline wrapper UTF-8 marker missing: $marker" }
}
$pythonSyntax = @(
    'import ast', 'import pathlib', 'import sys',
    'path = pathlib.Path(sys.argv[1])',
    'ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))'
) -join [Environment]::NewLine
$pythonSearchRoots = @(
    (Join-Path $PSScriptRoot 'Backend'),
    (Join-Path $PSScriptRoot 'BlenderExtractor'),
    (Join-Path $PSScriptRoot 'FullAssembly')
)
$pythonFiles = @(Get-ChildItem -LiteralPath $pythonSearchRoots -Recurse -File -Filter '*.py')
$rootConftest = Join-Path $PSScriptRoot 'conftest.py'
if (Test-Path -LiteralPath $rootConftest -PathType Leaf) {
    $pythonFiles += Get-Item -LiteralPath $rootConftest
}
foreach ($file in $pythonFiles) {
    & $pythonCommand -B -c $pythonSyntax $file.FullName
    Assert-ExitCode "Python syntax: $($file.FullName)"
}
foreach ($relative in @(
    'Backend\catalog.py', 'Backend\extract_ship.py', 'Backend\batch_extract.py',
    'Backend\armor_sidecar.py'
)) {
    & $pythonCommand (Join-Path $PSScriptRoot $relative) --help | Out-Null
    Assert-ExitCode "$relative CLI"
}
foreach ($test in @(
    Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'Backend') `
        -File -Filter 'test_*.py' | Sort-Object FullName
)) {
    & $pythonCommand -B $test.FullName | Out-Null
    Assert-ExitCode "Backend regression: $($test.FullName)"
}
$mappingResolverTest = @(
    'import importlib.util', 'import pathlib', 'import sys',
    'path = pathlib.Path(sys.argv[1])',
    'spec = importlib.util.spec_from_file_location("selected_mapping", path)',
    'module = importlib.util.module_from_spec(spec)',
    'assert spec.loader is not None', 'spec.loader.exec_module(module)',
    'root = {"PISC510_Napoli": object(), "PISC511_Napoli_B": object()}',
    'assert module._resolve_ship_key(root, "PISC510_Napoli_1944") == "PISC510_Napoli"'
) -join [Environment]::NewLine
& $pythonCommand -B -c $mappingResolverTest (Join-Path $PSScriptRoot `
    'FullAssembly\Ticonderoga1990\Mapping\build_selected_ship_assembly.py')
Assert-ExitCode 'Legends exact key resolver'
foreach ($test in @(
    Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'FullAssembly') `
        -Recurse -File -Filter 'test_*.py' | Sort-Object FullName
)) {
    & $pythonCommand -B $test.FullName | Out-Null
    Assert-ExitCode "FullAssembly regression: $($test.FullName)"
}
foreach ($test in @(
    Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot `
        'BlenderExtractor\geometry_decoder') -File -Filter 'test_*.py' |
        Sort-Object FullName
)) {
    & $pythonCommand -B $test.FullName | Out-Null
    Assert-ExitCode "Geometry decoder regression: $($test.FullName)"
}
foreach ($test in @(
    Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'Backend') `
        -File -Filter 'test_*.py' | Sort-Object FullName
)) {
    & $pythonCommand -B $test.FullName | Out-Null
    Assert-ExitCode "Backend regression: $($test.FullName)"
}

Write-Host '4/9 PowerShell syntax'
foreach ($file in Get-ChildItem -LiteralPath $PSScriptRoot -Recurse -File -Filter '*.ps1') {
    $tokens = $null
    $errors = $null
    [void] [Management.Automation.Language.Parser]::ParseFile(
        $file.FullName, [ref] $tokens, [ref] $errors
    )
    if (@($errors).Count) {
        throw "PowerShell syntax: $($file.FullName): $(@($errors).Message -join '; ')"
    }
    $powerShellText = Get-Content -Raw -LiteralPath $file.FullName
    if ($powerShellText -match '(?m)\breturn\s+if\b') {
        throw "PowerShell if-as-command pattern: $($file.FullName)"
    }
}

Write-Host '5/9 Offline viewer, armor, comparison, and analysis checks'
$viewerIndex = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'Viewer\web\index.html')
$viewerCss = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'Viewer\web\viewer.css')
$viewerLightingCss = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'Viewer\web\viewer-lighting-fix.css')
$viewerScript = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'Viewer\web\viewer.js')
$viewerVendor = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'Viewer\web\vendor\three.module.js')
$viewerCore = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'Viewer\web\vendor\three.core.js')
$viewerMtlLoader = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'Viewer\web\vendor\MTLLoader.js')

$viewerI18n = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'Viewer\web\i18n.js')
$advanced = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'Viewer\web\viewer-advanced.js')
$assemblerScript = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'FullAssembly\Ticonderoga1990\BlenderSceneAssembler\assemble_scene.py')
$backendExporter = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'Backend\blender_export_v5.py')
$legendsRepack = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'Backend\blender_repack_obj_v5.py')
if ($viewerIndex -match 'https?://(cdn|unpkg|jsdelivr)' -or
    $viewerI18n -notmatch 'get\(''lang''\) === ''ko'' \? ''ko'' : ''en''' -or
    $viewerI18n -notmatch 'formalKoreanReplacements' -or
    $viewerI18n -notmatch 'language === ''ko''.*formalizeKorean' -or
    $viewerIndex -match 'v=5\.0\.30' -or
    $viewerScript -notmatch "version: '5\.0\.61'" -or
    $advanced -match 'v=5\.0\.30' -or
    $viewerCss -notmatch '#app \{[^}]*grid-template-rows: minmax\(0, 1fr\);[^}]*overflow: hidden' -or
    $viewerCss -notmatch '\.inspector \{[^}]*min-height: 0;[^}]*overflow: hidden;' -or
    $viewerScript -notmatch "from './vendor/three\.module\.js'" -or
    $viewerIndex -notmatch 'viewer-advanced\.js' -or
    $viewerIndex -notmatch 'id="armorModeButton"' -or
    $viewerIndex -notmatch 'id="waterlineButton"' -or
    $viewerIndex -notmatch 'id="waterlinePosition"' -or
    $viewerIndex -notmatch 'id="waterlineValue"' -or
    $viewerIndex -notmatch 'id="backgroundButton"' -or
    $viewerIndex -match 'id="weaponPanel"' -or
    $viewerIndex -match 'id="weaponTraverse"' -or
    $viewerIndex -match 'id="barrelElevation"' -or
    $viewerIndex -match 'id="rotateButton"' -or
    $viewerIndex -notmatch 'id="inspectorResizeHandle"' -or
    $viewerIndex -notmatch 'id="filterPane"' -or
    $viewerIndex -notmatch 'id="selectionPane"' -or
    $viewerIndex -notmatch 'class="pane-resizer"' -or
    $viewerCss -notmatch 'background-hidden' -or
    $viewerIndex -notmatch 'id="compareLayoutButton"' -or
    $viewerScript -notmatch 'wows-toolbox-armor-viewer/v2' -or
    $viewerScript -notmatch 'wows-toolbox-armor-viewer/v3' -or
    $viewerScript -notmatch 'legacyArmorPositionsToViewer' -or
    $advanced -notmatch 'loadCompareModel' -or
    $advanced -notmatch 'effectiveArmor' -or
    $advanced -notmatch 'exactThicknessLabel' -or
    $advanced -notmatch 'SEA_SURFACE' -or
    $advanced -match 'rotateSelected' -or
    $advanced -notmatch 'measurePointerDown' -or
    $advanced -notmatch 'frameComparison' -or
    $advanced -notmatch 'resetAdvancedState' -or
    $advanced -match 'inferBarrelRig' -or
    $advanced -match 'applyBarrelElevation' -or
    $advanced -match 'updateWeaponPanel' -or
    $advanced -notmatch 'INSPECTOR_LAYOUT_KEY' -or
    $advanced -notmatch 'applyInspectorWidth' -or
    $viewerScript -notmatch 'wows-toolbox-native-object-layout/v1' -or
    $viewerScript -notmatch 'pivot_space' -or
    $viewerScript -notmatch 'applyModelMetadata' -or
    $viewerScript -notmatch 'wows-viewer-reset' -or
    $viewerScript -notmatch 'normalizeArmorData' -or
    $viewerScript -notmatch 'modelLoadSerial' -or
    $viewerScript -notmatch 'loadResourceWithRetry' -or
    $viewerScript -notmatch 'orientObjForViewer' -or
    $viewerScript -notmatch 'metadata-y-up' -or
    $viewerScript -match 'ARMOR_DEPTH_RENDER_ORDER' -or
    $viewerScript -match 'viewerArmorMaterialState' -or
    $viewerScript -match 'material\.colorWrite = false' -or
    $viewerScript -match 'function setArmorModelHidden' -or
    $viewerScript -notmatch 'function setArmorModelGhost' -or
    $viewerIndex -notmatch 'id="armorContextOpacity"' -or
    $viewerScript -notmatch "ARMOR_CONTEXT_OPACITY_KEY = 'wows-toolbox-viewer-armor-context-opacity-v1'" -or
    $viewerScript -notmatch 'function setArmorContextOpacity' -or
    $viewerScript -notmatch 'viewerArmorContextBaseOpacity' -or
    $viewerI18n -notmatch "'뒤쪽 함선 불투명도': 'Reference ship opacity'" -or
    $viewerScript -match 'material\.opacity = enabled \? 0\.13' -or
    $viewerScript -notmatch 'material.forceSinglePass = true' -or
    $viewerScript -notmatch 'mesh.renderOrder = ARMOR_RENDER_ORDER_BASE \+ groupIndex' -or
    $viewerScript -notmatch 'normalizeModelMaterials' -or
    $viewerScript -notmatch '  loadShip,' -or
    $viewerScript -notmatch "viewerMaterialPolicy === 'uniform-flat-v8'" -or
    $viewerScript -notmatch 'getMaxAnisotropy' -or
    $viewerScript -notmatch 'LinearMipmapLinearFilter' -or
    $viewerScript -notmatch 'AgXToneMapping' -or
    $viewerScript -notmatch 'createStandardViewerMaterial' -or
    $viewerScript -notmatch 'const neutral = new THREE\.MeshBasicMaterial' -or
    $viewerScript -notmatch 'viewerNeutralFlatMaterial: true' -or
    $viewerScript -notmatch 'viewerFlatBaseColor' -or
    $viewerScript -notmatch 'function flatLightingGain' -or
    $viewerScript -notmatch 'function applyFlatLightingGain' -or
    $viewerScript -notmatch '0\.28 \+ environment \* 0\.5 \+ key \* 0\.3' -or
    $viewerScript -notmatch 'roughnessMap' -or
    $viewerScript -notmatch "LIGHTING_SETTINGS_KEY = 'wows-toolbox-viewer-lighting-v9'" -or
    $viewerScript -notmatch 'metalnessMap: null' -or
    $viewerScript -notmatch 'normalStrengthControl' -or
    $viewerIndex -notmatch 'id="lightingPanel"' -or
    $viewerIndex -notmatch 'id="normalStrengthControl"' -or
    $viewerIndex -notmatch 'id="pbrPreviewControl"' -or
    $viewerScript -notmatch 'viewerPbrChannels' -or
    $viewerScript -notmatch 'THREE\.Cache\.enabled = true' -or
    $viewerScript -notmatch 'DEFERRED_PBR_TEXTURES' -or
    $viewerScript -notmatch 'ensurePbrTexturesLoaded' -or
    $viewerScript -notmatch 'geometryUseCounts' -or
    $viewerScript -notmatch 'sideVariants' -or
    $viewerMtlLoader -notmatch 'this\.textureCache = new Map' -or
    $viewerMtlLoader -notmatch 'ignoreTextureTypes' -or
    $viewerMtlLoader -notmatch 'new ImageBitmapLoader' -or
    $viewerCore -notmatch 'Duplicate texture requests can subscribe' -or
    $viewerCore -notmatch 'return imageBitmap;' -or
    $advanced -notmatch 'ignoreTextureTypes' -or
    $viewerI18n -notmatch "'PBR 텍스처 읽는 중': 'Loading PBR textures'" -or
    $viewerIndex -notmatch 'id="albedoPreviewControl"' -or
    $viewerScript -notmatch 'applyAlbedoPreview' -or
    $viewerScript -notmatch 'albedoPreview: false' -or
    $viewerI18n -notmatch "'알베도 검사': 'Albedo inspection'" -or
    $viewerScript -notmatch 'pbrPreview: false' -or
    $viewerScript -notmatch 'swapPbrMaterials\(modelContent, pbrPreviewEnabled\)' -or
    $viewerScript -notmatch 'Object\.values\(material\.userData\?\.viewerPbrChannels' -or
    $viewerScript -match 'applyStableDoubleSidedNormals' -or
    $viewerScript -notmatch 'applyPbrPreview' -or
    $viewerScript -notmatch 'function selectSurfacePreview' -or
    $viewerScript -notmatch "selectSurfacePreview\('albedo'" -or
    $viewerScript -notmatch 'state\.albedoPreview && state\.pbrPreview' -or
    $viewerScript -match 'WOWS_STABLE_DOUBLE_SIDED_NORMALS' -or
    $viewerVendor -match 'WOWS_STABLE_DOUBLE_SIDED_NORMALS' -or
    $viewerIndex -notmatch '조명과 표면' -or
    $viewerLightingCss -notmatch '\.lighting-desk' -or
    $viewerIndex -notmatch 'viewer\.js\?v=5\.0\.61\.0' -or
    $viewerIndex -notmatch 'viewer-advanced\.js\?v=5\.0\.61\.0' -or
    $viewerScript -notmatch 'loadAssemblyMetadata' -or
    $viewerScript -notmatch 'matrixRowsDeterminant' -or
    $viewerScript -notmatch 'assembly-mirrored-standard-double-sided-v4' -or
    $viewerScript -notmatch 'ship-surface-standard-double-sided-v6' -or
    $viewerScript -notmatch 'THREE\.DoubleSide' -or
    $viewerScript -match 'ARMOR_OCCLUDER_MODEL_TYPES' -or
    $viewerScript -match 'setArmorDepthMask' -or
    $viewerScript -notmatch 'viewerArmorContextMaterial: true' -or
    $viewerScript -notmatch 'nonOccludingArmorContext: true' -or
    $viewerScript -notmatch "type === '선체'\) return 0\.25" -or
    $viewerScript -notmatch "type === '상부구조'\) return 0\.2" -or
    $viewerScript -notmatch 'depthWrite: false' -or
    $viewerScript -match 'setArmorModelHidden' -or
    $viewerScript -match 'ensureArmorDepthProxy' -or
    $viewerScript -notmatch 'depthWrite: true' -or
    $viewerScript -notmatch 'occludesHiddenArmor = true' -or
    $viewerScript -notmatch 'applyAdaptiveRenderQuality' -or
    $viewerScript -notmatch 'modelRadius / 100' -or
    $assemblerScript -notmatch '_repair_obj_mirrored_winding' -or
    $assemblerScript -notmatch 'mirrored_winding_corrected' -or
    $viewerScript -notmatch 'BACKGROUND_VISIBILITY_KEY' -or
    $viewerScript -notmatch 'setBackgroundVisible' -or
    $viewerScript -match 'ensurePartPivot' -or
    $viewerScript -match 'rotatePartAroundUpAxis' -or
    $viewerScript -match 'setPartTraverseDegrees' -or
    $viewerScript -match "event\.code === 'KeyR'" -or
    $viewerScript -notmatch "event\.code === 'KeyB'" -or
    $viewerScript -notmatch 'undoViewerEdit' -or
    $viewerScript -match 'getPartRotationDegrees' -or
    $viewerScript -match 'setPartRotationDegrees' -or
    $viewerScript -notmatch 'LIGHTING_PANEL_POSITION_KEY' -or
    $viewerScript -notmatch 'beginLightingPanelDrag' -or
    $viewerIndex -match 'partRotationX' -or
    $viewerScript -notmatch "addEventListener\('keydown'" -or
    $viewerIndex -notmatch 'Ctrl\+Z 취소' -or
    $advanced -notmatch 'getModelContent\(\)\?\.position\?\.y' -or
    $advanced -notmatch 'sourceWaterline \+ adjustment' -or
    $advanced -notmatch 'compareLoadSerial' -or
    $advanced -notmatch 'core\.recordObjectEdit' -or
    $advanced -notmatch "hostname !== 'compare\.local'" -or
    $advanced -notmatch 'core\.loadResourceWithRetry' -or
    $advanced -notmatch 'core\.orientObjForViewer' -or
    $guiText -notmatch 'ViewerMappingSerial' -or
    $assemblerScript -notmatch 'GAME_NODE_TO_BLENDER_BASIS' -or
    $assemblerScript -notmatch '\(x, y, z\) to \(-x, z, y\)' -or
    $assemblerScript -notmatch 'axis_forward="-Z"' -or
    $assemblerScript -notmatch 'forward_axis="NEGATIVE_Z"' -or
    $backendExporter -notmatch '"obj_axis_forward": "-Z"' -or
    $backendExporter -notmatch 'forward_axis="NEGATIVE_Z"' -or
    $legendsRepack -notmatch 'axis_forward="-Z"' -or
    $legendsRepack -notmatch 'forward_axis="NEGATIVE_Z"' -or
    $viewerScript -match '\.innerHTML' -or
    $advanced -match '\.innerHTML' -or
    $advanced -notmatch 'measureMode') {
    throw 'Offline advanced viewer source acceptance failed.'
}

Write-Host '6/9 Package structure and launcher checks'
$required = @(
    'WoWS Toolbox.exe', 'WoWS-Toolbox-GUI.cmd',
    'Launcher\WoWSToolboxLauncher.cs', 'Launcher\Build-Launcher.ps1',
    'GUI\Launch-Gui.ps1', 'GUI\WoWSToolboxGUI.ps1',
    'Backend\catalog.py', 'Backend\extract_ship.py', 'Backend\batch_extract.py',
    'Backend\blitz_assets.py', 'Backend\blitz_extract.py',
    'Runtime\Python\python.exe',
    'Runtime\Python\Lib\site-packages\UnityPy\__init__.py',
    'Runtime\Python\Lib\site-packages\texture2ddecoder\_texture2ddecoder.cp310-win_amd64.pyd',
    'Backend\armor_sidecar.py',
    'Backend\blender_export_v5.py', 'Backend\blender_repack_obj_v5.py',
    'Backend\wowsunpack.exe', 'Backend\wowsunpack_armor.exe',
    'Viewer\Runtime\Microsoft.Web.WebView2.Core.dll',
    'Viewer\Runtime\Microsoft.Web.WebView2.Wpf.dll',
    'Viewer\Runtime\WebView2Loader.dll',
    'Viewer\web\index.html', 'Viewer\web\viewer.css', 'Viewer\web\viewer-v5.css',
    'Viewer\web\viewer-lighting-fix.css',
    'Viewer\web\viewer.js', 'Viewer\web\viewer-advanced.js',
    'Branding\WoWS-Toolbox.ico',
    'Viewer\web\diagnostics.js', 'Viewer\web\vendor\three.module.js',
    'Viewer\web\vendor\three.core.js', 'Viewer\web\vendor\OrbitControls.js',
    'Viewer\web\vendor\TransformControls.js', 'Viewer\web\vendor\OBJLoader.js',
    'Viewer\web\vendor\MTLLoader.js', 'README.md', 'README_KO.md', 'README.txt',
    'docs\WOWS_BLITZ_GUIDE.md', 'docs\WOWS_BLITZ_GUIDE_KO.md',
    'CONTRIBUTING.md', 'SECURITY.md', '.gitignore', '.gitattributes',
    '.github\workflows\ci.yml', 'examples\batch-request.example.json',
    'Update-SourceManifest.ps1', 'LEGAL_NOTICE.txt', 'THIRD_PARTY_NOTICES.md',
    'pytest.ini'
)
foreach ($relative in $required) {
    if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot $relative) -PathType Leaf)) {
        throw "Required file is missing: $relative"
    }
}
$embeddedPython = Join-Path $PSScriptRoot 'Runtime\Python\python.exe'
$embeddedProbe = & $embeddedPython -B -c 'import UnityPy, lz4, texture2ddecoder; print(UnityPy.__version__)'
if ($LASTEXITCODE -ne 0 -or [string] $embeddedProbe -ne '1.25.3') {
    throw "Bundled UnityPy runtime probe failed: $embeddedProbe"
}
if ((Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'WoWS-Toolbox-GUI.cmd')) `
        -notmatch 'GUI\\Launch-Gui\.ps1') {
    throw 'Main launcher target is wrong.'
}
$backendExtractText = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'Backend\extract_ship.py')
$backendBatchText = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'Backend\batch_extract.py')
$nativeExportText = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'Backend\native_glb_export.py')
if ($backendExtractText -notmatch 'quality_contract' -or
    $backendExtractText -notmatch 'texture-max-size.+default=0' -or
    $backendBatchText -notmatch 'texture_max_size.+, 0' -or
    $nativeExportText -notmatch 'texture-max-size.+default=0') {
    throw 'Lossless LOD0/original-texture quality contract is missing.'
}
if ($nativeExportText -match 'primitive_fingerprint' -or
    $nativeExportText -match 'seen_primitives') {
    throw 'Unsafe duplicate-primitive compaction returned; repeated GLB draw calls must be preserved.'
}
if ($backendExtractText -notmatch 'def validate_camouflage_selection' -or
    $backendExtractText -notmatch 'parser\.add_argument\("--camouflage", default="default"\)' -or
    $backendExtractText -notmatch '\["--camouflage", args\.camouflage\]' -or
    $backendExtractText -notmatch 'args\.camouflage != "default"' -or
    $backendBatchText -notmatch 'item\.get\("camouflage", common\.get\("camouflage", "default"\)\)' -or
    $nativeExportText -notmatch 'KHR_texture_transform' -or
    $nativeExportText -notmatch 'transform_material_uv') {
    throw 'Permanent-camouflage extraction or OBJ UV-transform contract is missing.'
}

$launchGuiText = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'GUI\Launch-Gui.ps1')
foreach ($marker in @('WoWSToolboxGUI.ps1', 'launch-error.log')) {
    if (-not $launchGuiText.Contains($marker)) { throw "GUI launcher marker missing: $marker" }
}

$launcherExe = Join-Path $PSScriptRoot 'WoWS Toolbox.exe'
$launcherInfo = Get-Item -LiteralPath $launcherExe
if ($launcherInfo.VersionInfo.FileVersion.Trim() -ne '5.0.61.0' -or
    $launcherInfo.VersionInfo.ProductVersion.Trim() -ne '5.0.61') {
    throw 'EXE launcher version metadata is wrong.'
}
$launcherProbe = Start-Process -FilePath $launcherExe -ArgumentList '--check' -Wait -PassThru
if ($launcherProbe.ExitCode -ne 0) {
    throw "EXE launcher readiness probe failed: $($launcherProbe.ExitCode)"
}
$launcherSource = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'Launcher\WoWSToolboxLauncher.cs')
foreach ($marker in @('CreateNoWindow = true', 'ProcessWindowStyle.Hidden',
    'PowerShell", "7", "pwsh.exe', 'WindowsPowerShell', '--check')) {
    if (-not $launcherSource.Contains($marker)) { throw "EXE launcher marker missing: $marker" }
}
foreach ($marker in @('if (!File.Exists(marker)) return "en";',
    'return value == "ko" ? "ko" : "en";')) {
    if (-not $launcherSource.Contains($marker)) {
        throw "English-default launcher marker missing: $marker"
    }
}
$installerDefinition = Join-Path $PSScriptRoot 'Installer\WoWS-Toolbox.iss'
if (Test-Path -LiteralPath $installerDefinition -PathType Leaf) {
    $installerText = Get-Content -Raw -LiteralPath $installerDefinition
    foreach ($marker in @('Name: "desktopicon"',
        'Filename: "{app}\WoWS Toolbox.exe"', 'Name: "{app}"; Attribs: notcontentindexed',
        'Tasks: desktopicon', 'CloseApplications=no',
        'RestartApplications=no', 'UpgradeWelcome', 'InstalledVersion',
        'PrepareToInstall', 'FindWindowByWindowName', 'CloseAppForUpdate',
        'onlyifdoesntexist', 'LanguageDetectionMethod=none')) {
        if (-not $installerText.Contains($marker)) { throw "Installer shortcut marker missing: $marker" }
    }
    if ($installerText -notmatch 'AppId=\{\{88AA1660-CC89-4EDA-9895-BC051E8CAD26\}' -or
        $installerText -match 'CloseApplications=force' -or
        $installerText -match 'RestartApplications=yes' -or
        $installerText -match 'Filename: "\{app\}\\WoWS Toolbox\.exe"[^\r\n]*Tasks: startmenuicon') {
        throw 'Installer in-place update identity or graceful close policy is wrong.'
    }
    $buildReleaseText = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'Build-Release.ps1')
    if ($buildReleaseText -notmatch 'FileAttributes]::NotContentIndexed') {
        throw 'Release output folders are not excluded from Windows Search indexing.'
    }
    if ($installerText -match 'Filename: "\{app\}\\WoWS-Toolbox-GUI\.cmd"') {
        throw 'Installer still routes a shortcut or post-install launch through CMD.'
    }
}
else {
    Write-Host 'Installer source checks omitted for runtime-only package.'
}

Write-Host '7/9 Bundled-asset audit'
$files = @(Get-ChildItem -LiteralPath $PSScriptRoot -Recurse -File)
$forbiddenExtensions = @(
    '.geometry','.dds','.wowsreplay','.korablireplay','.blend','.blend1',
    '.glb','.obj','.mtl','.png','.jpg','.jpeg','.tga','.bmp','.pkg','.idx','.pyc'
)
$forbidden = @($files | Where-Object {
    $relative = [IO.Path]::GetRelativePath($PSScriptRoot, $_.FullName)
    $forbiddenExtensions -contains $_.Extension.ToLowerInvariant() -and
    $relative -notlike 'Installer\assets\*'
})
$allowedDll = @(
    'Viewer\Runtime\Microsoft.Web.WebView2.Core.dll',
    'Viewer\Runtime\Microsoft.Web.WebView2.Wpf.dll',
    'Viewer\Runtime\WebView2Loader.dll',
    'Runtime\Python\libcrypto-1_1.dll',
    'Runtime\Python\libffi-7.dll',
    'Runtime\Python\libssl-1_1.dll',
    'Runtime\Python\python3.dll',
    'Runtime\Python\python310.dll',
    'Runtime\Python\sqlite3.dll',
    'Runtime\Python\vcruntime140_1.dll',
    'Runtime\Python\vcruntime140.dll'
)
$unexpectedDll = @($files | Where-Object Extension -eq '.dll' | Where-Object {
    $allowedDll -notcontains [IO.Path]::GetRelativePath($PSScriptRoot, $_.FullName)
})
$cacheDirs = @(Get-ChildItem -LiteralPath $PSScriptRoot -Recurse -Directory |
    Where-Object { $_.Name -in @('__pycache__','.selftest-state') })
if ($forbidden.Count -or $unexpectedDll.Count -or $cacheDirs.Count) {
    throw 'Asset/cleanup audit failed.'
}

Write-Host '8/9 Dependency and license audit'
$webViewBootstrapper = Join-Path $PSScriptRoot 'Installer\dependencies\MicrosoftEdgeWebview2Setup.exe'
if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'Installer') -PathType Container) {
    if (-not (Test-Path -LiteralPath $webViewBootstrapper -PathType Leaf) -or
        (Get-FileHash -LiteralPath $webViewBootstrapper -Algorithm SHA256).Hash -ne
            '8C4A80540B6BBCBEF30A4E8C7D1AC504B6FC09DB922B4ACDFD85C9D5F6F1050E') {
        throw 'Microsoft WebView2 bootstrapper is missing or has the wrong SHA-256.'
    }
}
$threeCore = Get-Item -LiteralPath (Join-Path $PSScriptRoot 'Viewer\web\vendor\three.core.js')
$threeModule = Get-Item -LiteralPath (Join-Path $PSScriptRoot 'Viewer\web\vendor\three.module.js')
$notices = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'THIRD_PARTY_NOTICES.md')
if ($threeCore.Length -lt 1000000 -or $threeModule.Length -lt 500000 -or
    $notices -notmatch '1\.0\.4078\.44' -or $notices -notmatch '0\.185\.1' -or
    $notices -notmatch 'Backend/wowsunpack_armor\.exe' -or
    $notices -notmatch 'RPC\s+`FLOAT64`\s+support') {
    throw 'Dependency or license acceptance failed.'
}
$expectedExporterHashes = @{
    'Backend\wowsunpack.exe' = '61964E7B197CC84FDDC629238CD5F0490F8EBD46F7788F6D65500C4F03BE70E6'
    'Backend\wowsunpack_armor.exe' = '401077298F115655650E3CCB60B65A55533D3B1DD5B39EC433998D655A0264C6'
}
foreach ($relative in $expectedExporterHashes.Keys) {
    $actualHash = (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot $relative) -Algorithm SHA256).Hash
    if ($actualHash -ne $expectedExporterHashes[$relative]) {
        throw "Bundled exporter hash is wrong: $relative"
    }
}

Write-Host '9/9 SHA-256 manifest verification'
$manifest = Join-Path $PSScriptRoot 'MANIFEST.sha256'
if (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) {
    throw 'MANIFEST.sha256 is missing.'
}
$seen = @{}
foreach ($line in Get-Content -LiteralPath $manifest) {
    if ($line -notmatch '^([0-9a-f]{64}) \*(.+)$') { throw "Malformed manifest: $line" }
    $expected, $relative = $Matches[1], $Matches[2]
    if ($seen.ContainsKey($relative)) { throw "Duplicate manifest path: $relative" }
    $seen[$relative] = $true
    $path = Join-Path $PSScriptRoot $relative.Replace('/', [IO.Path]::DirectorySeparatorChar)
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing: $relative" }
    $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected) { throw "Manifest hash mismatch: $relative" }
}
$expectedFiles = @(Get-ChildItem -LiteralPath $PSScriptRoot -Recurse -File -Force |
    Where-Object {
        if ($_.FullName -eq $manifest) { return $false }
        $relative = [IO.Path]::GetRelativePath($PSScriptRoot, $_.FullName).Replace('\','/')
        return (
            (-not $relative.StartsWith('.git/', [StringComparison]::OrdinalIgnoreCase)) -and
            (-not $relative.StartsWith('.test-', [StringComparison]::OrdinalIgnoreCase)) -and
            (-not $relative.StartsWith('test-results/', [StringComparison]::OrdinalIgnoreCase)) -and
            (-not $relative.StartsWith('output/', [StringComparison]::OrdinalIgnoreCase)) -and
            (-not $relative.StartsWith('validation/', [StringComparison]::OrdinalIgnoreCase)) -and
            (-not $relative.Contains('/__pycache__/')) -and
            (-not $relative.Contains('/.pytest_cache/'))
        )
    })
if ($seen.Count -ne $expectedFiles.Count) {
    throw "Manifest count mismatch: manifest=$($seen.Count), files=$($expectedFiles.Count)"
}
foreach ($file in $expectedFiles) {
    $relative = [IO.Path]::GetRelativePath($PSScriptRoot, $file.FullName).Replace('\','/')
    if (-not $seen.ContainsKey($relative)) { throw "Manifest entry missing: $relative" }
}

if ($environmentSkips) {
    Write-Host "WoWS Toolbox 5.0.61 self-tests passed with $environmentSkips environmental skip(s)."
}
else {
    Write-Host 'WoWS Toolbox 5.0.61 self-tests passed.'
}

