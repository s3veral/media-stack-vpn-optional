#Requires -Version 5.1
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$DockerArgs = @('ps'))
$ErrorActionPreference = 'Stop'
$settingsPath = Join-Path $PSScriptRoot '.settings.json'
if (-not (Test-Path -LiteralPath $settingsPath)) { throw 'Execute install-windows.ps1 primeiro.' }
$settings = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json
$override = if ($settings.VpnEnabled) { 'compose.vpn.yml' } else { 'compose.direct.yml' }
$profiles = @()
if ($settings.PSObject.Properties.Name -contains 'RemoteEnabled' -and $settings.RemoteEnabled) { $profiles = @('--profile', 'tunnel') }
& docker compose --project-directory $PSScriptRoot --env-file "$PSScriptRoot\.env" -f "$PSScriptRoot\compose.windows.yml" -f "$PSScriptRoot\$override" @profiles @DockerArgs
exit $LASTEXITCODE
