const $ = id => document.getElementById(id);
let chatAiEnabled=false;
let chatBusy=false;
async function api(path, options = {}) {
  const response = await fetch('/api/' + path, {...options, credentials:'same-origin', headers: {'Content-Type':'application/json'}});
  if (!response.ok) { const body = await response.json(); throw new Error(typeof body.detail === 'string' ? body.detail : 'Ungültige Eingabe. JSON-Struktur prüfen.'); }
  return response.json();
}
const notice = text => { $('notice').textContent=text;$('notice').hidden=!text; };
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
    const type=document.createElement('span');type.className='badge';type.textContent={paperless:'PAPERLESS',imap:'E-MAIL',ntfy:'NTFY-EREIGNIS'}[source.source]||'KONTEXT';
    const title=document.createElement('h3');title.textContent=source.title;
    const excerpt=document.createElement('div');excerpt.className='evidence-excerpt';excerpt.textContent=source.excerpt||'Kein Textauszug verfügbar.';card.append(type,title);
    if(source.document_date){const date=document.createElement('p');date.className='hint';date.textContent='Dokumentdatum: '+String(source.document_date).slice(0,10).split('-').reverse().join('.');card.append(date);}
    card.append(excerpt);
    if(source.url && /^https?:\/\//i.test(source.url)) {
      const link=document.createElement('a');link.href=source.url;link.target='_blank';link.rel='noopener noreferrer';link.textContent='Original in Paperless öffnen ↗';card.append(link);
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
  const [status, events, sync] = await Promise.all([api('status'),api('events'),api('sync')]);
  const archives=sync.states.filter(state=>state.key.startsWith('mail-archive:'));
  const total=archives.reduce((sum,state)=>sum+(state.total||0),0);
  const indexed=archives.reduce((sum,state)=>sum+(state.indexed||0),0);
  $('archive-progress').textContent=archives.length?'E-Mail-Archiv: '+indexed.toLocaleString('de-DE')+' von '+total.toLocaleString('de-DE')+' Nachrichten in '+archives.length+' Ordnern erfasst'+(archives.every(state=>state.complete)?' · vollständig':' · wird weiter eingelesen'):'E-Mail-Archiv: Bestandsaufnahme wird vorbereitet.';
  $('source-counts').textContent=Object.entries(sync.indexed).map(([source,count])=>({imap:'E-Mails',paperless:'Dokumente',ntfy:'ntfy-Ereignisse'}[source]||source)+': '+count.toLocaleString('de-DE')).join(' · ');
  $('connection').textContent = status.external_llm ? 'Verbunden · Externe KI freigegeben' : 'Verbunden · Lokaler Modus';
  chatAiEnabled=Boolean(status.external_llm);
  $('send').disabled=chatBusy||!connected;
  if(status.llm_limited) $('connection').textContent='KI vorübergehend begrenzt · lokale Antworten';
  const opened=new Set(Array.from($('events').querySelectorAll('details[open]')).map(el=>el.dataset.eventId));
  $('events').replaceChildren();$('count').textContent=events.length+' FUNDSTÜCKE';$('nav-count').textContent=events.length;
  if(!events.length) $('events').textContent='Noch keine relevanten Vorgänge gefunden. Dein Butler behält die verbundenen Quellen im Blick.';
  for(const item of events) {
    const el=document.createElement('details');el.className='event';el.dataset.eventId=String(item.id);el.open=opened.has(String(item.id));
    const summary=document.createElement('summary');
    const icon=document.createElement('span');icon.className='event-icon';icon.textContent=item.item_id.startsWith('mail:')?'✉':'▤';
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
async function restoreLogin() {
  try { await api('session'); } catch { await api('session/local',{method:'POST'}); }
  notice('');
  // Restore saved connections before any optional status or source requests.
  await loadConnections();
  const results=await Promise.allSettled([refresh(),api('context')]);
  if(results[1].status==='fulfilled') $('context-json').value=JSON.stringify(results[1].value,null,2);
  if(results[0].status==='rejected') notice('Einstellungen geladen. Statusübersicht derzeit nicht verfügbar.');
}
$('connect').onclick = async () => {
  $('connect').disabled=true;
  try { await restoreLogin(); }
  catch(e) { notice(e.message); }
  finally { $('connect').disabled=false; }
};
$('scan').onclick = async () => { $('scan').disabled = true; notice('Quellen werden geprüft …'); try { await api('scan',{method:'POST'}); notice('Prüfung abgeschlossen.'); await refresh(); } catch(e) { notice(e.message); } finally { $('scan').disabled = false; } };
$('save').onclick = async () => { try { const value = JSON.parse($('context-json').value); await api('context',{method:'PUT',body:JSON.stringify(value)}); notice('Kontext lokal gespeichert.'); } catch(e) { notice(e.message); } };
$('chat-form').onsubmit = async event => {
  event.preventDefault(); const message = $('question').value.trim(); if (!message) return;
  bubble(message,'user'); $('question').value = ''; chatBusy=true;$('send').disabled = true;
  const progress=document.createElement('div');progress.className='chat-progress';progress.textContent='Dein Butler sucht in deinen Quellen …';$('messages').append(progress);$('messages').setAttribute('aria-busy','true');$('messages').scrollTop=$('messages').scrollHeight;
  try {
    const result = await api('chat',{method:'POST',body:JSON.stringify({message,share_external:chatAiEnabled})});
    progress.remove();const answerBubble=bubble(result.answer,'assistant');
    const role=answerBubble.querySelector('.message-role');
    role.textContent=result.external_used?'BUTLER · KI-ANTWORT':'BUTLER · LOKALE ANTWORT';
    if(result.ai_issue){const status=document.createElement('small');status.className='answer-status';status.textContent='KI derzeit nicht verfügbar · lokale Ersatzantwort';status.title=result.ai_issue;answerBubble.append(status);}
    if(result.sources?.length || result.diagnostics?.length) {
      const evidence=document.createElement('button');evidence.type='button';evidence.className='evidence-link';evidence.textContent=result.sources?.length?'Belege ansehen ('+result.sources.length+') ↗':'Hinweise zur Suche ↗';evidence.onclick=()=>showEvidence(result.sources||[],result.diagnostics||[]);answerBubble.append(evidence);
    }
    if ($('speak').checked && 'speechSynthesis' in window) {
      const utterance = new SpeechSynthesisUtterance(result.answer); utterance.lang='de-DE';
      const voices = speechSynthesis.getVoices().filter(v=>v.localService && v.lang.startsWith('de'));
      if (voices.length) { utterance.voice=voices[0];utterance.onend=()=>{if(voiceConversation&&$('voice-send').checked)startLocalRecording();};utterance.onerror=()=>{voiceConversation=false;notice('Vorlesen ist nicht verfügbar.');};speechSynthesis.cancel();speechSynthesis.speak(utterance); }
      else {voiceConversation=false;notice('Keine lokale deutsche Stimme verfügbar. Vorlesen übersprungen.');}
    }
  } catch(e) { voiceConversation=false;progress.remove();bubble('Die Anfrage konnte nicht abgeschlossen werden: '+e.message,'assistant'); } finally { chatBusy=false;$('send').disabled=false;$('messages').setAttribute('aria-busy','false'); }
};
for(const id of ['speak','voice-send']) {
  $(id).checked=localStorage.getItem('butler-'+id)!=='false';
  $(id).onchange=()=>localStorage.setItem('butler-'+id,String($(id).checked));
}
let recorder=null,voiceConversation=false;
async function startLocalRecording(){
  if(recorder||chatBusy) return;
  if(!window.isSecureContext||!navigator.mediaDevices?.getUserMedia) {voiceConversation=false;return notice('Spracheingabe benötigt die HTTPS-Adresse deiner Webapp.');}
  let stream;
  try{
    const status=await api('speech');if(!status.ready) throw new Error('Lokale Spracherkennung ist noch nicht eingerichtet.');
    stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true}});
    const context=new (window.AudioContext||window.webkitAudioContext)();await context.resume();
    const source=context.createMediaStreamSource(stream),analyser=context.createAnalyser();analyser.fftSize=2048;source.connect(analyser);
    const samples=new Float32Array(analyser.fftSize),parts=[];
    const recording=new MediaRecorder(stream);recorder=recording;
    let lastVoice=performance.now(),heard=false;const started=lastVoice;
    const timer=setInterval(()=>{analyser.getFloatTimeDomainData(samples);const rms=Math.sqrt(samples.reduce((s,x)=>s+x*x,0)/samples.length);if(rms>0.015){lastVoice=performance.now();heard=true;}if((heard&&performance.now()-lastVoice>1800)||performance.now()-started>45000) recording.stop();},150);
    recording.ondataavailable=event=>{if(event.data.size)parts.push(event.data);};
    recording.onstop=async()=>{
      clearInterval(timer);stream.getTracks().forEach(track=>track.stop());await context.close();recorder=null;$('mic').textContent='🎙';$('mic').setAttribute('aria-label','Sprachgespräch starten');
      if(!voiceConversation){return;}
      if(!heard){voiceConversation=false;return notice('Keine Sprache erkannt. Mikrofon erneut starten.');}
      notice('Ich höre zu Ende und erkenne deine Frage lokal …');
      try{const response=await fetch('/api/speech/transcribe',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/octet-stream'},body:new Blob(parts,{type:recording.mimeType})});const result=await response.json();if(!response.ok)throw new Error(result.detail);if(!result.text)throw new Error('Keine Sprache erkannt.');$('question').value=result.text;notice('');if($('voice-send').checked&&!$('send').disabled)$('chat-form').requestSubmit();}
      catch(e){voiceConversation=false;notice(e.message);}
    };
    recording.start();$('mic').textContent='■';$('mic').setAttribute('aria-label','Sprachgespräch beenden');notice('Ich höre zu · Verarbeitung auf deinem Butler-Rechner');
  }catch(e){stream?.getTracks().forEach(track=>track.stop());voiceConversation=false;notice(e.message);}
}
$('mic').onclick=()=>{if(voiceConversation){voiceConversation=false;if(recorder?.state==='recording')recorder.stop();window.speechSynthesis?.cancel();notice('Sprachgespräch beendet.');return;}voiceConversation=true;window.speechSynthesis?.cancel();startLocalRecording();};
$('pair-phone').onclick=async()=>{try{const result=await api('session/pairing',{method:'POST'});$('pairing-info').textContent=result.url+' · Code: '+result.code+' · 10 Minuten gültig, einmal verwendbar.';}catch(e){$('pairing-info').textContent=e.message;}};
$('pair-form').onsubmit=async event=>{event.preventDefault();try{await api('session/pair',{method:'POST',body:JSON.stringify({code:$('pair-code').value})});$('pair-dialog').close();$('pair-code').value='';await restoreLogin();}catch(e){$('pair-error').textContent=e.message;}};
setInterval(() => { if (connected) refresh().catch(e=>notice(e.message)); },30000);


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
  {id:'iobroker', title:'ioBroker · Smart Home', icon:'⌂', description:'Smart-Home-Zustände im Hintergrund als Kontext für deine KI-Fragen bereitstellen.', fields:[
    ['iobroker_url','API-Adresse (nicht die Admin-Oberfläche)','url'], ['iobroker_api','API-Adapter','iobroker-api'],
    ['iobroker_user','Benutzername (optional)','text'], ['iobroker_password','Passwort (optional)','password'],
    ['iobroker_token','Bearer-Token (optional, statt Passwort)','password'], ['iobroker_patterns','Datenpunkt-Muster (mit Komma getrennt)','textarea'],
    ['iobroker_enabled','Regelmäßig im Hintergrund prüfen','checkbox']
  ]},
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
  const icon=document.createElement('span');icon.className='service-icon';icon.textContent=service.icon;
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
  if(service.id==='iobroker') {
    const hint=document.createElement('p');hint.className='hint';hint.textContent='Standardmäßig werden connected, reachable, online und UNREACH geprüft. Optional prüft *.alive den Adapterbetrieb, nicht zwingend ein Gerät. Zusätzliche Muster wie zigbee.0.*.temperature liefern Messwerte. Ein ausgeschaltetes Licht gilt nicht als offline. Die Werte dienen der KI als Fragekontext. Es werden keine Zustandslisten oder einzelnen Statusmeldungen in der Oberfläche ausgegeben.';fieldset.append(hint);
  }
  const actions=document.createElement('div');actions.className='service-actions';
  if(service.fields.length) {
    const save=document.createElement('button');save.textContent='Speichern';save.type='button';save.onclick=()=>saveService(service,save);actions.append(save);
  }
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
  savedConfig=await api('connections');connected=true;
  for(const service of services) {
    fillService(service,savedConfig);
    setServiceStatus(service.id,service.id==='internet'?'Bereit zur Prüfung.':'Gespeicherte Einstellungen geladen');
  }
  if(savedConfig.secrets.llm_api_key) loadModels();
}
async function saveService(service,button) {
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
      else config[key]=type==='number'?Number(value):value;
    }
    if(service.id==='llm') for(const key of ['chat_llm_enabled','allow_external_llm','gemini_paid_confirmed']) config[key]=$('cfg-'+key).checked;
    await api('connections/'+service.id,{method:'PUT',body:JSON.stringify(config),signal:AbortSignal.timeout(15000)});
    const current=await api('connections');savedConfig=current;
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
  try { const result=await api('connections/'+id+'/test',{method:'POST'});setServiceStatus(id,result.message,'success'); }
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
  chat:['GESPRÄCH','Was beschäftigt dich?','Frag deinen Butler. Er bringt deine Informationen zusammen.'],
  findings:['FUNDSTÜCKE','Das ist für dich wichtig.','Relevante Hinweise aus deinen E-Mails und Dokumenten.'],
  settings:['EINSTELLUNGEN','Ein Platz für deine Verbindungen.','Verwalte deine Dienste und erzähle deinem Butler, was dir wichtig ist.']
};
function showView() {
  const hash=window.location.hash.slice(1);
  const view=hash==='findings'?'findings':(hash==='settings'||hash==='setup'||hash==='context'||hash.startsWith('service-'))?'settings':'chat';
  for(const element of document.querySelectorAll('.page-view')) element.hidden=element.dataset.view!==view;
  for(const link of document.querySelectorAll('[data-nav]')) {
    if(link.dataset.nav===view) link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');
  }
  const context=hash==='context';$('setup').hidden=context;$('context').hidden=!context;
  for(const tab of document.querySelectorAll('[data-settings-tab]')) tab.setAttribute('aria-selected',String(tab.dataset.settingsTab===(context?'context':'connections')));
  const [label,title,subtitle]=viewLabels[view];$('page-label').textContent=label;$('page-title').textContent=title;$('page-subtitle').textContent=subtitle;
  document.title='Butler · '+{chat:'Gespräch',findings:'Fundstücke',settings:'Einstellungen'}[view];
  window.scrollTo({top:0,behavior:'instant'});
}
window.addEventListener('hashchange',showView);
for(const button of document.querySelectorAll('[data-prompt]')) button.onclick=()=>{$('question').value=button.dataset.prompt;$('question').focus();};
$('question').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();if(!$('send').disabled) $('chat-form').requestSubmit();}});
showView();

restoreLogin().catch(e=>{ notice(e.message);if(location.hostname!=='127.0.0.1'&&location.hostname!=='localhost') $('pair-dialog').showModal(); });

window.addEventListener('beforeunload',event=>{ if(dirtyServices.size){event.preventDefault();event.returnValue='';} });
