const $ = id => document.getElementById(id);
let chatAiEnabled=false;
let chatBusy=false;
let conversationId=null;
let contextDirty=false;
let currentUser=null;
let publicServices={weather:false,holidays:false};
let manualLocation=null,locationMemory=null;
let authRevision=0;
const broadcastAuth=()=>localStorage.setItem('butler-auth-event',Date.now()+'-'+Math.random());
let bootstrapAccount=false;
const conversationKey=()=>'butler-conversation-'+(currentUser?.id||'anonymous');
const iconNode=name=>{const el=document.createElementNS('http://www.w3.org/2000/svg','svg');el.classList.add('icon');el.setAttribute('aria-hidden','true');const use=document.createElementNS('http://www.w3.org/2000/svg','use');use.setAttribute('href','#i-'+name);el.append(use);return el;};
const node=(tag,className,text)=>{const el=document.createElement(tag);if(className)el.className=className;if(text!==undefined)el.textContent=text;return el;};
function welcome(){ $('messages').replaceChildren($('welcome-template').content.cloneNode(true)); }
function setMic(active){$('mic').classList.toggle('recording',active);$('mic').replaceChildren(active?node('span','','■'):iconNode('mic'));$('mic').setAttribute('aria-label',active?'Sprachgespräch beenden':'Sprachgespräch starten');}
function answerBubble(result){
  const text=result.ai_issue&&result.answer.includes('KI-Auswertung einschalten')?'Die passenden Unterlagen sind vorhanden. Die KI-Auswertung ist gerade nicht verfügbar.':result.answer;
  const answer=bubble(text,'assistant');
  answer.querySelector('.message-role').textContent=result.answer_mode==='live'?'BUTLER · LIVE-ABFRAGE':result.external_used?'BUTLER · KI-ANTWORT':'BUTLER · LOKALE ANTWORT';
  if(result.ai_issue){const status=node('small','answer-status',result.ai_issue.includes('429')?'KI-Anfragelimit erreicht · lokale Antwort':'KI-Auswertung nicht verfügbar · lokale Antwort');status.title=result.ai_issue;answer.append(status);}
  if(result.sources?.length||result.diagnostics?.length){const evidence=node('button','evidence-link',result.sources?.length?'Belege ansehen · '+result.sources.length:'Hinweise zur Suche');evidence.type='button';evidence.append(iconNode('arrow'));evidence.onclick=()=>showEvidence(result.sources||[],result.diagnostics||[]);answer.append(evidence);}
  return answer;
}
async function loadConversations(){
  const actor=currentUser?.id;if(!actor)return;
  const conversations=await api('conversations');if(currentUser?.id!==actor)return;
  for(const id of ['conversation-list','mobile-conversation-list']){
    const list=$(id);list.replaceChildren();
    if(!conversations.length) list.append(node('p','history-empty','Deine Gespräche erscheinen hier.'));
    for(const conversation of conversations){const button=node('button','',conversation.title);button.type='button';button.title=conversation.title;button.disabled=chatBusy;button.setAttribute('aria-current',String(conversation.id===conversationId));button.onclick=()=>openConversation(conversation.id);list.append(button);}
  }
}
async function openConversation(id){
  if(chatBusy)return;const actor=currentUser?.id;if(!actor)return;
  try{const conversation=await api('conversations/'+encodeURIComponent(id));if(currentUser?.id!==actor)return;voiceConversation=false;window.speechSynthesis?.cancel();if(recorder?.state==='recording')recorder.stop();conversationId=id;localStorage.setItem(conversationKey(),id);$('messages').replaceChildren();for(const message of conversation.messages){if(message.role==='user')bubble(message.text,'user');else answerBubble({...message.payload,answer:message.text});}if(!conversation.messages.length)welcome();$('history-dialog').close();location.hash='chat';await loadConversations();}
  catch(e){notice(e.message);}
}
function newConversation(){
  if(chatBusy)return;
  voiceConversation=false;window.speechSynthesis?.cancel();if(recorder?.state==='recording')recorder.stop();setMic(false);conversationId=null;localStorage.removeItem(conversationKey());$('question').value='';$('question').style.height='';welcome();location.hash='chat';loadConversations().catch(()=>{});$('question').focus();
}
const sourceDefinitions=[
  {id:'imap',name:'E-Mails',icon:'mail',color:'blue',description:'Nachrichten, Bestellungen und Korrespondenz',unit:'Nachrichten im lokalen Index'},
  {id:'paperless',name:'Dokumente',icon:'sources',color:'peach',description:'Deine Dokumente aus Paperless-ngx',unit:'Dokumente im lokalen Index'},
  {id:'ntfy',name:'Ereignisse',icon:'notification',color:'lilac',description:'Benachrichtigungen und zeitliche Zusammenhänge',unit:'lokal gespeicherte Meldungen'},
  {id:'icloud_calendar',name:'Kalender',icon:'history',color:'blue',description:'Termine und verfügbare Zeit aus iCloud',unit:'Termine im letzten Abrufzeitraum'},
  {id:'icloud_contacts',name:'Kontakte',icon:'mail',color:'lilac',description:'Personen und Kontaktdaten aus iCloud',unit:'Kontakte im letzten vollständigen Abruf'},
  {id:'iobroker',name:'Smart Home',icon:'home',color:'green',description:'Passende Zustände aus deinem ioBroker',unit:'Kontext bei passenden Fragen'}
];
function renderSources(status,sync){
  $('source-preview').replaceChildren();$('source-grid').replaceChildren();let configuredCount=0;
  for(const definition of sourceDefinitions){
    const id=definition.id,health=status.sources[id==='ntfy'?'ntfy_history':id]||{};
    const configured=status.configured_sources?.[id]??Boolean(savedConfig&&(id==='imap'?savedConfig.imap_host:id==='paperless'?savedConfig.paperless_url:id==='ntfy'?(savedConfig.ntfy_history_urls||savedConfig.ntfy_url):id.startsWith('icloud_')?savedConfig[id+'_enabled']:savedConfig.iobroker_url));
    if(configured)configuredCount++;
    const count=id.startsWith('icloud_')?(health.fetched||0):(sync.indexed[id]||0);
    const statusText=!configured?'Noch nicht verbunden':health.disabled?'Abruf pausiert':health.error||health.errors?'Abruf prüfen':Object.keys(health).length?'Verbunden':'Abruf wird vorbereitet';
    const state=!configured||health.disabled?'off':health.error||health.errors?'error':'ok';
    const preview=node('a','source-preview-item');preview.href='#sources';preview.dataset.state=state;
    const badge=node('span','source-icon '+definition.color);badge.append(iconNode(definition.icon));
    const text=node('span');text.append(node('strong','',definition.name),node('small','',id==='iobroker'?(configured?'Bei passenden Fragen':'Noch nicht verbunden'):count.toLocaleString('de-DE')+' '+(id==='imap'?'Nachrichten':id==='paperless'?'Dokumente':id==='icloud_calendar'?'Termine':id==='icloud_contacts'?'Kontakte':'Meldungen')));
    preview.append(badge,text,node('span','tiny-dot'));$('source-preview').append(preview);
    const card=node('article','source-card');const heading=node('div','source-card-heading');const badge2=badge.cloneNode(true);const labels=node('div');labels.append(node('h2','',definition.name),node('p','source-card-description',definition.description));heading.append(badge2,labels);
    const footer=node('div','source-card-footer');const stateLabel=node('span','',statusText);stateLabel.dataset.state=state;stateLabel.prepend(node('span','dot'));const settings=node('a','','Verbindung verwalten');settings.href='#service-'+id;settings.append(iconNode('arrow'));footer.append(stateLabel,settings);
    card.append(heading,node('div','source-card-count',id==='iobroker'?(configured?'Auf Anfrage':'—'):count.toLocaleString('de-DE')),node('span','source-card-label',definition.unit),footer);$('source-grid').append(card);
  }
  $('nav-source-count').textContent=configuredCount;
  const totalConfigured=configuredCount+Number(!!status.configured_sources?.weather)+Number(!!status.configured_sources?.holidays);
  $('chat-source-badge').textContent=totalConfigured?totalConfigured+' QUELLEN VERBUNDEN':'QUELLEN IN DEN EINSTELLUNGEN VERBINDEN';
  const archives=sync.states.filter(state=>state.key.startsWith('mail-archive:'));
  const total=archives.reduce((sum,s)=>sum+(s.total||0),0),indexed=archives.reduce((sum,s)=>sum+(s.indexed||0),0),percent=total?Math.min(100,Math.round(indexed/total*100)):0;
  $('source-progress').value=percent;$('archive-percent').textContent=total?percent+' %':'—';
  $('source-archive-status').textContent=total?indexed.toLocaleString('de-DE')+' von '+total.toLocaleString('de-DE')+' E-Mails aus '+archives.length+' Ordnern erfasst'+(archives.every(s=>s.complete)?' · Import vollständig':' · wird weiter eingelesen'):'Noch kein E-Mail-Archiv erfasst.';
}
function renderInitialImport(status,sync) {
  const rows=[],configured=status.configured_sources||{},initial=sync.initial_import||{},paused=status.worker_enabled===false;
  if(configured.imap){
    const archives=initial.mail||[];
    if(!archives.length||!archives.every(s=>s.complete)){
      const total=archives.reduce((n,s)=>n+(s.total||0),0),indexed=archives.reduce((n,s)=>n+(s.indexed||0),0);
      rows.push({name:'E-Mails',total:total||null,indexed,error:archives.some(s=>s.error||s.errors)||status.sources?.mail_archive?.error,disabled:paused||initial.mail_enabled===false});
    }
  }
  if(configured.paperless){
    const state=initial.paperless;
    if(!state?.complete)rows.push({name:'Dokumente',total:state?.total,indexed:state?.indexed||0,disabled:paused,error:state?.error||state?.phase==='error'});
  }
  if(configured.ntfy){
    const topics=initial.ntfy||[],complete=topics.length===initial.ntfy_topics&&topics.every(s=>!s.error&&s.checked);
    if(!complete)rows.push({name:'Benachrichtigungen',total:null,indexed:sync.indexed.ntfy||0,disabled:paused||initial.ntfy_enabled===false,error:topics.some(s=>s.error)||status.sources?.ntfy_history?.error});
  }
  for(const [id,name] of [['icloud_calendar','iCloud-Kalender'],['icloud_contacts','iCloud-Kontakte']]){const health=status.sources?.[id];if(configured[id]&&!health?.checked)rows.push({name,total:null,indexed:health?.fetched||0,error:health?.error,disabled:paused});}
  const panel=$('initial-import'),list=$('initial-import-rows');panel.hidden=!rows.length;list.replaceChildren();
  for(const row of rows){
    const line=node('div','initial-import-row'),heading=node('div','initial-import-label');
    const count=row.total!=null?row.indexed.toLocaleString('de-DE')+' / '+row.total.toLocaleString('de-DE'):row.indexed?row.indexed.toLocaleString('de-DE')+' erfasst':'Bestand wird ermittelt';
    heading.append(node('span','',row.name),node('span','',row.error?'Unterbrochen · erneuter Versuch folgt':row.disabled?'Import pausiert':count));
    const bar=document.createElement('progress');bar.max=100;bar.setAttribute('aria-label',row.name+' indexieren');
    if(row.total!=null)bar.value=row.total?Math.min(100,row.indexed/row.total*100):100;
    if(row.error||row.disabled){bar.value=row.total?Math.min(99,row.indexed/row.total*100):0;line.classList.add('paused');}
    line.append(heading,bar);list.append(line);
  }
}
function validationMessage(details) {
  const labels={iobroker_url:'ioBroker-Adresse',iobroker_patterns:'Datenpunkt-Muster',iobroker_api:'API-Adapter'};
  if(!Array.isArray(details))return 'Bitte prüfe deine Eingaben.';
  const messages=details.map(error=>{
    const field=error.loc?.at(-1),label=labels[field]||'Eingabe';
    if(field==='iobroker_url')return label+': vollständige API-Adresse mit http:// oder https:// verwenden, ohne Zugangsdaten oder URL-Parameter.';
    if(field==='iobroker_patterns')return label+': ein bis zehn Muster angeben, mit Komma oder Zeilenumbruch getrennt.';
    if(field==='iobroker_api')return label+': Simple-API oder REST-API auswählen.';
    return label+': ungültiger Wert. Bitte prüfen.';
  });
  return [...new Set(messages)].join(' ')||'Bitte prüfe deine Eingaben.';
}
async function api(path, options = {}) {
  const revision=authRevision;
  const response = await fetch('/api/' + path, {...options, credentials:'same-origin', headers: {'Content-Type':'application/json'}});
  if (!response.ok) { if(response.status===401&&revision===authRevision&&!['session','auth/bootstrap','account/password'].includes(path)&&connected)showLogin('Deine Sitzung ist abgelaufen. Bitte erneut anmelden.'); const body = await response.json().catch(()=>({})); throw new Error(typeof body.detail === 'string' ? body.detail : response.status===422?validationMessage(body.detail):'Der Butler ist gerade nicht erreichbar (HTTP '+response.status+'). Bitte erneut versuchen.'); }
  return response.json();
}
const notice = text => { $('notice').textContent=text;$('notice').hidden=!text; };
function renderContext(){
  const context=JSON.parse($('context-json').value);$('context-projects').replaceChildren();
  if(!context.projects.length)$('context-projects').append(node('div','empty-state','Noch keine Projekte hinterlegt. Was hast du als Nächstes vor?'));
  context.projects.forEach((project,index)=>{
    const card=node('article','project-card'),heading=node('div','project-heading');const enabled=node('label','project-active');const checkbox=node('input');checkbox.type='checkbox';checkbox.checked=project.active!==false;enabled.append(checkbox,node('span','','Aktiv'));const remove=node('button','icon-button','✕');remove.type='button';remove.setAttribute('aria-label','Projekt entfernen');remove.onclick=()=>{context.projects.splice(index,1);$('context-json').value=JSON.stringify(context);contextDirty=true;renderContext();};heading.append(node('span','eyebrow','PROJEKT ODER PLAN'),enabled,remove);card.append(heading);
    const update=()=>{$('context-json').value=JSON.stringify(context);contextDirty=true;};checkbox.onchange=()=>{project.active=checkbox.checked;update();};
    for(const [key,title,placeholder] of [['name','Bezeichnung','Zum Beispiel: Umbau des Arbeitszimmers'],['keywords','Schlagwörter','Zum Beispiel: Schreibtisch, Beleuchtung, Renovierung'],['notes','Was sollte dein Butler dazu wissen?','Pläne, Termine oder wichtige Details …']]){
      const label=node('label','project-field',title);const input=node(key==='notes'?'textarea':'input');input.value=key==='keywords'?(project.keywords||[]).join(', '):(project[key]||'');input.placeholder=placeholder;if(key==='notes'){input.rows=3;input.maxLength=2000;}else input.maxLength=key==='name'?200:3000;input.oninput=()=>{project[key]=key==='keywords'?input.value.split(',').map(x=>x.trim()).filter(Boolean):input.value;update();};label.append(input);card.append(label);
    }
    $('context-projects').append(card);
  });$('add-project').disabled=context.projects.length>=30;
}
$('add-project').onclick=()=>{const context=JSON.parse($('context-json').value);if(context.projects.length>=30)return;context.projects.push({name:'',keywords:[],active:true,notes:''});$('context-json').value=JSON.stringify(context);contextDirty=true;renderContext();$('context-projects').lastElementChild?.querySelector('input:not([type=checkbox])')?.focus();};
function formattedText(element,text) {
  // Render a small Markdown subset with DOM nodes only; source HTML stays inert.
  const parts=String(text).split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  for(const part of parts) {
    if(part.startsWith('**') && part.endsWith('**')) {const strong=document.createElement('strong');strong.textContent=part.slice(2,-2);element.append(strong);}
    else if(part.startsWith('`') && part.endsWith('`')) {const code=document.createElement('code');code.textContent=part.slice(1,-1);element.append(code);}
    else element.append(document.createTextNode(part));
  }
}
function bubble(text, role) {
  $('messages').querySelector('.welcome')?.remove();
  const el=document.createElement('div');el.className='bubble '+role;
  const label=document.createElement('span');label.className='message-role';label.textContent=role==='user'?'DU':'BUTLER';
  const content=document.createElement('div');content.className='bubble-content';formattedText(content,text);el.append(label,content);
  $('messages').append(el);$('messages').scrollTop=$('messages').scrollHeight;
  return el;
}
function showEvidence(sources,diagnostics=[]) {
  const content=$('evidence-content');content.replaceChildren();
  for(const source of sources) {
    const card=document.createElement('article');card.className='evidence-card';
    const type=document.createElement('span');type.className='badge';type.textContent={paperless:'PAPERLESS',imap:'E-MAIL',ntfy:'NTFY-EREIGNIS',weather:'WETTER · OPEN-METEO',holidays:'FEIERTAGE · NAGER.DATE',calendar:'ICLOUD · KALENDER',contacts:'ICLOUD · KONTAKTE'}[source.source]||'KONTEXT';
    const title=document.createElement('h3');title.textContent=source.title;
    const excerpt=document.createElement('div');excerpt.className='evidence-excerpt';excerpt.textContent=source.excerpt||'Kein Textauszug verfügbar.';card.append(type,title);
    if(source.document_date){const date=document.createElement('p');date.className='hint';date.textContent=source.source==='smart_home'?'Abgefragt: '+new Date(source.document_date).toLocaleString('de-DE',{timeZone:'Europe/Berlin'}):(source.source==='weather'?'Prognosetag: ':source.source==='holidays'?'Datum: ':'Dokumentdatum: ')+String(source.document_date).slice(0,10).split('-').reverse().join('.');card.append(date);}
    card.append(excerpt);
    if(source.url && /^https?:\/\//i.test(source.url)) {
      const link=document.createElement('a');link.href=source.url;link.target='_blank';link.rel='noopener noreferrer';link.textContent=source.source==='paperless'?'Original in Paperless öffnen ↗':'Datenquelle öffnen ↗';card.append(link);
    }content.append(card);
  }
  if(diagnostics.length) {
    const details=document.createElement('details');details.className='evidence-diagnostics';
    const summary=document.createElement('summary');summary.textContent='Hinweise zur Suche';details.append(summary);
    for(const message of diagnostics){const p=document.createElement('p');p.textContent=message;details.append(p);}content.append(details);
  }
  $('evidence-dialog').showModal();
}
$('evidence-close').onclick=()=>$('evidence-dialog').close();
$('evidence-dialog').addEventListener('click',event=>{if(event.target===$('evidence-dialog')){const bounds=event.target.getBoundingClientRect();if(event.clientX<bounds.left||event.clientX>bounds.right||event.clientY<bounds.top||event.clientY>bounds.bottom) event.target.close();}});
async function refresh() {
  const actor=currentUser?.id;if(!actor)return;
  const [status, events, sync] = await Promise.all([api('status'),api('events'),api('sync')]);if(currentUser?.id!==actor)return;
  const archives=sync.states.filter(state=>state.key.startsWith('mail-archive:'));
  const total=archives.reduce((sum,state)=>sum+(state.total||0),0);
  const indexed=archives.reduce((sum,state)=>sum+(state.indexed||0),0);
  $('archive-progress').textContent=archives.length?'E-Mail-Archiv: '+indexed.toLocaleString('de-DE')+' von '+total.toLocaleString('de-DE')+' Nachrichten in '+archives.length+' Ordnern erfasst'+(archives.every(state=>state.complete)?' · vollständig':' · wird weiter eingelesen'):'E-Mail-Archiv: Bestandsaufnahme wird vorbereitet.';
  $('source-counts').textContent=Object.entries(sync.indexed).map(([source,count])=>({imap:'E-Mails',paperless:'Dokumente',ntfy:'ntfy-Ereignisse'}[source]||source)+': '+count.toLocaleString('de-DE')).join(' · ');
  publicServices={weather:!!status.configured_sources?.weather,holidays:!!status.configured_sources?.holidays};
  renderSources(status,sync);
  renderInitialImport(status,sync);
  $('connection').textContent = status.external_llm ? 'Chat-KI bereit' : 'Lokale Suche';
  chatAiEnabled=Boolean(status.external_llm);
  $('send').disabled=chatBusy||!connected;
  if(status.llm_limited) $('connection').textContent='KI vorübergehend begrenzt · lokale Antworten';
  const opened=new Set(Array.from($('events').querySelectorAll('details[open]')).map(el=>el.dataset.eventId));
  $('events').replaceChildren();$('count').textContent=events.length+' FUNDSTÜCKE';$('nav-count').textContent=events.length;
  if(!events.length) $('events').textContent='Noch keine relevanten Vorgänge gefunden. Dein Butler behält die verbundenen Quellen im Blick.';
  for(const item of events) {
    const el=document.createElement('details');el.className='event';el.dataset.eventId=String(item.id);el.open=opened.has(String(item.id));
    const summary=document.createElement('summary');
    const icon=document.createElement('span');icon.className='event-icon';icon.append(iconNode(item.item_id.startsWith('mail:')?'mail':'sources'));
    const heading=document.createElement('div');heading.className='event-heading';
    const title=document.createElement('h3');title.textContent=item.title;
    const excerpt=document.createElement('p');excerpt.className='event-excerpt';excerpt.textContent=item.reason.replace(/\*\*/g,'').replace(/\s+/g,' ').slice(0,160);
    heading.append(title,excerpt);
    const arrow=document.createElement('span');arrow.className='event-chevron';arrow.textContent='›';summary.append(icon,heading,arrow);
    const body=document.createElement('div');body.className='event-body';formattedText(body,item.reason);
    const meta=document.createElement('div');meta.className='event-meta';const date=new Date(item.created.replace(' ','T')+'Z');
    meta.textContent=(item.item_id.startsWith('mail:')?'E-Mail':'Paperless')+' · '+(Number.isNaN(date.getTime())?item.created:date.toLocaleString('de-DE'));body.append(meta);el.append(summary,body);$('events').append(el);
  }
  const failures = Object.entries(status.sources).filter(([,v]) => v.error || v.errors);
  if (failures.length) notice('Quellenfehler: '+failures.map(([k])=>k).join(', ')+'. Zugangsdaten oder KI-Konfiguration prüfen.');
}
async function loadAccounts(){
  if(currentUser?.role!=='admin')return;
  const actor=currentUser.id;const users=await api('accounts');if(currentUser?.id!==actor)return;$('account-list').replaceChildren();
  for(const user of users){const row=node('div','account-row');const label=node('div');label.append(node('strong','',user.email),node('small','',user.role==='admin'?'Administrator':'Mitglied'));const button=node('button','secondary',user.active?'Sperren':'Freigeben');button.type='button';button.disabled=user.id===currentUser.id;button.onclick=async()=>{try{await api('accounts/'+user.id,{method:'PATCH',body:JSON.stringify({active:!user.active})});await loadAccounts();}catch(e){notice(e.message);}};row.append(label,node('span','badge',user.active?'AKTIV':'GESPERRT'),button);$('account-list').append(row);}
}
async function showLogin(message=''){
  const revision=++authRevision;if(currentUser)sessionStorage.removeItem('butler-manual-location-'+currentUser.id);manualLocation=null;locationMemory=null;$('location-results').replaceChildren();$('my-contact-results').replaceChildren();$('my-contact-query').value='';$('my-contact-status').textContent='';$('location-query').value='';$('use-device-location').checked=false;if($('location-status'))$('location-status').textContent='Für Wetter/Feiertage: Gerätestandort freigeben oder Ort wählen.';publicServices={weather:false,holidays:false};connected=false;chatBusy=false;currentUser=null;conversationId=null;savedConfig=null;chatAiEnabled=false;contextDirty=false;dirtyServices.clear();
  voiceConversation=false;if(recorder?.state==='recording')recorder.stop();window.speechSynthesis?.cancel();
  $('initial-import').hidden=true;$('initial-import-rows').replaceChildren();$('app-shell').hidden=true;$('login-view').hidden=false;$('send').disabled=true;
  for(const dialog of document.querySelectorAll('dialog[open]'))dialog.close();
  $('messages').replaceChildren();$('events').replaceChildren();$('evidence-content').replaceChildren();$('account-list').replaceChildren();$('conversation-list').replaceChildren();$('mobile-conversation-list').replaceChildren();$('context-projects').replaceChildren();
  for(const input of document.querySelectorAll('#app-shell input,#app-shell textarea'))if(input.type!=='checkbox')input.value='';
  $('context-json').value='{"projects":[]}';$('source-grid').replaceChildren();$('source-preview').replaceChildren();$('account-identity').textContent='';
  $('login-error').textContent=message;$('login-password').value='';$('login-password-confirm').value='';
  try{const config=await api('auth/config');if(revision!==authRevision)return;bootstrapAccount=!config.configured&&config.bootstrap_local;
    $('login-title').textContent=bootstrapAccount?'Dein Konto einrichten.':'Schön, dass du da bist.';
    $('login-description').textContent=!config.configured&&!config.bootstrap_local?'Noch kein Konto vorhanden. Im LXC einmal aibutler user add EMAIL ausführen.':bootstrapAccount?'Lege dein erstes Administratorkonto an. Bestehende Quellen und Gespräche bleiben erhalten.':'Mit deiner E-Mail-Adresse und deinem Passwort anmelden.';
    $('password-confirm-label').hidden=!bootstrapAccount;$('login-password-confirm').required=bootstrapAccount;$('login-password').minLength=bootstrapAccount?8:1;$('login-password').autocomplete=bootstrapAccount?'new-password':'current-password';$('remember-label').hidden=bootstrapAccount;
    $('login-submit').textContent=bootstrapAccount?'Konto erstellen ↗':'Anmelden ↗';$('login-submit').disabled=!config.configured&&!config.bootstrap_local;
  }catch(e){$('login-error').textContent=e.message;}
}
async function restoreLogin(){
  const revision=authRevision;const session=await api('session');if(revision!==authRevision)return;currentUser=session.user;
  if(currentUser)$('use-device-location').checked=localStorage.getItem('butler-device-location-'+currentUser.id)==='true';
  if(currentUser){try{manualLocation=JSON.parse(sessionStorage.getItem('butler-manual-location-'+currentUser.id)||'null');}catch{manualLocation=null;}if(manualLocation&&!$('use-device-location').checked)$('location-status').textContent=manualLocation.label+' · manuell, bis zum Abmelden';}
  if(!currentUser)throw new Error('Bitte mit einem Benutzerkonto anmelden.');
  document.body.dataset.role=currentUser.role;$('account-identity').textContent=currentUser.email+' · '+(currentUser.role==='admin'?'Administrator':'Mitglied');
  $('login-view').hidden=true;$('app-shell').hidden=false;$('login-password').value='';$('login-password-confirm').value='';notice('');
  connected=true;if(!$('messages').children.length)welcome();
  if(currentUser.role==='admin')await loadConnections();if(revision!==authRevision)return;
  const results=await Promise.allSettled([refresh(),api('context'),loadConversations(),loadAccounts(),loadMyIdentity()]);
  if(revision!==authRevision)return;
  if(results[1].status==='fulfilled'){$('context-json').value=JSON.stringify(results[1].value);if(currentUser.role==='admin')renderContext();}
  const remembered=localStorage.getItem(conversationKey());if(remembered&&!conversationId&&(!location.hash||location.hash==='#chat'))await openConversation(remembered);
  if(results[0].status==='rejected')notice('Statusübersicht derzeit nicht verfügbar.');
}
$('login-form').onsubmit=async event=>{
  event.preventDefault();if(!window.isSecureContext){$('login-error').textContent='Bitte die HTTPS-Adresse oder den lokalen SSH-Tunnel verwenden.';return;}
  const password=$('login-password').value;
  if(bootstrapAccount&&password!==$('login-password-confirm').value){$('login-error').textContent='Die Passwörter stimmen nicht überein.';return;}
  $('login-submit').disabled=true;$('login-error').textContent='';
  try{await api(bootstrapAccount?'auth/bootstrap':'session',{method:'POST',body:JSON.stringify({email:$('login-email').value,password,remember:$('login-remember').checked})});authRevision++;broadcastAuth();await restoreLogin();}
  catch(e){$('login-error').textContent=e.message;}finally{$('login-submit').disabled=false;}
};
$('logout').onclick=async()=>{try{await api('session',{method:'DELETE'});}catch{}broadcastAuth();await showLogin();};
async function loadMyIdentity(){
  const actor=currentUser?.id;if(!actor)return;const result=await api('account/identity');if(currentUser?.id!==actor)return;
  $('my-contact-status').textContent=result.contact?'Zugeordnet: '+result.contact.name+' · Angaben werden aus iCloud aktualisiert.':'Noch keine eigene Kontaktkarte zugeordnet.';
}
$('my-contact-form').onsubmit=async event=>{event.preventDefault();const revision=authRevision;$('my-contact-results').textContent='Suche in iCloud-Kontakten …';try{const result=await api('account/contact-candidates',{method:'POST',body:JSON.stringify({query:$('my-contact-query').value})});if(revision!==authRevision)return;$('my-contact-results').replaceChildren();for(const contact of result.contacts){const button=node('button','secondary','Das bin ich: '+contact.name+' · '+contact.emails.join(', '));button.type='button';button.onclick=async()=>{try{await api('account/identity',{method:'PUT',body:JSON.stringify({contact_id:contact.id})});if(revision!==authRevision)return;$('my-contact-results').replaceChildren();await loadMyIdentity();}catch(e){if(revision===authRevision)$('my-contact-status').textContent=e.message;}};$('my-contact-results').append(button);}if(!result.contacts.length)$('my-contact-results').textContent='Kein passender Kontakt gefunden.';}catch(e){if(revision===authRevision)$('my-contact-results').textContent=e.message;}};
$('clear-my-contact').onclick=async()=>{try{await api('account/identity',{method:'PUT',body:JSON.stringify({contact_id:null})});await loadMyIdentity();}catch(e){notice(e.message);}};
$('password-form').onsubmit=async event=>{event.preventDefault();try{await api('account/password',{method:'POST',body:JSON.stringify({current_password:$('current-password').value,new_password:$('new-password').value})});authRevision++;broadcastAuth();$('password-form').reset();$('password-status').textContent='Passwort geändert. Andere Sitzungen wurden abgemeldet.';}catch(e){$('password-status').textContent=e.message;}};
$('new-account-form').onsubmit=async event=>{event.preventDefault();try{await api('accounts',{method:'POST',body:JSON.stringify({email:$('new-account-email').value,password:$('new-account-password').value,role:$('new-account-role').value})});$('new-account-form').reset();$('new-account-status').textContent='Konto angelegt.';await loadAccounts();}catch(e){$('new-account-status').textContent=e.message;}};
$('connect').onclick = async () => {
  $('connect').disabled=true;
  try { await restoreLogin(); }
  catch(e) { notice(e.message); }
  finally { $('connect').disabled=false; }
};
$('scan').onclick = async () => { $('scan').disabled = true; notice('Quellen werden geprüft …'); try { await api('scan',{method:'POST'}); notice('Prüfung abgeschlossen.'); await refresh(); } catch(e) { notice(e.message); } finally { $('scan').disabled = false; } };
$('save').onclick = async () => { try { const value = JSON.parse($('context-json').value); if(value.projects.some(p=>!p.name.trim()||p.keywords.length>30||p.keywords.some(k=>k.length<2||k.length>100)))throw new Error('Bitte eine Bezeichnung und höchstens 30 Schlagwörter mit jeweils 2 bis 100 Zeichen eingeben.'); await api('context',{method:'PUT',body:JSON.stringify(value)}); contextDirty=false;notice('Kontext lokal gespeichert.'); } catch(e) { notice(e.message); } };
$('chat-form').onsubmit = async event => {
  event.preventDefault(); const message = $('question').value.trim(); if (!message) return;
  if(chatBusy)return;
  if(!connected||!currentUser)return;const actor=currentUser.id;
  bubble(message,'user'); $('question').value = '';$('question').style.height=''; chatBusy=true;$('send').disabled = true;
  const progress=document.createElement('div');progress.className='chat-progress';progress.textContent='Dein Butler sucht in deinen Quellen …';$('messages').append(progress);$('messages').setAttribute('aria-busy','true');$('messages').scrollTop=$('messages').scrollHeight;
  try {
    let deviceLocation=null;
    if(publicServices.weather||publicServices.holidays){const requirement=await api('chat/location-needed',{method:'POST',body:JSON.stringify({message,conversation_id:conversationId})});if(requirement.needed)deviceLocation=await getDeviceLocation().catch(()=>null);}
    if(currentUser?.id!==actor)return;
    const result = await api('chat',{method:'POST',body:JSON.stringify({message,device_location:deviceLocation,share_external:chatAiEnabled,conversation_id:conversationId})});
    if(currentUser?.id!==actor)return;
    progress.remove();answerBubble(result);conversationId=result.conversation_id||conversationId;
    if(conversationId)localStorage.setItem(conversationKey(),conversationId);
    loadConversations().catch(()=>{});
    if ($('speak').checked && 'speechSynthesis' in window) {
      const utterance = new SpeechSynthesisUtterance(result.answer); utterance.lang='de-DE';
      const voices = speechSynthesis.getVoices().filter(v=>v.localService && v.lang.startsWith('de'));
      if (voices.length) { utterance.voice=voices[0];utterance.onend=()=>{if(voiceConversation&&$('voice-send').checked)startLocalRecording();};utterance.onerror=()=>{voiceConversation=false;notice('Vorlesen ist nicht verfügbar.');};speechSynthesis.cancel();speechSynthesis.speak(utterance); }
      else {voiceConversation=false;notice('Keine lokale deutsche Stimme verfügbar. Vorlesen übersprungen.');}
    }
  } catch(e) { if(currentUser?.id!==actor)return;voiceConversation=false;progress.remove();bubble('Die Anfrage konnte nicht abgeschlossen werden: '+e.message,'assistant'); } finally { if(currentUser?.id===actor){chatBusy=false;$('send').disabled=false;$('messages').setAttribute('aria-busy','false');setMic(voiceConversation);loadConversations().catch(()=>{}); }}
};
for(const id of ['speak','voice-send']) {
  $(id).checked=localStorage.getItem('butler-'+id)!=='false';
  $(id).onchange=()=>localStorage.setItem('butler-'+id,String($(id).checked));
}
let recorder=null,voiceConversation=false;
async function startLocalRecording(){
  if(recorder||chatBusy) return;
  const actor=currentUser?.id,revision=authRevision;if(!actor)return;
  const stillCurrent=()=>currentUser?.id===actor&&authRevision===revision;
  if(!window.isSecureContext||!navigator.mediaDevices?.getUserMedia) {voiceConversation=false;return notice('Spracheingabe benötigt die HTTPS-Adresse deiner Webapp.');}
  let stream;
  try{
    const status=await api('speech');if(!status.ready) throw new Error('Lokale Spracherkennung ist noch nicht eingerichtet.');
    stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true}});
    if(!stillCurrent()||!voiceConversation||!connected){stream.getTracks().forEach(track=>track.stop());return;}
    const context=new (window.AudioContext||window.webkitAudioContext)();await context.resume();
    const source=context.createMediaStreamSource(stream),analyser=context.createAnalyser();analyser.fftSize=2048;source.connect(analyser);
    const samples=new Float32Array(analyser.fftSize),parts=[];
    const recording=new MediaRecorder(stream);recorder=recording;
    let lastVoice=performance.now(),heard=false;const started=lastVoice;
    const timer=setInterval(()=>{analyser.getFloatTimeDomainData(samples);const rms=Math.sqrt(samples.reduce((s,x)=>s+x*x,0)/samples.length);if(rms>0.015){lastVoice=performance.now();heard=true;}if((heard&&performance.now()-lastVoice>1800)||performance.now()-started>45000) recording.stop();},150);
    recording.ondataavailable=event=>{if(event.data.size)parts.push(event.data);};
    recording.onstop=async()=>{
      clearInterval(timer);stream.getTracks().forEach(track=>track.stop());await context.close();if(recorder===recording)recorder=null;if(!stillCurrent())return;setMic(voiceConversation);
      if(!voiceConversation){return;}
      if(!heard){voiceConversation=false;return notice('Keine Sprache erkannt. Mikrofon erneut starten.');}
      notice('Ich höre zu Ende und erkenne deine Frage lokal …');
      try{const response=await fetch('/api/speech/transcribe',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/octet-stream'},body:new Blob(parts,{type:recording.mimeType})});const result=await response.json();if(!response.ok)throw new Error(result.detail);if(!result.text)throw new Error('Keine Sprache erkannt.');if(!stillCurrent()||!voiceConversation||!connected)return;$('question').value=result.text;notice('');if($('voice-send').checked&&!$('send').disabled)$('chat-form').requestSubmit();}
      catch(e){if(!stillCurrent())return;voiceConversation=false;setMic(false);notice(e.message);}
    };
    if(!stillCurrent()||!voiceConversation||!connected){clearInterval(timer);stream.getTracks().forEach(track=>track.stop());await context.close();if(recorder===recording)recorder=null;return;}
    recording.start();setMic(true);notice('Ich höre zu · Verarbeitung auf deinem Butler-Rechner');
  }catch(e){stream?.getTracks().forEach(track=>track.stop());if(!stillCurrent())return;voiceConversation=false;setMic(false);notice(e.message);}
}
$('mic').onclick=()=>{if(voiceConversation){voiceConversation=false;if(recorder?.state==='recording')recorder.stop();window.speechSynthesis?.cancel();setMic(false);notice('Sprachgespräch beendet.');return;}voiceConversation=true;window.speechSynthesis?.cancel();startLocalRecording();};
async function pollStatus(){
  try {if(connected)await refresh();}catch(e){if(connected)notice(e.message);}
  finally {setTimeout(pollStatus,$('initial-import').hidden?30000:5000);}
}
setTimeout(pollStatus,5000);


