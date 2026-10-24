# Exercise the real bootstrap with isolated download/extraction fixtures.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$fixture = Join-Path $PSScriptRoot 'fixtures/bootstrap-installer.ps1'
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('media-stack-bootstrap-test-' + [Guid]::NewGuid().ToString('N'))
$installPath = Join-Path $testRoot 'Install with spaces'
$mediaPath = Join-Path $testRoot 'Media with spaces'
$configPath = Join-Path $testRoot 'Drive config.conf'
$downloadState = [pscustomobject]@{ Paths = (New-Object 'System.Collections.Generic.List[string]') }
function Invoke-WebRequest {
    [CmdletBinding()]
    param([switch]$UseBasicParsing, [string]$Uri, [string]$OutFile)
    if ($Uri -ne 'https://github.com/s3veral/media-stack-vpn-optional/archive/refs/heads/main.zip') { throw 'Unexpected download URL.' }
    if ((Split-Path -Leaf $OutFile) -ne 'source.zip') { throw 'Download destination must be a ZIP path, not the Archive option.' }
    $downloadState.Paths.Add($OutFile)
}
function Expand-Archive {
    [CmdletBinding()]
    param([string]$LiteralPath, [string]$DestinationPath)
    if ($LiteralPath -ne $downloadState.Paths[$downloadState.Paths.Count - 1]) { throw 'Extraction did not receive the downloaded ZIP path.' }
    $fixtureRoot = Join-Path $DestinationPath 'media-stack-vpn-optional-main'
    New-Item -ItemType Directory -Path $fixtureRoot | Out-Null
    Copy-Item -LiteralPath $fixture -Destination (Join-Path $fixtureRoot 'install-windows.ps1')
}
foreach ($choice in @('On', 'Off', 'Ask')) {
    $result = & (Join-Path $root 'bootstrap-windows.ps1') -Archive $choice -InstallDir $installPath -MediaPath $mediaPath -DriveConfig $configPath -Vpn Off -Remote Off -GenerateOnly -SkipConfigure
    if ($result.Archive -ne $choice) { throw "Archive option was overwritten during bootstrap: $choice" }
    if ($result.InstallDir -ne $installPath -or $result.MediaPath -ne $mediaPath -or $result.DriveConfig -ne $configPath) { throw 'Paths with spaces were not forwarded.' }
    if ($result.Vpn -ne 'Off' -or $result.Remote -ne 'Off' -or -not $result.GenerateOnly -or -not $result.SkipConfigure) { throw 'Bootstrap lost installer parameters.' }
}
$defaultResult = & (Join-Path $root 'bootstrap-windows.ps1') -GenerateOnly
if ($defaultResult.Archive -ne 'Ask') { throw 'Default Archive selection was not preserved.' }
if ($downloadState.Paths.Count -ne 4) { throw 'Not all bootstrap paths were exercised.' }
Write-Host 'PASS: real bootstrap preserves Archive On/Off/Ask/default and forwards installer parameters.'
