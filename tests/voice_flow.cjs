// Browser API contract tests for capture/cancellation; no microphone or private audio.
const {test}=require('node:test');const assert=require('node:assert/strict');const vm=require('node:vm');const fs=require('node:fs');
const app=fs.readFileSync('frontend/app.js','utf8');
const decoder=app.slice(app.indexOf('async function readApiResponse'),app.indexOf('// End response decoding.'));
const source=app.split('// Voice lifecycle:')[1].split('// End voice lifecycle.')[0];
function harness({denied=false,pending=false,proxyFailure=false,needsReview=false,live=false,liveFailure=false,pendingLive=false,setupPending=false}={}){
 const elements={'voice-hint':{},'voice-transcript':{},mic:{},'voice-stop':{},'voice-send':{checked:true},question:{dispatchEvent(){}},'text-entry':{},'chat-form':{requestSubmit(){submitted++;}}};
 let submitted=0,stopped=0,uploads=0,requests=0,time=0,interval,level=.1,uploadOptions;
 const states=[],recordings=[],events={};let resolveMedia,liveOptions,resolveLive,rejectLive;
 let resolveSetup;const setup=setupPending?new Promise(resolve=>resolveSetup=resolve):Promise.resolve();
 const liveResult=pendingLive?new Promise((resolve,reject)=>{resolveLive=resolve;rejectLive=reject;}):Promise.resolve({text:'Welche Lichter sind an?',needs_review:needsReview});
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
  createAnalyser(){return{fftSize:4,getFloatTimeDomainData(samples){samples.fill(level);}};}
 }
 const context=vm.createContext({pendingSearch:null,beginSearchWait:()=>({controller:new AbortController(),finish(){}}),elements,console,Blob,Event,AbortController,Float32Array,Math,Promise,MediaRecorder:Recorder,currentUser:{id:'test-user'},authRevision:1,connected:true,chatBusy:false,
  window:{isSecureContext:true,MediaRecorder:Recorder,AudioContext,speechSynthesis:{cancel(){}},...(live||liveFailure?{ButlerLiveSpeech:{async create(context,source,options){await setup;if(!options.current())return null;if(liveFailure)throw Error('worklet unavailable');liveOptions=options;let notified=false;const done=()=>{if(!notified){notified=true;queueMicrotask(()=>capture.onstop());}};const capture={state:'inactive',isLive:true,start(){this.state='recording';},stop(){this.state='inactive';done();},cancel(){this.state='inactive';rejectLive?.(new DOMException('cancelled','AbortError'));done();},result:()=>liveResult};recordings.push(capture);return capture;}}}: {})},navigator:{mediaDevices:{getUserMedia(){requests++;return denied?Promise.reject(Object.assign(new Error('denied'),{name:'NotAllowedError'})):media;}}},
  $:id=>elements[id],voiceState:(...x)=>states.push(x),api:async()=>({ready:true}),performance:{now:()=>time},setInterval:fn=>(interval=fn,1),clearInterval(){},setTimeout,clearTimeout,document:{hidden:false,addEventListener(name,fn){events[name]=fn;}},
  fetch:async(url,options)=>{uploadOptions=options;uploads++;return{ok:!proxyFailure,status:proxyFailure?502:200,json:async()=>{if(proxyFailure)throw new SyntaxError('Unexpected token <');return{text:'Welche Lichter sind an?',needs_review:needsReview};}};}});
 vm.runInContext(decoder+'\n//'+source+'\nglobalThis.probe={start:()=>elements.mic.onclick(),stop:stopVoice,active:()=>voiceConversation};',context);
 return{context,elements,states,recordings,resolveSetup:()=>resolveSetup(),partial:text=>liveOptions.onpartial(text),finishLive:()=>resolveLive({text:'Welche Lichter sind an?'}),hide(){context.document.hidden=true;events.visibilitychange();},flush:()=>new Promise(r=>setImmediate(r)),speak(){level=.1;time+=150;interval();},quiet(ms){level=0;time+=ms;interval();},resolve(){resolveMedia(stream);},uploadOptions:()=>uploadOptions,stats:()=>({submitted,stopped,uploads,requests})};
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
test('locking the phone stops recording but preserves an accepted chat request',async()=>{
 const h=harness();h.context.probe.start();await h.flush();let cancelled=0;
 h.context.pendingSearch={kind:'chat',cancel(){cancelled++;}};
 h.hide();await h.flush();assert.equal(cancelled,0);assert.equal(h.context.probe.active(),false);assert.equal(h.stats().uploads,0);assert.equal(h.stats().stopped,1);
});

