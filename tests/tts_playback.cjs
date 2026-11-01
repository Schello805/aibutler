const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const code=fs.readFileSync('frontend/speech.js','utf8');
function setup(){const calls=[],sources=[];let resolve;
 class AudioContext{state='running';destination={};async resume(){}async decodeAudioData(){return{};}createBufferSource(){const source={connect(){},start(){},stop(){this.onended?.();}};sources.push(source);return source;}}
 const ctx=vm.createContext({AbortController,AbortSignal,DOMException,Error,Promise,window:{AudioContext},fetch:(url,options)=>{calls.push({url,options});return new Promise((r,reject)=>{resolve=r;options.signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')));});}});vm.runInContext(code,ctx);
 return{speech:ctx.window.ButlerSpeech,calls,sources,respond(){resolve({ok:true,arrayBuffer:async()=>new ArrayBuffer(16)});},flush:()=>new Promise(r=>setImmediate(r))};
}
test('neural playback sends text only to the same-origin local API',async()=>{const h=setup();await h.speech.prime();const played=h.speech.speak('Hallo Michael.');h.respond();await h.flush();assert.equal(h.calls[0].url,'/api/speech/synthesize');assert.equal(h.calls[0].options.credentials,'same-origin');h.sources[0].onended();assert.equal(await played,true);});
test('stop during synthesis prevents any late audio from playing',async()=>{const h=setup();await h.speech.prime();const played=h.speech.speak('Hallo Michael.');h.speech.stop();assert.equal(await played,false);assert.equal(h.sources.length,0);assert.equal(h.calls[0].options.signal.aborted,true);});
