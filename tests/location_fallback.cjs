const {test}=require('node:test');const assert=require('node:assert/strict');const vm=require('node:vm');const fs=require('node:fs');
const text=fs.readFileSync('frontend/app.js','utf8');const source=text.slice(text.indexOf('async function getDeviceLocation(){'),text.indexOf("$('choose-location').onclick"));
test('refused reverse geocoding preserves weather location and backs off retries',async()=>{
 let requests=0;const status={};
 const context=vm.createContext({authRevision:0,currentUser:{id:'test'},manualLocation:null,locationMemory:null,publicServices:{holidays:true},window:{isSecureContext:true},
  navigator:{geolocation:{getCurrentPosition(resolve){resolve({coords:{latitude:52.52,longitude:13.405}});}}},Intl,Date,URLSearchParams,AbortSignal,
  $:id=>id==='use-device-location'?{checked:true}:status,fetch:async()=>{requests++;throw new TypeError('ERR_CONNECTION_REFUSED');}});
 vm.runInContext(source,context);
 const first=await context.getDeviceLocation();assert.equal(first.latitude,52.52);assert.equal(first.longitude,13.41);assert.equal(first.country,undefined);
 await context.getDeviceLocation();assert.equal(requests,1);assert.match(status.textContent,/Region derzeit nicht ermittelbar/);
});
