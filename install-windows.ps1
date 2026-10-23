#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$InstallDir = "$env:USERPROFILE\MediaStack",
    [string]$MediaPath,
    [ValidateSet('Ask', 'On', 'Off')][string]$Vpn = 'Ask',
    [string]$WireGuardConfig,
    [string]$LanNetwork,
    [string]$TimeZone = 'America/Sao_Paulo',
    [ValidateSet('Ask', 'On', 'Off')][string]$Remote = 'Ask',
    [string]$PublicHostname,
    [ValidateSet('Ask', 'On', 'Off')][string]$Archive = 'Ask',
    [string]$DriveConfig,
    [switch]$GenerateOnly,
    [switch]$SkipConfigure
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Write-Utf8([string]$Path, [string]$Content) {
    [IO.File]::WriteAllText($Path, $Content.Replace("`r`n", "`n"), (New-Object Text.UTF8Encoding($false)))
}
function Quote-DotEnv([string]$Value) {
    if ($Value -match "[`r`n]") { throw 'Settings must not contain line breaks.' }
    return "'" + $Value.Replace("'", "\'") + "'"
}
function Invoke-Compose {
    & docker compose --project-directory $script:InstallDir --env-file "$script:InstallDir\.env" -f "$script:InstallDir\compose.windows.yml" -f "$script:InstallDir\$script:Override" @args
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose failed ($LASTEXITCODE). See the message above." }
}

$InstallDir = [IO.Path]::GetFullPath($InstallDir)
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$settingsPath = Join-Path $InstallDir '.settings.json'
$saved = $null
if (Test-Path -LiteralPath $settingsPath) {
    $saved = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json
}
if (-not $MediaPath) {
    $defaultMedia = if ($saved) { $saved.MediaPath } else { Join-Path $InstallDir 'media' }
    $answer = Read-Host "Pasta da biblioteca (Enter: $defaultMedia)"
    $MediaPath = if ($answer) { $answer } else { $defaultMedia }
}
$MediaPath = [IO.Path]::GetFullPath($MediaPath)
if ($MediaPath.StartsWith('\\') -or $MediaPath -match '^[A-Za-z]:\\?$') {
    throw 'Escolha uma pasta local, por exemplo D:\Media; evite a raiz do disco e caminhos de rede.'
}
$vpnEnabled = if ($saved) { [bool]$saved.VpnEnabled } else { $false }
if ($Vpn -eq 'Ask') {
    Write-Host 'Torrent funciona sem VPN. Sem VPN, os peers veem o IP publico da sua conexao.'
    $defaultChoice = if ($vpnEnabled) { 's' } else { 'n' }
    do {
        $answer = Read-Host "Usar VPN WireGuard? [s/n] (Enter: $defaultChoice)"
        if (-not $answer) { $answer = $defaultChoice }
    } while ($answer -notmatch '^(s|sim|y|yes|n|nao|no)$')
    $vpnEnabled = $answer -match '^(s|sim|y|yes)$'
} else { $vpnEnabled = $Vpn -eq 'On' }

