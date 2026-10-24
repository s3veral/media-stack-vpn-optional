$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('media-stack-test-' + [Guid]::NewGuid().ToString('N'))
$install = Join-Path $testRoot 'Install with spaces'
$media = Join-Path $testRoot 'Media with spaces'
& (Join-Path $root 'install-windows.ps1') -InstallDir $install -MediaPath $media -Vpn Off -Remote Off -GenerateOnly
$first = Get-Content (Join-Path $install '.settings.json') -Raw | ConvertFrom-Json
if ($first.VpnEnabled) { throw 'Fresh direct install unexpectedly enabled VPN.' }
if (-not (Test-Path (Join-Path $media 'downloads/torrent'))) { throw 'Missing media directories.' }
$firstEnv = Get-Content (Join-Path $install '.env') -Raw
if ($firstEnv -notmatch "QBIT_NETWORK_INTERFACE=''" -or $firstEnv -notmatch "VPN_NAMESERVERS=''" ) { throw 'Direct mode retains VPN binding/DNS.' }
$profile = Join-Path $testRoot 'example.conf'
$example = "[Interface]`nPrivateKey = PLACEHOLDER`nAddress = 10.66.66.2/32`nDNS = 1.1.1.1`n[Peer]`nPublicKey = PLACEHOLDER`nEndpoint = 192.0.2.1:51820`nAllowedIPs = 0.0.0.0/0`n"
[IO.File]::WriteAllText($profile, $example)
& (Join-Path $root 'install-windows.ps1') -InstallDir $install -MediaPath $media -Vpn On -Remote Off -WireGuardConfig $profile -LanNetwork '192.168.1.0/24' -GenerateOnly
$vpnEnv = Get-Content (Join-Path $install '.env') -Raw
if ($vpnEnv -notmatch "VPN_ENABLED='true'" -or $vpnEnv -notmatch "QBIT_NETWORK_INTERFACE='wg0'") { throw 'VPN selection did not update environment.' }
& (Join-Path $root 'install-windows.ps1') -InstallDir $install -MediaPath $media -Vpn Off -Remote Off -GenerateOnly
$last = Get-Content (Join-Path $install '.settings.json') -Raw | ConvertFrom-Json
if ($last.AdminPass -ne $first.AdminPass) { throw 'Rerun unexpectedly changed admin password.' }
if (-not (Test-Path (Join-Path $install 'wireguard/wg0.conf'))) { throw 'Disabling VPN deleted the existing profile.' }
if ($last.VpnEnabled) { throw 'Disabling VPN failed.' }
if (-not (Test-Path (Join-Path $install '.settings.json.bak'))) { throw 'Previous settings were not backed up.' }
Write-Host 'PASS: Windows direct/VPN/direct, spaces in paths, saved credentials and preserved profile.'
& (Join-Path $root 'install-windows.ps1') -InstallDir $install -MediaPath $media -Vpn Off -Remote On -GenerateOnly
$remoteSettings = Get-Content (Join-Path $install '.settings.json') -Raw | ConvertFrom-Json
if (-not $remoteSettings.RemoteEnabled) { throw 'Remote choice was not saved.' }
if (-not (Test-Path (Join-Path $install '.secrets/runtime'))) { throw 'Runtime secrets directory is missing.' }
if (Test-Path (Join-Path $install '.secrets/tunnel_token')) { throw 'Fresh browser-login install created a legacy token placeholder.' }
Write-Host 'PASS: remote install can be prepared without requesting an API token or hostname before login.'
& (Join-Path $root 'install-windows.ps1') -InstallDir $install -MediaPath $media -Vpn Off -Remote Off -Archive On -GenerateOnly
$archiveSettings = Get-Content (Join-Path $install '.settings.json') -Raw | ConvertFrom-Json
if (-not $archiveSettings.ArchiveEnabled) { throw 'Archive choice was not saved.' }
if (-not (Test-Path (Join-Path $install 'portal/static/cloud.js'))) { throw 'Cloud UI asset missing from installed files.' }
foreach ($publicAsset in @('home.html', 'privacy.html', 'terms.html', 'public.css')) {
    if (-not (Test-Path (Join-Path $install ('portal/static/' + $publicAsset)))) { throw "Public page asset missing: $publicAsset" }
}
if (-not (Test-Path (Join-Path $media 'photos')) -or -not (Test-Path (Join-Path $media 'music'))) { throw 'Photos/music roots missing.' }
$key = Get-Content (Join-Path $install '.secrets/archive/proxy-key') -Raw
if ($key.Length -lt 32) { throw 'Archive proxy key is too short.' }
& (Join-Path $root 'install-windows.ps1') -InstallDir $install -MediaPath $media -Vpn Off -Remote Off -Archive Off -GenerateOnly
if ((Get-Content (Join-Path $install '.secrets/archive/proxy-key') -Raw) -ne $key) { throw 'Disabling archive deleted its credentials.' }
Write-Host 'PASS: optional archive, preserved credentials and complete cloud UI assets.'
