#Requires -Version 5.1
[CmdletBinding()]
param([string]$ConfigPath = "$PSScriptRoot\.secrets\rclone\rclone.conf")
$ErrorActionPreference = 'Stop'
$ConfigPath = [IO.Path]::GetFullPath($ConfigPath)
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ConfigPath) | Out-Null
if (Test-Path -LiteralPath $ConfigPath) {
    $answer = Read-Host 'rclone.conf ja existe. Refazer a autorizacao? [s/N]'
    if ($answer -notmatch '^(s|sim|y|yes)$') { Write-Host "Configuracao preservada: $ConfigPath"; return }
}
$binary = Join-Path $PSScriptRoot 'tools\rclone.exe'
if (-not (Test-Path -LiteralPath $binary)) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $versionResponse = Invoke-WebRequest -UseBasicParsing 'https://downloads.rclone.org/version.txt' -TimeoutSec 30
    $versionText = if ($versionResponse.Content -is [byte[]]) { [Text.Encoding]::UTF8.GetString($versionResponse.Content).Trim() } else { ([string]$versionResponse.Content).Trim() }
    if ($versionText -notmatch '^rclone v([0-9]+\.[0-9]+\.[0-9]+)$') { throw 'Versao rclone inesperada; baixe o rclone oficial manualmente.' }
    $version = $Matches[1]
    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'amd64' }
    $name = "rclone-v$version-windows-$arch.zip"
    $stage = Join-Path ([IO.Path]::GetTempPath()) ('sala-rclone-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $stage | Out-Null
    $zip = Join-Path $stage $name
    Write-Host 'Baixando rclone portatil do site oficial e verificando SHA256...'
    Invoke-WebRequest -UseBasicParsing "https://downloads.rclone.org/v$version/$name" -OutFile $zip -TimeoutSec 300
    $sumResponse = Invoke-WebRequest -UseBasicParsing "https://downloads.rclone.org/v$version/SHA256SUMS" -TimeoutSec 30
    $checksums = if ($sumResponse.Content -is [byte[]]) { [Text.Encoding]::UTF8.GetString($sumResponse.Content) } else { [string]$sumResponse.Content }
    $match = [regex]::Match($checksums, '(?m)^([a-fA-F0-9]{64})\s+\*?' + [regex]::Escape($name) + '\s*$')
    if (-not $match.Success -or (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash -ne $match.Groups[1].Value) { throw 'SHA256 do rclone nao confere. Download nao sera executado.' }
    Expand-Archive -LiteralPath $zip -DestinationPath $stage
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $binary) | Out-Null
    Copy-Item -LiteralPath (Join-Path $stage "rclone-v$version-windows-$arch\rclone.exe") -Destination $binary
}
if (-not (Test-Path -LiteralPath $ConfigPath)) {
    Write-Host 'Autorize o Drive no navegador. Use um aplicativo OAuth proprio, do tipo Desktop app.'
    Write-Host 'Guia: https://rclone.org/drive/#making-your-own-client-id'
    Write-Host 'Console: https://console.cloud.google.com/apis/credentials'
    Write-Host 'Escolha Aplicativo para computador, nao Aplicativo da Web. O retorno da autorizacao e local, nao pelo Cloudflare.'
    $clientId = Read-Host 'Google OAuth Client ID (termina em .apps.googleusercontent.com)'
    $secureSecret = Read-Host 'Google OAuth Client secret (oculto)' -AsSecureString
    $clientSecret = (New-Object Net.NetworkCredential('', $secureSecret)).Password
    if ($clientId -notmatch '^[A-Za-z0-9.-]+\.apps\.googleusercontent\.com$' -or $clientSecret -match "[`r`n]" -or -not $clientSecret) { throw 'Dados OAuth invalidos.' }
    $content = "[sala]`ntype = drive`nscope = drive.file`nclient_id = $clientId`nclient_secret = $clientSecret`n"
    [IO.File]::WriteAllText($ConfigPath, $content, (New-Object Text.UTF8Encoding($false)))
    $clientSecret = $null
    $content = $null
}
Write-Host 'No rclone: autorize pelo navegador; para conta pessoal, responda N a Shared Drive.'
Write-Host 'Se usar um cliente Web existente e aparecer redirect_uri_mismatch, autorize o URI local exato exibido pelo rclone no Google Cloud.'
& $binary --config $ConfigPath config reconnect 'sala:'
if ($LASTEXITCODE -ne 0) { throw 'Autorizacao do Drive nao concluida. O arquivo foi preservado para nova tentativa.' }
Write-Host "Drive autorizado. Arquivo privado: $ConfigPath"
Write-Host 'Para TrueNAS, transfira esse arquivo por um canal privado e informe o caminho no instalador.'
