#requires -Version 5.1
param([string]$Root=(Split-Path $PSScriptRoot -Parent))
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $Root 'GUI/WoWSToolboxGUI.ps1'),[ref]$tokens,[ref]$errors)
if($errors.Count){throw ($errors|Out-String)}
foreach($name in @('ConvertFrom-ToolboxJsonArray','Load-CatalogFile','Test-AllowCatalogAutoRefresh')){
    $definition=$ast.Find({param($node)$node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name},$true)
    Invoke-Expression $definition.Extent.Text
}
function Add-Log {param($Text)}
function Get-UiText {param($Korean,$English) return $English}
function Get-SourceDisplay {param($Source) return $Source}
function Get-CatalogPath {param($Source) return $script:TestCatalogPath}
$script:Catalogs=@{};$script:CatalogDiscoveredCounts=@{};$script:CatalogAutoRefreshAttempts=@{}
$script:TestCatalogPath=Join-Path ([IO.Path]::GetTempPath()) ('catalog-compat-'+[guid]::NewGuid()+'.json')
try {
    foreach($size in @(0,1,1230)){
        $rows=@(for($i=0;$i -lt $size;$i++){[pscustomobject]@{ShipCode="PTEST$i";Supported=$true;CamouflageCatalogVersion=2}})
        $json=ConvertTo-Json -InputObject $rows -Depth 5 -Compress
        [IO.File]::WriteAllText($script:TestCatalogPath,$json)
        Load-CatalogFile 'pc' $script:TestCatalogPath
        if($script:Catalogs.pc.Count -ne $size){throw "Loaded $($script:Catalogs.pc.Count) instead of $size"}
        if($size -gt 0 -and $script:Catalogs.pc[0] -is [array]){throw 'Nested catalog row'}
    }
    [IO.File]::WriteAllText($script:TestCatalogPath,'[{"ShipCode":"A","Supported":false},{"ShipCode":"B","Supported":true}]')
    Load-CatalogFile 'blitz' $script:TestCatalogPath
    if($script:Catalogs.blitz.Count -ne 1 -or $script:Catalogs.blitz[0].ShipCode -ne 'B' -or $script:CatalogDiscoveredCounts.blitz -ne 2){throw 'Supported filtering failed'}
    foreach($invalid in @('{"ShipCode":"A"}','[null]','[[{"ShipCode":"A"}]]','[{"Name":"bad"}]')){
        [IO.File]::WriteAllText($script:TestCatalogPath,$invalid)
        $rejected=$false
        try{Load-CatalogFile 'pc' $script:TestCatalogPath}catch{$rejected=$true}
        if(-not $rejected){throw "Accepted invalid catalog: $invalid"}
    }
    if(-not (Test-AllowCatalogAutoRefresh 'pc' 'camouflages')){throw 'Initial refresh blocked'}
    if(Test-AllowCatalogAutoRefresh 'pc' 'camouflages'){throw 'Repeated automatic refresh allowed'}
    if(-not (Test-AllowCatalogAutoRefresh 'korabli' 'materials')){throw 'Independent refresh blocked'}
    Write-Host "Catalog compatibility passed on PowerShell $($PSVersionTable.PSVersion)"
}finally{if(Test-Path -LiteralPath $script:TestCatalogPath){Remove-Item -LiteralPath $script:TestCatalogPath}}
