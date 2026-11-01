const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const app=fs.readFileSync('frontend/app.js','utf8'),code=app.slice(app.indexOf('// Source warning ownership:'),app.indexOf('// End source warning ownership.'));
test('a recovered source clears its stale banner and preserves unrelated messages',()=>{
 const element={textContent:''};const ctx=vm.createContext({$:()=>element,notice:text=>element.textContent=text});vm.runInContext(code,ctx);
 ctx.sourceNotice([['mail_archive',{error:'paused'}]]);assert.equal(element.textContent,'E-Mail-Archiv: paused');ctx.sourceNotice([]);assert.equal(element.textContent,'');
 ctx.sourceNotice([['semantic',{index_error:'index paused'}]]);assert.equal(element.textContent,'Lokale Suche: index paused');element.textContent='Gespeichert';ctx.sourceNotice([]);assert.equal(element.textContent,'Gespeichert');
});
