/* Audio is synthesized on the Butler host; no browser/cloud TTS fallback. */
(()=>{
  let context=null,source=null,request=null,epoch=0;
  function prime(){
    if(!context||context.state==='closed')context=new (window.AudioContext||window.webkitAudioContext)();
    return context.resume();
  }
  function stop(){epoch++;request?.abort();request=null;if(source){try{source.stop();}catch{}source=null;}}
  async function speak(text,onstart=()=>{}){
    stop();const turn=epoch;const controller=new AbortController();request=controller;
    try{
      if(text.length>2000)throw new Error('Die Antwort ist zum vollständigen Vorlesen zu lang. Bitte um eine kurze Zusammenfassung.');
      const response=await fetch('/api/speech/synthesize',{method:'POST',credentials:'same-origin',signal:AbortSignal.any([controller.signal,AbortSignal.timeout(30000)]),headers:{'Content-Type':'application/json'},body:JSON.stringify({text})});
      if(!response.ok){const body=await response.json().catch(()=>null);throw new Error(typeof body?.detail==='string'?body.detail:'Lokale Sprachausgabe gerade nicht verfügbar.');}
      const bytes=await response.arrayBuffer();if(turn!==epoch)return false;
      if(!context||context.state==='closed')await prime();
      if(context.state!=='running')throw new Error('Zum Vorlesen bitte „Anhören“ an der Antwort antippen.');
      const buffer=await context.decodeAudioData(bytes);if(turn!==epoch)return false;
      const playing=context.createBufferSource();playing.buffer=buffer;playing.connect(context.destination);source=playing;
      return await new Promise(resolve=>{playing.onended=()=>{if(source===playing)source=null;resolve(turn===epoch);};onstart();playing.start();});
    }catch(error){if(turn!==epoch||controller.signal.aborted)return false;if(error.name==='TimeoutError')throw new Error('Lokale Sprachausgabe dauert zu lange. Bitte erneut „Anhören“ wählen.');throw error;}
    finally{if(request===controller)request=null;}
  }
  window.ButlerSpeech={prime,stop,speak};
})();
