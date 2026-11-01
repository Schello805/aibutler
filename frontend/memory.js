/* Account-private memory UI. All data is rendered as text, never HTML. */
window.ButlerMemory=(()=>{
  let rows=[],edited=false,loading=null,generation=0;const dirtyForms=new Set();
  const element=id=>document.getElementById(id);
  const make=(tag,cls,text)=>{const el=document.createElement(tag);if(cls)el.className=cls;if(text!==undefined)el.textContent=text;return el;};
  const names={preference:'Vorliebe',project:'Vorhaben',fact:'Persönliche Angabe'};
  const states={active:'Aktiv',pending:'Bestätigung offen',archived:'Erledigt / inaktiv',superseded:'Durch neuere Angabe ersetzt'};
  const date=value=>{if(!value)return '—';if(value.length===10)return value.split('-').reverse().join('.');const parsed=new Date(value.includes('T')?value:value.replace(' ','T')+'Z');return Number.isNaN(parsed.getTime())?'—':parsed.toLocaleDateString('de-DE',{timeZone:'Europe/Berlin'});};
  const status=text=>{element('memory-status').textContent=text;};
  function markDirty(form){dirtyForms.add(form);edited=true;element('memory-discard').hidden=false;}
  function saved(form){dirtyForms.delete(form);edited=dirtyForms.size>0;element('memory-discard').hidden=!edited;}
  async function action(path,method='POST',body){
    const revision=authRevision,result=await api('memories'+path,{method,...(body?{body:JSON.stringify(body)}:{})});
    if(revision!==authRevision)throw new Error('Anmeldung wurde gewechselt.');generation++;loading=null;return result;
  }
  function fields(form,row={}){
    const inputs={};
    for(const [key,title,type] of [['content','Inhalt','textarea'],['kind','Art','select'],['valid_from','Gültig ab (optional)','date'],['valid_until','Gültig bis (optional)','date']]){
      const label=make('label','',title),input=document.createElement(['textarea','select'].includes(type)?type:'input');
      if(type==='select'){for(const [value,name] of Object.entries(names)){const option=make('option','',name);option.value=value;input.append(option);}}
      else if(type==='textarea'){input.rows=3;input.maxLength=2000;input.minLength=2;input.required=true;}
      else input.type=type;
      input.value=row[key]||'';input.oninput=()=>markDirty(form);label.append(input);form.append(label);inputs[key]=input;
    }
    return inputs;
  }
  function render(){
    const overview=element('memory-overview-count');if(overview)overview.textContent=rows.filter(row=>row.status==='active').length+' gespeichert'+(rows.some(row=>row.status==='pending')?' · '+rows.filter(row=>row.status==='pending').length+' Vorschläge':'');
    const list=element('memory-list');list.replaceChildren();const filter=element('memory-filter').value;
    const selected=rows.filter(row=>filter==='all'||row.status===filter);
    if(!selected.length)list.append(make('p','memory-empty',filter==='pending'?'Keine offenen Vorschläge.':'Hier erscheinen deine Erinnerungen. Sage zum Beispiel: „Merk dir, dass ich kurze Antworten bevorzuge.“'));
    for(const row of selected){
      const card=make('article','memory-card'),header=make('div','memory-card-header');
      const now=new Intl.DateTimeFormat('sv-SE',{timeZone:'Europe/Berlin'}).format(new Date()),expired=row.valid_until&&row.valid_until<now,future=row.valid_from&&row.valid_from>now;
      header.append(make('span','badge',names[row.kind]),make('span','memory-state',expired?'Abgelaufen':future?'Gilt künftig':states[row.status]));
      card.append(header,make('p','memory-text',row.content));
      card.append(make('p','hint','Erfasst: '+date(row.created)+' · '+(row.status==='pending'?'Vorgeschlagen: ':'Bestätigt/geändert: ')+date(row.updated)+(row.source==='chat'?' · Aus einem Chat':' · Manuell eingetragen')));
      if(row.source_date)card.append(make('p','hint','Quelldatum: '+date(row.source_date)));
      if(row.valid_from||row.valid_until)card.append(make('p','hint','Gültigkeit: '+date(row.valid_from)+' bis '+date(row.valid_until)));
      if(row.needs_date)card.append(make('p','memory-conflict','Bitte vor dem Bestätigen unter Bearbeiten das Feld Gültig ab ergänzen.'));
      if(row.conflicts?.length){const box=make('div','memory-conflict','Bisherige Angabe: '+row.conflicts.map(old=>old.content).join(' · '));card.append(box);}
      const buttons=make('div','memory-actions');
      const button=(label,handler,cls='secondary')=>{const el=make('button',cls,label);el.type='button';el.onclick=async()=>{if(edited){status('Bitte zuerst die offenen Änderungen speichern oder Entwürfe verwerfen.');return;}el.disabled=true;try{await handler();}catch(e){status(e.message);}finally{el.disabled=false;}};buttons.append(el);return el;};
      if(row.status==='pending'){
        button(row.conflicts?.length?'Neue Angabe übernehmen':'Übernehmen',async()=>{await action('/'+row.id+'/confirm');edited=false;await load();},'');
        button('Nicht übernehmen',async()=>{await action('/'+row.id+'/reject');await load();});
      }
      if(row.status==='active')button(row.kind==='project'?'Als erledigt markieren':'Inaktiv setzen',async()=>{await action('/'+row.id+'/archive');await load();});
      if(row.conversation_id)button('Quellgespräch',()=>openConversation(row.conversation_id));
      button('Löschen',async()=>{if(!confirm('Erinnerung löschen? Der ursprüngliche Chat bleibt erhalten.'))return;await action('/'+row.id,'DELETE');await load();});
      card.append(buttons);
      const details=make('details','memory-edit'),summary=make('summary','','Bearbeiten');details.append(summary);const form=make('form','memory-form'),inputs=fields(form,row),save=make('button','','Änderungen speichern');form.append(save);
      form.onsubmit=async event=>{event.preventDefault();save.disabled=true;try{const body={revision:row.revision};for(const [key,input] of Object.entries(inputs))body[key]=input.value||null;const changed=await action('/'+row.id,'PUT',body);Object.assign(row,changed);saved(form);await load();}catch(e){status(e.message);}finally{save.disabled=false;}};
      details.append(form);card.append(details);list.append(card);
    }
  }
  async function load(){
    if(!currentUser||document.body.dataset.view!=='knowledge')return;
    if(edited){status('Du hast ungespeicherte Änderungen. Bitte zuerst speichern.');return;}
    if(loading)return loading;
    const revision=authRevision,version=generation;
    const pending=(async()=>{try{const result=await api('memories');if(revision!==authRevision||version!==generation)return;if(edited){status('Du hast ungespeicherte Änderungen. Bitte zuerst speichern.');return;}rows=result.memories;render();status(rows.filter(row=>row.status==='active').length+' bestätigte Einträge · '+rows.filter(row=>row.status==='pending').length+' offene Vorschläge');}catch(e){if(revision===authRevision)status(e.message);}})();
    loading=pending;try{await pending;}finally{if(loading===pending)loading=null;}
  }
  function proposal(parent,value){
    const actor=currentUser?.id,revision=authRevision;const box=make('div','memory-proposal');if(value.status&&(value.action==='archive'?value.status!=='active':value.status!=='pending')){box.append(make('p','',value.status==='active'?'Bereits unter „Mein Wissen“ gespeichert.':'Dieser Vorschlag ist nicht mehr offen.'));parent.append(box);return;}box.append(make('strong','',value.action==='archive'?'Vorhaben als erledigt markieren?':'Für spätere Gespräche merken?'),make('p','',value.content));
    if(value.conflicts?.length)box.append(make('p','memory-conflict','Bisher: '+value.conflicts.map(row=>row.content).join(' · ')));
    const actions=make('div','memory-actions');
    for(const [label,path] of (value.action==='archive'?[['Ja, als erledigt markieren','archive'],['Offen lassen','keep']]:[[value.conflicts?.length?'Bisherige Angabe ersetzen':'Ja, merken','confirm'],['Nicht merken','reject']])){
      const button=make('button','secondary',label);button.type='button';button.onclick=async()=>{
        for(const child of actions.children)child.disabled=true;
        try{if(path!=='keep')await action('/'+value.id+'/'+path);if(actor!==currentUser?.id||revision!==authRevision)return;box.replaceChildren(make('p','',path==='archive'?'Vorhaben als erledigt markiert.':path==='keep'?'Status nicht geändert.':path==='confirm'?'Gemerkt. Unter „Mein Wissen“ kannst du das ändern.':'Nicht ins Langzeitgedächtnis übernommen.'));}
        catch(e){if(actor===currentUser?.id&&revision===authRevision){box.append(make('p','hint',e.message));for(const child of actions.children)child.disabled=false;}}
      };actions.append(button);
    }
    box.append(actions);parent.append(box);
  }
  element('memory-filter').onchange=()=>{if(!edited)render();else status('Bitte zuerst Änderungen speichern.');};
  element('memory-reload').onclick=()=>load();
  element('memory-discard').onclick=()=>{dirtyForms.clear();edited=false;element('memory-discard').hidden=true;element('memory-add-form').reset();load();};
  element('memory-discover').onclick=async event=>{if(edited){status('Bitte zuerst Änderungen speichern oder Entwürfe verwerfen.');return;}event.target.disabled=true;try{const result=await action('/discover');element('memory-filter').value='pending';await load();status(result.proposed+' neue Vorschläge aus bis zu 500 deiner letzten Chatnachrichten. Noch nichts automatisch übernommen.');}catch(e){status(e.message);}finally{event.target.disabled=false;}};
  element('memory-add-form').oninput=event=>markDirty(event.currentTarget);
  element('memory-add-form').onsubmit=async event=>{event.preventDefault();const button=event.target.querySelector('button');button.disabled=true;try{const result=await action('','POST',{content:element('memory-content').value,kind:element('memory-kind').value,valid_from:element('memory-from').value||null,valid_until:element('memory-until').value||null});saved(event.target);event.target.reset();element('memory-filter').value=result.status==='pending'?'pending':'active';await load();}catch(e){status(e.message);}finally{button.disabled=false;}};
  return {load,proposal,dirty:()=>edited,clear(){generation++;rows=[];edited=false;dirtyForms.clear();element('memory-discard').hidden=true;loading=null;element('memory-list').replaceChildren();if(element('memory-overview-count'))element('memory-overview-count').textContent='—';element('memory-add-form').reset();status('');}};
})();
