// Synthetic PCM and fake HTTP only: no microphone, provider, personal data or devices.
const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const code=fs.readFileSync('frontend/live_speech.js','utf8');
function load(extra={}){
 const ctx=vm.createContext({window:{},document:{currentScript:{src:'http://localhost/static/live_speech.js?v=fixture'}},location:{href:'http://localhost/'},URL,Blob,ArrayBuffer,DataView,Float32Array,AbortController,DOMException,setTimeout,clearTimeout,queueMicrotask,...extra});
 vm.runInContext(code,ctx);return {api:ctx.window.ButlerLiveSpeech,ctx};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const speech=(seconds,level=.1,rate=16000)=>new Float32Array(seconds*rate).fill(level);
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}

test('transcribes during capture, publishes draft, and decodes only the remaining tail at finish',async()=>{
 const {Engine}=load().api,calls=[],drafts=[],first=deferred();
 const engine=new Engine(16000,{transcribe:audio=>{calls.push(audio);return calls.length===1?first.promise:Promise.resolve({text:'meinen Arbeitsvertrag lesen.',timings:{recognition_seconds:.2}});},onpartial:text=>drafts.push(text)});
 engine.push(speech(6));await tick();assert.equal(calls.length,1);assert.equal(drafts.length,0);
 engine.push(speech(2));first.resolve({text:'Bitte meinen Arbeitsvertrag',timings:{recognition_seconds:.3}});await tick();
 assert.equal(drafts[0],'Bitte meinen Arbeitsvertrag');assert.equal(calls.length,1);
 const final=await engine.finish();assert.equal(final.text,'Bitte meinen Arbeitsvertrag lesen.');assert.equal(final.needs_review,false);
 assert.equal(calls.length,2);assert.equal(final.timings.recognition_seconds,.5);
 const bytes=await calls[1].arrayBuffer();assert.equal((bytes.byteLength-44)/32000,2.6);
});

test('a phrase pause starts recognition before automatic sentence-end waiting expires',async()=>{
 const {Engine}=load().api,calls=[];
 const engine=new Engine(16000,{transcribe:async audio=>{calls.push(audio);return {text:'Welche Termine habe ich?'};}});
 engine.push(speech(2));engine.push(speech(.4,0));await tick();assert.equal(calls.length,1);
 engine.push(speech(1,0));const result=await engine.finish();assert.equal(calls.length,1);assert.equal(result.text,'Welche Termine habe ich?');
});

test('slow recognizer has one in-flight job and coalesces pending audio without a request backlog',async()=>{
 const {Engine}=load().api,first=deferred(),calls=[];let active=0,maxActive=0;
 const engine=new Engine(16000,{transcribe:async audio=>{calls.push(audio);active++;maxActive=Math.max(active,maxActive);const result=calls.length===1?await first.promise:{text:'Fortsetzung.'};active--;return result;}});
 engine.push(speech(6));await tick();for(let i=0;i<3;i++)engine.push(speech(6));await tick();assert.equal(calls.length,1);
 const finished=engine.finish();first.resolve({text:'Anfang.'});const result=await finished;
 assert.equal(calls.length,2);assert.equal(maxActive,1);assert.equal(result.text,'Anfang. Fortsetzung.');
 assert.equal(((await calls[1].arrayBuffer()).byteLength-44)/32000,18.6);
});

test('cancel aborts transport and discards late text without starting any remaining decode',async()=>{
 const {Engine}=load().api,pending=deferred(),drafts=[],calls=[];
 const engine=new Engine(16000,{transcribe:(audio,signal)=>{calls.push(signal);return pending.promise;},onpartial:text=>drafts.push(text)});
 engine.push(speech(6));await tick();engine.push(speech(2));engine.cancel();assert.equal(calls[0].aborted,true);
 pending.resolve({text:'Späte Antwort'});await tick();await assert.rejects(engine.finish(),{name:'AbortError'});
 assert.equal(calls.length,1);assert.equal(drafts.length,0);assert.equal(engine.frames.length,0);
});

test('changing accounts suppresses late drafts and the final result',async()=>{
 const {Engine}=load().api,pending=deferred();let current=true;const drafts=[];
 const engine=new Engine(16000,{transcribe:()=>pending.promise,onpartial:text=>drafts.push(text),current:()=>current});
 engine.push(speech(6));await tick();current=false;pending.resolve({text:'Anderes Konto'});await tick();
 await assert.rejects(engine.finish(),{name:'AbortError'});assert.equal(drafts.length,0);
});

test('transport failure preserves a partial transcript for review and cannot masquerade as a complete question',async()=>{
 const {Engine}=load().api;let count=0;
 const engine=new Engine(16000,{transcribe:async()=>{if(count++)throw new Error('offline');return {text:'Bitte prüfe'};}});
 engine.push(speech(6));await tick();engine.push(speech(2));const result=await engine.finish();
 assert.equal(result.text,'Bitte prüfe');assert.equal(result.partial,true);assert.equal(result.needs_review,true);
});

