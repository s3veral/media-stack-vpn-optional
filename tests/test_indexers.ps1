#Requires -Version 5.1
$ErrorActionPreference = 'Stop'
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'add-indexers.ps1')
function Assert-Indexer($Condition, [string]$Message) { if (-not $Condition) { throw $Message } }

# Test the real HTTP wrapper with synthetic responses before replacing it.
function Invoke-WebRequest {
    param($Uri, $Method, $Headers, $UseBasicParsing, $TimeoutSec, $MaximumRedirection, $ContentType, $Body)
    if ($script:BadResponse) { throw 'private-error-detail-must-not-leak' }
    Assert-Indexer ($Uri -like 'http://127.0.0.1:9696/api/v1/*') 'API key destination changed.'
    Assert-Indexer ($MaximumRedirection -eq 0) 'API redirects must be disabled.'
    return [pscustomobject]@{Content='[{"name":"first"},{"name":"second"}]'}
}
$script:BadResponse = $false
$response = @(Invoke-Prowlarr 'unit-only-key' 'indexer/schema')
Assert-Indexer ($response.Count -eq 2 -and $response[1].name -eq 'second') 'JSON arrays must flatten on PowerShell 5.1.'
$script:BadResponse = $true
$failure = ''
try { Invoke-Prowlarr 'unit-only-key' 'indexer' }
catch { $failure = $_.Exception.Message }
Assert-Indexer ($failure -like 'Prowlarr indexer:*' -and $failure -notmatch 'private-error-detail') 'Raw HTTP error leaked or no failure was raised.'
Remove-Item Function:Invoke-WebRequest

function New-TestSource([string]$Definition, [string]$Privacy = 'public', [bool]$Cloudflare = $false) {
    $fields = @([pscustomobject]@{name='definitionFile';value=$Definition;type='textbox';privacy='normal'},
        [pscustomobject]@{name='baseUrl';type='select';privacy='normal'})
    if ($Cloudflare) { $fields += [pscustomobject]@{name='info_flaresolverr';type='info';privacy='normal'} }
    return [pscustomobject]@{name=$Definition;implementation='Cardigann';protocol='torrent';privacy=$Privacy;
        fields=$fields;indexerUrls=@('https://example.invalid/','https://mirror.invalid/');appProfileId=0;enable=$true;
        tags=@();capabilities=[pscustomobject]@{categories=@([pscustomobject]@{id=2000;name='Movies'})}}
}
$original = New-TestSource 'public-source' 'public' $true
$prepared = New-PublicIndexer $original 1 @(12)
Assert-Indexer (($prepared.fields | Where-Object name -eq 'baseUrl').value -eq 'https://example.invalid/') 'Use primary schema URL only.'
Assert-Indexer ($null -eq $original.fields[1].PSObject.Properties['value']) 'Input schema was mutated.'
Assert-Indexer ($prepared.appProfileId -eq 1 -and $prepared.tags[0] -eq 12) 'Sync profile / existing proxy tag missing.'
try { New-PublicIndexer (New-TestSource 'private-source' 'private') 1; throw 'Private source accepted.' }
catch { Assert-Indexer ($_.Exception.Message -like 'Fonte privada*') 'Private sources must not be automated.' }
$credentialSource = New-TestSource 'requires-cookie'
$credentialSource.fields += [pscustomobject]@{name='cookie';type='textbox';privacy='normal'}
try { New-PublicIndexer $credentialSource 1; throw 'Cookie source accepted.' }
catch { Assert-Indexer ($_.Exception.Message -like 'A definicao pede credenciais*') 'Credential source must be skipped.' }

function Get-LocalProwlarrKey([string]$Directory) { return 'unit-only-key' }
function Invoke-Prowlarr([string]$Key, [string]$Path, [string]$Method = 'GET', $Body = $null) {
    $script:Calls.Add([pscustomobject]@{Path=$Path;Method=$Method;Body=$Body})
    if ($Method -eq 'GET') {
        switch ($Path) {
            'indexer/schema' { return $script:Schemas }
            'indexer' { return @($script:Existing) + $script:Saved.ToArray() }
            'appprofile' { return [pscustomobject]@{id=1;enableRss=$true;enableAutomaticSearch=$true;enableInteractiveSearch=$true} }
            'applications' { return [pscustomobject]@{name='Radarr';implementation='Radarr'} }
            'indexerproxy' { return [pscustomobject]@{implementation='FlareSolverr';tags=@(12)} }
        }
    }
    if ($Path -eq 'indexer/test') {
        if ($Body.name -eq 'unreachable') { throw 'Prowlarr indexer/test: HTTP 400; bloqueio do site/Cloudflare.' }
        return [pscustomobject]@{}
    }
    if ($Path -eq 'indexer' -and $Method -eq 'POST') {
        $Body | Add-Member NoteProperty id (100 + $script:Saved.Count) -Force
        $script:Saved.Add($Body)
        return $Body
    }
    if ($Path -eq 'command') { return [pscustomobject]@{id=55} }
    throw 'Unexpected fake API call.'
}
function Reset-IndexerFixture {
    $script:Calls = New-Object 'Collections.Generic.List[object]'
    $script:Saved = New-Object 'Collections.Generic.List[object]'
    $script:Existing = @()
    $script:Schemas = @(New-TestSource 'public-source'; New-TestSource 'unreachable'; New-TestSource 'private-source' 'private')
}
Reset-IndexerFixture
$report = @(Invoke-IndexerSetup -Directory 'unused' -Names @('public-source','unreachable','private-source'))
Assert-Indexer ($script:Saved.Count -eq 0) 'Preview saved a source.'
Assert-Indexer (@($script:Calls | Where-Object Method -ne 'GET').Count -eq 0) 'Preview made a mutating/test request.'
Assert-Indexer ($report.Count -eq 3) 'Preview omitted selected sources.'

Reset-IndexerFixture
$report = @(Invoke-IndexerSetup -Directory 'unused' -Names @('public-source','public-source','unreachable','private-source') -Save)
Assert-Indexer ($script:Saved.Count -eq 1) 'Only one tested public source should be saved.'
Assert-Indexer (@($script:Calls | Where-Object Path -eq 'indexer/test').Count -eq 2) 'Private/duplicate sources must not be tested.'
Assert-Indexer (@($script:Calls | Where-Object { $_.Path -match 'forceSave' }).Count -eq 0) 'Failed sources must never use forceSave.'
Assert-Indexer (@($script:Calls | Where-Object Path -eq 'command').Count -eq 1) 'Missing app synchronization.'
Assert-Indexer ($report[1].Estado -like 'PENDENTE:*') 'Failure must remain visible.'

$script:Calls.Clear()
$report = @(Invoke-IndexerSetup -Directory 'unused' -Names @('public-source') -Save)
Assert-Indexer ($script:Saved.Count -eq 1) 'Rerun created a duplicate.'
Assert-Indexer (@($script:Calls | Where-Object Method -ne 'GET').Count -eq 0) 'Rerun changed an existing indexer.'
Assert-Indexer ($report[0].Estado -like 'EXISTENTE:*') 'Existing source not reported.'
Write-Host 'PASS: PowerShell 5.1 arrays, secret-safe failures, primary URL, public-only, preview, test-before-save, duplicates and sync.'
