/* Explicit synthetic-only provider comparison; no user documents are sent. */
(()=>{
 let poll=null,controller=null;
 async function open(){
  const provider=$('cfg-llm_provider').value,key=$('cfg-llm_api_key').value,actor=currentUser?.id;
  const dialog=node('dialog','benchmark-dialog');const header=node('div','cardhead');header.append(node('h2','','Modelle vergleichen'));const close=node('button','secondary','Schließen');close.type='button';close.onclick=()=>{clearTimeout(poll);controller?.abort();dialog.close();dialog.remove();};header.append(close);dialog.append(header,node('p','hint','Vergleicht bis zu drei Modelle desselben Anbieters mit jeweils drei identischen, künstlichen Testfragen. Es werden keine privaten Quellen verwendet. API-Kosten können entstehen. Der Vergleich läuft nach dem Schließen auf dem Server weiter.'));
  const form=node('form','benchmark-form');const choices=[];
  for(let index=0;index<3;index++){
   const label=node('label','','Modell '+(index+1)),select=node('select');select.required=index===0;option(select,'',index===0?'Modell wählen':'Nicht vergleichen');
   for(const model of $('cfg-llm_model').options)if(model.value)option(select,model.value,model.textContent);
   if(index===0)select.value=$('cfg-llm_model').value;choices.push(select);label.append(select);form.append(label);
  }
  const start=node('button','','Vergleich starten'),cancel=node('button','secondary','Vergleich abbrechen');cancel.type='button';cancel.hidden=true;const status=node('p','hint'),results=node('div','benchmark-results');form.append(start,cancel);dialog.append(form,status,results);document.body.append(dialog);dialog.showModal();const storageKey='butler-benchmark-'+actor+'-'+provider;let identity=localStorage.getItem(storageKey);
  dialog.addEventListener('cancel',event=>{event.preventDefault();close.click();});
  cancel.onclick=async()=>{if(identity)try{await api('benchmarks/'+identity+'/cancel',{method:'POST'});}catch(error){status.textContent=error.message;}};
  function render(state){
   status.textContent=(state.status==='running'?'Vergleich läuft: ':state.status==='done'?'Vergleich abgeschlossen: ':'Vergleich unterbrochen: ')+state.completed+' / '+state.total+' Testfragen';results.replaceChildren();
   const table=node('table');const head=node('tr');for(const title of ['Modell / Test','Erster Text','Gesamt','Prüfung'])head.append(node('th','',title));table.append(head);
   for(const row of state.results){const tr=node('tr');for(const cell of [row.model+' · '+row.case,row.first_token_seconds==null?'–':row.first_token_seconds.toFixed(2)+' s',row.total_seconds.toFixed(2)+' s',row.ok?(row.fact_check?'Testdaten korrekt':'Antwort prüfen'):'Fehler / Zeitlimit'])tr.append(node('td','',cell));table.append(tr);const detail=node('details');detail.append(node('summary','',row.model+' · '+row.case+' · Antwort ansehen'),node('p','',row.answer||row.error));results.append(detail);}
   results.prepend(table);
   if(state.status!=='running'){
    const groups=new Map();for(const row of state.results){if(!groups.has(row.model))groups.set(row.model,[]);groups.get(row.model).push(row);}
    const ranked=[...groups.entries()].filter(([,rows])=>rows.length===3&&rows.every(row=>row.ok&&row.fact_check)).sort((a,b)=>a[1].reduce((sum,row)=>sum+row.total_seconds,0)-b[1].reduce((sum,row)=>sum+row.total_seconds,0));
    if(ranked.length){const winner=ranked[0][0];results.append(node('p','hint','Schnellstes Modell mit drei bestandenen Testfällen: '+winner+'. Das ist ein kleiner Funktionstest, keine umfassende Qualitätsbewertung.'));const use=node('button','secondary','Dieses Modell als Standard verwenden');use.type='button';use.onclick=async()=>{if(actor!==currentUser?.id){close.click();return;}if(provider!==$('cfg-llm_provider').value||key!==$('cfg-llm_api_key').value){status.textContent='Anbieter oder Schlüssel wurde zwischenzeitlich geändert. Bitte Vergleich neu starten.';return;}const select=$('cfg-llm_model');if(![...select.options].some(option=>option.value===winner))option(select,winner,winner);select.value=winner;dirtyServices.add('llm');await saveService(services.find(service=>service.id==='llm'),use);};results.append(use);}
   }
  }
  async function update(){
   if(!dialog.isConnected)return;if(actor!==currentUser?.id){close.click();return;}controller=new AbortController();
   try{const state=await api('benchmarks/'+identity,{signal:controller.signal});if(!dialog.isConnected||actor!==currentUser?.id)return;render(state);if(state.status==='running')poll=setTimeout(update,1000);else{start.disabled=false;cancel.hidden=true;}}catch(error){if(dialog.isConnected){status.textContent=error.message;start.disabled=false;}}
  }
  form.onsubmit=async event=>{event.preventDefault();if(actor!==currentUser?.id){close.click();return;}start.disabled=true;results.replaceChildren();status.textContent='Vergleich wird gestartet …';try{const result=await api('benchmarks',{method:'POST',body:JSON.stringify({provider,api_key:key,models:[...new Set(choices.map(select=>select.value).filter(Boolean))],gemini_paid_confirmed:$('cfg-gemini_paid_confirmed').checked})});identity=result.id;localStorage.setItem(storageKey,identity);cancel.hidden=false;update();}catch(error){status.textContent=error.message;start.disabled=false;}};
  if(identity){start.disabled=true;cancel.hidden=false;update();}
 }
 window.ButlerBenchmark={open};
})();
