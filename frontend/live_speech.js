/* Local incremental ASR: bounded PCM buffers, one in-flight decode, no cloud speech. */
(()=>{
  const version=new URL(document.currentScript?.src||'/static/live_speech.js',location.href).search;
  function merge(left,right){
    const a=left.trim().split(/\s+/).filter(Boolean),b=right.trim().split(/\s+/).filter(Boolean);
    const key=word=>word.toLocaleLowerCase('de').replace(/[^\p{L}\p{N}]/gu,'');
    let overlap=0;
    for(let n=1;n<=Math.min(20,a.length,b.length);n++)if(a.slice(-n).every((word,i)=>key(word)&&key(word)===key(b[i])))overlap=n;
    return [...a,...b.slice(overlap)].join(' ');
  }
  function wav(samples,rate){
    const count=Math.floor(samples.length*16000/rate),bytes=new ArrayBuffer(44+count*2),view=new DataView(bytes);
    const write=(offset,text)=>{for(let i=0;i<text.length;i++)view.setUint8(offset+i,text.charCodeAt(i));};
    write(0,'RIFF');view.setUint32(4,36+count*2,true);write(8,'WAVE');write(12,'fmt ');view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);view.setUint32(24,16000,true);view.setUint32(28,32000,true);view.setUint16(32,2,true);view.setUint16(34,16,true);write(36,'data');view.setUint32(40,count*2,true);
    // Average each native-rate interval before downsampling; preserve the full duration.
    for(let i=0;i<count;i++){
      const start=Math.floor(i*rate/16000),end=Math.max(start+1,Math.floor((i+1)*rate/16000));let sum=0;
      for(let j=start;j<Math.min(end,samples.length);j++)sum+=samples[j];
      const value=Math.max(-1,Math.min(1,sum/(end-start)));view.setInt16(44+i*2,Math.round(value*(value<0?32768:32767)),true);
    }
    return new Blob([bytes],{type:'audio/wav'});
  }
  class Engine{
    constructor(rate,{transcribe,onpartial=()=>{},current=()=>true}){
      if(!Number.isFinite(rate)||rate<16000||rate>96000)throw new Error('Nicht unterstützte Mikrofon-Abtastrate.');
      Object.assign(this,{rate,transcribe,onpartial,current,frames:[],length:0,total:0,voiceEnd:0,sentVoiceEnd:0,sentTotal:0,text:'',needsReview:false,busy:null,ending:false,cancelled:false,error:null,timings:{recognition_seconds:0,model_load_seconds:0},chunks:0});
      this.controller=new AbortController();
    }
    push(samples){
      if(this.cancelled||this.ending)return;
      if(this.total+samples.length>this.rate*60){this.error=new Error('Bitte höchstens 60 Sekunden aufnehmen.');return;}
      this.frames.push(samples);this.length+=samples.length;this.total+=samples.length;
      let power=0;for(const value of samples)power+=value*value;
      if(Math.sqrt(power/Math.max(1,samples.length))>.008)this.voiceEnd=this.total;
      this.maybeSend();
    }
    maybeSend(final=false){
      if(this.cancelled||this.error||this.busy||!this.current()||(!final&&this.ending)||this.voiceEnd<=this.sentVoiceEnd)return;
      const newFrames=this.total-this.sentTotal;
      if(!final&&!(newFrames>=this.rate*6||(newFrames>=this.rate*2&&(this.total-this.voiceEnd)>=this.rate*.35)))return;
      const joined=new Float32Array(this.length);let offset=0;
      for(const part of this.frames){joined.set(part,offset);offset+=part.length;}
      // Acoustic overlap protects a word crossing a forced six-second boundary.
      const tail=joined.slice(-Math.min(joined.length,Math.round(this.rate*.6)));
      this.frames=[tail];this.length=tail.length;this.sentTotal=this.total;this.sentVoiceEnd=this.voiceEnd;
      const audio=wav(joined,this.rate);
      this.busy=(async()=>{
        await Promise.resolve(); // Assign busy before even a synchronous transport failure.
        try{
          const result=await this.transcribe(audio,this.controller.signal);
          if(this.cancelled||!this.current())return;
          this.text=merge(this.text,result.text||'');this.needsReview||=!!result.needs_review||!result.text?.trim();this.chunks++;
          for(const key of Object.keys(this.timings))this.timings[key]+=Number(result.timings?.[key]||0);
          if(this.text)this.onpartial(this.text);
        }catch(error){if(!this.cancelled)this.error=error;}
        finally{this.busy=null;if(!this.ending)this.maybeSend();}
      })();
    }
    async finish(){
      this.ending=true;
      if(this.busy)await this.busy;
      this.maybeSend(true);if(this.busy)await this.busy;
      this.frames=[];this.length=0;
      if(this.cancelled||!this.current())throw new DOMException('Abgebrochen','AbortError');
      if(!this.text)throw this.error||new Error('Keine Sprache erkannt.');
      return {text:this.text,needs_review:this.needsReview||!!this.error,partial:!!this.error,local:true,timings:this.timings,chunks:this.chunks};
    }
    cancel(){this.cancelled=true;this.controller.abort();this.frames=[];this.length=0;this.text='';}
  }
  async function create(context,source,options){
    if(!context.audioWorklet||!window.AudioWorkletNode)return null;
    let timer;
    try{await Promise.race([context.audioWorklet.addModule('/static/capture-worklet.js'+version),new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('Audioaufnahme konnte nicht vorbereitet werden.')),2500);})]);}
    finally{clearTimeout(timer);}
    if(!options.current())return null;
    const engine=new Engine(context.sampleRate,options),node=new AudioWorkletNode(context,'butler-capture'),mute=context.createGain();mute.gain.value=0;
    let resolveFlush,flushTimer,closed=false,notified=false;
    const notifyStop=()=>{if(!notified){notified=true;capture.onstop?.();}};
    const disconnect=()=>{try{source.disconnect(node);node.disconnect();mute.disconnect();}catch{}node.port.close();};
    const capture={state:'inactive',isLive:true,mimeType:'audio/wav',start(){this.state='recording';source.connect(node);node.connect(mute);mute.connect(context.destination);},stop(){
      if(this.state!=='recording')return;this.state='inactive';
      const flushed=new Promise(resolve=>{resolveFlush=resolve;flushTimer=setTimeout(()=>{engine.error=new Error('Audioaufnahme unvollständig · bitte Text prüfen.');resolve();},1000);});
      node.port.postMessage('stop');
      flushed.then(()=>{clearTimeout(flushTimer);closed=true;disconnect();notifyStop();});
    },cancel(){this.state='inactive';closed=true;clearTimeout(flushTimer);engine.cancel();resolveFlush?.();disconnect();queueMicrotask(notifyStop);},result:()=>engine.finish(),onstop:null};
    node.port.onmessage=event=>{if(closed)return;if(event.data?.stopped){resolveFlush?.();return;}if(event.data instanceof Float32Array)engine.push(event.data);};
    return capture;
  }
  window.ButlerLiveSpeech={create,Engine,merge,wav};
})();
