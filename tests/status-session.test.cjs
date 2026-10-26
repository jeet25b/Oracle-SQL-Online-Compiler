const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync('app.js','utf8');
const code=source.slice(source.indexOf('let statusRefreshPromise=null;'),source.indexOf('let activeFloatingPopup='));
function harness(fetch,refreshObjects=async()=>{}){
 const state={connected:true,objects:[{OWNER:'TEST',OBJECT_TYPE:'TABLE',CREATED:'today'}],selectedObject:'TEST.TABLE',schemaLocked:false};
 const applied=[];
 const context=vm.createContext({state,fetch,refreshObjects,document:{body:{classList:{remove(){}}},querySelector:()=>({})},applyConnectionStatus(status){applied.push(status);state.connected=status.connected;},setObjectPlaceholder(){}});
 vm.runInContext(code,context);
 return {state,applied,run:()=>vm.runInContext('refreshStatus()',context)};
}
test('network failure preserves session and objects',async()=>{
 const h=harness(async()=>{throw Error('network');});
 assert.equal(await h.run(),null);assert.equal(h.state.connected,true);assert.equal(h.state.objects.length,1);assert.equal(h.applied.length,0);
});
test('HTTP and malformed responses do not log out',async()=>{
 for(const response of [{ok:false,status:503},{ok:true,json:async()=>({})}]){
  const h=harness(async()=>response);assert.equal(await h.run(),null);assert.equal(h.applied.length,0);
 }
});
test('object loading failure preserves confirmed connection',async()=>{
 const h=harness(async()=>({ok:true,json:async()=>({connected:true})}),async()=>{throw Error('metadata timeout');});
 h.state.objects=[];assert.equal(await h.run(),true);assert.equal(h.state.connected,true);
});
test('confirmed absent session still requires login',async()=>{
 const h=harness(async()=>({ok:true,json:async()=>({connected:false})}));
 assert.equal(await h.run(),false);assert.equal(h.state.connected,false);assert.equal(h.state.objects.length,0);
});
test('overlapping probes share one request',async()=>{
 let resolve,count=0;const h=harness(()=>{count++;return new Promise(r=>{resolve=r;});});
 const a=h.run(),b=h.run();resolve({ok:true,json:async()=>({connected:true})});await Promise.all([a,b]);assert.equal(count,1);
});
