'use strict';
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = (t, short=false) => t ? new Intl.DateTimeFormat('fr-FR', {timeZone:'Europe/Paris', day:'2-digit', month:'2-digit', ...(short ? {} : {year:'numeric'}), hour:'2-digit', minute:'2-digit'}).format(new Date(t*1000)) : '—';
const labels = {ok:'Aucune coupure', outage:'Incident en cours', works:'Travaux signalés', unknown:'Statut inconnu', paused:'En pause'};
const badge = (s, text=null) => `<span class="status-badge status-${esc(s)}">${esc(text || labels[s] || s)}</span>`;
let state = {places:[],history:[],observations:[],counts:{checks:0}}, currentView='overview', filter='all', editId=null, selected=null, searchTimer, searchAbort, searchVersion=0, toastTimer, loading=false;

async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-Veille-Request':'1'},body:JSON.stringify(body)});
  if(response.status===401){window.location.replace('/login');throw Error('Votre session a expiré. Reconnectez-vous.');}
  const result = await response.json();
  if (!response.ok) throw Error(result.error || 'Le serveur ne répond pas.');
  return result;
}
function toast(message) { $('#toast').textContent=message; $('#toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('#toast').hidden=true,5500); }
function empty(title, text, add=false) {return `<div class="empty-state"><div class="empty-icon">⌖</div><h3>${esc(title)}</h3><p>${esc(text)}</p>${add?'<button class="button primary" data-add>＋ Ajouter mon premier lieu</button>':''}</div>`;}

async function refresh() {
  if (loading) return;
  loading=true;
  try {
    state=await api('/api/state');
    $('#connection').classList.toggle('offline', !state.worker_alive);
    $('#connection').innerHTML=`<span class="live-dot"></span>${state.worker_alive ? 'Surveillance active' : 'Service de contrôle arrêté'}`;
    $('#server-error').hidden=state.worker_alive;
    if(!state.worker_alive) $('#server-error').textContent='Le service de contrôle est arrêté. Redémarrez le conteneur pour reprendre la surveillance.';
    render();
  } catch(e) {
    $('#connection').classList.add('offline');
    $('#connection').textContent='Serveur inaccessible';
    $('#server-error').textContent='Connexion interrompue. Les informations affichées peuvent être anciennes. Nouvelle tentative dans 5 secondes.';
    $('#server-error').hidden=false;
  } finally { loading=false; }
}

function render() {
  const active=state.places.filter(p=>!p.paused), outages=active.filter(p=>p.status==='outage'), works=active.filter(p=>p.status==='works'), unknown=active.filter(p=>p.status==='unknown');
  $('#stat-places').textContent=active.length;
  $('#stat-outages').textContent=outages.length;
  $('#stat-works').textContent=works.length;
  $('#stat-checks').textContent=new Intl.NumberFormat('fr-FR').format(state.counts.checks);
  $('#stat-cities').textContent=`${state.places.length} lieu${state.places.length>1?'x':''} enregistré${state.places.length>1?'s':''} · ${state.places.filter(p=>p.paused).length} en pause`;
  $('#stat-unknown').textContent=unknown.length ? `${unknown.length} lieu${unknown.length>1?'x':''} avec un statut inconnu` : 'Toutes les consultations sont conservées';
  $('#place-count').textContent=state.places.length;
  $('#history-count').textContent=state.counts.episodes;
  let title='Votre veille commence ici', desc='Ajoutez un premier lieu pour suivre les informations Enedis.';
  if(state.places.length) {
    if(!active.length) {title='Votre surveillance est en pause';desc='Réactivez un lieu pour reprendre les contrôles automatiques.';}
    else if(outages.length) {title=`${outages.length} lieu${outages.length>1?'x':''} avec un incident signalé`;desc='Consultez les informations Enedis et les lieux concernés ci-dessous.';}
    else if(unknown.length) {title='Des lieux restent à vérifier';desc='Les données indisponibles ou en attente ne permettent pas de conclure sur leur alimentation.';}
    else if(works.length) {title='Des travaux sont signalés sur votre périmètre';desc='Les secteurs concernés et les prévisions de rétablissement sont affichés ci-dessous.';}
    else {title='Aucune coupure signalée sur vos lieux actifs';desc='Selon les dernières réponses Enedis. La surveillance continue en arrière-plan.';}
  }
  $('#summary-title').textContent=title; $('#summary-description').textContent=desc;
  $('.summary-banner').dataset.state=outages.length?'outage':unknown.length?'unknown':works.length?'works':'ok';
  renderPlaces();renderHistory();renderActivity();renderChart();renderRecent();
}

const expandedPlaces = new Set();
function renderPlaceIncidents(p) {
  const episodes=(state.overview_episodes||state.history).filter(h=>h.place_id===p.id);
  const open=episodes.filter(h=>!h.ended_at);
  const displayed=open.length?open:episodes.slice(0,1);
  if(!displayed.length)return '';
  const uncertain=p.paused||p.status==='unknown';
  const heading=open.length?`${open.length} signalement${open.length>1?'s':''} ${uncertain?'sans fin confirmée':'non clôturé'+(open.length>1?'s':'')}`:'Dernier signalement clôturé';
  const visible=expandedPlaces.has(p.id)?displayed:displayed.slice(0,3);
  const cards=visible.map(h=>{
    const d=h.details||{},homes=Number.isInteger(d.households)&&d.households>0?`${new Intl.NumberFormat('fr-FR').format(d.households)} foyer(s) déclaré(s)`:'Foyers : non renseigné';
    return `<article class="overview-incident ${h.ended_at?'is-closed':''}" data-overview-episode="${h.id}"><div class="overview-incident-heading"><div>${badge(h.kind,h.title)}<span class="overview-episode-state">${h.ended_at?'Fin observée : '+fmt(h.ended_at,true):uncertain?'Dernier signalement connu · suivi '+(p.paused?'en pause':'incertain'):'Présent au dernier contrôle exploitable'}</span></div><button class="text-button" data-episode="${h.id}">Voir les détails ↗</button></div><p class="overview-streets">⌖ ${esc(h.streets||'Secteur non précisé')}</p><dl class="overview-dates"><div><dt>${h.source_started?'Début annoncé par Enedis':'Première détection'}</dt><dd>${esc(h.source_started||fmt(h.first_seen))}</dd></div><div><dt>Rétablissement annoncé</dt><dd>${esc(h.eta||'Non communiqué')}</dd></div><div><dt>Dernière présence confirmée</dt><dd>${fmt(h.last_seen)}</dd></div></dl><div class="overview-incident-footer"><span>${esc(homes)}</span>${d.source_id?`<span>Réf. Enedis : ${esc(d.source_id)}</span>`:''}</div></article>`;
  }).join('');
  return `<div class="place-incidents"><div class="place-incidents-heading"><strong>${esc(heading)}</strong>${uncertain&&open.length?'<span>Ces signalements ne confirment pas la situation actuelle.</span>':''}</div>${cards}${displayed.length>3?`<button class="text-button overview-expand" data-action="expand" data-id="${p.id}" aria-expanded="${expandedPlaces.has(p.id)}">${expandedPlaces.has(p.id)?'Réduire la liste':`Afficher les ${displayed.length-3} autres signalements`}</button>`:''}</div>`;
}

function renderPlaces() {
  const q=$('#place-search').value.toLocaleLowerCase('fr');
  const places=state.places.filter(p=>(filter==='all'||(!p.paused&&p.status===filter))&&`${p.label} ${p.nickname}`.toLocaleLowerCase('fr').includes(q));
  if(!places.length) {$('#places-list').innerHTML=state.places.length?empty('Aucun lieu correspondant','Essayez un autre filtre ou une autre recherche.'):empty('Tous vos lieux, sous surveillance','Ajoutez une ville, votre maison ou votre bureau. Nous nous chargeons des vérifications.',true);return;}
  $('#places-list').innerHTML=places.map(p=>{
    const placeState=p.paused?'paused':p.status;
    const busy=state.running===p.id;
    const next=p.paused?'Surveillance suspendue':busy?'Contrôle en cours…':p.next_check<=state.server_time?'Contrôle à venir':`Prochain : ${fmt(p.next_check,true)}`;
    return `<section class="place-group"><article class="place-row"><div class="place-identity"><span class="place-icon">⌖</span><div><div class="place-title">${esc(p.nickname||p.label)}</div>${p.nickname?`<div class="place-subtitle">${esc(p.label)}</div>`:''}<a class="place-kind" href="${esc(p.source_url)}" target="_blank" rel="noopener noreferrer">Voir sur Enedis ↗</a></div></div><div class="place-status" title="${esc(p.message)}">${badge(placeState)}<button class="text-button incident-link" data-action="incidents" data-id="${p.id}">Voir les signalements →</button><button class="text-button incident-link" data-action="status-api" data-id="${p.id}">URL de supervision ↗</button></div><div class="place-check">${p.last_checked?fmt(p.last_checked,true):'Pas encore vérifié'}<small>${esc(next)}</small></div><div class="frequency">Toutes les ${p.interval_minutes} min</div><div class="row-actions"><button class="icon-button" data-action="pause" data-id="${p.id}" aria-label="${p.paused?'Reprendre':'Suspendre'} ${esc(p.nickname||p.label)}" title="${p.paused?'Reprendre':'Suspendre'}">${p.paused?'▷':'Ⅱ'}</button><button class="icon-button" data-action="edit" data-id="${p.id}" aria-label="Modifier ${esc(p.nickname||p.label)}" title="Modifier">⚙</button><button class="icon-button" data-action="archive" data-id="${p.id}" aria-label="Archiver ${esc(p.nickname||p.label)}" title="Archiver">×</button></div></article>${renderPlaceIncidents(p)}</section>`;
  }).join('');
}

function renderHistory() {
  const select=$('#history-place'), previous=select.value;
  const allPlaces=new Map([...state.history.map(h=>[h.place_id, {id:h.place_id,label:h.nickname||h.label, archived:h.archived}]),...state.places.map(p=>[p.id,{id:p.id,label:p.nickname||p.label}])]);
  select.innerHTML='<option value="">Tous les lieux</option>'+[...allPlaces.values()].map(p=>`<option value="${p.id}">${esc(p.label)}${p.archived?' (archivé)':''}</option>`).join('');
  select.value=allPlaces.has(Number(previous))?previous:'';
  const mode=$('#history-status').value;
  const rows=state.history.filter(h=>(!select.value||h.place_id===Number(select.value))&&(mode==='all'||(mode==='closed'?!!h.ended_at:!h.ended_at)));
  if(!rows.length){$('#history-list').innerHTML=empty('Aucune panne dans cet historique','Les incidents et travaux détectés apparaîtront ici automatiquement.');return;}
  $('#history-list').innerHTML=`<div class="table-wrap"><table><thead><tr><th>Lieu / signalement</th><th>Type</th><th>Première détection</th><th>Fin observée</th><th>Rétablissement annoncé</th><th>Détails</th></tr></thead><tbody>${rows.map(h=>`<tr><td><strong>${esc(h.nickname||h.label)}</strong><span class="table-detail">${esc(h.title)} · ${esc(h.streets)}</span>${h.source_started?`<span class="table-detail">Début Enedis : ${esc(h.source_started)}</span>`:''}${h.archived?'<span class="table-detail">Lieu archivé</span>':''}</td><td>${badge(h.kind,h.kind==='outage'?'Incident':'Travaux')}</td><td class="table-date">${fmt(h.first_seen)}<span class="table-detail">Dernière présence : ${fmt(h.last_seen)}</span></td><td class="table-date">${h.ended_at?fmt(h.ended_at):`<span class="chip">${h.archived||h.paused||h.place_status==='unknown'?'Suivi interrompu / incertain':'Non clôturé'}</span>`}</td><td class="table-date">${esc(h.eta||'Non communiqué')}</td><td><button class="button secondary" data-episode="${h.id}">Voir les détails ↗</button></td></tr>`).join('')}</tbody></table></div>`;
}

function renderActivity() {
  $('#activity-list').innerHTML=state.observations.length?`<div class="table-wrap"><table><thead><tr><th>Date du contrôle</th><th>Lieu</th><th>Résultat</th><th>Détail</th></tr></thead><tbody>${state.observations.map(o=>`<tr><td class="table-date">${fmt(o.checked_at)}</td><td>${esc(o.nickname||o.label)}</td><td>${badge(o.status)}</td><td><span class="table-detail">${esc(o.message)}</span></td></tr>`).join('')}</tbody></table></div>`:empty('Aucun contrôle pour le moment','Le premier contrôle sera lancé après l’ajout d’un lieu.');
}
function renderChart() {
  const dateKey=d=>new Intl.DateTimeFormat('fr-CA',{timeZone:'Europe/Paris',year:'numeric',month:'2-digit',day:'2-digit'}).format(d);
  const now=new Date((state.server_time||Date.now()/1000)*1000);
  const days=Array.from({length:7},(_,i)=>{const d=new Date(now);d.setUTCDate(d.getUTCDate()-(6-i));return {date:d,key:dateKey(d),count:0};});
  for(const day of days)day.count=state.chart?.[day.key]||0;
  const max=Math.max(1,...days.map(d=>d.count));
  $('#chart').innerHTML=days.map((d,i)=>`<div class="chart-column" title="${esc(d.key)} : ${d.count} épisode(s)"><span class="chart-value">${d.count}</span><div class="chart-bar" data-height="${Math.max(2, Math.round(d.count/max*112))}"></div><span class="chart-day">${i===6?'Auj.':esc(new Intl.DateTimeFormat('fr-FR',{timeZone:'Europe/Paris',weekday:'short'}).format(d.date))}</span></div>`).join('');
  // Valeurs numériques issues des comptages, appliquées via la propriété DOM.
  document.querySelectorAll('.chart-bar').forEach(el=>{el.style.height=`${el.dataset.height}px`;});
}
function renderRecent() {
  $('#recent-events').innerHTML=state.history.length?state.history.slice(0,3).map(h=>`<div class="recent-item"><span class="event-dot ${h.kind==='outage'?'coral-bg':'amber-bg'}"></span><div><button class="text-button recent-title" data-episode="${h.id}">${esc(h.title)} ↗</button><div class="recent-text">${esc(h.nickname||h.label)}</div></div><span class="recent-date">${fmt(h.first_seen,true)}</span></div>`).join(''):'<div class="recent-empty"><span>◷</span>Les prochains signalements apparaîtront ici.<br>Votre historique se construit au fil des contrôles.</div>';
}
function setView(view) {
  currentView=view;
  for(const name of ['overview','history','activity']) $(`#${name}-view`).hidden=name!==view;
  document.querySelectorAll('[data-view]').forEach(el=>{el.classList.toggle('active',el.dataset.view===view);el.setAttribute('aria-current',el.dataset.view===view?'page':'false');});
  const titles={overview:'Vue d’ensemble',history:'Historique des pannes',activity:'Journal des contrôles'};
  $('#page-title').innerHTML=esc(titles[view])+'<span class="heading-dot">.</span>';
  $('#breadcrumb-current').textContent=titles[view];
  $('#page-description').textContent={overview:'Gardez un œil sur l’électricité, à toutes vos adresses.',history:'Retrouvez les signalements et leur évolution dans le temps.',activity:'Suivez chaque vérification et la disponibilité des données.'}[view];
}

function openDialog(p=null) {
  editId=p?.id||null;selected=null;searchVersion++;searchAbort?.abort();clearTimeout(searchTimer);
  $('#place-form').reset();$('#form-error').textContent='';$('#suggestions').replaceChildren();$('#selected-location').hidden=true;
  $('#location-fields').hidden=!!p;
  $('#dialog-title').textContent=p?'Modifier ce lieu':'Ajouter un lieu';
  $('#dialog-description').textContent=p?p.label:'Recherchez une ville ou une adresse et sélectionnez un résultat.';
  $('#save-place').textContent=p?'Enregistrer les modifications':'Activer la surveillance';
  if(p){$('#nickname-input').value=p.nickname; const select=$('#interval-input');if(![...select.options].some(o=>Number(o.value)===p.interval_minutes)){const o=new Option(`${p.interval_minutes} minutes`,p.interval_minutes);select.add(o);}select.value=String(p.interval_minutes);}
  $('#place-dialog').showModal();
}
async function searchLocations() {
  const value=$('#location-input').value.trim(), version=++searchVersion;
  searchAbort?.abort();searchAbort=new AbortController();
  if(value.length<3){$('#suggestions').replaceChildren();return;}
  $('#suggestions').innerHTML='<p>Recherche auprès de l’IGN…</p>';
  try {
    const response=await fetch('/api/search?q='+encodeURIComponent(value),{signal:searchAbort.signal});const results=await response.json();
    if(version!==searchVersion)return;
    if(!response.ok)throw Error(results.error||'Recherche indisponible');
    $('#suggestions').replaceChildren();
    if(!results.length){$('#suggestions').innerHTML='<p>Aucun résultat. Précisez la ville ou le code postal.</p>';return;}
    for(const place of results){const button=document.createElement('button');button.type='button';button.textContent=`${place.label} · ${place.type==='municipality'?'Commune':'Rue / adresse'} · ${place.postcode}`;button.addEventListener('click',()=>{selected=place;$('#location-input').value=place.label;$('#suggestions').replaceChildren();$('#selected-location').textContent=`✓ ${place.label} — ${place.type==='municipality'?'Toute la commune':'Vérification à l’échelle de la rue'}`;$('#selected-location').hidden=false;});$('#suggestions').append(button);}
  } catch(e){if(e.name!=='AbortError'&&version===searchVersion)$('#suggestions').innerHTML=`<p>${esc(e.message)}</p>`;}
}

document.querySelectorAll('[data-view]').forEach(el=>el.addEventListener('click',()=>setView(el.dataset.view)));
document.querySelectorAll('[data-filter]').forEach(el=>el.addEventListener('click',()=>{filter=el.dataset.filter;document.querySelectorAll('[data-filter]').forEach(b=>b.classList.toggle('active',b===el));renderPlaces();}));
$('#place-search').addEventListener('input',renderPlaces);
$('#history-place').addEventListener('change',renderHistory);
$('#history-status').addEventListener('change',renderHistory);
$('#see-history').addEventListener('click',()=>setView('history'));
$('#add-place').addEventListener('click',()=>openDialog());
for(const id of ['close-dialog','cancel-dialog']) $(`#${id}`).addEventListener('click',()=>$('#place-dialog').close());
$('#location-input').addEventListener('input',()=>{selected=null;searchVersion++;searchAbort?.abort();$('#selected-location').hidden=true;clearTimeout(searchTimer);searchTimer=setTimeout(searchLocations,400);});
$('#place-form').addEventListener('submit',async e=>{
  e.preventDefault();$('#form-error').textContent='';
  if(!editId&&!selected){$('#form-error').textContent='Sélectionnez une ville ou une adresse dans les suggestions IGN.';return;}
  const button=$('#save-place');button.disabled=true;
  try {const body={nickname:$('#nickname-input').value.trim(),interval_minutes:Number($('#interval-input').value)};
    if(editId) await api(`/api/places/${editId}`,body);else await api('/api/places',{...body,place:selected});
    $('#place-dialog').close();toast(editId?'Les réglages du lieu ont été enregistrés.':'Lieu ajouté. Le premier contrôle va démarrer.');await refresh();
  }catch(error){$('#form-error').textContent=error.message;}finally{button.disabled=false;}
});
$('#places-list').addEventListener('click',async e=>{
  if(e.target.closest('[data-add]'))return openDialog();
  const button=e.target.closest('[data-action]');if(!button)return;
  const p=state.places.find(p=>p.id===Number(button.dataset.id));if(!p)return;
  if(button.dataset.action==='expand'){if(expandedPlaces.has(p.id))expandedPlaces.delete(p.id);else expandedPlaces.add(p.id);renderPlaces();return;}
  if(button.dataset.action==='status-api')return openStatusLink(p);
  if(button.dataset.action==='edit')return openDialog(p);
  if(button.dataset.action==='incidents'){setView('history');$('#history-place').value=String(p.id);$('#history-status').value='all';renderHistory();return;}
  if(button.dataset.action==='archive'){
    const dialog=$('#archive-dialog');dialog.returnValue='cancel';dialog.showModal();
    dialog.addEventListener('close',async()=>{if(dialog.returnValue!=='archive')return;try{await api(`/api/places/${p.id}/archive`,{});toast('Lieu archivé. Son historique est conservé.');await refresh();}catch(error){toast(error.message);}},{once:true});return;
  }
  button.disabled=true;
  try {await api(`/api/places/${p.id}`,{paused:!p.paused});toast(p.paused?'Surveillance reprise.':'Surveillance suspendue.');await refresh();}catch(error){toast(error.message);}finally{button.disabled=false;}
});
$('#check-all').addEventListener('click',async()=>{
  const button=$('#check-all');button.disabled=true;
  try{const r=await api('/api/check',{});toast(r.queued?'Vérification demandée. Les lieux contrôlés il y a moins d’une minute sont ignorés.':'Aucun lieu actif à vérifier.');await refresh();}catch(error){toast(error.message);}finally{button.disabled=false;}
});
let incidentRequest=0;
const numberOfHomes = value => Number.isInteger(value)&&value>0 ? new Intl.NumberFormat('fr-FR').format(value) : 'Non renseigné';
function observationDuration(start,end) {
  const minutes=Math.max(0,Math.floor((end-start)/60));
  return minutes<60?`${minutes} min`:`${Math.floor(minutes/60)} h ${minutes%60} min`;
}
function incidentState(h) {
  if(h.ended_at) return 'Clôturé lors d’un contrôle';
  if(h.archived) return 'Suivi archivé · fin non confirmée';
  if(h.paused) return 'Suivi en pause · fin non confirmée';
  if(h.place_status==='unknown') return 'Source indisponible · fin non confirmée';
  return 'Non clôturé';
}
async function openIncident(id) {
  const request=++incidentRequest, dialog=$('#incident-dialog');
  $('#incident-content').innerHTML='<p class="muted">Chargement des informations enregistrées…</p>';
  if(!dialog.open)dialog.showModal();
  try {
    const h=await api(`/api/episodes/${id}`);
    if(request!==incidentRequest||!dialog.open)return;
    const details=h.details||{}, addresses=details.addresses?.length?details.addresses:[{label:h.streets,households:null}];
    const network=h.title.includes('HTA')?'Moyenne tension (HTA)':h.title.includes('BT')?'Basse tension (BT)':'Non précisé';
    const field=(label,value)=>`<div><dt>${esc(label)}</dt><dd>${esc(value??'Non renseigné')}</dd></div>`;
    const observations=h.observations||[];
    const timeline=observations.map(o=>{
      let text=o.event?`Signalement présent · ${o.event.title}`:o.status==='unknown'?`Vérification incertaine · ${o.message}`:'Signalement absent de la réponse exploitable';
      return `<li><span class="timeline-point ${o.status==='unknown'?'':'teal-bg'}"></span><div><time>${fmt(o.checked_at)}</time><p>${esc(text)}</p>${o.event?.eta?`<small>Rétablissement annoncé : ${esc(o.event.eta)}</small>`:''}</div></li>`;
    }).join('');
    const sourceId=details.source_id||h.source_reference;
    $('#incident-content').innerHTML=`<div class="incident-intro"><div>${badge(h.kind,h.kind==='outage'?'Incident':'Travaux')}<h3>${esc(h.nickname||h.label)}</h3><p>${esc(h.label)}</p></div><span class="incident-state">${esc(incidentState(h))}</span></div>
      <div class="incident-eta"><span>Rétablissement annoncé par Enedis</span><strong>${esc(h.eta||'Non communiqué')}</strong><p>Prévision enregistrée au dernier contrôle ayant observé ce signalement. Elle ne confirme pas le rétablissement.</p></div>
      <dl class="incident-fields">${field('Type de signalement',h.title)}${field('Réseau concerné',network)}${field('Référence Enedis',sourceId||'Non fournie pour ce résultat')}${field('État communiqué par Enedis',details.source_state)}${field('Début annoncé par Enedis',h.source_started)}${field('Code INSEE',details.insee)}${field('Foyers coupés selon Enedis',numberOfHomes(details.households))}${field('Périmètre du résultat',details.scope==='street'?'Rue (sans mesure par logement)':details.scope==='municipality'?'Opération dans la commune':'Données antérieures à la fiche détaillée')}</dl>
      <section class="incident-section"><h3>Rues et secteurs concernés <span class="count-chip">${addresses.length}</span></h3><ul class="incident-addresses">${addresses.map(a=>`<li><span>⌖</span><div>${esc(a.label||'Non renseigné')}${Number.isInteger(a.households)&&a.households>0?`<small>${numberOfHomes(a.households)} foyer(s) déclaré(s)</small>`:''}</div></li>`).join('')}</ul><p class="help-text">Un nombre nul ou absent dans la source est affiché comme non renseigné ; il ne prouve pas l’absence de logements touchés.</p></section>
      <section class="incident-section"><h3>Suivi de cet épisode</h3><dl class="incident-fields">${field('Première détection',fmt(h.first_seen))}${field('Dernière présence confirmée',fmt(h.last_seen))}${field('Fin observée',h.ended_at?fmt(h.ended_at):'Non confirmée')}${field('Intervalle entre première et dernière présence',observationDuration(h.first_seen,h.last_seen))}</dl><p class="help-text">Ces dates sont des observations du tableau de bord. Cet intervalle n’est pas une mesure de la durée réelle de la coupure.</p></section>
      <section class="incident-section"><h3>Historique des vérifications</h3><p class="help-text">Jusqu’aux 200 derniers contrôles de ce lieu pendant l’épisode, du plus récent au plus ancien. Les prévisions successives restent visibles.</p><ol class="incident-timeline">${timeline||'<li>Aucune observation disponible.</li>'}</ol></section>
      ${!Object.keys(details).length?'<p class="help-text">Cet épisode a été enregistré avant l’ajout des détails. Les épisodes encore présents seront enrichis au prochain contrôle réussi.</p>':''}
      <div class="dialog-footer"><a class="button secondary" href="${esc(h.source_url)}" target="_blank" rel="noopener noreferrer">Consulter le lieu sur Enedis ↗</a></div>`;
  }catch(error){if(request===incidentRequest)$('#incident-content').innerHTML=`<p class="form-error">${esc(error.message)}</p>`;}
}
$('#close-incident').addEventListener('click',()=>$('#incident-dialog').close());
$('#incident-dialog').addEventListener('close',()=>incidentRequest++);
document.addEventListener('click',e=>{const button=e.target.closest('[data-episode]');if(button)openIncident(Number(button.dataset.episode));});
refresh();setInterval(refresh,5000);

$('#logout').addEventListener('click',async()=>{try{await api('/api/auth/logout',{});window.location.replace('/login');}catch(error){toast(error.message);}});

let statusLinkPlace=null,statusLinkPath=null,statusLinkRequest=0;
function renderStatusLink(info) {
  const jsonUrl=statusLinkPath?new URL(statusLinkPath,window.location.origin).href:null;
  $('#status-link-content').innerHTML=`<div class="status-link-state"><span class="tiny-dot ${info.enabled?'teal-bg':''}"></span>${info.enabled?'Une URL de supervision est active.':'L’accès par URL est désactivé.'}${info.created_at?`<small>Créée le ${fmt(info.created_at)}</small>`:''}</div>
    ${jsonUrl?`<div class="status-link-secret"><p>Copiez ces URL maintenant. Le jeton ne sera plus affiché après la fermeture de cette fenêtre.</p><label class="field-label" for="status-json-url">État JSON · HTTP 200 pour une URL valide</label><div class="status-url-row"><input id="status-json-url" readonly value="${esc(jsonUrl)}" aria-label="URL JSON de supervision"><button class="button secondary" data-copy-status="status-json-url">Copier</button></div><label class="field-label" for="status-probe-url">Sonde HTTP · 200 si état normal, 503 sinon</label><div class="status-url-row"><input id="status-probe-url" readonly value="${esc(jsonUrl+'?mode=probe')}" aria-label="URL de la sonde HTTP"><button class="button secondary" data-copy-status="status-probe-url">Copier</button></div><button class="button secondary status-test-button" id="test-status-link">Tester l’état sans connexion</button><pre id="status-link-preview" hidden></pre></div>`:info.enabled?'<p class="help-text">Le jeton n’est pas conservé en clair. Si vous avez perdu l’URL, régénérez-la puis mettez à jour votre outil de supervision.</p>':'<p class="help-text">Créez une URL pour intégrer ce lieu à votre outil de supervision.</p>'}
    <p class="help-text">Un contrôle suspendu, absent, trop ancien ou indisponible ne sera jamais présenté comme un état normal. La sonde utilise les derniers résultats enregistrés et ne déclenche pas de contrôle Enedis.</p>
    <div class="dialog-footer">${info.enabled?'<button class="button secondary" data-status-link-action="revoke">Révoquer l’URL</button><button class="button primary" data-status-link-action="rotate">Régénérer l’URL</button>':'<button class="button primary" data-status-link-action="create">Créer l’URL de supervision</button>'}</div>
    ${info.enabled?'<p class="help-text">Révoquer ou régénérer invalide immédiatement l’ancienne URL.</p>':''}`;
}
async function openStatusLink(p) {
  statusLinkPlace=p.id;statusLinkPath=null;const request=++statusLinkRequest;
  $('#status-link-place').textContent=p.nickname||p.label;
  $('#status-link-error').textContent='';$('#status-link-content').innerHTML='<p class="muted">Chargement…</p>';
  $('#status-link-dialog').showModal();
  try{const info=await api(`/api/places/${p.id}/status-link`);if(request===statusLinkRequest)renderStatusLink(info);}
  catch(error){if(request===statusLinkRequest)$('#status-link-error').textContent=error.message;}
}
$('#close-status-link').addEventListener('click',()=>$('#status-link-dialog').close());
$('#status-link-dialog').addEventListener('close',()=>{statusLinkRequest++;statusLinkPlace=null;statusLinkPath=null;$('#status-link-content').replaceChildren();});
$('#status-link-content').addEventListener('click',async e=>{
  const action=e.target.closest('[data-status-link-action]');
  if(action){
    const request=statusLinkRequest,controls=[...document.querySelectorAll('[data-status-link-action]')];controls.forEach(button=>button.disabled=true);$('#status-link-error').textContent='';
    try{const info=await api(`/api/places/${statusLinkPlace}/status-link`,{action:action.dataset.statusLinkAction});if(request!==statusLinkRequest)return;statusLinkPath=info.path||null;renderStatusLink(info);}
    catch(error){if(request===statusLinkRequest)$('#status-link-error').textContent=error.message;}
    finally{controls.forEach(button=>button.disabled=false);}
    return;
  }
  const copy=e.target.closest('[data-copy-status]');
  if(copy){
    const input=document.getElementById(copy.dataset.copyStatus);
    try{await navigator.clipboard.writeText(input.value);toast('URL copiée dans le presse-papiers.');}
    catch{input.focus();input.select();toast('URL sélectionnée : utilisez Ctrl+C pour la copier.');}
    return;
  }
  if(e.target.closest('#test-status-link')&&statusLinkPath){
    const request=statusLinkRequest;const preview=$('#status-link-preview');preview.hidden=false;preview.textContent='Consultation de l’API…';
    try{const response=await fetch(statusLinkPath,{credentials:'omit'});const result=await response.json();if(request===statusLinkRequest)preview.textContent=`HTTP ${response.status}\n`+JSON.stringify(result,null,2);}
    catch{if(request===statusLinkRequest)preview.textContent='Impossible de joindre l’API.';}
  }
});

$('#open-account').addEventListener('click',async()=>{
  $('#account-form').reset();$('#account-error').textContent='';$('#save-account').disabled=true;
  $('#account-dialog').showModal();
  try{const session=await api('/api/auth/session');if(!session.authenticated){window.location.replace('/login');return;}$('#account-username').value=session.username;$('#save-account').disabled=false;}
  catch(error){$('#account-error').textContent=error.message;}
});
for(const id of ['close-account','cancel-account'])$(`#${id}`).addEventListener('click',()=>$('#account-dialog').close());
$('#account-dialog').addEventListener('close',()=>{$('#account-form').reset();$('#account-error').textContent='';});
$('#account-form').addEventListener('submit',async e=>{
  e.preventDefault();$('#account-error').textContent='';
  const password=$('#account-new-password').value,confirmation=$('#account-confirm-password').value;
  if(password!==confirmation){$('#account-error').textContent='La confirmation du nouveau mot de passe est différente.';return;}
  $('#save-account').disabled=true;
  try{await api('/api/auth/account',{username:$('#account-username').value.trim(),current_password:$('#account-current-password').value,new_password:password,confirm_password:confirmation});$('#account-form').reset();window.location.replace('/login');}
  catch(error){$('#account-error').textContent=error.message;}
  finally{$('#save-account').disabled=false;}
});
