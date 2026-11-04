const {test}=require('node:test');const assert=require('node:assert/strict');const vm=require('node:vm');const fs=require('node:fs');
const app=fs.readFileSync('frontend/app.js','utf8');const source=app.slice(app.indexOf('async function restoreLogin(){'),app.indexOf("$('login-form').onsubmit"));
test('saved chat opens even when admin settings and import status never finish',async()=>{
 const calls=[];const elements={};const pending=new Promise(()=>{});
 const ctx=vm.createContext({authRevision:0,conversationRevision:0,currentUser:null,manualLocation:null,conversationId:null,connected:false,chatBusy:false,chatAiEnabled:false,location:{hash:'#chat'},
  $:id=>elements[id]??(elements[id]={children:[1],querySelector:()=>null}),document:{body:{dataset:{}}},localStorage:{getItem:key=>key==='conversation'?'last-chat':null},sessionStorage:{getItem:()=>null},
  conversationKey:()=> 'conversation',notice(){},welcome(){},renderContext(){},api:async path=>path==='session'?{user:{id:'u',role:'admin',email:'test@example.invalid'},external_llm:true}:pending,
  openConversation:id=>(calls.push(id),Promise.resolve()),refresh:()=>pending,loadConnections:()=>pending,loadAccounts:()=>pending,loadMyIdentity:()=>pending,loadConversations:()=>pending});
 vm.runInContext(source,ctx);await ctx.restoreLogin();assert.deepEqual(calls,['last-chat']);assert.equal(elements['app-shell'].hidden,false);assert.equal(ctx.connected,true);assert.equal(elements.send.disabled,false);assert.equal(ctx.chatAiEnabled,true);
});

test('chat startup defers settings, contacts and account administration until their view opens',async()=>{
 const calls=[],elements={};
 const ctx=vm.createContext({authRevision:2,conversationRevision:0,currentUser:null,manualLocation:null,conversationId:null,connected:false,chatBusy:false,chatAiEnabled:false,location:{hash:'#chat'},
  $:id=>elements[id]??(elements[id]={children:[1],querySelector:()=>null}),document:{body:{dataset:{view:'chat'}}},localStorage:{getItem:()=>null},sessionStorage:{getItem:()=>null},
  conversationKey:()=> 'conversation',notice(){},welcome(){},renderContext(){},api:async path=>{calls.push(path);return path==='session'?{user:{id:'u',role:'admin',email:'test@example.invalid'},external_llm:true}:{};},
  openConversation:async()=>{},refresh:async()=>{},loadConnections:async()=>{calls.push('settings');},loadAccounts:async()=>{calls.push('accounts');},loadMyIdentity:async()=>{calls.push('identity');},loadConversations:async()=>[]});
 vm.runInContext(source,ctx);await ctx.restoreLogin();assert.deepEqual(calls,['session']);
 ctx.loadSettingsView();await new Promise(r=>setImmediate(r));
 assert.deepEqual(calls,['session','settings','accounts','identity','context']);
 ctx.loadSettingsView();await new Promise(r=>setImmediate(r));assert.equal(calls.length,5);
});

test('read requests have a deadline while explicit signals and mutations are preserved',async()=>{
 const calls=[],deadlines=[];
 const apiSource=app.slice(app.indexOf('async function api('),app.indexOf('const notice ='));
 const supplied=new AbortController().signal,defaultSignal=new AbortController().signal;
 const ctx=vm.createContext({authRevision:1,connected:false,AbortSignal:{timeout:ms=>(deadlines.push(ms),defaultSignal)},
  fetch:async(url,options)=>{calls.push(options);return {status:200};},readApiResponse:async()=>({ok:true})});
 vm.runInContext(apiSource,ctx);
 await ctx.api('session');await ctx.api('sync',{signal:supplied});await ctx.api('context',{method:'PUT',body:'{}'});
 assert.equal(calls[0].signal,defaultSignal);assert.equal(calls[1].signal,supplied);assert.equal(calls[2].signal,undefined);
 assert.deepEqual(deadlines,[10000]);
});