$('use-device-location').checked=false;
$('use-device-location').onchange=()=>{localStorage.setItem('butler-device-location-'+currentUser.id,String($('use-device-location').checked));manualLocation=null;sessionStorage.removeItem('butler-manual-location-'+currentUser.id);locationMemory=null;$('location-status').textContent=$('use-device-location').checked?'Standort wird nur bei passenden Fragen abgefragt.':'Kein automatischer Gerätestandort.';};
async function getDeviceLocation(){
  const revision=authRevision,actor=currentUser?.id;const stillCurrent=()=>revision===authRevision&&actor===currentUser?.id;
  if(!$('use-device-location').checked)return manualLocation;
  if(!window.isSecureContext||!navigator.geolocation)throw new Error('Gerätestandort benötigt HTTPS und eine Browserfreigabe.');
  const position=await new Promise((resolve,reject)=>navigator.geolocation.getCurrentPosition(resolve,reject,{enableHighAccuracy:false,timeout:8000,maximumAge:120000}));
  if(!stillCurrent())throw new Error('Anmeldung wurde gewechselt.');
  const latitude=Math.round(position.coords.latitude*100)/100,longitude=Math.round(position.coords.longitude*100)/100,key=latitude+','+longitude;
  let location={latitude,longitude,timezone:Intl.DateTimeFormat().resolvedOptions().timeZone||'Europe/Berlin',label:'Ungefährer Gerätestandort'};
  if(publicServices.holidays){
    if(locationMemory?.key===key&&Date.now()-locationMemory.checked<300000)location={...location,...locationMemory.region};
    else{
      try{
        const params=new URLSearchParams({latitude,longitude,localityLanguage:'de'});
        const response=await fetch('https://api.bigdatacloud.net/data/reverse-geocode-client?'+params,{signal:AbortSignal.timeout(8000),credentials:'omit',referrerPolicy:'no-referrer',redirect:'error'});
        if(!response.ok)throw new Error('Region nicht verfügbar');const data=await response.json();
        const region={country:/^[A-Z]{2}$/.test(data.countryCode)?data.countryCode:'',subdivision:/^[A-Z]{2}-[A-Z0-9]{1,6}$/.test(data.principalSubdivisionCode)?data.principalSubdivisionCode:'',label:[data.city||data.locality,data.principalSubdivision].filter(Boolean).join(', ').slice(0,100)||'Ungefährer Gerätestandort'};
        if(!stillCurrent())throw new Error('Anmeldung wurde gewechselt.');location={...location,...region};locationMemory={key,region,checked:Date.now()};
      }catch{} // Weather still works; missing region is reported by the backend.
    }
  }
  if(!stillCurrent())throw new Error('Anmeldung wurde gewechselt.');
  $('location-status').textContent=location.label+' · ungefährer Gerätestandort';return location;
}
$('choose-location').onclick=()=>{$('location-dialog').showModal();};
$('location-current').onclick=async()=>{const revision=authRevision;$('use-device-location').checked=true;sessionStorage.removeItem('butler-manual-location-'+currentUser.id);manualLocation=null;try{await getDeviceLocation();if(revision!==authRevision)return;localStorage.setItem('butler-device-location-'+currentUser.id,'true');$('location-dialog').close();}catch(e){if(revision!==authRevision)return;$('use-device-location').checked=false;$('location-results').textContent='Standort nicht verfügbar: '+e.message;}};
$('location-close').onclick=()=>$('location-dialog').close();
$('location-search-form').onsubmit=async event=>{event.preventDefault();const revision=authRevision;$('location-results').textContent='Suche …';try{const result=await api('locations/search',{method:'POST',body:JSON.stringify({query:$('location-query').value})});if(revision!==authRevision)return;$('location-results').replaceChildren();for(const location of result.locations){const button=node('button','secondary',location.label);button.type='button';button.onclick=()=>{manualLocation=location;sessionStorage.setItem('butler-manual-location-'+currentUser.id,JSON.stringify(location));$('use-device-location').checked=false;localStorage.setItem('butler-device-location-'+currentUser.id,'false');$('location-status').textContent=location.label+' · manuell, bis zum Abmelden';$('location-dialog').close();};$('location-results').append(button);}if(!result.locations.length)$('location-results').textContent='Kein Ort gefunden.';}catch(e){if(revision===authRevision)$('location-results').textContent=e.message;}};

