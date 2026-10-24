#Requires -Version 5.1
param([switch]$UseDocker)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = Split-Path -Parent $PSScriptRoot
$parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $root 'install-windows.ps1'), [ref]$null, [ref]$parseErrors)
if ($parseErrors) { throw ($parseErrors | Out-String) }
$ownershipFunction = $ast.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Assert-StackContainerOwnership' }, $true)
if (-not $ownershipFunction) { throw 'Installer ownership check is missing.' }
# Load only the real read-only function, never execute the installer/container setup.
. ([ScriptBlock]::Create($ownershipFunction.Extent.Text))
$projectDir = Join-Path ([IO.Path]::GetTempPath()) 'Media Stack with spaces'
if ($UseDocker) {
    Assert-StackContainerOwnership $projectDir
    Write-Host 'PASS: native Docker ownership preflight (read-only).'
    return
}

$fixtureState = [pscustomobject]@{ Scenario = ''; Calls = @(); Inspects = 0 }
$containerId = 'a' * 64
function docker {
    $fixtureState.Calls += ,@($args)
    $script:LASTEXITCODE = 0
    if ($args[0] -eq 'ps') {
        if ($args.Count -ne 6 -or $args[1] -ne '--all' -or $args[2] -ne '--quiet' -or $args[3] -ne '--no-trunc' -or $args[4] -ne '--filter' -or $args[5] -notmatch '^name=\^/[a-z]+\$$') { throw 'Unexpected Docker ps arguments.' }
        if ($fixtureState.Scenario -eq 'ps-failure') { $script:LASTEXITCODE = 1; return }
        if ($fixtureState.Scenario -eq 'empty' -or $args[5] -ne 'name=^/qbittorrent$') { return }
        if ($fixtureState.Scenario -eq 'bad-id') { return 'not-a-container-id' }
        if ($fixtureState.Scenario -eq 'multiple-ids') { return @($containerId, ('b' * 64)) }
        return $containerId
    }
    if ($args[0] -ne 'inspect' -or $args.Count -ne 4 -or $args[1] -ne '--type' -or $args[2] -ne 'container' -or $args[3] -ne $containerId) { throw 'Unexpected Docker mutation or inspect arguments.' }
    $fixtureState.Inspects++
    if ($fixtureState.Scenario -eq 'inspect-failure') { $script:LASTEXITCODE = 1; return }
    if ($fixtureState.Scenario -eq 'invalid-json') { return 'not JSON' }
    if ($fixtureState.Scenario -eq 'empty-inspect') { return '[]' }
    $labels = @{
        'com.docker.compose.project' = 'media-stack'
        'com.docker.compose.project.working_dir' = $projectDir
    }
    switch ($fixtureState.Scenario) {
        'foreign' { $labels['com.docker.compose.project'] = 'another-project' }
        'unlabeled' { $labels = $null }
        'empty-labels' { $labels = @{} }
        'missing-project' { $labels.Remove('com.docker.compose.project') }
        'missing-directory' { $labels.Remove('com.docker.compose.project.working_dir') }
        'empty-directory' { $labels['com.docker.compose.project.working_dir'] = '' }
        'other-directory' { $labels['com.docker.compose.project.working_dir'] = $projectDir + '-other' }
    }
    $record = @{ Id = $containerId; Name = '/qbittorrent'; Config = @{ Labels = $labels } }
    if ($fixtureState.Scenario -eq 'wrong-id') { $record.Id = 'b' * 64 }
    if ($fixtureState.Scenario -eq 'wrong-name') { $record.Name = '/radarr' }
    if ($fixtureState.Scenario -eq 'multiple-records') { return (ConvertTo-Json -InputObject @($record, $record) -Depth 5) }
    return (ConvertTo-Json -InputObject @($record) -Depth 5)
}

foreach ($scenario in @('empty', 'owned', 'foreign', 'unlabeled', 'empty-labels', 'missing-project', 'missing-directory', 'empty-directory', 'other-directory', 'ps-failure', 'inspect-failure', 'invalid-json', 'empty-inspect', 'bad-id', 'multiple-ids', 'wrong-id', 'wrong-name', 'multiple-records')) {
    $fixtureState.Scenario = $scenario
    $fixtureState.Calls = @()
    $fixtureState.Inspects = 0
    $failure = $null
    try { Assert-StackContainerOwnership $projectDir } catch { $failure = $_ }
    if ($scenario -in @('empty', 'owned')) {
        if ($failure) { throw $failure }
        if ($fixtureState.Calls.Count -ne (11 + $fixtureState.Inspects)) { throw "Not all fixed container names were checked: $scenario" }
        if ($fixtureState.Inspects -ne [int]($scenario -eq 'owned')) { throw "Unexpected inspect count: $scenario" }
    } else {
        if (-not $failure) { throw "Unsafe container check accepted: $scenario" }
        if ($fixtureState.Calls.Count -gt 2) { throw "Container check did not stop at the first failure: $scenario" }
    }
    if ($scenario -in @('foreign', 'unlabeled', 'empty-labels', 'missing-project') -and $failure.Exception.Message -notlike '*fora deste projeto*') { throw "Ownership error was not reported: $scenario" }
    if ($scenario -in @('missing-directory', 'empty-directory') -and $failure.Exception.Message -notlike '*nao informa a pasta*') { throw "Missing directory was not reported: $scenario" }
    if ($scenario -eq 'other-directory' -and $failure.Exception.Message -notlike '*Use o mesmo -InstallDir*') { throw 'Directory conflict was not reported.' }
}
Write-Host 'PASS: Docker JSON ownership checks, empty/owned installs and fail-closed conflicts/errors (18 cases).'
