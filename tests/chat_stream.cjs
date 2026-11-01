const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const code=fs.readFileSync('frontend/chat_stream.js','utf8');
test('SSE transport handles split UTF-8, draft text and exactly one final result',async()=>{
 const text='event: progress\ndata: '+JSON.stringify({text:'Grüße',phase:'answer',label:'Antwort',elapsed_seconds:1})+'\n\nevent: result\ndata: '+JSON.stringify({answer:'Grüße Michael.'})+'\n\n';
 const bytes=new TextEncoder().encode(text);let offset=0,closed=0;const updates=[];
 const reader={async read(){if(offset>=bytes.length)return{done:true};const value=bytes.slice(offset,offset+7);offset+=7;return{done:false,value};},async cancel(){closed++;},releaseLock(){}};
 const ctx=vm.createContext({window:{},TextDecoder,Uint8Array,JSON,Error,DOMException,fetch:async(url,options)=>{assert.equal(url,'/api/chat/stream');assert.equal(options.credentials,'same-origin');return{ok:true,body:{getReader:()=>reader}};}});vm.runInContext(code,ctx);
 const answer=await ctx.window.ButlerChatStream({message:'test'},new AbortController().signal,state=>updates.push(state));assert.equal(answer.answer,'Grüße Michael.');assert.equal(updates[0].draft_text,'Grüße');assert.equal(closed,1);
});
test('cut-off streams are rejected rather than accepting an unfinished answer',async()=>{
 const reader={async read(){return{done:true};},async cancel(){},releaseLock(){}};
 const ctx=vm.createContext({window:{},TextDecoder,Uint8Array,JSON,Error,DOMException,fetch:async()=>({ok:true,body:{getReader:()=>reader}})});vm.runInContext(code,ctx);await assert.rejects(ctx.window.ButlerChatStream({},new AbortController().signal,()=>{}),/Stream unterbrochen/);
});
