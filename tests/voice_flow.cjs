// Browser API contract tests for capture/cancellation; no microphone or private audio.
const {test}=require('node:test');const assert=require('node:assert/strict');const vm=require('node:vm');const fs=require('node:fs');
const app=fs.readFileSync('frontend/app.js','utf8');
const decoder=app.slice(app.indexOf('async function readApiResponse'),app.indexOf('// End response decoding.'));
const source=app.split('// Voice lifecycle:')[1].split('// End voice lifecycle.')[0];
function harness({denied=false,pending=false,proxyFailure=false}={}){
 const elements={mic:{},'voice-stop':{},'voice-send':{checked:true},question:{dispatchEvent(){}},'text-entry':{},'chat-form':{requestSubmit(){submitted++;}}};
 let submitted=0,stopped=0,uploads=0,requests=0,time=0,interval;
 const states=[],recordings=[];let resolveMedia;
 const stream={getTracks:()=>[{stop(){stopped++;}}]};
 const media=pending?new Promise(r=>resolveMedia=r):Promise.resolve(stream);
 class Recorder{
  static isTypeSupported(type){return type==='audio/mp4';}
  constructor(stream,options){this.mimeType=options.mimeType;this.state='inactive';recordings.push(this);}
  start(){this.state='recording';}
  stop(){if(this.state!=='recording')throw Error('double stop');this.state='inactive';this.ondataavailable({data:new Blob(['fake-audio'])});queueMicrotask(()=>this.onstop());}
 }
 class AudioContext{
  state='running';async resume(){};async close(){this.state='closed';}
  createMediaStreamSource(){return{connect(){}};}
  createAnalyser(){return{fftSize:4,getFloatTimeDomainData(samples){samples.fill(.1);}};}
 }
 const context=vm.createContext({pendingSearch:null,beginSearchWait:()=>({controller:new AbortController(),finish(){}}),elements,console,Blob,Event,AbortController,Float32Array,Math,Promise,MediaRecorder:Recorder,currentUser:{id:'test-user'},authRevision:1,connected:true,chatBusy:false,
  window:{isSecureContext:true,MediaRecorder:Recorder,AudioContext,speechSynthesis:{cancel(){}}},navigator:{mediaDevices:{getUserMedia(){requests++;return denied?Promise.reject(Object.assign(new Error('denied'),{name:'NotAllowedError'})):media;}}},
  $:id=>elements[id],voiceState:(...x)=>states.push(x),api:async()=>({ready:true}),performance:{now:()=>time},setInterval:fn=>(interval=fn,1),clearInterval(){},setTimeout,clearTimeout,document:{addEventListener(){}},
  fetch:async()=>{uploads++;return{ok:!proxyFailure,status:proxyFailure?502:200,json:async()=>{if(proxyFailure)throw new SyntaxError('Unexpected token <');return{text:'Welche Lichter sind an?'};}};}});
 vm.runInContext(decoder+'\n//'+source+'\nglobalThis.probe={start:()=>elements.mic.onclick(),stop:stopVoice,active:()=>voiceConversation};',context);
 return{context,elements,states,recordings,flush:()=>new Promise(r=>setImmediate(r)),speak(){time+=150;interval();},resolve(){resolveMedia(stream);},stats:()=>({submitted,stopped,uploads,requests})};
}
test('Safari MP4 capture sends recognized text once and releases microphone',async()=>{
 const h=harness();h.context.probe.start();await h.flush();assert.equal(h.recordings[0].mimeType,'audio/mp4');h.speak();h.context.probe.start();await h.flush();
 assert.equal(h.elements.question.value,'Welche Lichter sind an?');assert.deepEqual(h.stats(),{submitted:1,stopped:1,uploads:1,requests:1});
});
test('cancel during permission request releases late stream and sends nothing',async()=>{
 const h=harness({pending:true});h.context.probe.start();await h.flush();h.context.probe.stop();h.resolve();await h.flush();
 assert.equal(h.recordings.length,0);assert.equal(h.stats().stopped,1);assert.equal(h.stats().uploads,0);
});
test('denied permission returns to idle',async()=>{
 const h=harness({denied:true});h.context.probe.start();await h.flush();assert.equal(h.context.probe.active(),false);assert.equal(h.states.at(-1)[0],'idle');
});
test('silence never uploads a recording and resets visual state',async()=>{
 const h=harness();h.context.probe.start();await h.flush();h.context.probe.start();await h.flush();assert.equal(h.stats().uploads,0);assert.equal(h.context.probe.active(),false);assert.equal(h.states.at(-1)[0],'idle');
});
test('review mode opens secondary text field without automatically submitting',async()=>{
 const h=harness();h.elements['voice-send'].checked=false;h.context.probe.start();await h.flush();h.speak();h.context.probe.start();await h.flush();assert.equal(h.stats().submitted,0);assert.equal(h.elements['text-entry'].open,true);
});

test('rapid cancellation/restart cannot open two permission requests',async()=>{
 const h=harness({pending:true});h.context.probe.start();await h.flush();h.context.probe.start();h.context.probe.start();assert.equal(h.context.probe.active(),false);h.resolve();await h.flush();assert.equal(h.stats().requests,1);assert.equal(h.stats().uploads,0);
});

test('HTML proxy error is reported as service failure and releases microphone',async()=>{
 const h=harness({proxyFailure:true});h.context.probe.start();await h.flush();h.speak();h.context.probe.start();await h.flush();assert.equal(h.stats().submitted,0);assert.equal(h.stats().stopped,1);assert.match(h.states.at(-1)[1],/Butler-Dienst.*HTTP 502/);assert.doesNotMatch(h.states.at(-1)[1],/Unexpected token/);
});
test('ending voice also cancels the pending transcription wait',()=>{
 const h=harness();let cancelled=0;
 h.context.pendingSearch={kind:'speech',cancel(){cancelled++;h.context.pendingSearch=null;}};
 h.context.probe.stop();assert.equal(cancelled,1);assert.equal(h.context.probe.active(),false);
});
