/* Local-only audio preparation. Nothing plays until speak() is explicitly called. */
(()=>{
  let context=null,source=null,request=null,active=null,epoch=0;
  const cache=new Map(),MAX_BYTES=8*1024*1024;
  function prime(){
    if(!context||context.state==='closed')context=new (window.AudioContext||window.webkitAudioContext)();
    return context.resume();
  }
  function stop(){epoch++;request?.abort();request=null;if(source){try{source.stop();source.disconnect?.();}catch{}source=null;}}
  function clear(){stop();for(const entry of cache.values())entry.controller.abort();cache.clear();}
  function entryFor(text,background){
    if(text.length>2000)throw new Error('Die Antwort ist zum vollständigen Vorlesen zu lang. Bitte um eine kurze Zusammenfassung.');
    let entry=cache.get(text);
    if(entry&&Date.now()-entry.created>300000){entry.controller.abort();cache.delete(text);entry=null;}
    if(entry){if(background)entry.background=true;return entry;}
    for(const [key,value] of cache){if(cache.size<2)break;if(value!==active){value.controller.abort();cache.delete(key);}}
    entry={controller:new AbortController(),background,created:Date.now()};cache.set(text,entry);
    entry.promise=(async()=>{
      try{
        const response=await fetch('/api/speech/synthesize',{method:'POST',credentials:'same-origin',signal:AbortSignal.any([entry.controller.signal,AbortSignal.timeout(30000)]),headers:{'Content-Type':'application/json'},body:JSON.stringify({text})});
        if(!response.ok){const body=await response.json().catch(()=>null);throw new Error(typeof body?.detail==='string'?body.detail:'Lokale Sprachausgabe gerade nicht verfügbar.');}
        const bytes=await response.arrayBuffer();if(bytes.byteLength>MAX_BYTES)throw new Error('Sprachausgabe ist zu groß. Bitte um eine kürzere Antwort.');
        if(entry.controller.signal.aborted)throw new DOMException('Aborted','AbortError');
        return bytes;
      }catch(error){if(cache.get(text)===entry)cache.delete(text);throw error;}
    })();
    return entry;
  }
  async function prepare(text){await entryFor(text,true).promise;return true;}
  async function speak(text,onstart=()=>{}){
    stop();const turn=epoch,controller=new AbortController();request=controller;
    let entry;
    try{
      entry=entryFor(text,false);active=entry;
      const aborted=new Promise((_,reject)=>controller.signal.addEventListener('abort',()=>{if(!entry.background)entry.controller.abort();reject(new DOMException('Aborted','AbortError'));},{once:true}));
      const bytes=await Promise.race([entry.promise,aborted]);if(turn!==epoch)return false;
      if(!context||context.state==='closed')await prime();
      if(context.state!=='running')throw new Error('Zum Vorlesen bitte „Anhören“ an der Antwort antippen.');
      const buffer=await context.decodeAudioData(bytes.slice(0));if(turn!==epoch)return false;
      const playing=context.createBufferSource();playing.buffer=buffer;playing.connect(context.destination);source=playing;
      return await new Promise(resolve=>{playing.onended=()=>{if(source===playing)source=null;resolve(turn===epoch);};onstart();playing.start();});
    }catch(error){if(turn!==epoch||controller.signal.aborted)return false;if(error.name==='TimeoutError')throw new Error('Lokale Sprachausgabe dauert zu lange. Bitte erneut „Anhören“ wählen.');throw error;}
    finally{if(request===controller)request=null;if(active===entry)active=null;}
  }
  window.ButlerSpeech={prime,stop,clear,prepare,speak};
})();
