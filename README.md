# Sala: homepage de midia + stack ARR, TrueNAS e Windows

Fork pessoal de [Servers@Home](https://github.com/serversathome/ServersatHome),
baseado no commit `ef1d9596165c2c5beaf069336a2f86947fc02615`.
Os materiais e creditos originais estao preservados abaixo.

BitTorrent nao precisa de VPN. Escolha **com WireGuard** ou **sem VPN** na
instalacao. Sem VPN, o trafego sai pela sua conexao normal e os peers veem
seu IP publico. Com VPN, o cliente fica vinculado a `wg0`; o instalador nao
muda automaticamente para conexao direta se o tunel falhar.

A homepage **Sala** centraliza busca de filmes/series pelo Seerr, solicitacao
de titulos, progresso e velocidade dos downloads, pausa/retomada, fila do
Radarr/Sonarr, pedidos recentes, biblioteca e capacidade do disco de midia.
O painel atualiza a cada 15 segundos e tem login, sessao HttpOnly, protecao CSRF
e limite de tentativas de acesso. As chaves dos servicos permanecem no backend.
O aplicativo le as configuracoes dos outros containers como somente leitura;
nao recebe acesso ao socket Docker.

## TrueNAS SCALE

Execute no shell do TrueNAS:

```bash
curl -fL https://raw.githubusercontent.com/s3veral/media-stack-vpn-optional/main/truenas-file-structure.sh -o /tmp/media-stack-install.sh && sudo bash /tmp/media-stack-install.sh
```

O menu pergunta se deseja VPN. Enter usa `false` em uma instalacao nova e
preserva a escolha anterior nas proximas execucoes. Tambem existem os argumentos
`--vpn` e `--no-vpn`. Ao migrar do script original, o modo VPN existente e
detectado e mantido como padrao. O perfil WireGuard so e solicitado no modo VPN.

O script preserva a estrutura ZFS e a configuracao automatica original. Tambem
ajusta a interface do qBittorrent: `wg0` com VPN, qualquer interface sem VPN.
No modo direto publica a porta P2P 6881 TCP/UDP. O estado e salvo em
`/mnt/<pool-configs>/docker/.vpn-enabled`. Reexecutar o script recria o Compose,
guardando uma copia do anterior. Os logins padrao do instalador TrueNAS original
continuam `admin` / `changeme`; altere-os nas interfaces.

Ao final, responda `s` para instalar tambem a homepage Sala. Ela fica em
`http://IP-DO-TRUENAS:8090`, com senha propria aleatoria exibida no terminal.
O instalador pergunta se deseja publicar com Cloudflare e qual subdominio usar.
O portal fica em `/mnt/<pool-configs>/docker/sala`, separado do Compose original.

## Windows com Docker Desktop

Requisito: [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/)
aberto, usando WSL 2 e **Linux containers**. Nao use o script TrueNAS no PowerShell.
Nao e necessario instalar Python, Bash ou Git no Windows.

No PowerShell (usuario normal):

```powershell
Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/s3veral/media-stack-vpn-optional/main/bootstrap-windows.ps1' -OutFile "$env:TEMP\media-stack-install.ps1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:TEMP\media-stack-install.ps1"
```

Escolha a pasta da biblioteca (por exemplo `D:\Media`), se deseja VPN e se quer
acesso externo com Cloudflare. Para acesso externo, informe o subdominio completo
e um API token da Cloudflare no prompt oculto. Instalacao padrao em
`%USERPROFILE%\MediaStack`. Exemplo apenas local, sem perguntas:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:TEMP\media-stack-install.ps1" -Vpn Off -Remote Off -MediaPath 'D:\Media'
```

O instalador cria `movies`, `tv`, `downloads/torrent` e `downloads/crossseed`
nessa pasta, monta tudo como `/media` nos containers e usa volumes nomeados para
as configuracoes e bancos SQLite. Discos locais sao suportados; compartilhamentos
SMB/UNC nao sao configurados automaticamente. Em NTFS, importacoes podem copiar
arquivos em vez de usar hardlinks; reserve espaco para downloads e biblioteca.

A stack inclui qBittorrent, Radarr, Sonarr, Prowlarr, Bazarr, Jellyfin, Seerr,
Profilarr, FlareSolverr, Qui e Dozzle. Um container temporario de setup reutiliza
as rotinas de configuracao do instalador TrueNAS (sem executar ZFS). Ele precisa
do socket Docker para reiniciar o qBittorrent e ler as configuracoes. Sai ao fim
da configuracao. Watchtower nao e iniciado no Windows; atualize manualmente.

Os paines ficam em `localhost` no Windows por padrao:

| Servico | Endereco |
| --- | --- |
| Homepage Sala | http://localhost:8090 |
| Jellyfin | http://localhost:8096 |
| Seerr | http://localhost:5055 |
| qBittorrent | http://localhost:8080 |
| Radarr | http://localhost:7878 |
| Sonarr | http://localhost:8989 |
| Prowlarr | http://localhost:9696 |
| Bazarr | http://localhost:6767 |
| Profilarr | http://localhost:6868 |
| Qui | http://localhost:7476 |
| Dozzle | http://localhost:8888 |

O Windows gera uma senha aleatoria no primeiro uso, salva em `.settings.json`
e `.env`; ela e reutilizada nas proximas execucoes e exibida ao final. Trate esses
arquivos como privados. A configuracao automatica pode levar varios minutos.
Se uma etapa falhar, o instalador sinaliza a integracao pendente e os containers ficam disponiveis
para diagnostico; reexecute para tentar novamente. Indexadores do Prowlarr,
idiomas/provedores do Bazarr e o scan inicial do Qui continuam manuais.
As automacoes do Qui herdadas do upstream incluem limpeza de downloads antigos;
revise-as na interface antes de adicionar sua biblioteca.

## Cloudflare Tunnel e subdominio

Para publicar somente a homepage (os paines administrativos continuam locais),
voce precisa de um dominio ativo na sua conta Cloudflare. **Nao precisa criar nem
colar um API token.** O Windows e o TrueNAS usam o login oficial do `cloudflared`:

1. Escolha publicar a homepage durante a instalacao.
2. Abra o link unico exibido no terminal (pode usar outro computador ou celular).
3. Entre na sua conta Cloudflare, selecione o dominio e clique em **Authorize / Autorizar**.
4. Volte ao terminal e informe o endereco completo, por exemplo `sala.seudominio.com.br`.
5. O instalador cria o tunnel localmente gerenciado, adiciona o CNAME e inicia o conector.

O link depende da sessao e por isso e gerado durante a instalacao, nao e um link
fixo neste README. O fluxo e o da [documentacao oficial da Cloudflare](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/local-management/create-local-tunnel/).
Nao sobrescrevemos DNS existente. Se o endereco estiver ocupado, escolha outro;
as credenciais ficam salvas para repetir a tentativa sem criar outro tunnel para o mesmo endereco.

Tudo fica na pasta privada `.secrets`, excluida do Git: a autorizacao da conta em
`login-home/.cloudflared/cert.pem` e as credenciais de execucao em `runtime/`.
O certificado tem permissoes de gerenciamento na conta; proteja-o e inclua-o apenas
em backups privados. **O conector permanente recebe somente `runtime/`, nao o certificado da conta.**
Tunnels ja configurados sao reutilizados, inclusive os da versao anterior com token.

No Windows, tambem pode iniciar assim:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:TEMP\media-stack-install.ps1" -Vpn Off -MediaPath 'D:\Media' -Remote On -PublicHostname 'sala.seudominio.com'
```

O login acontece no navegador; o instalador nao recebe sua senha Cloudflare.
Para desativar o acesso externo, execute `install-windows.ps1 -Remote Off`.
O conector para, mas o DNS/tunnel permanece na sua conta para reuso.
Para autorizar outro dominio/conta, renomeie `cert.pem` para um backup privado e
reexecute escolhendo um novo subdominio; o instalador mostrara outro link de login.
Se o login expirar, faca o mesmo. Nao apague as credenciais de `tunnels/` ou `runtime/`.

O tunnel publica a homepage, nao o streaming Jellyfin nem as APIs administrativas
dos aplicativos. Busca e solicitacoes dependem do setup do Seerr, dos perfis
Radarr/Sonarr e dos seus indexadores; o painel nao adiciona fontes de conteudo.
A capacidade exibida e a do filesystem montado em `/media`, nao a memoria/CPU
do host. No Docker Desktop, confira se a pasta escolhida corresponde ao disco
que deseja monitorar. Imagens de capas sao carregadas do TMDB pelo navegador.

Gerenciar depois da instalacao:

```powershell
cd "$env:USERPROFILE\MediaStack"
.\stack.ps1 ps
.\stack.ps1 logs --tail 100 qbittorrent
.\stack.ps1 stop
.\stack.ps1 up -d
# Atualizacao manual:
.\stack.ps1 pull
.\stack.ps1 up -d
```

Trocar de modo sem reinstalar a biblioteca:

```powershell
.\install-windows.ps1 -Vpn Off
.\install-windows.ps1 -Vpn On -WireGuardConfig 'C:\caminho\cliente.conf' -LanNetwork '192.168.1.0/24'
```

O perfil precisa conter `DNS`, `PrivateKey`, `Address`, `PublicKey`, `Endpoint`
e rota para a internet em `AllowedIPs`. Mantenha-o fora do Git. O arquivo do host
em `wireguard/wg0.conf` e copiado para o volume privado do qBittorrent apenas
quando VPN esta ativada. No modo VPN, as portas P2P do host nao sao publicadas;
se houver port forwarding no seu provedor/VPS, ajuste a porta nas configuracoes
do qBittorrent e no firewall/encaminhamento do servidor. O padrao e 6881.

Sem VPN, downloads funcionam mesmo sem porta de entrada. Para receber mais
conexoes, voce pode encaminhar 6881 TCP/UDP no roteador para o Windows e permitir
essa porta no firewall. CGNAT pode impedir conexoes de entrada e nao impede
conexoes de saida. A WebUI 8080 e os outros paines nao precisam desse encaminhamento.

Opcoes de preparacao: `-GenerateOnly` grava a estrutura sem iniciar containers;
`-SkipConfigure` inicia a stack sem executar a configuracao via APIs. Use sempre
a mesma pasta `-InstallDir` ao atualizar. `docker compose down -v` apaga volumes
de configuracao; `stop` apenas para os servicos.

## Validacao e limites

Testes locais: parser PowerShell, sintaxe Bash, configuracoes Compose nos dois
modos, selecao/persistencia de VPN, mudanca da interface e geracao do setup
portavel. O workflow verifica tambem o build do setup e um download de uma
fixture local via BitTorrent **sem VPN**, sem baixar midia de terceiros.
Testes adicionais cobrem login, sessao, CSRF, limitacao de tentativas, pedidos
permitidos e provisionamento Cloudflare com uma API simulada. Nenhuma zona DNS
real e alterada pelos testes. A interface foi conferida no navegador local.
Os fluxos completos de onboarding das APIs dos aplicativos dependem das imagens
`latest` e podem exigir ajustes conforme os projetos mudam. VPN requer um
servidor/perfil real para validar o handshake; nenhum segredo esta no repositorio.

---

## README original do Servers@Home

<!-- Banner image -->
<p align="center">
  <img src="https://raw.githubusercontent.com/serversathome/ServersatHome/main/servers-at-home-github-preview.png" alt="Servers@Home Banner" />
</p>

<h1 align="center">Servers@Home</h1>

<p align="center">
  <strong>Learn, build, and master self-hosted services, home labs, and server setups.</strong><br>
  <em>Making self-hosting approachable, fun, and rewarding — for beginners and seasoned sysadmins alike.</em>
</p>

<p align="center">
  <a href="https://youtube.com/@Serversathome"><img src="https://img.shields.io/badge/YouTube-Subscribe-FF0000?style=for-the-badge&labelColor=1F2328&logo=youtube&logoColor=white" alt="Subscribe on YouTube"></a>
  <a href="https://discord.gg/syvCPcRJnR"><img src="https://img.shields.io/discord/1120465621554040942?style=for-the-badge&labelColor=1F2328&logo=discord&logoColor=white&label=Discord&color=5865F2" alt="Join the Discord"></a>
  <a href="https://github.com/serversathome/ServersatHome/stargazers"><img src="https://img.shields.io/github/stars/serversathome/ServersatHome?style=for-the-badge&labelColor=1F2328&logo=github&logoColor=white&label=Stars&color=DAA520" alt="GitHub Stars"></a>
  <a href="https://wiki.serversatho.me"><img src="https://img.shields.io/badge/Wiki-Docs-1E90FF?style=for-the-badge&labelColor=1F2328&logo=wikidotjs&logoColor=white" alt="Wiki"></a>
</p>

---

## 🏠 About

Servers@Home is run by **Evan** — a Solutions Architect by day, homelabber by night. The mission is simple: make self-hosting accessible without dumbing it down.

Topics we cover:

- 🖥️ Home servers built on **TrueNAS**, **Proxmox**, and **Docker**
- 📦 Self-hosted apps — **Jellyfin**, **Nextcloud**, **Immich**, **Matrix/Synapse**, **Joplin**, and dozens more
- 🌐 Networking, **VPNs**, **Cloudflare Tunnels**, **NetBird**, and homelab security
- 🛠️ Real-world tutorials, scripts, and reference architectures

---

## 📺 Where to Find Us

| Platform | What you'll find |
| --- | --- |
| 🎥 [**YouTube**](https://youtube.com/@Serversathome) | Weekly tutorials, app walkthroughs, and homelab deep dives |
| 📖 [**Blog**](https://blog.serversatho.me) | Long-form articles and write-ups |
| 📚 [**Wiki**](https://wiki.serversatho.me) | Quick reference guides and project documentation |
| 💬 [**Discord**](https://discord.gg/syvCPcRJnR) | Community chat, help, and homelab show-and-tell |
| 💼 [**LinkedIn**](https://www.linkedin.com/company/serversathome/) | Professional updates and homelab tips |

---



## 🎓 Learn With Us

- 🎓 [**TrueNAS Fundamentals for Beginners**](https://www.udemy.com/course/truenas-fundamentals-for-beginners/) on Udemy — the essentials of TrueNAS SCALE from the ground up.

---

## 💼 Hire Me

Need a hand with your homelab, a TrueNAS or Proxmox migration, container deployments, or a self-hosted setup from scratch? **I'm available for consulting and project work.**

➡️ **[serversatho.me/hire](https://serversatho.me/hire)**

<p align="center">
  <a href="https://serversatho.me/hire">
    <img src="https://img.shields.io/badge/Available%20for%20Hire-Get%20in%20Touch-2EA44F?style=for-the-badge&labelColor=1F2328&logo=gmail&logoColor=white" alt="Available for Hire">
  </a>
</p>

---

## 🧡 Support the Project

If Servers@Home has saved you time or helped your homelab, here's how to give back:

- 🧡 [**Patreon**](https://www.patreon.com/serversathome) — Recurring support + bonus content
- ☕ [**Buy Me a Coffee**](https://www.buymeacoffee.com/serversathome) — One-time donations
- 🛒 [**Shop**](https://buymeacoffee.com/serversathome/extras) — T-shirts and homelab swag

<p align="center">
  <a href="https://www.patreon.com/serversathome"><img src="https://img.shields.io/badge/Patreon-Support-FF424D?style=for-the-badge&labelColor=1F2328&logo=patreon&logoColor=white" alt="Support on Patreon"></a>
  <a href="https://www.buymeacoffee.com/serversathome"><img src="https://img.shields.io/badge/Buy%20Me%20a%20Coffee-Donate-FFDD00?style=for-the-badge&labelColor=1F2328&logo=buymeacoffee&logoColor=white" alt="Buy Me a Coffee"></a>
  <a href="https://buymeacoffee.com/serversathome/extras"><img src="https://img.shields.io/badge/Shop-Swag-1E90FF?style=for-the-badge&labelColor=1F2328&logo=shopify&logoColor=white" alt="Shop"></a>
</p>

---

## 🌟 Star History

<a href="https://www.star-history.com/?repos=serversathome%2FServersatHome&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=serversathome/ServersatHome&type=date&theme=dark&legend=top-left&sealed_token=_-wCdaym6Oep_Fq-7HOfn67kYxOvHh5PC3Fzqg9zDAuvFiuC_eKucRtPVUSbKZJUZlJnfxX8jQ9jbBZY8Xr3H1ExrE6dUr1wiC4czr-TCdaDxDqdcMCSxEVRuOwD1BesYtgrtyAfkbFy0zN31q2HkWfA5RHLlAJ3fySpXuWBlqAVSpATmbyGsEjwMCTs" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=serversathome/ServersatHome&type=date&legend=top-left&sealed_token=_-wCdaym6Oep_Fq-7HOfn67kYxOvHh5PC3Fzqg9zDAuvFiuC_eKucRtPVUSbKZJUZlJnfxX8jQ9jbBZY8Xr3H1ExrE6dUr1wiC4czr-TCdaDxDqdcMCSxEVRuOwD1BesYtgrtyAfkbFy0zN31q2HkWfA5RHLlAJ3fySpXuWBlqAVSpATmbyGsEjwMCTs" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=serversathome/ServersatHome&type=date&legend=top-left&sealed_token=_-wCdaym6Oep_Fq-7HOfn67kYxOvHh5PC3Fzqg9zDAuvFiuC_eKucRtPVUSbKZJUZlJnfxX8jQ9jbBZY8Xr3H1ExrE6dUr1wiC4czr-TCdaDxDqdcMCSxEVRuOwD1BesYtgrtyAfkbFy0zN31q2HkWfA5RHLlAJ3fySpXuWBlqAVSpATmbyGsEjwMCTs" />
 </picture>
</a>