test('synchronous transport failure is bounded and does not leave a phantom in-flight job',async()=>{
 const {Engine}=load().api;let calls=0;
 const engine=new Engine(16000,{transcribe:()=>{calls++;throw new Error('synthetic failure');}});
 engine.push(speech(6));await tick();await assert.rejects(engine.finish(),/synthetic failure/);assert.equal(calls,1);assert.equal(engine.busy,null);
});

test('WAV frames are mono 16 kHz PCM with bounded, signed samples at Safari native rates',async()=>{
 const {wav}=load().api;
 for(const rate of [44100,48000]){
  const bytes=await wav(speech(1,.5,rate),rate).arrayBuffer(),view=new DataView(bytes);
  assert.equal(view.getUint32(24,true),16000);assert.equal(view.getUint16(22,true),1);assert.equal(view.getUint32(40,true),32000);
  assert.equal(view.getInt16(44,true),16384);
 }
 const bytes=await wav(new Float32Array([-2,2]),16000).arrayBuffer(),view=new DataView(bytes);
 assert.equal(view.getInt16(44,true),-32768);assert.equal(view.getInt16(46,true),32767);
});

test('silent audio never starts recognition and captures are bounded to sixty seconds',async()=>{
 const {Engine}=load().api;let calls=0;
 const quiet=new Engine(16000,{transcribe:async()=>{calls++;return {text:'Erfunden'};}});
 quiet.push(speech(8,0));await assert.rejects(quiet.finish(),/Keine Sprache/);assert.equal(calls,0);
 const long=new Engine(16000,{transcribe:async()=>{calls++;return {text:'Erfunden'};}});
 long.push(speech(61));await assert.rejects(long.finish(),/60 Sekunden/);assert.equal(calls,0);assert.equal(long.frames.length,0);
});

test('exact acoustic overlap is merged without losing dates or negation',()=>{
 const {merge}=load().api;
 assert.equal(merge('Am 12. November','12. November um acht.'),'Am 12. November um acht.');
 assert.equal(merge('Bitte nicht','nicht das Licht einschalten.'),'Bitte nicht das Licht einschalten.');
 assert.equal(merge('Rollo Wohnzimmer','Wohnzimmer auf 0 Prozent.'),'Rollo Wohnzimmer auf 0 Prozent.');
});

function captureHarness(){
 const messages=[];let node;
 class WorkletNode{constructor(){node=this;this.port={onmessage:null,postMessage:value=>messages.push(value),close(){}};}connect(){}disconnect(){}}
 const {api}=load({AudioWorkletNode:WorkletNode,window:{AudioWorkletNode:WorkletNode}});
 const context={sampleRate:48000,destination:{},audioWorklet:{async addModule(url){assert.equal(url,'/static/capture-worklet.js?v=fixture');}},createGain:()=>({gain:{},connect(){},disconnect(){}})};
 return {api,context,source:{connect(){},disconnect(){}},getNode:()=>node,messages};
}

test('stop waits for the worklet final block before exposing the full result',async()=>{
 const h=captureHarness(),calls=[];
 const capture=await h.api.create(h.context,h.source,{current:()=>true,transcribe:async audio=>{calls.push(audio);return {text:'Vollständiges Satzende.'};}});
 let stopped=0;capture.onstop=()=>stopped++;capture.start();h.getNode().port.onmessage({data:speech(.2,.1,48000)});capture.stop();
 assert.equal(stopped,0);h.getNode().port.onmessage({data:speech(.05,.1,48000)});h.getNode().port.onmessage({data:{stopped:true}});await tick();
 assert.equal(stopped,1);const result=await capture.result();assert.equal(result.text,'Vollständiges Satzende.');
 assert.equal(((await calls[0].arrayBuffer()).byteLength-44)/32000,.25);
});

test('cancel releases capture immediately and cannot deliver stop twice after a pending flush',async()=>{
 const h=captureHarness(),capture=await h.api.create(h.context,h.source,{current:()=>true,transcribe:async()=>({text:'Kein Aufruf'})});
 let stopped=0;capture.onstop=()=>stopped++;capture.start();capture.stop();capture.cancel();await tick();
 assert.equal(capture.state,'inactive');assert.equal(stopped,1);await assert.rejects(capture.result(),{name:'AbortError'});
});

test('unsupported worklet returns fallback without using a browser cloud recognizer',async()=>{
 const {create}=load().api;assert.equal(await create({}, {}, {}),null);
});

test('audio-thread processor flushes a final incomplete block before its stop acknowledgement',()=>{
 let Processor;const output=[];
 class Base{constructor(){this.port={postMessage:value=>output.push(value)};}}
 const ctx=vm.createContext({Float32Array,AudioWorkletProcessor:Base,registerProcessor:(name,ctor)=>Processor=ctor});
 vm.runInContext(fs.readFileSync('frontend/capture-worklet.js','utf8'),ctx);
 const node=new Processor();node.process([[speech(100/16000)]]);assert.equal(output.length,0);
 node.port.onmessage({data:'stop'});assert.equal(output[0].length,100);assert.equal(output[1].stopped,true);assert.equal(node.process([]),false);
});
