#Requires -Version 5.1
<#
Preview by default. -Apply tests and saves public torrent sources in Prowlarr.
No passwords/API keys in arguments, files, output, or remote request URLs.
No tracker ranking, private-account automation, mirrors, or force-save.
#>
[CmdletBinding()]
param(
    [string]$InstallDir = "$env:USERPROFILE\MediaStack",
    [string[]]$Indexers = @('internetarchive', 'knaben', 'nyaasi', 'eztv', 'yts', 'limetorrents', '1337x'),
    [switch]$Apply
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Get-IndexerValue($Object, [string]$Name, $Default = $null) {
    if ($null -eq $Object) { return $Default }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $Default }
    return $property.Value
}

function Normalize-IndexerName([string]$Name) { return $Name.ToLowerInvariant() -replace '[^a-z0-9]', '' }

function Get-IndexerKey($Source) {
    $definition = @((Get-IndexerValue $Source 'fields' @()) | Where-Object { $_.name -eq 'definitionFile' })
    if ($definition.Count -gt 0) { return Normalize-IndexerName ([string]$definition[0].value) }
    return Normalize-IndexerName ([string](Get-IndexerValue $Source 'implementation' ''))
}

function Get-LocalProwlarrKey([string]$Directory) {
    $directoryPath = [IO.Path]::GetFullPath($Directory).TrimEnd('\', '/')
    $labelOutput = @(& docker inspect --type container --format '{{json .Config.Labels}}' prowlarr 2>$null)
    if ($LASTEXITCODE -ne 0) { throw 'Prowlarr nao esta disponivel. Abra o Docker Desktop e inicie a stack.' }
    $labels = ($labelOutput -join "`n") | ConvertFrom-Json
    if ((Get-IndexerValue $labels 'com.docker.compose.project') -ne 'media-stack' -or
        (Get-IndexerValue $labels 'com.docker.compose.project.working_dir') -ine $directoryPath) {
        throw 'O container prowlarr nao pertence a esta instalacao MediaStack; nenhuma alteracao feita.'
    }
    $xmlOutput = @(& docker exec prowlarr cat /config/config.xml 2>$null)
    if ($LASTEXITCODE -ne 0) { throw 'Nao foi possivel ler a configuracao privada do Prowlarr.' }
    try {
        $document = New-Object Xml.XmlDocument
        $document.XmlResolver = $null
        $document.LoadXml(($xmlOutput -join "`n"))
        $key = [string]$document.Config.ApiKey
    } catch { throw 'Configuracao Prowlarr invalida; nenhum segredo foi exibido.' }
    if ($key -notmatch '^[a-fA-F0-9]{32}$') { throw 'Chave local Prowlarr ainda nao esta disponivel.' }
    return $key
}

function Invoke-Prowlarr([string]$Key, [string]$Path, [string]$Method = 'GET', $Body = $null) {
    # Fixed localhost destination; never forward the API key through a redirect.
    if ($Path -notmatch '^(indexer(/schema|/test)?|appprofile|applications|indexerproxy|command(/[0-9]+)?)$') {
        throw 'Endpoint nao permitido.'
    }
    $request = @{ Uri = "http://127.0.0.1:9696/api/v1/$Path"; Method = $Method;
        Headers = @{ 'X-Api-Key' = $Key }; UseBasicParsing = $true; TimeoutSec = 120; MaximumRedirection = 0 }
    if ($null -ne $Body) {
        $request.ContentType = 'application/json; charset=utf-8'
        $request.Body = [Text.Encoding]::UTF8.GetBytes(($Body | ConvertTo-Json -Depth 60 -Compress))
    }
    try {
        $response = Invoke-WebRequest @request
        if ($response.Content -and $response.Content.Trim()) {
            # Windows PowerShell 5.1 emits a JSON array as one pipeline object.
            $parsed = $response.Content | ConvertFrom-Json
            return $parsed
        }
    } catch {
        $status = 'sem conexao'
        $failureResponse = Get-IndexerValue $_.Exception 'Response'
        if ($null -ne $failureResponse) { $status = 'HTTP ' + [int]$failureResponse.StatusCode }
        $detail = if ($null -ne $_.ErrorDetails) { [string]$_.ErrorDetails.Message } else { '' }
        if ($detail -match '(?i)cloudflare|captcha|forbidden') { $status += '; bloqueio do site/Cloudflare' }
        # Error bodies can contain request settings: never print them.
        throw "Prowlarr $Path`: $status. Consulte o painel para detalhes."
    }
}

function New-PublicIndexer($Schema, [int]$ProfileId, [int[]]$ProxyTags = @()) {
    if ((Get-IndexerValue $Schema 'privacy') -ne 'public' -or (Get-IndexerValue $Schema 'protocol') -ne 'torrent') {
        throw 'Fonte privada, com cadastro ou Usenet: configure manualmente na interface.'
    }
    foreach ($field in $Schema.fields) {
        if ($field.type -eq 'password' -or (Get-IndexerValue $field 'privacy' 'normal') -notin @('normal', $null) -or
            $field.name -match '(?i)^(username|password|cookie|apikey|passkey|token)$') {
            throw 'A definicao pede credenciais; configure-a manualmente.'
        }
    }
    $body = $Schema | ConvertTo-Json -Depth 60 | ConvertFrom-Json
    $body.PSObject.Properties.Remove('id')
    $body | Add-Member NoteProperty enable $true -Force
    $body | Add-Member NoteProperty appProfileId $ProfileId -Force
    $body | Add-Member NoteProperty tags @() -Force
    $baseFields = @($body.fields | Where-Object { $_.name -eq 'baseUrl' })
    if ($baseFields.Count -gt 0) {
        $urls = @(Get-IndexerValue $Schema 'indexerUrls' @())
        if ($urls.Count -eq 0) { throw 'Sem URL padrao na definicao atual; ajuste pela interface.' }
        $primary = [Uri]$urls[0]
        if (-not $primary.IsAbsoluteUri -or $primary.Scheme -ne 'https' -or $primary.UserInfo) {
            throw 'URL padrao sem HTTPS ou com credenciais; ajuste pela interface.'
        }
        $baseFields[0] | Add-Member NoteProperty value $primary.AbsoluteUri -Force
    }
    if (@($body.fields | Where-Object { $_.name -eq 'info_flaresolverr' }).Count -gt 0) {
        $body.tags = @($ProxyTags)
    }
    return $body
}

function Invoke-IndexerSetup([string]$Directory, [string[]]$Names, [switch]$Save) {
    $key = Get-LocalProwlarrKey $Directory
    $schemas = @(Invoke-Prowlarr $key 'indexer/schema')
    $existing = @(Invoke-Prowlarr $key 'indexer')
    $profiles = @(Invoke-Prowlarr $key 'appprofile')
    $apps = @(Invoke-Prowlarr $key 'applications')
    $proxies = @(Invoke-Prowlarr $key 'indexerproxy')
    $profile = @($profiles | Where-Object { $_.enableRss -and $_.enableAutomaticSearch -and $_.enableInteractiveSearch } | Select-Object -First 1)
    if ($profile.Count -eq 0) { throw 'Falta um Sync Profile com RSS, pesquisa automatica e interativa. Crie-o no Prowlarr.' }
    $proxyTags = @($proxies | Where-Object { $_.implementation -eq 'FlareSolverr' } | ForEach-Object { $_.tags } | Select-Object -Unique)
    $appNames = @($apps | ForEach-Object { [string]$_.name })
    Write-Host ('Aplicativos ja vinculados: ' + ($appNames -join ', '))
    Write-Host 'O script nao instala Lidarr, gerenciador de livros, nem provedores de legendas.'
    if ($Save) {
        Write-Warning 'APLICANDO: fontes aprovadas serao ativadas e sincronizadas. Titulos ja monitorados podem comecar a baixar via RSS/pesquisas dos aplicativos.'
    } else {
        Write-Host 'PREVIA: somente leitura local. Nenhuma fonte sera testada, salva ou sincronizada. Use -Apply para aplicar.'
    }
    $report = @()
    $seen = @{}
    $added = 0
    foreach ($requested in $Names) {
        $normalized = Normalize-IndexerName $requested
        $matches = @($schemas | Where-Object {
            (Get-IndexerKey $_) -eq $normalized -or (Normalize-IndexerName $_.name) -eq $normalized
        })
        if ($matches.Count -eq 0) {
            $report += [pscustomobject]@{ Fonte = $requested; Categorias = ''; Estado = 'AUSENTE na definicao instalada' }
            continue
        }
        $schema = $matches[0]
        $identity = Get-IndexerKey $schema
        if ($seen.ContainsKey($identity)) { continue }
        $seen[$identity] = $true
        $categories = @((Get-IndexerValue (Get-IndexerValue $schema 'capabilities') 'categories' @()) |
            Where-Object { $_.id -in @(2000, 3000, 5000, 7000) } | ForEach-Object { $_.name }) -join ', '
        $row = [pscustomobject]@{ Fonte = $schema.name; Categorias = $categories; Estado = '' }
        $already = @($existing | Where-Object { (Get-IndexerKey $_) -eq $identity -or $_.name -eq $schema.name })
        if ($already.Count -gt 0) {
            $row.Estado = 'EXISTENTE: preservado, inclusive se desativado'
        } else {
            try {
                $body = New-PublicIndexer $schema ([int]$profile[0].id) $proxyTags
                if (-not $Save) { $row.Estado = 'PREVISTO: sera testado antes de salvar' }
                else {
                    Write-Host ('Testando ' + $schema.name + '...')
                    $test = @(Invoke-Prowlarr $key 'indexer/test' 'POST' $body)
                    $emptyObject = $test.Count -eq 1 -and $test[0] -is [pscustomobject] -and @($test[0].PSObject.Properties).Count -eq 0
                    $emptyText = $test.Count -eq 1 -and $test[0] -is [string] -and $test[0].Trim() -eq '{}'
                    if ($test.Count -gt 0 -and -not $emptyObject -and -not $emptyText) {
                        throw 'O teste retornou avisos; fonte nao salva.'
                    }
                    $saved = Invoke-Prowlarr $key 'indexer' 'POST' $body
                    if (-not (Get-IndexerValue $saved 'id')) { throw 'Salvamento nao confirmado. Confira o painel antes de repetir.' }
                    $existing += $saved
                    $added++
                    $row.Estado = 'ADICIONADO: teste aprovado'
                }
            } catch { $row.Estado = 'PENDENTE: ' + $_.Exception.Message }
        }
        $report += $row
    }
    if ($added -gt 0 -and $apps.Count -gt 0) {
        try {
            $command = Invoke-Prowlarr $key 'command' 'POST' @{ name = 'ApplicationIndexerSync'; forceSync = $false }
            Write-Host ('Sincronizacao solicitada; tarefa ' + $command.id + '. Confira Settings > Apps e os indexadores em Radarr/Sonarr.')
        } catch { Write-Warning 'Fontes salvas, mas a solicitacao de sincronizacao falhou. Use Sync App Indexers no Prowlarr.' }
    }
    Write-Host "Novas fontes salvas: $added. Fontes existentes nunca sao alteradas; falhas nunca usam forceSave."
    return $report
}

if ($MyInvocation.InvocationName -ne '.') {
    Invoke-IndexerSetup -Directory $InstallDir -Names $Indexers -Save:$Apply | Format-Table -AutoSize -Wrap
}
