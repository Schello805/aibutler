const {test}=require('node:test');const assert=require('node:assert/strict');const vm=require('node:vm');const fs=require('node:fs');
const source=fs.readFileSync('frontend/app.js','utf8');const code=source.slice(source.indexOf('async function openConversation(id){'),source.indexOf('function newConversation(){'));
test('late history response cannot erase a newly submitted question',async()=>{
 let resolve;let cleared=0;const request=new Promise(r=>resolve=r);
 const ctx=vm.createContext({chatBusy:false,conversationRevision:0,authRevision:1,currentUser:{id:'u'},api:()=>request,$:()=>({replaceChildren(){cleared++;}}),notice:message=>{throw Error(message);}});
 vm.runInContext(code,ctx);const loading=ctx.openConversation('old-chat');
 ctx.chatBusy=true;ctx.conversationRevision++;resolve({messages:[]});await loading;assert.equal(cleared,0);
});
test('history from an old login cannot replace the next session',async()=>{
 let resolve;const ctx=vm.createContext({chatBusy:false,conversationRevision:0,authRevision:1,currentUser:{id:'u'},api:()=>new Promise(r=>resolve=r),$:()=>{throw Error('must not touch DOM');},notice:message=>{throw Error(message);}});
 vm.runInContext(code,ctx);const loading=ctx.openConversation('old-chat');ctx.authRevision++;resolve({messages:[]});await loading;
});