New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir 'wireguard') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir '.secrets\runtime') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir '.secrets\archive') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir '.secrets\rclone') | Out-Null
$proxyKeyPath = Join-Path $InstallDir '.secrets\archive\proxy-key'
if (-not (Test-Path -LiteralPath $proxyKeyPath)) { Write-Utf8 $proxyKeyPath ([Guid]::NewGuid().ToString('N') + [Guid]::NewGuid().ToString('N')) }
$wgTarget = Join-Path $InstallDir 'wireguard\wg0.conf'
if ($vpnEnabled) {
    if (-not $WireGuardConfig -and -not (Test-Path -LiteralPath $wgTarget)) {
        $WireGuardConfig = Read-Host 'Caminho do perfil CLIENTE WireGuard (.conf)'
    }
    if ($WireGuardConfig) {
        $WireGuardConfig = (Resolve-Path -LiteralPath $WireGuardConfig).Path
        if ($WireGuardConfig -ne $wgTarget) { Copy-Item -LiteralPath $WireGuardConfig -Destination $wgTarget }
    }
    if (-not (Test-Path -LiteralPath $wgTarget)) { throw 'VPN selecionada: forneca um perfil WireGuard valido, ou use -Vpn Off.' }
    $profile = Get-Content -LiteralPath $wgTarget -Raw
    foreach ($field in @('\[Interface\]', '\[Peer\]', '(?m)^\s*PrivateKey\s*=', '(?m)^\s*Endpoint\s*=', '(?m)^\s*DNS\s*=')) {
        if ($profile -notmatch $field) { throw 'Perfil incompleto. Necessarios: Interface, Peer, PrivateKey, Endpoint e DNS.' }
    }
    if (-not $LanNetwork) {
        $defaultLan = if ($saved) { $saved.LanNetwork } else { '192.168.1.0/24' }
        $answer = Read-Host "Sub-rede LAN para acesso ao WebUI pela rede Docker (Enter: $defaultLan)"
        $LanNetwork = if ($answer) { $answer } else { $defaultLan }
    }
} elseif (-not $LanNetwork) { $LanNetwork = if ($saved) { $saved.LanNetwork } else { '192.168.1.0/24' } }
# Docker bridge subnets are included automatically by hotio. Keep the explicit LAN narrow.
if ($LanNetwork -notmatch '^\d{1,3}(\.\d{1,3}){3}/\d{1,2}$' -or $LanNetwork -match '/0$') {
    throw 'Use um CIDR IPv4 de LAN, por exemplo 192.168.1.0/24. Nao use 0.0.0.0/0.'
}

