const {test}=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const vm=require('node:vm');
const code=fs.readFileSync('frontend/app.js','utf8').split('const viewLabels=')[1].split("window.addEventListener('hashchange'")[0];
function page(hash){
 const elements={},views=['chat','knowledge','settings'].map(view=>({dataset:{view},hidden:false}));let loaded=0,scrolled=null;
 for(const id of ['setup','context','page-label','page-title','page-subtitle','sources-view','findings-view','memories-view'])elements[id]={open:false,scrollIntoView(){scrolled=id;}};
 const nav=views.map(view=>({dataset:{nav:view.dataset.view},setAttribute(k,v){this[k]=v;},removeAttribute(k){delete this[k];}}));
 const context=vm.createContext({$:id=>elements[id],newConversation(){},requestAnimationFrame:fn=>fn(),document:{body:{dataset:{}},querySelectorAll:s=>s==='.page-view'?views:s==='[data-nav]'?nav:[]},window:{location:{hash},scrollTo(){},ButlerMemory:{load(){loaded++;}}}});
 vm.runInContext('const viewLabels='+code+'\nshowView();',context);
 return{context,elements,views,nav,get loaded(){return loaded;},get scrolled(){return scrolled;}};
}
test('old source and finding bookmarks open the corresponding section in the unified knowledge page',()=>{
 for(const [hash,id] of [['#sources','sources-view'],['#findings','findings-view'],['#memories','memories-view']]){
  const p=page(hash);assert.equal(p.context.document.body.dataset.view,'knowledge');assert.equal(p.elements[id].open,true);assert.equal(p.scrolled,id);assert.equal(p.loaded,1);
  assert.deepEqual(p.views.filter(v=>!v.hidden).map(v=>v.dataset.view),['knowledge']);assert.equal(p.nav[1]['aria-current'],'page');
 }
});
test('opening the knowledge overview preserves an already open section and draft state',()=>{
 const p=page('#knowledge');p.elements['memories-view'].open=true;p.elements['memories-view'].draft='not saved yet';vm.runInContext('showView()',p.context);
 assert.equal(p.elements['memories-view'].open,true);assert.equal(p.elements['memories-view'].draft,'not saved yet');assert.equal(p.scrolled,null);
});
