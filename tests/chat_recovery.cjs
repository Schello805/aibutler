const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const text=fs.readFileSync('frontend/app.js','utf8');const code=text.slice(text.indexOf('// Recover a persisted answer'),text.indexOf('// End chat recovery.'));
test('persisted answer appears even when original HTTP response never arrives',async()=>{
 const timers=new Map();let next=0;const calls=[];
 const context=vm.createContext({AbortController,AbortSignal,DOMException,JSON,Promise,setTimeout:fn=>{timers.set(++next,fn);return next;},clearTimeout:id=>timers.delete(id),api:(path,options)=>{
  calls.push(path);if(path.startsWith('chat/result/'))return Promise.resolve({status:'done',result:{answer:'Recovered',conversation_id:'test'}});
  return new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true}));
 }});
 vm.runInContext(code,context);const search={controller:new AbortController(),requestId:'abc'};
 const pending=context.chatWithRecovery(search,{});const [id,fn]=timers.entries().next().value;timers.delete(id);fn();
 const answer=await pending;assert.equal(answer.answer,'Recovered');assert.deepEqual(calls,['chat','chat/result/abc']);assert.equal(timers.size,0);
});
test('cancelling a request also clears the answer recovery timer',async()=>{
 const timers=new Map();let next=0;
 const context=vm.createContext({AbortController,AbortSignal,DOMException,JSON,Promise,setTimeout:fn=>{timers.set(++next,fn);return next;},clearTimeout:id=>timers.delete(id),api:(_,options)=>new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true}))});
 vm.runInContext(code,context);const search={controller:new AbortController(),requestId:'abc'};const pending=context.chatWithRecovery(search,{});search.controller.abort();await assert.rejects(pending,{name:'AbortError'});assert.equal(timers.size,0);
});
