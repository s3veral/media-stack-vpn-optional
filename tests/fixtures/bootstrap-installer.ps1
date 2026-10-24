# Synthetic installer: records forwarded parameters, never starts containers.
[CmdletBinding()]
param(
    [string]$InstallDir,
    [string]$MediaPath,
    [string]$Vpn = 'Ask',
    [string]$WireGuardConfig,
    [string]$LanNetwork,
    [string]$TimeZone = 'America/Sao_Paulo',
    [string]$Remote = 'Ask',
    [string]$PublicHostname,
    [ValidateSet('Ask', 'On', 'Off')][string]$Archive = 'Ask',
    [string]$DriveConfig,
    [switch]$GenerateOnly,
    [switch]$SkipConfigure
)
[pscustomobject]@{
    Archive = $Archive
    InstallDir = $InstallDir
    MediaPath = $MediaPath
    DriveConfig = $DriveConfig
    Vpn = $Vpn
    Remote = $Remote
    GenerateOnly = [bool]$GenerateOnly
    SkipConfigure = [bool]$SkipConfigure
}
