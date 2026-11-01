const {test}=require('node:test');const assert=require('node:assert/strict');const vm=require('node:vm');const fs=require('node:fs');
const text=fs.readFileSync('frontend/app.js','utf8');const code=text.slice(text.indexOf('// A slow search asks'),text.indexOf('// End search wait controls.'));
function setup(kind='chat'){const timers=new Map(),calls=[];let next=0,voiceStops=0;
 const dialog={open:false,showModal(){this.open=true;},close(){this.open=false;},addEventListener(){}};
 const elements={'search-wait-dialog':dialog,'search-continue':{},'search-cancel':{},'search-wait-title':{},'search-wait-description':{}};
 const context=vm.createContext({AbortController,crypto:{randomUUID:()=> 'a'.repeat(32)},$:id=>elements[id],setTimeout:(fn,ms)=>{assert.equal(ms,20000);timers.set(++next,fn);return next;},clearTimeout:id=>timers.delete(id),api:(...args)=>(calls.push(args),Promise.resolve()),stopVoice:()=>voiceStops++});
 vm.runInContext(code,context);const search=context.beginSearchWait(kind);
 return{search,dialog,calls,context,elements,timers,expire(){const [id,fn]=timers.entries().next().value;timers.delete(id);fn();},voiceStops:()=>voiceStops};
}
test('asks after 20 seconds, continuation preserves original request and asks again',()=>{
 const h=setup();h.expire();assert.equal(h.dialog.open,true);const controller=h.search.controller;h.elements['search-continue'].onclick();assert.equal(h.dialog.open,false);assert.equal(h.search.controller,controller);assert.equal(controller.signal.aborted,false);assert.equal(h.calls.length,0);h.expire();assert.equal(h.dialog.open,true);
});
test('cancel aborts client, asks server to cancel and clears timer/dialog',()=>{
 const h=setup();h.expire();h.elements['search-cancel'].onclick();assert.equal(h.search.controller.signal.aborted,true);assert.equal(h.dialog.open,false);assert.equal(h.timers.size,0);assert.equal(h.calls[0][0],'chat/cancel');assert.equal(h.voiceStops(),1);h.search.cancel();assert.equal(h.calls.length,1);
});
test('a completed answer removes pending question and never shows a late dialog',()=>{
 const h=setup();h.expire();h.search.finish();assert.equal(h.dialog.open,false);assert.equal(h.timers.size,0);assert.equal(h.search.controller.signal.aborted,false);
});

test('slow transcription gets the same choice without cancelling an unrelated chat job',()=>{
 const h=setup('speech');h.expire();assert.match(h.elements['search-wait-title'].textContent,/Spracherkennung/);h.search.cancel();assert.equal(h.calls.length,0);assert.equal(h.search.controller.signal.aborted,true);
});