New-Item -ItemType Directory -Force -Path $MediaPath | Out-Null
foreach ($dir in @('movies', 'tv', 'photos', 'music', 'downloads', 'downloads\torrent', 'downloads\crossseed')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $MediaPath $dir) | Out-Null
}
$assets = @('compose.windows.yml', 'compose.direct.yml', 'compose.vpn.yml', 'truenas-file-structure.sh', 'install-windows.ps1', 'connect-drive.ps1', 'stack.ps1', '.dockerignore', 'windows\Dockerfile.setup', 'windows\build_configure.py', 'windows\entrypoint.sh', 'portal\Dockerfile', 'portal\Dockerfile.archive', 'portal\Dockerfile.telegram', 'portal\app.py', 'portal\archive.py', 'portal\archive_client.py', 'portal\archive_setup.py', 'portal\cloudflare_setup.py', 'portal\static\index.html', 'portal\static\style.css', 'portal\static\app.js', 'portal\static\cloud.js', 'portal\static\cloud.css')
foreach ($asset in $assets) {
    $source = Join-Path $PSScriptRoot $asset
    $destination = Join-Path $InstallDir $asset
    if (-not (Test-Path -LiteralPath $source)) { throw "Arquivo de instalacao ausente: $asset. Execute bootstrap-windows.ps1." }
    if ([IO.Path]::GetFullPath($source) -ne [IO.Path]::GetFullPath($destination)) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
        Copy-Item -LiteralPath $source -Destination $destination -Force
    }
}
$adminPass = if ($saved) { $saved.AdminPass } else { [Guid]::NewGuid().ToString('N').Substring(0, 24) }
$remoteEnabled = $false
if ($saved -and $saved.PSObject.Properties.Name -contains 'RemoteEnabled') { $remoteEnabled = [bool]$saved.RemoteEnabled }
if ($Remote -eq 'Ask') {
    $defaultRemote = if ($remoteEnabled) { 's' } else { 'n' }
    do {
        $answer = Read-Host "Publicar o painel com Cloudflare Tunnel? [s/n] (Enter: $defaultRemote)"
        if (-not $answer) { $answer = $defaultRemote }
    } while ($answer -notmatch '^(s|sim|y|yes|n|nao|no)$')
    $remoteEnabled = $answer -match '^(s|sim|y|yes)$'
} else { $remoteEnabled = $Remote -eq 'On' }
if (-not $PublicHostname -and $saved -and $saved.PSObject.Properties.Name -contains 'PublicHostname') { $PublicHostname = $saved.PublicHostname }
if ($remoteEnabled -and $PublicHostname) {
    $PublicHostname = $PublicHostname.Trim().ToLowerInvariant()
    if ($PublicHostname -notmatch '^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$') { throw 'Informe um subdominio completo, sem https:// ou caminhos.' }
}
$archiveEnabled = $false
if ($saved -and $saved.PSObject.Properties.Name -contains 'ArchiveEnabled') { $archiveEnabled = [bool]$saved.ArchiveEnabled }
if ($Archive -eq 'Ask' -and -not $GenerateOnly) {
    $defaultArchive = if ($archiveEnabled) { 's' } else { 'n' }
    do {
        $answer = Read-Host "Ativar arquivo Drive + Telegram (Drive para tudo, Telegram ate 2.000 MB)? [s/n] (Enter: $defaultArchive)"
        if (-not $answer) { $answer = $defaultArchive }
    } while ($answer -notmatch '^(s|sim|y|yes|n|nao|no)$')
    $archiveEnabled = $answer -match '^(s|sim|y|yes)$'
} elseif ($Archive -ne 'Ask') { $archiveEnabled = $Archive -eq 'On' }
$settings = [ordered]@{ MediaPath = $MediaPath; VpnEnabled = $vpnEnabled; LanNetwork = $LanNetwork; AdminPass = $adminPass; TimeZone = $TimeZone; RemoteEnabled = $remoteEnabled; PublicHostname = $PublicHostname; ArchiveEnabled = $archiveEnabled }
if (Test-Path -LiteralPath $settingsPath) { Copy-Item -LiteralPath $settingsPath -Destination "$settingsPath.bak" -Force }
Write-Utf8 $settingsPath ($settings | ConvertTo-Json)
$envValues = [ordered]@{
    MEDIA_PATH = $MediaPath.Replace('\', '/')
    VPN_ENABLED = $vpnEnabled.ToString().ToLowerInvariant()
    VPN_NAMESERVERS = $(if ($vpnEnabled) { 'wg' } else { '' })
    QBIT_NETWORK_INTERFACE = $(if ($vpnEnabled) { 'wg0' } else { '' })
    VPN_LAN_NETWORK = $LanNetwork
    TZ = $TimeZone
    ADMIN_USER = 'admin'
    ADMIN_PASS = $adminPass
    ADMIN_EMAIL = 'admin@localhost.local'
    PUBLIC_HOSTNAME = $PublicHostname
    ARCHIVE_ENABLED = $archiveEnabled.ToString().ToLowerInvariant()
}
$lines = foreach ($key in $envValues.Keys) { "$key=$(Quote-DotEnv $envValues[$key])" }
Write-Utf8 (Join-Path $InstallDir '.env') (($lines -join "`n") + "`n")
$Override = if ($vpnEnabled) { 'compose.vpn.yml' } else { 'compose.direct.yml' }
Write-Host "Arquivos preparados em $InstallDir; biblioteca: $MediaPath; VPN: $vpnEnabled"
if ($GenerateOnly) {
    Write-Host 'GenerateOnly: nenhum container foi iniciado.'
    return
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Instale e abra Docker Desktop com backend WSL 2 e Linux containers. Depois execute novamente install-windows.ps1.'
}
$dockerOs = & docker info --format '{{.OSType}}' 2>$null
if ($LASTEXITCODE -ne 0 -or $dockerOs -ne 'linux') { throw 'Abra Docker Desktop e selecione Linux containers antes de continuar.' }
& docker compose version
if ($LASTEXITCODE -ne 0) { throw 'Docker Compose v2 e necessario (incluido no Docker Desktop).' }
# Upstream uses fixed container names. Do not take over another stack's containers.
foreach ($name in @('qbittorrent', 'radarr', 'sonarr', 'prowlarr', 'bazarr', 'jellyfin', 'seerr', 'profilarr', 'flaresolverr', 'qui', 'dozzle')) {
    $existing = & docker ps -a --filter "name=^/$name`$" --format '{{.Label "com.docker.compose.project"}}'
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao verificar containers existentes.' }
    if ($existing -and $existing -ne 'media-stack') { throw "Ja existe um container $name fora deste projeto. Resolva o conflito antes de instalar." }
    if ($existing -eq 'media-stack') {
        $oldDir = & docker inspect --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' $name
        if ($oldDir -and $oldDir -ne $InstallDir) { throw "Projeto media-stack existente em $oldDir. Use o mesmo -InstallDir." }
    }
}
Invoke-Compose config --quiet
# Stop only this project's torrent client before copying a changed profile.
Invoke-Compose stop qbittorrent
Invoke-Compose --profile setup build setup
Invoke-Compose --profile setup run --rm --no-deps setup prepare
Invoke-Compose up -d --build
if (-not $SkipConfigure) {
    try { Invoke-Compose --profile setup run --rm --no-deps setup configure }
    catch { Write-Warning 'Algumas integracoes precisam de ajuste (veja as mensagens acima). Os containers seguem ativos; conclua o setup nas interfaces ou reexecute o instalador.' }
}
if ($remoteEnabled) {
    Write-Host 'Cloudflare: abra o link que aparecera abaixo e autorize o dominio na sua conta.'
    Write-Host 'Depois informe o endereco completo do painel, por exemplo sala.seudominio.com.'
    Invoke-Compose --profile setup run --rm --no-deps setup provision
    $tunnelInfo = Get-Content -LiteralPath (Join-Path $InstallDir '.secrets\tunnel.json') -Raw | ConvertFrom-Json
    $PublicHostname = $tunnelInfo.hostname
    $settings.PublicHostname = $PublicHostname
    $envValues.PUBLIC_HOSTNAME = $PublicHostname
    Write-Utf8 $settingsPath ($settings | ConvertTo-Json)
    $lines = foreach ($key in $envValues.Keys) { "$key=$(Quote-DotEnv $envValues[$key])" }
    Write-Utf8 (Join-Path $InstallDir '.env') (($lines -join "`n") + "`n")
    # Apply the hostname chosen after browser authorization to portal CSRF checks.
    Invoke-Compose up -d dashboard
}
if ($remoteEnabled) { Invoke-Compose --profile tunnel up -d cloudflared }
else { Invoke-Compose --profile tunnel stop cloudflared }
if ($archiveEnabled) {
    $rcloneConfig = Join-Path $InstallDir '.secrets\rclone\rclone.conf'
    if ($DriveConfig) {
        $DriveConfig = (Resolve-Path -LiteralPath $DriveConfig).Path
        if ($DriveConfig -ne $rcloneConfig) { Copy-Item -LiteralPath $DriveConfig -Destination $rcloneConfig -Force }
    }
    if (-not (Test-Path -LiteralPath $rcloneConfig)) { & (Join-Path $InstallDir 'connect-drive.ps1') -ConfigPath $rcloneConfig }
    Invoke-Compose --profile setup run --rm --no-deps setup archive-configure
    Write-Host 'Compilando o Bot API local a partir do codigo oficial Telegram; a primeira vez pode levar varios minutos.'
    Invoke-Compose --profile archive up -d --build archive telegram-bot-api
} else { Invoke-Compose --profile archive stop archive telegram-bot-api }
Write-Host ''
Write-Host 'Stack iniciada. Jellyfin: http://localhost:8096 | Seerr: http://localhost:5055'
Write-Host 'Homepage Sala: http://localhost:8090'
if ($archiveEnabled) { Write-Host 'Arquivo em nuvem: aba Nuvem da homepage. Copias incrementais; originais preservados.' }
if ($remoteEnabled) { Write-Host "Acesso externo: https://$PublicHostname" }
Write-Host 'qBittorrent: http://localhost:8080 | Radarr: http://localhost:7878 | Sonarr: http://localhost:8989'
Write-Host "Login da homepage: admin / $adminPass (tambem salvo em $settingsPath)."
Write-Host 'Finalize os indexadores no Prowlarr e idiomas/provedores no Bazarr.'
Write-Host "Gerenciar: cd `"$InstallDir`"; .\stack.ps1 ps"
