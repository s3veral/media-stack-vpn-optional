#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$InstallDir = "$env:USERPROFILE\MediaStack",
    [string]$MediaPath,
    [ValidateSet('Ask', 'On', 'Off')][string]$Vpn = 'Ask',
    [string]$WireGuardConfig,
    [string]$LanNetwork,
    [string]$TimeZone = 'America/Sao_Paulo',
    [switch]$GenerateOnly,
    [switch]$SkipConfigure
)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$stage = Join-Path ([IO.Path]::GetTempPath()) ('media-stack-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stage | Out-Null
$archive = Join-Path $stage 'source.zip'
Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/s3veral/media-stack-vpn-optional/archive/refs/heads/main.zip' -OutFile $archive
Expand-Archive -LiteralPath $archive -DestinationPath $stage
$installer = Join-Path $stage 'media-stack-vpn-optional-main\install-windows.ps1'
if (-not (Test-Path -LiteralPath $installer)) { throw 'O download nao contem o instalador esperado.' }
& $installer @PSBoundParameters