const services = [
  {id:'llm', title:'KI-Anbieter', icon:'✧', description:'Für Analyse und Antworten. Wähle deinen Anbieter und lade die Modelle mit deinem API-Key.', fields:[
    ['llm_provider','Anbieter','provider'], ['llm_api_key','API-Key','password'], ['llm_model','Modell','model']
  ]},
  {id:'imap', title:'E-Mail', icon:'✉', description:'Postfach über eine verschlüsselte IMAP-Verbindung lesen.', fields:[
    ['imap_host','IMAP-Server','text'], ['imap_port','Port (TLS)','number'], ['imap_user','Benutzername / E-Mail','text'], ['imap_password','Passwort / App-Passwort','password'], ['imap_folder','Ordner für den laufenden Abruf','text'], ['imap_archive_enabled','Gesamten E-Mail-Altbestand lokal einlesen','checkbox'], ['imap_archive_all_folders','Alle lesbaren Postfachordner einbeziehen','checkbox']
  ]},
  {id:'paperless', title:'Paperless-ngx', icon:'▤', description:'Dokumente und OCR-Texte aus deiner eigenen Ablage einbeziehen.', fields:[
    ['paperless_url','Adresse deiner Instanz','url'], ['paperless_token','API-Token','password']
  ]},
  {id:'ntfy', title:'Benachrichtigungen', icon:'♧', description:'Nur wichtige neue Hinweise oder eine gebündelte Zusammenfassung. Keine Meldungen für Chat, Einstellungen oder Altbestände. Der Test sendet eine Testnachricht.', fields:[
    ['ntfy_url','Vollständige Topic-URL','url'], ['ntfy_token','Token (optional)','password'], ['ntfy_mode','Benachrichtigungen','ntfy-mode'], ['ntfy_interval_seconds','Mindestabstand (Minuten)','minutes'], ['ntfy_history_urls','Ereignis-Topics zum Lesen (eine vollständige URL pro Zeile)','textarea'], ['ntfy_history_enabled','ntfy-Verlauf lokal archivieren und als KI-Kontext nutzen','checkbox']
  ]},
  {id:'icloud_calendar',title:'iCloud · Kalender',description:'Kalender einschließlich für deinen Apple-Account freigegebener Kalender rein lesend einbinden. Nur ausgewählte Kalender werden abgefragt. Apple verlangt ein app-spezifisches Passwort; kein normales Apple-Passwort eingeben. Quellen sind im Butler gemeinsam nutzbar.',fields:[['icloud_calendar_enabled','Kalenderabfragen aktivieren','checkbox'],['icloud_calendar_user','Apple-Account / E-Mail','text'],['icloud_calendar_password','App-spezifisches Passwort','password'],['icloud_calendar_names','Kalendernamen (optional; leer = alle lesbaren, mit Komma getrennt)','text']]},
  {id:'icloud_contacts',title:'iCloud · Kontakte',description:'Namen, E-Mail-Adressen, Telefonnummern und Firmenangaben lesend aktualisieren. Keine automatische Änderung deiner Kontakte. Eigene Kontaktkarte unter Mein Konto einmal zuordnen; Firmenangaben werden danach dynamisch gelesen. Quellen sind im Butler gemeinsam nutzbar.',fields:[['icloud_contacts_enabled','Kontaktabfragen aktivieren','checkbox'],['icloud_contacts_user','Apple-Account / E-Mail','text'],['icloud_contacts_password','App-spezifisches Passwort','password']]},
  {id:'iobroker', title:'ioBroker · Smart Home', icon:'⌂', description:'Smart-Home-Zustände im Hintergrund als Kontext für deine KI-Fragen bereitstellen.', fields:[
    ['iobroker_url','API-Adresse (nicht die Admin-Oberfläche)','url'], ['iobroker_api','API-Adapter','iobroker-api'],
    ['iobroker_user','Benutzername (optional)','text'], ['iobroker_password','Passwort (optional)','password'],
    ['iobroker_token','Bearer-Token (optional, statt Passwort)','password'], ['iobroker_patterns','Datenpunkt-Muster (mit Komma getrennt)','textarea'],
    ['iobroker_enabled','Regelmäßig im Hintergrund prüfen','checkbox']
  ]},
  {id:'weather',title:'Wetter · Open-Meteo',description:'Wetter am Standort des fragenden Geräts. Nur auf passende Fragen, ohne Mail- oder Dokumentinhalte an den Wetterdienst zu senden. Kostenfreie API für private, nichtkommerzielle Nutzung.',fields:[['weather_enabled','Wetterabfragen aktivieren','checkbox']]},
  {id:'holidays',title:'Feiertage · Nager.Date',description:'Feiertage passend zu Land/Bundesland des fragenden Geräts. Automatische Regionserkennung verwendet nach Standortfreigabe BigDataCloud; alternativ Ort manuell wählen. Kommunale Sonderfälle sind nicht vollständig abgedeckt.',fields:[['holidays_enabled','Feiertagsabfragen aktivieren','checkbox']]},
  {id:'internet', title:'Internet', icon:'◎', description:'Erreichbarkeit prüfen. Dafür sind keine Zugangsdaten nötig.', fields:[]}
];
let connected = false;
let savedConfig = null;
let modelRevision = 0;
let modelTimer;
const dirtyServices = new Set();
const savingServices = new Set();
function setServiceStatus(id, text, state='neutral') {
  const el=$('status-'+id); el.textContent=text; el.dataset.state=state;
}
function option(select, value, text) {
  const el=document.createElement('option'); el.value=value; el.textContent=text;select.append(el);
}
for (const service of services) {
  const card=document.createElement('article');card.className='connection-card';card.id='service-'+service.id;
  const heading=document.createElement('div');heading.className='connection-heading';
  const icon=document.createElement('span');icon.className='service-icon';icon.append(iconNode({llm:'spark',imap:'mail',paperless:'sources',ntfy:'notification',iobroker:'home',weather:'home',holidays:'history',icloud_calendar:'history',icloud_contacts:'mail',internet:'arrow'}[service.id]));
  const title=document.createElement('h3');title.textContent=service.title;heading.append(icon,title);card.append(heading);
  const description=document.createElement('p');description.textContent=service.description;card.append(description);
  const fieldset=document.createElement('fieldset');fieldset.id='fields-'+service.id;fieldset.disabled=true;
  const legend=document.createElement('legend');legend.className='sr-only';legend.textContent=service.title;fieldset.append(legend);
  const fields=document.createElement('div');fields.className='service-fields';
  for (const [key,labelText,type] of service.fields) {
    const label=document.createElement('label');label.textContent=labelText;
    const input=document.createElement(['provider','model','iobroker-api','ntfy-mode'].includes(type)?'select':type==='textarea'?'textarea':'input'); input.id='cfg-'+key;
    if(type==='ntfy-mode') { option(input,'important','Nur wichtige neue Hinweise');option(input,'digest','Fundstücke gebündelt');option(input,'off','Keine automatischen Benachrichtigungen'); }
    else if(type==='minutes') { input.type='number';input.min='1';input.max='1440'; }
    else if(type==='iobroker-api') { option(input,'simple','Simple-API (meist Port 8087)');option(input,'rest','REST-API (meist Port 8093)'); }
    else if(type==='textarea') { input.rows=3; }
    else if(type==='provider') { option(input,'openai','OpenAI');option(input,'gemini','Google Gemini'); }
    else if(type==='model') option(input,'','API-Key eingeben und Modelle laden');
    else { input.type=type;input.autocomplete='off'; }
    label.append(input);fields.append(label);
    input.addEventListener('input',()=>{
      dirtyServices.add(service.id);setServiceStatus(service.id,'Ungespeicherte Änderungen');
    });
  }
  fieldset.append(fields);
  if(service.id==='llm') {
    const modelsBar=document.createElement('div');modelsBar.className='models-bar';
    const load=document.createElement('button');load.id='load-models';load.type='button';load.className='secondary';load.textContent='Modelle neu laden';load.onclick=()=>loadModels();
    const status=document.createElement('span');status.id='model-status';status.setAttribute('role','status');status.textContent='Die Modellliste wird direkt vom Anbieter geladen.';modelsBar.append(load,status);fieldset.append(modelsBar);
    const flags=document.createElement('div');flags.className='options';
    for(const [key,labelText] of [
      ['chat_llm_enabled','KI für meine Chatfragen verwenden'],
      ['allow_external_llm','Automatische KI-Analysen im Hintergrund verwenden'],
      ['gemini_paid_confirmed','Gemini: geeignete Abrechnung und Vertragsbedingungen für sensible Daten geprüft']
    ]) {
      const label=document.createElement('label');const input=document.createElement('input');input.type='checkbox';input.id='cfg-'+key;
      input.onchange=()=>{dirtyServices.add('llm');setServiceStatus('llm','Ungespeicherte Änderungen');};
      label.append(input,document.createTextNode(' '+labelText));flags.append(label);
    }fieldset.append(flags);
    const hint=document.createElement('p');hint.className='hint';hint.append(document.createTextNode('Der KI-Test sendet nur einen Testprompt und kann API-Kosten verursachen. Die Modellliste garantiert noch keine Chat-Kompatibilität. '));
    const link=document.createElement('a');link.href='https://ai.google.dev/gemini-api/terms';link.target='_blank';link.rel='noopener';link.textContent='Gemini-Datenbedingungen';hint.append(link);fieldset.append(hint);
  }
  if(service.id.startsWith('icloud_')){const hint=node('p','hint');const link=node('a','','App-spezifisches Passwort bei Apple einrichten ↗');link.href='https://support.apple.com/de-de/102654';link.target='_blank';link.rel='noopener noreferrer';hint.append(link);fieldset.append(hint);}
  if(service.id==='iobroker') {
    const hint=document.createElement('p');hint.className='hint';hint.textContent='Die Admin-Oberfläche (meist Port 8081) ist keine Zustands-API. Nutze den installierten Simple-API- oder REST-API-Adapter mit seinem konfigurierten Port. Leere Datenpunkt-Muster verwenden die Standardwerte connected, reachable, online und UNREACH. Optional prüft *.alive den Adapterbetrieb, nicht zwingend ein Gerät. Zusätzliche Muster wie zigbee.0.*.temperature liefern Messwerte. Ein ausgeschaltetes Licht gilt nicht als offline. Die Werte dienen der KI als Fragekontext. Es werden keine Zustandslisten oder einzelnen Statusmeldungen in der Oberfläche ausgegeben.';fieldset.append(hint);
  }
  const actions=document.createElement('div');actions.className='service-actions';
  if(service.fields.length) {
    const save=document.createElement('button');save.textContent='Speichern';save.type='button';save.onclick=()=>saveService(service,save);actions.append(save);
  }
  if(['weather','holidays'].includes(service.id)){const choose=node('button','secondary','Standort für Test wählen');choose.type='button';choose.onclick=()=>$('location-dialog').showModal();actions.append(choose);}
  const test=document.createElement('button');test.textContent=service.id==='internet'?'Erreichbarkeit prüfen':'Verbindung testen';test.className='secondary';test.type='button';test.onclick=()=>testService(service.id,test);actions.append(test);fieldset.append(actions);card.append(fieldset);
  const status=document.createElement('p');status.id='status-'+service.id;status.className='service-status';status.setAttribute('role','status');status.textContent='Gespeicherte Verbindung wird geladen …';card.append(status);
  $('connection-cards').append(card);
}
function fillService(service,config) {
  for(const [key,,type] of service.fields) {
    const input=$('cfg-'+key);
    if(type==='minutes') input.value=config[key]/60;
    else if(type==='checkbox') input.checked=config[key];
    else if(type==='password') {
      input.value='';input.placeholder=config.secrets[key]?'•••••••• · gespeichert':'Noch nicht hinterlegt';
      let hint=input.parentElement.querySelector('.saved-secret');
      if(!hint) {hint=document.createElement('small');hint.className='saved-secret';input.parentElement.append(hint);}
      hint.textContent=config.secrets[key]?'✓ Bereits lokal gespeichert. Keine erneute Eingabe nötig.':'Noch kein Schlüssel oder Passwort gespeichert.';
    }
    else if(type==='model') {
      if(!Array.from(input.options).some(o=>o.value===config[key])) option(input,config[key],config[key]+' (gespeichert)');
      input.value=config[key];
    } else input.value=config[key];
  }
  if(service.id==='llm') for(const key of ['chat_llm_enabled','allow_external_llm','gemini_paid_confirmed']) $('cfg-'+key).checked=config[key];
  $('fields-'+service.id).disabled=false;
}
async function loadConnections() {
  const actor=currentUser?.id;const config=await api('connections');if(currentUser?.id!==actor)return;savedConfig=config;connected=true;
  for(const service of services) {
    fillService(service,savedConfig);
    setServiceStatus(service.id,service.id==='internet'?'Bereit zur Prüfung.':'Gespeicherte Einstellungen geladen');
  }
  if(savedConfig.secrets.llm_api_key) loadModels();
}
async function saveService(service,button) {
  const actor=currentUser?.id;if(!actor)return;
  if(savingServices.has(service.id)) return;
  savingServices.add(service.id);
  const fieldset=$('fields-'+service.id);
  const originalText=button.textContent;
  fieldset.disabled=true;button.disabled=true;button.textContent='Wird gespeichert …';
  setServiceStatus(service.id,'Wird lokal gespeichert …');
  try {
    const config={};
    for(const [key,,type] of service.fields) {
      const input=$('cfg-'+key);const value=input.value.trim();
      if(type==='minutes') config[key]=Number(value)*60;
      else if(type==='checkbox') config[key]=input.checked;
      else if(type==='password') { if(value) config[key]=input.value; }
      else if(key==='iobroker_patterns')config[key]=value||'*.connected,*.reachable,*.online,*.UNREACH';
      else config[key]=type==='number'?Number(value):value;
    }
    if(service.id==='llm') for(const key of ['chat_llm_enabled','allow_external_llm','gemini_paid_confirmed']) config[key]=$('cfg-'+key).checked;
    await api('connections/'+service.id,{method:'PUT',body:JSON.stringify(config),signal:AbortSignal.timeout(15000)});
    const current=await api('connections');if(currentUser?.id!==actor)return;savedConfig=current;
    // Refresh this card only: drafts in other connections must remain untouched.
    fillService(service,current);dirtyServices.delete(service.id);
    setServiceStatus(service.id,'Gespeichert · jetzt Verbindung testen','success');await refresh();
  } catch(e) { setServiceStatus(service.id,e.message,'error'); }
  finally { savingServices.delete(service.id);fieldset.disabled=false;button.disabled=false;button.textContent=originalText; }
}
async function testService(id,button) {
  if(savingServices.has(id)) return setServiceStatus(id,'Speichern läuft noch …');
  if(dirtyServices.has(id)) return setServiceStatus(id,'Bitte diese Verbindung zuerst speichern, dann testen.','error');
  button.disabled=true;setServiceStatus(id,'Verbindung wird geprüft …');
  try {let location=null;if(['weather','holidays'].includes(id)){location=await getDeviceLocation();if(!location)throw new Error('Bitte im Chat einen Ort wählen oder den Gerätestandort freigeben.');} const result=await api('connections/'+id+(location?'/preview':'/test'),{method:'POST',...(location?{body:JSON.stringify(location)}:{})});setServiceStatus(id,result.message,'success'); }
  catch(e) { setServiceStatus(id,e.message,'error'); }
  finally { button.disabled=false; }
}
async function loadModels() {
  clearTimeout(modelTimer);
  const revision=++modelRevision;
  if(!connected) return;
  const provider=$('cfg-llm_provider').value;
  const key=$('cfg-llm_api_key').value.trim();
  const select=$('cfg-llm_model');const button=$('load-models');
  if(!key && !(savedConfig?.llm_provider===provider && savedConfig.secrets.llm_api_key)) {
    $('model-status').textContent='Bitte den API-Key für diesen Anbieter eingeben.';return;
  }
  button.disabled=true;$('model-status').textContent='Verfügbare Modelle werden geladen …';
  try {
    const result=await api('connections/llm/models',{method:'POST',body:JSON.stringify({provider,api_key:key||null})});
    if(revision!==modelRevision) return;
    const selected=select.value;select.replaceChildren();option(select,'','Modell auswählen …');
    for(const model of result.models) option(select,model.id,model.name===model.id?model.id:model.name+' · '+model.id);
    if(result.models.some(m=>m.id===selected)) select.value=selected;
    else if(selected) { dirtyServices.add('llm');setServiceStatus('llm','Gespeichertes Modell ist nicht in der aktuellen Liste. Bitte ein Modell auswählen.'); }
    $('model-status').textContent=result.models.length+' Modelle vom Anbieter geladen. Bitte auswählen und speichern.';
  } catch(e) { if(revision===modelRevision) $('model-status').textContent=e.message; }
  finally { if(revision===modelRevision) button.disabled=false; }
}
$('cfg-llm_api_key').addEventListener('input',()=>{
  ++modelRevision;clearTimeout(modelTimer);$('load-models').disabled=false;
  modelTimer=setTimeout(loadModels,700);
});
$('cfg-llm_provider').addEventListener('change',()=>{
  ++modelRevision;clearTimeout(modelTimer);$('load-models').disabled=false;
  $('cfg-llm_api_key').value='';
  $('cfg-llm_api_key').placeholder=savedConfig?.llm_provider===$('cfg-llm_provider').value && savedConfig.secrets.llm_api_key?'Gespeichert · leer lassen zum Behalten':'API-Key für diesen Anbieter eingeben';
  $('cfg-llm_model').replaceChildren();option($('cfg-llm_model'),'','API-Key eingeben und Modelle laden');
  $('cfg-chat_llm_enabled').checked=false;$('cfg-allow_external_llm').checked=false;$('cfg-gemini_paid_confirmed').checked=false;
  loadModels();
});

