'use strict';
const $=id=>document.getElementById(id);
let csrf='',refreshing=false;
const mediaStatus={1:'Ainda não solicitado',2:'Pedido recebido',3:'A caminho',4:'Disponível em parte',5:'Na biblioteca',6:'Removido'};
const requestStatus={1:'Aguardando aprovação',2:'Solicitado',3:'Recusado',4:'Disponível',5:'Falhou'};
const stateLabel={downloading:'Baixando',stalledDL:'Aguardando peers',metaDL:'Buscando metadados',queuedDL:'Na fila',pausedDL:'Pausado',stoppedDL:'Pausado',uploading:'Compartilhando',stalledUP:'Concluído',queuedUP:'Fila de envio',pausedUP:'Concluído · pausado',stoppedUP:'Concluído · parado',checkingDL:'Verificando',checkingUP:'Verificando',error:'Erro',missingFiles:'Arquivos ausentes',moving:'Movendo arquivos',allocating:'Preparando espaço'};
function el(tag,cls,text){const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=text;return node;}
function empty(title,description=''){const node=el('div','empty');node.append(el('strong','',title));if(description)node.append(el('span','',description));return node;}
function bytes(value){let n=Math.max(0,Number(value)||0),unit=0;const units=['B','KB','MB','GB','TB','PB'];while(n>=1024&&unit<units.length-1){n/=1024;unit++;}return `${n.toLocaleString('pt-BR',{maximumFractionDigits:unit>=3?1:0})} ${units[unit]}`;}
function eta(value){const seconds=Number(value);if(!seconds||seconds>=8640000)return 'Calculando tempo';if(seconds<60)return 'Menos de 1 min';if(seconds<3600)return `${Math.ceil(seconds/60)} min`;if(seconds<86400)return `${Math.floor(seconds/3600)}h ${Math.floor(seconds%3600/60)}min`;return `${Math.ceil(seconds/86400)} dias`;}
function notify(message,error=false){$('message').textContent=message;$('message').className=error?'error':'';$('message').hidden=false;}
async function api(path,body){
  const options={headers:{}};
  if(body!==undefined){options.method='POST';options.headers={'Content-Type':'application/json','X-CSRF-Token':csrf};options.body=JSON.stringify(body);}
  let response;
  try{response=await fetch(path,options);}catch(_){throw new Error('Sem conexão com a Sala. Confira o servidor e tente novamente.');}
  if(response.status===401&&path!='/api/login')showLogin();
  let data;
  try{data=await response.json();}catch(_){throw new Error(`O servidor ou o túnel retornou uma resposta inesperada (HTTP ${response.status}). Tente novamente em instantes.`);}
  if(!response.ok)throw new Error(data.error||'Não foi possível concluir.');
  return data;
}
function showLogin(){$('login').hidden=false;$('dashboard').hidden=true;csrf='';}
function showDashboard(session){csrf=session.csrf;$('login').hidden=true;$('dashboard').hidden=false;$('login-error').textContent='';refresh();}
function tab(name){document.querySelectorAll('.tab-panel').forEach(node=>{node.hidden=node.id!==`tab-${name}`;});document.querySelectorAll('[data-tab]').forEach(node=>node.classList.toggle('active',node.dataset.tab===name));const labels={overview:'Visão geral',downloads:'Downloads',queue:'Fila e pedidos',cloud:'Nuvem',apps:'Aplicativos'};$('breadcrumb').replaceChildren(document.createTextNode('Sua central '),el('span','',`/ ${labels[name]}`));if(name==='cloud')refreshCloud();}
document.querySelectorAll('[data-tab]').forEach(node=>node.addEventListener('click',()=>tab(node.dataset.tab)));
document.querySelectorAll('[data-open-tab]').forEach(node=>node.addEventListener('click',()=>tab(node.dataset.openTab)));
$('login-form').addEventListener('submit',async event=>{event.preventDefault();const button=event.currentTarget.querySelector('button');button.disabled=true;try{showDashboard(await api('/api/login',{username:event.currentTarget.elements.username.value,password:event.currentTarget.elements.password.value}));event.target.elements.password.value='';}catch(error){$('login-error').textContent=error.message;}finally{button.disabled=false;}});
$('logout').addEventListener('click',async()=>{try{await api('/api/logout',{});}finally{showLogin();}});
$('refresh').addEventListener('click',refresh);
$('today').textContent=new Date().toLocaleDateString('pt-BR',{day:'2-digit',month:'long',year:'numeric'});
function stat(label,value,detail,icon){const node=el('article','stat'),top=el('div','stat-top',label);top.append(el('span','stat-icon',icon));node.append(top,el('strong','',value),el('small','',detail));return node;}
function download(item,controls=false){
  const node=el('div','download-row'),head=el('div','row-head'),title=el('span','download-name',item.name);title.title=item.name;head.append(title,el('span','percent',`${Math.round((item.progress||0)*100)}%`));
  const track=el('div','progress'),bar=el('div','progress-bar');bar.style.width=`${Math.min(100,Math.max(0,(item.progress||0)*100))}%`;track.append(bar);
  const meta=el('div','row-meta');meta.append(el('span','',`${stateLabel[item.state]||item.state} · ${bytes(item.size)}`),el('span','',item.progress>=1?`↑ ${bytes(item.upspeed)}/s`:`↓ ${bytes(item.dlspeed)}/s · ${eta(item.eta)}`));
  if(controls){const stopped=/paused|stopped/.test(item.state),button=el('button','download-action',stopped?'Retomar':'Pausar');button.addEventListener('click',async()=>{button.disabled=true;try{await api('/api/torrent',{hash:item.hash,action:stopped?'resume':'pause'});notify(stopped?'Download retomado.':'Download pausado.');setTimeout(refresh,1200);}catch(error){notify(error.message,true);}finally{button.disabled=false;}});meta.append(button);}
  node.append(head,track,meta);return node;
}
function renderRequests(target,items){target.replaceChildren();if(!items.length){target.append(empty('Nenhum pedido recente.','Busque e solicite títulos diretamente no Seerr.'));return;}for(const item of items){const row=el('div','request-row'),text=el('div','',item.title);text.append(el('small','',`${item.type==='tv'?'Série':'Filme'} · ${item.createdAt?new Date(item.createdAt).toLocaleDateString('pt-BR'):''}`));row.append(text,el('span','status-badge',item.mediaStatus>=4?mediaStatus[item.mediaStatus]:(requestStatus[item.status]||'Em processamento')));target.append(row);}}
function render(data){
  const s=data.stats,t=data.transfer,serviceOk=key=>data.services.find(item=>item.name===key)?.ok;
  $('stats').replaceChildren(stat('Filmes na biblioteca',serviceOk('radarr')?String(s.moviesAvailable):'—',`${s.movies} títulos acompanhados`,'▱'),stat('Séries acompanhadas',serviceOk('sonarr')?String(s.series):'—',`${s.episodes} episódios disponíveis`,'▤'),stat('Downloads ativos',serviceOk('torrents')?String(s.active):'—',`${data.downloadTotal} torrents no cliente`,'↓'),stat('Velocidade de download',serviceOk('transfer')?`${bytes(t.dl_info_speed)}/s`:'—',`Upload: ${bytes(t.up_info_speed)}/s`,'↗'));
  const active=data.downloads.filter(item=>item.progress<1);$('overview-downloads').replaceChildren(...(active.length?active.slice(0,3).map(item=>download(item)):[empty('Sem downloads em andamento.','Adicione um pedido no Seerr ou um torrent autorizado no qBittorrent.')]));$('downloads-list').replaceChildren(...(data.downloads.length?data.downloads.map(item=>download(item,true)):[empty('Nenhum torrent na lista.','Os downloads aparecem aqui quando o cliente os recebe.')]));$('nav-downloads').textContent=s.active;
  const disk=$('disk');disk.replaceChildren();if(data.disk){const d=data.disk,value=el('div','disk-value',bytes(d.free));value.append(el('small','',' livres'));const track=el('div','progress'),bar=el('div','progress-bar');bar.style.width=`${d.percent}%`;track.append(bar);const legend=el('div','disk-legend');legend.append(el('span','',`${bytes(d.used)} em uso`),el('span','',`${bytes(d.total)} no total`));disk.append(value,el('p','muted',`${d.percent.toLocaleString('pt-BR')}% do disco utilizado`),track,legend,el('p','disk-note','Capacidade do sistema de arquivos montado em /media. Confira o disco escolhido na instalação.'));}else disk.append(empty('Disco indisponível.','Confira a montagem da pasta da biblioteca.'));
  renderRequests($('recent-requests'),data.requests.slice(0,4));renderRequests($('requests-list'),data.requests);
  const services=$('services');services.replaceChildren();for(const [key,name] of Object.entries({torrents:'qBittorrent',radarr:'Radarr',sonarr:'Sonarr',requests:'Seerr'})){const state=data.services.find(item=>item.name===key),row=el('div','service-row');row.append(el('span','',name),el('span',`service-state${state?.ok?'':' error'}`,state?.ok?'● Conectado':`○ ${state?.error||'Indisponível'}`));services.append(row);}
  const queue=$('queue-list');queue.replaceChildren();if(!data.queue.length)queue.append(empty('Sem pendências de importação.','Esta fila é de acompanhamento. Crie pedidos no Seerr e configure fontes no Prowlarr.'));for(const item of data.queue){const row=el('div','request-row'),text=el('div','',item.title);text.append(el('small','',`${item.type==='tv'?'Série':'Filme'} · ${bytes(Math.max(0,item.size-item.sizeLeft))} de ${bytes(item.size)}`));row.append(text,el('span','status-badge',item.status||'Aguardando'));queue.append(row);}
  if(!serviceOk('torrents'))for(const id of ['overview-downloads','downloads-list'])$(id).replaceChildren(empty('Cliente de downloads desconectado.','Confira o qBittorrent e termine a configuração inicial.'));
  if(!serviceOk('requests'))for(const id of ['recent-requests','requests-list'])$(id).replaceChildren(empty('Seerr ainda não está conectado.','Conclua a configuração inicial no aplicativo.'));
  $('last-update').textContent=`Atualizado às ${new Date(data.generatedAt*1000).toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'})}`;$('connection-label').textContent=data.services.every(item=>item.ok)?'Serviços conectados':'Há serviços para configurar';$('vpn-label').textContent=data.vpn?'WireGuard selecionado':'Conexão direta · sem VPN';
}
const apps=[['Seerr',5055,'Descobrir e solicitar filmes e séries.'],['Jellyfin',8096,'Navegar pela biblioteca e assistir.'],['qBittorrent',8080,'Gerenciar torrents autorizados e a fila.'],['Prowlarr',9696,'Configurar suas fontes e indexadores.'],['Radarr',7878,'Importação e qualidade dos filmes.'],['Sonarr',8989,'Temporadas, episódios e importação de séries.'],['Bazarr',6767,'Configurar idiomas e provedores de legendas.'],['Profilarr',6868,'Gerenciar perfis de qualidade.'],['Qui',7476,'Gerenciar instâncias do cliente torrent.'],['Dozzle',8888,'Consultar os logs locais dos serviços.']];
for(const [name,port,description] of apps){const card=el('article','panel'),link=el('a','secondary',`Abrir ${name} ↗`);link.href=`http://localhost:${port}`;link.target='_blank';link.rel='noopener noreferrer';card.append(el('span','eyebrow','ACESSO LOCAL'),el('h2','',name),el('p','',description),link);$('apps-list').append(card);}
async function refresh(){if(!csrf||refreshing)return;refreshing=true;if(!$('tab-cloud').hidden)refreshCloud();try{render(await api('/api/status'));}catch(error){notify(error.message,true);}finally{refreshing=false;}}
setInterval(()=>{if(!document.hidden)refresh();},15000);
api('/api/session').then(showDashboard).catch(()=>showLogin());