test('a thinking pause does not cut off the rest of a spoken question',async()=>{
 const h=harness();h.elements['speech-pause']={value:'3500'};h.context.probe.start();await h.flush();h.speak();h.quiet(2200);
 assert.equal(h.recordings[0].state,'recording');assert.equal(h.stats().uploads,0);
 h.speak();h.quiet(3400);assert.equal(h.recordings[0].state,'recording');
 h.quiet(200);await h.flush();assert.equal(h.stats().uploads,1);assert.equal(h.stats().submitted,1);
});

test('recognition profiles preserve a selected long thinking pause',async()=>{
 for(const mode of ['fast','balanced','accurate']){
  const h=harness();h.elements['speech-mode']={value:mode};h.elements['speech-pause']={value:'3500'};h.context.probe.start();await h.flush();h.speak();h.quiet(2200);assert.equal(h.stats().uploads,0);h.context.probe.start();await h.flush();
  assert.equal(h.uploadOptions().headers['X-Speech-Mode'],mode);assert.equal(h.stats().submitted,1);
 }
});

test('short default pause sends after 1.3 seconds and keeps a one-second hesitation',async()=>{
 const h=harness();h.context.probe.start();await h.flush();h.speak();h.quiet(1000);
 assert.equal(h.recordings[0].state,'recording');h.speak();h.quiet(1200);
 assert.equal(h.stats().uploads,0);h.quiet(200);await h.flush();
 assert.equal(h.stats().uploads,1);assert.equal(h.stats().submitted,1);
 assert.equal(h.uploadOptions().headers['X-Speech-Mode'],'balanced');
});

test('uncertain recognition opens editable text and never sends a device command automatically',async()=>{
 const h=harness({needsReview:true});h.context.probe.start();await h.flush();h.speak();h.context.probe.start();await h.flush();
 assert.equal(h.stats().submitted,0);assert.equal(h.elements['text-entry'].open,true);
 assert.equal(h.elements.question.value,'Welche Lichter sind an?');assert.match(h.states.at(-1)[1],/unsicher/);
});

test('normal pause is respected instead of imposing a global cutoff',async()=>{
 const h=harness();h.elements['speech-pause']={value:'2000'};h.context.probe.start();await h.flush();h.speak();h.quiet(1500);
 assert.equal(h.stats().uploads,0);h.quiet(600);await h.flush();assert.equal(h.stats().uploads,1);
});

test('live drafts do not submit a question; completed capture sends once without a second full-audio upload',async()=>{
 const h=harness({live:true,pendingLive:true});h.context.probe.start();await h.flush();h.speak();h.partial('Welche Lichter');
 assert.equal(h.elements['voice-transcript'].hidden,false);assert.equal(h.elements['voice-transcript'].textContent,'Welche Lichter');assert.equal(h.stats().submitted,0);
 h.context.probe.start();await h.flush();assert.equal(h.stats().submitted,0);h.finishLive();await h.flush();
 assert.equal(h.stats().submitted,1);assert.equal(h.stats().uploads,0);assert.equal(h.elements['voice-transcript'].hidden,true);assert.equal(h.elements.question.value,'Welche Lichter sind an?');
});

test('ending live capture clears provisional text and cannot submit a late final transcript',async()=>{
 const h=harness({live:true,pendingLive:true});h.context.probe.start();await h.flush();h.speak();h.partial('Bitte das Rollo');
 h.context.probe.start();await h.flush();h.context.probe.stop();await h.flush();h.finishLive();await h.flush();
 assert.equal(h.stats().submitted,0);assert.equal(h.elements['voice-transcript'].hidden,true);assert.equal(h.elements['voice-transcript'].textContent,'');assert.equal(h.stats().stopped,1);
});

test('worklet setup failure retains the Safari complete-recording fallback',async()=>{
 const h=harness({liveFailure:true});h.context.probe.start();await h.flush();h.speak();h.context.probe.start();await h.flush();
 assert.equal(h.recordings[0].mimeType,'audio/mp4');assert.equal(h.stats().uploads,1);assert.equal(h.stats().submitted,1);
});

test('cancel while live audio setup is pending releases the microphone immediately',async()=>{
 const h=harness({live:true,setupPending:true});h.context.probe.start();await h.flush();assert.equal(h.stats().requests,1);
 h.context.probe.stop();assert.equal(h.stats().stopped,1);h.resolveSetup();await h.flush();
 assert.equal(h.recordings.length,0);assert.equal(h.stats().uploads,0);assert.equal(h.stats().submitted,0);assert.equal(h.stats().stopped,1);
});
