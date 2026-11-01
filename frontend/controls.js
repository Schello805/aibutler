// Explicit actuator configuration and confirmation; no automatic execution from model text.
window.ButlerControls={
 manage(){
  const actor=currentUser?.id;if(!actor)return;
  const input=$('cfg-iobroker_controls'),dialog=node('dialog','control-dialog'),heading=node('div','dialog-heading');
  const close=node('button','secondary','Schließen');close.type='button';close.onclick=()=>dialog.close();heading.append(node('h2','','Geräte freigeben'),close);
  const intro=node('p','hint','Nur Lampen, Steckdosen und Rollos freigeben. Keine Türschlösser, Alarmanlagen oder gefährlichen Geräte. Benutze exakte ioBroker-Datenpunkt-IDs. Bei Rollos bedeutet 100 % vollständig offen; die Rohwerte für offen und geschlossen müssen zu deinem Adapter passen. Ein Stopp benötigt einen eigenen schreibbaren Stopp-Datenpunkt.');
  const list=node('div','control-device-list'),status=node('p','hint'),add=node('button','secondary','Gerät hinzufügen'),save=node('button','','Auswahl übernehmen');add.type=save.type='button';
  function row(value={kind:'blind',open_value:100,closed_value:0}){
   const card=node('fieldset','control-device'),fields={};card.append(node('legend','','Freigegebenes Gerät'));
   for(const [key,title,type] of [['name','Name für Sprachbefehle','text'],['kind','Gerätetyp','select'],['state_id','Schalt- oder Positions-Datenpunkt','text'],['stop_id','Stopp-Datenpunkt (optional)','text'],['open_value','Rohwert vollständig offen','number'],['closed_value','Rohwert vollständig geschlossen','number']]){
    const label=node('label','',title),field=node(type==='select'?'select':'input');if(type==='select'){option(field,'blind','Rollo / Rollladen');option(field,'switch','Lampe / Steckdose');}else{field.type=type;if(type==='number')field.step='any';}field.value=value[key]??'';field.autocomplete='off';label.append(field);card.append(label);fields[key]=field;
   }
   const update=()=>{for(const key of ['stop_id','open_value','closed_value'])fields[key].parentElement.hidden=fields.kind.value!=='blind';};fields.kind.onchange=update;update();
   const remove=node('button','secondary','Aus Freigabe entfernen');remove.type='button';remove.onclick=()=>card.remove();card.append(remove);card.fields=fields;list.append(card);
  }
  try{JSON.parse(input.value||'[]').forEach(row);}catch{status.textContent='Gespeicherte Geräteliste konnte nicht gelesen werden.';}
  add.onclick=()=>row();save.onclick=()=>{
   if(currentUser?.id!==actor){dialog.close();return;}
   const values=[];
   for(const card of list.children){const fields=card.fields;const value={name:fields.name.value.trim(),kind:fields.kind.value,state_id:fields.state_id.value.trim(),stop_id:fields.stop_id.value.trim()||null,open_value:Number(fields.open_value.value),closed_value:Number(fields.closed_value.value)};if(!value.name||!value.state_id){status.textContent='Bitte für jedes Gerät Namen und Datenpunkt angeben.';return;}values.push(value);}
   input.value=JSON.stringify(values);input.dispatchEvent(new Event('input'));dialog.close();setServiceStatus('iobroker',values.length+' Geräte ausgewählt · Verbindung speichern, um die Freigabe zu übernehmen');
  };
  dialog.append(heading,intro,list,add,status,save);dialog.addEventListener('close',()=>dialog.remove(),{once:true});document.body.append(dialog);dialog.showModal();
 },
 confirmation(parent,action){
  const actor=currentUser?.id,box=node('div','control-confirmation'),hint=node('p','','Geräteaktion · '+action.label),yes=node('button','','Jetzt ausführen'),no=node('button','secondary','Nicht ausführen'),result=node('p','hint');yes.type=no.type='button';
  no.onclick=async()=>{yes.disabled=no.disabled=true;try{await api('controls/cancel',{method:'POST',body:JSON.stringify({token:action.token})});if(currentUser?.id===actor)result.textContent='Nicht ausgeführt · Auftrag verworfen.';}catch(error){if(currentUser?.id===actor)result.textContent='Hier nicht ausgeführt. Die Freigabe läuft nach fünf Minuten ab.';}};
  yes.onclick=async()=>{if(currentUser?.id!==actor)return;yes.disabled=no.disabled=true;result.textContent='Befehl wird übermittelt …';try{const reply=await api('controls/execute',{method:'POST',body:JSON.stringify({token:action.token}),signal:AbortSignal.timeout(30000)});if(currentUser?.id===actor)result.textContent=reply.answer;}catch(error){if(currentUser?.id===actor)result.textContent=error.name==='TimeoutError'?'Die Bestätigung fehlt. Möglicherweise wurde bereits geschaltet. Bitte den Gerätezustand prüfen; nicht automatisch erneut senden.':error.message;}};
  box.append(hint,yes,no,result);parent.append(box);
 }
};