const viewLabels={
  chat:['Gespräch','Deine Fragen. Dein Wissen.','Dein persönlicher Wissensraum.'],
  sources:['Wissensquellen','Dein Wissen. Verbunden.','Die Grundlage für Antworten, die zu dir passen.'],
  findings:['Fundstücke','Für dich gefunden.','Relevante Hinweise aus deinen E-Mails und Dokumenten.'],
  settings:['Einstellungen','Dein Butler. Deine Verbindungen.','Verwalte deine Dienste und erzähle deinem Butler, was dir wichtig ist.']
};
function showView() {
  const hash=window.location.hash.slice(1);
  if(hash==='new-chat'){newConversation();return;}
  const view=hash==='sources'?'sources':hash==='findings'?'findings':(hash==='settings'||hash==='setup'||hash==='context'||hash.startsWith('service-'))?'settings':'chat';
  for(const element of document.querySelectorAll('.page-view')) element.hidden=element.dataset.view!==view;
  for(const link of document.querySelectorAll('[data-nav]')) {
    if(link.dataset.nav===view) link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');
  }
  document.body.dataset.view=view;
  const context=hash==='context';$('setup').hidden=context;$('context').hidden=!context;
  for(const tab of document.querySelectorAll('[data-settings-tab]')) tab.setAttribute('aria-selected',String(tab.dataset.settingsTab===(context?'context':'connections')));
  const [label,title,subtitle]=viewLabels[view];$('page-label').textContent=label;$('page-title').textContent=title;$('page-subtitle').textContent=subtitle;
  document.title='Butler · '+{chat:'Gespräch',sources:'Wissensquellen',findings:'Fundstücke',settings:'Einstellungen'}[view];
  window.scrollTo({top:0,behavior:'instant'});
  if(hash.startsWith('service-'))requestAnimationFrame(()=>$(hash)?.scrollIntoView({block:'start',behavior:'auto'}));
}
window.addEventListener('hashchange',showView);
$('messages').addEventListener('click',event=>{const button=event.target.closest('[data-prompt]');if(button){$('question').value=button.dataset.prompt;autoSizeQuestion();$('question').focus();}});
$('question').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();if(!$('send').disabled) $('chat-form').requestSubmit();}});
function autoSizeQuestion(){$('question').style.height='auto';$('question').style.height=Math.min($('question').scrollHeight,130)+'px';}
$('question').addEventListener('input',autoSizeQuestion);
$('new-chat').onclick=newConversation;$('header-new-chat').onclick=newConversation;
$('show-history').onclick=()=>$('history-dialog').showModal();$('history-close').onclick=()=>$('history-dialog').close();
document.addEventListener('keydown',event=>{if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==='k'){event.preventDefault();newConversation();}});
let installPrompt=null;
window.addEventListener('beforeinstallprompt',event=>{event.preventDefault();installPrompt=event;});
$('install-app').onclick=async()=>{if(installPrompt){await installPrompt.prompt();installPrompt=null;}else $('install-dialog').showModal();};
$('install-close').onclick=()=>$('install-dialog').close();
window.addEventListener('storage',event=>{if(event.key==='butler-auth-event'){showLogin().then(()=>restoreLogin().catch(()=>{}));}});
window.addEventListener('pageshow',event=>{if(event.persisted){$('app-shell').hidden=true;restoreLogin().catch(()=>showLogin());}});
window.addEventListener('online',()=>{notice('Wieder verbunden.');if(connected)refresh().then(()=>notice('')).catch(e=>notice(e.message));});
function fitAppViewport(){document.documentElement.style.setProperty('--app-height',(window.visualViewport?.height||window.innerHeight)+'px');document.body.classList.toggle('keyboard-open',Boolean(window.visualViewport&&window.innerHeight-window.visualViewport.height>120&&document.activeElement?.id==='question'));}
window.visualViewport?.addEventListener('resize',fitAppViewport);window.addEventListener('resize',fitAppViewport);$('question').addEventListener('focus',fitAppViewport);$('question').addEventListener('blur',fitAppViewport);fitAppViewport();
window.addEventListener('offline',()=>notice('Du bist offline. Dein Butler ist wieder erreichbar, sobald die Verbindung steht.'));
if('serviceWorker' in navigator)navigator.serviceWorker.register('/sw.js').catch(()=>{});
welcome();showView();

restoreLogin().catch(()=>showLogin());

window.addEventListener('beforeunload',event=>{ if(dirtyServices.size||contextDirty){event.preventDefault();event.returnValue='';} });
