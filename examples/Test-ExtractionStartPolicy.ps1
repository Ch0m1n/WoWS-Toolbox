#requires -Version 5.1
param([string]$Root=(Split-Path $PSScriptRoot -Parent))
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
. (Join-Path $Root 'GUI/Workbench.ps1')
function Get-ExtractionPlan {return $script:TestPlan}
function Write-JsonAtomic {param($Path,$Value,$Depth) $script:SavedManifest=$Value}
function Show-ExtractionPlan {param($Manifest) $script:ModalCount++;return 'confirmed'}
$script:BatchManifestPath='unused';$script:ModalCount=0
foreach($action in @('new','skip_verified','replace')){
    $script:TestPlan=[pscustomobject]@{ok=$true;fingerprint='approved';items=@([pscustomobject]@{action=$action;blockers=@()})}
    $manifest=@{common=@{}}
    $before=$script:ModalCount
    $approved=Approve-ExtractionPlan $manifest
    if($action -eq 'replace'){
        if($approved -ne 'confirmed' -or $script:ModalCount -ne ($before+1)){throw 'Replacement must be confirmed'}
    }elseif($approved -ne 'approved' -or $script:ModalCount -ne $before -or $script:SavedManifest.common.approved_plan -ne 'approved'){
        throw 'Ordinary extraction should retain plan validation without a modal'
    }
}
$script:TestPlan=[pscustomobject]@{ok=$false;items=@([pscustomobject]@{blockers=@('insufficient space')})}
$rejected=$false
try{Approve-ExtractionPlan @{common=@{}}}catch{$rejected=$_.Exception.Message -match 'insufficient space'}
if(-not $rejected){throw 'Blocked extraction was allowed'}
Write-Host "Extraction start policy passed on PowerShell $($PSVersionTable.PSVersion)"
