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
