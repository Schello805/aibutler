const {test}=require('node:test');const assert=require('node:assert/strict');const vm=require('node:vm');const fs=require('node:fs');
const app=fs.readFileSync('frontend/app.js','utf8');const source=app.slice(app.indexOf('async function restoreLogin(){'),app.indexOf("$('login-form').onsubmit"));
test('saved chat opens even when admin settings and import status never finish',async()=>{
 const calls=[];const elements={};const pending=new Promise(()=>{});
 const ctx=vm.createContext({authRevision:0,conversationRevision:0,currentUser:null,manualLocation:null,conversationId:null,connected:false,location:{hash:'#chat'},
  $:id=>elements[id]??(elements[id]={children:[1],querySelector:()=>null}),document:{body:{dataset:{}}},localStorage:{getItem:key=>key==='conversation'?'last-chat':null},sessionStorage:{getItem:()=>null},
  conversationKey:()=> 'conversation',notice(){},welcome(){},renderContext(){},api:async path=>path==='session'?{user:{id:'u',role:'admin',email:'test@example.invalid'}}:pending,
  openConversation:id=>(calls.push(id),Promise.resolve()),refresh:()=>pending,loadConnections:()=>pending,loadAccounts:()=>pending,loadMyIdentity:()=>pending,loadConversations:()=>pending});
 vm.runInContext(source,ctx);await ctx.restoreLogin();assert.deepEqual(calls,['last-chat']);assert.equal(elements['app-shell'].hidden,false);assert.equal(ctx.connected,true);
});
